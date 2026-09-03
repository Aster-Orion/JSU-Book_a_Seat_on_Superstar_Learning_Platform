# -*- coding: utf-8 -*-
from .encrypt import AES_Encrypt, enc, generate_captcha_key, verify_param
import json
import requests
import re
import time
import logging
import datetime
from urllib3.exceptions import InsecureRequestWarning


def get_date(day_offset: int = 0):
    today = datetime.datetime.now().date()
    offset_day = today + datetime.timedelta(days=day_offset)
    tomorrow = offset_day.strftime("%Y-%m-%d")
    return tomorrow


def send_failure_email(failures):
    """发送失败邮件提醒（仅当预约请求失败，或开始时间大于结束时间时调用）

    failures: 失败信息列表，每个元素为 dict，字段：
        username / roomid / seatid / start_time / end_time / reason
    """
    if not failures:
        return

    config = json.load(open("config.json", encoding="utf-8"))
    mail_config = config.get("mail", {})
    receivers = config.get("receivers", [])
    if not mail_config or not receivers:
        logging.warning("未配置邮件信息，跳过失败邮件发送")
        return

    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header

    # 构建邮件内容
    email_lines = ["超星图书馆预约失败提醒！", ""]
    for idx, f in enumerate(failures, 1):
        email_lines.append(f"失败 {idx}:")
        email_lines.append(f"  账号: {f.get('username', '')}")
        email_lines.append(f"  房间代号: {f.get('roomid', '')}")
        email_lines.append(f"  座位: {f.get('seatid', '')}")
        email_lines.append(f"  开始时间: {f.get('start_time', '')}")
        email_lines.append(f"  结束时间: {f.get('end_time', '')}")
        email_lines.append(f"  失败原因: {f.get('reason', '')}")
        email_lines.append("")

    email_content = "\n".join(email_lines)

    # 发送邮件
    try:
        message = MIMEText(email_content, "plain", "utf-8")
        message["From"] = Header(mail_config["auth"]["user"])
        message["To"] = Header(",".join(receivers))
        message["Subject"] = Header(f"超星图书馆预约失败提醒 - 共{len(failures)}条")

        smtpObj = smtplib.SMTP_SSL(mail_config["host"], mail_config["port"])
        smtpObj.login(mail_config["auth"]["user"], mail_config["auth"]["pass"])
        smtpObj.sendmail(mail_config["auth"]["user"], receivers, message.as_string())
        logging.info("✓ 失败邮件发送成功")
    except Exception as e:
        logging.error(f"✗ 失败邮件发送失败: {str(e)}")


class reserve:
    def __init__(
        self,
        sleep_time=0.2,
        max_attempt=10,
        enable_slider=False,
        reserve_next_day=False,
        segment_interval=0.3,
    ):
        # API 端点
        self.login_page = "https://passport2.chaoxing.com/mlogin?loginType=1&newversion=true&fid="
        # 预约“选座页”（含 submit_enc 隐藏域）。注意：/seat/code 是“签到”页，不是预约页，勿用。
        self.select_url = (
            "https://office.chaoxing.com/front/third/apps/seat/select"
            "?deptIdEnc={deptIdEnc}&id={roomid}&day={day}&backLevel=2&fidEnc={deptIdEnc}"
        )
        self.submit_url = "https://office.chaoxing.com/data/apps/seat/submit"
        self.login_url = "https://passport2.chaoxing.com/fanyalogin"
        
        # 预约结果存储
        self.token = ""
        self.success_results = []  # 存储所有成功的预约结果
        # 配置读取
        config = json.load(open("config.json", encoding="utf-8"))
        self.mail_config = config.get("mail", {})
        self.receivers = config.get("receivers", [])
        # 学校/院系的加密 ID（deptIdEnc），用于选座页与提交接口；为固定值，从选座页 URL 或抓包获取
        self.deptIdEnc = config.get("deptIdEnc", "")
        
        # 与抓包一致的移动端 User-Agent（Android Chrome），用于所有请求。
        # 请求头中不再手动指定 Host，requests 会根据目标 URL 自动填充正确的 Host，
        # 避免登录后切换域名（passport2 -> office）时 Host 头错乱导致服务器返回登录页。
        self.user_agent = (
            "Mozilla/5.0 (Linux; Android 15; Pixel 9) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Mobile Safari/537.36"
        )

        # HTTP 会话
        self.requests = self._new_session()

        # 登录健壮性配置：
        # 云函数（阿里云 FC）出口 IP 可能被超星 WAF 拦截并 302 到 passport403.html（自循环），
        # requests 默认 allow_redirects=True 且上限 30 次会抛 TooManyRedirects("Exceeded 30 redirects")。
        # 这里登录 POST 不跟随重定向，并对瞬时失败做重试，提高夜间登录成功率。
        self._login_max_attempt = 3      # 登录失败后的最大重试次数
        self._login_retry_interval = 1.0 # 登录重试的基础间隔（秒）

        # 登录接口请求头（passport2.chaoxing.com）
        self.login_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://passport2.chaoxing.com",
            "Referer": "https://passport2.chaoxing.com/mlogin?loginType=1&newversion=true",
        }

        # 预约页面 / 提交接口请求头（office.chaoxing.com）
        self.seat_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://office.chaoxing.com/",
        }

        # 滑块验证码请求头（仅 enable_slider=True 时使用）
        self.headers = {
            "Referer": "https://office.chaoxing.com/",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self.user_agent,
        }

        # 从预约页面提取 submit_enc 隐藏域的 value（后续 MD5 签名的种子）
        self.token_pattern = re.compile(
            r'<input[^>]+(?:id|name)=["\']submit_enc["\'][^>]*value=["\'](.*?)["\']'
        )

        # 参数设置
        self.sleep_time = sleep_time
        self.max_attempt = max_attempt
        self.enable_slider = enable_slider
        self.reserve_next_day = reserve_next_day
        self.segment_interval = segment_interval
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    def _new_session(self):
        """创建一个新的 requests.Session（忽略系统代理，统一 UA/Accept 头）

        登录失败重试时也会调用，重建一个干净会话，避免上一次失败的重定向链污染 Cookie。
        """
        s = requests.session()
        # 关键：忽略系统代理 / 环境变量代理，直连服务器。
        # Windows 上 requests 会读取系统代理（如 127.0.0.1:7890 的 Clash），
        # 一旦代理软件未正确接管本地流量，就会抛 ProxyError / FileNotFoundError 导致连不上。
        # chaoxing 是国内服务，直连即可，这里强制关闭代理读取（只影响本脚本，不影响浏览器）。
        s.trust_env = False
        s.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
        })
        return s

    def _get_page_token(self, roomid, day):
        """获取预约“选座页”的 submit_enc 值（后续 MD5 签名的种子）

        返回 submit_enc 隐藏域的 value；失败返回空字符串。
        """
        url = self.select_url.format(deptIdEnc=self.deptIdEnc, roomid=roomid, day=day)
        # 携带移动端 UA + Referer，否则服务器可能判定会话无效，重定向到登录页 / 验证页，
        # 导致返回的是 HTML 而非包含 submit_enc 的选座页面。
        headers = dict(self.seat_headers)
        headers["Referer"] = "https://office.chaoxing.com/front/third/apps/seat/"
        try:
            response = self.requests.get(url=url, headers=headers, verify=False)
            html = response.content.decode("utf-8")
        except Exception as e:
            logging.error(f"获取预约页面失败 {url}: {e}")
            return ""

        # 兼容多种 HTML 写法：value 与 id/name 顺序不定、单双引号混用
        patterns = [
            r'id=["\']submit_enc["\'][^>]*value=["\'](.*?)["\']',
            r'value=["\'](.*?)["\'][^>]*id=["\']submit_enc["\']',
            r'name=["\']submit_enc["\'][^>]*value=["\'](.*?)["\']',
        ]
        for pattern in patterns:
            m = re.search(pattern, html)
            if m:
                return m.group(1)

        logging.error(
            f"Failed to get submit_enc from {url}, HTTP状态={response.status_code}, "
            f"返回内容前800字: {html[:800]}"
        )
        return ""

    def get_login_status(self):
        """（已停用）初始化登录会话。

        原实现会 GET passport2 的 mlogin 登录页以获取前置 Cookie，但在云函数出口 IP 上
        该页面会被超星 WAF 302 到 passport403.html 并自循环，最终抛
        TooManyRedirects("Exceeded 30 redirects")。实测跳过此页、直接 POST fanyalogin
        即可登录成功，因此保留方法签名（兼容调用方）但不再发起请求。
        """
        logging.info("跳过登录页预请求：mlogin 在云函数出口 IP 上会被 403，直接 POST 登录即可")

    def login(self, username, password):
        raw_username = username
        enc_username = AES_Encrypt(username)
        enc_password = AES_Encrypt(password)
        parm = {
            "fid": "-1",
            "uname": enc_username,
            "password": enc_password,
            # 登录后跳转目标。旧值写死成某个具体座位（id=4219&seatNum=380），与实际座位不符；
            # 登录本身并不依赖具体座位，这里指向座位模块入口即可。
            "refer": "http%3A%2F%2Foffice.chaoxing.com%2Ffront%2Fthird%2Fapps%2Fseat%2F",
            "t": "true",
        }

        last_msg = "未知错误"
        for attempt in range(1, self._login_max_attempt + 1):
            if attempt > 1:
                # 重建会话：上一次失败可能是重定向链污染了 Cookie，重试前换一个干净会话
                self.requests = self._new_session()
                logging.info(f"用户 {raw_username} 登录重试 {attempt}/{self._login_max_attempt}")

            try:
                # 关键修复：登录凭证必须以表单体（data=）提交，而非 URL 查询参数（params=）。
                # 抓包显示 fanyalogin 的 Content-Type 为 application/x-www-form-urlencoded 且有请求体。
                # 使用 data= 才能让服务器正确回写 p_auth_token / UID / _uid 等会话 Cookie。
                # allow_redirects=False：成功路径直接返回 JSON，无需跟随重定向；
                # 若被 WAF 重定向则记录目标并重试，而不是盲目跟随导致 Exceeded 30 redirects。
                jsons = self.requests.post(
                    url=self.login_url, data=parm, headers=self.login_headers,
                    verify=False, allow_redirects=False,
                )
            except Exception as e:
                last_msg = str(e)
                logging.error(f"用户 {raw_username} 登录请求异常: {e}")
            else:
                if jsons.is_redirect:
                    loc = jsons.headers.get("Location")
                    last_msg = f"登录被重定向 (HTTP {jsons.status_code}) -> {loc}"
                    logging.warning(f"用户 {raw_username} {last_msg}")
                else:
                    try:
                        obj = jsons.json()
                    except Exception as e:
                        # 返回非 JSON（可能是 WAF 校验页 / 半截响应），视为瞬时故障，进入重试
                        last_msg = f"登录响应非 JSON: {jsons.text[:200]}"
                        logging.error(f"用户 {raw_username} {last_msg}")
                    else:
                        if obj.get("status"):
                            logging.info(f"用户 {raw_username} 登录成功")
                            return (True, "")
                        # 服务端明确拒绝（如“用户名或密码错误”），重试无意义，直接返回
                        last_msg = obj.get("msg2") or obj.get("msg") or "未知错误"
                        logging.error(f"用户 {raw_username} 登录失败，原因: {last_msg}，服务器返回: {obj}")
                        return (False, last_msg)

            time.sleep(self._login_retry_interval * attempt)

        return (False, last_msg)

    def roomid(self, encode):
        """列出所有可用的房间及座位信息"""
        url = f"https://office.chaoxing.com/data/apps/seat/room/list?cpage=1&pageSize=100&firstLevelName=&secondLevelName=&thirdLevelName=&deptIdEnc={encode}"
        json_data = self.requests.get(url=url).content.decode("utf-8")
        ori_data = json.loads(json_data)
        for i in ori_data["data"]["seatRoomList"]:
            info = f'{i["firstLevelName"]}-{i["secondLevelName"]}-{i["thirdLevelName"]} id为: {i["id"]}'
            print(info)

    def resolve_captcha(self):
        """解析滑块验证码"""
        logging.info("开始解析验证码")
        captcha_token, bg, tp = self.get_slide_captcha_data()
        logging.info(f"已获取验证码令牌")
        
        # 计算滑块距离
        x = self.x_distance(bg, tp)
        logging.info(f"滑块距离: {x}px")

        # 提交验证码结果
        params = {
            "callback": "jQuery33109180509737430778_1716381333117",
            "captchaId": "42sxgHoTPTKbt0uZxPJ7ssOvtXr3ZgZ1",
            "type": "slide",
            "token": captcha_token,
            "textClickArr": json.dumps([{"x": x}]),
            "coordinate": json.dumps([]),
            "runEnv": "10",
            "version": "1.1.18",
            "_": int(time.time() * 1000),
        }
        response = self.requests.get(
            f"https://captcha.chaoxing.com/captcha/check/verification/result",
            params=params,
            headers=self.headers,
        )
        text = response.text.replace(
            "jQuery33109180509737430778_1716381333117(", ""
        ).replace(")", "")
        data = json.loads(text)
        logging.info(f"验证码验证完成")
        try:
            validate_val = json.loads(data["extraData"])["validate"]
            return validate_val
        except KeyError as e:
            logging.info("无法加载验证值。可能服务器返回错误.")
            return ""

    def get_slide_captcha_data(self):
        url = "https://captcha.chaoxing.com/captcha/get/verification/image"
        timestamp = int(time.time() * 1000)
        capture_key, token = generate_captcha_key(timestamp)
        referer = f"https://office.chaoxing.com/front/third/apps/seat/code?id=3993&seatNum=0199"
        params = {
            "callback": f"jQuery33107685004390294206_1716461324846",
            "captchaId": "42sxgHoTPTKbt0uZxPJ7ssOvtXr3ZgZ1",
            "type": "slide",
            "version": "1.1.18",
            "captchaKey": capture_key,
            "token": token,
            "referer": referer,
            "_": timestamp,
            "d": "a",
            "b": "a",
        }
        response = self.requests.get(url=url, params=params, headers=self.headers)
        content = response.text

        data = content.replace(
            "jQuery33107685004390294206_1716461324846(", ")"
        ).replace(")", "")
        data = json.loads(data)
        captcha_token = data["token"]
        bg = data["imageVerificationVo"]["shadeImage"]
        tp = data["imageVerificationVo"]["cutoutImage"]
        return captcha_token, bg, tp

    def x_distance(self, bg, tp):
        """计算滑块验证码的水平距离（使用图像匹配）"""
        import numpy as np
        import cv2

        def cut_slide(slide):
            """剪裁滑块图像"""
            slider_array = np.frombuffer(slide, np.uint8)
            slider_image = cv2.imdecode(slider_array, cv2.IMREAD_UNCHANGED)
            slider_part = slider_image[:, :, :3]
            mask = slider_image[:, :, 3]
            mask[mask != 0] = 255
            x, y, w, h = cv2.boundingRect(mask)
            cropped_image = slider_part[y : y + h, x : x + w]
            return cropped_image

        c_captcha_headers = {
            "Referer": "https://office.chaoxing.com/",
            "Host": "captcha-b.chaoxing.com",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }
        
        # 下载背景和滑块图像
        bgc, tpc = self.requests.get(bg, headers=c_captcha_headers), self.requests.get(tp, headers=c_captcha_headers)
        bg, tp = bgc.content, tpc.content
        
        # 图像处理：边界检测和模板匹配
        bg_img = cv2.imdecode(np.frombuffer(bg, np.uint8), cv2.IMREAD_COLOR)
        tp_img = cut_slide(tp)
        bg_edge = cv2.Canny(bg_img, 100, 200)
        tp_edge = cv2.Canny(tp_img, 100, 200)
        bg_pic = cv2.cvtColor(bg_edge, cv2.COLOR_GRAY2RGB)
        tp_pic = cv2.cvtColor(tp_edge, cv2.COLOR_GRAY2RGB)
        
        # 匹配滑块位置
        res = cv2.matchTemplate(bg_pic, tp_pic, cv2.TM_CCOEFF_NORMED)
        _, _, _, max_loc = cv2.minMaxLoc(res)
        
        return max_loc[0]

    def _split_times(self, start_time, end_time, max_hours=5):
        """将超过max_hours的时间段切分成多个子段"""
        def to_minutes(t):
            h, m = map(int, t.split(":"))
            return h * 60 + m

        def to_str(m):
            return f"{m // 60:02d}:{m % 60:02d}"

        start_min = to_minutes(start_time)
        end_min = to_minutes(end_time)
        if end_min <= start_min:
            end_min += 24 * 60  # 跨天

        total_min = end_min - start_min
        max_min = max_hours * 60

        if total_min <= max_min:
            return [(start_time, end_time)]

        segments = []
        cur = start_min
        while cur < end_min:
            nxt = min(cur + max_min, end_min)
            segments.append((to_str(cur), to_str(nxt)))
            cur = nxt
        return segments

    def _get_reserve_day(self, action):
        """计算要预约的日期（返回 datetime.date）"""
        delta_day = 1 if self.reserve_next_day else 0
        if action:
            # GitHub runner 为 UTC 时区，北京时间 = UTC + 8h。
            # 原实现用 `day += 1 天` 做“时区补偿”，会与“预约次日(+1)”重复累加：
            # 北京时间 08:00（= UTC 00:00）时 UTC 日期已与北京同一天，再 +1 就变成后天，
            # 选座页因“后天不可预约”返回“出错了”页，导致拿不到 submit_enc。
            today = datetime.datetime.fromtimestamp(
                time.time() + 8 * 3600, tz=datetime.timezone.utc
            ).date()
        else:
            today = datetime.date.today()
        return today + datetime.timedelta(days=delta_day)

    def submit(self, times, roomid, seatid, action):
        """提交预约请求"""
        start_time, end_time = times[0], times[1]
        day = self._get_reserve_day(action)
        logging.info(f"本次预约目标日期: {day}（RESERVE_NEXT_DAY={self.reserve_next_day}, action={action}）")

        # 切分超过5小时的时间段
        segments = self._split_times(start_time, end_time)
        if len(segments) > 1:
            logging.info(f"时段长度超过5小时，切分为 {len(segments)} 段")

        # 逐个座位、逐个时段提交预约
        for seat in seatid:
            for seg_index, (seg_start, seg_end) in enumerate(segments):
                # 同一座位不同时段之间加间隔，给服务器缓冲；第一段仍准点发送
                if seg_index > 0:
                    time.sleep(self.segment_interval)

                suc = False
                attempt = self.max_attempt

                # 重试逻辑
                while not suc and attempt > 0:
                    # 每次提交前重新获取选座页的 submit_enc，避免“安全验证超时(303)”
                    value = self._get_page_token(roomid, day)

                    # 如果启用滑块验证则解析验证码（本校 securityVerify=0，通常无需验证码）
                    captcha = self.resolve_captcha() if self.enable_slider else ""

                    # 提交预约
                    suc = self.get_submit(
                        self.submit_url,
                        times=[seg_start, seg_end],
                        roomid=roomid,
                        seatid=seat,
                        captcha=captcha,
                        day=day,
                        value=value,
                    )

                    if suc:
                        logging.info(f"✓ 时段 {seg_start}~{seg_end} 预约成功")
                        break

                    # 重试等待
                    time.sleep(self.sleep_time)
                    attempt -= 1
                if not suc:
                    logging.error(f"✗ 预约失败 - 房间:{roomid} 座位:{seat} 时段:{seg_start}~{seg_end}，已重试 {self.max_attempt} 次，跳过后续时段")
                    return False
        return True

    def get_submit(
        self, url, times, roomid, seatid, captcha="", day=None, value=""
    ):
        """提交预约表单并处理响应"""
        if day is None:
            day = self._get_reserve_day(False)

        # 构建预约参数（与前端 doSubmit 的 paramObj 一致，enc 字段稍后追加）
        parm = {
            "deptIdEnc": self.deptIdEnc,
            "roomId": roomid,
            "day": str(day),
            "startTime": times[0],
            "endTime": times[1],
            "seatNum": seatid,
            "captcha": captcha,
            "wyToken": "",   # 风险检测 token；本校 riskCheckOpen 未开启，恒为空
        }

        logging.info(f"提交预约 - 房间:{roomid} 座位:{seatid} 时间:{times[0]}-{times[1]} 日期:{day}")

        # 计算 enc 签名：MD5(排序后的 [k=v] + [submit_enc值])，与前端 submitVerify 一致
        parm["enc"] = verify_param(parm, value)

        # 提交参数必须以表单体（data=）提交；enc 需基于不含 enc 的 paramObj 计算
        headers = dict(self.seat_headers)
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        headers["Referer"] = self.select_url.format(
            deptIdEnc=self.deptIdEnc, roomid=roomid, day=day
        )
        try:
            resp = self.requests.post(url=url, data=parm, headers=headers, verify=False)
            html = resp.content.decode("utf-8")
            logging.info(f"预约请求返回 - HTTP状态={resp.status_code} 响应内容={html}")
        except Exception as e:
            logging.error(f"预约请求异常 房间:{roomid} 座位:{seatid}: {e}")
            return False

        try:
            result = json.loads(html)
        except Exception as e:
            logging.error(f"预约响应解析失败（非 JSON）房间:{roomid} 座位:{seatid}: {e}，原始内容={html}")
            return False

        # 处理预约结果
        if result.get("success"):
            seat_info = result.get('data', {}).get('seatReserve', {})
            start_time = times[0]
            end_time = times[1]
            logging.info(f"✓ 预约成功 - 座位:{seat_info.get('seatNum')} {start_time}~{end_time}")

            # 保存成功结果用于后续邮件
            self.success_results.append({
                'seatNum': seat_info.get('seatNum'),
                'startTime': start_time,
                'endTime': end_time,
                'roomId': roomid,
                'day': str(day)
            })
        else:
            logging.error(f"✗ 预约失败 房间:{roomid} 座位:{seatid}，服务器返回: {result}")

        return result.get("success")

    def send_all_results_email(self):
        """发送合并的邮件（包含所有成功的预约）"""
        if not self.success_results:
            logging.info("没有成功的预约，不发送邮件")
            return
        
        import smtplib
        from email.mime.text import MIMEText
        from email.header import Header

        # 构建邮件内容
        email_lines = ["超星图书馆预约座位成功！", " " * 60]
        for idx, result in enumerate(self.success_results, 1):
            email_lines.append(f"预约 {idx}:")
            email_lines.append(f"  房间代号: {result['roomId']}")
            email_lines.append(f"  座位: {result['seatNum']}")
            email_lines.append(f"  时间: {result['startTime']} ~ {result['endTime']}")
            email_lines.append(f"  日期: {result['day']}")
            email_lines.append(" " * 60)

        email_content = "\n".join(email_lines)
        
        # 发送邮件
        try:
            message = MIMEText(email_content, "plain", "utf-8")
            message["From"] = Header(self.mail_config["auth"]["user"])
            message["To"] = Header(",".join(self.receivers))
            message["Subject"] = Header(f"超星图书馆预约座位成功 - 共{len(self.success_results)}条")

            smtpObj = smtplib.SMTP_SSL(self.mail_config["host"], self.mail_config["port"])
            smtpObj.login(self.mail_config["auth"]["user"], self.mail_config["auth"]["pass"])
            smtpObj.sendmail(self.mail_config["auth"]["user"], self.receivers, message.as_string())
            logging.info("✓ 邮件发送成功")
        except smtplib.SMTPException as e:
            logging.error(f"✗ 邮件发送失败: {str(e)}")

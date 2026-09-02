# -*- coding: utf-8 -*-
"""
超星图书馆座位自动签到 / 签退模块

功能：
  1. 自动签到（签到）：对已预约的座位执行签到
  2. 自动签退（签退 / 离馆）：对已签到座位执行签退，提前释放座位
  3. 自动获取预约ID（reserveId）：从当前预约列表 / 预约信息接口提取
  4. 预约状态查询：查询当前用户的所有有效预约

技术路径：
  - 复用 reserve 类的登录与会话（requests.Session），保证登录方式与预约完全一致
  - 签到二维码中的固定值即 reserveId，可从预约信息接口（reserve/info）的 seatReserve.id 直接获取
  - 签到接口: https://office.chaoxing.com/data/apps/seat/sign?id={reserveId}
  - 签退接口: https://office.chaoxing.com/data/apps/seat/signback?id={reserveId}

参考：otherProject/signin.py 中的 SeatSignIn 类
"""

import json
import logging

from .reserve import reserve


def send_sign_failure_email(failures, action_name):
    """发送签到 / 签退失败邮件提醒

    failures: 失败信息列表，每个元素为 dict，字段：
        username / roomid / seatid / reason
    action_name: "签到" 或 "签退"（用于邮件标题与正文）
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
    email_lines = [f"超星图书馆座位{action_name}失败提醒！", ""]
    for idx, f in enumerate(failures, 1):
        email_lines.append(f"失败 {idx}:")
        email_lines.append(f"  账号: {f.get('username', '')}")
        email_lines.append(f"  房间代号: {f.get('roomid', '')}")
        email_lines.append(f"  座位: {f.get('seatid', '')}")
        email_lines.append(f"  失败原因: {f.get('reason', '')}")
        email_lines.append("")

    email_content = "\n".join(email_lines)

    # 发送邮件
    try:
        message = MIMEText(email_content, "plain", "utf-8")
        message["From"] = Header(mail_config["auth"]["user"])
        message["To"] = Header(",".join(receivers))
        message["Subject"] = Header(f"超星图书馆座位{action_name}失败提醒 - 共{len(failures)}条")

        smtpObj = smtplib.SMTP_SSL(mail_config["host"], mail_config["port"])
        smtpObj.login(mail_config["auth"]["user"], mail_config["auth"]["pass"])
        smtpObj.sendmail(mail_config["auth"]["user"], receivers, message.as_string())
        logging.info(f"✓ {action_name}失败邮件发送成功")
    except Exception as e:
        logging.error(f"✗ {action_name}失败邮件发送失败: {str(e)}")


class SeatSignIn:
    """
    超星座位自动签到 / 签退类

    使用示例:
        signin = SeatSignIn()
        signin.get_login_status()
        login_ok, msg = signin.login(username, password)

        # 签到（自动根据 roomid + seatid 从当前预约列表获取 reserveId）
        result = signin.execute_signin(roomid="18888", seatid="188")

        # 签退（结束使用，释放座位）
        result = signin.execute_signout(roomid="18888", seatid="188")
    """

    def __init__(self, session=None):
        # 复用 reserve 类的登录与 session（与预约同一套登录逻辑，确保可用）
        if session is not None:
            self.requests = session
            # 不再需要 reserve 实例
            self.reserve = None
        else:
            # 原有逻辑：复用 reserve 类创建新会话
            self.reserve = reserve()
            self.requests = self.reserve.requests

        # API 端点
        self.sign_url = "https://office.chaoxing.com/data/apps/seat/sign"
        self.signout_url = "https://office.chaoxing.com/data/apps/seat/signback"
        self.reserve_info_url = (
            "https://office.chaoxing.com/data/apps/seat/reserve/info"
        )

        # office.chaoxing.com 数据接口请求头（在 session 已有 UA/Accept 基础上追加）
        self.api_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://office.chaoxing.com/",
        }

    # ─── 登录（复用 reserve 类的登录逻辑） ──────────────────────

    def get_login_status(self):
        """初始化登录会话（获取前置 Cookie）"""
        self.reserve.get_login_status()

    def login(self, username, password):
        """登录，返回 (success: bool, message: str)"""
        return self.reserve.login(username, password)

    # ─── 预约信息查询 ─────────────────────────────────────────

    def _get_json(self, url):
        """GET 请求并解析 JSON；解析失败返回 {"raw": text} 便于排错"""
        try:
            resp = self.requests.get(
                url, headers=self.api_headers, verify=False, timeout=10
            )
            text = resp.content.decode("utf-8")
        except Exception as e:
            logging.error(f"请求失败 {url}: {e}")
            return {"raw": str(e)}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    def get_reserve_info(self, roomid, seatid):
        """通过预约信息接口获取指定座位的预约信息

        返回 data 对象（含 seatReserve / seatConfig），无预约返回 None。
        """
        url = f"{self.reserve_info_url}?id={roomid}&seatNum={seatid}"
        data = self._get_json(url)
        if data.get("success") and data.get("data"):
            return data["data"]
        return None

    def find_reserve_id(self, roomid, seatid):
        """通过预约信息接口获取 reserveId（seatReserve.id），找不到返回空字符串"""
        info = self.get_reserve_info(roomid, seatid)
        if not info:
            return ""
        seat_reserve = info.get("seatReserve") or {}
        return str(seat_reserve.get("id", ""))

    # ─── 签到 / 签退核心逻辑 ─────────────────────────────────

    def _resolve_reserve_id(self, roomid, seatid, reserve_id):
        """优先使用传入的 reserve_id，否则从预约列表自动获取"""
        if reserve_id:
            return reserve_id
        return self.find_reserve_id(roomid, seatid)

    def _request_sign_action(self, url, action_name, roomid, seatid, reserve_id):
        """执行一次签到 / 签退请求（GET id={reserveId}）"""
        reserve_id = self._resolve_reserve_id(roomid, seatid, reserve_id)
        if not reserve_id:
            return {
                "success": False,
                "message": f"未找到预约ID，无法{action_name} (room={roomid}, seat={seatid})",
                "reserve_id": "",
                "roomid": roomid,
                "seatid": seatid,
            }

        logging.info(
            f"开始{action_name}: room={roomid}, seat={seatid}, reserveId={reserve_id}"
        )
        data = self._get_json(f"{url}?id={reserve_id}")

        if data.get("success") or data.get("status") == "success":
            logging.info(
                f"{action_name}成功: room={roomid}, seat={seatid}, reserveId={reserve_id}"
            )
            return {
                "success": True,
                "message": f"{action_name}成功",
                "reserve_id": reserve_id,
                "roomid": roomid,
                "seatid": seatid,
            }

        msg = data.get("msg") or data.get("message") or json.dumps(data, ensure_ascii=False)[:200]
        logging.warning(f"{action_name}失败: {msg}")
        return {
            "success": False,
            "message": str(msg),
            "reserve_id": reserve_id,
            "roomid": roomid,
            "seatid": seatid,
        }

    def execute_signin(self, roomid="", seatid="", reserve_id=""):
        """
        执行单次签到

        Args:
            roomid: 房间ID（如 "18888"）
            seatid: 座位号（如 "188"）
            reserve_id: 预约ID（可选，提供则直接使用，否则自动从预约列表获取）

        Returns:
            dict: {"success": bool, "message": str, "reserve_id": str, "roomid": str, "seatid": str}
        """
        return self._request_sign_action(
            self.sign_url, "签到", roomid, seatid, reserve_id
        )

    def execute_signout(self, roomid="", seatid="", reserve_id=""):
        """
        执行单次签退（结束使用，释放座位）

        注意：超星的签退接口是 signback（区别于"暂离" leave）。

        Args:
            roomid: 房间ID（如 "18888"）
            seatid: 座位号（如 "188"）
            reserve_id: 预约ID（可选，提供则直接使用，否则自动从预约信息接口获取）

        Returns:
            dict: {"success": bool, "message": str, "reserve_id": str, "roomid": str, "seatid": str}
        """
        return self._request_sign_action(
            self.signout_url, "签退", roomid, seatid, reserve_id
        )

    # ─── 签到状态查询 ─────────────────────────────────────────

    def check_signin_status(self, roomid, seatid):
        """查询指定座位的预约状态（status 为服务器原始值，各校含义可能不同）"""
        info = self.get_reserve_info(roomid, seatid)
        seat_reserve = (info or {}).get("seatReserve") or {}
        if not seat_reserve:
            return {"has_reservation": False, "reserve_id": "", "status": None}
        return {
            "has_reservation": True,
            "reserve_id": str(seat_reserve.get("id", "")),
            "status": seat_reserve.get("status"),
        }

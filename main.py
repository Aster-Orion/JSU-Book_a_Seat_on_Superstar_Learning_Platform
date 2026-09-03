# -*- coding: utf-8 -*-
"""
超星图书馆座位预约工具
自动登录并预约指定的座位
"""
import json
import time
import argparse
import os
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from utils import reserve, get_user_credentials, send_failure_email, SeatSignIn, send_sign_failure_email

# 时间获取函数（支持时区偏移）
get_current_time = lambda action: time.strftime("%H:%M:%S", time.localtime(time.time() + 8*3600)) if action else time.strftime("%H:%M:%S", time.localtime(time.time()))
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
get_current_dayofweek = lambda action: WEEKDAYS[time.localtime(time.time() + 8*3600).tm_wday] if action else WEEKDAYS[time.localtime(time.time()).tm_wday]

# === 配置参数 ===
SLEEPTIME = 0.5           # 每次尝试的间隔时间（秒）
SEGMENT_INTERVAL = 0.4    # 同一座位不同时段之间的提交间隔（秒），给服务器缓冲；第一段仍准点发送
STARTTIME = "08:00:00"  # 预约正式开放时间（开始时间，北京时间），到点后才正式提交预约
LOGIN_AHEAD = 5          # 提前多少秒开始登录（即 开始登录时间 = STARTTIME - LOGIN_AHEAD 秒）
ENDTIME = "08:01:00"    # 停止尝试的时间（超过学校关闭时间1分钟）
ENABLE_SLIDER = False   # 是否启用滑块验证
MAX_ATTEMPT = 2         # 单次预约的最大尝试次数
RESERVE_NEXT_DAY = True # 预约明天的座位而不是今天
ENABLE_EMAIL = True     # 是否开启邮箱提醒（预约失败或开始时间大于结束时间时发送）

# === 签到 / 签退配置参数 ===
ENABLE_RESERVE = True   # 是否开启自动预约（默认开启，保持原行为）
ENABLE_SIGNIN = True   # 是否开启自动签到
ENABLE_SIGNOUT = True  # 是否开启自动签退
SIGNIN_TIME = "08:00:30"  # 签到时间（北京时间），须在 STARTTIME~ENDTIME 之间，与预约共用窗口
SIGNOUT_TIME = "21:29:30" # 签退开始时间（北京时间）
SIGNOUT_END_TIME = "21:30:00" # 签退结束时间（北京时间），超过后停止重试
SIGNOUT_LEAD = 300       # 签退提前量（秒）：定时器早于签退时间启动时的允许提前量，早于该时间则本次跳过签退


def time_add_seconds(hms, seconds):
    """对 HH:MM:SS 字符串做秒数加减，返回 HH:MM:SS"""
    h, m, s = map(int, hms.split(":"))
    total = (h * 3600 + m * 60 + s + seconds) % 86400
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def wait_until(target_time, action):
    """阻塞等待，直到当前时间 >= target_time（HH:MM:SS 字符串，北京时间）"""
    while get_current_time(action) < target_time:
        time.sleep(0.2)


def should_reserve_today(daysofweek, current_dayofweek):
    """判断今天是否需要预约（daysofweek 为空表示每天都预约）"""
    return (not daysofweek) or (current_dayofweek in daysofweek)



def login_all(users, usernames, passwords, action):
    """登录所有今天需要预约的用户（只登录，不提交预约）

    返回预约任务列表，每个元素为 (reserve实例, times, roomid, seatid, index)。
    """
    current_dayofweek = get_current_dayofweek(action)
    tasks = []

    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()

        # 座位ID转为列表（若为字符串）
        if isinstance(seatid, str):
            seatid = [seatid]

        # 检查今天是否需要预约
        if not should_reserve_today(daysofweek, current_dayofweek):
            logging.info("今天没有预订!")
            continue

        if action:
            username, password = usernames.split(',')[index], passwords.split(',')[index]

        logging.info(f"开始登录: {username}")

        # 创建预约实例并登录（此时不提交预约）
        s = reserve(sleep_time=SLEEPTIME, max_attempt=MAX_ATTEMPT, enable_slider=ENABLE_SLIDER, reserve_next_day=RESERVE_NEXT_DAY, segment_interval=SEGMENT_INTERVAL)
        login_ok, login_msg = s.login(username, password)
        if not login_ok:
            logging.error(f"用户 {username} 登录失败，跳过该用户预约，原因: {login_msg}")
            continue
        tasks.append((s, times, roomid, seatid, index))

    return tasks


def reserve_all(tasks, action, success_list):
    """对已登录的任务执行预约提交，返回更新后的 success_list"""
    for s, times, roomid, seatid, index in tasks:
        # 跳过已成功的预约
        if success_list[index]:
            continue
        logging.info(f"开始预约: 座位 {seatid}")
        suc = s.submit(times, roomid, seatid, action)
        success_list[index] = suc

    return success_list


def send_success_email(tasks):
    """发送邮件通知（合并所有成功的预约，只发送一次）"""
    for s, _, _, _, _ in tasks:
        if s.success_results:
            s.send_all_results_email()
            break  # 只发送一次（合并所有结果）


def collect_failures(users, success_list, action, usernames):
    """收集预约失败信息（预约请求失败 或 开始时间大于结束时间）

    返回失败信息列表，每个元素为 dict，用于发送失败邮件。
    """
    current_dayofweek = get_current_dayofweek(action)
    failures = []

    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()

        # 跳过今天无需预约的用户
        if not should_reserve_today(daysofweek, current_dayofweek):
            continue

        # 座位ID转为列表（若为字符串）
        if isinstance(seatid, str):
            seatid = [seatid]

        # Action 模式下账号被环境变量覆盖
        if action:
            username = usernames.split(',')[index]

        start_time, end_time = times[0], times[1]

        # 条件一：开始时间大于结束时间（配置异常）
        if start_time > end_time:
            failures.append({
                "username": username,
                "roomid": roomid,
                "seatid": ",".join(seatid),
                "start_time": start_time,
                "end_time": end_time,
                "reason": "开始时间大于结束时间",
            })
        # 条件二：预约请求失败（未预约成功）
        elif not success_list[index]:
            failures.append({
                "username": username,
                "roomid": roomid,
                "seatid": ",".join(seatid),
                "start_time": start_time,
                "end_time": end_time,
                "reason": "预约请求失败",
            })

    return failures


def _sign_login(users, usernames, passwords, action, action_name, reserve_tasks=None):
    """登录所有今天需要签到/签退的用户，返回任务列表。

    每个任务为 dict：
        s: SeatSignIn 实例（登录失败时为 None）
        roomid: 房间ID
        seatid: 座位号列表
        username: 账号
        index: 在 users 中的下标
        login_err: 登录失败原因（成功为 None）

    若提供 reserve_tasks，则复用其中已登录的 Session，不再重新登录。
    """
    current_dayofweek = get_current_dayofweek(action)
    tasks = []
    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()
        if isinstance(seatid, str):
            seatid = [seatid]
        if not should_reserve_today(daysofweek, current_dayofweek):
            continue
        if action:
            username, password = usernames.split(',')[index], passwords.split(',')[index]

        s = None
        login_err = None

        # 尝试从预约任务中获取已登录的 Session
        if reserve_tasks is not None:
            for task in reserve_tasks:
                # task 结构: (reserve_instance, times, roomid, seatid, idx)
                if task[4] == index:
                    # 复用会话
                    s = SeatSignIn(session=task[0].requests)
                    logging.info(f"复用预约会话，用户 {username} 无需重新登录")
                    break
            if s is None:
                logging.warning(f"用户 {username} 在预约任务中未找到对应会话，将重新登录")

        # 若未能复用，则执行完整登录流程（兼容原有逻辑）
        if s is None:
            logging.info(f"开始{action_name}登录: {username}")
            s = SeatSignIn()
            login_ok, login_msg = s.login(username, password)
            if not login_ok:
                logging.error(f"用户 {username} {action_name}登录失败，原因: {login_msg}")
                login_err = login_msg
                s = None

        tasks.append({
            "s": s,
            "roomid": roomid,
            "seatid": seatid,
            "username": username,
            "index": index,
            "login_err": login_err,
        })
    return tasks


def _sign_retry_once(tasks, action_name, done, fail_msg, execute):
    """执行一轮签到/签退：对尚未完成的座位各尝试一次，更新 done 与 fail_msg。"""
    for task in tasks:
        if task["login_err"]:
            continue
        for seat in task["seatid"]:
            key = (task["index"], seat)
            if done.get(key):
                continue
            result = execute(task["s"], task["roomid"], seat)
            if result.get("success"):
                done[key] = True
                logging.info(f"{action_name}成功 {task['username']} - 房间{task['roomid']} 座位{seat}")
            elif result.get("terminal"):
                # 终态（无预约 / 已签退 / 预约已失效等）：无需再重试，也不计入失败邮件
                done[key] = True
                logging.info(f"{action_name}跳过 {task['username']} - 房间{task['roomid']} 座位{seat}: {result.get('message')}")
            else:
                fail_msg[key] = result.get("message")
                logging.info(f"{action_name}失败 {task['username']} - 房间{task['roomid']} 座位{seat}: {result.get('message')}")


def _sign_all_done(tasks, done):
    """判断所有可签到/签退座位是否都已完成（登录失败的不计入重试）。"""
    return all(
        done.get((t["index"], seat))
        for t in tasks if not t["login_err"]
        for seat in t["seatid"]
    )


def _send_sign_failures(tasks, done, fail_msg, action_name):
    """汇总签到/签退失败项并发送邮件提醒（登录失败 + 超时未完成的座位）。"""
    failures = []
    for task in tasks:
        if task["login_err"]:
            failures.append({
                "username": task["username"],
                "roomid": task["roomid"],
                "seatid": ",".join(str(x) for x in task["seatid"]),
                "reason": f"登录失败: {task['login_err']}",
            })
            continue
        for seat in task["seatid"]:
            if not done.get((task["index"], seat)):
                failures.append({
                    "username": task["username"],
                    "roomid": task["roomid"],
                    "seatid": seat,
                    "reason": fail_msg.get((task["index"], seat), f"{action_name}失败"),
                })

    if failures:
        if ENABLE_EMAIL:
            logging.info(f"{action_name}存在 {len(failures)} 条失败项，发送失败邮件提醒")
            send_sign_failure_email(failures, action_name)
        else:
            logging.info(f"{action_name}存在 {len(failures)} 条失败项，但邮箱提醒已关闭（ENABLE_EMAIL=False），不发送邮件")
    else:
        logging.info(f"所有座位均已成功{action_name}")


def signout_all(users, usernames, passwords, action):
    """对已签到座位执行自动签退（窗口 [SIGNOUT_TIME, SIGNOUT_END_TIME]，超时停止重试）"""
    wait_until(SIGNOUT_TIME, action)
    logging.info(f"到达签退时间 {SIGNOUT_TIME}，开始签退（重试窗口 {SIGNOUT_TIME}~{SIGNOUT_END_TIME}）")

    tasks = _sign_login(users, usernames, passwords, action, "签退")
    done, fail_msg = {}, {}
    while get_current_time(action) < SIGNOUT_END_TIME:
        _sign_retry_once(tasks, "签退", done, fail_msg, lambda s, r, seat: s.execute_signout(r, seat))
        if _sign_all_done(tasks, done):
            break
        time.sleep(SLEEPTIME)

    _send_sign_failures(tasks, done, fail_msg, "签退")


def main(users=False, action=False):
    """主流程：先预约+签到（共用 [STARTTIME, ENDTIME] 窗口），再签退（[SIGNOUT_TIME, SIGNOUT_END_TIME] 窗口）"""
    current_time = get_current_time(action)
    logging.info(f"开始时间 {current_time} ({'action' if action else 'preview'})")
    logging.info(f"预约设置: 开放时间={STARTTIME} 提前登录={LOGIN_AHEAD}s 结束时间={ENDTIME} 睡眠={SLEEPTIME}s 滑块={ENABLE_SLIDER} 次日={RESERVE_NEXT_DAY} 启用={ENABLE_RESERVE}")
    logging.info(f"签到设置: 启用={ENABLE_SIGNIN} 时间={SIGNIN_TIME} | 签退设置: 启用={ENABLE_SIGNOUT} 窗口={SIGNOUT_TIME}~{SIGNOUT_END_TIME}")

    usernames, passwords = None, None
    if action:
        usernames, passwords = get_user_credentials(action)
        if len(usernames.split(",")) != len(users):
            raise Exception("用户号应与配置号匹配")

    current_dayofweek = get_current_dayofweek(action)
    # 计算今天应该预约的座位数
    today_reservation_num = sum(1 for d in users if should_reserve_today(d.get('daysofweek'), current_dayofweek))

    # 限制启动时间：当前时间若已超过所有执行窗口，直接退出，避免空跑/无限重复请求
    latest_end = "00:00:00"
    if ENABLE_RESERVE or ENABLE_SIGNIN:
        latest_end = max(latest_end, ENDTIME)
    if ENABLE_SIGNOUT:
        latest_end = max(latest_end, SIGNOUT_END_TIME)
    if get_current_time(action) > latest_end:
        logging.info(f"当前时间已超过所有执行窗口（最晚 {latest_end}），程序退出")
        return

    # ============ 1) 预约 + 签到（共用 [STARTTIME, ENDTIME] 窗口） ============
    if (ENABLE_RESERVE or ENABLE_SIGNIN) and today_reservation_num > 0:
        if get_current_time(action) >= ENDTIME:
            logging.info("当前时间已超过预约/签到窗口（ENDTIME），跳过预约与签到")
        else:
            success_list = [False] * len(users)
            tasks = None  # 在 if ENABLE_RESERVE 外部初始化
            signin_tasks = None
            signin_done, signin_fail_msg = {}, {}

            # 预约：提前到 STARTTIME - LOGIN_AHEAD 登录（仅预约需要）
            if ENABLE_RESERVE:
                login_start_time = time_add_seconds(STARTTIME, -LOGIN_AHEAD)
                wait_until(login_start_time, action)
                logging.info(f"到达登录时间 {login_start_time}，开始登录（提前 {LOGIN_AHEAD} 秒）")
                tasks = login_all(users, usernames, passwords, action)

            # 统一等待到窗口起点（预约从 STARTTIME；仅签到则从 SIGNIN_TIME）
            window_start = STARTTIME if ENABLE_RESERVE else SIGNIN_TIME
            wait_until(window_start, action)
            logging.info(f"到达执行时间 {window_start}，开始执行（窗口 {window_start}~{ENDTIME}）")

            # 主循环：预约不断重试；到 SIGNIN_TIME 后同时执行签到，直到超时或全部完成
            attempt_times = 0
            while get_current_time(action) < ENDTIME:
                attempt_times += 1
                now = get_current_time(action)

                suc_num = 0
                # 预约重试
                if ENABLE_RESERVE and suc_num < today_reservation_num:
                    success_list = reserve_all(tasks, action, success_list)
                    suc_num = sum(success_list)
                    logging.info(f"尝试 #{attempt_times} | 当前时间 {now} | 成功 {suc_num}/{today_reservation_num}")

                # 检查签到时间：到 SIGNIN_TIME 后执行签到（8:00-8:01 既预约也签到）
                if ENABLE_SIGNIN and now >= SIGNIN_TIME:
                    if signin_tasks is None:
                        logging.info(f"到达签到时间 {SIGNIN_TIME}，开始签到")
                        signin_tasks = _sign_login(users, usernames, passwords, action, "签到", reserve_tasks=tasks)
                    _sign_retry_once(signin_tasks, "签到", signin_done, signin_fail_msg,
                                     lambda s, r, seat: s.execute_signin(r, seat))

                # 退出条件：预约全部成功 且 签到全部完成
                reserve_done = (not ENABLE_RESERVE) or (sum(success_list) == today_reservation_num)
                signin_done_all = (not ENABLE_SIGNIN) or (signin_tasks is not None and _sign_all_done(signin_tasks, signin_done))
                if reserve_done and signin_done_all:
                    logging.info("预约与签到任务完成，结束本次窗口循环")
                    break

                time.sleep(SLEEPTIME)

            # 预约失败邮件（原逻辑）
            if ENABLE_RESERVE:
                failures = collect_failures(users, success_list, action, usernames)
                if failures and ENABLE_EMAIL:
                    logging.info(f"预约存在失败项，发送失败邮件提醒，共 {len(failures)} 条")
                    send_failure_email(failures)
                elif failures:
                    logging.info(f"预约存在 {len(failures)} 条失败项，但邮箱提醒已关闭（ENABLE_EMAIL=False），不发送邮件")
                else:
                    logging.info("所有座位均已成功预约，不发送邮件")

            # 签到失败邮件
            if ENABLE_SIGNIN and signin_tasks is not None:
                _send_sign_failures(signin_tasks, signin_done, signin_fail_msg, "签到")
    elif today_reservation_num == 0:
        logging.info("今天无需预约任何座位!")

    # ============ 2) 签退（窗口 [SIGNOUT_TIME, SIGNOUT_END_TIME]） ============
    # 仅当已进入"签退时段"（SIGNOUT_TIME 前 SIGNOUT_LEAD 秒内）才执行签退
    if ENABLE_SIGNOUT and today_reservation_num > 0:
        now = get_current_time(action)
        signout_gate = time_add_seconds(SIGNOUT_TIME, -SIGNOUT_LEAD)
        if now >= SIGNOUT_END_TIME:
            logging.info("当前时间已超过签退窗口（SIGNOUT_END_TIME），跳过签退")
        elif now >= signout_gate:
            signout_all(users, usernames, passwords, action)
        else:
            logging.info(f"当前时间 {now} 未进入签退时段（{signout_gate} 之后才执行），本次跳过签退")


def debug(users, action=False):
    """调试模式：立即同步测试预约、签到与签退（不等待时间点，顺序执行一次）"""
    logging.info(f"调试模式启动 ({'action' if action else 'preview'})")
    logging.info("调试：立即执行 预约 + 签到 + 签退，不等待 STARTTIME / SIGNIN_TIME / SIGNOUT_TIME")

    if action:
        usernames, passwords = get_user_credentials(action)

    current_dayofweek = get_current_dayofweek(action)

    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()

        # 座位ID转为列表（若为字符串）
        if isinstance(seatid, str):
            seatid = [seatid]

        # 检查今天是否需要操作
        if not should_reserve_today(daysofweek, current_dayofweek):
            logging.info(f"用户 {username} 今天无需操作，跳过")
            continue

        if action:
            username, password = usernames.split(',')[index], passwords.split(',')[index]

        logging.info(f"===== 调试账号 {username} 房间 {roomid} 座位 {seatid} =====")

        # 1) 立即预约（保留原调试的预约逻辑）
        r = reserve(
            sleep_time=SLEEPTIME,
            max_attempt=MAX_ATTEMPT,
            enable_slider=ENABLE_SLIDER,
            reserve_next_day=RESERVE_NEXT_DAY,
            segment_interval=SEGMENT_INTERVAL,
        )
        r.login(username, password)
        suc = r.submit(times, roomid, seatid, action)
        logging.info(f"预约结果 {username}: 成功={suc}")
        if suc and r.success_results:
            r.send_all_results_email()

        # 2) 立即签到 / 签退（复用 SeatSignIn，与预约同一套登录逻辑）
        s = SeatSignIn()
        login_ok, login_msg = s.login(username, password)
        if not login_ok:
            logging.error(f"用户 {username} 登录失败: {login_msg}")
            continue

        # 3) 立即测试签到
        for seat in seatid:
            res = s.execute_signin(roomid, seat)
            logging.info(f"签到结果 {username} - 房间{roomid} 座位{seat}: 成功={res.get('success')} | {res.get('message')}")

        # 4) 立即测试签退
        for seat in seatid:
            res = s.execute_signout(roomid, seat)
            logging.info(f"签退结果 {username} - 房间{roomid} 座位{seat}: 成功={res.get('success')} | {res.get('message')}")

def get_roomid(args1, args2):
    """获取房间ID（用于探测）"""
    username = input("请输入用户名: ")
    password = input("请输入密码: ")
    
    s = reserve(sleep_time=SLEEPTIME, max_attempt=MAX_ATTEMPT, enable_slider=ENABLE_SLIDER, reserve_next_day=RESERVE_NEXT_DAY, segment_interval=SEGMENT_INTERVAL)
    s.login(username=username, password=password)

    deptid_enc = input("请输入deptIdEnc: ")
    s.roomid(deptid_enc)


if __name__ == "__main__":
    # 读取命令行参数
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    parser = argparse.ArgumentParser(prog='Chao Xing seat auto reserve')
    parser.add_argument('-u', '--user', default=config_path, help='user config file')
    parser.add_argument('-m', '--method', default="reserve", choices=["reserve", "debug", "room"], help='execution method')
    parser.add_argument('-a', '--action', action="store_true", help='enable GitHub Action mode')
    args = parser.parse_args()
    
    # 执行对应的方法
    func_dict = {"reserve": main, "debug": debug, "room": get_roomid}
    with open(args.user, "r+", encoding="utf-8") as data:
        usersdata = json.load(data)["reserve"]
    func_dict[args.method](usersdata, args.action)

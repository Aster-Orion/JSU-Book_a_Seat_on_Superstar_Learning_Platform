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

from utils import reserve, get_user_credentials, send_failure_email

# 时间获取函数（支持时区偏移）
get_current_time = lambda action: time.strftime("%H:%M:%S", time.localtime(time.time() + 8*3600)) if action else time.strftime("%H:%M:%S", time.localtime(time.time()))
get_current_dayofweek = lambda action: time.strftime("%A", time.localtime(time.time() + 8*3600)) if action else time.strftime("%A", time.localtime(time.time()))

# === 配置参数 ===
SLEEPTIME = 0.5           # 每次尝试的间隔时间（秒）
SEGMENT_INTERVAL = 0.4    # 同一座位不同时段之间的提交间隔（秒），给服务器缓冲；第一段仍准点发送
STARTTIME = "08:00:00"  # 预约正式开放时间（开始时间，北京时间），到点后才正式提交预约
LOGIN_AHEAD = 5          # 提前多少秒开始登录（即 开始登录时间 = STARTTIME - LOGIN_AHEAD 秒）
ENDTIME = "08:01:00"    # 停止尝试的时间（超过学校关闭时间1分钟）
ENABLE_SLIDER = False   # 是否启用滑块验证
MAX_ATTEMPT = 2         # 单次预约的最大尝试次数
RESERVE_NEXT_DAY = True # 预约明天的座位而不是今天


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
        s.get_login_status()
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


def main(users, action=False):
    """主预约流程：先等待到登录时间登录，再等到开放时间正式预约"""
    current_time = get_current_time(action)
    logging.info(f"开始时间 {current_time} ({'action' if action else 'preview'})")
    logging.info(f"预约设置: 开放时间={STARTTIME} 提前登录={LOGIN_AHEAD}s 结束时间={ENDTIME} 睡眠={SLEEPTIME}s 滑块={ENABLE_SLIDER} 次日={RESERVE_NEXT_DAY}")

    usernames, passwords = None, None
    if action:
        usernames, passwords = get_user_credentials(action)
        if len(usernames.split(",")) != len(users):
            raise Exception("用户号应与配置号匹配")

    current_dayofweek = get_current_dayofweek(action)
    # 计算今天应该预约的座位数
    today_reservation_num = sum(1 for d in users if should_reserve_today(d.get('daysofweek'), current_dayofweek))
    if today_reservation_num == 0:
        logging.info("今天无需预约任何座位!")
        return

    success_list = [False] * len(users)

    # 计算登录开始时间 = 开放时间 - 提前秒数（例如 08:00:00 - 5s = 07:59:55）
    login_start_time = time_add_seconds(STARTTIME, -LOGIN_AHEAD)

    # 1) 等待到登录开始时间（GitHub 当前时间未到则等待）
    wait_until(login_start_time, action)
    logging.info(f"到达登录时间 {login_start_time}，开始登录（提前 {LOGIN_AHEAD} 秒）")
    tasks = login_all(users, usernames, passwords, action)

    # 2) 等待到正式开放时间，再正式提交预约
    wait_until(STARTTIME, action)
    logging.info(f"到达预约时间 {STARTTIME}，正式开始预约")

    # 3) 主循环：不断尝试预约直到超时或全部成功
    attempt_times = 0
    while get_current_time(action) < ENDTIME:
        attempt_times += 1
        success_list = reserve_all(tasks, action, success_list)

        current_time = get_current_time(action)
        logging.info(f"尝试 #{attempt_times} | 当前时间 {current_time} | 成功 {sum(success_list)}/{today_reservation_num}")

        # 检查是否全部预约成功
        if sum(success_list) == today_reservation_num:
            logging.info("已成功预订所有座位!")
            # send_success_email(tasks)
            return

        # 控制轮询间隔，避免登录失败/无任务时空转
        time.sleep(SLEEPTIME)

    # 超时仍未全部成功时，发送失败邮件提醒（仅当预约失败或开始时间大于结束时间）
    failures = collect_failures(users, success_list, action, usernames)
    if failures:
        logging.info(f"预约存在失败项，发送失败邮件提醒，共 {len(failures)} 条")
        send_failure_email(failures)
    else:
        logging.info("所有座位均已成功预约，不发送邮件")


def debug(users, action=False):
    """调试模式：单次预约并发送邮件"""
    logging.info(f"调试模式启动 ({'action' if action else 'preview'})")
    logging.info(f"配置: 睡眠={SLEEPTIME}s 滑块={ENABLE_SLIDER} 次日={RESERVE_NEXT_DAY}")
    
    if action:
        usernames, passwords = get_user_credentials(action)
    
    current_dayofweek = get_current_dayofweek(action)
    
    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()
        
        # 座位ID转为列表（若为字符串）
        if isinstance(seatid, str):
            seatid = [seatid]
        
        # 检查今天是否需要预约
        if current_dayofweek not in daysofweek:
            logging.info("今天没有预订")
            continue
        
        if action:
            username, password = usernames.split(',')[index], passwords.split(',')[index]
        
        logging.info(f"预约: {username} - {times} - {seatid}")
        
        # 执行预约
        s = reserve(sleep_time=SLEEPTIME, max_attempt=MAX_ATTEMPT, enable_slider=ENABLE_SLIDER, reserve_next_day=RESERVE_NEXT_DAY, segment_interval=SEGMENT_INTERVAL)
        s.get_login_status()
        s.login(username, password)
        suc = s.submit(times, roomid, seatid, action)
        
        # 发送邮件并返回
        if suc and s.success_results:
            s.send_all_results_email()
        return

def get_roomid(args1, args2):
    """获取房间ID（用于探测）"""
    username = input("请输入用户名: ")
    password = input("请输入密码: ")
    
    s = reserve(sleep_time=SLEEPTIME, max_attempt=MAX_ATTEMPT, enable_slider=ENABLE_SLIDER, reserve_next_day=RESERVE_NEXT_DAY, segment_interval=SEGMENT_INTERVAL)
    s.get_login_status()
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

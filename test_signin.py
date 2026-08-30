# -*- coding: utf-8 -*-
"""
单独测试签到 / 签退（调试用，不等待时间，立即执行一次）

用法：
  python test_signin.py signin                # 立即测试签到
  python test_signin.py signout               # 立即测试签退
  python test_signin.py signin [roomid] [seat]      # 指定房间和座位测试签到
  python test_signin.py signout [roomid] [seat]     # 指定房间和座位测试签退
  
  python test_signin.py signout 18888 001     # 指定房间和座位测试签退

前置条件：
  1. 在 config.json 中填好 username / password（本地模式直接读取）。
  2. 当天需已有有效预约，签到/签退才能匹配到 reserveId。
"""
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

from utils import SeatSignIn


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("signin", "signout"):
        print("用法: python test_signin.py signin|signout [roomid] [seatid]")
        sys.exit(1)

    mode = sys.argv[1]
    with open("config.json", encoding="utf-8") as f:
        users = json.load(f)["reserve"]

    for user in users:
        username, password, times, roomid, seatid, daysofweek = user.values()
        if isinstance(seatid, str):
            seatid = [seatid]
        # 命令行可覆盖房间/座位，方便快速指定
        if len(sys.argv) >= 4:
            roomid = sys.argv[2]
            seatid = [sys.argv[3]]

        logging.info(f"===== 测试{mode}：账号 {username} 房间 {roomid} 座位 {seatid} =====")
        s = SeatSignIn()
        s.get_login_status()
        ok, msg = s.login(username, password)
        if not ok:
            logging.error(f"登录失败: {msg}")
            continue

        for seat in seatid:
            if mode == "signin":
                r = s.execute_signin(roomid, seat)
            else:
                r = s.execute_signout(roomid, seat)
            logging.info(
                f"结果 {username} 房间{roomid} 座位{seat} -> 成功={r['success']} | {r['message']}"
            )


if __name__ == "__main__":
    main()

import json
import sys
import os
import logging
import time

# 以项目目录作为工作目录：主流程与 utils 均以相对路径读取 config.json
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def handler(event, context):
    # 设置时区为北京时间（服务器已为北京时间时 action=False 依赖本机时区；加 tzset 确保生效）
    os.environ['TZ'] = 'Asia/Shanghai'
    try:
        time.tzset()
    except AttributeError:
        pass  # Windows 无 tzset，忽略

    import main

    # 1. 读取 config.json
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps(f'读取 config.json 失败: {str(e)}')
        }

    reserve_list = config.get('reserve', [])
    if not reserve_list:
        return {
            'statusCode': 400,
            'body': json.dumps('config.json 中无 reserve 配置')
        }

    # 2. 从环境变量获取账号密码
    usernames_str = os.environ.get('USERNAMES', '')
    passwords_str = os.environ.get('PASSWORDS', '')
    if not usernames_str or not passwords_str:
        return {
            'statusCode': 400,
            'body': json.dumps('环境变量 USERNAMES 或 PASSWORDS 未设置')
        }

    usernames = [u.strip() for u in usernames_str.split(',') if u.strip()]
    passwords = [p.strip() for p in passwords_str.split(',') if p.strip()]

    # 3. 校验数量是否匹配
    if len(usernames) != len(reserve_list) or len(passwords) != len(reserve_list):
        return {
            'statusCode': 400,
            'body': json.dumps(
                f'账号密码数量与 reserve 列表长度不匹配: '
                f'账号 {len(usernames)} 个, 密码 {len(passwords)} 个, 配置 {len(reserve_list)} 个'
            )
        }

    # 4. 构建 users 列表（覆盖账号密码，其他字段从 config 读取）
    users = []
    for i, item in enumerate(reserve_list):
        user_dict = {
            'username': usernames[i],
            'password': passwords[i],
            'time': item.get('time'),          # 例如 ["08:00", "21:30"]
            'roomid': item.get('roomid'),      # 例如 "18888"
            'seatid': item.get('seatid'),      # 例如 ["188"]
            'daysofweek': item.get('daysofweek', [])
        }
        users.append(user_dict)

    # 5. 调用主预约函数
    try:
        main.main(users, action=False)   # action=True 启用时区校正
        return {
            'statusCode': 200,
            'body': json.dumps('预约任务执行完成')
        }
    except Exception as e:
        logging.error(f'执行预约失败: {e}', exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps(f'执行失败: {str(e)}')
        }
# 超星图书馆座位自动预约工具

基于 Python 的超星学习通（Chaoxing）图书馆座位自动预约脚本。支持**本地运行**和 **GitHub Actions 定时自动预约**两种方式，除自动预约外还支持**自动签到、自动签退**；预约 / 签到 / 签退失败时均可通过邮件通知结果。

 “当知识织就的茧房温柔剥落，你在图书馆沉淀的岁月自会羽化成风，托起你向青空而去的锋利轨迹。”

> ⚠️ **免责声明**：本项目仅供学习交流使用，请遵守学校图书馆的相关规定，合理使用，切勿滥用或用于任何商业用途。因使用本脚本产生的一切后果由使用者自行承担。


---

## 目录

- [超星图书馆座位自动预约工具](#超星图书馆座位自动预约工具)
        - [“当知识织就的茧房温柔剥落，你在图书馆沉淀的岁月自会羽化成风，托起你向青空而去的锋利轨迹。”](#当知识织就的茧房温柔剥落你在图书馆沉淀的岁月自会羽化成风托起你向青空而去的锋利轨迹)
  - [目录](#目录)
  - [一、项目简介](#一项目简介)
  - [二、功能特性](#二功能特性)
  - [三、目录结构](#三目录结构)
  - [四、核心工作流程（全流程）](#四核心工作流程全流程)
    - [主流程三阶段（预约 → 签到 → 签退）](#主流程三阶段预约--签到--签退)
  - [五、环境依赖](#五环境依赖)
  - [六、配置文件说明](#六配置文件说明)
  - [七、本地运行](#七本地运行)
    - [1. 直接运行](#1-直接运行)
    - [2. 命令行参数](#2-命令行参数)
    - [3. Windows 批处理](#3-windows-批处理)
  - [八、GitHub Actions 自动预约](#八github-actions-自动预约)
    - [1. 工作流文件](#1-工作流文件)
    - [2. 配置 Secrets](#2-配置-secrets)
    - [3. 时区说明](#3-时区说明)
  - [九、关键参数说明](#九关键参数说明)
    - [时间轴（双定时器运行）](#时间轴双定时器运行)
  - [十、自动签到与签退](#十自动签到与签退)
    - [1. 开关配置](#1-开关配置)
    - [2. 单独测试（调试）](#2-单独测试调试)
    - [3. 失败邮件提醒](#3-失败邮件提醒)
  - [十一、常见失败原因](#十一常见失败原因)
    - [预约失败](#预约失败)
    - [签到 / 签退失败](#签到--签退失败)
  - [十二、常见问题](#十二常见问题)

---

## 一、项目简介

本项目用于自动登录超星（学习通）图书馆座位预约系统，并在开放预约的时间点自动抢占指定房间、指定座位。脚本通过逆向超星登录与预约接口、使用 AES 加密账号密码、模拟移动端请求完成预约，同时支持：

- 多账号、多座位并行预约；
- 按星期几自动判断当天是否需要预约；
- 超长时段（超过 5 小时）自动切分；
- 预约成功后自动发送邮件通知；
- 按指定时间自动签到、自动签退（可独立开关）；
- 签到 / 签退失败时自动发送邮件提醒。

## 二、功能特性

| 特性 | 说明 |
| --- | --- |
| 自动登录 | 使用 AES 加密账号密码，模拟超星登录接口 |
| 定时抢座 | 主循环从启动时间一直重试到 `ENDTIME`，抢占开放瞬间的座位 |
| 多账号支持 | `USERNAMES` / `PASSWORDS` 环境变量逗号分隔，支持多人 |
| 星期过滤 | 每个座位可配置 `daysofweek`，仅在指定星期预约 |
| 时段切分 | 单次预约超过 5 小时自动拆分为多个时段 |
| 自动签到 | 到 `SIGNIN_TIME` 后对已预约座位自动签到 |
| 自动签退 | 到 `SIGNOUT_TIME` 后对已签到座位自动签退（结束使用、释放座位） |
| 邮件通知 | 预约成功后合并所有结果，通过 SMTP 发送邮件；签到/签退失败也会邮件提醒 |
| 双端运行 | 支持本地 Python 直接运行与 GitHub Actions 云端定时运行 |
| 滑块验证（可选） | 可开启滑块验证码自动识别（需额外依赖） |

## 三、目录结构

```
.
├── main.py                 # 程序入口：参数解析、主预约循环、签到/签退流程、调试/探测模式
├── config.json             # 用户配置（座位/时段/邮件等，已被 .gitignore 忽略）
├── config.example.json     # 配置文件模板（可复制后修改）
├── requirements.txt        # Python 依赖清单
├── chaoxing.bat            # Windows 批处理一键运行脚本
├── test_signin.py          # 单独测试签到/签退的调试脚本（不等待时间，立即执行一次）
├── utils/
│   ├── __init__.py         # 导出 get_user_credentials、send_failure_email、SeatSignIn 等
│   ├── encrypt.py          # AES 加密、MD5 签名、滑块验证码 key 生成
│   ├── reserve.py          # reserve 类：登录、获取 token、提交预约、发送邮件
│   └── signin.py           # SeatSignIn 类：登录、查询预约ID、签到、签退、发送失败邮件
└── .github/
    └── workflows/
        └── reserve.yml     # GitHub Actions 定时自动预约工作流
```

## 四、核心工作流程（全流程）

一次完整的预约执行流程如下：

```mermaid
flowchart TD
    A[启动 main.py] --> B{是否为 action 模式?}
    B -->|是 -a| C[从环境变量 USERNAMES/PASSWORDS 读取账号密码]
    B -->|否| D[使用 config.json 中的账号密码]
    C --> E[读取 config.json 的 reserve 列表]
    D --> E
    E --> F[遍历每个用户配置]
    F --> G{今天是星期几<br/>是否在 daysofweek 中?}
    G -->|否| F
    G -->|是| H[创建 reserve 实例并初始化会话]
    H --> I[获取登录状态 get_login_status]
    I --> J[AES 加密账号密码并登录 login]
    J --> K[获取预约页面 token 与加密值]
    K --> L{是否开启滑块验证?}
    L -->|是| M[解析滑块验证码]
    L -->|否| N[提交预约 submit]
    M --> N
    N --> O{预约成功?}
    O -->|否| P[等待 SLEEPTIME 秒后重试]
    P --> N
    O -->|是| Q[记录成功结果]
    Q --> F
    F --> R[发送汇总邮件通知]
    R --> S{全部座位成功 或 超过 ENDTIME?}
    S -->|否| T[下一轮尝试]
    T --> F
    S -->|是| U[结束]
```

**关键环节说明：**

1. **登录**：账号密码先经过 `AES_Encrypt` 加密，再通过 `passport2.chaoxing.com/fanyalogin` 接口登录。
2. **获取 token**：从预约页面提取 `submit_enc` 隐藏域的值，作为后续提交的加密种子。
3. **提交预约**：对预约参数按键排序后拼接，计算 MD5 签名（`verify_param`），再 POST 到预约接口。
4. **主循环**：`main()` 会不断重试，直到所有座位预约成功或超过 `ENDTIME`（默认 08:01:00）。

### 主流程三阶段（预约 → 签到 → 签退）

`main()` 顶层依次检查三个开关，决定是否执行对应阶段（签到 / 签退可独立于预约开启，也可单独开启）：

```mermaid
flowchart TD
    A[启动 main] --> B{ENABLE_RESERVE<br/>且今天有需预约座位?}
    B -->|是| C[等待 STARTTIME 抢座预约<br/>见上方预约流程]
    B -->|否| D
    C --> D{ENABLE_SIGNIN?}
    D -->|是| E[等待 SIGNIN_TIME<br/>对已预约座位执行签到]
    D -->|否| F
    E --> F{ENABLE_SIGNOUT?}
    F -->|是| G[等待 SIGNOUT_TIME<br/>对已签到座位执行签退]
    F -->|否| H[结束]
    G --> H
```

**签到 / 签退内部流程：**

```mermaid
flowchart TD
    A[等待到签到/签退时间] --> B[遍历今天需要操作的用户]
    B --> C[登录 login]
    C --> D{登录成功?}
    D -->|否| E[记录登录失败]
    D -->|是| F[逐个座位<br/>execute_signin / execute_signout]
    F --> G{操作成功?}
    G -->|否| H[记录失败原因]
    G -->|是| I[记录成功]
    E --> J
    H --> J
    I --> J{存在失败项?}
    J -->|是 且 ENABLE_EMAIL| K[发送失败邮件提醒]
    J -->|否| L[结束]
    K --> L
```

> **接口说明**：签到使用 `data/apps/seat/sign?id={reserveId}`，签退使用 `data/apps/seat/signback?id={reserveId}`。注意 `leave` 是"暂离"，**不是**签退。`reserveId` 从预约信息接口 `reserve/info` 的 `seatReserve.id` 动态获取。

## 五、环境依赖

- Python 3.8+（建议 3.11）
- 依赖包：`requests`、`cryptography`

安装依赖：

```bash
pip install -r requirements.txt
```

> 若需要开启滑块验证（`ENABLE_SLIDER = True`），请取消 `requirements.txt` 中 `numpy` 和 `opencv-python-headless` 的注释后再安装。

## 六、配置文件说明

复制 `config.example.json` 为 `config.json`，然后按需修改：

```json
{
  "reserve": [
    {
      "username": "学号/手机号",
      "password": "密码",
      "time": ["08:00", "21:30"],
      "roomid": "",
      "seatid": [""],
      "daysofweek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    }
  ],
  "mail": {
    "host": "smtp.qq.com",
    "port": 465,
    "secure": true,
    "auth": {
      "user": "你的邮箱@qq.com",
      "pass": "邮箱授权码"
    }
  },
  "receivers": ["收件邮箱@example.com"]
}
```

| 字段 | 说明 |
| --- | --- |
| `reserve` | 预约列表，每个元素代表一个预约任务 |
| `reserve[].username` | 账号（本地模式使用；Action 模式被环境变量覆盖） |
| `reserve[].password` | 密码（同上） |
| `reserve[].time` | 预约时段 `[开始, 结束]`，超过 5 小时自动切分 |
| `reserve[].roomid` | 房间 ID（可用 `-m room` 模式探测） |
| `reserve[].seatid` | 座位号数组，可写多个 |
| `reserve[].daysofweek` | 预约的星期（英文首字母大写），空数组表示每天 |
| `mail` | SMTP 邮件配置，预约成功后发送通知 |
| `receivers` | 收件人邮箱列表 |

> **注意**：`config.json` 已被 `.gitignore` 忽略，请勿提交到仓库，避免泄露账号密码和邮箱授权码。

## 七、本地运行

### 1. 直接运行

```bash
# 正式预约（读取 config.json 中的账号密码）
python main.py

# 调试模式：单次预约并发送邮件
python main.py -m debug

# 探测房间 ID（交互式输入）
python main.py -m room
```

### 2. 命令行参数

| 参数 | 说明 |
| --- | --- |
| `-u, --user` | 指定配置文件路径，默认 `config.json` |
| `-m, --method` | 运行模式：`reserve`（默认）/ `debug` / `room` |
| `-a, --action` | 开启 GitHub Actions 模式（从环境变量读取账号密码，并做时区校正） |

### 3. Windows 批处理

`chaoxing.bat` 是一个 Windows 一键运行脚本，请先按自己的环境修改其中的 Python 路径和项目路径：

```bat
@echo off
cd 你的项目路径
"你的python.exe路径" main.py >> run_log.txt 2>&1
```

## 八、GitHub Actions 自动预约

### 1. 工作流文件

工作流位于 `.github/workflows/reserve.yml`，已配置：

- **定时触发**：每天**北京时间 06:00** 自动执行（GitHub cron 基于 UTC，故写成 `0 22 * * *`，对应 UTC 22:00 = 北京时间 06:00）。
- **手动触发**：可在仓库 Actions 页面点击 `Run workflow` 手动运行，并选择 `reserve` 或 `debug` 模式。
- **环境变量注入**：

```yaml
env:
  USERNAMES: ${{ secrets.USERNAMES }}
  PASSWORDS: ${{ secrets.PASSWORDS }}
```

### 2. 配置 Secrets

在 GitHub 仓库 **Settings → Secrets and variables → Actions → New repository secret** 中添加以下三个 Secret：

| Secret 名称 | 内容 |
| --- | --- |
| `USERNAMES` | 多个账号用英文逗号分隔，如 `20230001,20230002` |
| `PASSWORDS` | 多个密码用英文逗号分隔，与 `USERNAMES` **顺序一一对应** |
| `CONFIG_JSON` | 完整的 `config.json` 内容（含房间/座位/时段/邮件配置） |

> **顺序要求**：`USERNAMES` 中账号的顺序必须与 `CONFIG_JSON` 中 `reserve` 列表的顺序一致；`PASSWORDS` 同理与 `USERNAMES` 一一对应。

### 3. 时区说明

- 脚本内置了 +8 时区偏移，`-a` 模式下 `get_current_time` 使用 `time.time() + 8*3600` 计算北京时间，`get_submit` 中也对 UTC 运行环境做了日期校正。
- 因此 GitHub Actions 的 runner 保持默认 UTC 即可，**无需**额外设置 `TZ` 环境变量。

## 九、关键参数说明

以下参数位于 `main.py` 顶部，可按需修改：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `STARTTIME` | `"08:00:00"` | 预约正式开放时间（开始时间，北京时间），到点后才正式提交预约 |
| `LOGIN_AHEAD` | `5` | 提前多少秒开始登录（开始登录时间 = `STARTTIME - LOGIN_AHEAD` 秒） |
| `SLEEPTIME` | `0.5` | 每次尝试的间隔秒数 |
| `ENDTIME` | `"08:01:00"` | 停止尝试的时间（北京时间） |
| `ENABLE_SLIDER` | `False` | 是否启用滑块验证 |
| `MAX_ATTEMPT` | `2` | 单次预约的最大尝试次数 |
| `RESERVE_NEXT_DAY` | `True` | 是否预约次日座位 |
| `ENABLE_RESERVE` | `True` | 是否开启自动预约（默认开启，保持原行为） |
| `ENABLE_SIGNIN` | `True` | 是否开启自动签到 |
| `ENABLE_SIGNOUT` | `True` | 是否开启自动签退 |
| `SIGNIN_TIME` | `"08:00:30"` | 签到时间（北京时间），须在 `STARTTIME`~`ENDTIME` 之间，与预约共用窗口 |
| `SIGNOUT_TIME` | `"21:29:30"` | 签退开始时间（北京时间） |
| `SIGNOUT_END_TIME` | `"21:30:00"` | 签退结束时间（北京时间），超过后停止重试 |
| `SIGNOUT_LEAD` | `300` | 签退提前量（秒）：定时器早于签退时间启动时的允许提前量，早于该时间则本次跳过签退 |

### 时间轴（双定时器运行）

**早间运行（定时器 07:58 启动）：**

```
07:59:55  —— 开始登录（只登录，不提交预约）
08:00:00  —— 正式提交预约（主循环不断重试）
08:00:30  —— 到达签到时间，预约继续重试的同时执行签到
08:01:00  —— 到达 ENDTIME，无论成功与否退出（不等待签退，签退由晚间定时器执行）
```

**晚间运行（定时器 21:28 启动）：**

```
21:28:00  —— 启动，已超过 ENDTIME，跳过预约/签到
21:29:30  —— 到达 SIGNOUT_TIME，开始签退（失败则重试）
21:30:00  —— 到达 SIGNOUT_END_TIME，停止重试并退出
```

> 脚本会先**等待到登录开始时间**再登录，再**等待到开放时间**才正式提交预约，避免过早请求被拒绝；8:00-8:01 窗口内既执行预约、也检查签到时间。

## 十、自动签到与签退

### 1. 开关配置

签到与签退由 `main.py` 顶部的开关独立控制，可单独开启，也可同时开启：

```python
ENABLE_RESERVE   = True    # 是否开启自动预约（默认开启）
ENABLE_SIGNIN    = True    # 是否开启自动签到
ENABLE_SIGNOUT   = True    # 是否开启自动签退
SIGNIN_TIME      = "08:00:30"  # 签到时间（北京时间，须在 08:00~08:01 窗口内）
SIGNOUT_TIME     = "21:29:30"  # 签退开始时间（北京时间）
SIGNOUT_END_TIME = "21:30:00"  # 签退结束时间（北京时间，超过后停止重试）
SIGNOUT_LEAD     = 300          # 签退提前量（秒），定时器早于签退时间启动时的允许提前量
```

- **预约 + 签到**：预约与签到共用 `[STARTTIME, ENDTIME]`（默认 08:00-08:01）窗口。到 `STARTTIME` 开始抢座，到 `SIGNIN_TIME` 后（预约仍在重试时）同步对已预约座位签到，直到 `ENDTIME` 停止。
- **签退**：在 `[SIGNOUT_TIME, SIGNOUT_END_TIME]`（默认 21:29:30-21:30:00）窗口内对已签到座位签退，失败会在窗口内重试，超过 `SIGNOUT_END_TIME` 停止。
- 三者复用预约的登录与会话，账号密码、房间座位、星期过滤（`daysofweek`）规则与预约完全一致。
- 脚本启动时会检查当前时间：若已超过所有执行窗口则直接退出，避免空跑与无限重复请求。

> **时间顺序建议**：`STARTTIME`（预约）≤ `SIGNIN_TIME`（签到）≤ `ENDTIME`（窗口结束），且都早于 `SIGNOUT_TIME`（签退）。签退依赖先签到成功，签到依赖先预约成功。

> **双定时器部署（推荐）**：建议用两个独立定时任务分别触发早间与晚间阶段，避免单个进程空等数小时：
> - **早间定时器**（如 07:58 启动）：脚本在 `[STARTTIME, ENDTIME]` 内完成预约 + 签到，到 `ENDTIME` 无论成功与否都退出，**不会**继续等待签退。
> - **晚间定时器**（如 21:28 启动）：此时已超过 `ENDTIME`，脚本跳过预约 + 签到，直接进入签退阶段；只要启动时间在 `SIGNOUT_TIME - SIGNOUT_LEAD` 之后，就会等待到 `SIGNOUT_TIME` 执行签退，直到 `SIGNOUT_END_TIME` 停止。

### 2. 单独测试（调试）

`test_signin.py` 用于不等待时间、立即执行一次签到 / 签退，方便调试：

```bash
python test_signin.py signin            # 立即测试签到
python test_signin.py signout           # 立即测试签退
python test_signin.py signin 18888 188  # 指定房间和座位测试签到
python test_signin.py signout 18888 188 # 指定房间和座位测试签退
```

> 前置条件：当天已有有效预约，签到 / 签退才能匹配到 `reserveId`；签退需要先签到成功。

### 3. 失败邮件提醒

签到 / 签退过程中，一旦出现**登录失败**或**签到 / 签退接口返回失败**，脚本会汇总失败项，在 `ENABLE_EMAIL = True` 时通过 `config.json` 中的 `mail` / `receivers` 发送邮件提醒（邮件标题形如"超星图书馆座位签到失败提醒 - 共 N 条"）。邮件正文包含每条失败的**账号、房间代号、座位、失败原因**。

## 十一、常见失败原因

### 预约失败

| 失败原因 | 说明与排查 |
| --- | --- |
| 登录失败 | 账号密码错误，或超星风控拦截。核对 `USERNAMES` / `PASSWORDS` 顺序与账号是否可正常登录 |
| 请求过早被拒 | 提交预约早于开放时间；脚本会等待到 `STARTTIME` 才正式提交 |
| 座位被抢光 | 热门座位在开放瞬间被抢完；可尝试缩短 `SLEEPTIME`、多配置几个候选座位 |
| 开始时间大于结束时间 | `time` 配置异常（如 `["21:00","08:00"]`），脚本会判定为配置错误 |
| 时段切分失败 | 长时段自动切分时某一段提交失败 |

### 签到 / 签退失败

| 失败原因 | 说明与排查 |
| --- | --- |
| 登录失败 | 账号密码错误或风控，导致无法进入签到 / 签退流程 |
| 未找到预约ID | 当天没有有效预约，或预约状态尚未同步；重新运行或等待片刻后重试 |
| 非法签退操作，请刷新重试 | 当前座位**尚未签到**，签退要求先签到成功；先执行签到再签退 |
| 非签到时间 / 状态不符 | 学校可能限定签到时间窗口，或当前预约状态与操作不匹配 |
| 接口返回失败 | 网络波动、会话过期等；查看日志中的 `msg` 返回信息 |

## 十二、常见问题

**Q1：为什么 GitHub 工作流里没有 `config.json` 会报错？**
`main.py` 和 `reserve.py` 都会读取 `config.json`。由于该文件被 `.gitignore` 忽略、不会上传到仓库，所以工作流里通过 `CONFIG_JSON` Secret 在运行前动态生成它。

**Q2：账号密码顺序不对会怎样？**
Action 模式下，`USERNAMES` / `PASSWORDS` 会按索引覆盖 `config.json` 中 `reserve` 列表的账号密码，顺序不一致会导致账号密码错配。脚本内部有校验：`USERNAMES` 的账号数量必须等于 `reserve` 列表长度。

**Q3：预约时段超过 5 小时怎么办？**
脚本会自动将长时段按 5 小时切分为多段并逐段提交。

**Q4：如何开启滑块验证？**
将 `main.py` 中 `ENABLE_SLIDER` 设为 `True`，并在 `requirements.txt` 中取消 `numpy`、`opencv-python-headless` 的注释。

**Q5：邮件发送失败？**
请确认 `config.json` 中 `mail.auth.pass` 使用的是邮箱**授权码**（而非登录密码），并正确填写 SMTP 主机和端口。

**Q6：签退提示"非法签退操作"？**
签退要求当前座位**已签到**。请先执行签到（或确认签到已成功），再执行签退。另外注意：超星的 `leave` 接口是"暂离"，真正的签退接口是 `signback`，本项目已使用正确接口。

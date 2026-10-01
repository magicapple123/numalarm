# 牛马铃（numalarm）

[![CI](https://github.com/magicapple123/numalarm/actions/workflows/ci.yml/badge.svg)](https://github.com/magicapple123/numalarm/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)

基于桌面 UI 自动化的 **QQ 电话通知 Skill**：通过模拟鼠标点击 PC 版 QQ 的「语音通话」按钮，自动向指定 QQ 好友拨打语音电话，为本地运行的各类 Agent 提供「任务完成 / 异常中断」时的强提醒能力。电话铃声能触达已离开电脑的用户——这是弹窗、消息推送做不到的。

> 牛马铃：给「牛马」打工人准备的铃。一次封装、多 Agent 复用，也支持桌面快捷方式一键拨打。

## 特性

- **四种接入方式**：Python SDK / HTTP REST（18600）/ CLI / Windows 桌面快捷方式，共用同一核心流程
- **图片匹配拨打**：定位语音按钮，置信度可配置，失败自动重试 2 次
- **并发安全**：全局互斥锁，同一时间仅一路拨打，并发返回「占线」（503）
- **防抖**：同一目标默认 5 分钟内仅拨一次（可配置）
- **无人接听自动重拨**：对方接听或拒绝均视为触达成功；无应答自动间隔重拨，直至接听或拒绝（次数可设上限或不限）
- **接听后语音提醒**（可选）：对方接听后自动播放固定语音（如「任务有新进展，请前往电脑查看」），播完自动挂断
- **全配置化**：快捷键、等待时长、置信度、防抖、端口全部进配置文件，无硬编码
- **合规**：仅模拟手动操作，无注入、无破解、不调用 QQ 私有接口、不存账号密码

## 环境要求

- Windows 10/11（仅支持 PC 版 QQ，不支持网页版 / Mac / 移动端）
- Python 3.10+
- PC 版 QQ 已登录，桌面已解锁（锁屏无法工作）

## 安装

```bash
# 1. 获取项目后进入目录
cd numalarm

# 2. 安装依赖（区分见文件内注释；pywin32 仅 Windows 自动安装）
pip install -r requirements.txt

# 3.（可选）安装为命令行工具，获得 numalarm 命令
pip install -e .
# 不安装也可用等价写法：python -m numalarm.interfaces.cli <命令>
```

## 作为 Agent Skill 安装（跨宿主通用）

本仓库符合 Agent Skill 规范（根目录 SKILL.md），克隆到支持该规范的宿主技能目录即可被自动发现：

```bash
# WorkBuddy（用户级，全部项目可用）
git clone https://github.com/magicapple123/numalarm.git "$HOME/.workbuddy/skills/numalarm"

# Claude Code / CodeBuddy 等兼容宿主同理
git clone https://github.com/magicapple123/numalarm.git "$HOME/.claude/skills/numalarm"
```

安装后 Agent 自动遵循 SKILL.md「Agent 集成约定」——任务中断/等待决策前先拨打电话。
命令行拨打能力仍需完成上方依赖安装与下方校准步骤。

### 小白一键安装：把这段话发给你的 Agent 即可

不熟悉命令行？把下面整段提示词复制、发送给你电脑里的 Agent（WorkBuddy / Claude Code / CodeBuddy 均可），它会自动完成询问配置、安装与校准：

```text
请帮我安装 numalarm（牛马铃）技能，这是一个在任务中断或需要我决策时自动给我打 QQ 电话提醒的工具。请按以下步骤执行：

1. 将 https://github.com/magicapple123/numalarm 克隆到我的 Agent 技能目录：
   - WorkBuddy：~/.workbuddy/skills/numalarm
   - Claude Code：~/.claude/skills/numalarm
   - CodeBuddy：~/.codebuddy/skills/numalarm
   （不确定我用的宿主时先询问我，或多个目录都放一份。）

2. 进入仓库目录，用当前 Python 环境执行 pip install -r requirements.txt 安装依赖。

3. 运行 numalarm doctor 做环境自检，把结果告诉我；有 FAIL 项按提示修复后重跑。

4. 询问我以下信息，并基于 config.example.yaml 生成 config.yaml：
   - default_target：我需要被提醒时，电话打给哪个 QQ 好友（昵称或备注）
   - 是否需要配置别名（target_alias）
   - 无人在电脑前时的响铃时长 ring_seconds（默认 45 秒）

5. 指导我完成两张模板截图（详细说明在 assets/README.md）：
   - 打开与目标好友的聊天窗口，截取「语音通话」按钮图标，覆盖 assets/voice_button_sample.png
   - 向该好友发起一次语音通话，在响铃界面截取顶部「等待对方接听」文字，覆盖 assets/call_ringing_sample.png
   注意：发起通话前必须先征得我同意。

6. 运行 numalarm test <我的目标> 做校准，四步全部 PASS 后告诉我安装完成；若有 FAIL，按提示协助我修正（常见为重新截图或调整偏移）。

7. 最后询问我是否需要：
   - 注册打断自动拨打 hook（numalarm hook install）
   - 创建桌面快捷方式（numalarm shortcut create <目标> --silent）

要求：每一步执行前先简要说明你要做什么；需要真实拨打测试时必须先征得我同意。
```

## 快速开始（三步）

```bash
# 0. 环境自检（新用户先跑这个：依赖/配置/素材/QQ 逐项体检 + 修复提示）
numalarm doctor

# 1. 生成配置
copy config.example.yaml config.yaml    # 或运行交互式向导：numalarm init

# 2. 替换语音按钮示例截图（重要！按 assets/README.md 的步骤）
#    截取你 QQ 聊天窗口的「语音通话」按钮覆盖 assets/voice_button_sample.png
#    以及通话窗口「等待对方接听」文字覆盖 assets/call_ringing_sample.png

# 3. 校准验证（搜索好友 + 打开聊天窗 + 识别按钮，不拨打）
numalarm test 张三
# 全部 PASS 后即可正式拨打：
numalarm call 张三
```

## 四种调用方式

### 1. Python SDK

```python
from numalarm import call_qq

result = call_qq(target="张三", timeout=30, silent=False)
# {"code": 200, "message": "拨打成功", "data": {"target": "张三", "elapsed_seconds": 6.2, ...}}

call_qq(target="老板")   # 使用别名（config.yaml: target_alias: {老板: 张三}）
call_qq()                # 使用 default_target
```

### 2. HTTP REST（供 Dify / Coze / n8n / 自研远端 Agent）

```bash
numalarm serve                          # 默认 127.0.0.1:18600
curl -X POST http://127.0.0.1:18600/api/call \
     -H "Content-Type: application/json" \
     -d '{"target": "张三", "timeout": 30, "silent": false}'
curl http://127.0.0.1:18600/api/status  # 查询是否空闲
```

### 3. CLI

```bash
numalarm call [目标] [--timeout 30] [--silent/-s]   # 目标可用昵称/备注/别名，不传用 default_target
numalarm test [目标]                                # 校准：不点击拨打，逐步输出检测项
numalarm init                                       # 交互式生成 config.yaml
numalarm serve [--host H] [--port P]                # 启动 HTTP 服务
```

### 4. 桌面快捷方式一键拨打（Windows）

```bash
# 基础：桌面生成「牛马铃-拨打张三.lnk」，双击即拨打
numalarm shortcut create 张三

# 静默模式：双击不弹控制台窗口，后台拨打，不打断当前工作
numalarm shortcut create 老板 --name "打老板电话" --silent

# 管理快捷方式
numalarm shortcut list                    # 列出所有牛马铃快捷方式
numalarm shortcut delete "打老板电话"      # 删除（确认后执行）
```

- 可为「默认号」「工作号」「测试号」创建多个独立快捷方式，互不冲突
- 快捷方式可右键固定到任务栏 / 开始菜单
- 未装 pywin32 / 非 Windows 平台时自动降级：给出明确提示与手动创建教程，不影响核心拨打功能

## 状态码

| 状态码 | 含义 |
|--------|------|
| 200 | 拨打成功 |
| 400 | 参数错误 |
| 429 | 请求过于频繁（防抖触发） |
| 503 | 服务占线（正在拨打中） |
| 1001 | QQ 进程未启动 |
| 1002 | 目标好友未找到 |
| 1003 | 语音按钮匹配失败 |
| 1004 | 拨打超时 |
| 1005 | 未知错误 |

所有接口统一返回 `{"code": int, "message": str, "data": object}`；成功时 `data` 含 `target / elapsed_seconds / button_position / chat_window`。

## 配置说明

完整项与注释见 [config.example.yaml](config.example.yaml)，复制为 `config.yaml` 后修改。主要参数：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| default_target | null | 默认拨打目标（昵称/备注） |
| target_alias | {} | 别名映射：别名 -> 真实昵称/备注 |
| qq_process_name / qq_window_title | QQ.exe / QQ | 进程名与主面板标题关键字 |
| call_window_hint / verify_call_window | 通话 / true | 通话窗口检测关键字与开关 |
| hotkey.wake_panel | ctrl+alt+z | 唤起主面板全局快捷键（与 QQ 设置一致） |
| hotkey.search | ctrl+f | 搜索框快捷键（search_method: hotkey 时使用） |
| search_method | click | 搜索框打开方式：click=点击搜索框（QQ NT 不响应 Ctrl+F）/ hotkey |
| search_box_offset | [225, 81] | click 模式搜索框点击点相对主面板左上角的像素偏移（换分辨率/DPI 后需校准） |
| search_select | click | 搜索结果选中方式：click=点击第一条（部分 QQ 版本回车无效）/ enter |
| search_result_offset | [178, 184] | click 选中模式：第一条搜索结果点击点相对主面板左上角的像素偏移 |
| timing.* | 见示例 | 各步骤等待时长（秒） |
| match.confidence / max_retry / grayscale | 0.8 / 2 / true | 图片匹配置信度、重试次数、灰度加速 |
| debounce.enabled / window_seconds | true / 300 | 防抖开关与窗口（秒） |
| retry.enabled | true | 无人接听自动重拨开关（需 verify_call_window: true） |
| retry.max_attempts | 0 | 总拨打次数上限（含首次）；0=不限次，直至接听或拒绝 |
| retry.interval_seconds | 10 | 无人接听重拨间隔（秒） |
| retry.ring_seconds | 45 | 单次响铃上限（秒），超时主动挂断重拨；0=等 QQ 自然结束（可能数分钟） |
| retry.natural_ringout_seconds | 120 | 自然响铃超时下限（秒）：窗口存活低于该值即关闭视为已接听/拒绝，防止秒拒被误判无人接听 |
| presence.enabled / idle_seconds | true / 180 | 用户在位检测：键鼠空闲超过该秒数才拨打（仅 auto 触发路径生效，手动命令不受限） |
| media.speak_on_answer | false | 接听后自动播放语音提醒（需虚拟声卡） |
| media.message | 任务有新进展，请前往电脑查看 | 提醒文本（SAPI 离线合成） |
| media.output_device_hint | CABLE | 语音输出设备名关键字 |
| media.hangup_after_speak | true | 播放完成后自动挂断 |
| retry.no_answer_seconds | 25 | 兜底判定阈值（秒）；有响铃模板时改用状态文本判定（推荐） |
| log.level / file | INFO / logs/numalarm.log | 日志级别与文件 |
| server.host / port | 127.0.0.1 / 18600 | HTTP 服务监听 |
| shortcut.name_prefix | 牛马铃-拨打 | 快捷方式名称前缀 |

## 接听后语音提醒（可选）

想让对方**接听后听到一段固定语音**（如「任务有新进展，请前往电脑查看」）？原理：QQ 语音通话只传麦克风的声音，因此用**虚拟声卡**把离线合成的语音注入 QQ 的麦克风输入。

**一次性准备**：

1. 安装免费虚拟声卡驱动 [VB-Audio Virtual Cable](https://vb-audio.com/Cable/)（安装后重启）；
2. QQ 设置 → 音视频通话 → 麦克风选择「CABLE Output (VB-Audio Virtual Cable)」；
   ⚠️ 此后你用 QQ 打电话说话对方听不到你本人声音，需真人通话时在 QQ 里把麦克风切回真实设备（进阶可用 VoiceMeeter 把真实麦克风与虚拟声卡混音，两者兼顾）。

**启用**：config.yaml 中设置

```yaml
media:
  speak_on_answer: true
  message: 任务有新进展，请前往电脑查看
  output_device_hint: CABLE
  hangup_after_speak: true    # 播完自动挂断
```

之后的拨打流程变为：对方接听 → 自动播放提醒语音 → 自动挂断（无人接听仍自动重拨）。语音由 Windows 自带 SAPI 离线合成（支持中文），**无需联网、无需额外依赖**；未安装虚拟声卡时该功能自动降级为普通拨打（如实记录播放失败）。

## 宿主 Hook 集成（跨 Agent 通用）

让「Agent 停下等待用户时自动打电话」在平台层生效，不依赖 Agent 自觉遵守 skill 约定：

```bash
numalarm hook install      # 自动探测已安装的兼容宿主并注册（幂等，先备份）
numalarm hook status       # 查看注册状态
numalarm hook uninstall    # 移除（只删自身条目，其余配置保留）
```

- 支持宿主：**WorkBuddy / Claude Code / CodeBuddy** 等 Claude Code hooks 兼容端（自动探测 `~/.workbuddy`、`~/.claude`、`~/.codebuddy` 下的 settings.json）
- 注册事件：`Notification`（Agent 需要用户注意）+ `PermissionRequest`（等待用户批准）——即「Agent 停下来等你处理」的时刻
- hook 命令：`NUMALARM_CONFIG=... pythonw -m numalarm.interfaces.cli call --silent`（async 异步不阻塞，无黑框）
- 自带防轰炸：防抖窗口内重复触发只拨一次；重拨会话持锁期间后续触发直接返回 503 静默退出
- 注册后重启对应宿主会话生效；配合 `numalarm call -m "原因"` 可在日志中追溯触发原因

## 常见问题（FAQ）

**Q：提示 1003 语音按钮匹配失败？**
重新按 assets/README.md 截图（分辨率/DPI 必须与运行时一致）；或调低 `match.confidence`（如 0.75）；确认聊天窗口未被遮挡。

**Q：提示 1001 但 QQ 明明开着？**
检查 `qq_process_name`（新版 QQ 进程可能不同）与 `qq_window_title`；确认 QQ 已登录（仅登录后主面板才可唤起）。

**Q：拨打成功但报 1004 超时？**
你的 QQ 版本拨打后可能不弹独立通话窗口，把 `verify_call_window` 改为 `false` 即可。

**Q：中文昵称能输入吗？**
能。搜索框输入通过剪贴板粘贴（Ctrl+V）实现，天然支持中文。

**Q：搜索框打不开 / 回车不选中结果？**
QQ NT 版本不响应 Ctrl+F，且部分版本回车不选中搜索结果。保持默认 `search_method: click` + `search_select: click`（点击搜索框 + 点击第一条结果）；更换分辨率/DPI 后需重新校准 `search_box_offset` 与 `search_result_offset` 两个偏移值（用 `numalarm test` 配合截图逐步校准）。

**Q：QQ 面板已经开着，会被热键藏起来吗？**
不会。程序会先探测面板是否可见，可见则直接前置，仅在其不可见时才按「提取消息」热键（该热键是开关切换）。

**Q：快捷键唤不起主面板？**
进入 QQ 设置 -> 热键，把「提取消息/主面板」快捷键改为与 `hotkey.wake_panel` 一致（默认 `Ctrl+Alt+Z`）。

**Q：无人接听会重拨吗？**
会。拨打后通话窗口头部显示「等待对方接听」，程序据此判定状态：文案**变化**（变为计时器=已接听 / 拒绝提示=已拒绝）即触达成功，立即停止；窗口**关闭**且全程仍在响铃（QQ 响铃超时自动挂断）即无人接听，按 `retry.interval_seconds` 间隔自动重拨。`retry.max_attempts: 0`（默认）不限次直至触达，设正整数可限次。重拨全程持锁，期间其他调用返回 503；HTTP 调用方需设置足够的客户端超时（QQ 响铃超时可达 1 分钟以上）。响铃文案模板见 assets/README.md，QQ 更新改变文案后需重新截取。

**Q：人在电脑前时不想被打扰怎么办？**
默认就帮你考虑了：hook 与 agent 的拨打都带 `--auto`（自动化触发）标记，受**用户在位检测**控制——键鼠空闲超过 `presence.idle_seconds`（默认 180s）才真正拨打，你动一下鼠标它就静默跳过，离开电脑后自动恢复。重拨会话进行中你回到电脑前，下一轮也会自动停止。人工手动敲的 `numalarm call`（不带 `--auto`）不受此限制——你手动要打的电话总会打。另可用 `numalarm hook uninstall` 摘掉平台钩子，或在对话里直接告诉 agent「这次不用打电话」（skill 约定层会遵循）。

**Q：误触发怎么办？**
拨打过程中把鼠标快速移到屏幕左上角可强制中断（pyautogui failsafe）。

## 目录结构

```
numalarm/
├── SKILL.md              # Skill 元数据（Agent Skill 规范）
├── README.md             # 本文档
├── TESTS.md              # 测试用例说明
├── CHANGELOG.md          # 更新日志
├── LICENSE               # MIT 许可证
├── requirements.txt      # 依赖清单（必选/可选区分）
├── pyproject.toml        # 打包配置（numalarm 命令入口 + ruff 规范）
├── config.example.yaml   # 配置示例（复制为 config.yaml 使用）
├── conftest.py           # pytest 根目录导入支持
├── tests/                # 单元测试（纯逻辑，跨平台可跑）
│   └── test_numalarm.py
├── .github/workflows/    # CI（Ubuntu 测试矩阵 + Windows 冒烟）
├── assets/
│   ├── voice_button_sample.png   # 语音按钮模板
│   ├── call_ringing_sample.png   # 响铃状态判定模板
│   └── README.md                 # 素材截取与替换说明
└── numalarm/             # 核心代码包
    ├── __init__.py       # 对外暴露 call_qq / test_call
    ├── core/             # 核心拨打逻辑层
    │   ├── qq_controller.py    # QQ 窗口唤起、搜索、聊天窗控制
    │   ├── call_executor.py    # 拨打流程编排与执行
    │   └── state_detector.py   # 进程、窗口、用户在位检测
    ├── interfaces/       # 对外接入层
    │   ├── sdk.py        # Python SDK
    │   ├── api.py        # HTTP REST
    │   └── cli.py        # CLI + 桌面快捷方式 + 宿主 Hook 管理
    └── common/           # 通用基础组件
        ├── config.py     # 配置加载与管理
        ├── lock.py       # 全局互斥锁（进程内 + 跨进程文件锁）
        ├── logger.py     # 统一日志（支持静默）
        └── exceptions.py # 自定义异常与状态码
```

## 约束与边界

1. 仅支持 Windows 平台 PC 版 QQ；仅在桌面解锁、QQ 已登录时可用
2. 不提供 QQ 自动登录、不存储账号密码
3. 仅 UI 模拟点击，不注入进程、不破解协议、不调用 QQ 私有接口
4. 无联网上报、无后台隐藏功能
5. 桌面快捷方式仅 Windows 可用，其他平台自动禁用并提示

## 扩展路线

core 与 interfaces 已解耦，预留：视频通话（替换点击目标图）、自动挂断（检测通话窗口后模拟点击）、附带消息（拨打前模拟输入发送）。详见 SKILL.md「扩展预留」。

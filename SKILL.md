---
name: numalarm
description: 牛马铃——QQ 语音通话强提醒 Skill。基于桌面 UI 自动化模拟点击 PC 版 QQ 的语音通话按钮，向指定 QQ 好友拨打语音电话，用于本地 Agent 在任务完成/异常中断时强提醒已离开电脑的用户。提供 Python SDK、HTTP REST（默认端口 18600）、CLI、Windows 桌面快捷方式四种接入方式，内置全局互斥、防抖与统一状态码，一次封装多 Agent 复用。
license: MIT
compatibility: 仅支持 Windows 10/11 + PC 版 QQ（已登录、桌面解锁）；Python 3.10+；不支持网页版/Mac/移动端
metadata:
  version: 1.0.1
  display-name: 牛马铃
  keywords: [qq, voice-call, phone-notification, ui-automation, agent-alert]
---

# 牛马铃（numalarm）

基于桌面 UI 自动化的 QQ 电话通知 Skill：为本地运行的各类 Agent 提供「任务完成 / 异常中断」时的强提醒能力。核心逻辑是模拟鼠标点击 PC 版 QQ 聊天窗口中的「语音通话」按钮，自动向指定 QQ 账号拨打语音电话——电话铃声可以触达已离开电脑的用户，这是弹窗与消息推送做不到的强提醒。

详细使用教程见同目录 [README.md](README.md)；测试验证步骤见 [TESTS.md](TESTS.md)。

## 功能特性

- 一次封装、多 Agent 复用：SDK / HTTP / CLI / 桌面快捷方式共用同一核心流程
- 图片匹配定位语音按钮，置信度可配置，失败自动重试 2 次
- 全局互斥锁：同一时间仅一路拨打，并发请求直接返回「占线」（503）
- 防抖机制：同一目标默认 5 分钟内重复调用仅执行第一次（可配置）
- 无人接听自动重拨：以「等待对方接听」状态文本模板确定性判定——文案变化（计时器/拒绝提示）即触达成功，窗口关闭且全程响铃即无应答，自动间隔重拨直至触达（次数可不限）
- 接听后语音提醒（可选）：对方接听后自动播放固定语音（SAPI 离线合成，经虚拟声卡注入 QQ 麦克风），播完自动挂断
- Windows 桌面快捷方式一键拨打，支持静默后台执行（无黑框）
- 全参数配置化（快捷键/等待时长/置信度/防抖/端口），无硬编码
- 仅模拟手动操作：无注入、无破解、不调用 QQ 私有接口、不存储账号密码

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| target | string | 配置 `default_target` | 好友昵称/备注，或配置 `target_alias` 中的别名 |
| timeout | int | 30 | 整体拨打流程超时（秒） |
| silent | bool | false | 静默模式：关闭控制台日志输出 |

## 输出定义

所有接口统一返回：`{"code": int, "message": str, "data": object}`

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

`data` 成功时包含：`target`（实际拨打目标）、`attempts`（实际拨打次数）、`outcome`（触达结果：「对方已接听」/「对方已接听或已拒绝」）、`call_elapsed_seconds`（通话窗口从出现到出结果历经秒数）、`elapsed_seconds`（总耗时）、`button_position`（按钮坐标）、`chat_window`（聊天窗口标题）；失败时包含错误上下文（如 `attempts`、防抖剩余秒数 `retry_after_seconds`）。

## 使用示例

### 1. Python SDK

```python
from numalarm import call_qq

result = call_qq(target="张三", timeout=30, silent=False)
print(result)   # {"code": 200, "message": "拨打成功", "data": {...}}

# 使用别名 / 默认目标
result = call_qq(target="老板")     # target_alias: 老板 -> 张三
result = call_qq()                  # 使用 default_target
```

### 2. HTTP REST

```bash
# 启动服务（默认 127.0.0.1:18600）
numalarm serve

# 拨打
curl -X POST http://127.0.0.1:18600/api/call \
     -H "Content-Type: application/json" \
     -d '{"target": "张三", "timeout": 30, "silent": false}'

# 查询空闲状态
curl http://127.0.0.1:18600/api/status
```

### 3. CLI

```bash
numalarm call 张三 --timeout 30     # 指定目标
numalarm call 老板                  # 使用别名
numalarm call                       # 使用 default_target
numalarm call 张三 -s               # 静默执行
numalarm test 张三                  # 校准（不拨打，逐步输出检测项）
numalarm init                       # 交互式生成 config.yaml
numalarm serve                      # 启动 HTTP 服务
```

### 4. 桌面快捷方式（Windows）

```bash
numalarm shortcut create 张三                          # 名称默认「牛马铃-拨打张三」
numalarm shortcut create 老板 --name "打老板电话" --silent   # 静默：双击无黑框后台拨打
numalarm shortcut list                                 # 列出已创建快捷方式
numalarm shortcut delete "打老板电话"                   # 删除
```

双击桌面图标即拨打；可右键固定到任务栏 / 开始菜单；可为「默认号」「工作号」「测试号」创建多个独立快捷方式互不冲突。

## Agent 集成约定（打断自动提醒，使用本 Skill 的 Agent 必读）

任何 Agent 使用本 Skill 时，凡遇「即将停下等待用户」的情况，必须**先拨打提醒电话，再进入等待**：

| 时机 | 触发条件 |
|------|----------|
| 需要用户决策 | 准备调用 AskUserQuestion / 请求计划确认（ExitPlanMode）/ 任何等待用户选择的场景 |
| 任务异常中断 | 遇到无法自行恢复的错误，即将以失败终止任务时 |
| 等待确认 | 阶段性完成、下一步需用户点头才能继续时 |

拨打方式（target 缺省用配置 default_target）：

```python
from numalarm import call_qq
call_qq(reason="任务中断：需要你在方案 A/B 之间选择")   # 阻塞直至提醒触达
```

```bash
numalarm call --silent -m "任务中断：需要你在方案 A/B 之间选择"
```

- 对方接听或拒绝均视为提醒成功（code=200）；无人接听自动重拨直至触达（retry 配置）
- 内置 5 分钟防抖：窗口内重复触发只拨一次，不会电话轰炸
- Agent 停止路径建议用 `--silent`，避免污染任务输出
- `call_qq` 默认 `auto=True`：用户正在电脑前（键鼠空闲低于 presence.idle_seconds）时自动跳过拨打，离开电脑才真正响铃——无需用户手动开关
- 零污染设计：依赖隔离在技能目录的独立虚拟环境，`numalarm uninstall` 一键清理运行时残留（hook/快捷方式/状态），删除技能目录即完全移除

**能力边界**：以上约定依赖 Agent 在停止前主动执行；Agent 被强制杀死/崩溃无法自报的极端场景，用宿主 Hook 兜底——`numalarm hook install` 向兼容 hooks 规范（settings.json）的 Agent 宿主的 Notification / PermissionRequest 事件注册静默拨打（自动探测常见宿主，其他宿主用 `--settings` 指定；`numalarm hook uninstall` 移除）。

## 配置说明

完整项见 [config.example.yaml](config.example.yaml)，主要参数：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| default_target | null | 默认拨打目标 |
| target_alias | {} | 别名映射：别名 -> 真实昵称/备注 |
| qq_process_name | QQ.exe | QQ 进程名 |
| qq_window_title | QQ | 主面板窗口标题关键字 |
| call_window_hint | 通话 | 通话窗口检测关键字 |
| verify_call_window | true | 拨打后是否检测通话窗口 |
| hotkey.wake_panel | ctrl+alt+z | 唤起主面板全局快捷键（与 QQ 设置一致） |
| hotkey.search | ctrl+f | 搜索框快捷键（search_method: hotkey 时使用） |
| search_method | click | 搜索框打开方式：click=点击搜索框（QQ NT 推荐）/ hotkey |
| search_box_offset | [225, 81] | click 模式搜索框点击点相对主面板左上角的像素偏移 |
| search_select | click | 搜索结果选中方式：click=点击第一条（QQ NT 推荐）/ enter |
| search_result_offset | [178, 184] | click 选中模式：第一条搜索结果点击点相对主面板左上角的像素偏移 |
| timing.* | 见示例 | 各步骤等待时长（秒） |
| match.confidence | 0.8 | 图片匹配置信度 |
| match.max_retry | 2 | 匹配失败重试次数 |
| match.grayscale | true | 灰度匹配加速 |
| debounce.enabled / window_seconds | true / 300 | 防抖开关与窗口（秒） |
| retry.enabled | true | 无人接听自动重拨开关（需 verify_call_window: true） |
| retry.max_attempts | 0 | 总拨打次数上限（含首次）；0=不限次，直至接听或拒绝 |
| retry.interval_seconds | 10 | 无人接听重拨间隔（秒） |
| retry.ring_seconds | 45 | 单次响铃上限（秒），超时主动挂断重拨；0=等 QQ 自然结束（可能数分钟） |
| retry.natural_ringout_seconds | 120 | 自然响铃超时下限（秒）：窗口存活低于该值即关闭视为已接听/拒绝，防止秒拒被误判无人接听 |
| media.speak_on_answer | false | 接听后自动播放语音提醒（需虚拟声卡，见 README） |
| media.message / output_device_hint | 任务有新进展… / CABLE | 提醒文本与语音输出设备关键字 |
| retry.no_answer_seconds | 25 | 无人接听判定阈值（秒），需略小于 QQ 响铃超时（约 30s） |
| log.level / file | INFO / logs/numalarm.log | 日志级别与文件 |
| server.host / port | 127.0.0.1 / 18600 | HTTP 服务监听 |
| shortcut.name_prefix | 牛马铃-拨打 | 快捷方式名称前缀 |

## 快捷方式专题

**创建**：`numalarm shortcut create <目标> [--name "名称"] [--silent]`
- 自动在桌面生成 `.lnk`，自动适配 Python 执行路径与命令参数
- `--silent` 使用 pythonw.exe 执行，双击后不弹控制台窗口，后台拨打
- 支持为不同目标创建多个独立快捷方式，互不冲突

**查看**：`numalarm shortcut list`（列出所有牛马铃快捷方式及其命令行）

**删除**：`numalarm shortcut delete <名称>`（确认后删除；也可直接在桌面手动删除）

**修改**：快捷方式本质是 CLI 封装，改目标最简单的方式是删除后重建；高级修改可右键快捷方式 -> 属性，编辑「目标」中的参数（`call` 后面的目标名）。

**固定**：右键快捷方式 -> 「固定到任务栏」或「固定到"开始"屏幕」。

**失败降级**：未安装 pywin32 或非 Windows 平台时，创建命令会给出明确提示与手动创建教程（桌面右键新建快捷方式，目标填写 `"python.exe路径" -m numalarm.interfaces.cli call "目标"`），核心拨打功能不受影响。

## 注意事项

### 使用前提
- Windows 平台 PC 版 QQ 已启动并登录；桌面已解锁（锁屏状态无法工作）
- 已按 assets/README.md 替换语音按钮示例截图，且分辨率/DPI 与拨打时一致
- QQ 内的「唤起主面板」「搜索」快捷键与 config.yaml 配置一致
- pyautogui 保护机制：鼠标快速移到屏幕左上角可强制中断（`failsafe: true`）

### 风险提示
- UI 自动化存在兼容性风险：分辨率/DPI 变化、界面遮挡、QQ 版本更新都可能导致失效
- 搜索结果不唯一时默认拨打第一项，请确保昵称/备注具有唯一性
- 建议先用 `numalarm test` 校准再投入使用
- 无人接听重拨默认不限次（`retry.max_attempts: 0`），注意合理设置上限避免过度打扰；HTTP 调用方需设置足够的客户端超时（QQ 响铃超时可达 1 分钟以上，一个重拨周期约 2 分钟）
- 结果判定基于通话窗口状态文本模板（「等待对方接听」），QQ 版本更新改变文案后需重新截取 assets/call_ringing_sample.png；模板缺失时回退为时长阈值启发式，对方在阈值临近时接听又秒挂存在极小概率误判

### 合规说明
- 仅模拟手动操作（快捷键/剪贴板/鼠标点击），无注入、无破解、不调用 QQ 私有接口
- 不提供自动登录、不存储任何账号密码
- 无任何联网上报与后台隐藏功能

## 扩展预留

core 层与 interfaces 层已解耦，后续可平滑扩展：视频通话（替换点击目标图）、自动挂断（检测通话窗口后模拟点击挂断）、附带消息（拨打前模拟输入框发送文本）。新增能力只需扩展 `core/call_executor.py` 流程编排，各接入层无需改动。

# 更新日志

## v1.1.1（2026-10-02）

社区反馈修复（感谢 [@zhangxingyu-1213](https://github.com/zhangxingyu-1213) 的四份高质量报告）：

- 修复 pywin32==306 无 Python 3.13 wheel 导致的安装/更新失败：requirements / pyproject / install 三处锁定改为 `pywin32>=307`；依赖安装失败不再中断整个安装器流程（后续步骤逐项标注失败/跳过），报错附 Python 3.13 提示（#4）
- 修复 `numalarm test` 假阳性：第 3 步回退主面板内嵌会话时 detail 明确标注「未确认会话（主面板回退）」，校准输出提示先关闭目标聊天窗口（#2）
- 拨打结果新增 `data.judgment` 字段：`template`=状态模板精确判定 / `threshold_fallback`=时长阈值回退（附回退原因），判定方式可观测（#3）
- CLI `call` 新增 `--json`（单行 JSON 输出完整结构化结果，含失败原因，失败退出码 1）与 `--force`（单次豁免防抖）；SDK/HTTP 同步新增 force 参数，HTTP 新增 reason 字段（#3）
- doctor 新增音频默认设备检查（`media.speak_on_answer=true` 时）：默认扬声器被虚拟声卡抢占、默认录音未指向 CABLE Output 均给出明确提示；零依赖 ctypes 实现（#1）

## v1.1.0（2026-10-02）

- 新增 `install.py` 一键安装器：仅标准库、幂等可重复执行、`--dry-run` 零副作用、`--yes` 非交互；自动判定当前宿主并只向该宿主注册 hook（非交互默认不注册——涉及真实拨打）
- 新增多副本体检与收拢：`numalarm installs [--json] [--apply]`；安装时自动发现其他 numalarm 副本，可复用其 config.yaml 与已校准模板（覆盖前自动备份到 ~/.numalarm/backups）
- `hook install` / `hook uninstall` / `watchdog install` / `watchdog uninstall` 新增 `--host` / `--settings` 作用域（默认仍为全部已探测宿主；`NUMALARM_HOOK_SCOPE=current` 可切为仅当前宿主）；计划任务保持全机唯一
- 修复 `watchdog uninstall` 误删拨打类 hook：hook 按命令分类（拨打/心跳），卸载只删各自类型；无法分类的条目宁留不误删并提示
- 修复窗口匹配可能误配外部窗口（浏览器标签标题恰好含「QQ」/目标昵称）：窗口查找/激活/等待全面支持进程过滤；同步修复负坐标窗口导致的图片匹配静默失败、超宽窗口无法归位、激活后尺寸异常
- 修复 `call_elapsed_seconds` 在接听/拒绝路径固定为 0；hold 跳过的日志与返回文案不再误报「用户在电脑前」
- doctor 增强：新增 `[提示]` 行；检查 hook 命令指向目录是否存在（缺失即 FAIL）、多副本报告、运行环境与生效配置；配置解析错误不再静默（`ConfigManager.load_error`）
- hook 命令优先内嵌安装目录内 `.venv` 的解释器（免黑框）；update 改为同时刷新拨打+心跳 hook、校验依赖同步退出码、修复非 Windows 虚拟环境路径
- `requirements.txt` 添加 UTF-8 BOM：修复中文注释在 GBK 默认编码环境下 pip 解析报 UnicodeDecodeError（无需改动注释）
- 文档与 CI：README 新增「安装范围与全机副作用」「多副本体检与收拢」，SKILL.md 清理过时条目；CI 纳入 install.py（编译 / ruff / `--help` 冒烟）
- 修复 CI 与英文区域 Windows 兼容：install.py 的 ruff F541；安装器与 CLI 入口在非 UTF-8 输出流（管道/重定向按 cp1252 等编码）下打印中文不再崩溃（stdout/stderr 错误处理器降级为 replace，`console_scripts` 入口点改指 `main`，控制台脚本与 `-m` 两条路径均生效）；`--json` 摘要统一 ASCII 转义，避免中文在非 UTF-8 流下损坏

## v1.0.3（2026-10-02）

- 移除桌面快捷方式功能（纯人工入口，与 Agent 自动提醒主线无关；手动呼叫可直接对 Agent 说「打给某人」）
- `numalarm uninstall` 保留对旧版本已创建快捷方式的清理能力，升级后不留垃圾文件
- doctor 的 pywin32 检查项更名「语音提醒」（pywin32 现服务于接听后语音提醒的 SAPI 合成）
- config.example.yaml / config.yaml / SKILL.md / README / TESTS.md 同步清理全部快捷方式相关内容，接入方式由四种调整为三种

## v1.0.2（2026-10-02）

- 小白提示词全面幂等化：装到一半中断后重复输入提示词可断点续装，不重复安装任何文件；虚拟声卡安装前先枚举音频设备检测是否已存在
- SKILL.md 新增用户口头开关映射：「本次不使用/恢复提醒」一句话开关全部提醒层（hold 对约定层/Stop hook/看门狗同时生效）
- 新增 `numalarm hold` 暂停机制：等待子代理/CI 等「自动继续回合」免打扰，需要用户操作或最终交付时 `--clear` 恢复，到期自动失效防漏提醒
- Hook 注册事件新增 `Stop`（Agent 回合结束=任务交付时刻）：配合在位检测实现「人离开电脑后任务完成自动响铃；人在电脑前零打扰」
- 新增看门狗（可选）：PreToolUse 心跳 + 计划任务每分钟检查，Agent 硬崩溃（强杀/无响应被杀）导致 hook 失效时自动拨打兜底；心跳协议跨宿主通用
- config 自动发现新增安装目录级：技能目录安装无需设置环境变量
- config.example.yaml 逐项详注：作用/默认/单位/何时调整/注意事项 + 常用场景速查，新增 watchdog 配置段
- 卸载流程补长期记忆清理提醒；README 全面修订（去特定宿主化、其他 Agent 接入指引、更新/卸载/看门狗章节）

## v1.0.1（2026-10-02）

- 新增「接听后语音提醒」（可选）：对方接听后自动播放固定语音（如「任务有新进展，请前往电脑查看」），播完自动挂断；Windows SAPI 离线合成 + 虚拟声卡注入 QQ 麦克风，未装声卡自动降级
- 新增「用户在位检测」（presence）：键鼠空闲低于阈值时自动跳过拨打（仅自动化触发生效），重拨会话中用户回到电脑前自动停止剩余重拨
- 新增 `numalarm update` 一键更新：先检查远端有无新版本（无则提示已最新），更新时自动保留 config.yaml 与校准模板、同步虚拟环境依赖并刷新 hook 注册
- 新增 `numalarm uninstall` 一站式卸载清理（hook/快捷方式/运行时状态），配合目录内独立虚拟环境实现零残留移除
- 新增 `numalarm doctor` 环境自检；`numalarm hook` 支持通过 `--settings` 向任意 Claude Code 兼容宿主注册
- 修复：手动 CLI 调用误受在位检测限制；窗口被拖出屏幕导致语音按钮无法匹配（自动移回可视区）；新版 pyscreeze 空消息异常绕过重试机制
- 实测校准：QQ NT 无全局麦克风设置、自然响铃超时长达数分钟、拒绝弹窗存活 <1s，相关判定逻辑均已适配

## v1.0.0（2026-10-02）

- 首个公开版本
- 核心拨打流程：QQ 进程校验 → 唤起主面板 → 搜索好友（剪贴板支持中文）→ 图片匹配定位语音按钮（置信度可配、自动重试）→ 模拟点击拨打
- 无人接听自动重拨：基于「等待对方接听」状态文本模板判定结果；接听/拒绝即视为触达成功；无人接听按间隔重拨直至触达（次数可不限）；响铃超时主动挂断快速进入下一轮
- 用户在位检测：键鼠空闲低于阈值时自动跳过拨打（仅自动化触发生效），人在电脑前不打扰
- 四种接入方式：Python SDK / HTTP REST（默认 18600）/ CLI / Windows 桌面快捷方式（支持静默后台拨打）
- 宿主 Hook 集成：向 Claude Code 兼容宿主（WorkBuddy / Claude Code / CodeBuddy）注册 Notification / PermissionRequest 自动拨打
- 全配置化：快捷键、时序、置信度、防抖、重拨、在位检测、端口全部进配置文件
- `numalarm doctor` 环境自检、`numalarm test` 校准模式、`numalarm init` 初始化向导

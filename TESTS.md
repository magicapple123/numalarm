# numalarm 测试用例说明

覆盖范围：核心拨打流程、异常与边界、并发/防抖、四种接入方式、桌面快捷方式。
测试环境要求：Windows 10/11 + PC 版 QQ（已登录）+ 已按 assets/README.md 替换按钮截图。

## 0. 自动化预检查（无需 QQ）

```bash
# 语法检查
python -m compileall numalarm

# 单元测试（纯逻辑，跨平台）
pytest -q

# 代码规范
ruff check numalarm tests

# 包导入检查
python -c "import numalarm; print(numalarm.__version__)"

# 环境自检（依赖/配置/素材/QQ 进程逐项体检）
numalarm doctor

# CLI 可用性
numalarm --help
numalarm call --help
numalarm shortcut --help

# 示例配置可解析
python -c "import yaml; from numalarm.common.config import NumAlarmConfig; \
cfg = NumAlarmConfig(**yaml.safe_load(open('config.example.yaml', encoding='utf-8'))); \
print('example config ok:', cfg.server.port)"
```

## 1. 校准测试（不拨打，优先执行）

| 编号 | 用例 | 步骤 | 预期 |
|------|------|------|------|
| TC-000 | 校准通过 | 登录 QQ 后执行 `numalarm test 张三` | 四步全部 PASS，code=200：QQ 进程检测 / 唤起主面板 / 搜索好友-打开聊天窗口 / 语音按钮匹配（不点击，输出坐标） |
| TC-001 | 别名校准 | `numalarm test 老板`（config 中映射到真实昵称） | 同上，detail 显示解析后的真实目标 |

## 2. 核心拨打流程

| 编号 | 用例 | 步骤 | 预期 |
|------|------|------|------|
| TC-01 | SDK 拨打 | `call_qq(target="张三")` | code=200，QQ 向目标发起语音通话，data 含 target/button_position |
| TC-02 | HTTP 拨打 | `numalarm serve` 后 `POST /api/call` | code=200，返回统一 JSON |
| TC-03 | CLI 拨打 | `numalarm call 张三` | 控制台输出 `[200] 拨打成功`，退出码 0 |
| TC-04 | 默认目标 | 不配置 target，`numalarm call` | 使用 default_target 拨打成功 |
| TC-05 | 静默拨打 | `numalarm call 张三 -s` | 全程无控制台输出，退出码 0 |
| TC-06 | 多别名 | 依次 `call 老板` / `call 工作号` | 分别拨打映射目标 |

## 3. 异常与边界

| 编号 | 用例 | 模拟方式 | 预期 |
|------|------|----------|------|
| TC-101 | QQ 未启动 | 完全退出 QQ 后调用 | code=1001，且不会自动启动 QQ |
| TC-102 | 目标不存在 | `numalarm call 不存在的名字` | code=1002 |
| TC-103 | 按钮匹配失败 | 将 assets 截图替换为无关图片，或 `match.confidence` 调至 0.99 | code=1003，重试 2 次后失败 |
| TC-104 | 拨打超时 | `numalarm call 张三 --timeout 1` | code=1004 |
| TC-105 | 通话窗口未出现 | `call_window_hint` 改为不匹配的关键字 | code=1004 |
| TC-106 | 参数错误 | 无 default_target 且不传 target | code=400 |
| TC-107 | 并发占线 | 两个终端同时执行 `numalarm call` | 先到者正常拨打，后到者 code=503 |
| TC-108 | 防抖触发 | 成功拨打后 5 分钟内再次 `call` 同一目标 | code=429，data 含 retry_after_seconds；换目标不受影响 |
| TC-109 | 锁屏调用 | Win+L 锁屏状态下远程触发 | code=1005 或对应步骤失败（锁屏不支持，属预期边界） |
| TC-110 | 锁释放 | TC-107 失败后再次单独调用 | 互斥锁已释放，可正常拨打（finally 保障） |
| TC-111 | 无人接听重拨 | `retry.max_attempts=2`，拨打后**不接听**让其响铃超时 | 第 1 次响铃超时判定无人接听 -> 自动间隔重拨 -> 第 2 次仍无人接听 -> code=1004，data.attempts=2 |
| TC-112 | 不限次重拨至接听 | `retry.max_attempts=0`，某次响铃时**接听** | 持续重拨直至接听 -> code=200，data.outcome=对方已接听，attempts=实际次数 |
| TC-113 | 拒绝即停止 | `retry.max_attempts=0`，响铃时**拒绝** | 通话窗口快速关闭 -> code=200，data.outcome=对方已接听或已拒绝，不再重拨 |
| TC-114 | 重拨期间并发 | 重拨循环进行中另开终端 `call` | code=503 占线（重拨全程持锁） |
| TC-115 | 触发失败重拨 | 拨打瞬间遮挡聊天窗口使按钮点击后无反应 | 判定 CLICK_FAILED -> 自动重拨；达上限后 code=1004 |
| TC-116 | 在位检测跳过 | 人正在操作电脑时执行 `call --auto`（或临时把 presence.idle_seconds 调大） | code=200，data.skipped=true、attempts=0，不产生真实拨打 |
| TC-117 | 手动拨打不受检测 | 不带 `--auto` 执行 `call` | 忽略在位检测，正常拨打 |
| TC-118 | 重拨中途回来 | 重拨会话进行中用户回到电脑前（动鼠标） | 下一轮重拨前检测到在位 -> 停止重拨，code=200 skipped |

## 4. HTTP 接口

```bash
numalarm serve

# 拨打（三种参数形态）
curl -X POST http://127.0.0.1:18600/api/call -H "Content-Type: application/json" \
     -d '{"target": "张三", "timeout": 30, "silent": false}'
curl -X POST http://127.0.0.1:18600/api/call -H "Content-Type: application/json" -d '{}'

# 状态查询：空闲 -> {"code": 200, ..., "data": {"busy": false}}
curl http://127.0.0.1:18600/api/status
# 拨打过程中查询 -> {"code": 503, ..., "data": {"busy": true}}
```

## 5. 桌面快捷方式：创建与功能验证

**创建：**

```bash
numalarm shortcut create 张三                                  # 默认名称：牛马铃-拨打张三
numalarm shortcut create 老板 --name "打老板电话" --silent      # 自定义名称 + 静默
numalarm shortcut create 李四 --name "测试号"                   # 第三个独立快捷方式
```

**验证清单：**

- [ ] 桌面出现对应 `.lnk` 图标，名称符合「牛马铃-拨打{目标}」或自定义 `--name`
- [ ] 双击「牛马铃-拨打张三」→ 弹出控制台执行 → QQ 发起语音通话 → 控制台显示 `[200] 拨打成功`
- [ ] 双击静默快捷方式「打老板电话」→ 无黑框弹出 → QQ 正常发起通话（后台执行）
- [ ] `numalarm shortcut list` 列出全部 3 个快捷方式及其命令参数
- [ ] 多个快捷方式指向不同目标，互不冲突、可并存
- [ ] 快捷方式右键「固定到任务栏」「固定到"开始"屏幕」均可用
- [ ] `numalarm shortcut delete "打老板电话"` → 确认后删除成功；`list` 不再显示
- [ ] 降级验证：`pip uninstall pywin32` 后执行 `shortcut create` → 输出明确提示与手动创建教程，核心 `call` 功能不受影响
- [ ] 非 Windows（如 WSL 内执行）→ 提示仅支持 Windows，不影响核心功能提示

## 6. 验收清单（汇总）

- [ ] `python -m compileall numalarm` 无错误
- [ ] `numalarm test` 四步全 PASS
- [ ] SDK / HTTP / CLI / 快捷方式四方式均可触发拨打（code=200）
- [ ] TC-101 ~ TC-110 异常场景状态码全部符合规范
- [ ] 并发仅一路拨打（503），防抖生效（429），锁正常释放
- [ ] 静默模式无任何标准输出
- [ ] config.example.yaml 全项可解析，修改后行为随之变化（全配置化）

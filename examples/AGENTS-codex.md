# AGENTS.md 约定模板 —— 牛马铃（numalarm）电话提醒

> 适用：OpenAI Codex 及其他读取 AGENTS.md、无 Claude Code hooks 体系的 Agent。
> 使用方法：将下方「约定正文」整段粘贴进你的 AGENTS.md（全局 `~/.codex/AGENTS.md`
> 或项目根目录），并把 `<numalarm 安装目录>` 替换为你机器上的实际安装路径
> （该目录需含 `.venv`，即通过 install.py 或 install 步骤安装的副本）。
> 路径统一使用正斜杠 `/`，避免 bash 类 shell 的转义歧义。

---

# 牛马铃（numalarm）电话提醒约定

工具命令前缀（CALL）：
CALL = "<numalarm 安装目录>/.venv/Scripts/python.exe" -m numalarm.interfaces.cli

## 拨打时机（先拨打，再做后续动作）
- 需要用户决策 / 任务异常中断 / 等待确认 / 任务完成交付时：
  CALL call --silent --auto -m "<原因>"
- --auto 表示用户正在电脑前会自动跳过拨打，无需自行判断

## 免打扰与口头开关
- 回合结束后将自动继续（等待子代理/CI 等，无需用户操作）：先运行 CALL hold -m 30
- 需要用户操作或最终交付：先运行 CALL hold --clear
- 用户说「本次不使用/静音」：CALL hold -m 120
- 用户说「本次使用/恢复提醒」：CALL hold --clear

## 长任务心跳（看门狗崩溃兜底）
- 长任务开始：CALL heartbeat
- 任务结束：CALL heartbeat --clear

## 激活方式
本约定与文件删除安全等技能并列；凡涉及「提醒用户/停下等待/任务完成」，
必须按本约定执行拨打，不得跳过。

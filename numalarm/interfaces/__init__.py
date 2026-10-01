"""numalarm 对外接入层（多 Agent 适配）。

- sdk.py：Python 原生 SDK（from numalarm import call_qq）
- api.py：HTTP REST（FastAPI，默认 127.0.0.1:18600）
- cli.py：命令行与 Windows 桌面快捷方式管理

三种接入方式共用 core.call_executor.CallExecutor 的同一套拨打流程。
"""

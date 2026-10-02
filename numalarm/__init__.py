"""numalarm（牛马铃）：基于桌面 UI 自动化的 QQ 语音通话提醒 Skill。

对外仅暴露核心调用方法，保证「一次封装、多 Agent 复用」：

    from numalarm import call_qq

    result = call_qq(target="张三", timeout=30, silent=False)
    # {"code": 200, "message": "拨打成功", "data": {...}}

其余接入方式见 interfaces 包：HTTP REST（api.py）、CLI 与桌面快捷方式（cli.py）。
"""

from numalarm.interfaces.sdk import call_qq, test_call

__version__ = "1.0.2"

__all__ = ["call_qq", "test_call", "__version__"]

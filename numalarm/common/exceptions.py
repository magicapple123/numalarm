"""numalarm 自定义异常与统一状态码定义。

所有对外接口统一返回字典：{"code": int, "message": str, "data": dict}
状态码规范见 SKILL.md；本模块是状态码唯一来源，业务代码禁止硬编码数字。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# ------------------------- 统一状态码 -------------------------
CODE_SUCCESS = 200              # 拨打成功
CODE_PARAM_ERROR = 400          # 参数错误
CODE_RATE_LIMITED = 429         # 请求过于频繁（防抖触发）
CODE_BUSY = 503                 # 服务占线（正在拨打中）
CODE_QQ_NOT_RUNNING = 1001      # QQ 进程未启动
CODE_TARGET_NOT_FOUND = 1002    # 目标好友未找到
CODE_BUTTON_NOT_FOUND = 1003    # 语音按钮匹配失败
CODE_CALL_TIMEOUT = 1004        # 拨打超时
CODE_UNKNOWN = 1005             # 未知错误

STATUS_MESSAGES: Dict[int, str] = {
    CODE_SUCCESS: "拨打成功",
    CODE_PARAM_ERROR: "参数错误",
    CODE_RATE_LIMITED: "请求过于频繁（防抖触发）",
    CODE_BUSY: "服务占线（正在拨打中）",
    CODE_QQ_NOT_RUNNING: "QQ 进程未启动",
    CODE_TARGET_NOT_FOUND: "目标好友未找到",
    CODE_BUTTON_NOT_FOUND: "语音按钮匹配失败",
    CODE_CALL_TIMEOUT: "拨打超时",
    CODE_UNKNOWN: "未知错误",
}


class NumAlarmError(Exception):
    """numalarm 业务异常基类：携带统一状态码与附加数据。

    子类只需覆盖 ``code`` 类属性即可继承统一状态码体系。
    """

    code: int = CODE_UNKNOWN

    def __init__(self, message: Optional[str] = None, data: Optional[Dict[str, Any]] = None) -> None:
        self.message = message or STATUS_MESSAGES.get(self.code, "未知错误")
        self.data: Dict[str, Any] = data or {}
        super().__init__(self.message)

    def to_result(self) -> Dict[str, Any]:
        """转换为统一返回字典（code / message / data）。"""
        return {"code": self.code, "message": self.message, "data": self.data}


class ParameterError(NumAlarmError):
    """参数错误（目标为空、别名不存在、timeout 非法等）。"""

    code = CODE_PARAM_ERROR


class RateLimitedError(NumAlarmError):
    """防抖触发：同一目标在防抖窗口内重复请求。"""

    code = CODE_RATE_LIMITED


class BusyError(NumAlarmError):
    """服务占线：已有一路拨打流程在执行（全局互斥锁）。"""

    code = CODE_BUSY


class QQNotRunningError(NumAlarmError):
    """QQ 进程未启动，或 QQ 未登录导致主面板无法唤起。"""

    code = CODE_QQ_NOT_RUNNING


class TargetNotFoundError(NumAlarmError):
    """目标好友搜索失败，或聊天窗口未能打开。"""

    code = CODE_TARGET_NOT_FOUND


class VoiceButtonNotFoundError(NumAlarmError):
    """语音通话按钮图片匹配失败（分辨率/DPI 变化、界面遮挡、QQ 版本差异等）。"""

    code = CODE_BUTTON_NOT_FOUND


class CallTimeoutError(NumAlarmError):
    """拨打动作未在超时时间内完成，或通话窗口未在预算内出现。"""

    code = CODE_CALL_TIMEOUT

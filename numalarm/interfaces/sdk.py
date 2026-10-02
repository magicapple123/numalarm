"""Python 原生 SDK 接口。

用法：

    from numalarm import call_qq

    result = call_qq(target="张三", timeout=30, silent=False)
    # {"code": 200, "message": "拨打成功", "data": {"target": "张三", ...}}

    from numalarm import test_call

    result = test_call(target="张三")   # 校准模式，不拨打
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from numalarm.core.call_executor import CallExecutor


def call_qq(
    target: Optional[str] = None,
    timeout: int = 30,
    silent: bool = False,
    reason: str = "",
    auto: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """向指定好友发起 QQ 语音通话（完整拨打流程，含无人接听自动重拨）。

    无人接听时按 ``retry`` 配置自动重拨，直至对方接听或拒绝（均视为触达成功，
    code=200）；``retry.max_attempts`` 可限制总次数（默认 0=不限次）。
    ``timeout`` 为单次拨打建立阶段的预算。

    :param target: 好友昵称/备注，或配置文件 ``target_alias`` 中的别名；
                   为空时使用配置的 ``default_target``
    :param timeout: 单次拨打建立阶段超时（秒）
    :param silent: 静默模式，关闭控制台日志输出
    :param reason: 拨打原因（记录到日志与结果 data，便于 Agent 集成时追溯）
    :param auto: 自动化触发标记（默认 True，Agent 集成使用默认值）——启用「用户在位
                 检测」：用户正在电脑前（键鼠空闲低于 presence.idle_seconds）时跳过
                 拨打；人工脚本显式拨打可传 False 跳过检测
    :param force: True 时单次豁免防抖（默认防抖拦截窗口内重复拨打）
    :return: 统一结果字典 ``{"code": int, "message": str, "data": dict}``；
             data.attempts 为实际拨打次数，data.outcome 为触达结果说明，
             data.judgment 为结果判定方式（template=状态模板 / threshold_fallback=时长回退）
    """
    executor = CallExecutor(silent=silent)
    return executor.call(target=target, timeout=float(timeout), reason=reason, auto=auto, force=force)


def test_call(target: Optional[str] = None, silent: bool = False) -> Dict[str, Any]:
    """校准模式：搜索好友、打开聊天窗口、识别语音按钮，不点击拨打。

    返回的 ``data.steps`` 包含每一步检测结果，用于验证目标设置与按钮匹配。
    """
    executor = CallExecutor(silent=silent)
    return executor.test(target=target)

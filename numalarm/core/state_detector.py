"""进程与界面状态检测。

依赖延迟导入：psutil / pygetwindow 仅在真正调用检测函数时导入，
保证 ``numalarm --help`` 等轻量操作在未安装 UI 依赖时依然可用。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from numalarm.common.logger import get_logger

logger = get_logger("state")


def is_qq_running(process_name: str = "QQ.exe") -> bool:
    """检测 QQ 进程是否正在运行（psutil 遍历进程表）。"""
    import psutil

    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name == process_name.lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def find_windows(title_substring: str) -> List[Dict[str, Any]]:
    """按标题模糊查找所有匹配窗口。

    :return: [{"title", "left", "top", "width", "height"}, ...]
    """
    import pygetwindow as gw

    results: List[Dict[str, Any]] = []
    for win in gw.getWindowsWithTitle(title_substring):
        try:
            if win.width <= 0 or win.height <= 0:
                continue
            results.append(
                {
                    "title": win.title,
                    "left": int(win.left),
                    "top": int(win.top),
                    "width": int(win.width),
                    "height": int(win.height),
                }
            )
        except Exception:  # noqa: BLE001 窗口可能在枚举过程中被关闭
            continue
    return results


def _clamp_to_virtual_screen(win) -> None:
    """若窗口超出虚拟桌面范围（用户拖出屏幕），自动移回可视区域。

    防止聊天工具条/按钮位于屏幕之外导致图片匹配与点击失效。
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        if vw <= 0 or vh <= 0:
            return
        new_x = min(max(win.left, vx), vx + vw - win.width)
        new_y = min(max(win.top, vy), vy + vh - win.height)
        if new_x != win.left or new_y != win.top:
            win.moveTo(int(new_x), int(new_y))
            logger.info("窗口超出屏幕范围，已自动移回可视区域")
    except Exception as exc:  # noqa: BLE001 非 Windows / 调整失败时静默跳过
        logger.debug("窗口位置调整失败：%s", exc)


def activate_window(title_substring: str) -> Optional[Dict[str, Any]]:
    """激活（前置）第一个标题匹配的窗口并返回其信息；失败返回 None。

    窗口若被拖出屏幕范围，会先自动移回可视区域。
    """
    import pygetwindow as gw

    for win in gw.getWindowsWithTitle(title_substring):
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.3)
            _clamp_to_virtual_screen(win)
            return {
                "title": win.title,
                "left": int(win.left),
                "top": int(win.top),
                "width": int(win.width),
                "height": int(win.height),
            }
        except Exception as exc:  # noqa: BLE001 激活失败时尝试下一个候选窗口
            logger.debug("激活窗口失败（%s）：%s", win.title, exc)
    return None


def wait_window(title_substring: str, timeout: float, interval: float = 0.3) -> Optional[Dict[str, Any]]:
    """在超时时间内等待指定标题的窗口出现，出现则激活并返回信息；超时返回 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = activate_window(title_substring)
        if info is not None:
            return info
        time.sleep(interval)
    return None


def window_exists(title_substring: str) -> bool:
    """判断是否存在标题匹配的窗口。"""
    return bool(find_windows(title_substring))


def get_idle_seconds() -> float:
    """获取键鼠空闲时长（自最后一次输入起的秒数，Windows）。

    :return: 空闲秒数；非 Windows 平台或查询失败返回 -1（调用方应视为「不启用检查」）
    """
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return -1.0
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return max(millis / 1000.0, 0.0)
    except Exception:  # noqa: BLE001 非 Windows / 权限异常
        return -1.0

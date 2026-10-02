"""进程与界面状态检测。

依赖延迟导入：psutil / pygetwindow 仅在真正调用检测函数时导入，
保证 ``numalarm --help`` 等轻量操作在未安装 UI 依赖时依然可用。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

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


def _window_process_name(win) -> str:
    """读取窗口所属进程名（小写）；失败返回空串（调用方按「未知」宽容处理）。"""
    try:
        import psutil
        import win32process

        _, pid = win32process.GetWindowThreadProcessId(win._hWnd)
        return (psutil.Process(pid).name() or "").lower()
    except Exception:  # noqa: BLE001 非 Windows / 权限不足 / 窗口枚举期间已销毁
        return ""


def _process_matches(win, process_name: Optional[str]) -> bool:
    """窗口所属进程是否符合期望。未指定进程名或进程名读取失败时不做排除。"""
    if not process_name:
        return True
    actual = _window_process_name(win)
    if not actual:
        return True
    return actual == process_name.lower()


def virtual_screen_rect() -> Optional[Tuple[int, int, int, int]]:
    """虚拟桌面区域 (x, y, width, height)；查询失败返回 None。"""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        h = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        if w <= 0 or h <= 0:
            return None
        return int(x), int(y), int(w), int(h)
    except Exception:  # noqa: BLE001 非 Windows
        return None


def find_windows(title_substring: str, process_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """按标题模糊查找所有匹配窗口。

    :param process_name: 限定窗口所属进程名（如 ``QQ.exe``）；None 表示不限制。
        浏览器等外部窗口的标题可能恰好包含关键字，按进程过滤可避免误匹配。
    :return: [{"title", "left", "top", "width", "height"}, ...]
    """
    import pygetwindow as gw

    results: List[Dict[str, Any]] = []
    for win in gw.getWindowsWithTitle(title_substring):
        try:
            if not _process_matches(win, process_name):
                continue
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
    比屏幕还大的窗口无法整体放入：此时对齐左上角，使可见部分最大化。
    """
    try:
        bounds = virtual_screen_rect()
        if bounds is None:
            return
        vx, vy, vw, vh = bounds
        if win.width >= vw:
            new_x = vx
        else:
            new_x = min(max(win.left, vx), vx + vw - win.width)
        if win.height >= vh:
            new_y = vy
        else:
            new_y = min(max(win.top, vy), vy + vh - win.height)
        if new_x != win.left or new_y != win.top:
            win.moveTo(int(new_x), int(new_y))
            logger.info("窗口超出屏幕范围，已自动移回可视区域")
    except Exception as exc:  # noqa: BLE001 非 Windows / 调整失败时静默跳过
        logger.debug("窗口位置调整失败：%s", exc)


def activate_window(title_substring: str, process_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """激活（前置）第一个标题匹配的窗口并返回其信息；失败返回 None。

    :param process_name: 限定窗口所属进程名（如 ``QQ.exe``）；None 表示不限制。
    窗口若被拖出屏幕范围，会先自动移回可视区域；激活后尺寸仍异常的候选会被跳过。
    """
    import pygetwindow as gw

    for win in gw.getWindowsWithTitle(title_substring):
        try:
            if not _process_matches(win, process_name):
                continue
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.3)
            if win.width <= 0 or win.height <= 0:
                logger.debug("窗口尺寸异常（%sx%s），跳过候选：%s", win.width, win.height, win.title)
                continue
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


def wait_window(title_substring: str, timeout: float, interval: float = 0.3,
                process_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """在超时时间内等待指定标题的窗口出现，出现则激活并返回信息；超时返回 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = activate_window(title_substring, process_name=process_name)
        if info is not None:
            return info
        time.sleep(interval)
    return None


def window_exists(title_substring: str, process_name: Optional[str] = None) -> bool:
    """判断是否存在标题匹配的窗口。"""
    return bool(find_windows(title_substring, process_name=process_name))


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

"""QQ 窗口唤起、好友搜索、聊天窗控制。

仅做公开 UI 模拟（快捷键 / 剪贴板粘贴 / 鼠标点击）：
- 不注入进程、不破解协议、不调用 QQ 私有接口
- 不自动登录 QQ、不存储任何账号密码
"""

from __future__ import annotations

import time
from typing import Any, Dict

from numalarm.common.config import NumAlarmConfig
from numalarm.common.exceptions import NumAlarmError, QQNotRunningError, TargetNotFoundError
from numalarm.common.logger import get_logger
from numalarm.core import state_detector

logger = get_logger("controller")


def press_combo(combo: str) -> None:
    """按下组合快捷键，如 ``"ctrl+alt+z"``、``"ctrl+f"``。"""
    pag = QQController.pag()
    keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
    if keys:
        pag.hotkey(*keys)


class QQController:
    """封装唤起面板、搜索好友、打开聊天窗口三步 UI 操作。"""

    def __init__(self, config: NumAlarmConfig) -> None:
        self.config = config
        pag = self.pag()
        pag.FAILSAFE = bool(config.failsafe)
        pag.PAUSE = 0.1  # 每次 pyautogui 调用之间的最小间隔，主要等待由显式 sleep 控制

    @staticmethod
    def pag():
        """延迟导入 pyautogui；未安装依赖时抛出带指引的业务异常（code=1005）。"""
        try:
            import pyautogui
        except ImportError as exc:  # pragma: no cover
            raise NumAlarmError("依赖 pyautogui 未安装，请先执行 pip install -r requirements.txt") from exc
        return pyautogui

    # ------------------------------------------------------------------
    def wake_panel(self) -> Dict[str, Any]:
        """唤起 QQ 主面板并前置，返回面板窗口信息。

        注意：QQ 的「提取消息」全局热键是开关切换——面板可见时按下会隐藏面板。
        因此先探测面板是否已存在（含最小化），存在则直接前置；不可见时才按热键唤起。

        :raises QQNotRunningError: 热键与窗口查找均失败（可能未登录/标题不匹配）
        """
        # 1. 面板已存在（含最小化）：直接前置恢复，不按热键（避免热键把面板切没）
        info = state_detector.activate_window(self.config.qq_window_title)
        if info is not None:
            logger.info("QQ 主面板已在前台：%s", info["title"])
            return info

        # 2. 面板不可见（托盘/隐藏）：按全局热键唤起后再前置
        press_combo(self.config.hotkey.wake_panel)
        time.sleep(self.config.timing.action_wait)
        info = state_detector.activate_window(self.config.qq_window_title)
        if info is None:
            # 快捷键无效时兜底：继续按标题查找
            info = state_detector.wait_window(self.config.qq_window_title, timeout=3.0)
        if info is None:
            raise QQNotRunningError(
                "未能唤起 QQ 主面板（可能未登录，或 hotkey.wake_panel / qq_window_title 配置与 QQ 不匹配）"
            )
        logger.info("QQ 主面板已前置：%s", info["title"])
        return info

    def search_friend(self, keyword: str) -> Dict[str, Any]:
        """在主面板搜索好友并回车打开聊天窗口。

        中文关键词无法通过键盘模拟输入，使用剪贴板粘贴（Ctrl+V）实现。
        搜索框打开方式由 ``search_method`` 决定：
        - ``click``（默认）：点击主面板左上角搜索框（QQ NT 不响应 Ctrl+F）
        - ``hotkey``：按下 ``hotkey.search`` 快捷键（适配支持快捷键的 QQ 版本）

        :param keyword: 好友昵称/备注（已经过别名解析）
        :return: 聊天窗口（或承载会话的主面板）窗口信息
        :raises TargetNotFoundError: 聊天窗口与主面板均未找到
        """
        pag = self.pag()

        # 1. 前置主面板并确保其可见（最小化时会恢复）
        panel = state_detector.activate_window(self.config.qq_window_title)
        if panel is None:
            raise TargetNotFoundError("未能前置 QQ 主面板，无法执行搜索")

        # 2. 打开搜索框
        if self.config.search_method == "hotkey":
            press_combo(self.config.hotkey.search)
        else:
            offset = self.config.search_box_offset or [225, 81]
            ox, oy = int(offset[0]), int(offset[1])
            pag.click(int(panel["left"]) + ox, int(panel["top"]) + oy)
        time.sleep(self.config.timing.action_wait)

        # 3. 剪贴板粘贴关键词（支持中文；Ctrl+A 覆盖可能残留的旧词）
        try:
            import pyperclip
        except ImportError as exc:  # pragma: no cover
            raise NumAlarmError("依赖 pyperclip 未安装，请先执行 pip install -r requirements.txt") from exc
        pyperclip.copy(keyword)
        time.sleep(0.1)
        pag.hotkey("ctrl", "a")
        time.sleep(0.1)
        pag.hotkey("ctrl", "v")
        time.sleep(self.config.timing.search_wait)

        # 4. 选中搜索结果：默认点击第一条结果（部分 QQ 版本回车无效），可配置为回车
        if self.config.search_select == "enter":
            pag.press("enter")
        else:
            offset = self.config.search_result_offset or [178, 184]
            rx, ry = int(offset[0]), int(offset[1])
            pag.click(int(panel["left"]) + rx, int(panel["top"]) + ry)
        time.sleep(self.config.timing.chat_open_wait)

        # 5. 定位聊天窗口：优先独立聊天窗口（标题含关键词）；
        #    QQ NT 可能在主面板内嵌打开会话，此时回落到主面板
        chat = state_detector.activate_window(keyword)
        if chat is not None:
            logger.info("聊天窗口已打开：%s", chat["title"])
            return chat
        panel = state_detector.activate_window(self.config.qq_window_title)
        if panel is None:
            raise TargetNotFoundError(f"未找到目标「{keyword}」对应的聊天窗口，请确认昵称/备注或别名配置")
        logger.info("未检测到独立聊天窗口，使用主面板内嵌会话：%s", panel["title"])
        return panel

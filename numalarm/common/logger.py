"""numalarm 统一日志模块。

- 日志级别与文件路径全部来自配置（config.log.*），禁止硬编码
- silent 静默模式：不添加任何标准输出/标准错误 handler（文件日志仍保留，便于事后排查）
- 所有模块统一通过 get_logger("子模块名") 获取 logger
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

LOGGER_NAME = "numalarm"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: str = "INFO", log_file: Optional[str] = None, silent: bool = False) -> logging.Logger:
    """初始化 numalarm 根 logger；重复调用会重建 handler（以最后一次为准）。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False

    # 清空已有 handler，避免重复输出
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT)

    # 文件日志：静默模式下也保留
    if log_file:
        try:
            from pathlib import Path

            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(path), encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            # 日志文件不可用时降级为仅控制台，不影响核心功能
            pass

    # 控制台日志：静默模式不添加任何标准输出 handler
    if not silent:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取 numalarm 下的子 logger；未初始化时也能安全使用（无 handler 则不输出）。"""
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)

"""全局互斥锁：同一时间仅允许一路拨打。

- 进程内：threading.Lock
- 跨进程（如 HTTP 服务与 CLI 同时运行）：文件锁（Windows msvcrt / POSIX fcntl）
- 获取失败立即返回 False，不做排队等待，由上层返回 503「占线」
- 无论成功失败，调用结束后必须释放（见 call_executor 的 finally 保障）
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from numalarm.common.config import ConfigManager
from numalarm.common.logger import get_logger

try:  # Windows
    import msvcrt  # type: ignore

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

except ImportError:  # POSIX 兜底（本项目仅面向 Windows，此处保证可移植导入不报错）
    import fcntl  # type: ignore

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class CallMutex:
    """进程内 + 跨进程双重互斥锁。单例使用：``CallMutex.instance()``。"""

    _instance: Optional["CallMutex"] = None
    _singleton_lock = threading.Lock()

    def __init__(self, lock_path: Optional[Path] = None) -> None:
        self._lock_path = lock_path or ConfigManager.lock_file()
        self._thread_lock = threading.Lock()
        self._file_handle = None
        self._held = False
        self._logger = get_logger("lock")

    @classmethod
    def instance(cls) -> "CallMutex":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def acquire(self, quiet: bool = False) -> bool:
        """非阻塞获取锁；成功返回 True，失败（占线）返回 False。

        :param quiet: 静默探测模式下不输出告警日志（供 /api/status 使用）
        """
        if not self._thread_lock.acquire(blocking=False):
            if not quiet:
                self._logger.warning("进程内互斥获取失败：当前进程已有拨打流程")
            return False
        try:
            self._file_handle = open(self._lock_path, "a+")
        except OSError as exc:
            self._thread_lock.release()
            if not quiet:
                self._logger.error("锁文件打开失败：%s", exc)
            return False
        if not _try_lock(self._file_handle.fileno()):
            self._file_handle.close()
            self._file_handle = None
            self._thread_lock.release()
            if not quiet:
                self._logger.warning("跨进程互斥获取失败：其他进程正在拨打")
            return False
        self._held = True
        self._logger.info("互斥锁获取成功")
        return True

    def release(self) -> None:
        """释放锁；未持锁时调用是安全的空操作。"""
        if not self._held:
            return
        self._held = False
        if self._file_handle is not None:
            _unlock(self._file_handle.fileno())
            self._file_handle.close()
            self._file_handle = None
        self._thread_lock.release()
        self._logger.info("互斥锁已释放")

    def probe_busy(self) -> bool:
        """探测当前是否占线（短暂尝试加锁后立即释放，仅用于状态查询）。"""
        busy = not self.acquire(quiet=True)
        if not busy:
            self.release()
        return busy

    def __enter__(self) -> "CallMutex":
        if not self.acquire():
            from numalarm.common.exceptions import BusyError

            raise BusyError()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

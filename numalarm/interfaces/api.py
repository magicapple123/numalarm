"""HTTP REST 接口（FastAPI + Uvicorn）。

启动方式：

    numalarm serve                     # 默认 127.0.0.1:18600
    python -m numalarm.interfaces.api

接口：
    POST /api/call   请求体 {"target": "张三", "timeout": 30, "silent": false}
    GET  /api/status 查询当前是否空闲

适用场景：Dify、Coze、n8n、自研远端 Agent 等支持 HTTP 调用的工具。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from numalarm import __version__
from numalarm.common.config import load_config
from numalarm.common.exceptions import CODE_BUSY, CODE_SUCCESS
from numalarm.common.lock import CallMutex
from numalarm.common.logger import get_logger, setup_logging

app = FastAPI(
    title="numalarm API",
    version=__version__,
    description="牛马铃 - QQ 语音通话提醒服务（仅模拟 UI 点击，无注入无破解）",
)
logger = get_logger("api")


class CallRequest(BaseModel):
    """POST /api/call 请求体。"""

    target: Optional[str] = Field(None, description="好友昵称/备注或别名；为空使用 default_target")
    timeout: int = Field(30, gt=0, description="单次拨打建立阶段超时秒数")
    silent: bool = Field(False, description="静默模式（本次请求不输出控制台日志）")
    auto: bool = Field(True, description="自动化触发标记：True 时受用户在位检测控制（用户在电脑前则跳过）")


@app.post("/api/call")
def api_call(req: CallRequest) -> Dict[str, Any]:
    """发起 QQ 语音通话。同步阻塞直至拨打流程结束，返回统一 JSON。"""
    from numalarm.core.call_executor import CallExecutor

    # 服务进程内已初始化日志，禁止每次请求重配置（否则静默请求会影响全局日志）
    executor = CallExecutor(silent=False, configure_logging=False)
    return executor.call(target=req.target, timeout=float(req.timeout), auto=req.auto)


@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    """查询当前是否空闲（占用包含本服务内的拨打与外部 CLI 进程的拨打）。"""
    busy = CallMutex.instance().probe_busy()
    return {
        "code": CODE_BUSY if busy else CODE_SUCCESS,
        "message": "服务占线（正在拨打中）" if busy else "空闲",
        "data": {"busy": busy},
    }


def run_server(host: Optional[str] = None, port: Optional[int] = None) -> None:
    """启动本地 HTTP 服务；地址/端口来自配置文件，可被参数覆盖。"""
    config = load_config()
    setup_logging(level=config.log.level, log_file=config.log.file, silent=False)
    import uvicorn

    uvicorn.run(
        app,
        host=host or config.server.host,
        port=port or config.server.port,
        log_level="info",
    )


if __name__ == "__main__":
    run_server()

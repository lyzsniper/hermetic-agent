"""test_log_middleware_context_fix.py — 校验 Sanic 25 跨 context clear 不再炸.

Sanic 25 response 中间件跟 request 中间件跑在不同的 asyncio context 里,
LogMiddleware 在 _on_request 里 bind_request_context, _on_response 里
clear_request_context, 但跨 context 调用 ContextVar.reset(token) 会抛
ValueError.

修复: clear_request_context 在 try/except 中调, 失败只记 debug 日志.

运行:
    pytest tests/test_log_middleware_context_fix.py -v
"""
from __future__ import annotations

import asyncio

from hermetic_agent.audit.log.log_context import (
    bind_request_context,
    clear_request_context,
)


def test_clear_in_same_context_works() -> None:
    """同 context 下 clear 应正常, 不抛错."""
    from datetime import datetime

    tokens = bind_request_context("test-req-1", datetime.now())
    clear_request_context(tokens)


def test_clear_in_different_context_raises_value_error() -> None:
    """跨 context 调用 reset(token) 必然抛 ValueError (Python 不变行为).

    我们只是确认这个不变量 — log_middleware 修复的核心是接受这个行为,
    把异常吞掉不让响应链路挂掉.
    """
    from datetime import datetime

    tokens = bind_request_context("test-req-2", datetime.now())

    async def inner_clear() -> None:
        # Sanic 在 inner context 跑, reset 这里会抛 ValueError
        with __import__("pytest").raises(ValueError, match="different Context"):
            clear_request_context(tokens)

    asyncio.run(inner_clear())


def test_log_middleware_swallows_value_error(monkeypatch) -> None:
    """构造一个 token, mock 让它跨 context, 然后调 middleware 的清理路径
    验证不会 500.

    实际: Sanic 25 的 request/response 中间件在两个不同 asyncio Task 里跑,
    ContextVar.Token 的语义就是 per-context. 但 middleware 不能让日志
    步骤挂掉主链.
    """
    from datetime import datetime

    from hermetic_agent.audit.log.log_middleware import LogMiddleware
    from hermetic_agent.audit.log.request_logger import RequestLogger

    rl = RequestLogger(log_system_type="test")
    mw = LogMiddleware(rl)

    # 模拟 _on_request 流程
    tokens = bind_request_context("mw-test-req-3", datetime.now())
    mw._tokens = tokens

    # 模拟跨 context: 直接调用 clear, 测 ValueError 是否被吞掉
    # 实际上是测试 middleware._on_response 里的 try/except 是否生效.
    # 这里用 monkeypatch 让 clear 抛 ValueError, 验证 middleware 不传递异常.
    monkeypatch.setattr(
        "hermetic_agent.audit.log.log_middleware.clear_request_context",
        lambda _t: (_ for _ in ()).throw(ValueError("different Context")),
    )

    # 验证调用 _on_response 不抛
    class _Req:
        path = "/test"
        method = "GET"
        ip = "127.0.0.1"
        headers = {}
        ctx = type("Ctx", (), {"log_skip": False, "log_done": False, "req_seq_no": "x", "req_start_time": datetime.now()})()

    class _Resp:
        status = 200

    asyncio.run(mw._on_response(_Req(), _Resp()))  # 不应抛
    assert mw._tokens is None  # 已清

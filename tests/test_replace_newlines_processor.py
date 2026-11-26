"""test_replace_newlines_processor.py — 验证 structlog 处理器把多行字符串折成单行."""
from __future__ import annotations

from hermetic_agent.api.http.logging_setup import _replace_newlines_in_strings


def test_replaces_newlines_in_event_dict_string_values() -> None:
    ed = {"event": "x", "msg": "line1\nline2\nline3", "ok": 1}
    out = _replace_newlines_in_strings(None, "info", dict(ed))
    assert out["msg"] == "line1 | line2 | line3"
    assert out["event"] == "x"
    assert out["ok"] == 1


def test_leaves_non_strings_unchanged() -> None:
    ed = {"event": "x", "n": 42, "b": True, "lst": [1, 2, 3]}
    out = _replace_newlines_in_strings(None, "info", dict(ed))
    assert out["n"] == 42
    assert out["b"] is True
    assert out["lst"] == [1, 2, 3]


def test_handles_crlf_and_trailing_newline() -> None:
    ed = {"event": "x", "msg": "a\r\nb\r\nc\r\n"}
    out = _replace_newlines_in_strings(None, "info", dict(ed))
    assert out["msg"] == "a | b | c"


def test_no_change_when_no_newline() -> None:
    ed = {"event": "x", "msg": "single line"}
    out = _replace_newlines_in_strings(None, "info", dict(ed))
    assert out["msg"] == "single line"

"""test_scenario_db_load.py — load_active_scenarios_from_db 行为校验.

验证:
1. DB 同名 scenario 应覆盖 filesystem YAML
2. 无效 config JSON 应被跳过
3. 无 services / 无 scenario_service 时应 fallback 0

运行:
    pytest tests/test_scenario_db_load.py -v
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from hermetic_agent.scenarios import ScenarioRegistry
from hermetic_agent.scenarios.config import ScenarioConfig
from hermetic_agent.scenarios.db_lifecycle import load_active_scenarios_from_db


# ---------------------------------------------------------------------------
# Mock 对象 — 替代真实 MySQL scenario service
# ---------------------------------------------------------------------------


@dataclass
class _FakeRow:
    code: str
    name: str
    version: int = 1
    config: dict[str, Any] = field(default_factory=dict)
    status: str = "enabled"


@dataclass
class _FakeScenarioService:
    rows: list[_FakeRow] = field(default_factory=list)

    async def list_active(self, *, limit: int = 200) -> list[_FakeRow]:
        return self.rows[:limit]


@dataclass
class _FakeServices:
    scenario: _FakeScenarioService | None = None


@dataclass
class _FakeApp:
    ctx: Any = None

    def __post_init__(self) -> None:
        self.ctx = type("Ctx", (), {})()


def _make_cfg_dict(name: str, *, version: str = "1.0.0", tier: str = "bronze") -> dict[str, Any]:
    """构造最小的合法 ScenarioConfig dict."""
    return {
        "name": name,
        "version": version,
        "description": f"test scenario {name}",
        "enabled": True,
        "tags": ["test"],
        "owner": "test",
        "tier": tier,
        "routing": {"trigger_keywords": [], "priority": 10},
        "execution": {
            "system_prompt": f"You are {name} test assistant.",
            "skills": [],
            "tools": [],
            "orchestration": "single",
        },
        "security": {
            "tool_level": "safe",
            "allowed_tools": [],
            "denied_tools": [],
            "allowed_commands": [],
            "denied_commands": ["rm -rf", "sudo", "dd"],
            "network": "off",
            "max_turns": 5,
            "max_budget_usd": 0.1,
            "require_approval_for_writes": True,
        },
        "workspace": {
            "strategy": "project_relative",
            "workspace_dirs": ["${PROJECT_DIR}"],
            "readonly_dirs": [],
            "deny_dirs": ["/etc"],
            "deny_path_patterns": [],
            "launcher": {"prefer_engine": "auto", "fallback_engine": "auto"},
        },
        "a2ui": {"enabled": False, "protocol": "auip"},
        "progressive_skill": {
            "strategy": "none",
            "budget_tokens": 500,
            "budget_policy": "error",
            "initial_skills": [],
            "load_on_state": {},
        },
        "resource_dirs": {
            "prompts": "${WORK_SHARED}/prompts/_generic",
            "skills": "${WORK_SHARED}/skills/_generic",
        },
        "resources": {"agent": None, "model": "MiniMax-M2.7-highspeed", "timeout": 60},
        "metadata": {"is_test": True},
    }


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> ScenarioRegistry:
    return ScenarioRegistry(ctx={"PROJECT_DIR": "/tmp", "WORK_SHARED": "/tmp/shared"})


@pytest.fixture
def fake_app() -> _FakeApp:
    return _FakeApp()


@pytest.mark.asyncio
async def test_load_overrides_existing_yaml(registry: ScenarioRegistry, fake_app: _FakeApp) -> None:
    """DB 同名 scenario 应覆盖 filesystem YAML 加载的."""
    old_cfg = ScenarioConfig.model_validate(_make_cfg_dict("paper_agent", tier="bronze"))
    registry.register(old_cfg)
    assert registry.get("paper_agent").tier == "bronze"

    new_cfg_dict = _make_cfg_dict("paper_agent", tier="gold")
    fake_app.ctx.services = _FakeServices(
        scenario=_FakeScenarioService(rows=[_FakeRow(code="paper_agent", name="paper_agent", config=new_cfg_dict)])
    )

    loaded = await load_active_scenarios_from_db(registry, fake_app)
    assert loaded == 1
    assert registry.get("paper_agent").tier == "gold"


@pytest.mark.asyncio
async def test_load_skips_invalid_json(registry: ScenarioRegistry, fake_app: _FakeApp) -> None:
    """config 为 None 或空 dict 时跳过."""
    fake_app.ctx.services = _FakeServices(
        scenario=_FakeScenarioService(
            rows=[
                _FakeRow(code="bad", name="bad", config={}),
                _FakeRow(code="also_bad", name="also_bad", config=None),
            ]
        )
    )
    loaded = await load_active_scenarios_from_db(registry, fake_app)
    assert loaded == 0
    assert "bad" not in registry.list_names()
    assert "also_bad" not in registry.list_names()


@pytest.mark.asyncio
async def test_load_skips_invalid_schema(registry: ScenarioRegistry, fake_app: _FakeApp) -> None:
    """config JSON 不合法 (缺必填字段) 应跳过."""
    bad_cfg = {"name": "broken"}
    fake_app.ctx.services = _FakeServices(
        scenario=_FakeScenarioService(rows=[_FakeRow(code="broken", name="broken", config=bad_cfg)])
    )
    loaded = await load_active_scenarios_from_db(registry, fake_app)
    assert loaded == 0
    assert "broken" not in registry.list_names()


@pytest.mark.asyncio
async def test_load_no_services_fallback(registry: ScenarioRegistry, fake_app: _FakeApp) -> None:
    """app.ctx.services 未设置时返回 0."""
    loaded = await load_active_scenarios_from_db(registry, fake_app)
    assert loaded == 0


@pytest.mark.asyncio
async def test_load_no_scenario_service_fallback(registry: ScenarioRegistry, fake_app: _FakeApp) -> None:
    """scenario_service 为 None 时返回 0."""
    fake_app.ctx.services = _FakeServices(scenario=None)
    loaded = await load_active_scenarios_from_db(registry, fake_app)
    assert loaded == 0


@pytest.mark.asyncio
async def test_load_empty_db(registry: ScenarioRegistry, fake_app: _FakeApp) -> None:
    """DB 无 enabled rows 时返回 0."""
    fake_app.ctx.services = _FakeServices(scenario=_FakeScenarioService(rows=[]))
    loaded = await load_active_scenarios_from_db(registry, fake_app)
    assert loaded == 0

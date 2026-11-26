"""
seed_agents.py — 从 work/scenarios/ 下所有 *.scenario.yaml 同步出 Agent 资产入 DB.

scenario 表 (路由编排) 和 agents 表 (前端资产页) 是同一份 YAML 的两种投影:
- scenario 含 routing / security / workspace / orchestration
- agent   含 system_prompt / model / tool_level / network / *_codes 引用

DB-only 模式下, 两个 seed 脚本覆盖 work/scenarios/ 全集.

运行:
    python scripts/seed_agents.py
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from tortoise import Tortoise

from hermetic_agent.config.settings import get_settings
from hermetic_agent.scenarios.loader import load_scenario
from hermetic_agent.store.models.agent import Agent

WORK_DIR = Path(__file__).resolve().parents[1] / "work"
SCENARIOS_DIR = WORK_DIR / "scenarios"

PLACEHOLDERS: dict[str, str] = {
    "PROJECT_DIR": str(WORK_DIR),
    "WORK_SHARED": str(WORK_DIR / "shared"),
    "WORK_ROOT": str(WORK_DIR),
    "SCENARIO_DIR": "",
}


def _derive_agent_code(scenario_code: str) -> str:
    return scenario_code


async def init_db() -> None:
    settings = get_settings()
    await Tortoise.init(
        db_url=settings.mysql_dsn,
        modules={"models": ["hermetic_agent.store.models"]},
    )


async def close_db() -> None:
    await Tortoise.close_connections()


async def upsert_agent_from_scenario(yaml_path: Path) -> tuple[str, bool]:
    """读取 YAML → 转 Agent row → upsert."""
    cfg = load_scenario(yaml_path, PLACEHOLDERS)
    code = _derive_agent_code(cfg.name)
    name = cfg.name
    description = cfg.description or ""
    system_prompt = cfg.execution.system_prompt or ""
    model = cfg.resources.model or "openai/gpt-4o-mini"
    tool_level = cfg.security.tool_level
    network = cfg.security.network
    owner = cfg.owner or "anonymous"
    visibility = "public"
    status = "enabled" if cfg.enabled else "draft"

    existing = await Agent.get_or_none(code=code, is_deleted=False)

    if existing is not None:
        existing.name = name
        existing.description = description
        existing.system_prompt = system_prompt
        existing.model = model
        existing.tool_level = tool_level
        existing.network = network
        existing.owner_user_id = owner
        existing.visibility = visibility
        existing.status = status
        await existing.save()
        return code, False

    new_id = uuid.uuid4()
    await Agent.create(
        id=new_id,
        code=code,
        name=name,
        description=description,
        system_prompt=system_prompt,
        model=model,
        tool_level=tool_level,
        network=network,
        skill_codes=[],
        mcp_server_codes=[],
        prompt_codes=[],
        command_codes=[],
        owner_user_id=owner,
        visibility=visibility,
        status=status,
        is_deleted=False,
    )
    return code, True


async def main() -> None:
    await init_db()
    try:
        print(f"connected: mysql={get_settings().mysql_dsn}")
        print("-" * 60)

        yaml_files = sorted(SCENARIOS_DIR.rglob("*.scenario.yaml"))
        if not yaml_files:
            print(f"[ERROR] no *.scenario.yaml files under {SCENARIOS_DIR}")
            return
        for p in yaml_files:
            if not p.exists():
                print(f"[SKIP] not found: {p.name}")
                continue
            try:
                code, created = await upsert_agent_from_scenario(p)
                action = "CREATED" if created else "UPDATED"
                print(f"[{action}] agent code={code}")
            except Exception as e:
                print(f"[ERROR] {p.name}: {type(e).__name__}: {e}")
                raise

        print("-" * 60)
        total = await Agent.all().count()
        print(f"total agents in DB: {total}")

    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

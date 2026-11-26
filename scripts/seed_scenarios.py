"""
seed_scenarios.py — 把 work/scenarios/ 下所有 *.scenario.yaml 入库.

DB-only 模式下 (docker-compose 注释掉 ./work 挂载), 这是 scenario 资产的
唯一入口. run 时把所有 YAML 都 upsert 到 scenarios 表 (含 _default,
astronomy_agent, paper_agent 等).

运行:
    python scripts/seed_scenarios.py
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from tortoise import Tortoise
from tortoise.transactions import in_transaction

from hermetic_agent.config.settings import get_settings
from hermetic_agent.scenarios.loader import load_scenario
from hermetic_agent.store.models.scenario import Scenario

WORK_DIR = Path(__file__).resolve().parents[1] / "work"
SCENARIOS_DIR = WORK_DIR / "scenarios"

PLACEHOLDERS: dict[str, str] = {
    "PROJECT_DIR": str(WORK_DIR),
    "WORK_SHARED": str(WORK_DIR / "shared"),
    "WORK_ROOT": str(WORK_DIR),
    "SCENARIO_DIR": "",  # 占位, load 时会被 _validate_resources 跳过（skills: [] 不校验）
}


def make_code_from_name(name: str) -> str:
    """scenario name 转 code：去版本号前缀 + 小写."""
    return name.lower().strip()


async def init_db() -> None:
    """连 MySQL（Tortoise ORM asyncio 模式）."""
    settings = get_settings()
    dsn = settings.mysql_dsn
    await Tortoise.init(
        db_url=dsn,
        modules={"models": ["hermetic_agent.store.models"]},
    )


async def close_db() -> None:
    await Tortoise.close_connections()


async def upsert_scenario(yaml_path: Path) -> tuple[str, str, bool]:
    """读取 YAML → 转 config dict → upsert 到 scenarios 表.

    返回 (code, name, created_or_updated).
    """
    cfg = load_scenario(yaml_path, PLACEHOLDERS)
    code = make_code_from_name(cfg.name)
    config_dict = cfg.model_dump(mode="json")

    description = cfg.description
    source = "yaml"
    status = "enabled" if cfg.enabled else "disabled"

    # upsert: code+version=1 已存在则更新 config, 否则插入
    async with in_transaction() as txn:
        existing = await Scenario.get_or_none(
            code=code, version=1, is_deleted=False,
        )
        if existing is not None:
            # 更新
            await existing.update_from_dict({
                "name": cfg.name,
                "description": description,
                "config": config_dict,
                "source": source,
                "status": status,
            })
            await existing.save(using_db=txn)
            return code, cfg.name, False
        else:
            # 新建
            new_id = uuid.uuid4()
            await Scenario.create(
                id=new_id,
                code=code,
                name=cfg.name,
                version=1,
                description=description,
                config=config_dict,
                source=source,
                status=status,
                is_deleted=False,
                using_db=txn,
            )
            return code, cfg.name, True


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
                code, name, created = await upsert_scenario(p)
                action = "CREATED" if created else "UPDATED"
                print(f"[{action}] code={code:<24} name={name}")
            except Exception as e:
                print(f"[ERROR] {p.name}: {type(e).__name__}: {e}")
                raise

        print("-" * 60)
        total = await Scenario.all().count()
        print(f"total scenarios in DB: {total}")

    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

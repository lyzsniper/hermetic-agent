"""scenarios/db_lifecycle.py — 从 MySQL scenarios 表加载 scenario 进 registry.

被 ``api.lifecycle.scenario_lifecycle.init_scenarios`` 调用.
独立模块以避免 cycle: 本模块只依赖 ScenarioConfig + 一个接受 kwargs 的 registry.
"""
from __future__ import annotations

from typing import Any

import structlog

from hermetic_agent.scenarios.config import ScenarioConfig

logger = structlog.get_logger(__name__)


async def load_active_scenarios_from_db(registry: Any, app: Any) -> int:
    """从 app.ctx.services.scenario 读取 DB, 注册到 registry.

    优先级: DB config > filesystem YAML (同名 scenario 以 DB 为准).
    依赖 storage.connect() 已完成 (lifecycle.startup 里 storage 先 connect).

    Returns:
        成功加载并注册的 scenario 数量.
    """
    services = getattr(app.ctx, "services", None)
    if services is None:
        logger.debug("scenario_db_load_skip_no_services")
        return 0
    scenario_svc = getattr(services, "scenario", None)
    if scenario_svc is None:
        logger.debug("scenario_db_load_skip_no_scenario_service")
        return 0

    try:
        db_rows = await scenario_svc.list_active(limit=200)
    except Exception as e:
        logger.warning("scenario_db_query_failed", error=str(e))
        return 0

    if not db_rows:
        logger.info("scenario_db_empty")
        return 0

    loaded = 0
    for row in db_rows:
        cfg_dict = getattr(row, "config", None)
        if not cfg_dict or not isinstance(cfg_dict, dict):
            logger.warning(
                "scenario_db_skip_no_config",
                code=getattr(row, "code", "?"),
            )
            continue
        try:
            cfg = ScenarioConfig.model_validate(cfg_dict)
            registry.register(cfg, override=True)
            loaded += 1
        except Exception as e:
            logger.warning(
                "scenario_db_register_failed",
                code=getattr(row, "code", "?"),
                error=str(e),
            )
    return loaded


__all__ = ["load_active_scenarios_from_db"]

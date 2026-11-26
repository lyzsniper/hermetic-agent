# 2026-07-08 — hermetic-agent 外部业务接入设计

> 用户场景：把 hermetic-agent 当通用 AI 网关，让其他业务项目（论文助手 charming-reader、天文助手 AstroAgent）通过 HTTP + SSE 调用，复用场景路由 / 模型调度 / 会话持久化。
>
> 用户拍板：**hermetic-agent 零代码改动**，只新增 2 个场景 YAML；两侧 app 自己写薄 adapter + 把 hermetic 连接配置写到自己 `.env`；主诉求是流式。

---

## §1 — 整体架构（已确认）

```
                  ┌──────────────────────────────────────────┐
                  │  hermetic-agent Hub (Sanic, 零代码改动)   │
                  │  ─ 新增 scenarios YAML × 2                │
                  │  ─ :28000 (容器) / :8000 (dev)             │
                  └──────────────────┬───────────────────────┘
                                     │ POST /agent/chat/stream
                                     │ X-Scenario: astronomy_agent | paper_agent
                                     │ X-Tenant-ID: astro | paper
                                     ▼
        ┌────────────────────────────┴────────────────────────────┐
        ▼                                                          ▼
┌────────────────────┐                                  ┌──────────────────────────┐
│  AstroAgent        │                                  │  charming-reader         │
│  (天文)             │                                  │  (论文 RAG 不动)          │
│                    │                                  │                          │
│  lib/agent/        │                                  │  新增 hermetic_client.py │
│   hermetic.ts      │                                  │   + /chat/hermetic       │
│   opencode.ts      │                                  │   流式端点                 │
│  + .env.local      │                                  │  原 RAG 流不动             │
│                    │                                  │   (LiteLLM / ADK 链路    │
│  Hermes + 真 WebGL │                                  │    全保留)               │
│  + NEO + arXiv     │                                  │  + .env                  │
│   (本地计算)       │                                  │                          │
└────────────────────┘                                  └──────────────────────────┘
```

## §2 — 边界契约

1. **协议**：HTTP POST + `Content-Type: text/event-stream`，事件序列固定为 `scenario → session → text/reasoning/tool_use/tool_result/state → done`。详细 schema 见 `docs/openapi.json` 的 `/agent/chat/stream`。
2. **路由**：消费方通过 `X-Scenario` header 锁定专属 scenario（`astronomy_agent` / `paper_agent`）；router 6 优先级里第 2 档命中，**绕过 LLM 意图分类**，延迟稳定。
3. **会话**：消费方自己生成/复用 `session_id`（UUID v4）。首次请求 hermetic-agent 自动建 session；session / turn / message 自动落 hermetic-agent DB。
4. **错误**：响应里 `code` 字段对应 12 个错误码之一（见 `docs/architecture-and-flow.md §7`），detail 字段含可读错误。

## §3 — hermetic-agent 侧改动（唯一）

**`work/scenarios/astronomy_agent.scenario.yaml`** — 物理计算类场景，不加载 skill/MCP，权限最严格。

**`work/scenarios/paper_agent.scenario.yaml`** — 文本生成场景，加载 system_prompt 即可，权限给 Read 类。

字段（按 `scenario_config.py` 严格校验）：
- `execution.orchestration: single` — 流式
- `execution.system_prompt`: 量身写
- `execution.skills: []`
- `execution.tools: []`
- `security.tool_level: safe` — 拒绝任何代码执行/网络（hermetic-agent 默认工作目录在 opencode 沙箱里，无意义开起来危险）
- `routing.priority: 10` — 显式 header 路由兜底
- `resources.model: MiniMax-M2.7-highspeed` — 与 _default 保持一致，避免双模型管理

**校验**：落地后跑 `pytest tests/test_scenario_smoke.py -v` 验证 YAML 通过 `ScenarioLoader`。

## §4 — AstroAgent 侧改动

`astro-api/lib/agent/` 新文件 `hermetic.ts` — 替换 `opencode.ts` 里的 `runWithOpencode()`，保持 `runAgent()` 公共签名不变（`AsyncIterable<AgentEvent>`）。

事件映射（hermetic → AstroAgent 现状）：
| hermetic-agent 事件 | AstroAgent AgentEvent.type |
|---|---|
| `scenario` / `session` | （内部消化） |
| `text` (`data.content`) | `token` (`text`) |
| `tool_use` (`data.tool_name`) | `tool` (`name`+`args`) — 不真调本地工具，hermetic 自带沙箱 |
| `tool_result` (`data.output`) | `tool_result` (`name`+`result`) |
| `error` (code 字段) | `error` (message) — 含 12 个 code 的入参位置 |
| `done` | `done` (sessionId) |

`astro-api/lib/agent/opencode.ts`：在 `runAgent()` 里加 `if HERMETIC_BASE_URL then runWithHermetic() else runWithOpencode()/runFallback()`；保留 `OPENCODE_BASE_URL` 路径不动，作为 fallback。

`astro-api/app/api/chat/route.ts`：**不改**（`runAgent()` 接口零变化）。

`astro-api/.env.example`：新增：
```
HERMETIC_BASE_URL=http://localhost:28000
HERMETIC_SCENARIO=astronomy_agent
HERMETIC_TENANT_ID=astro
HERMETIC_TIMEOUT_MS=60000
```

**保留 fallback** — `HERMETIC_BASE_URL` 不设时，继续走原 `OPENCODE_BASE_URL` / 离线规则分支。

## §5 — charming-reader 侧改动

**保留**：`flow.py` / `paper_agent/agent.py` / LiteLLM / LangChain / LlamaIndex / ADK — 一行不动。

**新增**：
1. `backend/app/services/hermetic_client.py` — 异步 HTTP + SSE 解析器（httpx.AsyncClient）。`async def chat_stream(message, *, session_id, ...) → AsyncIterator[HermeticEvent]`。env 读取 `HERMETIC_BASE_URL` / `HERMETIC_SCENARIO` / `HERMETIC_TENANT_ID` / `HERMETIC_TIMEOUT_MS`。
2. `backend/app/api/endpoints.py`（增 1 个端点，不删旧的）—— `POST /chat/hermetic`（SSE，Python 异步 yield 已有 `agent_content` / `tool_call` / `tool_result` / `agent_thinking` / `agent_complete` 事件，与原 `/chat/cancel` 流式规范一致）。
3. `backend/app/core/config.py` — 加 4 个 `HERMETIC_*` 配置项（默认空字符串 → 不启用）。
4. `backend/.env.example` — 4 个 `HERMETIC_*` 占位。

**取舍说明**：不替换原 LiteLLM，是因为 `flow.py` 在 Google ADK + LiteLlm + tools/MCP + 13 个 agent 子类型之间深度耦合，全面重构需 ~3-5 天且价值不显。**新 hermetic 流是平行轨道**，给前端一个切换开关（`general_mode: hermetic` vs `general_mode: rag`），由用户在聊天页顶部下拉里选。先验证 hermetic 稳定，未来再逐个 agent 替换 ADK 调用。

**charming-reader SSE 输出事件**：
| hermetic 事件 | charming-reader 现有事件（写到 SSE `data:` 字段）|
|---|---|
| `text` (`data.content`) | `agent_content` (`content`) |
| `tool_use` | `tool_call` (`tool_name`+`arguments`+`status=start`) |
| `tool_result` | `tool_result` (`tool_name`+`result`+`success`) |
| `error` | `error` (`code`+`message`) — 12 code 字段透传 |
| `done` | `agent_complete` |

## §6 — 配置文件

每个 app 自己的 `.env.local` / `.env` 读取：

| Key | AstroAgent 默认 | charming-reader 默认 |
|---|---|---|
| `HERMETIC_BASE_URL` | `http://localhost:28000` | `http://localhost:28000` |
| `HERMETIC_SCENARIO` | `astronomy_agent` | `paper_agent` |
| `HERMETIC_TENANT_ID` | `astro` | `paper` |
| `HERMETIC_TIMEOUT_MS` | `60000` | `60000` |

**hermetic-agent 容器内端口**：默认 `28000` 映射到 host（compose 配置见 `docker-compose.yml`）。dev 直接跑 `hermetic-agent` 时端口是 `8000`（看 `AGENTS.md` §2 命令速查）。

## §7 — 错误处理

消费方 adapter 必须识别 hermetic-agent 的 12 个 error code：

| Code | 含义 | 消费方处理 |
|---|---|---|
| `ROUTING_FAILED` | 6 优先级都没命中 | 500 + 提示用户切换场景 |
| `SCENARIO_DISABLED` | scenario 临时禁用 | 503，提示稍后再试 |
| `INJECTION_FAILED` | 白名单冲突 | 400 + 清理 caller skills/tools |
| `SESSION_ERROR` | session_id 无效 | 400，丢弃本地 session 让 hermetic 重发 |
| `PROVIDER_NOT_FOUND` | model 不存在 | 500，提示检查 scenario YAML 的 `resources.model` |
| `CHAT_FAILED` | LLM 调用失败 | 500，重试 1 次 |
| `PERSIST_FAILED` | DB 写入失败 | log 警告，不阻塞 chat |
| `TOOL_NOT_ALLOWED` | 工具不在白名单 | 400，告诉前端去掉工具 |
| `AGENT_NOT_REGISTERED` | 没注册 agent | 500，提示检查 opencode sandbox |
| `STREAM_GATE_ERROR` | 流式闸门 | 500，重连 SSE |
| `TRANSCRIPT_ABORTED` | 用户/系统中止 | 200，正常 done |
| `TIMEOUT` | LLM 超时 | 504，按 timeout 配置重试 |

**第一阶段消费方行为**：识别到 4xx → 直接报错给前端；5xx → 退避 1.5s 重试 1 次；成功 200 → 正常消费 SSE。

## §8 — 测试

hermetic-agent：
- `pytest tests/test_scenario_smoke.py -v` —— 新 YAML 加载通过
- `pytest tests/test_scenario_router.py -v` —— X-Scenario header 命中新场景

AstroAgent（人工，本地 dev）：
- `curl -X POST http://localhost:8000/agent/chat/stream -H "X-Scenario: astronomy_agent" -H "X-Tenant-ID: astro" -d '{"message":"猎户座的参宿四现在是什么状态？","session_id":"test-1"}'`
- 启 `astro-web` dev server，前端 store.ts 看到 token 事件，写入 messages[]

charming-reader：
- 启 hermetic-agent（先）+ AstroAgent dev + charming-reader
- 前端切到 hermetic 模式，问"帮我总结一篇 PDF"（无 RAG），应直接走 hermetic 流
- 问"看看我上传的论文"，应仍走 RAG 路径

**人工 smoke test 必跑项**（写在每个 adapter 文件 docstring 顶部）：
1. hermetic-agent up，/ready 200
2. AstroAgent → POST /api/chat (前端)，消息 "猎户座参宿四" → 看到 token 流
3. charming-reader → POST /chat/hermetic（curl），消息 "你好" → 看到 agent_content 流
4. 3 个 app 的日志里 `scenario=` 字段应分别命中 `astronomy_agent` / `paper_agent`

## §9 — 后续 (out of scope)

- API key 鉴权中间件（生产暴露前必做）
- 用户级 quota / 限流（per-tenant counter）
- hermetic-agent SDK（如果第 3 个 app 来，~200 行 httpx + SSE + pydantic）
- 消费方 adapter 共享（npm pypi 包 / pip 包）

---

**实现日期**：2026-07-08
**决定者**：用户
**改动原则**：hermetic-agent 零 src 改动；消费方各自改 1 个 .env + 1 个 adapter 文件 + 1 个 endpoint。

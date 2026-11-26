# hermetic-agent 外部业务接入 — Quick Start

> 2026-07-08; 三项目联调 checklist.

---

## 1. 启动顺序

```bash
# ① hermetic-agent (Hub, 通用 AI 网关)
cd C:\WorkSpace\Coding\hermetic\hermetic-agent
hermetic-agent                              # 或 python -m hermetic_agent.main
# 验证: curl http://localhost:8000/ready → 200

# ② AstroAgent (天文助手)
cd C:\WorkSpace\Coding\AstroAgent\astro-api
copy .env.example .env.local                 # 配 HERMETIC_BASE_URL 等
npm install
npm run dev                                 # → http://localhost:3001

cd C:\WorkSpace\Coding\AstroAgent\astro-web
npm install
npm run dev                                 # → http://localhost:5173

# ③ charming-reader (论文助手 — RAG 链路不动)
cd C:\WorkSpace\Coding\charming-reader\backend
copy .env.example .env                      # 配 DATABASE_URL / HERMETIC_* 等
uv venv && uv pip install -r requirements.txt
python app/main.py                          # → http://localhost:18000
```

**hermetic-agent 容器模式**：如果用 docker compose，hub 端口是 `28000`，消费方 `.env` 里 `HERMETIC_BASE_URL=http://localhost:28000`。

---

## 2. .env 模板（已加入各仓库 `.env.example`）

### astro-api/.env.local

```env
HERMETIC_BASE_URL=http://localhost:8000
HERMETIC_SCENARIO=astronomy_agent
HERMETIC_TENANT_ID=astro
HERMETIC_TIMEOUT_MS=60000

NASA_API_KEY=8l8ZNeYF3Yck8prOV5vNvrRLtoR41aOhjr5OpyZC
JWT_SECRET=change-me-to-a-long-random-string
WS_PORT=3002
DEVICES_STORE=.astro-devices.json

# 兼容老路径（不配 OPENCODE_BASE_URL = 离线 fallback）
# OPENCODE_BASE_URL=
# OPENCODE_API_KEY=
```

### backend/.env (charming-reader)

```env
DATABASE_URL=postgresql://jensenlyz:1014@db:35432/paperagent
ELASTICSEARCH_URL=http://es:9200
MINIO_ENDPOINT=minio:9000
QWEN_API_KEY=sk-your-key

# hermetic-agent 接入（不启用就注释掉，RAG 链路继续使用 LiteLLM）
# HERMETIC_BASE_URL=http://localhost:8000
# HERMETIC_SCENARIO=paper_agent
# HERMETIC_TENANT_ID=paper
# HERMETIC_TIMEOUT_MS=90000
```

启用 hermetic 只要取消注释 4 行；不启用时 charming-reader 行为完全不变。

---

## 3. 串通 smoke test（按顺序跑，每步都要看到预期输出）

### 3.1 hermetic-agent 就绪

```bash
curl http://localhost:8000/ready
# 预期: 200, {"storage": "ok", ...}

curl -s -X POST http://localhost:8000/agent/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-Scenario: astronomy_agent" \
  -H "X-Tenant-ID: astro" \
  -d '{"message":"猎户座参宿四现在是什么状态？","session_id":"smoke-test-1"}'
# 预期: SSE 流, 事件序列 scenario → session → text → done
```

### 3.2 AstroAgent 前端

1. 浏览器打开 `http://localhost:5173/`
2. 发消息 "猎户座参宿四现在是什么状态？"
3. 看前端 messages[] 里有 **流式 token** 出现

```bash
# 或直接 curl 后端
curl -s -X POST http://localhost:3001/api/chat \
  -H "Content-Type: application/json" \
  -d '{"sessionId":"x","scenario":"neo","text":"参宿四","history":[]}'
# 预期: SSE 流, event: token 行陆续出现
```

### 3.3 charming-reader hermetic 新端点

```bash
curl -s -X POST http://localhost:18000/chat/hermetic \
  -H "Content-Type: application/json" \
  -d '{"message":"什么是 Transformer 架构？","session_id":"paper-smoke-1"}'
# 预期: SSE, event: agent_thinking → 多个 event: agent_content → event: agent_complete
```

```bash
# 验证旧 RAG 链路未受影响
curl -s -X POST http://localhost:18000/agents/paper_agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"你好","session_id":"paper-rag-test"}'
# 预期: 正常返回 JSON response (非 SSE), 不动
```

### 3.4 失败回路（把 hermetic-agent 关掉再测）

```bash
# 停掉 hermetic-agent 进程后重跑 3.1 的 curl
# AstroAgent:
#   → 看到 event: error, message 含 "hermetic fetch" / "hermetic 连接失败"
#   → 处理: 在 astro-api/.env.local 注释 HERMETIC_BASE_URL，自动回退老路径
# charming-reader:
#   → 看到 event: error, code=HERMETIC_NOT_CONFIGUED
#   → 处理: 不配 HERMETIC_BASE_URL 即可
```

---

## 4. 各仓库落点速查

| 仓库 | 文件 | 改动 |
|---|---|---|
| **hermetic-agent** | `work/scenarios/astronomy_agent.scenario.yaml` | 新增 |
| | `work/scenarios/paper_agent.scenario.yaml` | 新增 |
| | `docs/superpowers/specs/2026-07-08-*.md` | spec doc |
| **AstroAgent** | `astro-api/lib/agent/hermetic.ts` | **新增文件** |
| | `astro-api/lib/agent/opencode.ts` | 加 hermetic dispatch 分支 |
| | `astro-api/.env.example` | 新增 |
| | `README.md` · "环境变量" 段 | 更新 |
| **charming-reader** | `backend/app/services/hermetic_client.py` | **新增文件** |
| | `backend/app/api/endpoints.py` | 加 `_chat_hermetic_stream` + `POST /chat/hermetic` |
| | `backend/app/core/config.py` | 加 4 项 HERMETIC_* 配置 |
| | `backend/.env.example` | 新增 |

## 5. 调试指北

- **AstroAgent 走 hermetic 不生效**：看 astro-api 控制台是否打印 `hermetic fetch:` 错误；确认 `HERMETIC_BASE_URL` 是 Next.js server-side env（不是 `VITE_` 前缀）。
- **charming-reader `/chat/hermetic` 报 `HERMETIC_NOT_CONFIGURED`**：`.env` 里 `HERMETIC_BASE_URL` 没生效 — 检查 pydantic-settings 的 `env_file = ".env"` 是否在 CWD 找对了 file；用 `python -c "from core.config import settings; print(settings.HERMETIC_BASE_URL)"` 验证。
- **新 scenario 不加载**：在工作目录跑 `from hermetic_agent.scenarios import ScenarioRegistry; ...` 看 list_names() 是否命中。
- **SSE 流断/乱码**：确认 nginx/proxy 关了 `proxy_buffering`（Stream 场景必须）。

## 6. 后续 (out of scope，先不做)

- API key 鉴权中间件
- per-tenant quota / 限流
- hermetic-agent SDK pip 包
- 把 charming-reader RAG 也切到 hermetic（当前 ADK+LiteLLM 链路保留）
- AstroAgent 把本地工具 (sky/neo/paper) 声明到 hermetic-agent scenario 的工具白名单里

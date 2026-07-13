# Pi Sidecar + Runtime 硬化 — 可执行施工图

> **状态：completed（2026-07-12，Task 1–3）。**
> **白话目标**：教练别每次聊天都重新「开机」；依赖面收紧。
> **不做**：Desktop 安装包、完整云代理账单、删掉 Python fallback。

## Sidecar 是什么（给产品/点点）

**Sidecar = 挂在主服务旁边的小助手进程。**

- 主服务：Python FastAPI（上传、分析队列、存聊天）
- Sidecar：长期开着的 Node 进程，里面已经加载好 Pi 教练引擎
- 聊天时：主服务 **喊一声旁边的助手**，而不是每次重新启动 Node + 加载 Pi

现在：`subprocess` 每轮冷启动 → 慢、脆。
目标：助手常驻，HTTP/stdio 长连接二选一；本 plan 冻结为 **本机 loopback HTTP**（更好测、更好 health）。

## Frozen

1. Sidecar 监听 `127.0.0.1` only，默认端口 `COACH_SIDECAR_PORT=8765`。
2. 合同仍 `coach_runtime_turn.v0`（与现 run-turn 兼容）；路径 `POST /v0/turn`。
3. Python `run_pi_coach_turn`：优先 HTTP sidecar；失败且 `COACH_SIDECAR_REQUIRED=0`（默认）时可回退 **一次性 subprocess**（过渡），再不行走现有 Python coach fallback。
4. 启动脚本：`scripts/run-coach-sidecar.sh` 或 `webapp/coach-runtime/start-sidecar.ts`。
5. Runtime import 只允许从 `packages/ai` + `packages/agent` 解析；测试断言不 import coding-agent。
6. 不在本 plan 物理删除 third_party 里 coding-agent 目录（另议）；只 **禁止产品路径依赖**。

## Tasks

### Task 1 — Sidecar HTTP server + client

**Allowed:** `webapp/coach-runtime/**`, `webapp/backend/coach_runtime.py`, `webapp/backend/config.py`, `webapp/tests/test_coach_runtime.py`, 可选 `webapp/coach-runtime/test/*`

**实现:**
- Node：`start-sidecar.ts` 起 HTTP server，`POST /v0/turn` body=turn request，返回 turn response；`GET /healthz` → 200。
- Python：`run_pi_coach_turn` 先 `httpx`/`urllib` 打 sidecar；timeout 沿用配置。
- 配置：`COACH_SIDECAR_URL` 默认 `http://127.0.0.1:8765`。

**Tests:** mock HTTP 成功/失败；health；现有 mock subprocess 测改为 mock HTTP 或保留 subprocess 回退测。

**Verify:** `pytest webapp/tests/test_coach_runtime.py -q` + node test if any。

### Task 2 — 启动说明 + import 边界测

**Allowed:** `webapp/coach-runtime/**`, `docs/PROGRESS.md`（一句）, `scripts/run-coach-sidecar.sh`（新建）

**实现:** sidecar 启动脚本；测试：从 coach-runtime 入口静态/动态加载路径不得出现 `coding-agent`。

**Verify:** 脚本 `--help` 或 dry-run；相关 test 绿。

### Task 3 — 回归

**Allowed:** 失败最小修 + `docs/PROGRESS.md` + plans README 勾选

**Verify:** `pytest -q`；`tsc --noEmit`

---

完成定义：有 key 时起 sidecar + API，`/coach` 一轮不冷启动 node（文档记录手工步骤即可）。

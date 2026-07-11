# Pi Coach Runtime 接入 — 可执行施工图（线 B 薄切片）

> **状态：active。点点 2026-07-12 授权开干；可改系统提示词与产品化裁剪。**  
> **目标**：`/coach` 对话默认走 **Pi Agent loop**，对话权威仍在 SQLite `coach_*`；旧 Python `chat_with_coach` 仅作 fallback。  
> **上游**：assessment CONDITIONAL GO、`docs/superpowers/assessments/2026-07-11-pi-assessment-decision.md`、Spike `spikes/pi-coach-runtime/`、线 A 已完成数据归属。  
> **明确不做（本 plan）**：Desktop 打包、可信 SSO、文件 TTL/quota、browser E2E gate、多 thread、Pi 跟随上游升级。

## 0. Executor 口令

每次只做一个 Task。开工回显 Task / Allowed files / Tests first / Frozen / Stop rule。  
不得 commit/push 除非点点要求。不得重写 agent loop。不得把 coding shell/file tools 暴露给教练。

---

## 1. Goal（用户可感知）

```text
用户在 /coach 发消息
  → FastAPI 写入 coach_messages(user)
  → 启动/调用 Node Coach Runtime（Pi）
  → Pi Agent + Aiming Cookie 系统提示词 +（可选）分析摘要工具/上下文
  → 经 OpenAI-compatible LLM（现有 key/env）
  → 返回 assistant 文本
  → FastAPI 写入 coach_messages(assistant)
```

成功标准：

1. `COACH_RUNTIME=pi` 时，primary messages **不调用** `chat_with_coach`；
2. 回复来自真实 `@earendil-works/pi-agent-core` `Agent`（从 `third_party/pi` 加载）；
3. 系统提示词为 Aiming Cookie 教练文案，可改、不在 Pi 上游硬编码 coding prompt；
4. 无 bash/read/write/edit 等 coding tools；
5. `COACH_RUNTIME=python` 仍可回退旧路径；
6. 删分析不丢消息（线 A 不回归）。

---

## 2. Frozen decisions

### 2.1 源码

| 项 | 冻结值 |
|---|---|
| 来源 | `https://github.com/earendil-works/pi.git` |
| commit | `3ea064ea2a0f01965923ce32e1bd17466c502b23` |
| 落盘 | 仓库内 **`third_party/pi/`**（完整树；可审计） |
| 不提交 | `node_modules`、构建缓存 |
| 运行时只用 | `packages/ai` + `packages/agent` |
| 默认删除/禁用 | coding tools、TUI、CLI main、project trust、任意 extension 加载、orchestrator 进 runtime |

本地已有 checkout 时可用 `/tmp/aiming-cookie-pi-assessment` 作拷贝源，但 **identity 必须与 commit 一致**；不一致则停止。

### 2.2 进程边界

- 新增产品目录：`webapp/coach-runtime/`（Node/TS，**不是** spike 目录）。
- Python FastAPI **每轮对话** `subprocess` 调用：

  ```bash
  node --import <tsx-or-native> webapp/coach-runtime/dist-or-src/run-turn.js
  ```

  stdin/stdout：**一行请求 JSON + 一行结果 JSON**（或 NDJSON 进度可选；本 plan 第一刀可只要最终 reply）。
- **不**在本 plan 做长期 daemon（可后置）。
- Pi JSONL 若落盘：仅 `webapp` 运行时 temp 目录，**不是** canonical 对话；canonical 仍 `coach_store`。

### 2.3 请求/响应合同（产品 v0，非 Spike 假协议名）

Request `coach_runtime_turn.v0`：

```json
{
  "schema_version": "coach_runtime_turn.v0",
  "run_id": "uuid",
  "user_id": "dev",
  "messages": [{"role": "user|assistant|system", "content": "..."}],
  "analysis_summary": null,
  "system_prompt": "……Aiming Cookie 教练……",
  "model": {
    "base_url": "https://api.deepseek.com",
    "api_key_env": "DEEPSEEK_API_KEY",
    "model_id": "deepseek-chat"
  }
}
```

- `analysis_summary`：由 Python 从 done session 的 deterministic 诊断 **序列化成短文本/JSON 字符串** 注入；Node **不直连 SQLite**。
- `system_prompt`：Python 或 runtime 默认文件提供；**允许产品改**，不得使用 Pi coding-agent 默认 system prompt。

Response：

```json
{
  "schema_version": "coach_runtime_turn.v0",
  "ok": true,
  "reply": "助手最终文本",
  "error": null,
  "notes": []
}
```

失败：`ok: false`，`error: { category, code, message, retryable }` 形状对齐现有 error 习惯即可。

### 2.4 系统提示词（产品所有，可改）

默认要点（实现落在 `webapp/coach-runtime/prompts/coach-system.md` 或 `.ts` 常量）：

- 你是 Aiming Cookie 瞄准教练，说中文，简洁、可执行；
- **deterministic 诊断/指标是事实源**，不得编造数字或改写诊断结论；
- 没有分析上下文时承认没有指标，给通用训练建议；
- 不执行代码、不读用户磁盘、不给 shell 命令当工具；
- 处方可引用社区经验，但不得伪装成传感器测量。

### 2.5 Tools

本切片 **最多 1 个只读 tool**：`get_analysis_summary`  
- 实现：返回本轮 request 里已带的 `analysis_summary`（或“无分析”）；  
- **禁止** Node 读视频/CSV/DB。  
若首轮用 system 消息直接塞摘要也能通过测试，tool 可仍注册以证明 Pi tool path，但不得侧写文件系统。

### 2.6 LLM

- `streamFn`：OpenAI-compatible **streaming** chat/completions（或 Pi 已支持的等价 API）。
- Key **只从环境变量读**，不进仓库、不进日志。
- 无 key / 调用失败：返回 ok:false；Python 层记 notes，**可**按 flag 回退 python（Task 4 写清：默认 pi 失败是否 fallback——冻结为 **`COACH_RUNTIME_FALLBACK_PYTHON=1` 默认开启** 预览期）。

### 2.7 配置

| env | 含义 | 默认 |
|---|---|---|
| `COACH_RUNTIME` | `pi` \| `python` | `pi`（测无 key 时可 python） |
| `COACH_RUNTIME_FALLBACK_PYTHON` | pi 失败是否回退 | `1` |
| `PI_SOURCE_DIR` | 可选覆盖；默认仓库 `third_party/pi` | `third_party/pi` |
| 现有 LLM env | DeepSeek 等 | 与现网一致 |

### 2.8 许可

- 保留 `third_party/pi/LICENSE`；
- 根或 `third_party/pi/PROVENANCE.md` 记录 URL+commit+日期；
- 本 plan **不做**完整 SBOM/法务结论；只做技术 provenance。

---

## 3. Tasks

# Task 1 — Vendor `third_party/pi` + provenance

## Goal

把冻结 commit 的 Pi 源码落入 `third_party/pi/`，可审计，不含 node_modules。

## Allowed files

- `third_party/pi/**`（源码树）
- `third_party/pi/PROVENANCE.md`（或 `third_party/README.md`）
- `.gitignore`（仅当需忽略 `third_party/pi/**/node_modules` 等）

## Tests first / evidence

```bash
test -d third_party/pi/.git || test -f third_party/pi/package.json
# 若保留 .git：rev-parse 必须等于冻结 commit
# 若不保留 .git：PROVENANCE 必须写死 commit 且用源文件校验（package version 0.80.6）
node -e 'const p=require("./third_party/pi/packages/agent/package.json"); console.log(p.name,p.version)'
# expect @earendil-works/pi-agent-core 0.80.6
ls third_party/pi/packages/ai third_party/pi/packages/agent
test ! -d third_party/pi/node_modules || (echo 'do not commit node_modules' && exit 1)
```

## Steps

1. 从已验证 checkout 或 shallow clone 拷贝到 `third_party/pi`（优先 rsync 源码，**不要**带 node_modules）。
2. 写 PROVENANCE.md。
3. 更新 `.gitignore`。
4. **不要**在 Task 1 改 webapp 业务。

## Acceptance

- commit identity 正确；agent package 在树内；无 node_modules；报告 status 停止。

## Stop

- checkout commit 不符且无法在不改冻结值下修复。

---

# Task 2 — `webapp/coach-runtime` 单轮 Pi turn

## Depends on

Task 1。

## Allowed files

- `webapp/coach-runtime/**`（新建）
- 可参考但 **不要修改** `spikes/pi-coach-runtime/**`（只读抄思路）

## Tests first

在 `webapp/coach-runtime` 用 Node test（tsx loader 指向 third_party 已装依赖 **或** 在 coach-runtime 装最小 devDep——优先：在 `third_party/pi` 本地 `npm install` **仅开发机**，不提交 lock 外的脏 node_modules；测试用 `PI_SOURCE_DIR=third_party/pi`）。

最少测试：

1. 加载真实 `Agent` from third_party（identity check）。
2. fake/local streamFn 驱动一轮，返回非空 reply（可 mock fetch）。
3. system prompt 来自产品文件，断言 coding-agent 默认 prompt 字符串不出现。
4. tools 列表不含 bash/read/write。

## Implementation

- `run-turn.ts`：读 stdin JSON → Agent.prompt → stdout JSON。
- `prompts/coach-system.md` + 加载器。
- `stream-openai-compatible.ts`：真流式 + 可注入 mock。
- `package.json`：私有包名 `@aiming-cookie/coach-runtime`，Node `>=22`。

## Verification

```bash
# 安装仅限 third_party/pi 或 coach-runtime 本地，不提交 node_modules
cd /Users/clickist/Projects/Aiming-cookie
PI_SOURCE_DIR=$PWD/third_party/pi node --import ... --test webapp/coach-runtime/test/*.test.ts
```

## Stop

- 需要改 FastAPI / schema / frontend → 停。
- 启用 coding tools → 停。

---

# Task 3 — Python subprocess 客户端

## Depends on

Task 2。

## Allowed files

- `webapp/backend/coach_runtime.py`（新建）
- `webapp/backend/config.py`（仅 COACH_RUNTIME* 与路径配置）
- `webapp/tests/test_coach_runtime.py`（新建）

## Tests first

1. mock subprocess：pi 路径返回 ok reply。
2. subprocess 失败 / ok:false → 抛错或结构化错误。
3. 超时可配置（默认 120s）。

## Implementation

- `run_pi_coach_turn(messages, analysis_summary, system_prompt=None) -> str`
- 组装 `coach_runtime_turn.v0`，调 node run-turn。
- 从 diagnosis 对象生成 **analysis_summary 短文本** 的 helper（只读字段，不改诊断算法）。

## Verification

```bash
source .venv/bin/activate
python -m pytest webapp/tests/test_coach_runtime.py -q
```

---

# Task 4 — 接通 `POST /api/coach/primary/messages`（+ session chat 兼容）

## Depends on

Task 3。

## Allowed files

- `webapp/backend/routes.py`（`_execute_coach_chat_turn` 及必要 helper）
- `webapp/tests/test_routes_coach.py`
- `webapp/tests/test_routes_chat.py`
- `webapp/backend/config.py`（若 Task3 未完成 flag）

## Tests first

1. `COACH_RUNTIME=python`：行为与现网一致（可 mock chat_with_coach）。
2. `COACH_RUNTIME=pi`：mock `run_pi_coach_turn` 被调用，`chat_with_coach` 不被调用。
3. pi 失败且 fallback=1：回退 python。
4. 消息仍写入 coach_messages。

## Implementation

- 在 `_execute_coach_chat_turn` 分支 runtime。
- 构建 messages 列表给 Pi（含历史）。
- analysis_summary 来自 diagnosis。

## Verification

```bash
python -m pytest webapp/tests/test_routes_coach.py webapp/tests/test_routes_chat.py webapp/tests/test_coach_runtime.py -q
python -m pytest webapp/tests -q
```

## Stop

- 改前端大面积 / 改删除语义 / vendor 范围外文件。

---

# Task 5 — 回归 + 文档收口

## Depends on

Task 4。

## Allowed files

- `docs/PROGRESS.md`
- `docs/ROADMAP.md`（仅状态）
- `docs/superpowers/plans/README.md`
- `docs/superpowers/assessments/2026-07-11-pi-assessment-decision.md`（补「线 B 已开工/切片完成」一句）
- 失败则最小修测试/接线

## Verification

```bash
python -m pytest -q
cd webapp/frontend && npx tsc --noEmit
```

可选：有 key 时手工 `/coach` 一轮（记在 PROGRESS，无 key 不阻塞）。

---

## 4. 成功后的产品状态（白话）

教练大脑换成 Pi；提示词我们自己管；分析数据仍是我们的；旧引擎可回退。  
还没做：完整云代理账单、Desktop 沙箱、浏览器 E2E。

## 5. 与点点确认的自由裁量（已授权）

- 可改系统提示词与摘要注入方式（tool vs system 消息），以「能测绿 + 不丢产品铁律」为准。
- 可在 `third_party/pi` 内做 **最小** 产品补丁（必须写进 PROVENANCE patch 列表）；优先不改上游、只在 `webapp/coach-runtime` 适配。

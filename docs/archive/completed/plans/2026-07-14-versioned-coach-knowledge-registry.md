# Versioned Coach Knowledge Registry — Implementation Plan

> **状态：completed（2026-07-14）。** Task 1–6 已完成；正式前端、Coach productization Task 6、提交和推送均未进入本计划范围。
>
> **完成证据：** canonical Registry、Python/TypeScript parity、Pi bounded retrieval、bridge 解耦、安全 trace 与真实 Analysis → Knowledge → SQLite E2E 均已通过；最终验证记录见 [`../../../PROGRESS.md`](../../../PROGRESS.md)。
>
> **Design contract:** [`../../../superpowers/specs/2026-07-14-versioned-coach-knowledge-registry-design.md`](../../../superpowers/specs/2026-07-14-versioned-coach-knowledge-registry-design.md)

## Task 1 — Registry contract and migration inventory

### Allowed files

- active Knowledge Registry spec/plan；
- `docs/ARCHITECTURE.md` 的 Knowledge 数据归属与依赖边界；
- specs/plans indexes；
- `docs/ROADMAP.md`、`docs/PROGRESS.md` 仅链接/摘要。

### Tests first / Gate

- 单一事实源、entry/version、source/claim/limitation、signal alias、历史引用与确定性检索顺序明确；
- 冻结 37 Python chunk + 19 legacy signal + 11 TS seed 的逐项 migration audit；
- 明确无 system-prompt knowledge、无 Markdown runtime read、无 vector DB、无任意 filesystem tool。

### Stop rule

- 上游产品范围冲突或需要前端/在线知识服务。

## Task 2 — Canonical assets, Python loader and validator

### Allowed files

- `knowledge/coach/schema.v1.json` (new)
- `knowledge/coach/registry.v1.json` (new, 可先放最小 fixture)
- `knowledge/coach/migration-audit.v1.json` (new)
- `kovaak_tracker/coach/knowledge_registry.py` (new)
- `tests/coach/test_knowledge_registry.py` (new)

### Tests first / Gate

- schema/version/size/duplicate/status/source-claim/limitation/alias/path-secret-raw-payload fail closed；
- stable refs、query scoring、最多 3 条与零命中无全库 fallback；
- body/tension/settings experimental matrix。

### Stop rule

- 需要新增依赖或 SQLite 正文双写。

## Task 3 — Full migration: Flicking, Tracking, body/tension, settings and verification

### Allowed files

- `knowledge/coach/registry.v1.json`
- `knowledge/coach/migration-audit.v1.json`
- focused Registry tests

### Tests first / Gate

- 37 + 19 + 11 audit coverage exact；
- 19 legacy signals 全覆盖；Tracking、身体/张力、settings、practice、verification 均有 active entry；
- 旧阈值、固定 sensitivity、身体根因和训练剂量过强措辞 rewrite/experimental/reject；
- 所有非 reject audit target 存在。

### Stop rule

- 来源无法定位、claim 无法诚实降级或需要医疗建议。

## Task 4 — Python compatibility adapters and single runtime truth

### Allowed files

- `kovaak_tracker/coach/knowledge.py`
- `kovaak_tracker/coach/agent_kb.py`
- `kovaak_tracker/coach/agent_tools.py`
- `kovaak_tracker/coach/agent.py` 仅 tool 指引需要调整时
- `webapp/backend/coach_context.py` 仅 source-level contract
- focused Python Coach tests

### Tests first / Gate

- legacy tool names/response shape compatibility；
- `knowledge.py` / `agent_kb.py` 只从 Registry 生成 adapter，无独立正文；
- source-specific/topic/signal 查询改走同一 deterministic retrieval；
- 现有 diagnosis/progress/plan Coach 回归通过。

### Stop rule

- 需要改变 deterministic diagnosis 或 Training Plan lifecycle。

## Task 5 — TypeScript Registry loader, Pi tool and bridge decoupling

### Allowed files

- `webapp/coach-runtime/src/knowledge-registry.ts` (new)
- `webapp/coach-runtime/src/knowledge-tools.ts`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/test/**` focused

### Tests first / Gate

- TS validation 与 Python parity；
- v1 无 bridge registry 精确为 analysis + knowledge；有 bridge 再加 product command；v0 仅 legacy analysis；
- tool result bounded，event 保存 registry/entry/version/source/max-claim refs；
- TS 不保留第二份知识正文，不获得任意 fs/coding-agent tool。

### Stop rule

- Python/TS query 结果无法一致或打包路径只能靠任意目录扫描。

## Task 6 — Trace safety, real Analysis E2E and final verification

### Allowed files

- `webapp/backend/coach_runtime.py`
- `webapp/backend/coach_engine.py`
- `webapp/backend/coach_service.py`
- focused Coach trace/E2E tests
- `docs/PROGRESS.md`, `docs/ROADMAP.md`, active plan status

### Tests first / Gate

- Python safe-event validator 接受新版本 refs，拒绝正文/path/secret/raw payload；
- 真实 Analysis issue signal/metric → Registry retrieval → Pi tool result/event → persisted trace；
- full Python、Pi runtime、strict TS、migration/parity/sentinel、diff/link checks；
- 最终报告 changed files、验证、偏差、风险和 git status；停止，不进入前端。

### Stop rule

- E2E 需要伪造产品成功、扩大前端范围或跳过 trace redaction。

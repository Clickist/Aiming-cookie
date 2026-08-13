# Launch Family Production Activation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
>
> **状态：active / 点点于 2026-07-27 授权全速推进。** 每次仍只执行一个 Task；Task 完成并复核后再进入下一 Task。不提交、不推送，不修改 PRD / Architecture，不把私人 Raw、MP4、Stats 或 Performance 写入仓库。

**Goal:** 用已采集的真实 Static、Dynamic 和 Switching Run，为三个 exact scenario hash 分别建立可复现、严格 fail-closed 的 production activation Gate。

**Architecture:** 保持 exact-hash ScenarioProfile + launch manifest 为唯一生产开关。Static 复用已验证的 input-native analyzer；Dynamic 使用独立标注校准的单目标视觉 producer 与 one-shot outcome association；Switching 使用 `Stats` kill boundary 与 event-local target episode 组合 transition/acquire/settle/path，不建立整局 target identity，也不依赖 continuous-LG outcome association。三个 family 不共享未经验证的视觉阈值、身份模型或 outcome 规则。

**Tech Stack:** Python 3.11、pytest、OpenCV、本地 MP4/Raw/Stats/Performance、versioned JSON registries、FastAPI/SQLite worker、pinned Pi Coach runtime。

---

## 1. 冻结输入与决策

### 真实 field 输入

| Family | Run | Scenario | Exact hash | 已验证 source |
|---|---:|---|---|---|
| Static | `1030` | `1wall 6targets small` | `7378a811f430b6072d052a75896afb98` | Stats / Performance / Raw / MP4，60 秒，`Pause Count=0` |
| Dynamic | `1032` + `1347` | `pasu small reload` | `a37d2ba4f3f33d59ae7018e37445a5e9` | 两局四源完整、85.714 秒、零暂停；冻结 `1032=calibration`、`1347=untouched holdout` |
| Switching | `1036` + `1038` | `beanTS Larger` | `3b42bdfd38a6b194737d650f3f53e8c1` | 两局四源完整、60 秒、零暂停；冻结 `1036=calibration`、`1038=untouched holdout` |

`Run 1034` 的 `voxTargetSwitch Click` 只作为点击式转火边界样本，不替代连续 LG Switching Gate。历史 `Analysis 6 / 7 / 8` 保持冻结的 outcome-only 结果；激活后新建新的 Analysis，不改写历史 snapshot。

### 采用方案

1. **采用：逐 family exact activation。** 每个 hash 独立绑定 profile、fixture、review、quality 与 association rule。
2. **拒绝：按场景名或关键词分类。** 同名、改版和未知 hash 继续 outcome-only。
3. **拒绝：一个通用 CV producer 覆盖 Dynamic 与 Switching。** Dynamic 只需 click 附近目标几何；Switching 需要跨帧双目标 identity，失败模式不同。
4. **拒绝：把完整采集当作算法质量证明。** Source completeness 只证明输入可用，不证明 detector、identity 或 outcome association 正确。

### 私有数据边界

- 原始 MP4、Raw、Stats、Performance 和绝对路径只留在 field data root。
- 仓库只允许小型、可再分发的 synthetic fixture、量化 annotation summary、source digest、协议/version/ref 与不可逆聚合值。
- frame、video bytes、Raw points、原始 CSV/protobuf 不进入 Coach、Provider、普通 API、测试日志或 Git。

## Task 1 - Activate exact Static input-native scenario

> **实施状态：completed（2026-07-27）。** Exact hash `7378a811f430b6072d052a75896afb98` 已绑定 reviewed static profile、path-free redistributable fixture 与 active launch manifest。最终 field `Analysis 11` 从 Run 1030 生成 `native_flicking.v1`，123/123 per-flick rows complete、0 target-relative metric；结果因 `alignment_partial` 保持 `support_status=partial`，不掩盖 0.999866 coverage。两条 Static profile dimension 为 medium/comparable；Coach v3 context 为 30,640 bytes、保留 8 种表查询且不重复内联 sample refs。focused verification 为 `157 passed`，Coach context 为 `42 passed`；历史 Analysis 6 保持 outcome-only。

### Allowed files

- `knowledge/scenarios/registry.v1.json`
- `knowledge/scenarios/launch-manifest.v1.json`
- create `tests/fixtures/scenarios/1wall-6targets-small-static.v1.json`
- `tests/test_scenario_profiles.py`
- `tests/test_native_flicking_analysis.py` only if an exact packaged fixture assertion is needed
- `webapp/tests/test_worker.py`
- `webapp/tests/test_aiming_profile_store.py` only for exact supported contribution eligibility
- `webapp/backend/worker.py` only for the reviewed Static scenario result projection defect proven by field Analysis 9
- `webapp/backend/aiming_profile_store.py` only for one-to-one native Static metric-key projection into existing namespaced profile dimensions
- `webapp/backend/coach_context.py` only to deduplicate per-event refs from v3 metric summaries when the same rows remain queryable through ProcessedEventTable refs
- `webapp/tests/test_coach_context.py` only for fresh projection and serialized v3 re-coercion coverage
- `docs/PROGRESS.md`
- this plan for evidence/status only

### Tests first

1. Add a failing packaged-asset test: exact hash resolves `active`, `aim_family=static_clicking`, `allowed_analyzers=[native_flicking.v1]`, metric families `input_kinematics/static_clicking`.
2. Assert name-only, another `1wall 6targets small` hash, `pending_gate`, `retired` and unlisted profiles remain outcome-only.
3. Assert the exact v3 snapshot dispatches `native_flicking.v1`, emits complete per-flick ProcessedEventTable and no target error/overshoot/undershoot.
4. Assert supported deterministic metrics may create profile contribution; outcome-only results still may not.
5. Keep legacy frozen static compatibility unchanged.

### Minimal implementation

1. Add a reviewed Static profile with exact hash. Set `target_count_model=concurrent` only after field-frame review confirms simultaneous targets; otherwise stop and record `unknown` rather than guessing.
2. Add a small fixture containing taxonomy, source-contract versions, field-review refs and aggregate evidence only.
3. Add the matching `active` manifest entry after all focused tests pass.
4. Do not modify analyzer formulas, thresholds, worker dispatch or Coach knowledge unless a failing test proves a real integration defect; any such defect requires scope review first.

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
.\.venv\Scripts\python.exe -m pytest tests\test_scenario_profiles.py tests\test_native_flicking_analysis.py webapp\tests\test_worker.py webapp\tests\test_aiming_profile_store.py -q
```

Field verify: create a new input-native Analysis from `Run 1030`; require `native_flicking.v1`, supported static metrics, complete rows, no visual claims, Coach-safe evidence and eligible profile contribution.

### Stop rule

- Static regression changes an existing formula, threshold, SPARC version or segmentation.
- Activation requires target-relative facts from Raw or unreviewed video.
- Any unknown hash gains a family claim.

## Task 2 - Calibrate and activate exact Dynamic scenario

> **实施状态：completed（2026-07-27，partial evidence ceiling）。** Exact hash `a37d2ba4f3f33d59ae7018e37445a5e9` 已绑定 reviewed round detector、独立 calibration/holdout annotation ledger、accepted quality profile 与 active manifest。Calibration 为 `133/133`、coverage `1.0`、FPR `0`；untouched holdout 为 `124/125`、coverage `0.992`、FPR `0`，唯一真实 miss 为 `99px²`、低于冻结 `min_area=100`。保守 envelope 为 center median `1.032295px`、P95 `3.519083px`、radius error `0.749257px`；identity/re-entry 继续 unavailable。
>
> 最终 field `Analysis 16 / 17` 分别从 Run `1032 / 1347` 生成 `dynamic_clicking.v1`，worker wall time `111s / 137s`，ProcessedEventTable `107/107` 与 `106/106` complete，artifact `3,334,224 / 3,319,603` bytes，quality `accepted`、support `partial`。每条均有 1 个可播放 EvidenceSegment、128 个 bounded marker、Coach v3 table ref 与 8 种查询能力；path / CSV / Performance / protobuf / secret/token sentinel 为 `0`。normalized click error coverage 仅 `9.35% / 12.26%`，因此贡献保留为 low/not-comparable、History 明确 `insufficient_metric_coverage`、Coach 不生成无可比依据的 knowledge candidate。one-shot outcome rule 未注册，acquisition、relative velocity 与 target-state outcome 保持 unavailable；这不是用 prompt 或 UI 隐藏失败。
>
> Tests-first 修复了 Dynamic 顶层 scenario 投影、跨 Analysis condition taxonomy 与 versioned visual profile ref 的 History 解析。最终 Task 2 focused 为 `289 passed, 2 skipped`，compileall 与 scoped `git diff --check` 通过。

### Prerequisite

Two complete Runs with the same exact hash/profile/condition are required: one calibration Run and one untouched holdout Run. 当前已冻结 `Run 1032 = calibration`、`Run 1347 = untouched holdout`；两者均为 `pasu small reload`、exact hash `a37d2ba4f3f33d59ae7018e37445a5e9`、四源完整、85.714 秒、零暂停。`Run 1040` 是不同场景 `1wall5targets_pasu`，不得混入该 split。

### Allowed files

- `knowledge/scenarios/registry.v1.json`
- `knowledge/scenarios/launch-manifest.v1.json`
- `knowledge/scenarios/outcome-association-rules.v1.json`
- `kovaak_tracker/visual_signals.py` only for a versioned reviewed-profile/selector adapter or a defect proven by annotation
- `kovaak_tracker/outcome_association.py` only if the existing one-shot rule cannot represent the reviewed contract without weakening it
- `webapp/backend/worker.py` only for reviewed producer registration and exact dispatch
- `kovaak_tracker/dynamic_clicking_analysis.py` only for a proven adapter defect; metric formulas stay frozen
- create bounded fixtures under `tests/fixtures/visual_signals/` and `tests/fixtures/scenarios/`
- `tests/test_visual_signals.py`
- `tests/test_outcome_association.py`
- `tests/test_dynamic_clicking_analysis.py`
- `tests/test_scenario_profiles.py`
- `tests/coach/test_advice_dynamic_clicking.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_coach_context.py`
- `webapp/backend/history_trends.py` only for the explicitly authorized versioned visual-profile ref parser fix
- `webapp/tests/test_history_trends.py`
- `docs/PROGRESS.md`
- this plan for evidence/status only

### Tests first

1. Freeze calibration/holdout split before tuning; tests reject overlap and digest mismatch.
2. Annotation ledger validates independent reviewer, target ID, center/radius, HUD exclusion, visible/occluded/merged state and click-window coverage.
3. Exact selector binds scenario hash, decoded resolution and canonical video mapping version; another hash/profile cannot reuse it.
4. Measure center median/p95, radius error, false-positive rate and coverage on calibration and holdout. Identity/re-entry claims remain disabled unless those conditions were actually observed and annotated.
5. One-shot association requires the reviewed hitscan condition, exact source bindings, Raw click, stable target geometry and unambiguous timing; ambiguous/non-one-shot events remain unavailable.
6. Dynamic analyzer may expose click-relative geometry without outcome association, but target-conditioned accuracy/outcome claims require a valid association.
7. Active manifest dispatches only after producer/profile/rule/analyzer/knowledge tests pass; unlisted/pending/mismatched inputs stay outcome-only.

### Minimal implementation

1. Run the existing round detector against both field Runs without changing thresholds; collect annotated errors first.
2. If existing detector passes both sets, register a new exact reviewed quality profile. If it misses, tune only against calibration, then rerun untouched holdout; do not relax the quality schema.
3. Register a scenario-specific one-shot outcome rule only for conditions demonstrated by Stats/Raw/visual annotations.
4. Add profile and manifest entry last, after all focused and field Gate checks pass.
5. Run new multimodal Analyses for both Dynamic Runs; require real `dynamic_clicking.v1`, complete rows, bounded EvidenceSegments, Coach knowledge refs and comparable exact-scenario history.

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
.\.venv\Scripts\python.exe -m pytest tests\test_visual_signals.py tests\test_outcome_association.py tests\test_dynamic_clicking_analysis.py tests\test_scenario_profiles.py tests\coach\test_advice_dynamic_clicking.py webapp\tests\test_worker.py webapp\tests\test_coach_context.py webapp\tests\test_history_trends.py -q
```

Record both Run wall times, quality metrics, association included/excluded counts, artifact size and limitations. No latency target is invented before the first measurement.

### Stop rule

- Only one complete exact-condition Run exists.
- Detector can only pass by using holdout for tuning, lowering quality thresholds or hiding misses/false positives.
- Cross-frame identity, occlusion/re-entry or predictive-lead is claimed without corresponding annotation evidence.
- Outcome association relies on nearest target, `.perf` aggregate or scenario name.

## Task 3 - Build and activate exact event-local Switching episode path

> **Field split frozen（2026-07-27）：** `Run 1036 = calibration`、`Run 1038 = untouched holdout`。两者 exact hash 均为 `3b42bdfd38a6b194737d650f3f53e8c1`，Stats / Performance / Raw / MP4 完整、resolved/finalized、60 秒、零暂停；冻结时 `1038` 尚无关联 Analysis。任何 source identity 或状态变化都会使该 split 失效。
>
> **Scope decision（2026-07-28，点点裁决）：** Switching 的正式模型固定为 `Stats kill boundary -> event-local post-kill acquisition/tracking episode -> transition/acquire/settle`。`Stats` 确认“前一目标已经死亡”；视觉只证明随后准星对下一可观察目标的 acquire、settle 和路径，不需要、也不得推断刚死亡目标的 persistent identity。复用现有 detector、逐帧 observation、Tracking 几何与 Evidence primitive；不得新建或扩展全局 multi-target identity、persistent-ID、re-entry 或第二套通用 tracker。crossing、merge、disappearance 和 re-entry 只结束受影响的局部候选；重叠发生在击杀时不要求判定是哪一个目标死亡。continuous-LG hold 与离散点击都只作输入 provenance，不定义 family；不得合成 shot、first-damage 或 selection。没有可验证的 `Stats` kill 时不得产生正式 Switching row；单个击杀后的视觉歧义只排除该 kill chain。整局 persistent identity、identity-switch rate、跨遮挡恢复和 re-entry accuracy 永久移出本 Task Gate。
>
> **实施状态：completed（2026-07-28，partial evidence ceiling）。** exact beanTS hash `3b42bdfd38a6b194737d650f3f53e8c1` 已绑定 accepted event-local producer、冻结 detector config ref 与 active manifest。`>50ms` observation gap 会切断 episode；merge/crossing 只结束空间上受影响的 episode，安全第三目标继续。Switching producer 已绕过整局 identity/re-entry 计算；continuous-LG association、备用 analyzer 与专属测试死路径已删除，第二波生产代码净删约 `680` 行、测试净删约 `827` 行。最终 focused suite 为 `307 passed, 1 skipped`。修复后新建 `Analysis 20 / 21`，分别从 `Run 1036 / 1038` 生成 `64/64` 与 `83/83` complete Stats-bounded chains，worker wall time 约 `73s / 77s`；五类公开事件分布与 chain 数一致，公开 EvidenceSegment `64/64` 可播放，managed MP4 range 为 HTTP `206`。两条 Coach context 均为 v3、各含 1 个 table ref 与 8 种查询能力；profile contribution 各写入 4 个 exact-scenario dimensions。History 因严格 evidence coverage 保持 `no_comparable_baseline`，不降低门槛。selection、first-shot、first-damage、persistent identity 与 re-entry 均未成为指标；公开 DTO 的 path/secret/token/traceback sentinel 为 `0`。旧 `Analysis 18 / 19` 保留为修复前诊断证据，不作为最终 Gate。

### Allowed files

- `knowledge/scenarios/registry.v1.json`
- `knowledge/scenarios/launch-manifest.v1.json`
- `knowledge/scenarios/outcome-association-rules.v1.json`
- `kovaak_tracker/visual_signals.py` only for a thin exact-scenario contact-episode adapter over existing detector/observation primitives
- `kovaak_tracker/outcome_association.py` only for optional versioned Stats outcome enrichment; it must not gate base transitions
- `kovaak_tracker/target_switching_analysis.py` for the thin episode composer; existing metric meanings stay frozen
- `webapp/backend/visual_worker_process.py`
- `webapp/backend/worker.py`
- `webapp/backend/contracts.py` only for new producer/rule version registration
- create bounded fixtures under `tests/fixtures/visual_signals/` and `tests/fixtures/scenarios/`
- `tests/test_visual_signals.py`
- `tests/test_outcome_association.py`
- `tests/test_target_switching_analysis.py`
- `tests/test_scenario_profiles.py`
- `tests/coach/test_advice_target_switching.py`
- `webapp/tests/test_visual_worker_process.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_history_trends.py`
- `docs/PROGRESS.md`
- this plan for evidence/status only

### Tests first

1. Existing target observations form independent contact/tracking episodes only inside continuous, unambiguous local visibility windows; detector order changes do not change a safe episode.
2. Crossing, merge, disappearance and re-entry end only the affected episode. Later observations start new episodes, never recover an old identity, and do not invalidate other safe episodes in the Run.
3. The composer creates one formal chain only from a parsed `Stats` kill boundary to the first following, locally verifiable acquisition episode. It does not need to identify the dead target. Ambiguous or incomplete post-kill evidence rejects only that kill chain; other safe chains remain available.
4. Transition/acquire/settle/path rows and metrics require the parsed `Stats` kill boundary. They begin at that boundary and never infer a previous visual target, selection, shot or first damage.
5. continuous-LG hold and discrete click provenance are both accepted without defining the Switching family. A held interval may span many kills, but no per-shot, first-damage or selection event is synthesized.
6. Merge, crossing, disappearance or re-entry at the kill boundary do not require a target-identity decision. Only ambiguity that prevents finding the next locally verifiable acquisition rejects the affected kill chain; nearest-target or post-hoc identity matching is forbidden.
7. Selection, first-shot, first-damage, persistent identity and re-entry remain unavailable in all paths.
8. Worker child supports `target_switching` postprocess and downgrades only missing or unsafe kill chains, or the whole Analysis to outcome-only when no safe chain remains, without losing native evidence.
9. Only the exact accepted observation/episode producer + profile + active manifest dispatches `target_switching.v1`. All hash/profile/producer mismatches fail closed. Complete ProcessedEventTable, EvidenceSegments, Coach refs and profile contribution use only Stats-bounded observable rows.

### Annotation protocol

1. Freeze source digests, canonical window, selector and detector config.
2. Use only `Run 1036` to freeze versioned post-kill acceptance criteria: kill timestamp alignment, minimum next-target observation/local duration, local uniqueness and acquire/settle evidence. Do not preselect or retune those values on holdout.
3. Annotate each `Stats` kill candidate: kill boundary, first following local acquire, settle, path observability and every rejection reason. A merge at the kill boundary is not an identity annotation failure. `selection`, `first-shot`, `first_damage`, persistent identity and re-entry default unavailable.
4. Run the frozen criteria unchanged on untouched `Run 1038`. Independent review reports Stats-kill chains included and rejected counts, coverage, ambiguity reasons and false positives. Unpassed post-kill acquire/settle quality disables Switching activation.

### Minimal implementation

1. Reuse the existing detector and observation primitives through a thin exact beanTS adapter that emits event-local post-kill acquisition/tracking episodes plus explicit local boundaries. Remove or simplify Task 3 code that exists only for global persistent identity.
2. Make `target_switching_analysis` a thin kill-boundary composer: for each `Stats` kill, find the first safe subsequent episode and compute kill-to-acquire transition/path/settle/correction. It must not infer player intent, a dead-target identity or long-lived identity.
3. Keep continuous-LG/discrete input only as provenance. Remove the whole-Run `identity_result.status == accepted` requirement and continuous-LG outcome-association production dependency; each formal row is bounded directly by a parsed Stats kill.
4. Keep the isolated visual child and worker integration only as needed for exact episode production; do not duplicate a general tracker or change capture/native receipts.
5. Validate the frozen episode pipeline on untouched `Run 1038`; register the exact active manifest entry last.
6. Run new multimodal Analyses from `Run 1036` and `Run 1038`; require `target_switching.v1`, Stats-bounded observable transition rows, honest unavailable fields, Coach query/knowledge refs and profile contribution.
7. Report Task 3-owned lines added and removed and identify deleted global-identity-only code; do not count or revert unrelated dirty work.

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
.\.venv\Scripts\python.exe -m pytest tests\test_visual_signals.py tests\test_outcome_association.py tests\test_target_switching_analysis.py tests\test_scenario_profiles.py tests\coach\test_advice_target_switching.py webapp\tests\test_visual_worker_process.py webapp\tests\test_worker.py webapp\tests\test_coach_context.py webapp\tests\test_history_trends.py -q
```

### Stop rule

- Versioned post-kill acceptance criteria were not frozen on calibration, or the frozen criteria cannot distinguish useful and ambiguous kill chains on untouched holdout.
- Any crossing, merge, disappearance or re-entry continues an old identity; an ambiguous/incomplete post-kill window suppresses unrelated safe rows; or an unsafe local episode still emits a transition.
- A formal transition row lacks a parsed Stats kill boundary, is bound to a guessed dead target, or relies on nearest-target matching, a `.perf` aggregate or scenario name.
- Analysis or Coach claims selection, first-shot, first-damage, persistent identity or re-entry; or a new producer would require changing capture/native receipt or exposing media to Coach/Provider.

## Task 4 - Integrated family, Coach and release Gate

> **实施状态：in progress；full-suite 与 Coach 对话循环 blocker 已于 2026-07-29 清除，完整 knowledge/plan field condition 仍按证据 fail-closed。** 复用现有 Run 并通过正式 `/analyze` API 串行新建 `Analysis 22 / 23 / 24 / 25`；四条均 1 次 attempt、`support=partial`、公开 DTO privacy sentinel 通过。PowerShell 只监控既有 worker 进程树，不增加产品 telemetry：Static 为 `14.097s / 13.250 CPU-s / 189.1 MiB peak WS / 3,085,034 bytes / 123 rows`，Dynamic 为 `85.938s / 394.516 CPU-s / 373.1 MiB / 3,319,603 bytes / 106 rows`，Tracking 为 `127.611s / 533.703 CPU-s / 393.2 MiB / 869,250 bytes / 382 aggregate rows`，Switching 为 `75.123s / 299.172 CPU-s / 380.2 MiB / 2,925,955 bytes / 83 rows`。这些是同一 Run 的串行 warm-cache replay；Tracking 单次低于 `130s` 不覆盖此前 `148.039s` 三次中位数，稳定性能 Gate 仍未通过。四条均可只读投影为 `coach_diagnostic_context.v3`，大小约 `30.1 / 10.6 / 22.8 / 9.7 KB`，各有 8 种查询能力。没有 grounded issue 或 active Training Plan 的 family 仍不得伪造 knowledge/plan refs；该证据边界不是对话死循环回归。

2026-07-28 Coach field 探针使用四份 field DB 副本、同一个已保存且公开状态为 `ready` 的 DeepSeek profile 与正式 Pi sidecar，未改写原 field DB。Dynamic `Analysis 23`、Tracking `24`、Switching `25` 各完成一条真实 Agent run，并诚实返回“当前没有明确问题”的 intake；三条均无 knowledge、plan、confirmation、目标身份或选靶推断，但现有 planner 不能把用户随后说明的错误转成 grounded candidate，因此该路径无法自然继续。Static `Analysis 22` 有 observation、primary candidate 与 alternative，本应在 discriminator 后进入 hypothesize；连续三条真实 Agent run 的 Provider draft 均被 Teaching validator 降级为同一个本地安全 fallback，TeachingSession 保持 `intake / version 1`、无 tool event，未进入 teach/knowledge/plan。两条路径都正确阻止了无依据输出和副作用，却形成不可继续的产品可用性缺陷，不满足本 Task 的完整实链 Gate。定向现有自动化另通过 `442 passed, 1 skipped`，覆盖四 family dispatch、outcome-only controls、privacy/owner/idempotency、retry/deletion/restart、artifact revision/checksum 与 History comparability。完整 Python 的 stop 测试再跑 10 次均稳定暴露测试在 cleanup 后索引私有 `_tasks`；当前不是生产 cleanup 缺陷，仍需在另一个授权范围修测试。

2026-07-29 follow-up 在新的四份 field DB 副本上复用同一 ready DeepSeek profile。Static `Analysis 22` 第一轮从 `intake` 推进到 `hypothesize / version 2`，第二轮回复不同且不再带原 discriminator 问句，也没有再次推进；两轮均无 tool/confirmation。Dynamic `23`、Tracking `24`、Switching `25` 均返回有限 no-lesson 回复并保持 `intake / version 0`，无问句、候选、处方、tool 或 confirmation。两条 tests-only 修复后 `test_task6_backend_contracts.py` 为 `18 passed`；完整 Coach Node 为 `156 passed`，完整隔离 Python 为 `1440 passed, 5 skipped`，compileall 与 whole-worktree `git diff --check` 通过。原 field DB、Provider credential、Run/media 均未改写；临时副本和子进程已清理。

### Allowed files

- focused tests already authorized above
- small redistributable fixtures already produced above
- `docs/PROGRESS.md`
- `docs/ROADMAP.md` only for Gate status
- `docs/DEVELOPMENT.md` only for stable replay commands
- `docs/superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md` only for Task 12 evidence
- `webapp/tests/test_task6_backend_contracts.py` only for the two tests-only v20/terminal-cleanup expectation repairs authorized on 2026-07-29
- this plan

### Tests first / field matrix

1. Static, Dynamic, Tracking and Switching each produce one new exact-hash production Analysis; manifest-external and movement-aiming controls remain outcome-only.
2. Source -> canonical window -> analyzer -> evidence -> Coach queries -> knowledge -> profile/plan refs is exercised for every activated family.
3. Coach checks supporting evidence and at least one counterexample where available; it does not invent target identity, selection, first damage or causes.
4. Raw/path/secret/video/frame/private payload sentinels remain absent from public DTO, tool trace, message, logs and Provider transcript.
5. Retry, deletion, restart, owner and idempotency paths remain correct.
6. Record latency, CPU, peak memory, artifact size, query/token budget and unavailable fields per family.
7. Run focused suites, full Python with nonexistent KovaaK path, full Coach Node suite and `git diff --check`. Frontend/desktop smoke is required only if an exposed contract or user-visible state changes.

### Stop rule

- Any privacy/owner/quality/correctness Gate fails.
- A family can pass only by prompt wording or UI hiding rather than analyzer evidence.
- Private source artifacts would need to be committed.
- Required fixes exceed this plan's Allowed files or change a frozen product/architecture contract.

## 2. Execution order and reporting

1. Execute Task 1 first and report changed files/tests/field result/status.
2. Execute Task 2 only after the second Dynamic Run is finalized and calibration/holdout split is frozen.
3. Execute Task 3 after the event-local post-kill annotation protocol is frozen; do not borrow Dynamic thresholds.
4. Execute Task 4 only after Tasks 1–3 are individually reviewed.
5. No commits or pushes until 点点 separately authorizes a commit batch. Preserve the current dirty worktree and report new changes separately.

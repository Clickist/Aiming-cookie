# 当前施工计划入口

> **只有本页列为 active 且由点点明确指定 Task 的 implementation plan 才能交给 executor。** Roadmap、Architecture、spec 或归档 plan 都不能替代可执行 Task。

## Active

| 顺序 | Plan | 当前可执行范围 | 状态 |
|---|---|---|---|
| 1 | `2026-07-11-persistent-coach-data-ownership.md` | **线 A**：SQLite 常驻 Coach 数据归属 + 删除语义 + `/coach`；Task 1–5 | **active — 优先开工** |
| — | `2026-07-11-pi-agent-coach-runtime-assessment-spike.md` | assessment/Spike | **completed**（裁决见 assessments） |

## 已裁决、待写 plan（不得无 plan 施工）

顺序由 `docs/ROADMAP.md` 与 `docs/superpowers/assessments/2026-07-11-pi-assessment-decision.md` 冻结：

1. **线 B**：Pi `third_party` 接管 + Coach sidecar（assessment CONDITIONAL GO 后）
2. session workspace + streaming upload
3. artifact deletion / no-auto-TTL / orphan / quota / low-disk
4. health/readiness + structured logging + supervisor
5. trusted preview deployment boundary
6. 真实素材 E2E + browser E2E release gate

## Archive

- 已完成：`docs/archive/completed/plans/`
- 冻结：`docs/archive/frozen/plans/`
- 退役且不得执行：`docs/archive/retired/plans/`（含旧 persistent-coach-migration）

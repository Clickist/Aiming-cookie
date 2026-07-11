# 当前施工计划入口

> **只有本页列为 active 且由点点明确指定 Task 的 implementation plan 才能交给 executor。**

## Active

（无 — 等待点点指定下一独立 plan 的 Task。）

## Completed（近期）

| Plan | 状态 |
|---|---|
| `2026-07-12-coach-structure-hardening.md` | completed（v3 migrate、delete 单事务、CoachEngine+service、routes ~572 行） |
| `2026-07-12-pi-coach-runtime-integration.md` | completed（薄切片） |
| `2026-07-11-persistent-coach-data-ownership.md` | completed |
| `2026-07-11-pi-agent-coach-runtime-assessment-spike.md` | completed |

## 尚待独立 plan

1. vendor/runtime 依赖面收紧 + 禁止 import coding-agent  
2. Pi 常驻 sidecar / 预编译入口（去冷启动 subprocess）  
3. session workspace + streaming upload  
4. artifact lifecycle / quota  
5. health + trusted preview + browser E2E  

## Archive

- `docs/archive/completed|frozen|retired/plans/`
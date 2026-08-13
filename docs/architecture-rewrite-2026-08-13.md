# 2026-08-13 架构重写授权与计划

> **状态：点点授权执行中。** 本文档记录产品负责人（点点）2026-08-13 的架构重写决策，取代之前所有与此冲突的架构描述。

## 1. 授权背景

当前架构（Python backend + Node sidecar + SQLite）从 Web 应用时代遗留而来，包含大量过度工程：context attach 机制、云服务级安全约束、50+ API 端点、~20 个 DB CRUD 模块。这些中间层严重阻碍了核心功能——Coach 无法直接读取用户的分析数据，导致对话功能完全受阻。

点点授权进行彻底的架构简化，目标是：**Coach 直接读写文件，后端只做不可替代的计算（MP4/Raw Input 处理），消除一切不必要的中转。**

## 2. 新架构

```text
Tauri (Rust) — 保留 Raw Input、WGC 窗口录制、Capture Coordinator、Media Protocol
  │
  ├─ Python backend (瘦身后)
  │   ├─ KovaaK 文件发现（Stats/Performance watcher）— 留在 Python
  │   ├─ 分析计算（kovaak_tracker: Raw Input + MP4 → 数值证据）— 不可替代的核心
  │   ├─ 分析结果输出为渐进式披露 JSON 文档到 app-data/analyses/
  │   └─ Provider OAuth 等复杂认证流程
  │
  └─ Node Coach (Pi AgentHarness + 文件系统)
      ├─ read/write/ls 工具直接读写 app-data/
      ├─ JSONL 对话持久化（Pi 原生 JsonlSessionRepo）
      ├─ 用户画像、训练计划等 → JSON 文件读写
      └─ 直接和用户对话，零中转
```

## 3. 文件系统结构

```text
app-data/
  analyses/{id}/
    overview.json       ← 诊断概览（Coach 日常读这个，≤32KB）
    metrics.json        ← 完整指标分布
    events.json         ← 事件级数据（每次 flick/click）
    evidence.json       ← 完整 evidence artifact（按需深挖）
    stats.txt           ← 原始 Stats CSV 副本（Coach 可直接读）
    video.mp4           ← managed 回放视频（前端播放，Coach 不读）
  profile.json          ← 用户画像
  training/
    plan.json           ← 当前训练计划
    history.jsonl       ← 训练历史
  conversations/
    {id}.jsonl          ← 对话记录（Pi JSONL 格式）
  config/
    provider.json       ← Provider 配置 + API key（明文 local-first）
    settings.json       ← 应用设置
  raw/
    run_{id}/
      trace.bin         ← Raw Input 二进制（仅 Python 读）
```

**Coach 文件系统权限：**
- 读：`analyses/`, `profile.json`, `training/`, `conversations/`, `config/provider.json`
- 写：`profile.json`, `training/`, `conversations/`
- 不能写：`analyses/`（Python 后端写）、`raw/`（Rust 写）
- 不能删除用户文件（系统提示词限制）

## 4. 废弃清单

### Python backend 废弃（~15 个模块）
- `coach_runtime.py`（1755 行代理层）
- `coach_service.py`, `coach_store.py`, `coach_agent_runs.py`
- `coach_context_refs.py`, `coach_confirmations.py`, `coach_commands.py`
- `coach_context.py`, `coach_guidance.py`, `coach_problem_compiler.py`
- `coach_evidence_bridge.py`, `coach_engine.py`
- `coach_steam_profiles.py`, `coach_retest_decision.py`

### Node sidecar 废弃
- `analysis-summary-tool.ts`（~1380 行）
- `skill-loader.ts`
- context attach 逻辑（`sidecar-coach-data.ts` 中 attach/detach/cards 部分）
- product command 中的读命令和 evidence 命令（Coach 直接读文件替代）

### 前端废弃
- CoachPanel.tsx 中的批量分析编排（~150 行）
- context attach/detach UI
- evidence card 后端推导
- 大量 legacy API 函数

### 完全废弃
- SQLite 数据库（内测阶段清空重来，不迁移历史数据）

## 5. 渐进式披露文档格式

分析完成后 Python worker 输出 4 层 JSON 文档：

| 文件 | 内容 | Coach 用途 |
|---|---|---|
| `overview.json` | 诊断 issues、关键指标摘要、scenario 信息、evidence 可用性 | 日常对话读这层够用 |
| `metrics.json` | 完整指标分布（median/p25/p75/p90/mean/std/min/max） | 深入讨论指标时读 |
| `events.json` | 每次事件的运动学详情（flick/click/episode） | 定位具体问题时读 |
| `evidence.json` | 完整 evidence artifact（现有格式迁移） | 复杂查询时读 |

`stats.txt` 是 Stats CSV 的纯文本副本，Coach 可以直接读，理解场景名、击杀/命中、FOV/DPI/Sensitivity 等字段。

## 6. 分阶段计划

### Phase 1 — Node sidecar 改造（核心）
1. 迁移到 AgentHarness + NodeExecutionEnv(cwd=app-data)
2. 注册 Pi 文件系统工具（read/write/ls）
3. JSONL 对话持久化（JsonlSessionRepo 替代 SQLite）
4. 重写系统提示词（移除限制，指导 Coach 直接读文件）
5. 删除 analysis-summary-tool、skill-loader、context attach 逻辑

### Phase 2 — Python backend 瘦身
1. worker 完成后输出渐进式披露 JSON 文档到文件系统
2. 保留 KovaaK ingestion、analysis worker、Provider auth
3. 删除 Coach 代理层

### Phase 3 — 前端简化
1. CoachPanel 瘦身（删除批量分析、context attach）
2. 时间段链接（解析 Coach 消息中 `@3.4s` 标记 → 可点击跳转视频）
3. SessionRail 简化

### Phase 4 — 清理
1. 清空 SQLite，全新开始
2. 废弃 DB 相关代码
3. 更新所有文档

## 7. 关键决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 历史数据 | 清空重来 | 内测阶段，不值得写迁移脚本 |
| 时间段链接格式 | `@3.4s` | 简洁，前端 regex 解析 |
| KovaaK watcher | 留在 Python | 和 analysis worker 在一起，减少改动 |
| SQLite | 完全废弃 | 本地单用户应用不需要 DB |
| Coach 权限 | 全部文件读写（除删除用户文件） | 本地应用，无安全风险 |
| Provider API key | 明文 JSON 文件 | local-first，用户自己的机器 |

## 8. 上游文档同步要求

本重写完成后需更新：
- `docs/PRD.md` — 移除 context attach、evidence card 推导等产品描述
- `docs/ARCHITECTURE.md` — 全面重写为新架构
- `docs/ROADMAP.md` — 重写优先级替换为 Phase 1-4
- `docs/DEVELOPMENT.md` — 更新启动和开发流程
- `CLAUDE.md` / `AGENTS.md` — 更新架构描述

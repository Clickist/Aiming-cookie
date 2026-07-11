> ⚠️ **时间勘误（2026-07-09 核实 git 史）**：本 review 中 narrator 相关的「17 个月未调用」系 subagent hallucination（07-08 与 07-09 两轮 subagent 均如此编造）。实际：narrator.py 2026-06-28 引入、agent.py 2026-07-05 引入并替代、narrator 退役约 4 天。代码事实（report.py 只 import agent、providers.generate 仅 narrator 调、无触发入口）属实。07-09 已决策：删 narrator。

# 2026-07-08 全量 Review（代码 + 理论 + 产品方向）

> 9 个 agent 并行 review，三维度。点点睡前授权自主执行。各分报告在同目录。

## 总览

| 维度 | Agent | 健康度 | C | H | M | L |
|---|---|---|---|---|---|---|
| 代码 | 01-coach 子包 | B+ | 0 | 1 | 4 | 6 |
| 代码 | 02-flicking 主线 | 黄绿 | 0 | 2 | 5 | 6 |
| 代码 | 03-tracking+CV | B- | 0 | 3 | 5 | 4 |
| 代码 | 04-webapp 后端 | B+ | 0 | 4 | 7 | 7 |
| 代码 | 05-webapp 前端 | B+ | 0 | 1 | 6 | 6 |
| 理论 | 06-flicking 指标 | 8/10 | 0 | 0 | 3 | — |
| 理论 | 07-tracking 理论 | 中上 | — | 1† | 5 | — |
| 产品 | 08-PRD 对齐 | 无偏差 | — | — | — | — |
| 产品 | 09-YAGNI | A- | — | — | — | — |

† tracking 的 High 是理论债（speed_mismatch 命名/实现不符），非实现 bug。

**代码维度合计：0 Critical / 11 High / 27 Medium / 29 Low**
**对比 07-07（3C/19H/30M/21L）：Critical 清零，High 降 42%（19→11），无回归。**

## 跨领域 pattern（共性问题）

1. **VideoCapture try/finally 覆盖不全** —— 07-07 修了 5 处，但本次发现还有 **2 处遗漏**：`pan_tracker.py:141-184`（compute_pan_trajectory）+ `video.py:27`（get_video_metadata）。Windows 异常锁文件。说明上轮"5 处"盘点不完整。
2. **命名 vs 实现型理论债** —— `submovement_overlap`（flicking，实为 trough depth ratio）+ PTC（tracking）+ speed_mismatch，都是"实现合理但名字/文档误导"。flicking agent 明确指出 submovement_overlap 与 tracking PTC **同型问题**。
3. **历史切换遗留 dead code** —— 旧 flicking pipeline ~470 行（`aligner.py` 整文件 + `flicking.py` 旧函数 + `pan_tracker.analyze_flicking_video`）+ narrator 双轨 ~340 行。PROGRESS 记了"切换公平指标"但旧代码没回收。
4. **文档内部矛盾** —— `tracking-coach spec §1.1-1.3` 用数学误解否认 CLAUDE.md（spec 说 v_c≠0，CLAUDE.md 说 v_c=0，**代码站在 CLAUDE.md 这边**——`advice_tracking.py:155`）。

## 最该修 Top（按影响 × 紧迫）

### 🔴 必修（v1 开放注册前）
1. **webapp IDOR**（04 H-3）—— 所有 `/api/sessions/{id}/...` 无 ownership 校验，session_id 可枚举读他人视频/诊断/chat + 消耗他人 budget。开放注册后第一个用户就受影响。修复：每端点加 `user_id` 对比 → 403。
2. **`analysis.py:197` sys.exit(1)**（03 H-3）—— 嵌入场景（sidecar/webapp）杀死宿主进程。1 行改 `raise`。
3. **VideoCapture 2 处遗漏**（02 H-1 + 03 H-2）—— `pan_tracker.compute_pan_trajectory` + `video.get_video_metadata` 补 try/finally。
4. **零消灭 CSV NaN 崩溃**（02 H-2）—— 全 miss 场景（有效 KovaaK 场景）`math.ceil(NaN)` ValueError。NaN 守卫。
5. **profiles.ROOT_CAUSES 缺 decel_frac low**（01 H1）—— 三层根因 fallback 到单层（只有 symptom）。1 行补。

### 🟡 计费准确（B 阶段 freemium 立墙前）
6. **budget 记账不一致 + 2 TOCTOU**（04 H-1/H-2/H-4）—— chat vs worker 记账不一致（reply=None 跳过 cost 但 LLM 已调用）；chat budget 预检查 + analyze 单 job 检查都是 check-then-act，并发可绕过。D 阶段无墙可容忍。

### 🟢 文档诚实（理论，不阻塞）
7. **tracking-coach spec §1.1-1.3 修正**（07）—— 承认默认模式 v_c=0 是实现事实，别用"逐帧采样≠常量"的数学误解否认 CLAUDE.md。保留"metric 作 info/watch"的实践结论，但理由改为"miss 段目标速度是有用情境信号"。
8. **`submovement_overlap` docstring**（06）—— 标注"实为 trough depth ratio，非 Novak time overlap"，与 PTC 同型命名债。

### 🔵 清理 ROI（不阻塞，减负）
9. **旧 flicking pipeline ~470 行**（09 D-1/2/3）—— `aligner.py` 整文件（159 行）+ `flicking.py:35-374` 旧函数 + `pan_tracker.py:199-245`。runtime 100% 走 `analyze_flicking_fair_summary`，零测试覆盖。
10. **narrator 双轨 ~340 行**（09 D-4）—— `narrator.py` + providers 各 backend `generate()` + `test_narrator.py`。17 个月未 runtime 调一次，倾向删（需点点决策）。

## 07-07 修复验证（无回归）

上轮 3 Critical + 19 High 全部修了，本次逐项验证：

| Agent | 上轮修复 | 验证 |
|---|---|---|
| 01-coach | 7 项（tool_calls 单条件 / throughput / decel_frac 带状化 / SPARC -5.0 / provider backends / KB 阈值 / BY_SIGNAL 删） | ✅ 7/7 完整（残留：agent loop 终端分支 last_text 泄漏，Medium） |
| 02-flicking | 7 项（滑窗 / aligner NaN / csv_parser TTK+大小写 / segment 空守卫 / 短 flick docstring / fps 显式传） | ✅ 7/7 完整 |
| 03-tracking | 5 项 VideoCapture | ✅ 5/5 完整（**但发现第 6 处 get_video_metadata 漏——上轮盘点遗漏**） |
| 04-webapp-be | 5 项（size 预检 / asyncio / budget updated_at / Win tmp / SQL assert） | ✅ 5/5 完整，有回归测试 |
| 05-webapp-fe | 5 项（no-scrollbar/pulse-ring / videoRef / 轮询闭包 / timeline a11y / 响应式） | ✅ 5/5 完整 |

**结论：07-07 修复无回归；唯一遗憾是 VideoCapture 盘点遗漏 2 处（已列入必修 #3）。**

## 产品方向（08）—— 13 条 UIUX 决策对齐

**❌ 偏差 0 条**。分布：✅ 已实现 5 / 🟡 部分 5 / ⬜ 待实现 3。

两个"轻度偏离"（不算 bug，记录原因）：
- **processing 完成强制跳转**（`sessions/[id]/page.tsx:164-168` router.push）vs PRD §6.3"不强制跳转"——受当前 processing 全屏页架构限制，toast/角标落地时一并改。
- **webapp 用 server-side SQLite** vs"history 本地优先"——且 coach 包自己的 JSONL 持久化 webapp worker 没接通（`worker.py:91` 没传 history_path）。桌面 hybrid 落地时统一。

**架构分工干净**：`kovaak_tracker` 对 `webapp.backend` 零依赖，worker 纯函数无 FastAPI/DB 耦合，搬本地 sidecar 不需解耦——印证 PRD §9"既有资产演进不浪费"。

**最大功能缺口**：history 页（item 1 默认页动态 + 6 删/导出/导入 + 11 角标 都依赖它落地）。

## 点点决策待办

- [ ] **narrator 双轨删还是留**（09 D-4，17 个月未用，倾向删）
- [ ] **文件夹记忆"bug"定性**（05）—— 是 Chromium 原生行为（同 origin 所有 file input 共享"上次目录"），**非代码 bug**；修复要 `showOpenFilePicker({id})` 重构，算功能开发。PRD §8 #13 描述需修正。
- [ ] **旧 flicking pipeline 清理确认**（09 D-1/2/3，~470 行，runtime 0%）
- [ ] **tracking-coach spec §1 修正方向**（07，承认 v_c=0 vs 改实现让 v_c≠0）

## 分报告索引

| # | 报告 | 维度 |
|---|---|---|
| 01 | [coach.md](01-coach.md) | 代码 — coach 子包 + 规则引擎 |
| 02 | [flicking.md](02-flicking.md) | 代码 — flicking 主线 |
| 03 | [tracking-cv.md](03-tracking-cv.md) | 代码 — tracking + CV |
| 04 | [webapp-backend.md](04-webapp-backend.md) | 代码 — webapp 后端 |
| 05 | [webapp-frontend.md](05-webapp-frontend.md) | 代码 — webapp 前端 |
| 06 | [theory-flicking.md](06-theory-flicking.md) | 理论 — flicking 指标 vs 学术 |
| 07 | [theory-tracking.md](07-theory-tracking.md) | 理论 — tracking 理论债 + 声称 |
| 08 | [product-prd-alignment.md](08-product-prd-alignment.md) | 产品 — 实现 vs PRD |
| 09 | [yagni.md](09-yagni.md) | 产品 — YAGNI + dead code |

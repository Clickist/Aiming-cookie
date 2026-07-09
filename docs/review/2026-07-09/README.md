# 2026-07-09 全量 Review（代码 + 测试 + 理论 + 产品）

> 9 agent 并行 review。距上次（07-08）仅 1 天，代码只动了 1 个修复 commit（5a5bb84，4 处低风险）。所以本次定位≠重复昨天，而是：**验证昨天修复 / 复查必修未修项 / 深挖遗漏 / 补昨天缺的维度（测试质量 + 安全纵深）**。各分报告在同目录。

## 总览

| # | Agent | 健康度 | C | H | M | L |
|---|---|---|---|---|---|---|
| 01 | coach 运行时 | B+ | 0 | 0 | 3 | 4 |
| 02 | coach 报告链 | A- | 0 | 0 | 0 | 3 |
| 03 | flicking 主线 | 黄绿 | 0 | 3 | 3 | 3 |
| 04 | tracking+CV+CLI | B- | 0 | 3 | 4 | 4 |
| 05 | webapp 后端+安全 | **C** | 2 | 4 | 7 | 6 |
| 06 | webapp 前端 | B+ | 1 | 1 | 6 | 4 |
| 07 | 测试质量+回归 | C | 1 | 3 | 6 | 4 |
| 08 | 理论一致性 | 8.5/10 | 0(实现bug) | — | —(4命名债) | — |
| 09 | 产品/PRD/YAGNI | A | 0偏差 | — | — | — |

**代码+测试维度合计：~4C / 14H / 29M / 28L**（理论/产品维度无 C/H，不阻塞）

## ⚠️ 核心结论：不是回归，是"昨天必修没修完 + 审查更深"

对比 07-08（0C/11H/27M/29L）→ 07-09（~4C/14H/29M/28L），C/H 看似上升，但根因是三件事，**不是代码恶化**：

1. **昨天"必修 5 项"只执行了 2 项**（5a5bb84 修了低风险的 sys.exit + profiles ROOT_CAUSES）。真正高风险的 **IDOR / pan_tracker try/finally / CSV NaN 一直没动**——这次重新确认并升级。
2. **昨天 tracking 域几个 M 经深入分析升级为 H**（VideoWriter 泄漏 / cross_x NaN 传播 / HSV 环绕失效），07-08 修了 video.py 那处，但这 3 处昨天判 M 今天判 H。
3. **两个新维度**（07 测试质量、05 安全纵深）是昨天没有的 agent，暴露了昨天没审的盲区（IDOR 无测试、TOCTOU、rate limit、stale job 等）。

**无回归**：昨天修的 4 处（5a5bb84）全部验证通过；07-07 的修复也都还在；基线 tests/ 127 + webapp/ 47 passed。

## 昨天必修 5 项执行情况（关键信号）

| # | 昨天(07-08)必修项 | 今天状态 | 备注 |
|---|---|---|---|
| 1 | webapp IDOR（H-3，必修#1） | ❌ **未修 → 升级 C-1** | 阻塞点明确：`queue.py` 不返回 user_id |
| 2 | analysis.py sys.exit(1)（H-3） | ✅ **已修**（5a5bb84） | 验证通过 |
| 3 | VideoCapture 2 处遗漏（H-1/H-2） | 🟡 **半修** | video.py ✅；**pan_tracker.py:141-184 仍未修**（03 H-1） |
| 4 | 零消灭 CSV NaN 崩溃（H-2） | ❌ **未修**（03 H-2） | `math.ceil(NaN)` ValueError，有效场景崩溃 |
| 5 | profiles ROOT_CAUSES 缺 decel_frac low（H1） | ✅ **已修**（5a5bb84） | 三层根因链完整 |

**必修 5 项完成 2/5（40%）**，且未完成的都是高风险项。这是本次 review 最该行动的信号。

## 跨领域 pattern（共性问题）

1. **资源管理（VideoCapture/VideoWriter）始终没闭环** —— 07-07 修 5 处，07-08 发现漏 2 处（pan_tracker + video），07-08 修 video、今天发现还有 **pan_tracker.compute_pan_trajectory（03 H-1，仍未修）+ calibration_cli VideoWriter（04 H-1，新发现）+ app.py:85（04 L-1）**。"VideoCapture 全覆盖"三轮 review 没彻底完成，建议改成统一的 `_open_video` context manager 一次性收口（昨天 README 已提此建议）。

2. **测试盲区与必修未修项完全重合** —— IDOR 无测试（07 C）、CSV 全 miss 无测试（07 H）、pan_tracker try/finally 无测试（07 H）。5a5bb84 的 4 处修复仅 1 处有间接测试。**测试套件未能暴露任何昨天标必修的 bug**，说明测试策略需补"跨用户越权 / 边界条件 / 资源管理"三个维度。CV/vision/analysis/pan_tracker 几乎无单元测试。

3. **budget 计费机制多处不一致** —— chat 路径 `reply=None` 但 LLM 已调时漏记 cost（05 H-1）+ 硬编码 DeepSeek 单价切 provider 失准（05 M-4）+ 长历史低估 input tokens（05 L-4）+ chat/analyze 两处 TOCTOU（05 H-2/H-3）。计费这面墙在 B 阶段 freemium 前必须整体修，不能打补丁。

4. **命名债 4 项已充分标记，诊断规则安全** —— submovement_overlap（已修）/ PTC / speed_mismatch / accel_mismatch，全部 threshold=None 或 severity=info/watch，不触发硬诊断（08）。理论层 0 实现 bug，flicking 公平指标 8 个全部验证符合学术锚点。

5. **架构分工保持干净** —— kovaak_tracker 对 webapp.backend 零依赖、worker 纯函数无 FastAPI/DB 耦合、依赖全部真用无冗余、无死参数（09）。印证 PRD §9"既有资产演进不浪费"。

## 最该修 Top（按影响 × 紧迫）

### 🔴 必修（v1 开放注册前）
1. **webapp IDOR**（05 C-1）—— 所有 `/api/sessions/{id}/...` 无 ownership 校验，session_id 可枚举读他人视频/诊断/chat + 消耗他人 budget。**阻塞点：`queue.py:111-112` SELECT 不含 user_id 列**，需先改 queue.py 返回 user_id，再各端点加 `if s["user_id"] != x_user_id: raise 403`。
2. **pan_tracker try/finally**（03 H-1，昨天必修#3）—— `compute_pan_trajectory` Windows 异常锁文件。5 行修。
3. **CSV 全 miss NaN 崩溃**（03 H-2，昨天必修#4）—— `math.ceil(NaN)` ValueError，有效 KovaaK 场景崩溃。6 行修。
4. **calibration_cli VideoWriter 泄漏**（04 H-1）—— `writer.release()` 在 finally 外，异常时锁 calibration_check.mp4。2 行修。

### 🟡 计费/并发（B 阶段 freemium 立墙前）
5. **budget 记账不一致**（05 H-1）—— chat 路径改为"backend 非 None 即记 cost"。
6. **TOCTOU ×2**（05 H-2/H-3）—— chat 加 per-user `asyncio.Lock`；analyze 用 `BEGIN IMMEDIATE` 包 check+enqueue。
7. **DB_PATH 默认相对路径**（05 H-4）—— web/worker 启动目录不同会连不同 DB，改绝对路径。
8. **worker stale job + 无超时**（05 M-1/M-2）—— 崩溃后 job 卡 running 永久锁死用户；坏视频卡死整个 worker。加超时 + stale 回收。

### 🟠 防御性（新发现 H，低成本）
9. **analysis.py cross_x NaN 传播**（04 H-2）—— `load_tracking_data` 只过滤 ball_x，cross_x 为 None 时全指标被 NaN 污染。1 行修（加 `& df["cross_x"].notna()`）。
10. **vision.py HSV 环绕失效**（04 H-3）—— `get_hsv_range` 永不产生环绕区间，`_make_mask` 环绕分支是 dead code，红色目标检测召回低。5 行修。
11. **flicking peak_v=0 除零**（03 H-3）—— `peak_v <= 0` 守卫。1 行修。

### 🟢 文档诚实（不阻塞）
12. **tracking-coach spec §1.1-1.3 修正**（04 M-3 + 08）—— spec 用"逐帧采样≠常量"否认 CLAUDE.md 的 v_c=0，但 08 用数学证伪（Savitzky-Golay 不向常量注入噪声、`np.gradient` 对常量严格返回 0、`advice_tracking.py:155` 注释也承认 v_rel 主导项是目标速度）。**CLAUDE.md 对，spec §1 错**。
13. **5a5bb84 submovement_overlap docstring** —— 验证准确充分（08）。

### 🔵 清理 ROI（不阻塞，减负 ~470 行）
14. **旧 flicking pipeline ~280 行 + aligner.py 159 行 + analyze_flicking_video 50 行**（03 + 09 D-1/2/3）—— runtime 0%、测试 0%、零 API 暴露。最高 ROI。
15. **零散**：`direction_deg`（算了从不读，6 行）、`save_uploaded_video`（5 行）、孤儿 .pyc。

## ⚠️ 决策待办（含 2 项 agent 结论冲突，需点点拍板）

### 冲突项（两个 agent 给出相反建议）

- [x] **narrator 双轨 → 已决策：删**。⚠️ 时间勘误：02/09 称"17 月未调用 / 2024-11 替代 / 2025-02 修改"系 subagent hallucination——核实 git：narrator 6-28 引入、agent 7-5 替代、退役仅 ~4 天。代码事实成立（report.py 只 import agent、providers.generate 仅 narrator 调、无触发入口），故删。**已执行（07-09）**：narrator.py + test_narrator.py 回收，providers 去 generate + LLMBackend Protocol + 返回类型收窄，各 docstring 清理；tests 116 + webapp 47 passed。agent_kb 7 处 KB 文本代称保留。
- [x] **processing 完成强制跳转 → 定性：暂不改（现在改更差）**。06 判 Critical vs 09 判轻度偏离；采纳 09 并补关键点：toast/角标组件未建（PRD item 11 待实现），现在删 `router.push` 用户完成后无反馈（比现在更差）。记为「等 toast/角标组件 + processing 后台化时一并把跳转换成 toast」，**非 Critical**。

### 一致建议项（agent 间无分歧，待确认即可执行）

- [x] **IDOR 修复时机 → v1 前做 A（ownership），B 等 Clerk**。点点确认 v1 开放注册近：开放前必修最小 ownership（queue.py 返回 user_id + 各端点 403 校验，防枚举）；Clerk 验签（防伪造 user_id）等切片 3。
- [x] **旧 flicking pipeline → 删**。03 + 09 一致（runtime 0%/测试 0%/零调用）：aligner.py 整文件 + flicking.py 旧函数群 ~280 行 + pan_tracker.analyze_flicking_video ~50 行。执行先精确区分旧/现役函数边界再删 + 测试验证。
- [x] **spec §1 → 改 spec 承认 v_c=0**。04+08 一致（CLAUDE.md 对、spec §1 数学论证错）。点点确认准星写死画面中央是实现事实。改 spec §1.1-1.3 对齐 CLAUDE.md，metric 留作 info/watch，理由改成"目标速度是有用情境信号"。纯文档，不改代码。
- [x] **文件夹记忆 → 非 bug，PRD §13 描述修正**。06+09 确认 Chromium 同 origin 共享目录记忆（浏览器行为非代码 bug）；⚠️ 06「天然分别」vs 09「共享」两说法打架未实测。**点点补充：后续打包桌面应用不用浏览器，此问题目标形态不复现**。PRD §13 标注「web 形态浏览器行为；桌面形态不复现」。
- [ ] **过时 spec 加演进标注**（09）—— `2026-06-28-ai-aim-coach-design.md` + `progress-loop-design.md` 顶部标注被谁演进。

## 5a5bb84 修复验证（无回归）

| 修复项 | 域 | 验证 | 回归测试 |
|---|---|---|---|
| analysis.py sys.exit→raise + 删 import | 04 | ✅ | ❌ 无（07） |
| profiles.py ROOT_CAUSES 补 decel_frac low | 02 | ✅ 三层根因链完整 | ✅ 间接（07） |
| video.py get_video_metadata try/finally | 04 | ✅ | ❌ 无（07，被 mock 绕过） |
| flicking.py submovement_overlap docstring | 03/08 | ✅ 准确充分 | N/A |

**4 处修复全部正确无回归。但 4 处中 3 处无回归测试覆盖**（07 H），profiles 那处仅间接覆盖。下次修复应配套补测试。

## 正向发现（值得保持）

- **flicking 公平指标 8 个全部符合学术锚点**（03/08）：SPARC(Balasubramanian 2012)、Fitts throughput、linearity、decel_frac、path_efficiency、reverse_ratio、peak_position 实现正确。
- **架构零依赖**（09）：kovaak_tracker 对 webapp.backend 零依赖，worker 纯函数，搬本地 sidecar 不需解耦。
- **前端类型严格 + a11y 意识强**（06）：types.ts 与 schemas.py 一致、AbortController 正确使用、时间戳联动优雅。
- **理论层诚实**（08）：PTC/J-E/TBR 命名债全部文档诚实标注，J/E+TBR 代码清理干净无残留。
- **测试无 flaky、无死测试**（07），单元/集成比例 ~40/60 健康。

## 分报告索引

| # | 报告 | 维度 | 健康度 |
|---|---|---|---|
| 01 | [coach-runtime.md](01-coach-runtime.md) | 代码 — agent loop/tool/KB/providers | B+ |
| 02 | [coach-report.md](02-coach-report.md) | 代码 — 报告/诊断/计划/可视化/narrator | A- |
| 03 | [flicking.md](03-flicking.md) | 代码 — flicking 主线 + 公平指标 | 黄绿 |
| 04 | [tracking-cv.md](04-tracking-cv.md) | 代码 — tracking + CV + CLI | B- |
| 05 | [webapp-backend.md](05-webapp-backend.md) | 代码 — webapp 后端 + 安全纵深 | C |
| 06 | [webapp-frontend.md](06-webapp-frontend.md) | 代码 — webapp 前端 | B+ |
| 07 | [tests.md](07-tests.md) | 测试 — 质量 + 回归覆盖 | C |
| 08 | [theory.md](08-theory.md) | 理论 — 指标锚点 + 命名债 | 8.5/10 |
| 09 | [product-yagni.md](09-product-yagni.md) | 产品 — PRD 对齐 + YAGNI + dead code | A |

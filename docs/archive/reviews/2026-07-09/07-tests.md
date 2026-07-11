# 2026-07-09 Review — 测试质量+回归

## 健康度 | C

**总体评估**：测试覆盖核心路径但缺少关键安全测试（IDOR），必修未修项无测试暴露。断言强度中等，mock 合理但过宽（部分测试只验证 mock 被调，不验证真实逻辑）。

**C/H/M/L**
- C (Critical): 1 — IDOR 测试完全缺失（必修未修项 #1）
- H (High): 3 — 5a5bb84 修复 4 处中 3 处无回归测试；必修未修项 #2/#3 无测试
- M (Medium): 6 — 测试盲区（CV/vision/analysis/pan_tracker 资源泄漏）；断言偏弱
- L (Low): 4 — conftest 冲突；测试组织；夹具复用可改进

---

## 基线（tests/ 127 passed + webapp/tests/ 47 passed 1 skipped，无回归）

```
tests/: 127 passed in 2.69s
webapp/tests/: 47 passed, 1 skipped in 2.71s
```

**无回归确认**：与昨天 commit message 报告一致。但测试本身存在盲区，未测到的功能是否被破坏无法通过测试验证。

---

## 5a5bb84 修复回归覆盖（逐项✅/❌）

| 修复项 | 位置 | 修复内容 | 回归测试 | 结论 |
|--------|------|----------|----------|------|
| 1 | `analysis.py:191-197` | `sys.exit(1) → raise FileNotFoundError` | ❌ 无 | tests/ 无 `run_analysis` 测试 |
| 2 | `profiles.py:79` | `ROOT_CAUSES` 补 `decel_frac low` | ✅ 间接覆盖 | `test_planning.py:128/136` 使用该 signal；`test_diagnosis.py:12-16` 验证所有 archetype 条件在 ROOT_CAUSES 中（防止 future 回退） |
| 3 | `video.py:25-56` | `get_video_metadata` 加 try/finally | ❌ 无 | `test_progress_a.py:33/135` mock 此函数，未测真实行为 |
| 4 | `flicking.py:417/514` | `submovement_overlap` docstring 命名澄清 | N/A | docstring 修改无需测试 |

**结论**：4 处修复中 **仅 1 处有回归测试覆盖**（profiles.py 间接覆盖），其余 3 处修改未触发任何失败测试。

---

## 必修未修项测试盲区（IDOR/NaN/pan_tracker）

### 1. IDOR（webapp）— 🔴 CRITICAL 测试盲区

**现状**：`webapp/tests/` **完全没有 ownership/越权测试**。

**证据**：
```bash
$ grep -r "403\|Forbidden\|cross.*user\|different.*user" webapp/tests/
# No matches found
```

所有 session 相关端点测试都使用单一用户（默认 "u1"），无跨用户访问测试：
- `test_routes_coach.py`: `/api/sessions/{sid}/video`, `/timeline`, `/chat` — 仅测 happy path，无 user_id 校验
- `test_routes_chat.py`: `/api/sessions/{sid}/chat` POST/GET — 全用 `user_id: str = "u1"`
- `test_routes.py`: `/api/sessions/{sid}` GET — 单用户测试
- `test_llm_budget.py`: 仅测用户间额度隔离，非所有权

**影响**：开放注册后，用户 A 可枚举 session_id 访问用户 B 的视频/诊断/chat/budget。这是 **Critical 安全漏洞**，且测试完全未覆盖。

**建议**：加 `test_cross_user_access_forbidden_403` 测试套件：
```python
async def test_get_session_cross_user_403():
    sid = await queue.enqueue("alice", "/v", "/c")
    async with AsyncClient(..., headers={"X-User-Id": "bob"}) as client:
        resp = await client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 403  # 当前是 200，无所有权检查
```

---

### 2. CSV 全 miss NaN 崩溃（flicking）— 🔴 HIGH 测试盲区

**现状**：tests/ 无 "全 miss" 场景测试。

**必修未修描述**（昨天 02-flicking H-2）：有效 KovaaK 场景可能全部脱靶，`math.ceil(NaN)` 崩溃。

**搜索结果**：
```bash
$ grep -r "全.*miss\|all.*miss\|NaN.*miss" tests/
# 仅找到 throughput NaN 测试（test_progress_a.py:82-113），非全 miss 场景
```

**影响**：真实用户场景（极端低准确率）会触发 ValueError，测试未捕获。

**建议**：加 `test_all_miss_targets_no_crash` 测试（合成全 NaN `hit=` 场景）。

---

### 3. pan_tracker try/finally 资源泄漏 — 🟡 HIGH 测试盲区

**现状**：tests/ 无 VideoCapture 资源管理测试。

**必修未修描述**（昨天 02-flicking H-1）：`pan_tracker.py:141-184 compute_pan_trajectory` 有 cap = cv2.VideoCapture(...) 但无 try/finally。

**搜索结果**：
```bash
$ grep -r "release()\|VideoCapture\|cleanup\|try.*finally" tests/
# No matches found
```

**影响**：异常路径（视频损坏/权限问题）会泄漏 Windows 句柄，测试未覆盖。

**建议**：加 `test_compute_pan_trajectory_releases_cap_on_exception`（模拟 cv2.VideoCapture 异常，验证资源释放）。

---

## 测试质量问题

### 断言强度

**✅ 良好**：
- `test_advice_tracking.py`: 具体值断言（`assert acc.severity == "fix"`）
- `test_diagnosis.py`: 验证三层结构（`assert levels == ["symptom", "physical", "training"]`）
- `test_agent.py`: 验证 message 结构、tool_use_id 对应

**⚠️ 偏弱**：
- 部分测试仅 `assert result is not None`（如 `test_report.py:34`），未验证内容
- `test_smoke.py`: 仅 `assert kovaak_tracker.coach is not None`（几乎无价值）

**建议**：将 `is not None` 断言改为具体字段验证（如 `assert r.diagnosis.comparison is not None and len(...) > 0`）。

---

### Mocking 合理性

**✅ 良好**：
- `test_agent.py` / `test_agent_chat.py`: Mock 后端但保留真实 agent loop 逻辑
- `test_advice_tracking.py`: 完整走规则引擎，无 mock
- `test_progress_a.py`: Monkeypatch 轨迹生成但保留下游分析逻辑

**⚠️ 过宽**：
- `test_progress_a.py:33/135`: `monkeypatch.setattr(P, "get_video_metadata", ...)` → `video.py` 的 try/finally 修复完全被绕过，未测到
- `test_report.py`: 部分 backend mock（`_Boom` 异常）但未验证真实降级行为

**建议**：对资源管理类修复（try/finally），加独立集成测试而非仅 mock 绕过。

---

### Flaky 测试

**✅ 无 Flaky 发现**：
```bash
$ grep -r "time\.sleep\|sleep(\|random\.rand\|\.shuffle" tests/
# No matches found
```

测试无时间依赖、随机性或隐式顺序依赖。

---

### 覆盖盲区（kovaak_tracker/ 未测模块）

对比 27 个 `kovaak_tracker/**/*.py` 文件，以下 **无专门测试**：

| 模块 | 风险 | 建议 |
|------|------|------|
| `aligner.py` | 159 行，已弃用（昨天 09 D-1） | 删除或标记 deprecated |
| `start_frame.py` | 起始帧检测，核心算法 | 加单元测试 |
| `calibration_cli.py` | CLI 交互，复杂逻辑 | 难测（依赖 OpenCV 窗口），可接受 |
| `analysis.py` | tracking 主入口，sys.exit 已修 | 加 `run_analysis` 异常测试 |
| `tracking.py` | CSRT 追踪引擎，复杂 | 难测（依赖 CV），可接受 |
| `vision.py` | HSV blob 检测，CV 原语 | 加单元测试（合成图像） |
| `pan_tracker.py` | flicking 主线，资源泄漏风险 | 加资源管理 + 全 miss 测试 |

**当前覆盖**：集中在 coach/（11 文件）+ csv_parser + progress_a + smoke。CV/tracking/pan_tracker 几乎空白。

---

### 死测试

**✅ 无明显死测试**：
- `test_narrator.py`: 测试 narrator 生成逻辑，虽 runtime 已切 agent 但作为 fallback 仍有效（CLAUDE.md 证实"保留作 manual fallback"）
- `test_planning.py`: 测试 ④ 动态处方，已 merge（memory 记录）
- `test_progress.py`: 测试历史趋势，活跃使用

**注意**：昨天 09 YAGNI 提到 `narrator 双轨 ~340 行` 倾向删，但需点点决策。若删，`test_narrator.py` 需同步删。

---

## conftest 冲突现状+建议

**现状**：`tests/conftest.py` vs `webapp/tests/conftest.py` 路径冲突导致必须分两批跑。

**证据**：
```python
# tests/conftest.py
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# webapp/tests/conftest.py  
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 重复设置
@pytest_asyncio.fixture(autouse=True)  # 与 tests/ 无冲突但定义重复
```

**当前影响**：必须 `python -m pytest tests/ -q` 然后 `python -m pytest webapp/tests/ -q`，无法统一跑。

**建议**（成本排序）：
1. **低成本**：保留现状，加 CI 脚本分两批跑（已如此操作）
2. **中成本**：重命名 `webapp/tests/conftest.py → webapp/conftest_pytest.py` 显式隔离
3. **高成本**：将 `tests/` 迁移至 `kovaak_tracker/tests/`，消除 sys.path 重复（需更新所有 import）

---

## 端到端 vs 单元比例

**统计**（按文件）：
- **单元测试**：`test_providers.py`, `test_diagnosis.py`, `test_csv_parser.py`, `test_advice_tracking.py`（4 文件）
- **集成/E2E**：`test_e2e.py`, `test_agent.py`, `test_agent_chat.py`, `test_report.py`, `test_planning.py`, `test_progress.py`, `test_visualization.py`（7 文件）
- **Smoke**：`test_smoke.py`（1 文件）
- **Webapp E2E**：`test_routes*.py`, `test_llm_budget.py`, `test_queue.py`, `test_db.py`, `test_worker.py`, `test_e2e.py`（8 文件）

**比例**：约 **40% 单元 / 60% 集成**，健康。

**夹具复用**：
- ✅ 良好：`_diag()`, `_summary()`, `_fake_report_dict()` 复用度高
- ⚠️ 可改进：webapp tests 的 `_seed_done_session` 重复定义于 `test_routes_coach.py` 和 `test_routes_chat.py`

---

## 最大测试盲区一句话

**IDOR（webapp ownership 校验）完全无测试覆盖** — 开放注册后第一个用户即可枚举访问他人数据，且测试套件无法捕获此漏洞。

# Windows Desktop Pre-Frontend Gates Implementation Plan

> **状态：completed。** Task 1–2 已完成；本文归档后仅保留实施与验证证据，不再作为 executor 入口。
> **Executor:** 历史执行遵守各 Task 的 Allowed files、Tests first、冻结决策与 Stop rule；未提交、推送或在本计划内进入正式 frontend。

**Goal:** 闭合当前 Windows 机器可自动验证的 bounded KovaaK 自动发现与 launch-token 子进程隔离 Gate。

**Architecture:** 继续使用现有 Desktop local-first 边界：Tauri 只启动 loopback Python runtime，Python runtime 负责 KovaaK watcher 与 Coach fallback subprocess。Steam discovery 只读取 Windows Steam registry、`libraryfolders.vdf` 与 app `824270` manifest，不扫描磁盘；watcher 对每个 source 目录只观察 lifecycle spec 冻结的最新 50 个 supported files；安全边界只收紧现有 launch token 传播，不新增账号、鉴权、后台服务或 restart policy。

**Tech Stack:** Python 3.11、Windows `winreg`、Steam VDF/ACF text、asyncio/uvicorn、pytest/pytest-asyncio。

**Contracts:** [`../../../ARCHITECTURE.md`](../../../ARCHITECTURE.md)、[`../../../superpowers/specs/2026-07-13-kovaak-run-trace-lifecycle-design.md`](../../../superpowers/specs/2026-07-13-kovaak-run-trace-lifecycle-design.md)、[`../../../ROADMAP.md`](../../../ROADMAP.md)。

---

## Task 1 — Steam multi-library KovaaK discovery

**状态：completed。**

### Allowed files

- `webapp/backend/config.py`
- `webapp/backend/kovaak_ingest.py`
- `webapp/backend/desktop_runtime.py` only for passing the frozen candidate limit
- `webapp/tests/test_config.py`（new）
- `webapp/tests/test_kovaak_ingest.py`

### Tests first

1. `KOVAAK_INSTALL_DIR` 显式 override 保持最高优先级，不读取 registry/VDF，也不要求路径已存在；
2. Windows Steam root 的主 library 与 `libraryfolders.vdf` 中的附加 library 均可参与发现，重复路径去重；
3. 只有存在 `steamapps/appmanifest_824270.acf`、manifest `appid=824270`、安全单目录 `installdir` 且最终 install dir 存在时才返回；
4. malformed/missing registry、VDF、manifest、错误 appid、绝对/父级/多段 `installdir` 均 fail closed，不扫描 drive，也不返回不存在的默认 C 盘路径；多个不同有效安装 fail closed，必须由显式 override 消歧；
5. `KOVAAK_STATS_DIR` / `KOVAAK_PERFORMANCE_DIR` 继续分别覆盖自动推导值；非 Windows 且无 override 时继续返回 `None`；
6. watcher 每个 source 目录只把按 `(mtime_ns DESC, filename casefold ASC)` 排序的最新 50 个 supported files 纳入 state/group/emission；窗口外文件不 callback、不删除，修改后可因新 revision 进入窗口；
7. Stats/Performance 后到补全、async callback retry 与 revision re-emit 在 bounded window 内保持现有行为；
8. 本机只读 live Gate 在无 override 时解析到实际 Steam library，且推导出的 `FPSAimTrainer/stats` 与 `performances` 均存在；首次 watcher scan callback 数不超过冻结窗口。

### Implementation

- 新增小型 Windows Steam root/library/appmanifest helper；只使用 stdlib，不引入通用 VDF dependency；
- Steam root 来源按稳定顺序读取 HKCU `Software\\Valve\\Steam` 的 `SteamPath`、HKLM `SOFTWARE\\WOW6432Node\\Valve\\Steam` 的 `InstallPath`，再使用存在的默认 Program Files Steam root；
- 对每个 root 只读取其 `steamapps/libraryfolders.vdf`，并把 root 本身作为 library candidate；
- 对每个 library 读取 `steamapps/appmanifest_824270.acf`，验证 exact app id 与安全 `installdir` 后返回存在的 `steamapps/common/{installdir}`；
- 若发现多个不同的有效 app install dir，返回 `None`，要求 `KOVAAK_INSTALL_DIR` 显式消歧；
- `KovaaKDirectoryWatcher` 在 stat 后按冻结顺序截取最新 50 个 supported files，再进入现有 stable/retry/pairing 状态机；Desktop service 显式传入该冻结上限；
- 不创建、不修改、不移动 Steam/KovaaK 文件；不递归扫描盘符；不缓存绝对路径到新持久化位置。

### Verify

```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_config.py webapp\tests\test_kovaak_ingest.py webapp\tests\test_desktop_runtime.py -q
.\.venv\Scripts\python.exe -c "from webapp.backend.config import resolve_kovaak_install_dir, resolve_kovaak_data_dirs; print(resolve_kovaak_install_dir()); print(resolve_kovaak_data_dirs())"
.\.venv\Scripts\python.exe -m compileall -q webapp\backend\config.py webapp\backend\kovaak_ingest.py webapp\backend\desktop_runtime.py webapp\tests\test_config.py webapp\tests\test_kovaak_ingest.py
git diff --check -- webapp/backend/config.py webapp/backend/kovaak_ingest.py webapp/backend/desktop_runtime.py webapp/tests/test_config.py webapp/tests/test_kovaak_ingest.py
```

Expected: unit/focused tests pass；本机输出实际安装目录与存在的 Stats/Performance 目录；无 Steam/KovaaK 写入。

### Stop rule

- 需要扫描整个磁盘、调用 Steam CLI、修改 Steam registry/VDF/manifest 或自动移动用户文件；
- 需要改变显式 override precedence；
- 需要引入第三方 VDF parser、后台 indexer 或新的持久化 schema；
- 需要把 50-file bound 改成时间 retention、用户配置或后台历史 backfill；
- 本机 manifest 与实际安装目录不一致，或出现多个有效安装且没有显式 override。

## Task 2 — Launch-token descendant isolation

**状态：completed。**

### Allowed files

- `webapp/backend/coach_runtime.py`
- `webapp/tests/test_coach_runtime.py`

### Tests first

1. Coach fallback subprocess 继续保留普通 caller env 与 pinned `PI_SOURCE_DIR` / `TSX_TSCONFIG_PATH`；
2. `AIMING_COOKIE_DESKTOP_TOKEN` 永不出现在 Node child env；
3. turn-scoped product bridge 仍只通过 versioned request payload 携带必要的 desktop token，并继续通过现有 secret redaction/fail-closed tests；
4. sidecar HTTP 成功路径不启动 fallback subprocess。

### Implementation

- 从 fallback subprocess env 的副本中显式移除 Desktop launch token；
- 不改 Provider credential、tool bridge schema、sidecar URL、fallback policy 或通用环境 allow-list。

### Verify

```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_coach_runtime.py webapp\tests\test_coach_tool_runtime.py -q
.\.venv\Scripts\python.exe -m compileall -q webapp\backend\coach_runtime.py webapp\tests\test_coach_runtime.py
git diff --check -- webapp/backend/coach_runtime.py webapp/tests/test_coach_runtime.py
```

### Stop rule

- 需要把 Desktop launch token 持久化、写日志或改成产品账号/JWT；
- 需要重新设计 Provider credential/env contract 或移除现有 subprocess fallback；
- 需要把任意 shell/filesystem authority 暴露给 Coach runtime。

## Global verification and remaining manual Gates

- Windows pytest 单进程串行，避免 SQLite `WinError 32`；
- `AGENTS.md` / `CLAUDE.md` byte parity；
- 报告真实 changed files、focused/full tests、未运行检查、偏差、剩余风险与 `git status --short`；
- 本计划不定义 READY 后自动 restart/fatal-state、Rust cached connection 失效或 token 轮换；这些需要独立 spec/plan；
- 本计划不宣称 Windows Raw Input 前台 KovaaK gate、实机注册/持续采集、高 polling-rate 性能、真实 input-native/multimodal/video-fallback 素材、GUI、installer、签名或 updater 已通过。

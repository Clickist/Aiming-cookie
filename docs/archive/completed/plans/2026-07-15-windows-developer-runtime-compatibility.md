# Windows Developer / Runtime Compatibility — Implementation Plan

> **状态：completed（2026-07-15）。** Task 1–2 已完成；正式 frontend、GUI、真实 KovaaK、Raw Input、高 polling-rate、installer、签名和 updater 均未被本计划声明为通过。
>
> **完成证据：** Python、Coach/Pi source、Pi AI、frontend adapters 与 Tauri MSVC 自动化 Gate 的 Windows 实机结果见 [`../../../PROGRESS.md`](../../../PROGRESS.md)。
>
> 上游事实源：[`../../../PRD.md`](../../../PRD.md)、[`../../../ARCHITECTURE.md`](../../../ARCHITECTURE.md)、[`../../../ROADMAP.md`](../../../ROADMAP.md)。
> 本计划不新增产品语义、schema、数据生命周期或安全权限；不需要新增 spec。

## 目标

让当前 Windows 开发机能够真实执行既有 Desktop / Coach runtime 自动化 Gate，而不是继续依赖 macOS 历史结果或把环境阻断误报为产品通过：

```text
Python backend
  -> Node + tsx + pinned Pi source
  -> Coach runtime tests / subprocess fallback

Tauri build script
  -> existing placeholder icon source
  -> Windows MSVC cargo checks
```

## 已确认的实现差距

1. Python Coach subprocess 把 Windows 绝对路径直接交给 Node `--import`；Node ESM loader 要求 `file:///...` URL。
2. fresh install 从仓库根运行 Pi Agent 源码时没有设置 pinned `third_party/pi/tsconfig.json`，bare imports 会错误落到尚未生成的 `dist`。
3. Coach knowledge parity test 硬编码 `.venv/bin/python`，Windows `.venv/Scripts/python.exe` 不可达。
4. Tauri Windows build script 要求 `icons/icon.ico`；仓库只有 tracked `icons/icon.png`，因此 Rust 检查在进入项目代码前失败。

## 冻结决策

1. Node loader specifier 使用 `Path.resolve().as_uri()` 生成；不得手写盘符、反斜杠替换或 shell quoting。
2. `run-turn.ts` argv、`PI_SOURCE_DIR` 和 `TSX_TSCONFIG_PATH` 保持绝对本地路径；只有 `--import` loader 使用 file URL。
3. Coach runtime 继续直接加载 pinned Pi source；不得生成、提交或依赖 `third_party/pi/**/dist`。
4. `icon.ico` 只从现有 tracked `icons/icon.png` 机械生成，作为 compile-time placeholder；它不是正式品牌图标，不解除 installer、签名、updater 或 release packaging Gate。
5. `bundle.active=false`、正式 frontend 路由和 frontend reconstruction 顺序不变；本计划不得恢复 prototype、创建占位 App Router 或把 `npm run build` 伪装成已闭合。
6. Pi `packages/coding-agent`、shell、filesystem、skills、prompt harness 不属于 Aiming Cookie 产品工具边界；其 Windows 全仓失败不升级为当前产品 Gate，也不得在本计划修改。
7. 本计划不校准 Raw Input、高 polling-rate、真实 KovaaK 或运动学阈值，不声明 release-ready。
8. 未经点点另行要求，不提交、不推送、不创建发布包。

## Task 1 — Windows runtime / build unblockers

> 本 Task 含两个 Allowed files 完全不重叠的 workstream。点点明确指定 Task 1 后，可由两个 Luna-role subagents 并行实施；主代理必须分别审阅和验收，任一 workstream 触发 Stop rule 时不得用另一个 workstream 的成功覆盖。

### Workstream A — Python ↔ Pi Windows 启动兼容

#### Allowed files

- `webapp/backend/config.py`
- `webapp/backend/coach_runtime.py`
- `webapp/tests/test_coach_runtime.py`
- `webapp/tests/test_coach_tool_runtime.py`
- `webapp/coach-runtime/test/knowledge-parity.test.ts`
- `scripts/run-coach-sidecar.sh`

#### Tests first

1. 在 `webapp/tests/test_coach_runtime.py` 增加 Windows-safe command regression：临时 loader 存在时，`_subprocess_command()` 返回 `--import=file:///...`，并保留原生 `run-turn.ts` argv。
2. 增加 subprocess env regression：`PI_SOURCE_DIR` 与 `TSX_TSCONFIG_PATH` 都是 pinned Pi checkout 内的绝对路径，且不覆盖调用方无关环境。
3. 运行新增测试，确认当前实现分别因 Windows path specifier 与缺少 `TSX_TSCONFIG_PATH` 失败。
4. 最小实现应等价于：

   ```python
   loader_url = COACH_RUNTIME_TSX_LOADER.resolve().as_uri()
   env = {
       **os.environ,
       "PI_SOURCE_DIR": str(PI_SOURCE_DIR.resolve()),
       "TSX_TSCONFIG_PATH": str((PI_SOURCE_DIR / "tsconfig.json").resolve()),
   }
   ```

5. `knowledge-parity.test.ts` 的默认 Python 路径按平台选择：Windows 使用 `.venv/Scripts/python.exe`，其他平台继续使用 `.venv/bin/python`；显式 `PYTHON_BIN` 仍具有最高优先级。
6. `scripts/run-coach-sidecar.sh` 导出 pinned `TSX_TSCONFIG_PATH`，并把 loader 转成 Node 可接受的 file URL；不得改变 sidecar host、port、schema 或 provider contract。
7. 修复真实 Analysis → TypeScript knowledge E2E 中的 Node loader specifier 与 pinned tsconfig env。

#### Focused verification

```powershell
.\.venv\Scripts\python.exe -m pytest webapp\tests\test_coach_runtime.py webapp\tests\test_coach_tool_runtime.py -q
```

Coach runtime 全量测试必须显式使用：

```text
PI_SOURCE_DIR=<repo>/third_party/pi
TSX_TSCONFIG_PATH=<repo>/third_party/pi/tsconfig.json
PYTHON_BIN=<repo>/.venv/Scripts/python.exe
node --import=<tsx loader file URL> --test webapp/coach-runtime/test/*.test.ts
```

预期：不依赖 Pi `dist`，Coach runtime 全量通过；Python focused tests 不再出现 `ERR_UNSUPPORTED_ESM_URL_SCHEME`、missing `dist/compat.js` 或 Windows Python path failure。

#### Stop rule

- 需要修改 `third_party/pi` 源码、package exports 或提交生成的 `dist`；
- 需要引入 coding-agent、filesystem、shell 或额外产品 capability；
- 需要改变 Provider、auth、turn schema、fallback 或 secret-redaction 语义；
- Node 22+、file URL 与 pinned tsconfig 仍不能稳定加载 source；
- 需要扩大 Allowed files。

### Workstream B — Tauri Windows resource compile

> **2026-07-15 Sol scope 裁决：** `icon.ico` 补齐后，`cargo clippy --locked --all-targets -- -D warnings` 进入既有 Windows Raw Input 代码，并在 `raw_input.rs:1019` 唯一报告 `clippy::unnecessary_cast`。当前 `winapi::shared::minwindef::MAX_PATH` 已为 `usize`；本 Workstream 仅追加授权删除该行的 `as usize`，该类型级 no-op 不改变 fallback 值、process gate、采集行为或 Windows API 合同。任何其他 Rust source 改动或新 lint/error 仍触发 Stop rule。

#### Allowed files

- Create: `webapp/frontend/src-tauri/icons/icon.ico`
- Modify: `webapp/frontend/src-tauri/src/raw_input.rs:1019`，仅将 `.unwrap_or(MAX_PATH as usize)` 改为 `.unwrap_or(MAX_PATH)`

#### Tests first

1. 在 Windows MSVC toolchain 上运行 `cargo check --locked --all-targets`，确认当前唯一首要失败为缺少 `icons/icon.ico`。
2. 使用仓库已有 `webapp/frontend/src-tauri/icons/icon.png` 作为输入，在系统临时目录运行 Tauri icon generator；只复制生成的 `icon.ico` 到 Allowed file，不提交其他平台图标。
3. 验证 `icon.png` byte identity 未变化，新增 ICO 可被 Tauri build script 读取。
4. 运行 `cargo clippy --locked --all-targets -- -D warnings`，确认修复前唯一 project-source lint 为 `raw_input.rs:1019` 的 `clippy::unnecessary_cast`；删除该 cast 后重新运行完整 Rust focused verification，预期无 warning/error。

#### Focused verification

```powershell
cargo +stable-x86_64-pc-windows-msvc fmt --check
cargo +stable-x86_64-pc-windows-msvc check --locked --all-targets
cargo +stable-x86_64-pc-windows-msvc test --locked --all-targets
cargo +stable-x86_64-pc-windows-msvc clippy --locked --all-targets -- -D warnings
```

#### Stop rule

- ICO 补齐后出现必须修改 Rust/Tauri source、`tauri.conf.json` 或其他资源的新失败；
- Tauri 要求扩大到 bundle、installer、签名、updater 或正式品牌设计；
- 需要覆盖或重新设计现有 `icon.png`；
- 生成器无法只保留一个合法 `icon.ico`；
- `raw_input.rs:1019` 除删除 `as usize` 外还需要任何行为、类型或控制流修改；
- 删除该 cast 后出现任何其他 Rust source lint/error；
- 需要扩大 Allowed files。

## Task 2 — Windows 产品 Gate 与文档闭合

> 依赖 Task 1 的两个 workstream 全部完成；不得与 Task 1 并行。

### Allowed files

- `docs/DEVELOPMENT.md`
- `docs/PROGRESS.md`

### Verification

- Python full suite 与 `compileall`；
- Coach runtime 全量测试，使用 Windows `PYTHON_BIN`、pinned `PI_SOURCE_DIR` 与 `TSX_TSCONFIG_PATH`；
- Pi `packages/ai` suite；
- frontend `type-check` 与 adapter tests；正式 route 尚未重建时，`npm run build` 明确记录为未闭合而非伪通过；
- Tauri MSVC `fmt/check/test/clippy`；
- `git diff --check`、active plan/spec index consistency、`AGENTS.md` / `CLAUDE.md` byte parity 与最终 `git status --short`；
- GUI、真实 KovaaK、Raw Input 和高 polling-rate Gate 未运行时逐项报告。

### Pi verification boundary

| Suite | 当前分类 | 理由 |
|---|---|---|
| `packages/ai` | 产品 Gate | Provider/model/auth 被 Coach runtime 直接使用 |
| 实际 Agent turn + Coach runtime tests | 产品 Gate | 产品真实构造 pinned Pi `Agent` |
| `packages/agent` 的 NodeExecutionEnv/skills/prompt harness failures | upstream / non-gate | 当前产品不注册这些 harness capability |
| `packages/coding-agent` failures | upstream / non-gate | Architecture 与 import-boundary 明确禁止产品导入 |
| Pi 全仓全绿 | 非当前 Gate | 会把未采纳的 CLI/TUI/filesystem/shell 能力错误升级为产品依赖 |

如果未来产品采用 `AgentHarness`、skills、prompt templates 或 Node filesystem env，对应 Windows path failures 必须通过新的 active Task 重新升级为 blocker。

### Stop rule

- Task 1 任一 workstream 尚未满足自身 Gate；
- 需要修改业务代码、第三方 Pi 或正式 frontend；
- 发现新的产品级 Windows blocker 但不在既有 Allowed files；
- 需要把 mock、cross-target compile 或上游非产品测试写成真实 Windows product E2E。

## Subagent 编排

- Task 1 的 Workstream A/B 文件完全不重叠，可由两个 Luna-role subagents 并行实施；主代理在合并前分别审阅 diff 与 focused tests。
- Task 2 只能在 Task 1 两个 workstream 验收后串行执行。
- subagent 长时间运行时先通过状态和进程确认是否仍在工作；未卡死时不打断。

## 全局非目标

- RefleK Task 4 worker mode dispatch；
- frontend reconstruction Task 2–7；
- Pi coding-agent / CLI / TUI / extension ecosystem 的 Windows 全仓修复；
- 正式品牌 icon、installer、签名、公证、updater；
- Windows CI 供应商与发布流水线；
- 真实 KovaaK、Raw Input、高 polling-rate 与阈值校准。

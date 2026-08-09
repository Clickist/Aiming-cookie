# Windows Internal Installer v1 Implementation Plan

> **Status: active for Tasks 1-4.** 点点于 2026-08-08 明确授权开始内测安装包施工；Tasks 必须按顺序执行并逐项验证。

**Goal:** Build an x64 NSIS installer that runs Aiming Cookie without the repository, system Python, or system Node, while leaving trusted Windows signing as an optional certificate-backed step in the same pipeline.

**Architecture:** Next produces static WebView assets. A reproducible PowerShell build prepares a PyInstaller onedir backend and a Bun-compiled Coach executable under Tauri resources. Tauri selects source runtimes in development and packaged resources in release, then NSIS bundles the complete application.

**Tech Stack:** Tauri 2, Rust, Next.js static export, PyInstaller, Bun compile, PowerShell, NSIS, SignTool.

## Frozen Decisions

- Reuse the existing Tauri parent lifecycle, loopback token, local database, Coach runtime, Pi provider catalog, and app-data ownership.
- Do not ship the repository, `.venv`, `third_party/pi/node_modules`, or require global Python/Node.
- Do not add updater, CI release automation, Microsoft Store packaging, or public download hosting in this plan.
- Do not create or trust a self-signed certificate.
- Preserve developer-mode commands and test behavior.

## Task 1 - Build Contracts And Failing Tests

### Allowed files

- this plan and `../specs/2026-08-08-windows-internal-installer-design.md`
- `docs/superpowers/plans/README.md`, `docs/superpowers/specs/README.md`
- `webapp/frontend/tests/packaging-contract.test.ts`
- `webapp/frontend/src-tauri/src/runtime.rs`

### Tests first

1. Release runtime selection resolves packaged resources and never falls back to source paths.
2. Development runtime selection preserves `AIMING_COOKIE_PROJECT_ROOT` and `AIMING_COOKIE_PYTHON` behavior.
3. Missing packaged backend or Coach executable fails before spawning either child.

### Verification

```powershell
npm.cmd --prefix webapp\frontend run test:contracts
Push-Location webapp\frontend\src-tauri
cargo +stable-x86_64-pc-windows-msvc test runtime --locked
Pop-Location
```

### Stop Rule

Stop if release mode must infer the repository path, write into resources, or silently use a system runtime.

## Task 2 - Static Frontend Export

### Allowed files

- `webapp/frontend/next.config.ts`
- `webapp/frontend/app/analysis/page.tsx`
- navigation helpers and directly corresponding tests under `webapp/frontend/`
- `webapp/frontend/src-tauri/tauri.conf.json`

### Tests first

1. Production build emits `out/index.html` plus every fixed product route.
2. Analysis navigation in Desktop resolves through the static analysis shell and preserves the numeric id.
3. Browser development behavior remains compatible.

### Verification

```powershell
npm.cmd --prefix webapp\frontend run type-check
npm.cmd --prefix webapp\frontend run build
Test-Path webapp\frontend\out\index.html
Test-Path webapp\frontend\out\analysis\index.html
```

### Stop Rule

Stop if static export requires a Next server or changes API/data contracts.

## Task 3 - Packaged Backend And Coach Runtimes

### Allowed files

- `.gitignore`
- `scripts/build-windows-runtime.ps1`
- `scripts/desktop-runtime-entry.py`
- `webapp/coach-runtime/src/pi-source.ts`
- `webapp/coach-runtime/src/knowledge-registry.ts`
- `webapp/coach-runtime/src/load-system-prompt.ts`
- `webapp/coach-runtime/start-sidecar.ts`
- directly corresponding Python/Coach tests

### Tests first

1. Packaged resource-root overrides are explicit and path-bounded.
2. Bun binary returns health, Provider catalog, profile status, and a deterministic fake Coach turn without `PI_SOURCE_DIR` or Node.
3. PyInstaller runtime emits readiness and health without repository cwd or Python.
4. Build inputs and generated outputs never include secrets or app data.

### Verification

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\build-windows-runtime.ps1
powershell.exe -ExecutionPolicy Bypass -File scripts\test-packaged-runtime.ps1
```

### Stop Rule

Stop if any runtime needs the source checkout, global executable lookup, writable installation resources, or a second Coach implementation.

## Task 4 - Tauri Resources, NSIS, Optional Signing, And Field Smoke

### Allowed files

- `webapp/frontend/src-tauri/tauri.conf.json`
- `webapp/frontend/src-tauri/src/lib.rs`
- `webapp/frontend/src-tauri/src/runtime.rs`
- `scripts/build-windows-installer.ps1`
- `scripts/test-windows-installer.ps1`
- `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`, `docs/PROGRESS.md`
- plan/spec and their indexes

### Tests first

1. Release runtime uses `app.path().resource_dir()` and validates every executable/data resource before spawning.
2. Signed mode fails early without a valid private-key certificate; unsigned mode is explicit.
3. NSIS artifact and SHA-256 are generated deterministically from the configured version.
4. Install/launch/close/uninstall smoke detects stale child processes and listeners.

### Verification

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\build-windows-installer.ps1
powershell.exe -ExecutionPolicy Bypass -File scripts\test-windows-installer.ps1
Get-AuthenticodeSignature <installer>
git diff --check
```

### Stop Rule

Stop if the installed app only works beside the repository, signing secrets would enter Git/logs, the installer cannot be cleanly removed, or the smoke leaves managed processes alive.

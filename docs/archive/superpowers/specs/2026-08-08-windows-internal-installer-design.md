# Windows Internal Installer Design

## Purpose

Freeze the minimum distribution contract for a Windows installer that can run Aiming Cookie outside the source checkout. This is an internal-test package, not a public-release claim.

## Runtime Layout

The Tauri application owns and launches two packaged child runtimes from its resource directory:

- `runtime/aiming-cookie-runtime/aiming-cookie-runtime.exe`: a PyInstaller onedir build of `webapp.backend.desktop_runtime` and its Python dependencies;
- `runtime/coach-sidecar.exe`: a Bun-compiled build of the pinned Pi-based Coach sidecar;
- `runtime/knowledge/` and `runtime/coach-system.md`: versioned data files consumed by both runtimes.

Development mode keeps the existing source-checkout behavior. Release mode must not require `AIMING_COOKIE_PROJECT_ROOT`, `AIMING_COOKIE_PYTHON`, system Python, system Node, `third_party/pi/node_modules`, or the repository working directory.

The packaged resources are generated build outputs and are not committed. The build script must recreate them from tracked source and fail if a required input is absent.

## Frontend

The Tauri WebView embeds a Next static export. Product API requests continue to use the native desktop connection and loopback token; the static export must not depend on a Next server or the development `/api` route handler.

All fixed product routes must be emitted. Analysis detail uses a static shell route with the analysis id carried in the query string in packaged navigation; the UI may continue accepting the existing dynamic route during development and compatibility tests.

## Process And Data Boundaries

- Both child runtimes bind only to `127.0.0.1`.
- Tauri remains the parent supervisor and terminates both process trees on shutdown.
- SQLite, managed media, credentials, and logs remain under the Tauri app-data directory, never under the installation directory.
- Resource paths are read-only. Runtime-generated files must not be written beside packaged executables.
- Runtime startup failure is fatal and user-visible; there is no legacy Coach or source-tree fallback in release mode.

## Installer

Tauri builds one per-user NSIS installer for x64 Windows. The installer uses the existing product identifier and icon, includes the packaged resources, and uses the normal WebView2 bootstrapper behavior.

The package version comes from the existing Tauri configuration. A SHA-256 file is emitted beside every internal installer.

## Signing

Signing is optional for internal builds but must use the same build pipeline. When a valid code-signing certificate with private key is available, the build accepts its certificate thumbprint and issuer-provided timestamp URL without committing either certificate material or secret values.

Self-signed certificates do not satisfy this contract. An unsigned package must be labelled as unsigned and may trigger SmartScreen. Public distribution remains blocked until a trusted certificate and clean-machine signature verification are available.

## Verification Gates

1. Static frontend build and fixed-route navigation pass without a Next server.
2. Python and Coach executables start with source/runtime environment variables cleared and a non-repository working directory.
3. Packaged Tauri starts, exposes the desktop runtime, loads Provider catalog, opens an existing Analysis, and can complete a real configured Provider Coach turn.
4. Closing the app leaves no managed Python/Coach process or loopback listener.
5. NSIS install, launch, close, and uninstall pass from a clean temporary user path.
6. Installer and installed executable signatures are verified when signing is enabled; otherwise both are explicitly reported unsigned.

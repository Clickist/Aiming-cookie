"""PyInstaller entry point for the Tauri-owned desktop runtime."""

import sys


def main() -> None:
    # The frozen exe is also reused as the one-shot CV worker child: the
    # backend spawns it with this argv mode because "-m module" does not work
    # under the PyInstaller bootloader.
    if len(sys.argv) >= 2 and sys.argv[1] == "--visual-worker":
        from webapp.backend.visual_worker_process import main as worker_main

        worker_main()
        return

    # Same reuse pattern for the KovaaK telemetry capture scripts (standalone
    # stdlib-only files under telemetry_capture/): "--telemetry-child <script>
    # [args...]" re-enters the frozen interpreter with the script as __main__.
    if len(sys.argv) >= 3 and sys.argv[1] == "--telemetry-child":
        from webapp.backend.telemetry_capture_service import run_telemetry_child

        run_telemetry_child(sys.argv[2], sys.argv[3:])
        return

    # [fix cp1252 2026-10-13] 冻结 bootloader 按系统 locale 初始化 stdio 且无视
    # PYTHONIOENCODING（见 1013 巴西报障分诊）：主 runtime 进程先修 utf-8 再干活。
    # 特意放在两个子进程分支之后——顶层/此处提前 import desktop_runtime 会把
    # uvicorn/FastAPI 拖进轻量子进程；--telemetry-child 的修正已在
    # run_telemetry_child 开头完成。
    from webapp.backend.desktop_runtime import force_utf8_stdio, main as runtime_main

    force_utf8_stdio()
    runtime_main()


if __name__ == "__main__":
    main()

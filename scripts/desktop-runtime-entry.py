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

    from webapp.backend.desktop_runtime import main as runtime_main

    runtime_main()


if __name__ == "__main__":
    main()

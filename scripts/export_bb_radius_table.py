"""Generate the bundled bb radius table (knowledge/scenarios/bb-radius.v1.json).

Scan every local .sce pool on this machine with the product's own parser
(``kovaak_tracker.sce_reading``) and emit, per normalized scenario name, the
per-spawned-bot MainBB radius list. The committed artifact ships with the app
and is the first lookup layer of worker bb lazy-fill
(``kovaak_tracker/bb_fill.py``); the second layer (user-local .sce) covers
scenarios absent from the table at runtime.

Pools, in priority order (all read-only; first hit wins on name collision,
every matching file is recorded in the entry's ``sources``):
1. product install local Scenarios (KOVAAK_INSTALL_DIR override, then the
   webapp config Steam discovery used by the desktop app);
2. research workspace local Scenarios (default: the FPSAimTrainer analysis
   checkout next to this repo, if present; --pool to add more);
3. Steam workshop ``content/824270`` pools of each existing Steam library root.

Keys are normalized scenario names (case/whitespace-insensitive, same rule as
``sce_reading._norm_name``); both the file stem and the .sce ``Name=`` header
register a scenario (same two-level matching as the runtime local layer).

Output is deterministic (sorted keys). Rerunning rewrites ``generated_at``;
entries only change when the underlying .sce pools change.

stdlib only; Python 3.11. Run from the repo root:
    .venv/Scripts/python.exe scripts/export_bb_radius_table.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kovaak_tracker import sce_reading  # noqa: E402
from kovaak_tracker.bb_fill import BB_RADIUS_TABLE_SCHEMA_VERSION  # noqa: E402

# 研究工作区本地场景池（read-only 参考；存在才收）。
_RESEARCH_SCENARIO_DIR = (
    Path(r"C:\Users\袜子\Desktop\FPSAimTrainer")
    / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
)
# 研究版 scenario_meta.DEFAULT_STEAM_ROOTS 同款候选（存在的才收）。
_STEAM_ROOT_CANDIDATES = (
    Path(r"E:\SteamLibrary"),
    Path(r"C:\Program Files (x86)\Steam"),
    Path(r"D:\SteamLibrary"),
    Path(r"F:\SteamLibrary"),
)
WORKSHOP_APP_ID = 824270
MAX_ENTRIES = 5000


def normalize_name(value: str) -> str:
    """与 sce_reading._norm_name 同规：大小写/空白不敏感的场景名规范化键。"""
    return " ".join(value.split()).casefold()


def discover_pools() -> list[tuple[str, Path]]:
    """优先级有序的 (pool tag, 目录) 列表；目录不存在的不收。"""
    pools: list[tuple[str, Path]] = []
    env_dir = os.environ.get("KOVAAK_INSTALL_DIR", "").strip()
    if env_dir:
        install: Path | None = Path(env_dir)
    else:
        try:
            from webapp.backend.config import resolve_kovaak_install_dir

            install = resolve_kovaak_install_dir()
        except Exception:  # noqa: BLE001 - 脚本容错：装不上 KovaaK 也不崩
            install = None
    if install is not None:
        pools.append((
            "local_scenarios",
            install / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios",
        ))
    if _RESEARCH_SCENARIO_DIR.is_dir():
        pools.append(("research_local", _RESEARCH_SCENARIO_DIR))
    roots: list[Path] = []
    if install is not None and len(install.parents) >= 2:
        roots.append(install.parents[1])
    roots.extend(_STEAM_ROOT_CANDIDATES)
    seen: set[Path] = set()
    for root in roots:
        workshop = root / "steamapps" / "workshop" / "content" / str(WORKSHOP_APP_ID)
        key = workshop.resolve()
        if not workshop.is_dir() or key in seen:
            continue
        seen.add(key)
        # workshop 布局：content/824270/<item>/<name>.sce（与 sce_reading 的
        # 候选布局同规：逐 item 目录，排序，上限 2000）。
        try:
            children = sorted(child for child in workshop.iterdir() if child.is_dir())
        except OSError:
            continue
        for child in children[:2000]:
            pools.append((f"workshop:{root.name}/{child.name}", child))
    return [(tag, pdir) for tag, pdir in pools if pdir.is_dir()]


def build_table() -> dict[str, Any]:
    """扫描全部场景池 → round_bb 半径表文档（确定性输出）。"""
    entries: dict[str, dict[str, Any]] = {}
    stats = {"files": 0, "parsed": 0, "with_radius": 0}
    for tag, pdir in discover_pools():
        try:
            files = sorted(pdir.glob("*.sce"))
        except OSError:
            continue
        for path in files:
            stats["files"] += 1
            try:
                data = path.read_bytes()
            except OSError:
                continue
            parsed = sce_reading.extract_bounding_radius_from_bytes(data)
            if parsed.get("availability") != "available":
                continue
            stats["parsed"] += 1
            if not parsed.get("radii_cm"):
                continue
            stats["with_radius"] += 1
            source = f"{tag}:{path}"
            bots = [
                {
                    "profile": bot.get("profile"),
                    "character": bot.get("character"),
                    "bb_type": bot.get("bb_type"),
                    "radius_cm": bot.get("radius_cm"),
                }
                for bot in parsed.get("bots") or []
                if isinstance(bot.get("radius_cm"), (int, float))
            ]
            if not bots:
                continue
            names = {normalize_name(path.stem), normalize_name(parsed["header_name"])}
            names.discard("")
            for name in names:
                entry = entries.get(name)
                if entry is None:
                    if len(entries) >= MAX_ENTRIES:
                        raise SystemExit(f"radius table exceeds {MAX_ENTRIES} entries")
                    entry = {
                        "scenario": parsed["header_name"] or path.stem,
                        "timescale": parsed.get("timescale"),
                        "sce_file": path.name,
                        "sources": [],
                        "bots": bots,
                    }
                    entries[name] = entry
                if source not in entry["sources"]:
                    entry["sources"].append(source)
    for entry in entries.values():
        entry["sources"].sort()
    return {
        "schema_version": BB_RADIUS_TABLE_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generator": "scripts/export_bb_radius_table.py (kovaak_tracker.sce_reading)",
        "name_key": "casefolded whitespace-normalized scenario name (sce_reading._norm_name)",
        "stats": stats,
        "entries": dict(sorted(entries.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "knowledge" / "scenarios" / "bb-radius.v1.json",
        help="output path (default: the bundled resource location)",
    )
    args = parser.parse_args()
    table = build_table()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(table, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(args.output)
    stats = table["stats"]
    print(
        f"[done] {len(table['entries'])} scenarios "
        f"({stats['files']} .sce files, {stats['parsed']} parsed, "
        f"{stats['with_radius']} with radius) -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Backfill sidecar bb.json for existing external telemetry frozen copies.

Walks ``{DATA_ROOT}/external/ext-*`` directories lacking bb.json and generates
it through the product path (builtin radius table -> local .sce, via
``kovaak_tracker.bb_fill.ensure_round_bb``), pairing each directory to its
KovaaK run scenario from authoritative sources only:

1. import-time window pairing recorded in meta.json
   (``pairing.perf_official`` + ``pairing.matched_run_ids``); with multiple
   matched runs, all must agree on the scenario name;
2. fallback: ``origin.round_file`` containing ``incr/cut-run{id}/`` -> the
   run's own record at ``{DATA_ROOT}/runs/{id}/meta.json``.

Heuristic scenario-label proposals (``scenario_proposal``) are deliberately
NOT used: a guessed name would silently fabricate a radius. Directories that
cannot be paired stay degraded (current honest behavior) and are listed in
the report with the failure reason.

Read-only on the run store; writes only bb.json into sidecar directories
(never overwrites). Run from the repo root:

    DATA_ROOT=E:/ACData .venv/Scripts/python.exe scripts/backfill_external_bb.py
    [--data-root PATH] [--report PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_CUT_RUN_RE = re.compile(r"cut-run(\d+)")


def _default_data_root() -> Path:
    override = os.environ.get("DATA_ROOT", "").strip()
    if override:
        return Path(override)
    app_data = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    return base / "Aiming Cookie"


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _run_scenario(data_root: Path, run_id: object) -> str | None:
    if not isinstance(run_id, (int, str)) or not str(run_id).isdigit():
        return None
    meta = _load_json(data_root / "runs" / str(run_id) / "meta.json")
    if meta is None:
        return None
    scenario = meta.get("scenario")
    return scenario if isinstance(scenario, str) and scenario.strip() else None


def _pair_scenario(meta: dict[str, Any], data_root: Path) -> tuple[str | None, str | None]:
    """(scenario_name, failure_reason)。只用权威配对源，启发式提案不采信。"""
    pairing = meta.get("pairing") if isinstance(meta.get("pairing"), dict) else {}
    official = pairing.get("perf_official") if isinstance(pairing.get("perf_official"), dict) else {}
    matched = pairing.get("matched_run_ids") if isinstance(pairing.get("matched_run_ids"), list) else []
    name = official.get("scenario_name")
    if isinstance(name, str) and name.strip():
        names = {
            n for n in (
                _run_scenario(data_root, run_id) or (name if run_id == official.get("run_id") else "")
                for run_id in matched
            ) if n
        }
        if len(names) == 1:
            return next(iter(names)), None
        if len(names) > 1:
            return None, "ambiguous_pairing"
        return name, None
    # 回退：origin.round_file 的 incr/cut-run{id} 路径。
    origin = meta.get("origin") if isinstance(meta.get("origin"), dict) else {}
    match = _CUT_RUN_RE.search(str(origin.get("round_file") or ""))
    if match:
        scenario = _run_scenario(data_root, match.group(1))
        if scenario:
            return scenario, None
        return None, "run_record_missing"
    return None, "no_scenario_pairing"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", type=Path, default=_default_data_root())
    parser.add_argument("--report", type=Path, default=None, help="also write the JSON report to this path")
    parser.add_argument("--dry-run", action="store_true", help="resolve and report without writing bb.json")
    args = parser.parse_args()

    from webapp.backend.config import resolve_kovaak_install_dir
    from kovaak_tracker import bb_fill

    install_dir = resolve_kovaak_install_dir()
    external_root = args.data_root / "external"
    report: dict[str, Any] = {
        "data_root": str(args.data_root),
        "install_dir": str(install_dir) if install_dir else None,
        "dry_run": args.dry_run,
        "totals": {},
        "written_by_source": {},
        "failures": {},
    }
    totals = {"scanned": 0, "already_present": 0, "written": 0, "failed": 0}
    failures: dict[str, list[dict[str, str]]] = {}

    def record_failure(ext_id: str, reason: str, scenario: str | None) -> None:
        failures.setdefault(reason, []).append(
            {"external_run_id": ext_id, **({"scenario": scenario} if scenario else {})}
        )

    for ext_dir in sorted(external_root.glob("ext-*")):
        if not ext_dir.is_dir():
            continue
        totals["scanned"] += 1
        ext_id = ext_dir.name
        if (ext_dir / "bb.json").exists():
            totals["already_present"] += 1
            continue
        meta = _load_json(ext_dir / "meta.json")
        if meta is None:
            totals["failed"] += 1
            record_failure(ext_id, "meta_unreadable", None)
            continue
        scenario, failure = _pair_scenario(meta, args.data_root)
        if scenario is None:
            totals["failed"] += 1
            record_failure(ext_id, failure or "no_scenario_pairing", None)
            continue
        origin = meta.get("origin") if isinstance(meta.get("origin"), dict) else {}
        time_meta = meta.get("time") if isinstance(meta.get("time"), dict) else {}
        window_t = (time_meta.get("t_start"), time_meta.get("t_end"))
        if args.dry_run:
            resolution = bb_fill.resolve_target_radii(scenario, install_dir=install_dir)
            if resolution.get("availability") == "available":
                totals["written"] += 1
                source = resolution["source"]
                report["written_by_source"][source] = report["written_by_source"].get(source, 0) + 1
            else:
                totals["failed"] += 1
                record_failure(ext_id, str(resolution.get("reason")), scenario)
            continue
        outcome = bb_fill.ensure_round_bb(
            ext_dir,
            scenario,
            install_dir=install_dir,
            round_number=origin.get("round") if isinstance(origin.get("round"), int) else 1,
            window_t=window_t,
        )
        status = outcome.get("status")
        if status == "written":
            totals["written"] += 1
            source = str(outcome.get("source"))
            report["written_by_source"][source] = report["written_by_source"].get(source, 0) + 1
        elif status == "already_present":
            totals["already_present"] += 1
        else:
            totals["failed"] += 1
            record_failure(ext_id, str(outcome.get("reason")), scenario)

    report["totals"] = totals
    report["failures"] = dict(sorted(failures.items()))
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

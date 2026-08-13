"""Progressive disclosure JSON output for analysis results.

After an analysis completes and the result is committed to DB, the worker calls
write_progressive_disclosure() to emit layered JSON documents to
{DATA_ROOT}/analyses/{session_id}/ so Coach can read them directly from the
file system without going through the API.

See docs/architecture-rewrite-2026-08-13.md §5 for the disclosure format.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from .file_store import _sanitize_json_value

log = logging.getLogger(__name__)

# Distribution stat keys to extract from each metric dict for metrics.json.
# ``med`` is normalised to ``median`` in the output.
_DISTRIBUTION_KEYS = ("p25", "p75", "p90", "mean", "std", "min", "max", "IQR")


def write_progressive_disclosure(
    data_root: str,
    session_id: int,
    result: dict,
    stats_path: str | None = None,
) -> None:
    """输出渐进式披露 JSON 文档到 analyses/{session_id}/ 目录。

    Writes overview.json, metrics.json, events.json, evidence.json, and
    stats.txt.  Best-effort: file output failures log a warning but never
    raise, so the analysis result (already committed via mark_done) is not
    affected.
    """
    try:
        analyses_dir = Path(data_root) / "analyses" / str(session_id)
        analyses_dir.mkdir(parents=True, exist_ok=True)

        _write_json(analyses_dir / "overview.json", _build_overview(session_id, result))
        _write_json(analyses_dir / "metrics.json", _build_metrics(result))
        _write_json(analyses_dir / "events.json", _build_events(result))
        _write_evidence(analyses_dir, session_id, result)
        if stats_path:
            _write_stats(analyses_dir, stats_path)
    except Exception:
        log.warning(
            "progressive disclosure output failed session=%s",
            session_id,
            exc_info=True,
        )


# --- helpers ---------------------------------------------------------------

def _write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(_sanitize_json_value(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_overview(session_id: int, result: dict) -> dict:
    deterministic = result.get("deterministic") or {}
    diagnosis = deterministic.get("diagnosis") or {}
    metrics = deterministic.get("metrics") or {}
    evidence = result.get("evidence") or {}
    snapshot = result.get("input_snapshot") or {}
    scenario_block = result.get("scenario") or {}

    return {
        "analysis_ref": result.get("analysis_id") or f"analysis:{session_id}",
        "scenario": snapshot.get("scenario"),
        "analysis_type": result.get("analysis_type"),
        "input_mode": result.get("input_mode"),
        "status": "done",
        "completed_at": result.get("completed_at"),
        "diagnosis": {
            "issues": list(diagnosis.get("issues") or []),
            "headline": _build_headline(diagnosis),
        },
        "metrics_summary": {
            key: _extract_metric_summary(metric)
            for key, metric in metrics.items()
            if isinstance(metric, dict)
        },
        "evidence_availability": {
            "sources": {
                name: src.get("availability")
                for name, src in (evidence.get("sources") or {}).items()
                if isinstance(src, dict)
            },
            "coverage": evidence.get("coverage"),
        },
        "scenario_info": {
            "support_status": (
                scenario_block.get("support_status")
                or deterministic.get("support_status")
            ),
            "limitations": list(deterministic.get("limitations") or []),
        },
    }


def _build_headline(diagnosis: dict) -> str:
    issues = diagnosis.get("issues") or []
    if issues and isinstance(issues[0], dict):
        for field in ("signal", "plain_language_meaning"):
            value = issues[0].get(field)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _extract_metric_summary(metric: dict) -> dict:
    return {
        "value": metric.get("value"),
        "unit": metric.get("unit"),
        "classification": metric.get("classification"),
        "availability": metric.get("availability"),
    }


def _build_metrics(result: dict) -> dict:
    metrics = (result.get("deterministic") or {}).get("metrics") or {}
    output: dict[str, dict] = {}
    for key, metric in metrics.items():
        if not isinstance(metric, dict):
            continue
        entry = {
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "metric_version": metric.get("metric_version"),
            "classification": metric.get("classification"),
            "availability": metric.get("availability"),
        }
        distribution = _extract_distribution(metric)
        if distribution:
            entry["distribution"] = distribution
        output[key] = entry
    return output


def _extract_distribution(metric: dict) -> dict:
    stats: dict[str, object] = {}
    median = metric.get("median", metric.get("med"))
    if median is not None:
        stats["median"] = median
    for key in _DISTRIBUTION_KEYS:
        value = metric.get(key)
        if value is not None:
            stats[key] = value
    return stats


def _build_events(result: dict) -> list[dict]:
    timeline = (result.get("deterministic") or {}).get("timeline") or []
    return [event for event in timeline if isinstance(event, dict)]


def _write_evidence(analyses_dir: Path, session_id: int, result: dict) -> None:
    derived = (result.get("evidence") or {}).get("derived_artifact")
    if not isinstance(derived, dict):
        return
    revision = derived.get("evidence_revision")
    if not isinstance(revision, str):
        return
    try:
        from . import evidence_store

        artifact_file = evidence_store._artifact_file(session_id, revision)
    except Exception:
        return
    if artifact_file.exists():
        shutil.copy2(artifact_file, analyses_dir / "evidence.json")


def _write_stats(analyses_dir: Path, stats_path: str) -> None:
    source = Path(stats_path)
    if source.is_file():
        shutil.copy2(source, analyses_dir / "stats.txt")

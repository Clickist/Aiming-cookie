"""Local Analysis-owned evidence artifact storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from kovaak_tracker.analysis_evidence import (
    page_normalized_outcomes,
    validate_analysis_evidence_artifact,
)

from . import db
from .workspace import session_dir


ARTIFACT_CONTRACT_VERSION = "analysis_evidence_artifact.v1"
SUPPORTED_ARTIFACT_CONTRACT_VERSIONS = {
    "analysis_evidence_artifact.v1",
    "analysis_evidence_artifact.v2",
}
_REVISION_RE = re.compile(r"^sha256:([0-9a-f]{64})$")


class EvidenceAccessError(ValueError):
    """The requested artifact is not reachable in the caller's owner scope."""


class EvidenceIntegrityError(ValueError):
    """A committed local artifact failed checksum or contract validation."""


def analysis_evidence_root(session_id: int | str) -> Path:
    return session_dir(session_id) / "derived" / "analysis_evidence"


def _revision_digest(revision: str) -> str:
    match = _REVISION_RE.fullmatch(revision)
    if match is None:
        raise EvidenceAccessError("evidence revision is invalid")
    return match.group(1)


def _artifact_dir(session_id: int | str, revision: str) -> Path:
    return analysis_evidence_root(session_id) / "revisions" / _revision_digest(revision)


def _artifact_file(session_id: int | str, revision: str) -> Path:
    return _artifact_dir(session_id, revision) / "artifact.json"


def _manifest_file(session_id: int | str, revision: str) -> Path:
    return _artifact_dir(session_id, revision) / "manifest.json"


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_ref_payload(
    *,
    artifact_ref: str,
    revision: str,
    contract_version: str,
    checksum: str,
    size_bytes: int,
) -> dict:
    return {
        "artifact_ref": artifact_ref,
        "evidence_revision": revision,
        "contract_version": contract_version,
        "checksum_sha256": checksum,
        "size_bytes": size_bytes,
    }


def _validate_safe_ref(safe_ref: object, *, session_id: int | str) -> dict:
    fields = {
        "artifact_ref", "evidence_revision", "contract_version",
        "checksum_sha256", "size_bytes",
    }
    if not isinstance(safe_ref, dict) or set(safe_ref) != fields:
        raise EvidenceAccessError("evidence artifact ref is invalid")
    revision = safe_ref.get("evidence_revision")
    digest = _revision_digest(revision)
    expected_ref = f"analysis:{session_id}:evidence:{digest[:24]}"
    if safe_ref.get("artifact_ref") != expected_ref:
        raise EvidenceAccessError("evidence artifact ref does not match analysis")
    if safe_ref.get("contract_version") not in SUPPORTED_ARTIFACT_CONTRACT_VERSIONS:
        raise EvidenceAccessError("evidence artifact contract version is unsupported")
    if safe_ref.get("checksum_sha256") != digest:
        raise EvidenceAccessError("evidence checksum does not match revision")
    size = safe_ref.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise EvidenceAccessError("evidence artifact size is invalid")
    return dict(safe_ref)


def write_analysis_evidence_artifact(
    *, session_id: int, owner_id: str, artifact: dict,
) -> dict:
    """Validate and atomically commit one immutable evidence revision."""
    validated = validate_analysis_evidence_artifact(artifact)
    analysis_ref = f"analysis:{session_id}"
    if validated["analysis_ref"] != analysis_ref:
        raise ValueError("analysis evidence artifact is bound to another analysis")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("analysis evidence owner is required")

    payload = _canonical_bytes(validated)
    checksum = hashlib.sha256(payload).hexdigest()
    revision = f"sha256:{checksum}"
    artifact_ref = f"{analysis_ref}:evidence:{checksum[:24]}"
    safe_ref = _safe_ref_payload(
        artifact_ref=artifact_ref,
        revision=revision,
        contract_version=validated["schema_version"],
        checksum=checksum,
        size_bytes=len(payload),
    )
    root = analysis_evidence_root(session_id)
    revisions = root / "revisions"
    final_dir = revisions / checksum
    if final_dir.exists():
        validate_committed_analysis_evidence(
            session_id=session_id,
            owner_id=owner_id,
            safe_ref=safe_ref,
        )
        return safe_ref

    revisions.mkdir(parents=True, exist_ok=True)
    temp_dir = root / f".tmp-{uuid.uuid4().hex}"
    manifest = {
        "schema_version": "analysis_evidence_manifest.v1",
        "owner_id": owner_id,
        "analysis_ref": analysis_ref,
        **safe_ref,
    }
    committed = False
    try:
        temp_dir.mkdir()
        with (temp_dir / "artifact.json").open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        with (temp_dir / "manifest.json").open("wb") as handle:
            handle.write(_canonical_bytes(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        if final_dir.exists():
            shutil.rmtree(temp_dir)
        else:
            os.replace(temp_dir, final_dir)
            committed = True
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        if committed and final_dir.exists():
            shutil.rmtree(final_dir, ignore_errors=True)
        raise
    return safe_ref


def validate_committed_analysis_evidence(
    *, session_id: int, owner_id: str, safe_ref: dict,
) -> dict:
    """Validate immutable metadata, checksum and typed artifact content."""
    safe_ref = _validate_safe_ref(safe_ref, session_id=session_id)
    revision = safe_ref["evidence_revision"]
    artifact_file = _artifact_file(session_id, revision)
    manifest_file = _manifest_file(session_id, revision)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        payload = artifact_file.read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceIntegrityError("evidence artifact is incomplete") from exc
    expected_manifest = {
        "schema_version": "analysis_evidence_manifest.v1",
        "owner_id": owner_id,
        "analysis_ref": f"analysis:{session_id}",
        **safe_ref,
    }
    if manifest != expected_manifest:
        raise EvidenceAccessError("evidence artifact owner or analysis binding is invalid")
    checksum = hashlib.sha256(payload).hexdigest()
    if checksum != safe_ref["checksum_sha256"] or len(payload) != safe_ref["size_bytes"]:
        raise EvidenceIntegrityError("evidence artifact checksum mismatch")
    try:
        decoded = json.loads(payload)
        validated = validate_analysis_evidence_artifact(decoded)
    except (json.JSONDecodeError, ValueError) as exc:
        raise EvidenceIntegrityError("evidence artifact contract validation failed") from exc
    if validated["analysis_ref"] != f"analysis:{session_id}":
        raise EvidenceIntegrityError("evidence artifact analysis binding mismatch")
    if validated["schema_version"] != safe_ref["contract_version"]:
        raise EvidenceIntegrityError("evidence artifact contract version mismatch")
    return validated


async def read_analysis_evidence_artifact(
    *, owner_id: str, analysis_ref: str, artifact_ref: str, evidence_revision: str,
) -> dict:
    """Read only a terminal result's exact owner-bound evidence revision."""
    prefix = "analysis:"
    if not analysis_ref.startswith(prefix):
        raise EvidenceAccessError("analysis ref is invalid")
    session_text = analysis_ref[len(prefix):]
    if not session_text.isdigit():
        raise EvidenceAccessError("analysis ref is invalid")
    session_id = int(session_text)
    conn = await db.get_conn()
    row = await (
        await conn.execute(
            "SELECT user_id, status, result FROM sessions WHERE id=?",
            (session_id,),
        )
    ).fetchone()
    if row is None:
        raise EvidenceAccessError("analysis not found")
    if row["user_id"] != owner_id:
        raise EvidenceAccessError("analysis owner mismatch")
    if row["status"] != "done":
        raise EvidenceAccessError("analysis is not terminal")
    try:
        result = json.loads(row["result"] or "null")
    except json.JSONDecodeError as exc:
        raise EvidenceIntegrityError("analysis result is malformed") from exc
    stored_ref = ((result or {}).get("evidence") or {}).get("derived_artifact")
    if not isinstance(stored_ref, dict):
        raise EvidenceAccessError("analysis has no committed evidence artifact")
    if stored_ref.get("artifact_ref") != artifact_ref:
        raise EvidenceAccessError("artifact ref is not reachable from analysis")
    if stored_ref.get("evidence_revision") != evidence_revision:
        raise EvidenceAccessError("evidence revision is stale")
    return validate_committed_analysis_evidence(
        session_id=session_id,
        owner_id=owner_id,
        safe_ref=stored_ref,
    )


async def read_normalized_outcome_page(
    *,
    owner_id: str,
    analysis_ref: str,
    artifact_ref: str,
    descriptor: dict,
) -> dict:
    """Read one exact internal page after owner/revision/query revalidation."""
    if not isinstance(descriptor, dict):
        raise EvidenceAccessError("outcome page descriptor is invalid")
    if (
        descriptor.get("owner_id") != owner_id
        or descriptor.get("analysis_ref") != analysis_ref
    ):
        raise EvidenceAccessError("outcome page descriptor owner mismatch")
    revision = descriptor.get("evidence_revision")
    if not isinstance(revision, str):
        raise EvidenceAccessError("outcome page descriptor revision is invalid")
    artifact = await read_analysis_evidence_artifact(
        owner_id=owner_id,
        analysis_ref=analysis_ref,
        artifact_ref=artifact_ref,
        evidence_revision=revision,
    )
    records = artifact["normalized_outcome_records"]
    segment_bounds = None
    if descriptor.get("scope") == "evidence_segment":
        segment_ref = descriptor.get("segment_ref")
        segment = next(
            (
                item
                for item in artifact["evidence_segments"]
                if item["segment_id"] == segment_ref
            ),
            None,
        )
        if segment is None:
            raise EvidenceAccessError("outcome page segment is not reachable")
        segment_bounds = (segment["start_ms"], segment["end_ms"])
    elif descriptor.get("scope") != "whole_run":
        raise EvidenceAccessError("outcome page scope is invalid")
    try:
        return page_normalized_outcomes(
            records,
            analysis_ref=analysis_ref,
            canonical_time_window_ref=f"{analysis_ref}:canonical-window",
            descriptor=descriptor,
            segment_bounds=segment_bounds,
        )
    except ValueError as exc:
        raise EvidenceAccessError("outcome page descriptor is stale or invalid") from exc


def remove_analysis_evidence_workspace(session_id: int | str) -> bool:
    root = analysis_evidence_root(session_id)
    if not root.is_dir():
        return False
    shutil.rmtree(root)
    return True


def analysis_evidence_manifest_entry(
    safe_ref: dict, *, derived_from: list[str],
) -> dict:
    return {
        "id": safe_ref["artifact_ref"],
        "kind": "analysis_evidence",
        "source": "analysis",
        "availability": "available",
        "ownership": "analysis",
        "managed": True,
        "local_only": True,
        "status": "committed",
        "format_version": safe_ref["contract_version"],
        "checksum": safe_ref["checksum_sha256"],
        "revision": safe_ref["evidence_revision"],
        "size_bytes": safe_ref["size_bytes"],
        "derived_from": list(derived_from),
    }


__all__ = [
    "ARTIFACT_CONTRACT_VERSION", "SUPPORTED_ARTIFACT_CONTRACT_VERSIONS",
    "EvidenceAccessError", "EvidenceIntegrityError",
    "analysis_evidence_manifest_entry", "analysis_evidence_root",
    "read_analysis_evidence_artifact", "read_normalized_outcome_page",
    "remove_analysis_evidence_workspace",
    "validate_committed_analysis_evidence", "write_analysis_evidence_artifact",
]

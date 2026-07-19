from __future__ import annotations

import datetime
import logging
import os
import shutil
from pathlib import Path as FilePath
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, Form, UploadFile, File, Header, HTTPException, Path, Request
from fastapi.responses import FileResponse

from . import (
    benchmark_store,
    coach_commands,
    coach_runtime,
    coach_store,
    config,
    provider_auth,
    provider_commands,
    provider_store,
    db,
    history_trends,
    kovaak_run_store,
    queue,
)
from .auth import get_request_user_id, require_desktop_token
from .coach_service import run_chat_turn
from .coach_context import project_coach_diagnostic_context
from .health import build_coach_runtime_status
from .contracts import UnsupportedContractVersion, analysis_result_to_coach_report
from .queue import (
    RetryNotAllowed,
    SessionForbidden,
    SessionNotDeletable,
    SessionNotFound,
)
from .schemas import (
    AnalyzePathsRequest,
    AnalyzeResponse,
    BenchmarkRecordCreate,
    BenchmarkRecordListResponse,
    BenchmarkRecordOut,
    KovaaKAnalysisRequest,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    CoachAnalysisRefOut,
    CoachPrimaryAttachRequest,
    CoachPrimaryAttachResponse,
    CoachPrimaryMessageRequest,
    CoachPrimaryMessageResponse,
    CoachPrimaryResponse,
    CoachProductCommandResult,
    CoachRuntimeStatusResponse,
    ProviderProfileCreate,
    ProviderApiKeyRequest,
    ProviderAuthorizeRequest,
    ProviderAuthInputRequest,
    ProviderAuthOperationOut,
    ProviderProfileDeleteResponse,
    ProviderProfileListResponse,
    ProviderProfileOut,
    ProviderProfilePatch,
    ProviderProfileStatusResponse,
    ProviderRevokeResponse,
    RunEvidenceRemovalResponse,
    CoachThreadMessageOut,
    CoachThreadOut,
    DeleteSessionResponse,
    KovaaKRunItem,
    KovaaKRunListItem,
    KovaaKRunListResponse,
    HistoryTrendResponse,
    SessionListItem,
    SessionListResponse,
    SessionStatus,
    StorageResponse,
    StorageCategoryTotals,
    StorageSessionItem,
    Timeline,
    TimelineEvent,
)
from .workspace import (
    UploadSizeExceeded,
    copy_path_to_path,
    remove_session_workspace,
    session_dir,
    stream_upload_to_path,
)

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)

# user_id 经 auth.get_request_user_id 校验(路径安全字符集)。
_ALLOWED_VIDEO_EXTS = {".mp4"}
_ALLOWED_CSV_EXTS = {".csv"}


def _coach_tool_bridge_endpoint(request: Request) -> str | None:
    """Return the local product route only when this request has a loopback origin."""
    if request.url.hostname not in {"127.0.0.1", "localhost"} or request.url.port is None:
        return None
    return str(request.base_url).rstrip("/") + "/api/coach/tools/execute"


def _require_upload_disk_space(required_bytes: int = 0) -> None:
    """Reject writes when the managed data volume lacks reserve plus the incoming bytes."""
    config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(config.DATA_ROOT)
    required = config.MIN_FREE_DISK_BYTES + required_bytes
    if usage.free < required:
        need_mb = required // (1024 * 1024)
        raise HTTPException(
            507,
            f"数据盘可用空间不足，无法接收上传（需至少 {need_mb}MB 空闲）",
        )


def _validate_local_input_path(raw_path: str, *, allowed_exts: set[str], label: str):
    path = os.path.abspath(raw_path)
    if not os.path.isabs(raw_path) or not os.path.isfile(path):
        raise HTTPException(400, f"{label} 路径必须是存在的绝对普通文件")
    if not os.access(path, os.R_OK):
        raise HTTPException(400, f"{label} 文件不可读")
    ext = os.path.splitext(path)[1].lower()
    if ext not in allowed_exts:
        allowed = ", ".join(sorted(allowed_exts))
        raise HTTPException(400, f"{label} 扩展名不支持（仅 {allowed}）")
    return os.path.realpath(path)


def _assert_session_owner(s: dict, x_user_id: str) -> None:
    """IDOR 防护:校验 session 属于调用者。v1 最小方案(X-User-Id 自报无签名,
    防 session_id 枚举读他人数据/花他人 budget);切片 3 换 Clerk session token
    + 服务端验签后由鉴权中间件取代。"""
    if s["user_id"] != x_user_id:
        raise HTTPException(403, "无权访问此 session")


async def _get_owned_session(session_id: int, x_user_id: str) -> dict:
    try:
        s = await queue.get_session(session_id)
    except UnsupportedContractVersion as exc:
        log.error(
            "unsupported analysis contract session_id=%s version=%s",
            session_id,
            exc,
        )
        raise HTTPException(500, "分析结果版本不受支持") from exc
    if s is None:
        raise HTTPException(404, "session 不存在")
    _assert_session_owner(s, x_user_id)
    return s



async def _update_session_input_paths(
    session_id: int,
    video_path: str,
    csv_path: str,
) -> None:
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET video_path=?, csv_path=? WHERE id=?",
        (video_path, csv_path, session_id),
    )
    await conn.commit()


async def _abort_uploading_session(session_id: int) -> None:
    try:
        remove_session_workspace(session_id)
    except OSError:
        log.exception("incomplete workspace cleanup failed session_id=%s", session_id)
    conn = await db.get_conn()
    await conn.execute(
        "DELETE FROM sessions WHERE id=? AND status='uploading'",
        (session_id,),
    )
    await conn.commit()


@router.post("/desktop/analyze-paths", response_model=AnalyzeResponse)
async def analyze_paths(
    request: AnalyzePathsRequest,
    _: None = Depends(require_desktop_token),
):
    """Import desktop-selected local files into a managed session workspace."""
    user_id = config.DESKTOP_LOCAL_PROFILE
    if await queue.has_active(user_id):
        raise HTTPException(429, "已有分析进行中,等完成再提交")

    video_path = _validate_local_input_path(
        request.video_path, allowed_exts=_ALLOWED_VIDEO_EXTS, label="视频",
    )
    csv_path = _validate_local_input_path(
        request.csv_path, allowed_exts=_ALLOWED_CSV_EXTS, label="CSV",
    )
    video_size = os.path.getsize(video_path)
    csv_size = os.path.getsize(csv_path)
    _require_upload_disk_space(video_size + csv_size)

    sid = await queue.enqueue(
        user_id, "", "", cm_per_360=request.cm_per_360, fov=request.fov, status="uploading",
    )
    workspace = session_dir(sid)
    managed_video = workspace / "video.mp4"
    managed_csv = workspace / "stats.csv"
    try:
        copy_path_to_path(FilePath(video_path), managed_video)
        copy_path_to_path(FilePath(csv_path), managed_csv)
        await _update_session_input_paths(sid, str(managed_video), str(managed_csv))
        if not await queue.finish_upload(sid):
            raise HTTPException(409, "上传状态已失效，请重新提交")
    except HTTPException:
        await _abort_uploading_session(sid)
        raise
    except Exception:
        await _abort_uploading_session(sid)
        raise
    return AnalyzeResponse(session_id=sid)


@router.get("/storage", response_model=StorageResponse)
async def get_storage(_: None = Depends(require_desktop_token)):
    sessions = await queue.list_storage_sessions(config.DESKTOP_LOCAL_PROFILE)
    run_usage = await kovaak_run_store.run_storage_usage(
        config.DESKTOP_LOCAL_PROFILE, config.DATA_ROOT,
    )
    categories = StorageCategoryTotals(
        analysis_artifacts_bytes=sum(
            session["workspace_bytes"] for session in sessions
        ),
        **run_usage,
    )
    return StorageResponse(
        total_bytes=sum(categories.model_dump().values()),
        categories=categories,
        sessions=[StorageSessionItem(**session) for session in sessions],
    )


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    video: UploadFile = File(...),
    csv: UploadFile = File(...),
    cm_per_360: Optional[float] = Form(default=None),
    fov: Optional[float] = Form(default=None),
    x_user_id: str = Depends(get_request_user_id),
):
    """接收 flicking 视频 + Stats CSV,入队异步分析。

    限制:单用户同时 1 个 job(并发防滥用);视频 100MB 上限。
    user_id 由 get_request_user_id 解析(dev: X-User-Id; trust: 反代用户头)。
    """
    if await queue.has_active(x_user_id):
        raise HTTPException(429, "已有分析进行中,等完成再提交")

    if video.size is not None and video.size > config.MAX_VIDEO_BYTES:
        raise HTTPException(413, "视频超过 100MB 限制")
    if csv.size is not None and csv.size > config.MAX_CSV_BYTES:
        raise HTTPException(
            413,
            f"CSV 超过 {config.MAX_CSV_BYTES // 1024 // 1024}MB 限制",
        )

    video_ext = os.path.splitext(video.filename or "video.mp4")[1].lower()
    if video_ext not in _ALLOWED_VIDEO_EXTS:
        raise HTTPException(400, f"视频扩展名不支持(仅 .mp4): {video_ext or '(无)'}")
    csv_ext = os.path.splitext(csv.filename or "stats.csv")[1].lower()
    if csv_ext not in _ALLOWED_CSV_EXTS:
        raise HTTPException(400, f"CSV 扩展名不支持(仅 .csv): {csv_ext or '(无)'}")

    _require_upload_disk_space()

    sid = await queue.enqueue(
        x_user_id,
        "",
        "",
        cm_per_360=cm_per_360,
        fov=fov,
        status="uploading",
    )
    ws = session_dir(sid)
    ws.mkdir(parents=True, exist_ok=True)
    video_path = ws / f"video{video_ext}"
    csv_path = ws / f"stats{csv_ext}"
    await _update_session_input_paths(sid, str(video_path), str(csv_path))

    try:
        await stream_upload_to_path(
            video,
            video_path,
            max_bytes=config.MAX_VIDEO_BYTES,
            field="video",
        )
        await stream_upload_to_path(
            csv,
            csv_path,
            max_bytes=config.MAX_CSV_BYTES,
            field="csv",
        )
    except UploadSizeExceeded as exc:
        await _abort_uploading_session(sid)
        if exc.field == "video":
            raise HTTPException(413, "视频超过 100MB 限制") from exc
        raise HTTPException(
            413,
            f"CSV 超过 {config.MAX_CSV_BYTES // 1024 // 1024}MB 限制",
        ) from exc
    except HTTPException:
        await _abort_uploading_session(sid)
        raise
    except Exception:
        await _abort_uploading_session(sid)
        raise

    if not await queue.finish_upload(sid):
        await _abort_uploading_session(sid)
        raise HTTPException(409, "上传状态已失效，请重新提交")

    return AnalyzeResponse(session_id=sid)


def _session_status_response(s: dict, *, history: dict | None = None) -> SessionStatus:
    return SessionStatus(
        id=s["id"],
        status=s["status"],
        result=s["result"],
        error=s["error"],
        llm_cost_cny=float(s["llm_cost_cny"] or 0),
        created_at=s["created_at"],
        attempts=int(s["attempts"] or 0),
        max_attempts=int(s["max_attempts"] or 1),
        worker_id=s.get("worker_id"),
        started_at=s.get("started_at"),
        finished_at=s.get("finished_at"),
        analysis_type=s.get("analysis_type") or "flicking",
        input_mode=s.get("input_mode") or "video_fallback",
        kovaak_run_id=s.get("kovaak_run_id"),
        history=history,
    )


def _raise_product_command_error(
    result: dict,
    *,
    status_overrides: Optional[dict[str, int]] = None,
) -> None:
    if result.get("status") == "succeeded":
        return
    error = result.get("warning_or_error") or {}
    code = error.get("code")
    message = error.get("message") or "Product command failed"
    default_statuses = {
        "idempotency_key_required": 400,
        "invalid_idempotency_key": 400,
        "invalid_parameters": 400,
        "forbidden": 403,
        "not_found": 404,
        "deleted": 404,
        "internal_error": 500,
    }
    status = (status_overrides or {}).get(code, default_statuses.get(code, 409))
    raise HTTPException(status, message)


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    x_user_id: str = Depends(get_request_user_id),
):
    """当前用户的分析列表(新→旧)。不返回完整 result。"""
    rows = await coach_commands.list_history(x_user_id)
    return SessionListResponse(sessions=[SessionListItem(**row) for row in rows])


@router.get(
    "/history/trends/{metric_key}",
    response_model=HistoryTrendResponse,
    response_model_exclude_none=True,
)
async def get_history_trend(
    metric_key: str = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        trend = await coach_commands.history_trend(x_user_id, metric_key)
    except coach_commands.ProductCommandError as exc:
        raise HTTPException(400, exc.message) from exc
    return HistoryTrendResponse(**trend)


@router.get("/benchmarks", response_model=BenchmarkRecordListResponse)
async def list_benchmarks(x_user_id: str = Depends(get_request_user_id)):
    records = await benchmark_store.list_records(x_user_id)
    return BenchmarkRecordListResponse(
        records=[BenchmarkRecordOut(**record) for record in records]
    )


@router.post("/benchmarks", response_model=BenchmarkRecordOut)
async def create_benchmark(
    request: BenchmarkRecordCreate,
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        record = await benchmark_store.create_record(
            x_user_id, request.model_dump(),
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return BenchmarkRecordOut(**record)


@router.get("/kovaak-runs", response_model=KovaaKRunListResponse)
async def list_kovaak_runs(_: None = Depends(require_desktop_token)):
    runs = await coach_commands.list_runs(config.DESKTOP_LOCAL_PROFILE)
    return KovaaKRunListResponse(runs=[KovaaKRunListItem(**run) for run in runs])


@router.get("/kovaak-runs/{run_id}", response_model=KovaaKRunItem)
async def get_kovaak_run(
    run_id: int = Path(...),
    _: None = Depends(require_desktop_token),
):
    try:
        run = await coach_commands.get_run(config.DESKTOP_LOCAL_PROFILE, run_id)
    except coach_commands.ProductCommandError as exc:
        status = 403 if exc.code == "forbidden" else 404
        raise HTTPException(status, exc.message) from exc
    return KovaaKRunItem(**run)


@router.delete(
    "/kovaak-runs/{run_id}/evidence/{evidence_kind}",
    response_model=RunEvidenceRemovalResponse,
)
async def remove_kovaak_run_evidence(
    run_id: int = Path(...),
    evidence_kind: Literal["video", "raw"] = Path(...),
    _: None = Depends(require_desktop_token),
):
    try:
        result = await kovaak_run_store.remove_run_evidence(
            run_id,
            config.DESKTOP_LOCAL_PROFILE,
            evidence_kind,
            config.DATA_ROOT,
        )
    except LookupError as exc:
        raise HTTPException(404, "KovaaK run 不存在") from exc
    except PermissionError as exc:
        raise HTTPException(403, "无权访问此 Run") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "Run evidence 无法安全移除") from exc
    return RunEvidenceRemovalResponse(**result)


@router.post("/kovaak-runs/{run_id}/analyze", response_model=AnalyzeResponse)
async def analyze_kovaak_run(
    request: KovaaKAnalysisRequest,
    run_id: int = Path(...),
    _: None = Depends(require_desktop_token),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Freeze one owned Run through the shared product-command application handler."""
    video_source = None
    if request.video_path:
        video_source = FilePath(_validate_local_input_path(
            request.video_path, allowed_exts=_ALLOWED_VIDEO_EXTS, label="视频",
        ))
    result = await coach_commands.execute_trusted_analysis_create(
        config.DESKTOP_LOCAL_PROFILE,
        run_id,
        input_mode=request.input_mode,
        cm_per_360=request.cm_per_360,
        fov=request.fov,
        managed_video_source=video_source,
        idempotency_key=idempotency_key,
    )
    _raise_product_command_error(
        result,
        status_overrides={
            "active_analysis": 429,
            "input_setup_failed": 500,
            "not_found": 409,
        },
    )
    created = result.get("result") or {}
    session_id = created.get("session_id")
    if not isinstance(session_id, int):
        raise HTTPException(500, "Analysis creation returned an invalid result")
    return AnalyzeResponse(session_id=session_id)


@router.get("/sessions/{session_id}", response_model=SessionStatus)
async def get_session(
    session_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    """查询分析状态/结果(queued / running / done / failed)。"""
    s = await _get_owned_session(session_id, x_user_id)
    history = await history_trends.analysis_history_detail(s)
    return _session_status_response(s, history=history)


@router.delete("/sessions/{session_id}", response_model=DeleteSessionResponse)
async def delete_session(
    session_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    """Delete a terminal analysis and only its managed workspace copy."""
    try:
        out = await queue.delete_session(session_id, x_user_id)
    except SessionNotFound as exc:
        raise HTTPException(404, "session 不存在") from exc
    except SessionForbidden as exc:
        raise HTTPException(403, "无权访问此 session") from exc
    except SessionNotDeletable as exc:
        raise HTTPException(409, exc.message) from exc
    return DeleteSessionResponse(**out)


@router.post("/sessions/{session_id}/retry", response_model=SessionStatus)
async def retry_session(
    session_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """将 failed session 通过共享产品命令处理器重新入队。"""
    result = await coach_commands.execute_product_command(
        x_user_id,
        {
            "command_name": "analysis.retry",
            "parameters": {"analysis_ref": f"analysis:{session_id}"},
            "idempotency_key": idempotency_key,
        },
        authorization_source="explicit_user_request",
    )
    _raise_product_command_error(result)
    session = await queue.get_session(session_id)
    if session is None:  # Defensive: retry handler succeeded only for an existing session.
        raise HTTPException(404, "session 不存在")
    return _session_status_response(session)




async def _diagnosis_from_done_session(s: dict):
    result = s.get("result") or {}
    if not isinstance(result, dict):
        raise HTTPException(409, "诊断结果缺失,暂不可对话")
    context = project_coach_diagnostic_context(result)
    analysis_ref = context.get("analysis_ref")
    if isinstance(analysis_ref, dict) and analysis_ref.get("analysis_id") is None:
        analysis_ref["analysis_id"] = f"analysis:{s['id']}"
    diagnosis = context.get("diagnosis") or {}
    if not diagnosis.get("profile") and not diagnosis.get("summary") and not diagnosis.get("issues"):
        raise HTTPException(409, "诊断结果缺失,暂不可对话")
    return context


def _coach_thread_message_out(m: dict) -> CoachThreadMessageOut:
    return CoachThreadMessageOut(
        id=int(m["id"]),
        role=m["role"],
        content=m["content"],
        created_at=m["created_at"],
        legacy_session_id=m.get("legacy_session_id"),
        context=m.get("context"),
    )


def _coach_ref_out(r: dict) -> CoachAnalysisRefOut:
    status = "unavailable" if r["status"] == "deleted" else r["status"]
    return CoachAnalysisRefOut(
        id=int(r["id"]),
        analysis_session_id=r.get("analysis_session_id"),
        status=status,
        attached_at=r["attached_at"],
        deleted_at=r.get("deleted_at"),
    )


async def _build_coach_primary_response(x_user_id: str) -> CoachPrimaryResponse:
    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    tid = int(thread["id"])
    messages = await coach_store.load_messages(tid)
    refs = await coach_store.list_analysis_refs(tid)
    return CoachPrimaryResponse(
        thread=CoachThreadOut(
            id=tid,
            user_id=thread["user_id"],
            kind=thread["kind"],
            created_at=thread["created_at"],
            updated_at=thread["updated_at"],
        ),
        messages=[_coach_thread_message_out(m) for m in messages],
        refs=[_coach_ref_out(r) for r in refs],
    )


async def _assert_analysis_ref_active(
    thread_id: int,
    analysis_session_id: int,
) -> None:
    refs = await coach_store.list_analysis_refs(thread_id)
    for r in refs:
        if r.get("analysis_session_id") == analysis_session_id:
            if r["status"] == "deleted":
                raise HTTPException(409, "分析已删除,不可作为对话上下文")
            return


async def _ensure_done_analysis_attached(
    x_user_id: str,
    thread_id: int,
    analysis_session_id: int,
) -> dict:
    await _assert_analysis_ref_active(thread_id, analysis_session_id)
    s = await _get_owned_session(analysis_session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成,不可附加")
    await coach_store.attach_analysis_ref(thread_id, analysis_session_id)
    return s


async def _load_session_coach_messages(x_user_id: str, session_id: int) -> list[dict]:
    await coach_store.ensure_legacy_session_messages_migrated(session_id)
    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    all_msgs = await coach_store.load_messages(int(thread["id"]))
    return [m for m in all_msgs if m.get("legacy_session_id") == session_id]


# ---------------------------------------------------------------------------
# Persistent Coach (primary thread)
# ---------------------------------------------------------------------------


@router.get("/coach/runtime-status", response_model=CoachRuntimeStatusResponse)
async def get_coach_runtime_status():
    return await build_coach_runtime_status()


def _redact_catalog_secrets(value):
    if isinstance(value, list):
        return [_redact_catalog_secrets(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_catalog_secrets(item)
            for key, item in value.items()
            if key not in {
                "credential", "api_key", "key", "access", "refresh",
                "access_token", "refresh_token", "token", "secret",
            }
        }
    return value


@router.get("/providers/catalog")
@router.get("/coach/providers/catalog")
async def get_provider_catalog():
    """Proxy the full provider/model catalog from the local Pi sidecar."""
    try:
        return _redact_catalog_secrets(await coach_runtime.fetch_provider_catalog())
    except coach_runtime.CoachRuntimeError as error:
        log.warning("provider catalog unavailable: %s", error)
        raise HTTPException(503, "Pi Provider catalog 暂不可用，请稍后重试") from error


def _provider_status_response(
    profile_id: int | None,
    runtime_profile: dict | None,
    result: dict | None = None,
) -> ProviderProfileStatusResponse:
    if runtime_profile is None:
        return ProviderProfileStatusResponse(
            profile_id=profile_id,
            configured=False,
            status="unconfigured",
            message="Coach Provider 尚未配置",
        )
    raw = result or {}
    status = raw.get("status")
    if status not in {
        "unconfigured", "auth_expired", "needs_reauth", "ready", "model_unavailable",
        "connection_failed",
    }:
        status = (
            "ready"
            if provider_store.runtime_profile_configured(runtime_profile)
            else "unconfigured"
        )
    message = str(raw.get("message") or status)
    message = coach_runtime.redact_provider_secrets(message, runtime_profile)
    return ProviderProfileStatusResponse(
        profile_id=profile_id,
        configured=bool(raw.get("configured", provider_store.runtime_profile_configured(runtime_profile))),
        status=status,
        message=message,
    )


@router.get("/provider-profiles", response_model=ProviderProfileListResponse)
async def list_provider_profiles(
    x_user_id: str = Depends(get_request_user_id),
):
    return ProviderProfileListResponse(
        profiles=await provider_store.list_profiles(x_user_id),
    )


@router.post("/provider-profiles", response_model=ProviderProfileOut)
async def create_provider_profile(
    body: ProviderProfileCreate = Body(...),
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        profile = await provider_commands.create_profile(
            x_user_id, body.model_dump(),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return profile


@router.get("/provider-profiles/status", response_model=ProviderProfileStatusResponse)
async def get_default_provider_status(
    x_user_id: str = Depends(get_request_user_id),
):
    profile = await provider_store.get_default_profile(x_user_id)
    runtime_profile = await provider_store.get_default_runtime_profile(x_user_id)
    if profile is None:
        return _provider_status_response(None, None)
    result = await coach_runtime.get_provider_profile_status(runtime_profile or {})
    return _provider_status_response(profile["id"], runtime_profile, result)


@router.get("/provider-profiles/{profile_id}/status", response_model=ProviderProfileStatusResponse)
async def get_provider_profile_status(
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    runtime_profile = await provider_store.get_runtime_profile(profile_id, x_user_id)
    if runtime_profile is None:
        raise HTTPException(404, "Provider profile 不存在")
    result = await coach_runtime.get_provider_profile_status(runtime_profile)
    return _provider_status_response(profile_id, runtime_profile, result)


@router.get("/provider-profiles/{profile_id}", response_model=ProviderProfileOut)
async def get_provider_profile(
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    profile = await provider_store.get_profile(profile_id, x_user_id)
    if profile is None:
        raise HTTPException(404, "Provider profile 不存在")
    return profile


@router.put("/provider-profiles/{profile_id}", response_model=ProviderProfileOut)
async def update_provider_profile(
    body: ProviderProfilePatch = Body(...),
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    changes = body.model_dump(exclude_unset=True)
    try:
        profile = (
            await provider_commands.update_profile(x_user_id, profile_id, changes)
            if changes
            else await provider_store.get_profile(profile_id, x_user_id)
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if profile is None:
        raise HTTPException(404, "Provider profile 不存在")
    return profile


@router.delete("/provider-profiles/{profile_id}", response_model=ProviderProfileDeleteResponse)
async def delete_provider_profile(
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    if not await provider_commands.delete_profile(x_user_id, profile_id):
        raise HTTPException(404, "Provider profile 不存在")
    return ProviderProfileDeleteResponse(deleted=True, id=profile_id)


@router.post("/provider-profiles/{profile_id}/default", response_model=ProviderProfileOut)
async def set_default_provider_profile(
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    profile = await provider_store.set_default_profile(profile_id, x_user_id)
    if profile is None:
        raise HTTPException(404, "Provider profile 不存在")
    return profile


@router.post("/provider-profiles/{profile_id}/test", response_model=ProviderProfileStatusResponse)
async def test_provider_profile_route(
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    runtime_profile = await provider_store.get_runtime_profile(profile_id, x_user_id)
    if runtime_profile is None:
        raise HTTPException(404, "Provider profile 不存在")
    result = await coach_runtime.test_provider_profile(runtime_profile)
    return _provider_status_response(profile_id, runtime_profile, result)


def _raise_provider_auth_error(error: provider_auth.ProviderAuthError) -> None:
    raise HTTPException(error.status_code, str(error)) from error


@router.get("/provider-auth/capabilities")
async def get_provider_auth_capabilities():
    try:
        return await provider_auth.fetch_capabilities()
    except provider_auth.ProviderAuthError as error:
        _raise_provider_auth_error(error)


@router.put(
    "/provider-profiles/{profile_id}/auth/api-key",
    response_model=ProviderProfileOut,
)
async def set_provider_api_key(
    body: ProviderApiKeyRequest = Body(...),
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    profile = await provider_commands.set_api_key(
        x_user_id, profile_id, body.api_key,
    )
    if profile is None:
        raise HTTPException(404, "Provider profile 不存在")
    return profile


@router.delete(
    "/provider-profiles/{profile_id}/auth/credential",
    response_model=ProviderProfileOut,
)
async def delete_provider_credential(
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    profile = await provider_commands.delete_credential(x_user_id, profile_id)
    if profile is None:
        raise HTTPException(404, "Provider profile 不存在")
    return profile


@router.post(
    "/provider-profiles/{profile_id}/auth/authorize",
    response_model=ProviderAuthOperationOut,
)
async def authorize_provider_profile(
    body: ProviderAuthorizeRequest = Body(...),
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        return await provider_commands.authorize(
            x_user_id, profile_id, mode=body.mode,
        )
    except provider_auth.ProviderAuthError as error:
        _raise_provider_auth_error(error)


@router.post(
    "/provider-profiles/{profile_id}/auth/refresh",
    response_model=ProviderAuthOperationOut,
)
async def refresh_provider_profile(
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        return await provider_commands.refresh(x_user_id, profile_id)
    except provider_auth.ProviderAuthError as error:
        _raise_provider_auth_error(error)


@router.post(
    "/provider-profiles/{profile_id}/auth/revoke",
    response_model=ProviderRevokeResponse,
)
async def revoke_provider_profile(
    profile_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    result = await provider_commands.revoke(x_user_id, profile_id)
    if result is None:
        raise HTTPException(404, "Provider profile 不存在")
    return result


@router.get(
    "/provider-auth-operations/{operation_id}",
    response_model=ProviderAuthOperationOut,
)
async def get_provider_auth_operation(
    operation_id: str = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        result = await provider_commands.get_auth_operation(x_user_id, operation_id)
    except provider_auth.ProviderAuthError as error:
        _raise_provider_auth_error(error)
    if result is None:
        raise HTTPException(404, "Provider auth operation 已中断或不存在，请重试")
    return result


@router.post(
    "/provider-auth-operations/{operation_id}/input",
    response_model=ProviderAuthOperationOut,
)
async def submit_provider_auth_input(
    body: ProviderAuthInputRequest = Body(...),
    operation_id: str = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        result = await provider_commands.submit_auth_input(
            x_user_id, operation_id, body.prompt_id, body.value,
        )
    except provider_auth.ProviderAuthError as error:
        _raise_provider_auth_error(error)
    if result is None:
        raise HTTPException(404, "Provider auth operation 已中断或不存在，请重试")
    return result


@router.post(
    "/provider-auth-operations/{operation_id}/cancel",
    response_model=ProviderAuthOperationOut,
)
async def cancel_provider_auth_operation(
    operation_id: str = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        result = await provider_commands.cancel_auth_operation(x_user_id, operation_id)
    except provider_auth.ProviderAuthError as error:
        _raise_provider_auth_error(error)
    if result is None:
        raise HTTPException(404, "Provider auth operation 已中断或不存在，请重试")
    return result


@router.post("/coach/tools/execute", response_model=CoachProductCommandResult)
async def execute_coach_tool_bridge(
    payload: dict = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    """Execute one turn-scoped Coach tool call using only its bearer principal."""
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(401, "Coach tool bridge bearer token is required")
    result = await coach_commands.execute_tool_bridge(authorization[len(prefix):], payload)
    return CoachProductCommandResult(**result)


@router.get("/coach/primary", response_model=CoachPrimaryResponse)
async def get_coach_primary(
    x_user_id: str = Depends(get_request_user_id),
):
    return await _build_coach_primary_response(x_user_id)


@router.post("/coach/primary/attach", response_model=CoachPrimaryAttachResponse)
async def attach_coach_primary_analysis(
    body: CoachPrimaryAttachRequest = Body(...),
    x_user_id: str = Depends(get_request_user_id),
):
    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    await _ensure_done_analysis_attached(
        x_user_id, int(thread["id"]), body.analysis_session_id,
    )
    refs = await coach_store.list_analysis_refs(int(thread["id"]))
    ref = next(
        r for r in refs
        if r.get("analysis_session_id") == body.analysis_session_id
    )
    return CoachPrimaryAttachResponse(ref=_coach_ref_out(ref))


@router.post("/coach/primary/messages", response_model=CoachPrimaryMessageResponse)
async def post_coach_primary_message(
    request: Request,
    body: CoachPrimaryMessageRequest = Body(...),
    x_user_id: str = Depends(get_request_user_id),
):
    user_msg = body.content.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    thread_id = int(thread["id"])
    prior = await coach_store.load_messages(thread_id)

    diagnosis = None
    cost_session_id: Optional[int] = None
    legacy_session_id: Optional[int] = None

    if body.analysis_session_id is not None:
        try:
            s = await _get_owned_session(body.analysis_session_id, x_user_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise HTTPException(409, "分析已删除,不可作为对话上下文") from exc
            raise
        await _assert_analysis_ref_active(thread_id, body.analysis_session_id)
        if s["status"] != "done":
            raise HTTPException(409, "分析未完成,不可作为对话上下文")
        await coach_store.attach_analysis_ref(thread_id, body.analysis_session_id)
        diagnosis = await _diagnosis_from_done_session(s)
        cost_session_id = body.analysis_session_id
        legacy_session_id = body.analysis_session_id

    result = await run_chat_turn(
        x_user_id=x_user_id,
        thread_id=thread_id,
        prior_messages=prior,
        user_msg_to_store=user_msg,
        diagnosis=diagnosis,
        legacy_session_id=legacy_session_id,
        cost_session_id=cost_session_id,
        tool_bridge_endpoint=_coach_tool_bridge_endpoint(request),
        desktop_token=config.DESKTOP_LAUNCH_TOKEN or None,
    )

    now_ts = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S",
    )
    out_msgs = [
        _coach_thread_message_out({
            "id": 0,
            "role": "user",
            "content": user_msg,
            "created_at": now_ts,
            "legacy_session_id": legacy_session_id,
            "context": result.context,
        }),
        _coach_thread_message_out({
            "id": 0,
            "role": "assistant",
            "content": result.assistant_content,
            "created_at": now_ts,
            "legacy_session_id": legacy_session_id,
            "context": result.context,
        }),
    ]
    return CoachPrimaryMessageResponse(
        reply=result.reply,
        notes=result.notes,
        messages=out_msgs,
    )

# ---------------------------------------------------------------------------
# Coach 多轮对话
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    session_id: int = Path(...),
    body: ChatRequest = Body(...),
    x_user_id: str = Depends(get_request_user_id),
):
    """多轮对话(兼容层):写入用户 primary thread + 附加本 session 分析 ref。"""
    s = await _get_owned_session(session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成,暂不可对话")

    user_msg = body.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    pinned = body.pinned_frame_sec
    if pinned is not None and pinned >= 0:
        mm, ss = divmod(int(pinned), 60)
        user_msg_to_store = f"[锁定 {mm}:{ss:02d}] {user_msg}"
    else:
        user_msg_to_store = user_msg

    thread = await coach_store.get_or_create_primary_thread(x_user_id)
    thread_id = int(thread["id"])
    await coach_store.attach_analysis_ref(thread_id, session_id)
    history = await _load_session_coach_messages(x_user_id, session_id)
    diagnosis = await _diagnosis_from_done_session(s)

    result = await run_chat_turn(
        x_user_id=x_user_id,
        thread_id=thread_id,
        prior_messages=history,
        user_msg_to_store=user_msg_to_store,
        diagnosis=diagnosis,
        legacy_session_id=session_id,
        cost_session_id=session_id,
        tool_bridge_endpoint=_coach_tool_bridge_endpoint(request),
        desktop_token=config.DESKTOP_LAUNCH_TOKEN or None,
    )

    now_ts = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S",
    )
    out_history = [
        ChatMessageOut(role=m["role"], content=m["content"],
                       created_at=m["created_at"])
        for m in history
    ]
    out_history.append(ChatMessageOut(
        role="user", content=user_msg_to_store, created_at=now_ts,
    ))
    out_history.append(ChatMessageOut(
        role="assistant", content=result.assistant_content, created_at=now_ts,
    ))
    return ChatResponse(
        reply=result.reply,
        history=out_history,
        notes=result.notes,
    )


@router.get("/sessions/{session_id}/chat", response_model=ChatResponse)
async def get_chat_history(
    session_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    """页面 mount 时拉历史对话(primary thread 中 legacy_session_id 过滤)。"""
    s = await _get_owned_session(session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成,暂不可对话")
    history = await _load_session_coach_messages(x_user_id, session_id)
    return ChatResponse(
        reply=None,
        history=[ChatMessageOut(role=m["role"], content=m["content"],
                                created_at=m["created_at"]) for m in history],
        notes=[],
    )


# ---------------------------------------------------------------------------
# Coach 页:视频流 + 时间轴 markers
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/video")
async def get_session_video(
    session_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    """流式返回 session 关联的视频文件(给 coach 页 <video src>)。

    路径从 sessions.video_path 取。worker 分析完后**不再删视频**(见
    worker.process_one 注释),所以 coach 页能播。文件不存在 → 404。
    """
    s = await _get_owned_session(session_id, x_user_id)
    replay = history_trends.visual_replay_capability(s)
    if replay.get("kind") != "seekable_mp4":
        raise HTTPException(404, "视频文件不存在或已归档")
    video_path = s.get("video_path") or ""
    return FileResponse(video_path, media_type="video/mp4")


@router.get("/sessions/{session_id}/timeline", response_model=Timeline)
async def get_session_timeline(
    session_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    """返回视频时间轴事件 markers。

    数据源: analysis_result_to_coach_report 后的 timeline + diagnosis.meta。
    """
    s = await _get_owned_session(session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成")

    result = s.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    coach_view = analysis_result_to_coach_report(result)
    meta = (coach_view.get("diagnosis") or {}).get("meta") or {}

    is_v2 = result.get("schema_version") == "analysis_result.v2"
    fps = None if is_v2 else 60
    if isinstance(meta.get("fps"), (int, float)) and not isinstance(meta.get("fps"), bool):
        fps = int(meta["fps"])

    # duration:优先 meta.duration_s,fallback meta.duration_frames
    duration_frames = None if is_v2 else 0
    if isinstance(meta.get("duration_frames"), (int, float)) and not isinstance(
        meta.get("duration_frames"), bool,
    ):
        duration_frames = int(meta["duration_frames"])
    elif fps is not None and isinstance(meta.get("duration_s"), (int, float)) and not isinstance(
        meta.get("duration_s"), bool,
    ):
        duration_frames = int(meta["duration_s"] * fps)

    events_raw = coach_view.get("timeline") or []
    events: list[TimelineEvent] = []
    for event in events_raw:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type") or event.get("payload_type")
        if not isinstance(event_type, str) or not event_type:
            continue
        frame = event.get("frame")
        time_s = event.get("time_s")
        relative_ms = event.get("relative_ms")
        source = event.get("source")
        events.append(
            TimelineEvent(
                frame=(
                    int(frame)
                    if isinstance(frame, (int, float)) and not isinstance(frame, bool)
                    else None
                ),
                time_s=(
                    float(time_s)
                    if isinstance(time_s, (int, float)) and not isinstance(time_s, bool)
                    else None
                ),
                relative_ms=(
                    float(relative_ms)
                    if isinstance(relative_ms, (int, float))
                    and not isinstance(relative_ms, bool)
                    else None
                ),
                type=event_type,
                label=str(event.get("label") or event_type),
                source=source if isinstance(source, str) else None,
            )
        )
    return Timeline(fps=fps, duration_frames=duration_frames, events=events)

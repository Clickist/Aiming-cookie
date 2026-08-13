from __future__ import annotations

import datetime
import asyncio
import logging
import os
import shutil
from pathlib import Path as FilePath
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, Form, UploadFile, File, Header, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import (
    analysis_service,
    benchmark_catalog,
    benchmark_store,
    calibration_profile_store,
    config,
    evidence_store,
    history_trends,
    kovaak_benchmark_provider,
    kovaak_benchmark_service,
    kovaak_connection_store,
    kovaak_run_store,
    queue,
    training_plan_store,
)
from .auth import get_request_user_id, require_desktop_token
from .read_models import (
    build_frontend_analysis_family_data_v1,
    build_frontend_analysis_data_v1,
    build_current_training_v1,
    build_capture_status_v1,
    build_product_state_v1,
    build_task_detail_v1,
    build_task_list_v1,
)
from .contracts import (
    UnsupportedContractVersion,
    analysis_result_to_coach_report,
    project_analysis_result_metric_definitions,
    project_error_for_session,
    project_evidence_segment,
)
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
    KovaaKBenchmarkSyncRequest,
    KovaaKBenchmarkSyncResponse,
    KovaaKConnectionDeleteResponse,
    KovaaKConnectionSaveRequest,
    KovaaKConnectionStatusResponse,
    KovaaKScoresResponse,
    KovaaKAnalysisRequest,
    CalibrationProfileOut,
    CalibrationProfileUpdateRequest,
    IncompleteCaptureListResponse,
    IncompleteCaptureRemovalResponse,
    RunEvidenceRemovalResponse,
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
    FrontendEvidenceSegment,
    FrontendEvidenceSegmentsResponse,
    FrontendAnalysisFamilyDataResponse,
    FrontendAnalysisDataResponse,
    CurrentTrainingResponse,
    EvidenceSegmentPlayback,
    ManagedVideoUnavailableResponse,
    TimelineEvent,
    CaptureStatusResponse,
    OnboardingStateRequest,
    ProductStateResponse,
    TaskDetailResponse,
    TaskListResponse,
)
from .workspace import (
    UploadSizeExceeded,
    copy_path_to_path,
    remove_session_workspace,
    session_dir,
    stream_upload_to_path,
)
from .native_capture_client import NativeCaptureClient, NativeCaptureRetryableError

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)

# user_id 经 auth.get_request_user_id 校验(路径安全字符集)。
_ALLOWED_VIDEO_EXTS = {".mp4"}
_ALLOWED_CSV_EXTS = {".csv"}


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
    user_id: str,
    video_path: str,
    csv_path: str,
) -> None:
    await queue.set_session_input_paths(
        session_id, user_id, video_path, csv_path,
    )


async def _abort_uploading_session(session_id: int) -> None:
    try:
        remove_session_workspace(session_id)
    except OSError:
        log.exception("incomplete workspace cleanup failed session_id=%s", session_id)
    await queue.abort_uploading_session(session_id, config.DESKTOP_LOCAL_PROFILE)


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

    try:
        sid = await queue.enqueue(
            user_id,
            "",
            "",
            cm_per_360=request.cm_per_360,
            fov=request.fov,
            profile_default=(request.profile_default.model_dump() if request.profile_default else None),
            manual_override=(request.manual_override.model_dump() if request.manual_override else None),
            status="uploading",
            require_no_active=True,
        )
    except queue.ActiveSessionExists as exc:
        raise HTTPException(429, "已有分析进行中,等完成再提交") from exc
    workspace = session_dir(sid)
    managed_video = workspace / "video.mp4"
    managed_csv = workspace / "stats.csv"
    temp_video = workspace / "video.mp4.tmp"
    temp_csv = workspace / "stats.csv.tmp"
    try:
        copy_path_to_path(FilePath(video_path), temp_video)
        copy_path_to_path(FilePath(csv_path), temp_csv)
        temp_video.replace(managed_video)
        temp_csv.replace(managed_csv)
        await _update_session_input_paths(
            sid, config.DESKTOP_LOCAL_PROFILE, str(managed_video), str(managed_csv),
        )
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


@router.get("/storage/incomplete", response_model=IncompleteCaptureListResponse)
async def get_incomplete_capture_storage(
    _: None = Depends(require_desktop_token),
):
    raw_items = await kovaak_run_store.list_incomplete_capture_items(
        config.DESKTOP_LOCAL_PROFILE, config.DATA_ROOT,
    )
    items = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in raw_items
    ]
    return IncompleteCaptureListResponse(
        total_bytes=sum(int(item["size_bytes"]) for item in items),
        items=items,
    )


@router.delete(
    "/storage/incomplete/{item_ref}",
    response_model=IncompleteCaptureRemovalResponse,
)
async def remove_incomplete_capture_storage(
    item_ref: str = Path(...),
    _: None = Depends(require_desktop_token),
):
    result = await kovaak_run_store.remove_incomplete_capture_item(
        config.DESKTOP_LOCAL_PROFILE, item_ref, config.DATA_ROOT,
    )
    if result is None:
        raise HTTPException(404, "Incomplete capture item is unavailable")
    return IncompleteCaptureRemovalResponse(**result)


@router.get("/product-state", response_model=ProductStateResponse)
async def get_product_state(x_user_id: str = Depends(get_request_user_id)):
    """Conditional-start state; unavailable is never projected as an empty state."""
    try:
        state = await queue.get_product_state(x_user_id)
    except Exception:
        log.exception("product state read failed user=%s", x_user_id)
        state = {"read_error": "database_unavailable"}
    return ProductStateResponse(**build_product_state_v1(**state))


@router.post("/product-state/onboarding", response_model=ProductStateResponse)
async def set_product_onboarding(
    request: OnboardingStateRequest,
    _: None = Depends(require_desktop_token),
    x_user_id: str = Depends(get_request_user_id),
):
    """Persist a completed desktop onboarding decision.

    Provider readiness is owned by the Coach sidecar; the desktop onboarding
    flow verifies the Provider before finishing, so this route only records
    the decision.
    """
    try:
        state = await queue.set_onboarding_state(
            x_user_id,
            completed=request.completed,
            completion_kind=request.completion_kind,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        log.exception("product onboarding write failed user=%s", x_user_id)
        return ProductStateResponse(**build_product_state_v1(
            read_error="product_state_unavailable",
        ))
    return ProductStateResponse(**build_product_state_v1(**state))


@router.get("/capture-status", response_model=CaptureStatusResponse)
async def get_capture_status(
    request: Request,
    _: None = Depends(require_desktop_token),
):
    """Aggregate native coordinator status with path-free Run attachments."""
    try:
        runs = await kovaak_run_store.list_kovaak_run_summaries(
            config.DESKTOP_LOCAL_PROFILE,
        )
        native_status = None
        if config.NATIVE_CAPTURE_CONTROL_ADDR and config.NATIVE_CAPTURE_CONTROL_SECRET:
            client = NativeCaptureClient(
                config.NATIVE_CAPTURE_CONTROL_ADDR,
                config.NATIVE_CAPTURE_CONTROL_SECRET,
            )
            native_status = await asyncio.to_thread(client.status)
        elif bool(config.NATIVE_CAPTURE_CONTROL_ADDR) != bool(
            config.NATIVE_CAPTURE_CONTROL_SECRET
        ):
            raise RuntimeError("native capture control configuration is incomplete")
        status = build_capture_status_v1(native_status=native_status, runs=runs)
    except NativeCaptureRetryableError as error:
        if (
            error.code == "capture_control_unavailable"
            and getattr(request.app.state, "desktop_shutdown_requested", False)
        ):
            log.info("capture status read skipped during desktop shutdown")
        else:
            log.error("capture status read failed: %s", error.code)
        status = build_capture_status_v1(read_error="capture_status_unavailable")
    except Exception:
        log.exception("capture status read failed")
        status = build_capture_status_v1(read_error="capture_status_unavailable")
    return CaptureStatusResponse(**status)


def _task_groups(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        group = row.get("task_group_ref") or f"task:{row.get('id')}"
        groups.setdefault(str(group), []).append(row)
    out: list[dict] = []
    for group_rows in groups.values():
        current = max(
            group_rows,
            key=lambda row: (int(row.get("attempt_number") or 1), int(row.get("id") or 0)),
        )
        out.append(build_task_detail_v1(current, attempts=group_rows))
    out.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return out


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(x_user_id: str = Depends(get_request_user_id)):
    try:
        rows = await queue.list_task_rows(x_user_id)
        projected = _task_groups(rows)
        return TaskListResponse(
            schema_version="task_list.v1",
            availability="available",
            tasks=projected,
            error=None,
        )
    except Exception:
        log.exception("task list read failed user=%s", x_user_id)
        return TaskListResponse(**build_task_list_v1(read_error="task_list_unavailable"))


@router.get("/tasks/{task_ref}", response_model=TaskDetailResponse)
async def get_task(task_ref: str, x_user_id: str = Depends(get_request_user_id)):
    try:
        rows = await queue.get_task_rows(task_ref, x_user_id)
        if not rows and task_ref.startswith("analysis:"):
            try:
                session_id = int(task_ref.split(":", 1)[1])
            except (TypeError, ValueError):
                session_id = 0
            session = await queue.get_session(session_id) if session_id > 0 else None
            if session and session.get("user_id") == x_user_id:
                rows = await queue.get_task_rows(
                    session.get("task_group_ref") or f"task:{session_id}", x_user_id,
                )
    except Exception:
        log.exception("task detail read failed user=%s", x_user_id)
        return TaskDetailResponse(**build_task_detail_v1(
            {}, read_error="task_detail_unavailable",
        ))
    if not rows:
        raise HTTPException(404, "task not found")
    current = max(
        rows,
        key=lambda row: (int(row.get("attempt_number") or 1), int(row.get("id") or 0)),
    )
    return TaskDetailResponse(**build_task_detail_v1(current, attempts=rows))


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    video: UploadFile = File(...),
    csv: UploadFile = File(...),
    cm_per_360: Optional[float] = Form(default=None),
    fov: Optional[float] = Form(default=None),
    profile_default_cm_per_360: Optional[float] = Form(default=None),
    profile_default_fov: Optional[float] = Form(default=None),
    manual_override_cm_per_360: Optional[float] = Form(default=None),
    manual_override_fov: Optional[float] = Form(default=None),
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

    try:
        sid = await queue.enqueue(
            x_user_id,
            "",
            "",
            cm_per_360=cm_per_360,
            fov=fov,
            profile_default={
                "cm_per_360": profile_default_cm_per_360,
                "fov": profile_default_fov,
            },
            manual_override=(
                {
                    "cm_per_360": manual_override_cm_per_360,
                    "fov": manual_override_fov,
                }
                if manual_override_cm_per_360 is not None or manual_override_fov is not None
                else None
            ),
            status="uploading",
            require_no_active=True,
        )
    except queue.ActiveSessionExists as exc:
        raise HTTPException(429, "已有分析进行中,等完成再提交") from exc
    ws = session_dir(sid)
    ws.mkdir(parents=True, exist_ok=True)
    video_path = ws / f"video{video_ext}"
    csv_path = ws / f"stats{csv_ext}"
    video_temp_path = ws / f"video{video_ext}.tmp"
    csv_temp_path = ws / f"stats{csv_ext}.tmp"

    try:
        await stream_upload_to_path(
            video,
            video_temp_path,
            max_bytes=config.MAX_VIDEO_BYTES,
            field="video",
        )
        await stream_upload_to_path(
            csv,
            csv_temp_path,
            max_bytes=config.MAX_CSV_BYTES,
            field="csv",
        )
        video_temp_path.replace(video_path)
        csv_temp_path.replace(csv_path)
        await _update_session_input_paths(sid, x_user_id, str(video_path), str(csv_path))
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
        result=(
            project_analysis_result_metric_definitions(s["result"])
            if isinstance(s.get("result"), dict)
            else s["result"]
        ),
        error=project_error_for_session(s["error"]),
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
        presentation_label=s.get("presentation_label"),
        training_at=s.get("training_at"),
        analysis_completed_at=s.get("analysis_completed_at"),
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
    rows = await analysis_service.list_history(x_user_id)
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
        trend = await analysis_service.history_trend(x_user_id, metric_key)
    except analysis_service.ProductCommandError as exc:
        raise HTTPException(400, exc.message) from exc
    return HistoryTrendResponse(**trend)


@router.get("/current-training", response_model=CurrentTrainingResponse)
async def get_current_training(
    x_user_id: str = Depends(get_request_user_id),
):
    """Return the bounded read-only current Training Plan for one owner."""
    plans = await training_plan_store.list_plans(x_user_id)
    current = next((plan for plan in plans if plan["status"] == "active"), None)
    if current is None:
        current = next((plan for plan in plans if plan["status"] == "paused"), None)
    if current is None:
        projection = build_current_training_v1(plan=None, items=[])
    else:
        items = await training_plan_store.list_plan_items(x_user_id, current["plan_id"])
        projection = build_current_training_v1(plan=current, items=items)
    return CurrentTrainingResponse(**projection)


def _public_benchmark_record(record: dict) -> BenchmarkRecordOut:
    return BenchmarkRecordOut(**{
        field: record[field]
        for field in BenchmarkRecordOut.model_fields
    })


@router.get("/benchmarks", response_model=BenchmarkRecordListResponse)
async def list_benchmarks(x_user_id: str = Depends(get_request_user_id)):
    records = await benchmark_store.list_records(x_user_id)
    return BenchmarkRecordListResponse(
        records=[_public_benchmark_record(record) for record in records]
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
    return _public_benchmark_record(record)


@router.post(
    "/benchmarks/sync/kovaaks",
    response_model=KovaaKBenchmarkSyncResponse,
)
async def sync_kovaak_benchmarks(
    request: Request,
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        sync_request = KovaaKBenchmarkSyncRequest.model_validate(await request.json())
    except (TypeError, ValueError):
        raise HTTPException(422, "KovaaK score sync input is invalid") from None
    try:
        result = await kovaak_benchmark_service.sync_owner_snapshot(
            x_user_id, sync_request.steam_id,
        )
    except (ValueError, kovaak_benchmark_provider.KovaaKBenchmarkError):
        raise HTTPException(502, "KovaaK benchmark sync is unavailable") from None
    return KovaaKBenchmarkSyncResponse(**result)


@router.get("/kovaak-connection", response_model=KovaaKConnectionStatusResponse)
async def get_kovaak_connection(x_user_id: str = Depends(get_request_user_id)):
    return KovaaKConnectionStatusResponse(
        connected=await kovaak_connection_store.get_connection(x_user_id) is not None,
    )


@router.put("/kovaak-connection", response_model=KovaaKConnectionStatusResponse)
async def save_kovaak_connection(
    request: Request,
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        connection_request = KovaaKConnectionSaveRequest.model_validate(
            await request.json(),
        )
    except (TypeError, ValueError):
        raise HTTPException(422, "KovaaK connection input is invalid") from None
    await kovaak_connection_store.save_connection(
        x_user_id, connection_request.steam_profile,
    )
    return KovaaKConnectionStatusResponse(connected=True)


@router.delete("/kovaak-connection", response_model=KovaaKConnectionDeleteResponse)
async def delete_kovaak_connection(x_user_id: str = Depends(get_request_user_id)):
    return KovaaKConnectionDeleteResponse(
        deleted=await kovaak_connection_store.delete_connection(x_user_id),
    )


@router.post(
    "/kovaak-connection/refresh",
    response_model=KovaaKBenchmarkSyncResponse,
)
async def refresh_kovaak_connection(x_user_id: str = Depends(get_request_user_id)):
    try:
        result = await kovaak_benchmark_service.refresh_connected_snapshot(x_user_id)
    except kovaak_benchmark_service.KovaaKConnectionNotFound:
        raise HTTPException(404, "KovaaK account is not connected") from None
    except (ValueError, kovaak_benchmark_provider.KovaaKBenchmarkError):
        raise HTTPException(502, "KovaaK benchmark sync is unavailable") from None
    return KovaaKBenchmarkSyncResponse(**result)


def _unavailable_kovaak_scores() -> dict:
    return kovaak_benchmark_service.unavailable_score_projection()


def _project_kovaak_scores(summary: object) -> dict:
    return kovaak_benchmark_service.project_score_summary(summary)


@router.get("/kovaak-scores", response_model=KovaaKScoresResponse)
async def get_kovaak_scores(x_user_id: str = Depends(get_request_user_id)):
    """Return the latest complete, identity-free KovaaK score snapshot."""
    catalog = benchmark_catalog.load_catalog()
    return _project_kovaak_scores(
        benchmark_catalog.project_benchmark_summary(
            await benchmark_store.list_latest_snapshot(
                x_user_id,
                provider="kovaaks-webapp",
                catalog_version=catalog["catalog_version"],
                exact_record_count=158,
            ),
        ),
    )


@router.get("/kovaak-runs", response_model=KovaaKRunListResponse)
async def list_kovaak_runs(_: None = Depends(require_desktop_token)):
    runs = await analysis_service.list_runs(config.DESKTOP_LOCAL_PROFILE)
    return KovaaKRunListResponse(runs=[KovaaKRunListItem(**run) for run in runs])


@router.get("/kovaak-runs/{run_id}", response_model=KovaaKRunItem)
async def get_kovaak_run(
    run_id: int = Path(...),
    _: None = Depends(require_desktop_token),
):
    try:
        run = await analysis_service.get_run(config.DESKTOP_LOCAL_PROFILE, run_id)
    except analysis_service.ProductCommandError as exc:
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
    result = await analysis_service.execute_trusted_analysis_create(
        config.DESKTOP_LOCAL_PROFILE,
        run_id,
        cm_per_360=request.cm_per_360,
        fov=request.fov,
        profile_default=(request.profile_default.model_dump() if request.profile_default else None),
        manual_override=(request.manual_override.model_dump() if request.manual_override else None),
        managed_video_source=video_source,
        idempotency_key=idempotency_key,
        allow_parallel=request.allow_parallel,
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
    result = await analysis_service.execute_analysis_retry(
        x_user_id,
        session_id,
        idempotency_key=idempotency_key,
    )
    _raise_product_command_error(result)
    retried = result.get("result") if isinstance(result, dict) else None
    returned_id = retried.get("id") if isinstance(retried, dict) else None
    session = await queue.get_session(returned_id if isinstance(returned_id, int) else session_id)
    if session is None:  # Defensive: retry handler succeeded only for an existing session.
        raise HTTPException(404, "session 不存在")
    return _session_status_response(session)


def _validate_public_body(model, payload: dict, message: str):
    try:
        return model.model_validate(payload)
    except Exception as error:
        raise HTTPException(400, message) from error


@router.get("/calibration-profile", response_model=CalibrationProfileOut)
async def get_calibration_profile(
    x_user_id: str = Depends(get_request_user_id),
):
    return CalibrationProfileOut(
        **await calibration_profile_store.get_profile(x_user_id),
    )


@router.put("/calibration-profile", response_model=CalibrationProfileOut)
async def save_calibration_profile(
    body: CalibrationProfileUpdateRequest = Body(...),
    x_user_id: str = Depends(get_request_user_id),
):
    try:
        result = await calibration_profile_store.save_profile(
            x_user_id, cm_per_360=body.cm_per_360, fov=body.fov,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return CalibrationProfileOut(**result)


@router.delete("/calibration-profile", response_model=CalibrationProfileOut)
async def delete_calibration_profile(
    x_user_id: str = Depends(get_request_user_id),
):
    return CalibrationProfileOut(
        **await calibration_profile_store.delete_profile(x_user_id),
    )


# Coach 页:视频流 + 时间轴 markers
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/analysis-data",
    response_model=FrontendAnalysisDataResponse,
)
async def get_session_analysis_data(
    session_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    """Return the bounded, path-free data projection for one owned Analysis."""
    s = await _get_owned_session(session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成")
    result = s.get("result") or {}
    safe_ref = (result.get("evidence") or {}).get("derived_artifact")
    if not isinstance(safe_ref, dict):
        raise HTTPException(404, "Analysis Data 不可用")
    try:
        artifact = await evidence_store.read_analysis_evidence_artifact(
            owner_id=x_user_id,
            analysis_ref=f"analysis:{session_id}",
            artifact_ref=safe_ref.get("artifact_ref"),
            evidence_revision=safe_ref.get("evidence_revision"),
        )
        projection = build_frontend_analysis_data_v1(
            analysis_ref=f"analysis:{session_id}",
            artifact=artifact,
        )
    except (ValueError, OSError):
        raise HTTPException(404, "Analysis Data 不可用") from None
    return FrontendAnalysisDataResponse(**projection)


@router.get(
    "/sessions/{session_id}/analysis-data/family",
    response_model=FrontendAnalysisFamilyDataResponse,
)
async def get_session_analysis_family_data(
    session_id: int = Path(...),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    x_user_id: str = Depends(get_request_user_id),
):
    """Return a version-dispatched, paginated family detail projection."""
    s = await _get_owned_session(session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成")
    result = s.get("result") or {}
    analysis_ref = f"analysis:{session_id}"
    analysis_type = result.get("analysis_type")
    analysis_version = result.get("analysis_version")
    input_mode = result.get("input_mode")
    if (
        not isinstance(analysis_type, str)
        or not isinstance(analysis_version, str)
        or not isinstance(input_mode, str)
    ):
        raise HTTPException(404, "Analysis Data 不可用")
    safe_ref = (result.get("evidence") or {}).get("derived_artifact")
    artifact = None
    if isinstance(safe_ref, dict):
        try:
            artifact = await evidence_store.read_analysis_evidence_artifact(
                owner_id=x_user_id,
                analysis_ref=analysis_ref,
                artifact_ref=safe_ref.get("artifact_ref"),
                evidence_revision=safe_ref.get("evidence_revision"),
            )
        except (ValueError, OSError):
            raise HTTPException(404, "Analysis Data 不可用") from None
    projection = build_frontend_analysis_family_data_v1(
        analysis_ref=analysis_ref,
        analysis_type=analysis_type,
        analysis_version=analysis_version,
        input_mode=input_mode,
        artifact=artifact,
        limit=limit,
        offset=offset,
    )
    return FrontendAnalysisFamilyDataResponse(**projection)


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
        unavailable = ManagedVideoUnavailableResponse(
            schema_version="managed_video_unavailable.v1",
            availability="unavailable",
            reason=replay.get("reason") or "managed_video_unavailable",
        )
        return JSONResponse(status_code=410, content=unavailable.model_dump())
    video_path = s.get("video_path") or ""
    return FileResponse(video_path, media_type="video/mp4")


@router.get(
    "/sessions/{session_id}/evidence-segments",
    response_model=FrontendEvidenceSegmentsResponse,
)
async def list_session_evidence_segments(
    session_id: int = Path(...),
    x_user_id: str = Depends(get_request_user_id),
):
    """Return bounded EvidenceSegment metadata and local MP4 seek anchors."""
    s = await _get_owned_session(session_id, x_user_id)
    if s["status"] != "done":
        raise HTTPException(409, "分析未完成")
    result = s.get("result") or {}
    safe_ref = (result.get("evidence") or {}).get("derived_artifact")

    snapshot = s.get("input_snapshot") or {}
    window = snapshot.get("canonical_time_window") if isinstance(snapshot, dict) else None
    window_start = window.get("start_ms") if isinstance(window, dict) else None
    window_end = window.get("end_ms") if isinstance(window, dict) else None
    valid_window = (
        isinstance(window_start, int)
        and isinstance(window_end, int)
        and window_end > window_start
    )
    replay = history_trends.visual_replay_capability(s)
    video_available = replay.get("kind") == "seekable_mp4"
    video_route = f"/api/sessions/{session_id}/video" if video_available else None
    if not isinstance(safe_ref, dict):
        return FrontendEvidenceSegmentsResponse(
            schema_version="frontend_evidence_segments.v1",
            analysis_ref=f"analysis:{session_id}",
            video_availability="available" if video_available else "unavailable",
            video_route=video_route,
            canonical_window_start_ms=window_start if valid_window else None,
            segments=[],
        )
    try:
        artifact = await evidence_store.read_analysis_evidence_artifact(
            owner_id=x_user_id,
            analysis_ref=f"analysis:{session_id}",
            artifact_ref=safe_ref.get("artifact_ref"),
            evidence_revision=safe_ref.get("evidence_revision"),
        )
    except (ValueError, OSError):
        raise HTTPException(404, "Evidence 不可用") from None

    projected: list[FrontendEvidenceSegment] = []
    for raw in list(artifact.get("evidence_segments") or [])[:64]:
        if not isinstance(raw, dict):
            continue
        safe = project_evidence_segment(raw)
        focus_start = safe.get("focus_start_ms")
        focus_end = safe.get("focus_end_ms")
        playback_limitations = list(safe.get("limitations") or [])
        relative_start = None
        relative_end = None
        if (
            video_available
            and valid_window
            and isinstance(focus_start, int)
            and isinstance(focus_end, int)
        ):
            relative_start = max(0, focus_start - window_start)
            relative_end = min(window_end - window_start, max(relative_start, focus_end - window_start))
        else:
            playback_limitations.append("local_video_seek_unavailable")
        projected.append(FrontendEvidenceSegment(
            **safe,
            playback=EvidenceSegmentPlayback(
                schema_version="evidence_segment_playback.v1",
                availability="available" if relative_start is not None else "unavailable",
                video_route=video_route if relative_start is not None else None,
                relative_start_ms=relative_start,
                relative_end_ms=relative_end,
                limitations=list(dict.fromkeys(playback_limitations)),
            ),
        ))
    return FrontendEvidenceSegmentsResponse(
        schema_version="frontend_evidence_segments.v1",
        analysis_ref=f"analysis:{session_id}",
        video_availability="available" if video_available else "unavailable",
        video_route=video_route,
        canonical_window_start_ms=window_start if valid_window else None,
        segments=projected,
    )


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

export interface paths {
    "/api/analyze": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Analyze
         * @description 接收 flicking 视频 + Stats CSV,入队异步分析。
         *
         *     限制:单用户同时 1 个 job(并发防滥用);视频 100MB 上限。
         *     user_id 由 get_request_user_id 解析(dev: X-User-Id; trust: 反代用户头)。
         */
        post: operations["analyze_api_analyze_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/benchmarks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Benchmarks */
        get: operations["list_benchmarks_api_benchmarks_get"];
        put?: never;
        /** Create Benchmark */
        post: operations["create_benchmark_api_benchmarks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/benchmarks/sync/kovaaks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sync Kovaak Benchmarks */
        post: operations["sync_kovaak_benchmarks_api_benchmarks_sync_kovaaks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/calibration-profile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Calibration Profile */
        get: operations["get_calibration_profile_api_calibration_profile_get"];
        /** Save Calibration Profile */
        put: operations["save_calibration_profile_api_calibration_profile_put"];
        post?: never;
        /** Delete Calibration Profile */
        delete: operations["delete_calibration_profile_api_calibration_profile_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/capture-status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Capture Status
         * @description Aggregate native coordinator status with path-free Run attachments.
         */
        get: operations["get_capture_status_api_capture_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/current-training": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Current Training
         * @description Return the bounded read-only current Training Plan for one owner.
         */
        get: operations["get_current_training_api_current_training_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/desktop/analyze-paths": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Analyze Paths
         * @description Import desktop-selected local files into a managed session workspace.
         */
        post: operations["analyze_paths_api_desktop_analyze_paths_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/external-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List External Runs */
        get: operations["list_external_runs_api_external_runs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/external-runs/{external_run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get External Run */
        get: operations["get_external_run_api_external_runs__external_run_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/external-telemetry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get External Telemetry */
        get: operations["get_external_telemetry_api_external_telemetry_get"];
        /** Save External Telemetry Watch Root */
        put: operations["save_external_telemetry_watch_root_api_external_telemetry_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/history/trends/{metric_key}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get History Trend */
        get: operations["get_history_trend_api_history_trends__metric_key__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/kovaak-connection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Kovaak Connection */
        get: operations["get_kovaak_connection_api_kovaak_connection_get"];
        /** Save Kovaak Connection */
        put: operations["save_kovaak_connection_api_kovaak_connection_put"];
        post?: never;
        /** Delete Kovaak Connection */
        delete: operations["delete_kovaak_connection_api_kovaak_connection_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/kovaak-connection/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Refresh Kovaak Connection */
        post: operations["refresh_kovaak_connection_api_kovaak_connection_refresh_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/kovaak-local-directories": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Kovaak Local Directories */
        get: operations["get_kovaak_local_directories_api_kovaak_local_directories_get"];
        /** Save Kovaak Local Directories */
        put: operations["save_kovaak_local_directories_api_kovaak_local_directories_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/kovaak-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Kovaak Runs */
        get: operations["list_kovaak_runs_api_kovaak_runs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/kovaak-runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Kovaak Run */
        get: operations["get_kovaak_run_api_kovaak_runs__run_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/kovaak-runs/{run_id}/analyze": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Analyze Kovaak Run
         * @description Freeze one owned Run through the shared product-command application handler.
         */
        post: operations["analyze_kovaak_run_api_kovaak_runs__run_id__analyze_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/kovaak-runs/{run_id}/evidence/{evidence_kind}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Remove Kovaak Run Evidence */
        delete: operations["remove_kovaak_run_evidence_api_kovaak_runs__run_id__evidence__evidence_kind__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/kovaak-scores": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Kovaak Scores
         * @description Return the latest complete, identity-free KovaaK score snapshot.
         */
        get: operations["get_kovaak_scores_api_kovaak_scores_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/product-state": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Product State
         * @description Conditional-start state; unavailable is never projected as an empty state.
         */
        get: operations["get_product_state_api_product_state_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/product-state/onboarding": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Set Product Onboarding
         * @description Persist a completed desktop onboarding decision.
         *
         *     Provider readiness is owned by the Coach sidecar; the desktop onboarding
         *     flow verifies the Provider before finishing, so this route only records
         *     the decision.
         */
        post: operations["set_product_onboarding_api_product_state_onboarding_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Sessions
         * @description 当前用户的分析列表(新→旧)。不返回完整 result。
         */
        get: operations["list_sessions_api_sessions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sessions/{session_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Session
         * @description 查询分析状态/结果(queued / running / done / failed)。
         */
        get: operations["get_session_api_sessions__session_id__get"];
        put?: never;
        post?: never;
        /**
         * Delete Session
         * @description Delete a terminal analysis and only its managed workspace copy.
         */
        delete: operations["delete_session_api_sessions__session_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sessions/{session_id}/analysis-data": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Session Analysis Data
         * @description Return the bounded, path-free data projection for one owned Analysis.
         */
        get: operations["get_session_analysis_data_api_sessions__session_id__analysis_data_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sessions/{session_id}/analysis-data/family": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Session Analysis Family Data
         * @description Return a version-dispatched, paginated family detail projection.
         */
        get: operations["get_session_analysis_family_data_api_sessions__session_id__analysis_data_family_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sessions/{session_id}/evidence-segments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Session Evidence Segments
         * @description Return bounded EvidenceSegment metadata and local MP4 seek anchors.
         */
        get: operations["list_session_evidence_segments_api_sessions__session_id__evidence_segments_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sessions/{session_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Retry Session
         * @description 将 failed session 通过共享产品命令处理器重新入队。
         */
        post: operations["retry_session_api_sessions__session_id__retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sessions/{session_id}/timeline": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Session Timeline
         * @description 返回视频时间轴事件 markers。
         *
         *     数据源: analysis_result_to_coach_report 后的 timeline + diagnosis.meta。
         */
        get: operations["get_session_timeline_api_sessions__session_id__timeline_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sessions/{session_id}/video": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Session Video
         * @description 流式返回 session 关联的视频文件(给 coach 页 <video src>)。
         *
         *     路径从 sessions.video_path 取。worker 分析完后**不再删视频**(见
         *     worker.process_one 注释),所以 coach 页能播。文件不存在 → 404。
         */
        get: operations["get_session_video_api_sessions__session_id__video_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/storage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Storage */
        get: operations["get_storage_api_storage_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/storage/incomplete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Incomplete Capture Storage */
        get: operations["get_incomplete_capture_storage_api_storage_incomplete_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/storage/incomplete/{item_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Remove Incomplete Capture Storage */
        delete: operations["remove_incomplete_capture_storage_api_storage_incomplete__item_ref__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tasks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Tasks */
        get: operations["list_tasks_api_tasks_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tasks/{task_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Task */
        get: operations["get_task_api_tasks__task_ref__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/healthz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Healthz */
        get: operations["healthz_healthz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/readyz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Readyz */
        get: operations["readyz_readyz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AnalysisHistoryDetailOut */
        AnalysisHistoryDetailOut: {
            /** Analysis Completed At */
            analysis_completed_at?: string | null;
            /** Analysis Ref */
            analysis_ref: string;
            diagnosis_locator: components["schemas"]["DiagnosisLocatorOut"];
            /** Evidence Refs */
            evidence_refs?: components["schemas"]["EvidenceReferenceOut"][];
            /** Input Mode */
            input_mode: string;
            /** Presentation Label */
            presentation_label?: string | null;
            /** Run Ref */
            run_ref?: string | null;
            /** Scenario */
            scenario?: string | null;
            /** Source Availability */
            source_availability?: {
                [key: string]: string;
            };
            trace_quality: components["schemas"]["TraceQualityOut"];
            /** Training At */
            training_at?: string | null;
            visual_replay: components["schemas"]["VisualReplayOut"];
        };
        /** AnalyzePathsRequest */
        AnalyzePathsRequest: {
            /** Cm Per 360 */
            cm_per_360?: number | null;
            /** Csv Path */
            csv_path: string;
            /** Fov */
            fov?: number | null;
            manual_override?: components["schemas"]["CalibrationValues"] | null;
            profile_default?: components["schemas"]["CalibrationValues"] | null;
            /** Video Path */
            video_path: string;
        };
        /** AnalyzeResponse */
        AnalyzeResponse: {
            /** Session Id */
            session_id: number;
        };
        /** BenchmarkRecordCreate */
        BenchmarkRecordCreate: {
            /**
             * Availability
             * @default available
             * @enum {string}
             */
            availability: "available" | "stale" | "unavailable";
            /** Catalog Version */
            catalog_version: string;
            /** Metric Key */
            metric_key: string;
            /** Observed At */
            observed_at: string;
            /** Provider */
            provider: string;
            /** Provider License Note */
            provider_license_note: string;
            /** Scenario Id */
            scenario_id: string;
            /** Unit */
            unit: string;
            /** Value */
            value: number;
        };
        /** BenchmarkRecordListResponse */
        BenchmarkRecordListResponse: {
            /** Records */
            records: components["schemas"]["BenchmarkRecordOut"][];
        };
        /** BenchmarkRecordOut */
        BenchmarkRecordOut: {
            /**
             * Availability
             * @default available
             * @enum {string}
             */
            availability: "available" | "stale" | "unavailable";
            /** Catalog Version */
            catalog_version: string;
            /** Created At */
            created_at: string;
            /** Id */
            id: number;
            /** Metric Key */
            metric_key: string;
            /** Observed At */
            observed_at: string;
            /** Provider */
            provider: string;
            /** Provider License Note */
            provider_license_note: string;
            /** Scenario Id */
            scenario_id: string;
            /** Unit */
            unit: string;
            /** Value */
            value: number;
        };
        /** Body_analyze_api_analyze_post */
        Body_analyze_api_analyze_post: {
            /** Cm Per 360 */
            cm_per_360?: number | null;
            /** Csv */
            csv: string;
            /** Fov */
            fov?: number | null;
            /** Manual Override Cm Per 360 */
            manual_override_cm_per_360?: number | null;
            /** Manual Override Fov */
            manual_override_fov?: number | null;
            /** Profile Default Cm Per 360 */
            profile_default_cm_per_360?: number | null;
            /** Profile Default Fov */
            profile_default_fov?: number | null;
            /** Video */
            video: string;
        };
        /** CalibrationProfileOut */
        CalibrationProfileOut: {
            /** Adoption Priority */
            adoption_priority: ("stats" | "manual_override" | "profile_default" | "undetermined")[];
            /** Configured */
            configured: boolean;
            /** Deletion State */
            deletion_state?: ("completed" | "already_absent") | null;
            /** Dpi */
            dpi?: number | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "calibration_profile.v1";
            /** Sensitivity */
            sensitivity?: number | null;
            /** Updated At */
            updated_at?: string | null;
            values: components["schemas"]["CalibrationValuesOut"];
        };
        /** CalibrationProfileUpdateRequest */
        CalibrationProfileUpdateRequest: {
            /** Cm Per 360 */
            cm_per_360?: number | null;
            /** Fov */
            fov?: number | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "calibration_profile_update.v1";
        };
        /**
         * CalibrationValues
         * @description Path-free calibration inputs; Stats values are selected by the worker.
         */
        CalibrationValues: {
            /** Cm Per 360 */
            cm_per_360?: number | null;
            /** Fov */
            fov?: number | null;
        };
        /** CalibrationValuesOut */
        CalibrationValuesOut: {
            /** Cm Per 360 */
            cm_per_360?: number | null;
            /** Fov */
            fov?: number | null;
        };
        /** CaptureRunAttachment */
        CaptureRunAttachment: {
            /** Raw Attached */
            raw_attached: boolean;
            /** Run Ref */
            run_ref: string;
            /** Video Attached */
            video_attached: boolean;
        };
        /** CaptureStatusResponse */
        CaptureStatusResponse: {
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable";
            /** Capture Enabled */
            capture_enabled?: boolean | null;
            /** Error */
            error?: {
                [key: string]: unknown;
            } | null;
            /** Finalization State */
            finalization_state: string;
            /** Kovaak Process Present */
            kovaak_process_present?: boolean | null;
            /** Pause Fail Closed */
            pause_fail_closed: boolean;
            /**
             * Pause State
             * @enum {string}
             */
            pause_state: "clear" | "fail_closed" | "unknown";
            /** Platform Supported */
            platform_supported?: boolean | null;
            /**
             * Raw Input Permission
             * @enum {string}
             */
            raw_input_permission: "granted" | "denied" | "not_determined";
            /** Replay Buffer Active */
            replay_buffer_active?: boolean | null;
            /** Runs */
            runs?: components["schemas"]["CaptureRunAttachment"][];
            /**
             * Runtime Health
             * @enum {string}
             */
            runtime_health: "healthy" | "degraded" | "unavailable";
            /**
             * Schema Version
             * @constant
             */
            schema_version: "capture_status.v1";
        };
        /** CurrentTrainingItem */
        CurrentTrainingItem: {
            /** Cue */
            cue?: string | null;
            /** Display Name */
            display_name?: string | null;
            /** Dose Guardrail */
            dose_guardrail?: string | null;
            /** Observation */
            observation?: string | null;
            /** Practice Condition */
            practice_condition?: string | null;
            /** Retest */
            retest?: string | null;
            /**
             * Scenario Availability
             * @enum {string}
             */
            scenario_availability: "available" | "unavailable";
            /** Scenario Profile Ref */
            scenario_profile_ref?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "planned" | "active" | "completed" | "cancelled";
        };
        /** CurrentTrainingResponse */
        CurrentTrainingResponse: {
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable";
            /** Items */
            items?: components["schemas"]["CurrentTrainingItem"][];
            /** Limitations */
            limitations?: string[];
            /** Plan Status */
            plan_status?: ("active" | "paused") | null;
            /** Reason */
            reason?: "no_current_plan" | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "current_training.v1";
            /** Total Item Count */
            total_item_count: number;
            /** Visible Item Count */
            visible_item_count: number;
        };
        /** DeleteSessionResponse */
        DeleteSessionResponse: {
            /** Cleanup Failed */
            cleanup_failed: string[];
            /** Deleted */
            deleted: boolean;
            /** Files Removed */
            files_removed: string[];
            /** Id */
            id: number;
        };
        /** DiagnosisLocatorOut */
        DiagnosisLocatorOut: {
            /** Analysis Ref */
            analysis_ref: string;
            /**
             * Section
             * @constant
             */
            section: "diagnosis";
        };
        /**
         * ErrorV1
         * @description Wire Error v1 envelope (sessions.error after read-time coerce).
         */
        ErrorV1: {
            /**
             * Category
             * @enum {string}
             */
            category: "input_validation" | "local_cv_runtime" | "llm_provider" | "network_cloud" | "storage_disk" | "internal_unknown";
            /** Code */
            code: string;
            /** Message */
            message: string;
            /** Retryable */
            retryable: boolean;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "error.v1";
            /** Trace Id */
            trace_id?: string | null;
        };
        /** EvidenceReferenceOut */
        EvidenceReferenceOut: {
            /** Alignment Status */
            alignment_status: string;
            /** Artifact Id */
            artifact_id?: string | null;
            /** Availability */
            availability: string;
            /** Challenge Time Range Ms */
            challenge_time_range_ms?: number[] | null;
            /** Id */
            id: string;
            /** Local Only */
            local_only: boolean;
            /** Metric Keys */
            metric_keys?: string[];
            /** Source */
            source: string;
        };
        /** EvidenceSegmentPlayback */
        EvidenceSegmentPlayback: {
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable";
            /** Limitations */
            limitations?: string[];
            /** Relative End Ms */
            relative_end_ms?: number | null;
            /** Relative Start Ms */
            relative_start_ms?: number | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "evidence_segment_playback.v1";
            /** Video Route */
            video_route?: string | null;
        };
        /** ExternalRunDetailResponse */
        ExternalRunDetailResponse: {
            /** Run */
            run: {
                [key: string]: unknown;
            };
            /**
             * Schema Version
             * @constant
             */
            schema_version: "external_run_detail.v1";
        };
        /** ExternalRunListItem */
        ExternalRunListItem: {
            /** Deaths */
            deaths?: number | null;
            /** External Run Id */
            external_run_id: string;
            /** Imported At */
            imported_at?: string | null;
            /** Index File */
            index_file?: string | null;
            /** Label Agreement */
            label_agreement?: string | null;
            /** Matched Run Ids */
            matched_run_ids?: unknown[];
            /** N Targets */
            n_targets?: number | null;
            /** Proposal Label */
            proposal_label?: string | null;
            /** Proposal Score */
            proposal_score?: number | null;
            /** Proposal Status */
            proposal_status?: string | null;
            /** Quality Issues */
            quality_issues?: string[];
            /** Round */
            round?: number | null;
            /** Schema Version */
            schema_version: string;
            /** Source File */
            source_file?: string | null;
            /** Spawns */
            spawns?: number | null;
            /** T2K P50 */
            t2k_p50?: number | null;
            /** Timeouts */
            timeouts?: number | null;
        };
        /** ExternalRunListResponse */
        ExternalRunListResponse: {
            /** Items */
            items: components["schemas"]["ExternalRunListItem"][];
            /**
             * Schema Version
             * @constant
             */
            schema_version: "external_run_list.v1";
            /** Total */
            total: number;
        };
        /** ExternalTelemetryConfigResponse */
        ExternalTelemetryConfigResponse: {
            /**
             * Activation
             * @default not_requested
             * @enum {string}
             */
            activation: "not_requested" | "activated" | "runtime_unavailable" | "failed";
            /** Run Count */
            run_count: number;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "external_telemetry_config.v1";
            /**
             * Source
             * @enum {string}
             */
            source: "unset" | "confirmed" | "automatic";
            /** Watch Root */
            watch_root?: string | null;
            /** Watcher */
            watcher?: {
                [key: string]: unknown;
            } | null;
        };
        /** ExternalTelemetryWatchRootUpdateRequest */
        ExternalTelemetryWatchRootUpdateRequest: {
            /** Watch Root */
            watch_root: string;
        };
        /** FrontendAnalysisDataDistribution */
        FrontendAnalysisDataDistribution: {
            /** Count */
            count: number;
            /** Kind */
            kind: string;
        };
        /** FrontendAnalysisDataMarker */
        FrontendAnalysisDataMarker: {
            /** Event Ref */
            event_ref: string;
            /** Kind */
            kind: string;
            /** Relative Ms */
            relative_ms: number;
        };
        /** FrontendAnalysisDataResponse */
        FrontendAnalysisDataResponse: {
            /** Analysis Ref */
            analysis_ref: string;
            /** Event Distribution */
            event_distribution: components["schemas"]["FrontendAnalysisDataDistribution"][];
            /** Event Markers */
            event_markers: components["schemas"]["FrontendAnalysisDataMarker"][];
            /** Limitations */
            limitations?: string[];
            /**
             * Schema Version
             * @constant
             */
            schema_version: "frontend_analysis_data.v1";
            target_relative_error_radius: components["schemas"]["TargetRelativeErrorRadius"];
        };
        /** FrontendAnalysisFamilyDataResponse */
        FrontendAnalysisFamilyDataResponse: {
            /** Analysis Ref */
            analysis_ref: string;
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable";
            /**
             * Family
             * @enum {string}
             */
            family: "switching" | "tracking" | "flicking" | "unsupported";
            /** Limitations */
            limitations?: string[];
            /** Next Offset */
            next_offset?: number | null;
            /** Reason */
            reason?: string | null;
            /** Rows */
            rows?: components["schemas"]["FrontendAnalysisFamilyDataRow"][];
            /**
             * Schema Version
             * @constant
             */
            schema_version: "frontend_analysis_family_data.v1";
            /**
             * Total Count
             * @default 0
             */
            total_count: number;
        };
        /** FrontendAnalysisFamilyDataRow */
        FrontendAnalysisFamilyDataRow: {
            /**
             * Kind
             * @enum {string}
             */
            kind: "switch_chain" | "tracking_fixed_window" | "tracking_loss" | "tracking_reacquisition" | "tracking_change_response" | "static_flick";
            /** Limitations */
            limitations?: string[];
            /** Metrics */
            metrics: {
                [key: string]: number;
            };
            /** Timing */
            timing: {
                [key: string]: number;
            };
        };
        /** FrontendEvidenceSegment */
        FrontendEvidenceSegment: {
            /** Analysis Ref */
            analysis_ref: string;
            /** Analyzer Ref */
            analyzer_ref?: string | null;
            /** Available Channels */
            available_channels?: string[];
            /** Confidence */
            confidence?: number | null;
            /** End Ms */
            end_ms?: number | null;
            /** Event Refs */
            event_refs?: string[];
            /** Focus End Ms */
            focus_end_ms?: number | null;
            /** Focus Start Ms */
            focus_start_ms?: number | null;
            /** Issue Refs */
            issue_refs?: string[];
            /** Limitations */
            limitations?: string[];
            /** Metric Refs */
            metric_refs?: string[];
            playback: components["schemas"]["EvidenceSegmentPlayback"];
            /** Rank Reason */
            rank_reason?: string | null;
            /** Segment Id */
            segment_id: string;
            /** Segment Kind */
            segment_kind?: string | null;
            /** Source Coverage */
            source_coverage?: number | null;
            /** Start Ms */
            start_ms?: number | null;
            /** Title Key */
            title_key?: string | null;
        };
        /** FrontendEvidenceSegmentsResponse */
        FrontendEvidenceSegmentsResponse: {
            /** Analysis Ref */
            analysis_ref: string;
            /** Canonical Window Start Ms */
            canonical_window_start_ms?: number | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "frontend_evidence_segments.v1";
            /** Segments */
            segments: components["schemas"]["FrontendEvidenceSegment"][];
            /**
             * Video Availability
             * @enum {string}
             */
            video_availability: "available" | "unavailable";
            /** Video Route */
            video_route?: string | null;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HistoryTrendResponse */
        HistoryTrendResponse: {
            /** Baseline */
            baseline?: number | null;
            /** Baseline Session Id */
            baseline_session_id?: number | null;
            /** Classification */
            classification?: "deterministic" | null;
            /** Comparable */
            comparable: boolean;
            /** Current */
            current?: number | null;
            /** Current Session Id */
            current_session_id?: number | null;
            /** Delta */
            delta?: number | null;
            /** Metric Key */
            metric_key?: string | null;
            /** Metric Version */
            metric_version?: string | null;
            /** Percent Change */
            percent_change?: number | null;
            /** Reason */
            reason?: string | null;
            /** Unit */
            unit?: string | null;
        };
        /** IncompleteCaptureImpactOut */
        IncompleteCaptureImpactOut: {
            /**
             * Code
             * @constant
             */
            code: "incomplete_recovery_only";
            /** Message */
            message: string;
        };
        /** IncompleteCaptureItemOut */
        IncompleteCaptureItemOut: {
            /** Created At */
            created_at: string;
            impact: components["schemas"]["IncompleteCaptureImpactOut"];
            /** Item Ref */
            item_ref: string;
            /**
             * Reason
             * @enum {string}
             */
            reason: "interrupted_finalization" | "unclassified_capture_artifact";
            /** Removable */
            removable: boolean;
            /** Run Ref */
            run_ref: string;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "incomplete_capture_item.v1";
            /** Size Bytes */
            size_bytes: number;
        };
        /** IncompleteCaptureListResponse */
        IncompleteCaptureListResponse: {
            /** Items */
            items: components["schemas"]["IncompleteCaptureItemOut"][];
            /**
             * Schema Version
             * @default incomplete_capture_list.v1
             * @constant
             */
            schema_version: "incomplete_capture_list.v1";
            /** Total Bytes */
            total_bytes: number;
        };
        /** IncompleteCaptureRemovalResponse */
        IncompleteCaptureRemovalResponse: {
            impact: components["schemas"]["IncompleteCaptureImpactOut"];
            /** Item Ref */
            item_ref: string;
            /** Reclaimed Bytes */
            reclaimed_bytes: number;
            /**
             * Removal State
             * @enum {string}
             */
            removal_state: "completed" | "pending_cleanup" | "already_unavailable";
            /**
             * Schema Version
             * @constant
             */
            schema_version: "incomplete_capture_removal.v1";
        };
        /**
         * KovaaKAnalysisRequest
         * @description Create an Analysis from a persisted local Run.
         */
        KovaaKAnalysisRequest: {
            /**
             * Allow Parallel
             * @default false
             */
            allow_parallel: boolean;
            /** Cm Per 360 */
            cm_per_360?: number | null;
            /** Fov */
            fov?: number | null;
            manual_override?: components["schemas"]["CalibrationValues"] | null;
            profile_default?: components["schemas"]["CalibrationValues"] | null;
            /** Video Path */
            video_path?: string | null;
        };
        /** KovaaKBenchmarkSyncResponse */
        KovaaKBenchmarkSyncResponse: {
            /** Difficulty Counts */
            difficulty_counts: {
                [key: string]: number;
            };
            /** Imported Score Count */
            imported_score_count: number;
            /** Observed At */
            observed_at: string;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "kovaak_benchmark_sync_result.v1";
        };
        /** KovaaKConnectionDeleteResponse */
        KovaaKConnectionDeleteResponse: {
            /** Deleted */
            deleted: boolean;
        };
        /** KovaaKConnectionStatusResponse */
        KovaaKConnectionStatusResponse: {
            /** Connected */
            connected: boolean;
        };
        /** KovaaKLocalDirectoriesResponse */
        KovaaKLocalDirectoriesResponse: {
            /**
             * Activation
             * @enum {string}
             */
            activation: "not_requested" | "activated" | "runtime_unavailable" | "failed";
            performance: components["schemas"]["KovaaKLocalDirectoryStatus"];
            /**
             * Schema Version
             * @constant
             */
            schema_version: "kovaak_local_directories.v1";
            stats: components["schemas"]["KovaaKLocalDirectoryStatus"];
            /** Watcher Status */
            watcher_status?: ("no_candidates" | "not_exporting" | "ingesting") | null;
        };
        /** KovaaKLocalDirectoriesUpdateRequest */
        KovaaKLocalDirectoriesUpdateRequest: {
            /** Performance Dir */
            performance_dir: string;
            /** Stats Dir */
            stats_dir: string;
        };
        /** KovaaKLocalDirectoryStatus */
        KovaaKLocalDirectoryStatus: {
            /** Matching File Count */
            matching_file_count: number;
            /**
             * Matching Files
             * @enum {string}
             */
            matching_files: "found" | "no_matching_files";
            /** Path */
            path?: string | null;
            /**
             * Source
             * @enum {string}
             */
            source: "environment" | "confirmed" | "automatic" | "unavailable";
        };
        /** KovaaKRunItem */
        KovaaKRunItem: {
            /** Alignment */
            alignment?: {
                [key: string]: unknown;
            };
            /**
             * Analysis Count
             * @default 0
             */
            analysis_count: number;
            /** Created At */
            created_at: string;
            /** Evidence Availability */
            evidence_availability?: {
                [key: string]: string;
            };
            /** Finalization Error */
            finalization_error?: string | null;
            /**
             * Finalization State
             * @default discovered
             */
            finalization_state: string;
            /** Id */
            id: number;
            /** Limitations */
            limitations?: string[];
            /** Performance Source Ref */
            performance_source_ref?: string | null;
            /** Performance Summary */
            performance_summary?: {
                [key: string]: unknown;
            } | null;
            /**
             * Readiness State
             * @default incomplete_evidence
             * @enum {string}
             */
            readiness_state: "pending_analysis" | "analyzed" | "incomplete_evidence";
            /** Run Ref */
            run_ref: string;
            /** Scenario */
            scenario?: string | null;
            /** Source Availability */
            source_availability?: {
                [key: string]: string;
            };
            /** Source Key */
            source_key?: string | null;
            /** Stats Calibration */
            stats_calibration?: {
                [key: string]: number;
            } | null;
            /** Stats Source Ref */
            stats_source_ref?: string | null;
            /** Stats Summary */
            stats_summary?: {
                [key: string]: unknown;
            } | null;
            /** Supported Input Modes */
            supported_input_modes?: ("input_native" | "multimodal" | "video_fallback")[];
            /** Trace Artifact Ref */
            trace_artifact_ref?: string | null;
            /** Trace Error */
            trace_error?: string | null;
            trace_quality: components["schemas"]["TraceQualityOut"];
            /**
             * Trace State
             * @default none
             */
            trace_state: string;
            /** Updated At */
            updated_at: string;
            /** Video Artifact Ref */
            video_artifact_ref?: string | null;
            /** Video Error */
            video_error?: string | null;
            /** Video Quality */
            video_quality?: {
                [key: string]: unknown;
            };
        };
        /** KovaaKRunListItem */
        KovaaKRunListItem: {
            /** Alignment */
            alignment?: {
                [key: string]: unknown;
            };
            /**
             * Analysis Count
             * @default 0
             */
            analysis_count: number;
            /** Created At */
            created_at: string;
            /** Evidence Availability */
            evidence_availability?: {
                [key: string]: string;
            };
            /** Finalization Error */
            finalization_error?: string | null;
            /**
             * Finalization State
             * @default discovered
             */
            finalization_state: string;
            /** Id */
            id: number;
            /** Limitations */
            limitations?: string[];
            /**
             * Readiness State
             * @default incomplete_evidence
             * @enum {string}
             */
            readiness_state: "pending_analysis" | "analyzed" | "incomplete_evidence";
            /** Run Ref */
            run_ref: string;
            /** Scenario */
            scenario?: string | null;
            /** Source Availability */
            source_availability?: {
                [key: string]: string;
            };
            /** Source Key */
            source_key?: string | null;
            /** Stats Calibration */
            stats_calibration?: {
                [key: string]: number;
            } | null;
            /** Supported Input Modes */
            supported_input_modes?: ("input_native" | "multimodal" | "video_fallback")[];
            /** Trace Error */
            trace_error?: string | null;
            trace_quality: components["schemas"]["TraceQualityOut"];
            /**
             * Trace State
             * @default none
             */
            trace_state: string;
            /** Updated At */
            updated_at: string;
            /** Video Artifact Ref */
            video_artifact_ref?: string | null;
            /** Video Error */
            video_error?: string | null;
            /** Video Quality */
            video_quality?: {
                [key: string]: unknown;
            };
        };
        /** KovaaKRunListResponse */
        KovaaKRunListResponse: {
            /** Runs */
            runs: components["schemas"]["KovaaKRunListItem"][];
        };
        /** KovaaKScoreItem */
        KovaaKScoreItem: {
            /** Category */
            category: string;
            /** Completed */
            completed: boolean;
            /** Item Rank */
            item_rank: number;
            /** Item Rank Name */
            item_rank_name: string;
            /** Name */
            name: string;
            /** Score */
            score: number;
            /**
             * Stage
             * @enum {string}
             */
            stage: "easier" | "medium";
            /** Subcategory */
            subcategory: string;
        };
        /** KovaaKScoreStage */
        KovaaKScoreStage: {
            /** Completed */
            completed: number;
            /** Rank */
            rank: number;
            /** Rank Name */
            rank_name: string;
            /** Required */
            required: number;
            /**
             * Stage
             * @enum {string}
             */
            stage: "easier" | "medium";
        };
        /** KovaaKScoresResponse */
        KovaaKScoresResponse: {
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable";
            /** Items */
            items?: components["schemas"]["KovaaKScoreItem"][];
            /** Observed At */
            observed_at?: string | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "kovaak_scores.v1";
            /** Stages */
            stages?: components["schemas"]["KovaaKScoreStage"][];
        };
        /** OnboardingStateRequest */
        OnboardingStateRequest: {
            /**
             * Completed
             * @default true
             */
            completed: boolean;
            /**
             * Completion Kind
             * @enum {string}
             */
            completion_kind: "connected" | "legacy";
        };
        /** ProductStateResponse */
        ProductStateResponse: {
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable";
            /** Error */
            error?: {
                [key: string]: unknown;
            } | null;
            /** Has Analyses */
            has_analyses?: boolean | null;
            /** Has Pending Runs */
            has_pending_runs?: boolean | null;
            /** Has Runs */
            has_runs?: boolean | null;
            /** Onboarding Completed */
            onboarding_completed?: boolean | null;
            /** Onboarding Completion Kind */
            onboarding_completion_kind?: ("connected" | "legacy") | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "product_state.v1";
        };
        /** RunEvidenceRemovalResponse */
        RunEvidenceRemovalResponse: {
            /** Affected Modes */
            affected_modes: ("input_native" | "multimodal" | "video_fallback")[];
            /** Artifact Ref */
            artifact_ref?: string | null;
            /**
             * Availability
             * @constant
             */
            availability: "unavailable";
            /**
             * Evidence Kind
             * @enum {string}
             */
            evidence_kind: "video" | "raw";
            /** Reclaimed Bytes */
            reclaimed_bytes: number;
            /**
             * Removal State
             * @enum {string}
             */
            removal_state: "completed" | "pending_cleanup" | "already_unavailable";
            /** Run Ref */
            run_ref: string;
        };
        /** SessionListItem */
        SessionListItem: {
            /** Analysis Completed At */
            analysis_completed_at?: string | null;
            /** Analysis Ref */
            analysis_ref: string;
            /**
             * Analysis Type
             * @default flicking
             */
            analysis_type: string;
            /** Attempts */
            attempts: number;
            /** Created At */
            created_at: string;
            /** Finished At */
            finished_at?: string | null;
            /** Id */
            id: number;
            /**
             * Input Mode
             * @default video_fallback
             */
            input_mode: string;
            /** Kovaak Run Id */
            kovaak_run_id?: number | null;
            /** Llm Cost Cny */
            llm_cost_cny?: number | null;
            /** Max Attempts */
            max_attempts: number;
            /** Presentation Label */
            presentation_label?: string | null;
            /** Run Ref */
            run_ref?: string | null;
            /** Scenario */
            scenario?: string | null;
            /** Source Availability */
            source_availability?: {
                [key: string]: string;
            };
            /** Started At */
            started_at?: string | null;
            /** Status */
            status: string;
            /** Summary Label */
            summary_label?: string | null;
            trace_quality: components["schemas"]["TraceQualityOut"];
            /** Training At */
            training_at?: string | null;
        };
        /** SessionListResponse */
        SessionListResponse: {
            /** Sessions */
            sessions: components["schemas"]["SessionListItem"][];
        };
        /**
         * SessionStatus
         * @description GET /sessions/{id} — result is AnalysisResult v1 dict (validated at queue layer).
         */
        SessionStatus: {
            /** Analysis Completed At */
            analysis_completed_at?: string | null;
            /**
             * Analysis Type
             * @default flicking
             */
            analysis_type: string;
            /** Attempts */
            attempts: number;
            /** Created At */
            created_at: string;
            error?: components["schemas"]["ErrorV1"] | null;
            /** Finished At */
            finished_at?: string | null;
            history?: components["schemas"]["AnalysisHistoryDetailOut"] | null;
            /** Id */
            id: number;
            /**
             * Input Mode
             * @default video_fallback
             */
            input_mode: string;
            /** Kovaak Run Id */
            kovaak_run_id?: number | null;
            /** Llm Cost Cny */
            llm_cost_cny?: number | null;
            /** Max Attempts */
            max_attempts: number;
            /** Presentation Label */
            presentation_label?: string | null;
            /** Result */
            result?: {
                [key: string]: unknown;
            } | null;
            /** Started At */
            started_at?: string | null;
            /** Status */
            status: string;
            /** Task Phase */
            task_phase?: string | null;
            /** Training At */
            training_at?: string | null;
            /** Worker Id */
            worker_id?: string | null;
        };
        /** StorageCategoryTotals */
        StorageCategoryTotals: {
            /**
             * Analysis Artifacts Bytes
             * @default 0
             */
            analysis_artifacts_bytes: number;
            /**
             * Incomplete Recovery Bytes
             * @default 0
             */
            incomplete_recovery_bytes: number;
            /**
             * Run Raw Bytes
             * @default 0
             */
            run_raw_bytes: number;
            /**
             * Run Video Bytes
             * @default 0
             */
            run_video_bytes: number;
        };
        /** StorageResponse */
        StorageResponse: {
            categories: components["schemas"]["StorageCategoryTotals"];
            /** Sessions */
            sessions: components["schemas"]["StorageSessionItem"][];
            /** Total Bytes */
            total_bytes: number;
        };
        /** StorageSessionItem */
        StorageSessionItem: {
            /** Created At */
            created_at: string;
            /** Session Id */
            session_id: number;
            /** Status */
            status: string;
            /** Workspace Bytes */
            workspace_bytes: number;
        };
        /** TargetRelativeErrorRadius */
        TargetRelativeErrorRadius: {
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable";
            /** Points */
            points: components["schemas"]["TargetRelativeErrorRadiusPoint"][];
            /** Reason */
            reason?: string | null;
        };
        /** TargetRelativeErrorRadiusPoint */
        TargetRelativeErrorRadiusPoint: {
            /** Normalized Error Radius */
            normalized_error_radius: number;
            /** Relative Ms */
            relative_ms: number;
        };
        /** TaskAttempt */
        TaskAttempt: {
            /** Attempt Number */
            attempt_number: number;
            /** Attempt Ref */
            attempt_ref: string;
            /** Can Delete */
            can_delete: boolean;
            /** Created At */
            created_at?: string | null;
            failure?: components["schemas"]["TaskFailure"] | null;
            /** Finished At */
            finished_at?: string | null;
            partial_outcome?: components["schemas"]["TaskPartialOutcome"] | null;
            /** Phase */
            phase?: string | null;
            /**
             * Retryable
             * @default false
             */
            retryable: boolean;
            /** Started At */
            started_at?: string | null;
            /**
             * State
             * @enum {string}
             */
            state: "importing" | "queued" | "running" | "done" | "failed" | "retrying";
            /** State Label */
            state_label: string;
        };
        /** TaskDetailResponse */
        TaskDetailResponse: {
            /** Analysis Completed At */
            analysis_completed_at?: string | null;
            /** Analysis Ref */
            analysis_ref?: string | null;
            /** Analysis Type */
            analysis_type?: string | null;
            /** Attempt History */
            attempt_history?: components["schemas"]["TaskAttempt"][];
            /** Attempt Number */
            attempt_number?: number | null;
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable";
            /** Can Delete */
            can_delete?: boolean | null;
            /** Created At */
            created_at?: string | null;
            /** Error */
            error?: {
                [key: string]: unknown;
            } | null;
            failure?: components["schemas"]["TaskFailure"] | null;
            /** Finished At */
            finished_at?: string | null;
            /** Input Mode */
            input_mode?: string | null;
            partial_outcome?: components["schemas"]["TaskPartialOutcome"] | null;
            /** Phase */
            phase?: string | null;
            /** Phase Label */
            phase_label?: string | null;
            /** Presentation Label */
            presentation_label?: string | null;
            /** Retryable */
            retryable?: boolean | null;
            /** Run Ref */
            run_ref?: string | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "task_detail.v1";
            /** Started At */
            started_at?: string | null;
            /** State */
            state?: string | null;
            /** State Label */
            state_label?: string | null;
            /** Task Ref */
            task_ref?: string | null;
            /** Training At */
            training_at?: string | null;
        };
        /** TaskFailure */
        TaskFailure: {
            /** Code */
            code: string;
            /**
             * Domain
             * @enum {string}
             */
            domain: "source_file" | "alignment" | "kinematics" | "video" | "provider" | "coach" | "network";
            /** Message */
            message: string;
            /** Retryable */
            retryable: boolean;
        };
        /** TaskListResponse */
        TaskListResponse: {
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable";
            /** Error */
            error?: {
                [key: string]: unknown;
            } | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "task_list.v1";
            /** Tasks */
            tasks?: components["schemas"]["TaskDetailResponse"][];
        };
        /** TaskPartialOutcome */
        TaskPartialOutcome: {
            /** Native Preserved */
            native_preserved: boolean;
            /** Reason Code */
            reason_code: string;
            /**
             * Status
             * @constant
             */
            status: "partial";
            /** Visual Status */
            visual_status: string;
        };
        /** Timeline */
        Timeline: {
            /** Duration Frames */
            duration_frames?: number | null;
            /**
             * Events
             * @default []
             */
            events: components["schemas"]["TimelineEvent"][];
            /** Fps */
            fps?: number | null;
        };
        /** TimelineEvent */
        TimelineEvent: {
            /** Frame */
            frame?: number | null;
            /** Label */
            label: string;
            /** Relative Ms */
            relative_ms?: number | null;
            /** Source */
            source?: string | null;
            /** Time S */
            time_s?: number | null;
            /** Type */
            type: string;
        };
        /** TraceQualityOut */
        TraceQualityOut: {
            /** Alignment Status */
            alignment_status?: string | null;
            /** Availability */
            availability: string;
            /** Coverage */
            coverage?: number | null;
            /** State */
            state: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** VisualReplayOut */
        VisualReplayOut: {
            /** Artifact Ref */
            artifact_ref?: string | null;
            /** Available */
            available: boolean;
            /** Endpoint */
            endpoint?: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "seekable_mp4" | "native_only" | "unavailable";
            /** Reason */
            reason?: string | null;
            /** Seekable */
            seekable: boolean;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    analyze_api_analyze_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_analyze_api_analyze_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnalyzeResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_benchmarks_api_benchmarks_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BenchmarkRecordListResponse"];
                };
            };
        };
    };
    create_benchmark_api_benchmarks_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BenchmarkRecordCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BenchmarkRecordOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    sync_kovaak_benchmarks_api_benchmarks_sync_kovaaks_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKBenchmarkSyncResponse"];
                };
            };
        };
    };
    get_calibration_profile_api_calibration_profile_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CalibrationProfileOut"];
                };
            };
        };
    };
    save_calibration_profile_api_calibration_profile_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CalibrationProfileUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CalibrationProfileOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_calibration_profile_api_calibration_profile_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CalibrationProfileOut"];
                };
            };
        };
    };
    get_capture_status_api_capture_status_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaptureStatusResponse"];
                };
            };
        };
    };
    get_current_training_api_current_training_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CurrentTrainingResponse"];
                };
            };
        };
    };
    analyze_paths_api_desktop_analyze_paths_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnalyzePathsRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnalyzeResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_external_runs_api_external_runs_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExternalRunListResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_external_run_api_external_runs__external_run_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                external_run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExternalRunDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_external_telemetry_api_external_telemetry_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExternalTelemetryConfigResponse"];
                };
            };
        };
    };
    save_external_telemetry_watch_root_api_external_telemetry_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExternalTelemetryWatchRootUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExternalTelemetryConfigResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_history_trend_api_history_trends__metric_key__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                metric_key: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HistoryTrendResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_kovaak_connection_api_kovaak_connection_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKConnectionStatusResponse"];
                };
            };
        };
    };
    save_kovaak_connection_api_kovaak_connection_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKConnectionStatusResponse"];
                };
            };
        };
    };
    delete_kovaak_connection_api_kovaak_connection_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKConnectionDeleteResponse"];
                };
            };
        };
    };
    refresh_kovaak_connection_api_kovaak_connection_refresh_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKBenchmarkSyncResponse"];
                };
            };
        };
    };
    get_kovaak_local_directories_api_kovaak_local_directories_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKLocalDirectoriesResponse"];
                };
            };
        };
    };
    save_kovaak_local_directories_api_kovaak_local_directories_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KovaaKLocalDirectoriesUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKLocalDirectoriesResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_kovaak_runs_api_kovaak_runs_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKRunListResponse"];
                };
            };
        };
    };
    get_kovaak_run_api_kovaak_runs__run_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKRunItem"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    analyze_kovaak_run_api_kovaak_runs__run_id__analyze_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path: {
                run_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KovaaKAnalysisRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnalyzeResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    remove_kovaak_run_evidence_api_kovaak_runs__run_id__evidence__evidence_kind__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: number;
                evidence_kind: "video" | "raw";
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunEvidenceRemovalResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_kovaak_scores_api_kovaak_scores_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KovaaKScoresResponse"];
                };
            };
        };
    };
    get_product_state_api_product_state_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProductStateResponse"];
                };
            };
        };
    };
    set_product_onboarding_api_product_state_onboarding_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OnboardingStateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProductStateResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_sessions_api_sessions_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionListResponse"];
                };
            };
        };
    };
    get_session_api_sessions__session_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_session_api_sessions__session_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeleteSessionResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_session_analysis_data_api_sessions__session_id__analysis_data_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FrontendAnalysisDataResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_session_analysis_family_data_api_sessions__session_id__analysis_data_family_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: never;
            path: {
                session_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FrontendAnalysisFamilyDataResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_session_evidence_segments_api_sessions__session_id__evidence_segments_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FrontendEvidenceSegmentsResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retry_session_api_sessions__session_id__retry_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path: {
                session_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_session_timeline_api_sessions__session_id__timeline_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Timeline"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_session_video_api_sessions__session_id__video_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_storage_api_storage_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StorageResponse"];
                };
            };
        };
    };
    get_incomplete_capture_storage_api_storage_incomplete_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IncompleteCaptureListResponse"];
                };
            };
        };
    };
    remove_incomplete_capture_storage_api_storage_incomplete__item_ref__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                item_ref: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IncompleteCaptureRemovalResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_tasks_api_tasks_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskListResponse"];
                };
            };
        };
    };
    get_task_api_tasks__task_ref__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                task_ref: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    healthz_healthz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: boolean;
                    };
                };
            };
        };
    };
    readyz_readyz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
}

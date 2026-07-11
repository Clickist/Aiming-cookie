import json
import sys
from pathlib import Path

PROTOCOL = "analysis_tool_stdio.v0"
FIXTURE_ID = "analysis-fixture-1"


def write_json(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def write_error(request_id, code, message):
    write_json({
        "protocol": PROTOCOL,
        "request_id": request_id,
        "type": "error",
        "error": {
            "schema_version": "error.v1",
            "category": "local_cv_runtime",
            "code": code,
            "message": message,
            "retryable": False,
            "trace_id": None,
            "details": None,
        },
    })


def validated_request():
    raw_line = sys.stdin.readline()
    try:
        request = json.loads(raw_line)
    except (json.JSONDecodeError, TypeError):
        return None, ""
    if not isinstance(request, dict):
        return None, ""
    request_id = request.get("request_id")
    if (
        request.get("protocol") != PROTOCOL
        or request.get("operation") != "get_analysis_summary"
        or not isinstance(request_id, str)
        or not request_id
        or not isinstance(request.get("analysis_id"), str)
    ):
        return None, request_id if isinstance(request_id, str) else ""
    return request, request_id


def main():
    request, request_id = validated_request()
    if request is None:
        write_error(request_id, "analysis_request_invalid", "Analysis request is invalid")
        return
    if request["analysis_id"] != FIXTURE_ID:
        write_error(request_id, "analysis_not_found", "Analysis fixture not found")
        return

    write_json({
        "protocol": PROTOCOL,
        "request_id": request_id,
        "type": "progress",
        "stage": "loading_fixture",
        "message": "Loading analysis fixture",
    })
    fixture_path = Path(__file__).resolve().parent.parent / Path("fixtures/analysis-result.v1.json")
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        summary = {
            "analysis_id": FIXTURE_ID,
            "schema_version": fixture["schema_version"],
            "summary_type": fixture["summary_type"],
            "diagnosis": fixture["deterministic"]["diagnosis"],
            "notes": fixture["notes"],
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        write_error(request_id, "analysis_adapter_failed", "Analysis fixture could not be read")
        return
    write_json({
        "protocol": PROTOCOL,
        "request_id": request_id,
        "type": "result",
        "summary": summary,
    })


if __name__ == "__main__":
    main()

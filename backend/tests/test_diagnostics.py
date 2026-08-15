from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from ava_backend.diagnostics import (
    AvaJsonLogFormatter,
    RuntimeDiagnostics,
    configure_persistent_development_logging,
    new_failure_id,
)


def test_failure_ids_and_runtime_source_revision_are_safe_and_stale_aware(
    tmp_path,
) -> None:
    package_root = tmp_path / "ava_backend"
    package_root.mkdir()
    (package_root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime = RuntimeDiagnostics(
        instance_id="instance-test",
        started_at=datetime(2026, 8, 12, tzinfo=UTC),
        source_revision="sha256:not-the-current-source",
        package_root=package_root,
    )

    build = runtime.readiness_build()

    assert new_failure_id().startswith("failure-")
    assert build["sourceCurrent"] is False
    assert build["version"] == "0.1.0"


def test_development_json_log_is_persistent_structured_and_idempotent(
    tmp_path,
) -> None:
    log_path = tmp_path / "ava-backend.jsonl"
    first = configure_persistent_development_logging(configured_path=log_path)
    second = configure_persistent_development_logging(configured_path=log_path)
    assert first == second == log_path.resolve()

    logger = logging.getLogger("ava_backend.diagnostics_test")
    logger.error(
        "synthetic failure",
        extra={
            "ava_context": {
                "failureId": "failure-test",
                "component": "coordinator",
            }
        },
    )
    for handler in logging.getLogger("ava_backend").handlers:
        handler.flush()

    payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["message"] == "synthetic failure"
    assert payload["context"] == {
        "failureId": "failure-test",
        "component": "coordinator",
    }

    root = logging.getLogger("ava_backend")
    matching = [
        handler
        for handler in root.handlers
        if getattr(handler, "baseFilename", None) == str(log_path.resolve())
    ]
    assert len(matching) == 1
    root.removeHandler(matching[0])
    matching[0].close()


def test_json_log_formatter_rejects_non_mapping_context() -> None:
    formatter = AvaJsonLogFormatter()
    record = logging.LogRecord(
        name="ava_backend.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="bad context",
        args=(),
        exc_info=None,
    )
    record.ava_context = "not-a-mapping"  # type: ignore[attr-defined]

    try:
        formatter.format(record)
    except TypeError as error:
        assert "must be a mapping" in str(error)
    else:  # pragma: no cover - explicit failure makes the contract readable
        raise AssertionError("invalid diagnostic context was accepted")

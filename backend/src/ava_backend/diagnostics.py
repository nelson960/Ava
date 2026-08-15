from __future__ import annotations

import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from .version import AVA_BACKEND_VERSION


_LOG_HANDLER_MARKER = "ava_persistent_diagnostics"
_DEFAULT_LOG_BYTES = 4 * 1024 * 1024
_DEFAULT_LOG_BACKUPS = 3


def new_failure_id() -> str:
    return f"failure-{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class RuntimeDiagnostics:
    instance_id: str
    started_at: datetime
    source_revision: str
    package_root: Path

    @classmethod
    def capture(cls) -> RuntimeDiagnostics:
        package_root = Path(__file__).resolve().parent
        return cls(
            instance_id=f"instance-{uuid4().hex}",
            started_at=datetime.now(UTC),
            source_revision=_source_revision(package_root),
            package_root=package_root,
        )

    def readiness_build(self) -> dict[str, object]:
        current_revision = _source_revision(self.package_root)
        return {
            "version": AVA_BACKEND_VERSION,
            "instanceId": self.instance_id,
            "startedAt": self.started_at.isoformat(),
            "sourceRevision": self.source_revision,
            "sourceCurrent": current_revision == self.source_revision,
        }


class AvaJsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "ava_context", None)
        if context is not None:
            if not isinstance(context, Mapping):
                raise TypeError("ava_context logging extra must be a mapping")
            payload["context"] = dict(context)
        if record.exc_info is not None:
            exception_type = record.exc_info[0]
            payload["exception"] = {
                "type": exception_type.__name__ if exception_type is not None else None,
                "stack": self.formatException(record.exc_info),
            }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def configure_persistent_development_logging(
    *,
    configured_path: Path | None,
) -> Path:
    log_path = (
        configured_path.expanduser().resolve()
        if configured_path is not None
        else Path(__file__).resolve().parents[2] / ".logs" / "ava-backend.jsonl"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("ava_backend")
    for existing in logger.handlers:
        if (
            getattr(existing, _LOG_HANDLER_MARKER, False) is True
            and Path(getattr(existing, "baseFilename", "")).resolve() == log_path
        ):
            return log_path

    handler = RotatingFileHandler(
        log_path,
        maxBytes=_DEFAULT_LOG_BYTES,
        backupCount=_DEFAULT_LOG_BACKUPS,
        encoding="utf-8",
    )
    setattr(handler, _LOG_HANDLER_MARKER, True)
    handler.setLevel(logging.INFO)
    handler.setFormatter(AvaJsonLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(min(logger.level or logging.INFO, logging.INFO))
    return log_path


def _source_revision(package_root: Path) -> str:
    digest = hashlib.sha256()
    source_files = sorted(package_root.rglob("*.py"))
    if not source_files:
        raise RuntimeError(f"Ava backend source tree is empty: {package_root}")
    for path in source_files:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"

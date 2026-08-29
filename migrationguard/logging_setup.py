"""Structured JSON logging -- every stage logs through this, never
print(). Each run's log lands in run.jsonl next to the report and the
trajectory-disclosure file, so a run's full activity is reconstructable
from three files, none of which is scrollback."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(log_path: Path, *, also_console: bool = True) -> logging.Logger:
    logger = logging.getLogger("migrationguard")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    if also_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(console_handler)

    logger.propagate = False
    return logger

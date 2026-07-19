"""Structured JSON logging to stdout (Railway captures stdout)."""
import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if extra:
            entry.update(extra)
        return json.dumps(entry, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.LoggerAdapter:
    class FieldAdapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            fields = {k: v for k, v in kwargs.items() if k not in ("exc_info", "stack_info", "stacklevel")}
            for k in fields:
                kwargs.pop(k)
            kwargs["extra"] = {"extra_fields": fields}
            return msg, kwargs

    return FieldAdapter(logging.getLogger(name), {})

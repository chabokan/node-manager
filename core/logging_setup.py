"""Central structured logging for the node manager.

Both the web container and the host worker write to the same file inside the
mounted repository (``logs/node-manager.log``), so an operator can be asked for
a single file when something goes wrong. Every line carries ``service=`` and an
``event=`` name with ``key=value`` fields to make support analysis easy.

Secrets must never be logged: callers pass job names, keys, status codes and
error classes, never tokens, passwords or raw job payloads.
"""

import logging
import os
from logging.handlers import RotatingFileHandler


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG_FILE = os.path.join(BASE_DIR, "logs", "node-manager.log")
LOG_FORMAT = "%(asctime)s %(levelname)s service=%(service)s %(name)s %(message)s"
MARKER = "_node_manager_handler"
NOISY_LOGGERS = ("urllib3", "requests", "docker", "botocore", "boto3",
                 "s3transfer", "httpx")


class _ServiceFilter(logging.Filter):
    def __init__(self, service):
        super().__init__()
        self.service = service

    def filter(self, record):
        record.service = self.service
        return True


def configure_logging(service="node", path=None, level=logging.INFO):
    """Configure root logging once per process, without duplicating handlers."""
    root = logging.getLogger()
    if any(getattr(handler, MARKER, False) for handler in root.handlers):
        return logging.getLogger("node_manager")

    root.setLevel(level)
    path = path or os.environ.get("NODE_MANAGER_LOG_FILE") or DEFAULT_LOG_FILE
    formatter = logging.Formatter(LOG_FORMAT)
    handlers = []
    file_error = None
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3)
        handlers.append(file_handler)
    except OSError as exc:
        file_error = exc
    stream_handler = logging.StreamHandler()
    handlers.append(stream_handler)

    for handler in handlers:
        setattr(handler, MARKER, True)
        handler.setFormatter(formatter)
        handler.addFilter(_ServiceFilter(service))
        root.addHandler(handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    log_event(logging.getLogger("node_manager"), "logging_configured",
              log_file=path if file_error is None else None,
              write_error=type(file_error).__name__ if file_error else None)
    return logging.getLogger("node_manager")


def log_event(logger, event, level=logging.INFO, **fields):
    """Emit one greppable ``event=... key=value`` line."""
    parts = [f"event={event}"]
    for key in sorted(fields):
        value = fields[key]
        if value is None:
            continue
        text = str(value).replace("\r", " ").replace("\n", " ")[:200]
        parts.append(f"{key}={text}")
    logger.log(level, " ".join(parts))


def log_transition(previous, key, ok, logger, ok_event, fail_event, **fields):
    """Log hub connectivity only when it changes, so outages do not flood."""
    if previous.get(key) is ok:
        return ok
    previous[key] = ok
    log_event(logger, ok_event if ok else fail_event,
              level=logging.INFO if ok else logging.WARNING, **fields)
    return ok
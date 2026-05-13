"""Structured logging setup.

Two output modes, picked by LOG_FORMAT env (default `text`):

  text  — human-readable colored output, what `scripts/dev.sh` shows.
  json  — single-line JSON per record, ready to ship to ELK / loki /
          datadog / cloudwatch.

Both modes go through structlog so log calls in our own code can use
key=value structured fields:

    logger = structlog.get_logger(__name__)
    logger.info("episode written", episode_id=ep.id, namespace=ns.raw)

uvicorn's own loggers (`uvicorn`, `uvicorn.access`) are also reshaped
into the same format so the per-request access lines look consistent.
"""
from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Call once during app startup. Idempotent."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    # The shared processor chain for both structlog and stdlib records.
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    if fmt == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processor=renderer,
        )
    )

    root = logging.getLogger()
    # Replace any handlers configured by `logging.basicConfig` so we
    # have a single source of truth.
    root.handlers = [handler]
    root.setLevel(log_level)

    # uvicorn installs its own handlers in `configure_logging`. Strip
    # them so the records flow through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

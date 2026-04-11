"""
Logging setup — UI-safe logging for engine modules.

WHY: Converting print() to logging.info() in engine modules is
normally a win (structured output, level filtering, redirection),
but it breaks the trade-bot UI's stdout capture mechanism.

The UI captures engine output in two ways:

  1. subprocess.Popen with stdout=PIPE — the subprocess's real
     stdout file descriptor is piped into the panel's text widget.
     Standard logging.StreamHandler(sys.stdout) works here because
     the subprocess's sys.stdout is the same FD as the pipe.

  2. sys.stdout = io.StringIO() monkey-patch — the panel replaces
     sys.stdout with an in-memory buffer, runs an engine function
     that is imported (not subprocessed), and reads the buffer after
     the function returns. Used by run_scenarios.py and the yfinance
     download path in configuration.py.

Standard logging.StreamHandler(sys.stdout) BREAKS pattern 2 because
StreamHandler captures the sys.stdout reference at handler creation
time. When the panel reassigns sys.stdout later, the handler keeps
writing to the ORIGINAL stdout and the buffer stays empty.

FIX: LazyStdoutHandler reads sys.stdout on every emit() call. When
the panel monkey-patches sys.stdout to a StringIO, the very next
logger.info() call writes into the buffer. This makes logging
transparent to both capture patterns.

USAGE:
    from shared.logging_setup import get_logger
    log = get_logger(__name__)

    log.info(f"Loading candles from {path}")
    log.warning(f"Missing column {col}")
    log.error(f"Failed to parse: {e}")

CHANGED: April 2026 — new module for UI-safe logging (Phase 19d)
"""

import logging
import sys


class LazyStdoutHandler(logging.Handler):
    """Logging handler that reads sys.stdout on every emit.

    Unlike logging.StreamHandler(sys.stdout) which captures the
    stdout reference at construction time, this handler looks up
    sys.stdout fresh every time a log record is emitted. This makes
    it compatible with the UI's sys.stdout = io.StringIO() monkey-patch
    pattern used to capture engine output in imported-function mode.
    """

    def emit(self, record):
        try:
            msg = self.format(record)
            # Read sys.stdout NOW, not at handler construction time
            stream = sys.stdout
            stream.write(msg + "\n")
            # flush() may not exist on all stream replacements — tolerate
            try:
                stream.flush()
            except Exception:
                pass
        except Exception:
            self.handleError(record)


_configured_loggers = set()


def get_logger(name):
    """Return a logger with a LazyStdoutHandler attached.

    Each logger is configured only once. Subsequent calls return the
    already-configured logger without duplicating handlers.

    The format matches the old print() output style: just the message,
    no timestamps or logger-name prefix. This keeps the UI panels'
    output identical to the pre-refactor behavior.

    Callers should use module-level __name__ as the logger name:
        log = get_logger(__name__)
    """
    logger = logging.getLogger(name)

    if name not in _configured_loggers:
        handler = LazyStdoutHandler()
        # WHY: Match the current print() output exactly — no prefixes,
        #      no timestamps, no level indicators. This keeps the UI
        #      panels' visible output identical to the pre-refactor
        #      behavior. If users want structured logging later, they
        #      can change the format here.
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # WHY: Don't propagate to the root logger. Some environments
        #      configure the root logger with a different handler (e.g.,
        #      file logging or syslog) that could cause double-logging
        #      or different formatting. Each engine module's logger is
        #      self-contained.
        logger.propagate = False
        _configured_loggers.add(name)

    return logger


def reset_loggers_for_testing():
    """Test-only helper to reset the configured-loggers cache.

    Useful when a test needs to verify logger behavior in isolation
    without interference from prior test runs.
    """
    global _configured_loggers
    for name in _configured_loggers:
        logger = logging.getLogger(name)
        logger.handlers.clear()
    _configured_loggers = set()

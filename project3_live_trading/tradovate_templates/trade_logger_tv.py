"""
Trade logger for Tradovate bot — CSV logging matching MT5 EA format.
The same CSV format is read by ea_verifier.py for comparison.
"""

import csv
from datetime import datetime, timezone
import os

# WHY (Phase 38 Fix 5): Platform-detect file locking primitives so
#      multi-process writes to the same CSV don't interleave. On
#      POSIX: fcntl.flock. On Windows: msvcrt.locking. Import-guarded
#      so missing modules don't break anything — locking degrades to
#      no-op.
# CHANGED: April 2026 — Phase 38 Fix 5 — cross-platform advisory lock
#          (audit Part C MED #57)
try:
    import fcntl as _fcntl
    _LOCK_EX = _fcntl.LOCK_EX
    _LOCK_UN = _fcntl.LOCK_UN
    def _lock_file(fh):
        try:
            _fcntl.flock(fh.fileno(), _LOCK_EX)
        except Exception:
            pass
    def _unlock_file(fh):
        try:
            _fcntl.flock(fh.fileno(), _LOCK_UN)
        except Exception:
            pass
except ImportError:
    try:
        import msvcrt as _msvcrt
        def _lock_file(fh):
            try:
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_LOCK, 1)
            except Exception:
                pass
        def _unlock_file(fh):
            try:
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
    except ImportError:
        def _lock_file(fh):
            pass
        def _unlock_file(fh):
            pass


class TradeLogger:
    FIELDS = [
        "timestamp", "symbol", "direction", "lots", "entry_price",
        "exit_price", "net_pips", "exit_reason",
        "entry_time", "exit_time", "skip_reason",
    ]

    def __init__(self, log_path):
        self.log_path = log_path

        # WHY (Phase 38 Fix 4): Old code checked os.path.exists then
        #      opened in 'a' mode. Two processes racing through the
        #      check both saw is_new=True and both wrote the header,
        #      producing a CSV with two header rows. Use O_EXCL|O_CREAT
        #      which is atomic: only one process can successfully
        #      create the file. Winner writes the header; losers skip.
        # CHANGED: April 2026 — Phase 38 Fix 4 — atomic header write
        #          (audit Part C MED #56)
        _we_created = False
        try:
            _fd = os.open(log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(_fd)
            _we_created = True
        except FileExistsError:
            # Another process got there first; file exists, header
            # already written (or will be) by the creator.
            _we_created = False

        self._fh = open(log_path, 'a', newline='', buffering=1, encoding='utf-8')
        self._writer = csv.DictWriter(self._fh, fieldnames=self.FIELDS, extrasaction='ignore')
        if _we_created:
            self._writer.writeheader()
            self._fh.flush()

    def log_open(self, symbol, direction, lots, entry_price, sl, tp):
        now = datetime.now(timezone.utc).isoformat()
        # CHANGED: April 2026 — Phase 38 Fix 5b — advisory lock
        _lock_file(self._fh)
        try:
            self._writer.writerow({
                "timestamp":    now,
                "symbol":       symbol,
                "direction":    direction,
                "lots":         round(lots, 2),
                "entry_price":  round(entry_price, 5),
                "exit_price":   "",
                "net_pips":     "",
                "exit_reason":  "",
                "entry_time":   now,
                "exit_time":    "",
                "skip_reason":  "",
            })
            self._fh.flush()
        finally:
            _unlock_file(self._fh)

    # WHY (Phase 34 Fix 3): Old code hardcoded entry_time = "" on close
    #      rows, so downstream readers couldn't join close rows back to
    #      their matching open rows except by fragile timestamp
    #      proximity. Accept entry_time as an optional parameter
    #      (default ""). New callers pass the open timestamp captured
    #      at log_open time; old callers still work (empty string
    #      stays, matching pre-fix behavior).
    # CHANGED: April 2026 — Phase 34 Fix 3 — entry_time parameter
    #          (audit Part C HIGH #54)
    def log_close(self, symbol, direction, lots, entry_price, exit_price, net_pips, exit_reason, entry_time=""):
        now = datetime.now(timezone.utc).isoformat()
        # CHANGED: April 2026 — Phase 38 Fix 5c — advisory lock
        _lock_file(self._fh)
        try:
            self._writer.writerow({
                "timestamp":    now,
                "symbol":       symbol,
                "direction":    direction,
                "lots":         round(lots, 2),
                "entry_price":  round(entry_price, 5),
                "exit_price":   round(exit_price, 5),
                "net_pips":     round(net_pips, 1),
                "exit_reason":  exit_reason,
                "entry_time":   entry_time,
                "exit_time":    now,
                "skip_reason":  "",
            })
            self._fh.flush()
        finally:
            _unlock_file(self._fh)

    def log_skip(self, symbol, skip_reason):
        now = datetime.now(timezone.utc).isoformat()
        # CHANGED: April 2026 — Phase 38 Fix 5d — advisory lock
        _lock_file(self._fh)
        try:
            self._writer.writerow({
                "timestamp":   now,
                "symbol":      symbol,
                "direction":   "SKIP",
                "lots":        0,
                "entry_price": 0,
                "exit_price":  0,
                "net_pips":    0,
                "exit_reason": "",
                "entry_time":  now,
                "exit_time":   "",
                "skip_reason": skip_reason,
            })
            self._fh.flush()
        finally:
            _unlock_file(self._fh)

    def close(self):
        self._fh.close()

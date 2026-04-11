"""
Trade logger for Tradovate bot — CSV logging matching MT5 EA format.
The same CSV format is read by ea_verifier.py for comparison.
"""

import csv
from datetime import datetime, timezone
import os


class TradeLogger:
    FIELDS = [
        "timestamp", "symbol", "direction", "lots", "entry_price",
        "exit_price", "net_pips", "exit_reason",
        "entry_time", "exit_time", "skip_reason",
    ]

    def __init__(self, log_path):
        self.log_path = log_path
        is_new = not os.path.exists(log_path) or os.path.getsize(log_path) == 0
        self._fh = open(log_path, 'a', newline='', buffering=1, encoding='utf-8')
        self._writer = csv.DictWriter(self._fh, fieldnames=self.FIELDS, extrasaction='ignore')
        if is_new:
            self._writer.writeheader()
            self._fh.flush()

    def log_open(self, symbol, direction, lots, entry_price, sl, tp):
        now = datetime.now(timezone.utc).isoformat()
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

    def log_skip(self, symbol, skip_reason):
        now = datetime.now(timezone.utc).isoformat()
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

    def close(self):
        self._fh.close()

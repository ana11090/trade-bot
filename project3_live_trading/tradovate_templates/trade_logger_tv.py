"""
Trade logger for Tradovate bot — CSV logging matching MT5 EA format.
The same CSV format is read by ea_verifier.py for comparison.
"""

import csv
import datetime
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
        now = datetime.datetime.utcnow().isoformat()
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

    def log_close(self, symbol, direction, lots, entry_price, exit_price, net_pips, exit_reason):
        now = datetime.datetime.utcnow().isoformat()
        self._writer.writerow({
            "timestamp":    now,
            "symbol":       symbol,
            "direction":    direction,
            "lots":         round(lots, 2),
            "entry_price":  round(entry_price, 5),
            "exit_price":   round(exit_price, 5),
            "net_pips":     round(net_pips, 1),
            "exit_reason":  exit_reason,
            "entry_time":   "",
            "exit_time":    now,
            "skip_reason":  "",
        })
        self._fh.flush()

    def log_skip(self, symbol, skip_reason):
        now = datetime.datetime.utcnow().isoformat()
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

"""
Risk manager for Tradovate bot — DD limits, daily safety checks.
Mirrors the risk logic in the MT5 EA (risk_manager.mqh).
"""


class RiskManager:
    def __init__(self, session_equity, daily_dd_pct=5.0, total_dd_pct=10.0, safety_pct=80.0):
        self.session_equity = session_equity
        self.daily_dd_pct   = daily_dd_pct
        self.total_dd_pct   = total_dd_pct
        self.safety_pct     = safety_pct
        self.stop_for_day   = False
        self.stop_forever   = False

    def check(self, current_equity):
        """Check drawdown levels. Returns (ok, reason)."""
        daily_loss  = self.session_equity - current_equity
        daily_limit = self.session_equity * self.daily_dd_pct / 100.0

        if daily_loss >= daily_limit * self.safety_pct / 100.0:
            self.stop_for_day = True
            return False, f"daily_dd_limit ({daily_loss:.2f} >= {daily_limit * self.safety_pct / 100:.2f})"

        total_dd = self.session_equity - current_equity
        if total_dd >= self.session_equity * self.total_dd_pct / 100.0:
            self.stop_forever = True
            return False, f"total_dd_limit ({total_dd:.2f})"

        return True, "ok"

    def reset_day(self, new_equity):
        """Call at start of each trading day."""
        self.session_equity = new_equity
        self.stop_for_day   = False

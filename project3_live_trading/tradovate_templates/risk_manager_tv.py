"""
Risk manager for Tradovate bot — DD limits, daily safety checks.
Mirrors the risk logic in the MT5 EA (risk_manager.mqh).
"""


class RiskManager:
    # WHY: Old class used a single session_equity field as the reference
    #      for BOTH daily DD and total DD. reset_day() overwrote
    #      session_equity every morning, so the total DD limit was
    #      measured against today's opening balance — it could never
    #      trigger across multi-day losing streaks. A trader losing 2%/day
    #      for 5 days had total_dd=0 every morning. Introduce a separate
    #      starting_equity set once at construction and never reset; use
    #      it as the total-DD denominator.
    # WHY (stop_forever): Old check() did not test self.stop_forever on
    #      entry. After a total-DD breach, the next call re-evaluated
    #      against the reset session_equity and returned True. The
    #      "irreversible" flag was not actually checked.
    # CHANGED: April 2026 — Phase 29 Fix 1 — HWM-based total DD + stop_forever
    #          guard (audit Part C crit #10 + HIGH #50)
    def __init__(self, session_equity, daily_dd_pct=5.0, total_dd_pct=10.0, safety_pct=80.0):
        self.starting_equity = session_equity   # set once, never reset
        self.session_equity  = session_equity   # daily reference, reset by reset_day()
        self.daily_dd_pct    = daily_dd_pct
        self.total_dd_pct    = total_dd_pct
        self.safety_pct      = safety_pct
        self.stop_for_day    = False
        self.stop_forever    = False
        # WHY (Phase 34 Fix 4): Old code returned (ok, reason) without a
        #      way for callers to distinguish graceful day-stop from
        #      permanent lock without string-parsing the reason. Set
        #      last_level during each check() so callers read one
        #      attribute to know what state the RM is in.
        # CHANGED: April 2026 — Phase 34 Fix 4 — last_level attribute
        #          (audit Part C HIGH #51)
        self.last_level      = 'ok'   # 'ok' | 'day_stop' | 'locked'

    def check(self, current_equity):
        """Check drawdown levels. Returns (ok, reason).

        Also sets self.last_level to one of:
          'ok'        — all clear, trading allowed
          'day_stop'  — daily safety margin hit, stop for today only
                        (reset_day() the next morning clears the flag)
          'locked'    — total DD breached, account permanently stopped
                        (stop_forever set, no reset can clear it)

        Callers should read self.last_level after a False return to
        decide whether to close for the day or shut down permanently.
        """
        # WHY: Once total DD has been breached the account is dead. Don't
        #      let a later call re-evaluate against reset state and return
        #      True.
        # CHANGED: April 2026 — Phase 29 Fix 1 — stop_forever early-return
        if self.stop_forever:
            self.last_level = 'locked'
            return False, "total_dd_locked"

        daily_loss  = self.session_equity - current_equity
        daily_limit = self.session_equity * self.daily_dd_pct / 100.0

        if daily_loss >= daily_limit * self.safety_pct / 100.0:
            self.stop_for_day = True
            self.last_level = 'day_stop'
            return False, f"daily_dd_limit ({daily_loss:.2f} >= {daily_limit * self.safety_pct / 100:.2f})"

        # WHY: Total DD must be measured from account-open HWM, not from
        #      this morning's session reset. Use starting_equity.
        # CHANGED: April 2026 — Phase 29 Fix 1 — total DD from starting_equity
        total_dd = self.starting_equity - current_equity
        if total_dd >= self.starting_equity * self.total_dd_pct / 100.0:
            self.stop_forever = True
            self.last_level = 'locked'
            return False, f"total_dd_limit ({total_dd:.2f})"

        self.last_level = 'ok'
        return True, "ok"

    def reset_day(self, new_equity):
        """Call at start of each trading day. Resets DAILY DD reference only —
        starting_equity is preserved so total DD continues accumulating."""
        self.session_equity = new_equity
        self.stop_for_day   = False

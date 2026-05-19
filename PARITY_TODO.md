# Backtester ↔ Simulator ↔ EA Parity TODO

This file tracks known divergences between the three pieces of code that
model the prop firm's behavior:

- **Simulator**: `shared/prop_firm_simulator.py` — scores historical
  pass/fail (used by refiner eval panel and validator panel).
- **Backtester**: `project2_backtesting/strategy_backtester.py` — emits
  the trade list (uses DD circuit breaker to halt entries when risky).
- **EA Generator**: `project3_live_trading/ea_generator.py` — emits MQL5
  that enforces DD/safety rules in live trading.

These three are NOT supposed to be identical. They solve different
problems: the simulator scores, the backtester emits trades, the EA
trades live. But they MUST share the same firm-rule assumptions or the
backtest will mislead about live behavior.

---

## Status legend

- ✅ **Aligned** — all three implement the same rule the same way.
- ⚠️ **Partial** — implemented in some but not all; toggleable; or
  approximated.
- ❌ **Diverges** — actively disagrees; known issue.

---

## Items

### 1. HWM lock-at-gain — ⚠️ Partial (toggleable May 2026)

- Simulator: implements via `_simulate_phase` L289-311. Default ON.
- EA: implements via `ea_generator.py` L1172-1208. Default ON.
- Backtester: NOT implemented by default. Toggle via `use_hwm_lock=True`
  parameter to `run_backtest`/`fast_backtest`, or the
  "Use HWM-lock (match EA)" checkbox in the panel. With the toggle OFF
  the backtester's `_dd_hwm` trails forever — more pessimistic than the
  EA on any strategy that triggers `lock_after_gain_pct`.

**Why it matters**: A strategy that triggers HWM lock will have the
backtester's DD circuit breaker halting earlier than the EA would in
production. So fewer trades make it into the backtest than would happen
live, biasing the eval simulator's pass rate downward.

**Resolution path**: Default may flip to ON later. For now the toggle
exists so the user can run both versions and compare.

---

### 2. Daily safety stop — ⚠️ Partial — different mechanisms, different thresholds

- Simulator: `_apply_daily_safety` truncates day's trades when running
  daily loss reaches `daily_dd_safety_pct` (% of firm's daily DD limit).
  Default 80%; refiner overrides to 90% via `_sum_daily_lim * 0.9`.
- Backtester: `dd_daily_alert_pct` threshold from firm config
  `trading_rules.parameters.daily_dd_alert_pct`. Halts entries when daily
  P&L hits this. For Get Leveraged eval this is 2.7% (firm limit is 3%).
- EA: `EvalDailyDDAlert` from the same firm-config field as backtester.
  Same numeric value (2.7%). Closes positions + stops for day.

**Why it matters**: Backtester and EA happen to use the same value
(2.7% for Leveraged), so they agree. The simulator derives its
threshold differently — 80-90% of the firm's actual daily DD limit. For
Leveraged at 90% × 3% = 2.7%, this happens to match; at default 80% =
2.4%, the simulator stops earlier. Inconsistent threshold derivation
even when numeric values happen to coincide.

**Resolution path**: The simulator should pull `daily_dd_safety_pct`
from the firm config's `daily_dd_alert_pct` directly, not derive it from
the firm's hard limit × an arbitrary percentage. Single source of truth.

---

### 3. Intraday DD modeling — ❌ Diverges across all three

- Simulator: approximates from the daily trade list (Phase 71 Fix 10,
  L331-348). Computes peak-to-trough drawdown by replaying the day's
  trades in close-order — captures intra-day-list excursions but cannot
  see actual equity excursions between trade close times.
- Backtester: uses net daily P&L only. A day of +500 then -800 then +200
  (net -100) is treated as a -100 day. Misses the -1300 excursion
  entirely. Does NOT trigger daily DD alert on the excursion.
- EA: uses true tick-level equity. Sees every drawdown moment-by-moment.
  Will trigger `EvalDailyDDAlert` on the -1300 excursion above.

**Why it matters**: For strategies with bursty intraday losses, the EA
will close positions on the intraday low while the backtester keeps
trading and the simulator scores something in between. Live behavior
will be more conservative than either backtest or simulator suggests.

**Resolution path**: Port the simulator's trade-list peak-to-trough
calculation (L339-348) into the backtester's DD circuit breaker. Won't
match the EA's tick-level precision but will be closer than current
net-daily-only behavior. New parameter `intraday_dd_approx=False`
defaulted off; toggle on for parity testing.

---

### 4. Daily DD reference — ⚠️ Partial

- Simulator: `balance at start of day` (closed P&L based). For firms
  with `max_balance_equity` reference, uses `balance - day_pnl` (start
  of day).
- Backtester: `_dd_ref_equity = balance at start of day`. Same as
  simulator.
- EA: `g_dailyReference = max(balance, equity) at reset time`. Captures
  unrealized P&L on positions open at reset.

**Why it matters**: For a strategy with positions held overnight, the
EA sees the equity component at reset; simulator and backtester only see
the closed balance. Strategies that close all positions before reset
won't be affected.

**Resolution path**: For most strategies (close before reset) this is a
non-issue. Document the caveat in `prop_firm_simulator.py`'s docstring;
no code change for now.

---

### 5. Window / time concept — ✅ Aligned by design

- Simulator: slides a hypothetical eval window across the trade
  history. Each window starts on a different date.
- Backtester: runs continuously from the start of data to the end. No
  window concept.
- EA: runs continuously from deployment. No window concept.

This is correct by design. The simulator overlays a window on the
backtester's output; the backtester and EA produce a continuous trade
stream and don't know about windows. No fix needed.

---

## Maintenance rule

Any change to DD logic, HWM logic, profit target, consistency rule, or
daily safety in any of these three files should:

1. Look for the **PARITY NOTE** comment near the change.
2. Check this file to see whether the change affects an aligned, partial,
   or diverged item.
3. Update this file accordingly: add a new item if a new divergence
   appears; flip an item to ✅ if a divergence gets fixed; flip to ❌
   if a regression is introduced.

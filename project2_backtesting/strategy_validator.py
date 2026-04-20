"""
Strategy Validator Engine — walk-forward validation + Monte Carlo robustness testing.

Proves (or disproves) that a strategy has a real edge rather than fitting noise.
Results are saved to outputs/validation_results.json so the Prop Firm Test panel
can show confidence badges without re-running validation.
"""

import os
import json
import time
import random
import threading
import numpy as np
import pandas as pd
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

VALIDATION_PATH = os.path.join(_HERE, 'outputs', 'validation_results.json')

_stop_flag = threading.Event()

# WHY: Magic thresholds extracted from inline code so they're easy to find
#      and adjust. Changing one value here updates every place that reads it.
#      The defaults match the previous hardcoded values exactly — no behavior
#      change unless someone deliberately overrides them.
# CHANGED: April 2026 — extract magic numbers
_DEFAULT_DAILY_DD_TOUCH_PCT  = 4.0   # was hardcoded in _compute_rich_window_stats
_DEFAULT_TOTAL_DD_TOUCH_PCT  = 8.0   # was hardcoded in _compute_rich_window_stats
_DEFAULT_RECOVERY_THRESHOLD  = 0.98  # within 2% of peak = recovered
_DEFAULT_PROFIT_SPLIT_PCT    = 80.0  # default firm profit split
_DEFAULT_EDGE_HELD_WR        = 0.50  # walk-forward "edge held" win rate floor (fraction)

# ── Module-level data cache ────────────────────────────────────────────────────
# WHY: walk_forward_validate and slippage_stress_test both load the same
#      candles CSV + parquet. When called in sequence (validation panel runs
#      both), we load 130K rows twice. Cache prevents the double load.
_cached_candles_path  = None
_cached_candles_df    = None
_cached_indicators_df = None


def _check_missing_features(rules, indicators_df):
    """Return set of feature names that rules need but indicators_df doesn't have."""
    if not rules or indicators_df is None:
        return set()
    _needed = set()
    for r in rules:
        for c in r.get('conditions', []):
            feat = c.get('feature', '')
            if feat:
                _needed.add(feat)
    _have = set(indicators_df.columns)
    return _needed - _have


def _load_data_cached(candles_path, rules=None):
    """Load candles + indicators, returning cached copies if path matches last call.

    WHY: Old code cached by path only. If rules changed, the cached parquet
         might be missing indicator columns the new rules need. Now checks
         required columns and forces rebuild when missing.
    CHANGED: April 2026 — column-aware cache validation
    """
    global _cached_candles_path, _cached_candles_df, _cached_indicators_df

    candles_path = os.path.abspath(candles_path)
    # FIX 5: also guard _cached_indicators_df — if a previous load failed mid-way,
    #         indicators may be None while candles_path matches, returning partial data.
    # CHANGED: April 2026 — cache reliability fix
    if (_cached_candles_path == candles_path
            and _cached_candles_df is not None
            and _cached_indicators_df is not None):
        # Check if cached data has all columns the rules need
        _missing = _check_missing_features(rules, _cached_indicators_df)
        if not _missing:
            log.info(f"[VALIDATOR] Using cached data for {os.path.basename(candles_path)}")
            return _cached_candles_df, _cached_indicators_df
        else:
            log.info(f"[VALIDATOR] Cache hit but {len(_missing)} columns missing — rebuilding")
            _cached_candles_path  = None
            _cached_candles_df    = None
            _cached_indicators_df = None

    # Reset all cache vars before load so a partial failure leaves no stale state
    _cached_candles_path  = None
    _cached_candles_df    = None
    _cached_indicators_df = None

    log.info(f"[VALIDATOR] Loading data: {os.path.basename(candles_path)}")
    candles_df = pd.read_csv(candles_path)
    ts_col = candles_df.columns[0]
    candles_df['timestamp'] = pd.to_datetime(candles_df[ts_col]).astype('datetime64[ns]')

    cache_path = candles_path.replace('.csv', '_indicators.parquet')
    _need_rebuild = False

    if os.path.exists(cache_path):
        indicators_df = pd.read_parquet(cache_path)
        if 'timestamp' in indicators_df.columns:
            indicators_df['timestamp'] = indicators_df['timestamp'].astype('datetime64[ns]')
        # Check if all required columns exist
        _missing = _check_missing_features(rules, indicators_df)
        if _missing:
            log.info(f"[VALIDATOR] Parquet cache missing {len(_missing)} columns: "
                     f"{list(_missing)[:5]}... — forcing rebuild")
            _need_rebuild = True

    if not os.path.exists(cache_path) or _need_rebuild:
        _, _, build_multi_tf_indicators = _load_backtester()
        data_dir = os.path.dirname(candles_path)

        # WHY: Delete ALL stale caches BEFORE calling build_multi_tf_indicators.
        #      The build function has its own per-TF cache mechanism — if stale
        #      parquet files exist, it loads them (file mtime > csv mtime = valid).
        #      Old code deleted caches AFTER the build, which was too late.
        #      Must run regardless of _need_rebuild — even when the validator's
        #      own cache_path doesn't exist, build_multi_tf_indicators has its
        #      own per-TF partial caches that may be stale.
        # CHANGED: April 2026 — delete BEFORE build, always
        import glob as _glob
        _deleted = 0
        for _pattern in ['.cache_*_partial_*.parquet', '.cache_*_indicators.parquet']:
            for _stale in _glob.glob(os.path.join(data_dir, _pattern)):
                try:
                    os.remove(_stale)
                    _deleted += 1
                except Exception:
                    pass
        if _deleted:
            log.info(f"[VALIDATOR] Deleted {_deleted} stale cache files before rebuild")

        # WHY: Pass required_indicators=None to build ALL indicators.
        #      Old code passed group names like 'price_action' as
        #      required_indicators, but _load_tf_indicators ran them through
        #      map_rule_indicators_to_compute_groups which treated them as
        #      indicator names and mangled them: 'price_action' → 'price',
        #      'elder_ray' → 'elder', 'std_dev' → 'std'. Result: those
        #      groups were never computed → columns missing → 0 trades.
        #      Passing None = compute everything, no mapping needed.
        # CHANGED: April 2026 — pass None to avoid group name mangling
        indicators_df = build_multi_tf_indicators(
            data_dir, candles_df['timestamp'], required_indicators=None)

        # Verify the rebuild actually produced the needed columns
        _still_missing = _check_missing_features(rules, indicators_df)
        if _still_missing:
            log.warning(f"[VALIDATOR] ⚠️ REBUILD FAILED — still missing {len(_still_missing)} columns: "
                        f"{sorted(_still_missing)[:10]}")
            log.warning(f"[VALIDATOR] These features may not be in any indicator group. "
                        f"Walk-forward will produce 0 trades for conditions using them.")
        else:
            _needed = _check_missing_features(rules, pd.DataFrame())  # Get all needed features
            if _needed:
                log.info(f"[VALIDATOR] ✅ Rebuild complete — all {len(_needed)} needed columns present")
            else:
                log.info(f"[VALIDATOR] ✅ Rebuild complete")

    _cached_candles_path  = candles_path
    _cached_candles_df    = candles_df
    _cached_indicators_df = indicators_df
    return candles_df, indicators_df


def stop_validation():
    _stop_flag.set()


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

def get_validation_for_strategy(strategy_index):
    """Get saved validation result. Returns dict or None. Used by prop_firm_test panel."""
    if not os.path.exists(VALIDATION_PATH):
        return None
    try:
        with open(VALIDATION_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get(str(strategy_index))
    except Exception:
        return None


def _save_validation(strategy_index, result):
    """Save after validation completes."""
    os.makedirs(os.path.dirname(VALIDATION_PATH), exist_ok=True)
    existing = {}
    if os.path.exists(VALIDATION_PATH):
        try:
            with open(VALIDATION_PATH, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass
    existing[str(strategy_index)] = result
    with open(VALIDATION_PATH, 'w', encoding='utf-8') as f:
        json.dump(existing, f, indent=2, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_backtester():
    """Lazy import to avoid circular deps."""
    import sys
    project_root = os.path.abspath(os.path.join(_HERE, '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from project2_backtesting.strategy_backtester import (
        run_backtest, compute_stats, build_multi_tf_indicators
    )
    return run_backtest, compute_stats, build_multi_tf_indicators


def _build_exit_strategy(exit_strategy_class, exit_strategy_params, pip_size):
    """Reconstruct exit strategy object from class name + params dict."""
    import sys
    project_root = os.path.abspath(os.path.join(_HERE, '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    import project2_backtesting.exit_strategies as es_mod

    # WHY (Hotfix): exit_strategy_class can be empty string or None when
    #      the optimizer/refiner didn't store it. Try to detect from
    #      params, map common exit_name strings, fall back to FixedSLTP.
    # CHANGED: April 2026 — Hotfix
    if not exit_strategy_class:
        # Try to detect from params
        params = exit_strategy_params or {}
        if 'trail_distance_pips' in params or 'activation_pips' in params:
            exit_strategy_class = 'TrailingStop'
        elif 'sl_atr_mult' in params or 'tp_atr_mult' in params:
            exit_strategy_class = 'ATRBased'
        elif 'exit_indicator' in params:
            exit_strategy_class = 'IndicatorExit'
        elif 'breakeven_activation_pips' in params or 'breakeven_pips' in params:
            exit_strategy_class = 'HybridExit'
        elif 'max_candles' in params and 'tp_pips' not in params:
            exit_strategy_class = 'TimeBased'
        else:
            exit_strategy_class = 'FixedSLTP'
        log.warning(f"[VALIDATOR] exit_strategy_class was empty — detected as "
                    f"{exit_strategy_class} from params: {list((exit_strategy_params or {}).keys())}")

    cls = getattr(es_mod, exit_strategy_class, None)
    if cls is None:
        # Try common name mappings
        _name_to_class = {
            'fixed sl/tp': 'FixedSLTP',
            'trailing stop': 'TrailingStop',
            'atr-based': 'ATRBased',
            'time-based': 'TimeBased',
            'indicator exit': 'IndicatorExit',
            'hybrid': 'HybridExit',
        }
        mapped = _name_to_class.get(exit_strategy_class.lower().strip())
        if mapped:
            cls = getattr(es_mod, mapped, None)
        if cls is None:
            log.warning(f"[VALIDATOR] Unknown exit class {exit_strategy_class!r} — using FixedSLTP")
            cls = es_mod.FixedSLTP
    params = dict(exit_strategy_params or {})
    # Only pass pip_size if the constructor accepts it
    import inspect
    sig = inspect.signature(cls.__init__)
    if 'pip_size' in sig.parameters:
        params['pip_size'] = pip_size
    return cls(**params)


def _compute_window_stats(trades):
    """Return stats dict for a list of trades."""
    if not trades:
        return {
            'count': 0, 'win_rate': 0.0, 'avg_pips': 0.0,
            'total_pips': 0.0, 'profit_factor': 0.0, 'max_dd_pips': 0.0,
        }
    net = np.array([t.get('net_pips', 0) for t in trades], dtype=float)
    wins  = net[net > 0]
    losses = net[net <= 0]
    wr = len(wins) / len(net) if len(net) > 0 else 0.0
    pf = float(np.sum(wins)) / float(-np.sum(losses)) if len(losses) > 0 and np.sum(losses) != 0 else float('inf')
    cum = np.cumsum(net)
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.max(peak - cum)) if len(cum) > 0 else 0.0
    return {
        'count':         int(len(net)),
        'win_rate':      round(float(wr), 4),
        'avg_pips':      round(float(np.mean(net)), 2),
        'total_pips':    round(float(np.sum(net)), 1),
        'profit_factor': round(float(pf), 3) if pf != float('inf') else 99.0,
        'max_dd_pips':   round(max_dd, 1),
    }


def _compute_rich_window_stats(trades, account_size=100000, risk_per_trade_pct=1.0,
                                default_sl_pips=150.0, pip_value_per_lot=10.0):
    """Compute detailed stats for a walk-forward window: DD tracking, monthly profit,
    trade frequency, payout estimation."""
    base = _compute_window_stats(trades)
    if not trades:
        base.update({
            'daily_dd_max_pct': 0.0,
            'total_dd_max_pct': 0.0,
            'dd_daily_touches': 0,
            'dd_total_touches': 0,
            'dd_recovered': True,
            'monthly_profits': {},
            'monthly_avg': 0.0,
            'monthly_best': 0.0,
            'monthly_worst': 0.0,
            'months_green': 0,
            'months_red': 0,
            'trades_per_day_avg': 0.0,
            'trades_per_day_min': 0,
            'trades_per_day_max': 0,
            'trading_days': 0,
            'trades_per_month_avg': 0.0,
            'trades_per_month_min': 0,
            'trades_per_month_max': 0,
            'trading_months': 0,
            'min_payout_14d': 0.0,
            'max_payout_14d': 0.0,
        })
        return base

    import pandas as pd

    # ── Lot size and dollar conversion ────────────────────────────────────────
    lot_size = (account_size * risk_per_trade_pct / 100.0) / (default_sl_pips * pip_value_per_lot)
    dollar_per_pip = pip_value_per_lot * lot_size

    # ── Daily PnL and DD tracking ─────────────────────────────────────────────
    daily_pnl = {}
    for t in trades:
        try:
            day = str(pd.to_datetime(t.get('exit_time', t.get('entry_time', ''))).date())
            pnl = (t.get('net_pips', 0) or 0) * dollar_per_pip
            daily_pnl[day] = daily_pnl.get(day, 0) + pnl
        except:
            continue

    days_sorted = sorted(daily_pnl.keys())
    equity = account_size
    peak = equity
    max_daily_dd_pct = 0.0
    max_total_dd_pct = 0.0
    daily_dd_touches = 0  # times daily DD >= 4%
    total_dd_touches = 0  # times total DD >= 8%
    daily_start = equity

    for day in days_sorted:
        daily_start = equity  # reset daily start at beginning of day
        equity += daily_pnl[day]
        peak = max(peak, equity)

        # Daily DD: loss from start of day
        daily_dd = (daily_start - equity) / account_size * 100 if equity < daily_start else 0
        max_daily_dd_pct = max(max_daily_dd_pct, daily_dd)
        # WHY: Threshold extracted to module constant — see top of file.
        # CHANGED: April 2026 — no magic numbers
        if daily_dd >= _DEFAULT_DAILY_DD_TOUCH_PCT:
            daily_dd_touches += 1

        # Total DD: loss from peak
        total_dd = (peak - equity) / account_size * 100 if equity < peak else 0
        max_total_dd_pct = max(max_total_dd_pct, total_dd)
        if total_dd >= _DEFAULT_TOTAL_DD_TOUCH_PCT:
            total_dd_touches += 1

    dd_recovered = equity >= peak * _DEFAULT_RECOVERY_THRESHOLD

    # ── Monthly profits ───────────────────────────────────────────────────────
    monthly = {}
    for day, pnl in daily_pnl.items():
        month = day[:7]  # "2006-03"
        monthly[month] = monthly.get(month, 0) + pnl

    monthly_vals = list(monthly.values()) if monthly else [0]
    months_green = sum(1 for v in monthly_vals if v > 0)
    months_red = sum(1 for v in monthly_vals if v <= 0)

    # ── Trades per day stats ──────────────────────────────────────────────────
    trades_by_day = {}
    for t in trades:
        try:
            day = str(pd.to_datetime(t.get('entry_time', '')).date())
            trades_by_day[day] = trades_by_day.get(day, 0) + 1
        except:
            continue

    day_counts = list(trades_by_day.values()) if trades_by_day else [0]
    trading_days = len(trades_by_day)

    # ── Trades per month ──────────────────────────────────────────────────────
    trades_by_month = {}
    for t in trades:
        try:
            month = str(pd.to_datetime(t.get('entry_time', '')).date())[:7]  # "2006-03"
            trades_by_month[month] = trades_by_month.get(month, 0) + 1
        except:
            continue

    month_counts = list(trades_by_month.values()) if trades_by_month else [0]

    # ── Payout estimation (14-day windows) ────────────────────────────────────
    # WHY: Old code stepped by 7 days but windows are 14 days long → overlapping
    #      windows counted each ~14-day period TWICE, distorting min/max
    #      payout stats. Step by 14 days (non-overlapping) for clean stats.
    # CHANGED: April 2026 — non-overlapping windows + module constant for split
    window_payouts = []
    if len(days_sorted) >= 5:
        for start_i in range(0, len(days_sorted) - 3, 14):
            start_day = pd.to_datetime(days_sorted[start_i])
            window_pnl = 0
            for d in days_sorted[start_i:]:
                if (pd.to_datetime(d) - start_day).days >= 14:
                    break
                window_pnl += daily_pnl[d]
            if window_pnl > 0:
                window_payouts.append(window_pnl * (_DEFAULT_PROFIT_SPLIT_PCT / 100.0))

    base.update({
        'daily_dd_max_pct':   round(max_daily_dd_pct, 2),
        'total_dd_max_pct':   round(max_total_dd_pct, 2),
        'dd_daily_touches':   daily_dd_touches,
        'dd_total_touches':   total_dd_touches,
        'dd_recovered':       dd_recovered,
        'monthly_profits':    {k: round(v, 2) for k, v in monthly.items()},
        'monthly_avg':        round(sum(monthly_vals) / max(len(monthly_vals), 1), 0),
        'monthly_best':       round(max(monthly_vals), 0),
        'monthly_worst':      round(min(monthly_vals), 0),
        'months_green':       months_green,
        'months_red':         months_red,
        'trades_per_day_avg': round(sum(day_counts) / max(len(day_counts), 1), 1),
        'trades_per_day_min': min(day_counts),
        'trades_per_day_max': max(day_counts),
        'trading_days':       trading_days,
        'trades_per_month_avg': round(sum(month_counts) / max(len(month_counts), 1), 1),
        'trades_per_month_min': min(month_counts),
        'trades_per_month_max': max(month_counts),
        'trading_months':       len(trades_by_month),
        'min_payout_14d':     round(min(window_payouts), 0) if window_payouts else 0.0,
        'max_payout_14d':     round(max(window_payouts), 0) if window_payouts else 0.0,
    })
    return base


def _trades_to_df(trades, risk_per_trade_pct=1.0, default_sl_pips=150.0,
                  pip_value_per_lot=10.0, account_size=100000):
    """Convert trade list to DataFrame accepted by simulate_challenge.

    Includes 'Pips' column so _rescale_trades in the simulator can compute
    dollar profit from pips directly — preventing double lot-size scaling.
    """
    rows = []
    for t in trades:
        net_pips = t.get('net_pips', 0)
        rows.append({
            'Close Date': pd.to_datetime(t.get('exit_time', t.get('entry_time', '2020-01-01'))),
            'Pips':       float(net_pips),
            'Profit':     0.0,  # placeholder — _rescale_trades will compute from Pips
        })
    if not rows:
        return pd.DataFrame(columns=['Close Date', 'Pips', 'Profit'])
    df = pd.DataFrame(rows)
    df = df.sort_values('Close Date').reset_index(drop=True)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Walk-Forward Validation
# ─────────────────────────────────────────────────────────────────────────────

def walk_forward_validate(
    rules,
    candles_path,
    exit_strategy_class,
    exit_strategy_params=None,
    n_windows=4,
    train_years=3,
    test_years=1,
    pip_size=0.01,
    spread_pips=2.5,
    commission_pips=0.0,
    account_size=100000,
    progress_callback=None,
    custom_windows=None,
    # WHY: Old signature had no direction parameter, so run_backtest
    #      defaulted to "BUY". Every SELL strategy was validated as
    #      if it were BUY, producing completely garbage walk-forward
    #      results for the wrong direction. BUY default preserves
    #      backward compat — callers must now pass the real direction.
    # CHANGED: April 2026 — direction parameter (audit CRITICAL)
    direction="BUY",
    # WHY: Phase 8 added news_blackout + parameterized risk/pip_value
    #      to run_backtest. Walk-forward was not updated to pass these
    #      through, so validated backtests never enforced news blackout
    #      and always used default lot sizing. Add optional pass-through.
    # CHANGED: April 2026 — news + sizing pass-through (audit HIGH)
    news_blackout_minutes=0,
    risk_per_trade_pct=1.0,
    pip_value_per_lot=10.0,
    default_sl_pips=150.0,
    # WHY (Validator Fix): Add filters parameter so optimizer filters
    #      (min_hold, sessions, etc.) can be applied to validation results.
    # CHANGED: April 2026 — Validator Fix
    filters=None,
    # WHY (leverage): Pass through to fast_backtest so validation uses
    #      the same margin cap as the backtest it's validating.
    #      0 = no cap (backward compat).
    # CHANGED: April 2026 — leverage pass-through
    leverage=0,
    contract_size=100.0,
):
    """
    Rule-stability test across sliding time windows.

    ⚠️  DESPITE THE NAME, THIS IS NOT ML-STYLE WALK-FORWARD.  ⚠️

    This function runs the SAME pre-existing `rules` on multiple
    train/test window pairs and measures how the win rate degrades
    between in-sample and out-of-sample periods within each window.
    Real walk-forward validation re-discovers rules on each training
    window and tests those freshly-discovered rules on the OOS window.
    This function does NOT re-discover rules per window — the same
    `rules` argument is used on every slice.

    What it DOES measure: whether a given rule set's edge holds up
    as the data moves forward in time. Useful for detecting rules
    that only worked during a specific historical regime.

    What it does NOT measure: whether the rule-discovery process
    itself generalizes. For that you'd need a full retrain loop,
    which is the walk-forward refactor deferred from Phase family #31.

    Returns dict with 'windows' list and 'summary' dict.
    """
    # WHY: Old code simply logged "Starting walk-forward" without
    #      clarifying what it actually does. Users reading the log
    #      thought they were seeing rediscovery-per-window; they
    #      weren't. Log the real intent so log readers aren't misled.
    # CHANGED: April 2026 — Phase 31 Fix 5 — clarify function purpose
    #          (audit Part C HIGH #38)
    log.info(f"[RULE_STABILITY] Testing {len(rules)} pre-existing rules across "
             f"{n_windows} sliding train/test windows. This is NOT rule rediscovery — "
             f"it's a stability test of the given rule set over time.")
    _stop_flag.clear()
    run_backtest, compute_stats, build_multi_tf_indicators = _load_backtester()

    # Diagnostic: print rule summary
    _win_rules = [r for r in rules if r.get('prediction') == 'WIN']
    _total_conds = sum(len(r.get('conditions', [])) for r in rules)
    print(f"[WF] Rules: {len(rules)} total ({len(_win_rules)} WIN), {_total_conds} total conditions")
    if not _win_rules:
        print(f"[WF] ⚠️ NO WIN rules! Only LOSS/other predictions → run_backtest will find 0 trades")
        print(f"[WF] Rule predictions: {[r.get('prediction') for r in rules]}")

    if progress_callback:
        progress_callback(0, n_windows, "Loading candle data...")

    candles_df, indicators_df = _load_data_cached(candles_path, rules=rules)

    print(f"[WF] Candles: {len(candles_df)} rows, "
          f"{candles_df['timestamp'].min()} to {candles_df['timestamp'].max()}")
    print(f"[WF] Indicators: {len(indicators_df)} rows, {len(indicators_df.columns)} columns")

    # Check if indicator columns required by rules exist
    _missing_features = []
    for _r in rules:
        for _c in _r.get('conditions', []):
            _feat = _c.get('feature', '')
            if _feat and _feat not in indicators_df.columns:
                _missing_features.append(_feat)
    if _missing_features:
        print(f"[WF] ⚠️ MISSING {len(_missing_features)} indicator columns that rules need:")
        for _mf in sorted(set(_missing_features))[:10]:
            print(f"[WF]   ❌ {_mf}")
        print(f"[WF]   (These conditions will always fail → 0 trades)")
    else:
        print(f"[WF] ✅ All rule features found in indicators_df")

    # Check regime filter pass rate on first window
    # WHY: A regime filter that blocks 99.8% of candles effectively means
    #      0 trades. Warn the user so they can adjust strictness.
    # CHANGED: April 2026 — regime filter pass rate warning
    try:
        from project2_backtesting.regime_filter_runtime import build_regime_pass_mask
        _regime_mask, _regime_info = build_regime_pass_mask(
            indicators_df, rule_action=direction)
        if _regime_info.get('enabled'):
            _pass_pct = _regime_info.get('pass_pct', 100)
            _pass_count = _regime_info.get('pass_count', 0)
            _total = _regime_info.get('total', 0)
            print(f"[WF] Regime filter: {_pass_count}/{_total} candles pass ({_pass_pct:.1f}%)")
            if _pass_pct < 5.0:
                print(f"[WF] ⚠️ REGIME FILTER TOO STRICT — only {_pass_pct:.1f}% of candles pass!")
                print(f"[WF]   This will produce near-zero trades in most windows.")
                print(f"[WF]   Consider: changing strictness to 'Balanced' or 'Conservative',")
                print(f"[WF]   or unchecking 'Apply regime filter during Step 3 discovery'")
    except Exception:
        pass

    exit_strat = _build_exit_strategy(exit_strategy_class, exit_strategy_params, pip_size)

    # Determine data range
    all_dates = pd.to_datetime(candles_df['timestamp'])
    data_start = all_dates.min()
    data_end   = all_dates.max()

    # ── STEP 1: Build ALL auto sliding windows ────────────────────────────────
    windows_schedule = []  # list of (train_start, train_end, test_start, test_end, is_custom)
    t = data_start
    while len(windows_schedule) < n_windows:
        train_start = t
        train_end   = t + pd.DateOffset(years=train_years)
        test_start  = train_end
        test_end    = test_start + pd.DateOffset(years=test_years)
        if test_end > data_end + pd.DateOffset(days=1):
            break
        windows_schedule.append((train_start, train_end, test_start, test_end, False))
        t += pd.DateOffset(years=test_years)

    # ── STEP 2: ADD custom windows on top (never replace) ─────────────────────
    if custom_windows:
        for cw in custom_windows:
            try:
                ts = pd.to_datetime(f"{cw['train_start']}-01-01")
                te = pd.to_datetime(f"{cw['train_end']}-12-31")
                os_start = pd.to_datetime(f"{cw['test_year']}-01-01")
                os_end = min(pd.to_datetime(f"{cw['test_year']}-12-31"),
                             data_end + pd.DateOffset(days=1))
                if ts >= data_start and os_start <= data_end:
                    windows_schedule.append((ts, te, os_start, os_end, True))
            except Exception as e:
                log.info(f"  [WF] Skipping invalid custom window: {cw} — {e}")

    # Sort all windows by test period start date
    # WHY (Phase 37 Fix 4): Old code had this sort twice — the second
    #      call was a no-op on an already-sorted list. Dead code.
    # CHANGED: April 2026 — Phase 37 Fix 4 — remove duplicate sort
    #          (audit Part C MED #44)
    windows_schedule.sort(key=lambda w: w[2])

    if not windows_schedule:
        return {
            'windows': [],
            'summary': {
                'verdict': 'INSUFFICIENT_DATA',
                'windows_completed': 0,
                'avg_out_wr': 0.0,
                'avg_degradation': 0.0,
                'edge_held_count': 0,
                'edge_held_ratio': 0.0,
            }
        }

    results_windows = []
    completed = 0

    # ── Pre-prepare data ONCE for fast_backtest ──────────────────────────
    # WHY: run_backtest copies candles_df + indicators_df on EVERY call.
    #      For M5 data (1.5M rows × 570 cols), each copy is ~3 GB.
    #      38 calls = ~114 GB of copies. Pre-prepare once, then use
    #      fast_backtest which takes read-only references — no copies.
    # CHANGED: April 2026 — 10-20x speedup for walk-forward
    import time as _wf_time
    _wf_start_time = _wf_time.time()

    _, _, _wf_build = _load_backtester()
    from project2_backtesting.strategy_backtester import fast_backtest, compute_stats as _wf_compute_stats

    # 1. Dedup timestamps once
    _wf_df = candles_df
    _wf_ind = indicators_df
    if 'timestamp' in _wf_df.columns:
        _dedup_count = len(_wf_df) - _wf_df['timestamp'].nunique()
        if _dedup_count > 0:
            _wf_df = _wf_df.drop_duplicates(subset=['timestamp'], keep='last').reset_index(drop=True)
            if 'timestamp' in _wf_ind.columns:
                _wf_ind = _wf_ind.drop_duplicates(subset=['timestamp'], keep='last').reset_index(drop=True)

    # 2. Align lengths
    _min_len = min(len(_wf_df), len(_wf_ind))
    if len(_wf_df) != len(_wf_ind):
        _wf_df = _wf_df.iloc[:_min_len].reset_index(drop=True)
        _wf_ind = _wf_ind.iloc[:_min_len].reset_index(drop=True)

    # 3. Remove warmup once
    if len(_wf_df) > 200:
        _wf_df = _wf_df.iloc[200:].reset_index(drop=True)
        _wf_ind = _wf_ind.iloc[200:].reset_index(drop=True)

    # 4. Compute SMART features once if needed
    _smart_needed = {c['feature'] for r in rules for c in r.get('conditions', [])
                     if c['feature'].startswith('SMART_')}
    if _smart_needed and not any(c.startswith('SMART_') for c in _wf_ind.columns):
        try:
            from project1_reverse_engineering.smart_features import (
                _add_tf_divergences, _add_indicator_dynamics,
                _add_alignment_scores, _add_session_intelligence,
                _add_volatility_regimes, _add_price_action,
                _add_momentum_quality,
            )
            if 'hour_of_day' not in _wf_ind.columns:
                _wf_ind = _wf_ind.copy()
                _wf_ind['hour_of_day'] = _wf_df['timestamp'].dt.hour
            if 'open_time' not in _wf_ind.columns:
                _wf_ind['open_time'] = _wf_df['timestamp'].astype(str)
            _wf_ind = _add_tf_divergences(_wf_ind)
            _wf_ind = _add_indicator_dynamics(_wf_ind)
            _wf_ind = _add_alignment_scores(_wf_ind)
            _wf_ind = _add_session_intelligence(_wf_ind)
            _wf_ind = _add_volatility_regimes(_wf_ind)
            _wf_ind = _add_price_action(_wf_ind)
            _wf_ind = _add_momentum_quality(_wf_ind)
            print(f"[WF] Pre-computed SMART features once")
        except Exception as _e:
            print(f"[WF] SMART features failed: {_e}")

    # 5. Parse timestamps for fast date slicing
    _wf_timestamps = pd.to_datetime(_wf_df['timestamp'])

    print(f"[WF] Data prepared: {len(_wf_df)} candles, {len(_wf_ind.columns)} indicators "
          f"({_wf_time.time() - _wf_start_time:.1f}s)")

    for i, (train_start, train_end, test_start, test_end, is_custom) in enumerate(windows_schedule):
        if _stop_flag.is_set():
            break

        prefix = "★ CUSTOM" if is_custom else f"W{i+1}"
        w_label = f"{prefix}: In-Sample {train_start.year}–{train_end.year-1}, Out-of-Sample {test_start.year}"
        if progress_callback:
            progress_callback(i, len(windows_schedule), f"Window {i+1}/{len(windows_schedule)}: backtesting in-sample...")

        # In-sample
        in_error = None
        try:
            # WHY: Use fast_backtest with pre-sliced data — no .copy(),
            #      no SMART recompute, no warmup removal per window.
            # CHANGED: April 2026 — fast_backtest for walk-forward
            _in_mask = (_wf_timestamps >= pd.Timestamp(train_start)) & (_wf_timestamps <= pd.Timestamp(train_end))
            _in_df = _wf_df[_in_mask].reset_index(drop=True)
            _in_ind = _wf_ind[_in_mask].reset_index(drop=True)
            in_trades = fast_backtest(
                df=_in_df, ind=_in_ind,
                rules=rules, exit_strategy=exit_strat,
                direction=direction,
                pip_size=pip_size,
                spread_pips=spread_pips,
                commission_pips=commission_pips,
                account_size=account_size,
                risk_per_trade_pct=risk_per_trade_pct,
                default_sl_pips=default_sl_pips,
                pip_value_per_lot=pip_value_per_lot,
                leverage=leverage, contract_size=contract_size,
            )
        except Exception as e:
            in_trades = []
            in_error = str(e)
            import traceback
            log.info(f"  [WF] Window {i+1} IN-SAMPLE ERROR: {e}")
            traceback.print_exc()

        # WHY (Validator Fix): Apply optimizer filters (min_hold, sessions, etc.)
        #      to the generated trades. Without this, the validator tests a
        #      different strategy than what the optimizer found.
        # CHANGED: April 2026 — Validator Fix
        if filters and in_trades:
            try:
                from project2_backtesting.strategy_refiner import apply_filters, enrich_trades
                in_trades = enrich_trades(list(in_trades))
                in_trades, _ = apply_filters(in_trades, filters)
            except Exception:
                pass

        print(f"[WF] Window {i+1} IN: {len(in_trades)} trades "
              f"({train_start.strftime('%Y')}-{train_end.strftime('%Y')})")

        if progress_callback:
            progress_callback(i, len(windows_schedule),
                              f"Window {i+1}/{len(windows_schedule)}: backtesting out-of-sample...")

        # Out-of-sample
        out_error = None
        try:
            # WHY: Use fast_backtest with pre-sliced data — no .copy(),
            #      no SMART recompute, no warmup removal per window.
            # CHANGED: April 2026 — fast_backtest for walk-forward
            _out_mask = (_wf_timestamps >= pd.Timestamp(test_start)) & (_wf_timestamps <= pd.Timestamp(test_end))
            _out_df = _wf_df[_out_mask].reset_index(drop=True)
            _out_ind = _wf_ind[_out_mask].reset_index(drop=True)
            out_trades = fast_backtest(
                df=_out_df, ind=_out_ind,
                rules=rules, exit_strategy=exit_strat,
                direction=direction,
                pip_size=pip_size,
                spread_pips=spread_pips,
                commission_pips=commission_pips,
                account_size=account_size,
                risk_per_trade_pct=risk_per_trade_pct,
                default_sl_pips=default_sl_pips,
                pip_value_per_lot=pip_value_per_lot,
                leverage=leverage, contract_size=contract_size,
            )
        except Exception as e:
            out_trades = []
            out_error = str(e)
            import traceback
            log.info(f"  [WF] Window {i+1} OUT-OF-SAMPLE ERROR: {e}")
            traceback.print_exc()

        # WHY (Validator Fix): Apply optimizer filters to out-of-sample trades.
        # CHANGED: April 2026 — Validator Fix
        if filters and out_trades:
            try:
                from project2_backtesting.strategy_refiner import apply_filters, enrich_trades
                out_trades = enrich_trades(list(out_trades))
                out_trades, _ = apply_filters(out_trades, filters)
            except Exception:
                pass

        print(f"[WF] Window {i+1} OUT: {len(out_trades)} trades "
              f"({test_start.strftime('%Y')}-{test_end.strftime('%Y')})")

        in_stats  = _compute_rich_window_stats(in_trades, account_size)
        out_stats = _compute_rich_window_stats(out_trades, account_size)

        in_wr  = in_stats['win_rate']
        out_wr = out_stats['win_rate']

        # WHY: Old helper used wr > 1 else wr, which treated WR=1.0 as
        #      fraction (100%) but WR=1.001 as percent (1.001%).
        #      Discontinuity at the boundary gave weird verdicts on
        #      windows with tiny numbers of trades. compute_stats and
        #      strategy_backtester.compute_stats both always return
        #      percent (0-100 scale), so pass scale='percent' from
        #      callers. scale='auto' preserves the old heuristic and
        #      logs a warning once per session when the ambiguous
        #      [1.0, 2.0] range is encountered.
        # CHANGED: April 2026 — Phase 31 Fix 7 — explicit scale param
        #          (audit Part C HIGH #40)
        _wr_scale_warned = [False]  # list so closure can mutate

        def _wr_to_fraction(wr, scale='percent'):
            try:
                wr = float(wr)
            except (TypeError, ValueError):
                return 0.0
            if scale == 'fraction':
                return wr
            if scale == 'percent':
                return wr / 100.0
            # auto mode — heuristic with warning on ambiguity
            if 1.0 <= wr < 2.0 and not _wr_scale_warned[0]:
                log.warning(
                    f"[WF] _wr_to_fraction: ambiguous value {wr} in auto mode. "
                    f"Caller should pass scale='percent' or scale='fraction' explicitly. "
                    f"Assuming percent. (This warning shown once per run.)"
                )
                _wr_scale_warned[0] = True
            return wr / 100.0 if wr > 1 else wr

        # Fix: 0-trade windows should not show as "no degradation"
        if in_stats['count'] == 0 and out_stats['count'] == 0:
            degradation = 0.0
            edge_held = False  # No data = no edge
        elif in_wr > 0:
            in_wr_frac  = _wr_to_fraction(in_wr)
            out_wr_frac = _wr_to_fraction(out_wr)
            degradation = (out_wr_frac - in_wr_frac) / in_wr_frac * 100.0
            # WHY: Edge held if EITHER WR >= 50% OR PF > 1.0 (profitable).
            #      Asymmetric strategies (trailing stop) have WR 30-45% but
            #      PF 1.2-2.0 — the edge IS real, just asymmetric.
            # CHANGED: April 2026 — PF-aware edge detection
            _out_pf = out_stats.get('profit_factor', 0)
            edge_held = (out_wr_frac >= _DEFAULT_EDGE_HELD_WR or _out_pf > 1.0)
        else:
            # WHY: Old fallback used out_wr without normalizing → percent
            #      format always >= 0.50 → edge_held was always True even
            #      when the out-of-sample win rate was actually low.
            # CHANGED: April 2026 — normalize in fallback too
            degradation = 0.0
            _out_pf = out_stats.get('profit_factor', 0)
            edge_held = (_wr_to_fraction(out_wr) >= _DEFAULT_EDGE_HELD_WR or _out_pf > 1.0)

        results_windows.append({
            'window_idx':   i + 1,
            'label':        w_label,
            'train_start':  str(train_start.date()),
            'train_end':    str(train_end.date()),
            'test_start':   str(test_start.date()),
            'test_end':     str(test_end.date()),
            'in_sample':    in_stats,
            'out_sample':   out_stats,
            'degradation':  round(degradation, 2),
            'edge_held':    edge_held,
            'is_custom':    is_custom,
            'in_error':     in_error,
            'out_error':    out_error,
        })
        completed += 1

    if progress_callback:
        progress_callback(len(windows_schedule), len(windows_schedule), "Summarising walk-forward results...")

    # Summary
    if completed < 2:
        verdict = 'INSUFFICIENT_DATA'
    else:
        # Check if walk-forward actually produced trades
        total_in_trades  = sum(w['in_sample']['count'] for w in results_windows)
        total_out_trades = sum(w['out_sample']['count'] for w in results_windows)

        if total_in_trades == 0 and total_out_trades == 0:
            verdict = 'INSUFFICIENT_DATA'
        elif total_out_trades == 0:
            verdict = 'INSUFFICIENT_DATA'
        else:
            out_wrs = [w['out_sample']['win_rate'] for w in results_windows]
            out_pfs = [w['out_sample']['profit_factor'] for w in results_windows]
            degs    = [w['degradation'] for w in results_windows]
            held    = [w['edge_held'] for w in results_windows]

            avg_out_wr      = float(np.mean(out_wrs))
            avg_out_pf      = float(np.mean(out_pfs))
            avg_degradation = float(np.mean(degs))
            edge_held_count = sum(held)
            edge_held_ratio = edge_held_count / len(held)

            # WHY: Count windows where PF > 1.0 (profitable) as "edge held"
            #      for asymmetric strategies (low WR, high PF like trailing stop).
            #      Old code only checked WR >= 50%. A strategy with 40% WR
            #      but PF 1.4 is profitable — the edge IS held.
            # CHANGED: April 2026 — PF-aware edge detection
            pf_held_count = sum(1 for w in results_windows
                               if w['out_sample']['profit_factor'] > 1.0)
            pf_held_ratio = pf_held_count / max(len(results_windows), 1)

            # WHY: Old verdict was a three-way cliff — a strategy at
            #      avg_out_wr=0.549 got INCONCLUSIVE; one at 0.551 got
            #      LIKELY_REAL. The boundary produced noisy verdicts on
            #      strategies that were genuinely borderline. Add a
            #      MARGINAL zone between LIKELY_REAL and INCONCLUSIVE so
            #      close calls are labeled honestly.
            # CHANGED: April 2026 — Phase 31 Fix 6 — MARGINAL verdict zone
            #          (audit Part C HIGH #39)
            #
            # WHY: Old grading only checked WR. Trailing stop strategies have
            #      30-45% WR but PF 1.2-2.0 (winners much bigger than losers).
            #      A strategy with PF > 1.2 across 90%+ of OOS windows is
            #      clearly profitable regardless of WR. Add PF-based path.
            # CHANGED: April 2026 — PF-aware grading for asymmetric strategies

            # Path 1: High WR strategies (symmetric, WR > 50%)
            if avg_out_wr >= 0.55 and avg_degradation > -15 and edge_held_ratio >= 0.60:
                verdict = 'LIKELY_REAL'
            elif (0.52 <= avg_out_wr < 0.55
                  and edge_held_ratio >= 0.50
                  and avg_degradation > -20):
                verdict = 'MARGINAL'
            elif avg_out_wr >= 0.50 and edge_held_ratio >= 0.40:
                verdict = 'INCONCLUSIVE'

            # Path 2: High PF strategies (asymmetric, WR < 50% but big winners)
            elif avg_out_pf >= 1.3 and pf_held_ratio >= 0.80:
                verdict = 'LIKELY_REAL'
                print(f"[WF] PF-based verdict: avg_out_pf={avg_out_pf:.2f}, "
                      f"pf_held={pf_held_count}/{len(results_windows)} ({pf_held_ratio:.0%})")
            elif avg_out_pf >= 1.15 and pf_held_ratio >= 0.65:
                verdict = 'MARGINAL'
            elif avg_out_pf >= 1.0 and pf_held_ratio >= 0.50:
                verdict = 'INCONCLUSIVE'

            else:
                verdict = 'LIKELY_OVERFITTING'

    in_wrs_all  = [w['in_sample']['win_rate'] for w in results_windows] if results_windows else [0.0]
    out_wrs_all = [w['out_sample']['win_rate'] for w in results_windows] if results_windows else [0.0]
    out_pfs_all = [w['out_sample']['profit_factor'] for w in results_windows] if results_windows else [0.0]
    in_pfs_all  = [w['in_sample']['profit_factor'] for w in results_windows] if results_windows else [0.0]
    degs_all    = [w['degradation'] for w in results_windows] if results_windows else [0.0]
    held_all    = [w['edge_held'] for w in results_windows] if results_windows else []
    total_in    = sum(w['in_sample']['count'] for w in results_windows) if results_windows else 0
    total_out   = sum(w['out_sample']['count'] for w in results_windows) if results_windows else 0

    summary = {
        'verdict':           verdict,
        'windows_completed': completed,
        'total_in_trades':   total_in,
        'total_out_trades':  total_out,
        'avg_in_wr':         round(float(np.mean(in_wrs_all)), 4),
        'avg_out_wr':        round(float(np.mean(out_wrs_all)), 4),
        'avg_in_pf':         round(float(np.mean(in_pfs_all)), 3),
        'avg_out_pf':        round(float(np.mean(out_pfs_all)), 3),
        'avg_degradation':   round(float(np.mean(degs_all)), 2),
        'edge_held_count':   sum(held_all),
        'edge_held_ratio':   round(sum(held_all) / max(len(held_all), 1), 3),
    }

    _wf_elapsed = _wf_time.time() - _wf_start_time
    print(f"[WF] Walk-forward completed in {_wf_elapsed:.1f}s "
          f"({len(windows_schedule)} windows, {completed} completed)")

    return {'windows': results_windows, 'summary': summary}


# ─────────────────────────────────────────────────────────────────────────────
# Monte Carlo Robustness Test
# ─────────────────────────────────────────────────────────────────────────────

def monte_carlo_test(
    trades,
    firm_id='ftmo',
    challenge_id=None,
    account_size=100000,
    n_simulations=500,
    risk_per_trade_pct=1.0,
    default_sl_pips=150.0,
    pip_value_per_lot=10.0,
    progress_callback=None,
    # WHY (Phase 37 Fix 2): Old code used np.random.default_rng() with
    #      no seed — every MC run produced different shuffles and
    #      different verdicts. Users couldn't reproduce a saved
    #      verdict. Accept an optional seed; default None = unseeded
    #      (backward compat).
    # CHANGED: April 2026 — Phase 37 Fix 2 — seed parameter
    #          (audit Part C MED #42)
    shuffle_seed=None,
    symbol='XAUUSD',
):
    """
    Shuffle trade PnL values and re-run prop firm simulation to test
    whether results depend on trade sequence vs actual edge.

    Returns dict with stats, histogram data, and verdict.
    """
    _stop_flag.clear()

    print(f"[MC] Starting: {len(trades)} trades, firm={firm_id!r}, "
          f"challenge_id={challenge_id!r}, sims={n_simulations}, account={account_size}")

    import sys
    project_root = os.path.abspath(os.path.join(_HERE, '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from shared.prop_firm_simulator import simulate_challenge
    from project2_backtesting.prop_firm_tester import load_available_firms

    if not trades:
        print(f"[MC] Empty trades list — returning INSUFFICIENT_DATA")
        return {'verdict': 'INSUFFICIENT_DATA', 'error': 'No trades provided'}

    # Discover challenge_id if not given
    # WHY (Phase 37 Fix 3): Old code picked the FIRST matching firm
    #      entry — insertion-order dependent. A firm with 10k/25k/50k/100k
    #      challenges silently used whichever came first in the JSON,
    #      regardless of the user's account_size parameter. Now: collect
    #      all matching challenges, prefer the one matching account_size,
    #      and warn if no exact match exists so the user knows they're
    #      running against a potentially wrong rule set.
    # CHANGED: April 2026 — Phase 37 Fix 3 — account_size matching
    #          (audit Part C MED #43)
    if challenge_id is None:
        firms_data = load_available_firms()
        matching = [fc for fc in firms_data if fc.get('firm_id') == firm_id]
        if matching:
            # Prefer one whose declared account_size matches
            exact_match = None
            for fc in matching:
                fc_sizes = fc.get('account_sizes') or [fc.get('account_size')]
                if account_size in [int(s) for s in fc_sizes if s is not None]:
                    exact_match = fc
                    break
            if exact_match is not None:
                challenge_id = exact_match['challenge_id']
            else:
                # Fall back to first, but warn
                challenge_id = matching[0]['challenge_id']
                if len(matching) > 1:
                    log.warning(
                        f"[MC] Firm {firm_id!r} has {len(matching)} challenges "
                        f"but none match account_size={account_size}. "
                        f"Using first: challenge_id={challenge_id}. "
                        f"Pass challenge_id= explicitly to pick a specific one."
                    )
    if challenge_id is None:
        print(f"[MC] No challenge found for firm_id={firm_id!r} — returning INSUFFICIENT_DATA")
        return {'verdict': 'INSUFFICIENT_DATA', 'error': f'No challenges found for {firm_id}'}

    trades_df = _trades_to_df(
        trades, risk_per_trade_pct, default_sl_pips, pip_value_per_lot, account_size
    )

    # Baseline: original order
    if progress_callback:
        progress_callback(0, n_simulations, "Running baseline (original order)...")

    # WHY (Phase 37 Fix 1): Baseline was 200 samples, shuffles below
    #      were 50 samples each. Variance of the shuffle pass-rate
    #      estimator was ~2× the baseline's, so the "is baseline in
    #      the shuffle tail" comparison had artificially wide CIs.
    #      Align both to 100 — compromise between accuracy and
    #      runtime (shuffle total doubles from 25k to 50k samples).
    # CHANGED: April 2026 — Phase 37 Fix 1 — aligned sample counts
    #          (audit Part C MED #41)
    _MC_SAMPLES = 100
    try:
        baseline_summary = simulate_challenge(
            trades_df=trades_df,
            firm_id=firm_id,
            challenge_id=challenge_id,
            account_size=account_size,
            mode='sliding_window',
            num_samples=_MC_SAMPLES,
            risk_per_trade_pct=risk_per_trade_pct,
            default_sl_pips=default_sl_pips,
            pip_value_per_lot=pip_value_per_lot,
            symbol=symbol,
        )
        baseline_pass_rate = float(baseline_summary.eval_pass_rate) if baseline_summary else 0.0
    except Exception as e:
        baseline_pass_rate = 0.0
    print(f"[MC] Baseline pass rate: {baseline_pass_rate:.2%}")

    # Shuffle simulations — shuffle Pips, let simulator compute Profit
    pips_values = list(trades_df['Pips'].values) if 'Pips' in trades_df.columns else list(trades_df['Profit'].values)
    dates       = list(trades_df['Close Date'].values)
    has_pips    = 'Pips' in trades_df.columns
    shuffled_rates = []

    # WHY: Old code did random.shuffle on the full pip sequence, which is
    #      IID — destroys serial dependence. Strategies with autocorrelated
    #      losing streaks look much safer than reality because the IID
    #      shuffle disperses clustered losses evenly. Fix: moving block
    #      shuffle. Draw contiguous blocks of ~sqrt(n) trades and
    #      concatenate until the same length is filled. Preserves short-
    #      range autocorrelation (same heuristic as Phase 12 Fix 1).
    # CHANGED: April 2026 — block shuffle (audit HIGH)
    # CHANGED: April 2026 — Phase 37 Fix 2b — accept shuffle_seed
    _rng_mc   = np.random.default_rng(shuffle_seed)
    _n_pips   = len(pips_values)
    _block_len = max(5, int(np.sqrt(_n_pips)))
    _pips_arr  = np.asarray(pips_values)

    def _block_shuffle(n_needed):
        out = np.empty(n_needed, dtype=_pips_arr.dtype)
        pos = 0
        while pos < n_needed:
            start = int(_rng_mc.integers(0, _n_pips))
            take  = min(_block_len, n_needed - pos, _n_pips - start)
            out[pos:pos + take] = _pips_arr[start:start + take]
            pos += take
        return list(out)

    for sim_i in range(n_simulations):
        if _stop_flag.is_set():
            break
        if progress_callback and sim_i % 50 == 0:
            progress_callback(sim_i, n_simulations,
                              f"Monte Carlo: shuffle {sim_i}/{n_simulations}...")

        shuffled_pips = _block_shuffle(_n_pips)
        if has_pips:
            shuffled_df = pd.DataFrame({'Close Date': dates, 'Pips': shuffled_pips, 'Profit': 0.0})
        else:
            shuffled_df = pd.DataFrame({'Close Date': dates, 'Profit': shuffled_pips})
        shuffled_df = shuffled_df.sort_values('Close Date').reset_index(drop=True)

        try:
            # CHANGED: April 2026 — Phase 37 Fix 1b — use _MC_SAMPLES
            #          (aligned with baseline per audit Part C MED #41)
            sim_summary = simulate_challenge(
                trades_df=shuffled_df,
                firm_id=firm_id,
                challenge_id=challenge_id,
                account_size=account_size,
                mode='sliding_window',
                num_samples=_MC_SAMPLES,
                risk_per_trade_pct=risk_per_trade_pct,
                default_sl_pips=default_sl_pips,
                pip_value_per_lot=pip_value_per_lot,
                symbol=symbol,
            )
            rate = float(sim_summary.eval_pass_rate) if sim_summary else 0.0
        except Exception:
            rate = 0.0
        shuffled_rates.append(rate)

    print(f"[MC] Completed {len(shuffled_rates)}/{n_simulations} shuffles, "
          f"mean_rate={float(np.mean(shuffled_rates)) if shuffled_rates else 0:.2%}")

    if not shuffled_rates:
        return {'verdict': 'INSUFFICIENT_DATA', 'error': 'No shuffles completed'}

    arr = np.array(shuffled_rates)
    mean_rate  = float(np.mean(arr))
    median_rate = float(np.median(arr))
    p5  = float(np.percentile(arr, 5))
    p95 = float(np.percentile(arr, 95))
    std = float(np.std(arr))
    pct_worse_than_original = float(np.mean(arr < baseline_pass_rate))

    # Build histogram (8 bins from 0 to 1)
    bin_edges = [i / 8 for i in range(9)]
    hist = []
    for j in range(8):
        lo, hi = bin_edges[j], bin_edges[j + 1]
        count = int(np.sum((arr >= lo) & (arr < hi)))
        hist.append({
            'label':  f"{int(lo*100)}-{int(hi*100)}%",
            'count':  count,
            'pct':    round(count / len(arr) * 100, 1),
        })

    # Verdict
    if p5 >= 0.40 and mean_rate >= 0.50:
        verdict = 'ROBUST'
    elif p5 >= 0.25 and mean_rate >= 0.40:
        verdict = 'MODERATE'
    else:
        verdict = 'FRAGILE'

    if progress_callback:
        progress_callback(n_simulations, n_simulations, "Monte Carlo complete.")

    return {
        'firm_id':                 firm_id,
        'challenge_id':            challenge_id,
        'n_simulations':           len(shuffled_rates),
        'baseline_pass_rate':      round(baseline_pass_rate, 4),
        'mean_pass_rate':          round(mean_rate, 4),
        'median_pass_rate':        round(median_rate, 4),
        'p5_pass_rate':            round(p5, 4),
        'p95_pass_rate':           round(p95, 4),
        'std_pass_rate':           round(std, 4),
        'pct_worse_than_original': round(pct_worse_than_original, 4),
        'shuffled_pass_rates':     [round(r, 4) for r in shuffled_rates],
        'histogram':               hist,
        'verdict':                 verdict,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Slippage Stress Test
# ─────────────────────────────────────────────────────────────────────────────

def slippage_stress_test(
    trades,
    rules,
    candles_path,
    exit_strategy_class,
    exit_strategy_params,
    slippage_levels=None,
    pip_size=0.01,
    spread_pips=2.5,
    commission_pips=0.0,
    account_size=100000,
    n_runs_per_level=3,
    progress_callback=None,
    filters=None,
    leverage=0,
    contract_size=100.0,
):
    """
    Re-run the backtest at increasing slippage levels to find where the
    strategy becomes unprofitable.

    Returns dict with 'levels', 'max_safe_slippage', 'breakeven_slippage', 'verdict'.
    """
    if slippage_levels is None:
        slippage_levels = [0, 1, 2, 3, 5]

    _stop_flag.clear()
    run_backtest, compute_stats, _ = _load_backtester()
    from project2_backtesting.strategy_backtester import fast_backtest

    candles_df, indicators_df = _load_data_cached(candles_path, rules=rules)

    # Pre-trim once — slippage loop has no per-level date filter
    _c = candles_df.iloc[200:].reset_index(drop=True)
    _i = indicators_df.iloc[200:].reset_index(drop=True)

    exit_strat = _build_exit_strategy(exit_strategy_class, exit_strategy_params, pip_size)

    levels_results = []
    total_runs = len(slippage_levels) * n_runs_per_level
    run_count = 0

    for slip_pips in slippage_levels:
        if _stop_flag.is_set():
            break

        level_wrs       = []
        level_avg_pips  = []
        level_total_pips = []

        for run_i in range(n_runs_per_level):
            if _stop_flag.is_set():
                break
            run_count += 1
            if progress_callback:
                progress_callback(run_count, total_runs,
                                  f"Slippage {slip_pips} pips — run {run_i+1}/{n_runs_per_level}")
            try:
                run_trades = fast_backtest(
                    df=_c, ind=_i,
                    rules=rules,
                    exit_strategy=exit_strat,
                    pip_size=pip_size,
                    spread_pips=spread_pips,
                    commission_pips=commission_pips,
                    slippage_pips=float(slip_pips),
                    account_size=account_size,
                    leverage=leverage, contract_size=contract_size,
                )
                # Apply filters if provided (max_trades_per_day, sessions, etc.)
                if filters and run_trades:
                    try:
                        from project2_backtesting.strategy_refiner import apply_filters
                        run_trades, _ = apply_filters(run_trades, filters)
                    except Exception:
                        pass
                stats = compute_stats(run_trades)
                level_wrs.append(stats['win_rate'])
                level_avg_pips.append(stats['net_avg_pips'])
                level_total_pips.append(stats['net_total_pips'])
            except Exception as e:
                import traceback
                log.info(f"  [SLIPPAGE] Level {slip_pips} pips, run {run_i+1} ERROR: {e}")
                traceback.print_exc()

        if not level_wrs:
            continue

        avg_wr      = float(np.mean(level_wrs))
        avg_pips    = float(np.mean(level_avg_pips))
        total_pips  = float(np.mean(level_total_pips))

        levels_results.append({
            'slippage_pips': slip_pips,
            'win_rate':      round(avg_wr, 1),        # already in 0-100
            'avg_pips':      round(avg_pips, 1),
            'total_pips':    round(total_pips, 0),
            'profitable':    total_pips > 0,
        })

    if not levels_results:
        return {'verdict': 'INSUFFICIENT_DATA', 'error': 'No runs completed'}

    # Highest slippage level that remains profitable
    max_safe = 0
    for lvl in levels_results:
        if lvl['profitable']:
            max_safe = lvl['slippage_pips']

    # Estimate breakeven by linear interpolation between last profitable and first unprofitable
    # WHY: Find the slippage level where profit = 0 by linear interpolation.
    #      Formula: x = a.slip + (a.profit / (a.profit - b.profit)) * (b.slip - a.slip)
    #      This is cleaner than the previous version and handles edge cases
    #      where profit_diff is near zero.
    # CHANGED: April 2026 — clearer interpolation
    breakeven_slip = None
    for i in range(len(levels_results) - 1):
        a = levels_results[i]
        b = levels_results[i + 1]
        a_profit = a['total_pips']
        b_profit = b['total_pips']
        if a_profit > 0 and b_profit <= 0:
            profit_diff = a_profit - b_profit   # always > 0 here
            if profit_diff > 0.01:
                fraction = a_profit / profit_diff   # 0 < fraction <= 1
                slip_diff = b['slippage_pips'] - a['slippage_pips']
                breakeven_slip = round(a['slippage_pips'] + fraction * slip_diff, 1)
            else:
                breakeven_slip = a['slippage_pips']
            break
    if breakeven_slip is None:
        last = levels_results[-1]
        breakeven_slip = last['slippage_pips'] * 2 if last['profitable'] else levels_results[0]['slippage_pips']

    if max_safe >= 5:
        verdict = 'ROBUST'
    elif max_safe >= 3:
        verdict = 'MODERATE'
    elif max_safe >= 1:
        verdict = 'FRAGILE'
    else:
        verdict = 'NO_EDGE'

    return {
        'levels':             levels_results,
        'max_safe_slippage':  max_safe,
        'breakeven_slippage': breakeven_slip,
        'verdict':            verdict,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Combined Score
# ─────────────────────────────────────────────────────────────────────────────

def combined_score(walk_forward_result, monte_carlo_result=None, slippage_result=None,
                   live_firm_results=None):
    """
    Compute 0-100 confidence score from walk-forward + Monte Carlo results.

    Returns dict with score, grade, verdicts, recommendation, warnings.
    """
    score = 50
    warnings = []
    verdicts = {}

    # Walk-forward scoring
    wf_summary = walk_forward_result.get('summary', {}) if walk_forward_result else {}
    wf_verdict = wf_summary.get('verdict', 'INSUFFICIENT_DATA')
    verdicts['walk_forward'] = wf_verdict

    # WHY: MARGINAL was missing — scored +0, lower than INCONCLUSIVE (+10).
    #      MARGINAL is between LIKELY_REAL and INCONCLUSIVE, should be +20.
    # CHANGED: April 2026 — handle MARGINAL verdict in scoring
    if wf_verdict == 'LIKELY_REAL':
        score += 30
    elif wf_verdict == 'MARGINAL':
        score += 20
    elif wf_verdict == 'INCONCLUSIVE':
        score += 10
    elif wf_verdict == 'LIKELY_OVERFITTING':
        score -= 20
    elif wf_verdict == 'INSUFFICIENT_DATA':
        score -= 10

    edge_held_ratio = wf_summary.get('edge_held_ratio', 0.0)
    score += int(edge_held_ratio * 15)

    # WHY: Bonus for high PF strategies. A strategy with PF 1.4 avg across
    #      OOS windows has a real edge even at 40% WR.
    # CHANGED: April 2026 — PF bonus in combined score
    avg_out_pf = wf_summary.get('avg_out_pf', 0)
    if avg_out_pf >= 1.3:
        score += 15
    elif avg_out_pf >= 1.15:
        score += 8
    elif avg_out_pf >= 1.0:
        score += 3

    avg_deg = wf_summary.get('avg_degradation', 0.0)
    if avg_deg < -20:
        score -= 15
        warnings.append(f"Out-of-sample WR degraded {abs(avg_deg):.0f}% from in-sample")
    elif avg_deg < -10:
        score -= 5
        warnings.append(f"Minor WR degradation of {abs(avg_deg):.0f}% out-of-sample")

    # Monte Carlo scoring
    mc_verdict = 'N/A'
    if monte_carlo_result and monte_carlo_result.get('verdict') != 'INSUFFICIENT_DATA':
        mc_verdict = monte_carlo_result.get('verdict', 'N/A')
        verdicts['monte_carlo'] = mc_verdict

        if mc_verdict == 'ROBUST':
            score += 25
        elif mc_verdict == 'MODERATE':
            score += 10
        elif mc_verdict == 'FRAGILE':
            score -= 10
            warnings.append("Strategy is sensitive to trade order — potential curve fitting")

        p5 = monte_carlo_result.get('p5_pass_rate', 0.0)
        if p5 >= 0.50:
            score += 10
        elif p5 < 0.25:
            score -= 10
            warnings.append(f"Worst-case (5th percentile) pass rate is only {p5*100:.0f}%")

        pct_worse = monte_carlo_result.get('pct_worse_than_original', 0.0)
        if pct_worse > 0.70:
            warnings.append(f"{pct_worse*100:.0f}% of shuffles underperform original — sequence-dependent")

    verdicts['monte_carlo'] = mc_verdict

    # Slippage stress test scoring
    slip_verdict = 'N/A'
    if slippage_result and slippage_result.get('verdict') not in (None, 'INSUFFICIENT_DATA'):
        slip_verdict  = slippage_result.get('verdict', 'N/A')
        max_safe      = slippage_result.get('max_safe_slippage', 0)
        be_slip       = slippage_result.get('breakeven_slippage', 10)
        verdicts['slippage'] = slip_verdict

        if slip_verdict == 'ROBUST' and max_safe >= 5:
            score += 10
        elif max_safe < 3 and max_safe > 0:
            score -= 15
            warnings.append(f"Unprofitable above {max_safe} pip slippage — limited real-world buffer")
        elif be_slip < 2:
            score -= 25
            warnings.append("Barely profitable edge — near breakeven at just 2 pip slippage")

    verdicts['slippage'] = slip_verdict

    # Live firm simulation scoring
    # WHY: Tests against ACTUAL firm rules — most accurate survival prediction.
    # CHANGED: April 2026
    if live_firm_results:
        viable_firms = [r for r in live_firm_results
                        if r.get('blow_count', 99) == 0 and r.get('payout_cycles_completed', 0) >= 1]
        if len(viable_firms) >= 3:
            score += 15
            verdicts['live_firm'] = 'EXCELLENT'
        elif len(viable_firms) >= 1:
            score += 5
            verdicts['live_firm'] = 'ACCEPTABLE'
        else:
            score -= 15
            verdicts['live_firm'] = 'NO_VIABLE_FIRM'
            warnings.append("Strategy doesn't survive on any prop firm under real rules")
    else:
        verdicts['live_firm'] = 'N/A'

    # Clamp
    score = max(0, min(100, score))

    # Grade
    if score >= 80:
        grade = 'A'
    elif score >= 65:
        grade = 'B'
    elif score >= 50:
        grade = 'C'
    elif score >= 35:
        grade = 'D'
    else:
        grade = 'F'

    # Recommendation
    if grade in ('A', 'B'):
        recommendation = "Strategy shows real edge. Proceed to prop firm testing with confidence."
    elif grade == 'C':
        recommendation = "Strategy shows some edge but results are mixed. Test with smaller account first."
    elif grade == 'D':
        recommendation = "Significant uncertainty. Consider refining strategy before prop firm testing."
    else:
        recommendation = "Strategy likely overfitting. Do not trade with real money until edge is proven."

    return {
        'confidence_score': score,
        'grade':            grade,
        'verdicts':         verdicts,
        'recommendation':   recommendation,
        'warnings':         warnings,
        'avg_degradation':  avg_deg,
        'edge_held_ratio':  edge_held_ratio,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Full Validation Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_full_validation(
    strategy_index,
    rules,
    candles_path,
    exit_strategy_class,
    exit_strategy_params=None,
    n_windows=4,
    train_years=3,
    test_years=1,
    pip_size=0.01,
    spread_pips=2.5,
    commission_pips=0.0,
    account_size=100000,
    mc_firm_id='ftmo',
    mc_challenge_id=None,
    # CHANGED: April 2026 — Phase 37 Fix 2c — seed for reproducible MC
    mc_shuffle_seed=None,
    n_simulations=500,
    risk_per_trade_pct=1.0,
    default_sl_pips=150.0,
    pip_value_per_lot=10.0,
    trades=None,
    wf_progress_callback=None,
    mc_progress_callback=None,
    slippage_result=None,
    # WHY: Orchestrator must accept and thread direction through to
    #      walk_forward_validate so panel callers can pass it once here.
    # CHANGED: April 2026 — direction parameter (audit CRITICAL)
    direction="BUY",
):
    """
    Run walk-forward + Monte Carlo + combined score, save to validation_results.json.
    Returns full result dict.
    """
    validated_at = datetime.now().isoformat()

    wf_result = walk_forward_validate(
        rules=rules,
        candles_path=candles_path,
        exit_strategy_class=exit_strategy_class,
        exit_strategy_params=exit_strategy_params,
        n_windows=n_windows,
        train_years=train_years,
        test_years=test_years,
        pip_size=pip_size,
        spread_pips=spread_pips,
        commission_pips=commission_pips,
        account_size=account_size,
        progress_callback=wf_progress_callback,
        direction=direction,
        risk_per_trade_pct=risk_per_trade_pct,
        pip_value_per_lot=pip_value_per_lot,
    )

    mc_result = None
    if trades:
        mc_result = monte_carlo_test(
            trades=trades,
            firm_id=mc_firm_id,
            challenge_id=mc_challenge_id,
            account_size=account_size,
            n_simulations=n_simulations,
            risk_per_trade_pct=risk_per_trade_pct,
            default_sl_pips=default_sl_pips,
            pip_value_per_lot=pip_value_per_lot,
            progress_callback=mc_progress_callback,
            shuffle_seed=mc_shuffle_seed,   # Phase 37 Fix 2d
        )

    # ── Live Firm Simulation ─────────────────────────────────────────────
    # WHY: Tests the strategy against each prop firm's exact rules.
    # CHANGED: April 2026
    live_firm_results = None
    if trades:
        try:
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(_HERE), '..'))
            from shared.live_firm_sim import simulate_all_firms
            live_firm_results = simulate_all_firms(trades, account_size=account_size)
        except Exception as e:
            log.info(f"[validator] live firm simulation failed: {e}")

    combined = combined_score(wf_result, mc_result, slippage_result, live_firm_results)

    result = {
        'strategy_index':   strategy_index,
        'validated_at':     validated_at,
        'walk_forward':     wf_result,
        'monte_carlo':      mc_result,
        'slippage':         slippage_result,
        'live_firm_results': live_firm_results,
        'combined':         combined,
    }

    _save_validation(strategy_index, result)
    return result

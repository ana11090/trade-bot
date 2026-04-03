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
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))

VALIDATION_PATH = os.path.join(_HERE, 'outputs', 'validation_results.json')

_stop_flag = threading.Event()


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
    cls = getattr(es_mod, exit_strategy_class, None)
    if cls is None:
        raise ValueError(f"Unknown exit strategy class: {exit_strategy_class!r}")
    params = dict(exit_strategy_params or {})
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


def _trades_to_df(trades, risk_per_trade_pct=1.0, default_sl_pips=150.0,
                  pip_value_per_lot=10.0, account_size=100000):
    """Convert trade list to DataFrame accepted by simulate_challenge."""
    rows = []
    for t in trades:
        net_pips = t.get('net_pips', 0)
        lot_size = (account_size * risk_per_trade_pct / 100.0) / (default_sl_pips * pip_value_per_lot)
        profit = net_pips * pip_value_per_lot * lot_size
        rows.append({
            'Close Date': pd.to_datetime(t.get('exit_time', t.get('entry_time', '2020-01-01'))),
            'Profit':     round(profit, 2),
        })
    if not rows:
        return pd.DataFrame(columns=['Close Date', 'Profit'])
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
):
    """
    Walk-forward validation: train on N years, test on following M years,
    slide the window forward, repeat.

    Returns dict with 'windows' list and 'summary' dict.
    """
    _stop_flag.clear()
    run_backtest, compute_stats, build_multi_tf_indicators = _load_backtester()

    if progress_callback:
        progress_callback(0, n_windows, "Loading candle data...")

    # Load candles
    candles_df = pd.read_csv(candles_path)
    ts_col = candles_df.columns[0]
    candles_df['timestamp'] = pd.to_datetime(candles_df[ts_col]).astype('datetime64[ns]')

    # Load/build indicator cache
    cache_path = candles_path.replace('.csv', '_indicators.parquet')
    if os.path.exists(cache_path):
        indicators_df = pd.read_parquet(cache_path)
        if 'timestamp' in indicators_df.columns:
            indicators_df['timestamp'] = indicators_df['timestamp'].astype('datetime64[ns]')
    else:
        data_dir = os.path.dirname(candles_path)
        indicators_df = build_multi_tf_indicators(data_dir, candles_df['timestamp'])

    exit_strat = _build_exit_strategy(exit_strategy_class, exit_strategy_params, pip_size)

    # Determine data range
    all_dates = pd.to_datetime(candles_df['timestamp'])
    data_start = all_dates.min()
    data_end   = all_dates.max()

    # Build window schedule
    # Each window: train [t, t+train_years), test [t+train_years, t+train_years+test_years)
    # Slide by test_years each step
    windows_schedule = []
    t = data_start
    while len(windows_schedule) < n_windows:
        train_start = t
        train_end   = t + pd.DateOffset(years=train_years)
        test_start  = train_end
        test_end    = test_start + pd.DateOffset(years=test_years)
        if test_end > data_end + pd.DateOffset(days=1):
            break
        windows_schedule.append((train_start, train_end, test_start, test_end))
        t += pd.DateOffset(years=test_years)

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

    for i, (train_start, train_end, test_start, test_end) in enumerate(windows_schedule):
        if _stop_flag.is_set():
            break

        w_label = f"W{i+1}: Train {train_start.year}–{train_end.year-1}, Test {test_start.year}"
        if progress_callback:
            progress_callback(i, len(windows_schedule), f"Window {i+1}/{len(windows_schedule)}: backtesting in-sample...")

        # In-sample
        try:
            in_trades = run_backtest(
                candles_df=candles_df,
                indicators_df=indicators_df,
                rules=rules,
                exit_strategy=exit_strat,
                start_date=train_start.strftime('%Y-%m-%d'),
                end_date=train_end.strftime('%Y-%m-%d'),
                pip_size=pip_size,
                spread_pips=spread_pips,
                commission_pips=commission_pips,
                account_size=account_size,
            )
        except Exception as e:
            in_trades = []

        if progress_callback:
            progress_callback(i, len(windows_schedule),
                              f"Window {i+1}/{len(windows_schedule)}: backtesting out-of-sample...")

        # Out-of-sample
        try:
            out_trades = run_backtest(
                candles_df=candles_df,
                indicators_df=indicators_df,
                rules=rules,
                exit_strategy=exit_strat,
                start_date=test_start.strftime('%Y-%m-%d'),
                end_date=test_end.strftime('%Y-%m-%d'),
                pip_size=pip_size,
                spread_pips=spread_pips,
                commission_pips=commission_pips,
                account_size=account_size,
            )
        except Exception as e:
            out_trades = []

        in_stats  = _compute_window_stats(in_trades)
        out_stats = _compute_window_stats(out_trades)

        in_wr  = in_stats['win_rate']
        out_wr = out_stats['win_rate']
        degradation = ((out_wr - in_wr) / in_wr * 100.0) if in_wr > 0 else 0.0
        edge_held = out_wr >= 0.50

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
        })
        completed += 1

    if progress_callback:
        progress_callback(len(windows_schedule), len(windows_schedule), "Summarising walk-forward results...")

    # Summary
    if completed < 2:
        verdict = 'INSUFFICIENT_DATA'
    else:
        out_wrs = [w['out_sample']['win_rate'] for w in results_windows]
        degs    = [w['degradation'] for w in results_windows]
        held    = [w['edge_held'] for w in results_windows]

        avg_out_wr      = float(np.mean(out_wrs))
        avg_degradation = float(np.mean(degs))
        edge_held_count = sum(held)
        edge_held_ratio = edge_held_count / len(held)

        if avg_out_wr >= 0.55 and avg_degradation > -15 and edge_held_ratio >= 0.60:
            verdict = 'LIKELY_REAL'
        elif avg_out_wr >= 0.50 and edge_held_ratio >= 0.40:
            verdict = 'INCONCLUSIVE'
        else:
            verdict = 'LIKELY_OVERFITTING'

    out_wrs_all = [w['out_sample']['win_rate'] for w in results_windows] if results_windows else [0.0]
    degs_all    = [w['degradation'] for w in results_windows] if results_windows else [0.0]
    held_all    = [w['edge_held'] for w in results_windows] if results_windows else []

    summary = {
        'verdict':           verdict,
        'windows_completed': completed,
        'avg_out_wr':        round(float(np.mean(out_wrs_all)), 4),
        'avg_degradation':   round(float(np.mean(degs_all)), 2),
        'edge_held_count':   sum(held_all),
        'edge_held_ratio':   round(sum(held_all) / max(len(held_all), 1), 3),
    }

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
):
    """
    Shuffle trade PnL values and re-run prop firm simulation to test
    whether results depend on trade sequence vs actual edge.

    Returns dict with stats, histogram data, and verdict.
    """
    _stop_flag.clear()

    import sys
    project_root = os.path.abspath(os.path.join(_HERE, '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from shared.prop_firm_simulator import simulate_challenge
    from project2_backtesting.prop_firm_tester import load_available_firms

    if not trades:
        return {'verdict': 'INSUFFICIENT_DATA', 'error': 'No trades provided'}

    # Discover challenge_id if not given
    if challenge_id is None:
        firms_data = load_available_firms()
        for fc in firms_data:
            if fc['firm_id'] == firm_id:
                challenge_id = fc['challenge_id']
                break
    if challenge_id is None:
        return {'verdict': 'INSUFFICIENT_DATA', 'error': f'No challenges found for {firm_id}'}

    trades_df = _trades_to_df(
        trades, risk_per_trade_pct, default_sl_pips, pip_value_per_lot, account_size
    )

    # Baseline: original order
    if progress_callback:
        progress_callback(0, n_simulations, "Running baseline (original order)...")

    try:
        baseline_summary = simulate_challenge(
            trades_df=trades_df,
            firm_id=firm_id,
            challenge_id=challenge_id,
            account_size=account_size,
            mode='sliding_window',
            num_samples=200,
            risk_per_trade_pct=risk_per_trade_pct,
            default_sl_pips=default_sl_pips,
            pip_value_per_lot=pip_value_per_lot,
        )
        baseline_pass_rate = float(baseline_summary.eval_pass_rate) if baseline_summary else 0.0
    except Exception as e:
        baseline_pass_rate = 0.0

    # Shuffle simulations
    pnl_values = list(trades_df['Profit'].values)
    dates      = list(trades_df['Close Date'].values)
    shuffled_rates = []

    for sim_i in range(n_simulations):
        if _stop_flag.is_set():
            break
        if progress_callback and sim_i % 50 == 0:
            progress_callback(sim_i, n_simulations,
                              f"Monte Carlo: shuffle {sim_i}/{n_simulations}...")

        shuffled_pnl = pnl_values[:]
        random.shuffle(shuffled_pnl)
        shuffled_df = pd.DataFrame({'Close Date': dates, 'Profit': shuffled_pnl})
        shuffled_df = shuffled_df.sort_values('Close Date').reset_index(drop=True)

        try:
            sim_summary = simulate_challenge(
                trades_df=shuffled_df,
                firm_id=firm_id,
                challenge_id=challenge_id,
                account_size=account_size,
                mode='sliding_window',
                num_samples=50,
                risk_per_trade_pct=risk_per_trade_pct,
                default_sl_pips=default_sl_pips,
                pip_value_per_lot=pip_value_per_lot,
            )
            rate = float(sim_summary.eval_pass_rate) if sim_summary else 0.0
        except Exception:
            rate = 0.0
        shuffled_rates.append(rate)

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
):
    """
    Re-run the backtest at increasing slippage levels to find where the
    strategy becomes unprofitable.

    Returns dict with 'levels', 'max_safe_slippage', 'breakeven_slippage', 'verdict'.
    """
    if slippage_levels is None:
        slippage_levels = [0, 1, 2, 3, 5]

    _stop_flag.clear()
    run_backtest, compute_stats, build_multi_tf_indicators = _load_backtester()

    # Load candles
    candles_df = pd.read_csv(candles_path)
    ts_col = candles_df.columns[0]
    candles_df['timestamp'] = pd.to_datetime(candles_df[ts_col]).astype('datetime64[ns]')

    # Reuse cache if available, otherwise build
    cache_path = candles_path.replace('.csv', '_indicators.parquet')
    if os.path.exists(cache_path):
        indicators_df = pd.read_parquet(cache_path)
        if 'timestamp' in indicators_df.columns:
            indicators_df['timestamp'] = indicators_df['timestamp'].astype('datetime64[ns]')
    else:
        data_dir = os.path.dirname(candles_path)
        indicators_df = build_multi_tf_indicators(data_dir, candles_df['timestamp'])

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
                run_trades = run_backtest(
                    candles_df=candles_df,
                    indicators_df=indicators_df,
                    rules=rules,
                    exit_strategy=exit_strat,
                    pip_size=pip_size,
                    spread_pips=spread_pips,
                    commission_pips=commission_pips,
                    slippage_pips=float(slip_pips),
                    account_size=account_size,
                )
                stats = compute_stats(run_trades)
                level_wrs.append(stats['win_rate'])
                level_avg_pips.append(stats['net_avg_pips'])
                level_total_pips.append(stats['net_total_pips'])
            except Exception:
                pass

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
    breakeven_slip = None
    for i in range(len(levels_results) - 1):
        a = levels_results[i]
        b = levels_results[i + 1]
        if a['total_pips'] > 0 and b['total_pips'] <= 0:
            denom = a['total_pips'] - b['total_pips']
            if denom != 0:
                t = a['total_pips'] / denom
                breakeven_slip = round(a['slippage_pips'] + t * (b['slippage_pips'] - a['slippage_pips']), 1)
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

def combined_score(walk_forward_result, monte_carlo_result=None, slippage_result=None):
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

    if wf_verdict == 'LIKELY_REAL':
        score += 30
    elif wf_verdict == 'INCONCLUSIVE':
        score += 10
    elif wf_verdict == 'LIKELY_OVERFITTING':
        score -= 20
    elif wf_verdict == 'INSUFFICIENT_DATA':
        score -= 10

    edge_held_ratio = wf_summary.get('edge_held_ratio', 0.0)
    score += int(edge_held_ratio * 15)

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
    n_simulations=500,
    risk_per_trade_pct=1.0,
    default_sl_pips=150.0,
    pip_value_per_lot=10.0,
    trades=None,
    wf_progress_callback=None,
    mc_progress_callback=None,
    slippage_result=None,
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
        )

    combined = combined_score(wf_result, mc_result, slippage_result)

    result = {
        'strategy_index': strategy_index,
        'validated_at':   validated_at,
        'walk_forward':   wf_result,
        'monte_carlo':    mc_result,
        'slippage':       slippage_result,
        'combined':       combined,
    }

    _save_validation(strategy_index, result)
    return result

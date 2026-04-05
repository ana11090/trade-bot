"""
Strategy Validator Panel — walk-forward validation + Monte Carlo robustness testing.

Proves whether a strategy has a real edge or is overfitting.
Results are saved to validation_results.json for use by the Prop Firm Test panel.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys
import threading
import json
import re

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

import state

BG      = "#f0f2f5"
WHITE   = "white"
GREEN   = "#2d8a4e"
RED     = "#e94560"
AMBER   = "#996600"
DARK    = "#1a1a2a"
GREY    = "#666666"
MIDGREY = "#555566"

GRADE_COLORS = {
    'A': "#28a745",
    'B': "#2d8a4e",
    'C': "#996600",
    'D': "#e67e00",
    'F': "#e94560",
}

# ── Module-level state ────────────────────────────────────────────────────────
_strategy_var   = None
_strategies     = []
_tree           = None
_selected_count = None
_check_vars     = {}  # index -> bool (checkbox state)
_firm_name_to_id = {}  # firm display name -> firm_id (for Monte Carlo)


# Settings vars
_train_var      = None
_test_var       = None
_windows_var    = None
_sims_var       = None
_mc_firm_var    = None
_stage_var      = None
_account_var    = None
_spread_var     = None
_comm_var       = None
_risk_var       = None
_sl_var         = None
_pipval_var     = None
_pip_size_var   = None

# Filter vars
_filt_wr        = None
_filt_pf        = None
_filt_trades    = None

# Widgets
_strat_info_lbl  = None
_prev_result_lbl = None
_start_wf_btn    = None
_start_mc_btn    = None
_start_full_btn  = None
_start_slip_btn  = None
_stop_btn        = None
_status_lbl      = None
_progress_bar    = None
_scroll_canvas   = None
_wf_frame        = None
_mc_frame        = None
_slip_frame      = None
_verdict_frame   = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Cache to prevent reloading 43MB file every time panel is shown
_strategies_cache = []
_cache_mtime = 0

def _load_strategies():
    global _strategies, _strategies_cache, _cache_mtime
    try:
        # Check if backtest_matrix.json has been modified
        backtest_path = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
        if os.path.exists(backtest_path):
            current_mtime = os.path.getmtime(backtest_path)
            if current_mtime == _cache_mtime and _strategies_cache:
                # Use cached data — file hasn't changed
                _strategies = _strategies_cache
                return

            # File changed or no cache — reload
            from project2_backtesting.strategy_refiner import load_strategy_list
            _strategies = load_strategy_list()
            _strategies_cache = _strategies
            _cache_mtime = current_mtime
        else:
            _strategies = []
    except Exception as e:
        print(f"[validator_panel] {e}")
        _strategies = []


def _get_selected_index():
    """Get the first selected strategy index."""
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    if '───' in val:
        return None  # separator, not a real selection
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _get_all_selected_indices():
    """Get all checked strategy indices (from checkboxes)."""
    global _check_vars
    if not _check_vars:
        return []

    checked = []
    for idx_str, is_checked in _check_vars.items():
        if is_checked:
            try:
                checked.append(int(idx_str))
            except (ValueError, TypeError) as e:
                print(f"[validator] Warning: Could not convert checkbox key '{idx_str}' to int: {e}")
                continue

    return sorted(checked)  # Return sorted list for consistent ordering


def _parse_exit_strategy(exit_name, exit_str):
    """Parse the human-readable exit_strategy string back into class + params.

    Returns (class_name, params_dict).
    """
    if not exit_name or not exit_str:
        return 'FixedSLTP', {'sl_pips': 150, 'tp_pips': 300}

    name = exit_name.lower().strip()
    s = exit_str.strip()

    if name == 'fixed sl/tp' or s.startswith('Fixed SL'):
        # "Fixed SL 150 pips / TP 300 pips"
        m = re.search(r'SL\s+(\d+).*TP\s+(\d+)', s)
        if m:
            return 'FixedSLTP', {'sl_pips': int(m.group(1)), 'tp_pips': int(m.group(2))}
        return 'FixedSLTP', {'sl_pips': 150, 'tp_pips': 300}

    elif name == 'trailing stop' or 'trail after' in s:
        # "SL 150 pips, trail after +100 pips, trail distance 150 pips"
        sl_m = re.search(r'SL\s+(\d+)', s)
        act_m = re.search(r'trail after \+(\d+)', s)
        dist_m = re.search(r'trail distance\s+(\d+)', s)
        return 'TrailingStop', {
            'sl_pips': int(sl_m.group(1)) if sl_m else 150,
            'activation_pips': int(act_m.group(1)) if act_m else 50,
            'trail_distance_pips': int(dist_m.group(1)) if dist_m else 100,
        }

    elif name == 'atr-based' or 'xATR' in s:
        # "SL 1.5xATR, TP 3.0xATR"
        sl_m = re.search(r'SL\s+([\d.]+)xATR', s)
        tp_m = re.search(r'TP\s+([\d.]+)xATR', s)
        return 'ATRBased', {
            'sl_atr_mult': float(sl_m.group(1)) if sl_m else 1.5,
            'tp_atr_mult': float(tp_m.group(1)) if tp_m else 3.0,
        }

    elif name == 'time-based' or 'close after' in s:
        # "SL 150 pips, close after 6 candles"
        sl_m = re.search(r'SL\s+(\d+)', s)
        candles_m = re.search(r'after\s+(\d+)\s+candles', s)
        return 'TimeBased', {
            'sl_pips': int(sl_m.group(1)) if sl_m else 150,
            'max_candles': int(candles_m.group(1)) if candles_m else 6,
        }

    elif name == 'indicator exit' or 'exit when' in s:
        # "SL 150 pips, exit when H1_rsi_14 above 70"
        sl_m = re.search(r'SL\s+(\d+)', s)
        ind_m = re.search(r'when\s+(\S+)\s+(above|below)\s+([\d.]+)', s)
        if ind_m:
            return 'IndicatorExit', {
                'sl_pips': int(sl_m.group(1)) if sl_m else 150,
                'exit_indicator': ind_m.group(1),
                'exit_threshold': float(ind_m.group(3)),
                'exit_direction': ind_m.group(2),
            }
        return 'IndicatorExit', {'sl_pips': 150, 'exit_indicator': 'H1_rsi_14',
                                  'exit_threshold': 70, 'exit_direction': 'above'}

    elif name == 'hybrid' or 'BE at' in s:
        # "SL 150, BE at +50, trail 100, max 12 candles"
        sl_m = re.search(r'SL\s+(\d+)', s)
        be_m = re.search(r'BE at \+(\d+)', s)
        trail_m = re.search(r'trail\s+(\d+)', s)
        candles_m = re.search(r'max\s+(\d+)\s+candles', s)
        return 'HybridExit', {
            'sl_pips': int(sl_m.group(1)) if sl_m else 150,
            'breakeven_activation_pips': int(be_m.group(1)) if be_m else 50,
            'trail_distance_pips': int(trail_m.group(1)) if trail_m else 100,
            'max_candles': int(candles_m.group(1)) if candles_m else 12,
        }

    # Fallback
    return 'FixedSLTP', {'sl_pips': 150, 'tp_pips': 300}


def _get_strategy_meta(idx):
    """Return (rules, exit_class, exit_params, trades, spread, commission) for strategy idx."""
    try:
        backtest_path = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
        with open(backtest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        r = data['results'][idx]
        rules = r.get('rules', [])

        # Parse exit strategy from stored strings
        exit_name = r.get('exit_name', '')
        exit_str  = r.get('exit_strategy', '')
        exit_class, exit_params = _parse_exit_strategy(exit_name, exit_str)

        trades     = r.get('trades', [])
        spread     = r.get('spread_pips', 2.5)
        commission = r.get('commission_pips', 0.0)
        return rules, exit_class, exit_params, trades, spread, commission
    except Exception:
        return [], 'FixedSLTP', {'sl_pips': 150, 'tp_pips': 300}, [], 2.5, 0.0


def _get_candles_path():
    for p in [
        os.path.join(project_root, 'data', 'xauusd_H1.csv'),
        os.path.join(project_root, 'data', 'xauusd', 'H1.csv'),
    ]:
        if os.path.exists(p):
            return p
    return None


def _update_strat_info():
    global _strat_info_lbl, _prev_result_lbl, _check_vars
    if not _strat_info_lbl:
        return

    # Show info for last checked item (or first selected)
    checked_indices = [int(idx) for idx, is_checked in _check_vars.items() if is_checked]
    idx = checked_indices[-1] if checked_indices else None

    if idx is None:
        _strat_info_lbl.configure(text="")
        return

    # Strategy stats - show combined name like View Results
    for s in _strategies:
        if s['index'] == idx:
            rule_name = s.get('rule_combo', '?')
            exit_name = s.get('exit_name', '?')
            combined_name = f"{rule_name} × {exit_name}"

            text = (f"{combined_name} [{s['total_trades']} trades, "
                    f"WR {s['win_rate']:.1f}%, "
                    f"PF {s['net_profit_factor']:.2f}, "
                    f"{s['net_total_pips']:+,.0f} pips]")
            _strat_info_lbl.configure(text=text, fg=MIDGREY)
            break
    # Previous validation result
    if _prev_result_lbl:
        try:
            from project2_backtesting.strategy_validator import get_validation_for_strategy
            result = get_validation_for_strategy(idx)
            if result is None:
                _prev_result_lbl.configure(text="Not yet validated", fg=GREY)
            else:
                combined = result.get('combined', {})
                grade    = combined.get('grade', '?')
                score    = combined.get('confidence_score', 0)
                validated_at = result.get('validated_at', '')[:10]
                color = GRADE_COLORS.get(grade, GREY)
                _prev_result_lbl.configure(
                    text=f"Previously validated: Grade {grade} ({score}/100) on {validated_at}",
                    fg=color,
                )
        except Exception:
            _prev_result_lbl.configure(text="", fg=GREY)


def _set_buttons(running):
    for btn in (_start_wf_btn, _start_mc_btn, _start_full_btn, _start_slip_btn):
        if btn:
            btn.configure(state="disabled" if running else "normal")
    if _stop_btn:
        _stop_btn.configure(state="normal" if running else "disabled")


def _stop():
    try:
        from project2_backtesting.strategy_validator import stop_validation
        stop_validation()
    except Exception:
        pass
    if _status_lbl:
        _status_lbl.configure(text="Stopped by user", fg=AMBER)
    _set_buttons(False)


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

def _display_slip_results(slip_result):
    if _slip_frame is None:
        return
    for w in _slip_frame.winfo_children():
        w.destroy()

    tk.Label(_slip_frame, text="Slippage Stress Test",
             font=("Segoe UI", 11, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(8, 4))

    if not slip_result or slip_result.get('verdict') == 'INSUFFICIENT_DATA':
        msg = slip_result.get('error', 'Not run.') if slip_result else 'Not run.'
        tk.Label(_slip_frame, text=msg,
                 font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(anchor="w", padx=5)
        return

    levels   = slip_result.get('levels', [])
    max_safe = slip_result.get('max_safe_slippage', 0)
    be_slip  = slip_result.get('breakeven_slippage', '?')
    verdict  = slip_result.get('verdict', '?')

    verdict_colors = {'ROBUST': GREEN, 'MODERATE': AMBER, 'FRAGILE': RED, 'NO_EDGE': RED}
    verdict_icons  = {'ROBUST': '✅', 'MODERATE': '⚠️', 'FRAGILE': '❌', 'NO_EDGE': '❌'}

    # Table header
    hdr_frame = tk.Frame(_slip_frame, bg=WHITE, padx=12, pady=6)
    hdr_frame.pack(fill="x", padx=5, pady=(0, 2))
    tk.Label(hdr_frame,
             text=f"{'Slippage':>10}  {'Win Rate':>10}  {'Avg Pips':>10}  {'Total Pips':>11}  {'Status':>10}",
             font=("Consolas", 8, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")

    # Level rows
    table_frame = tk.Frame(_slip_frame, bg=WHITE, padx=12, pady=4)
    table_frame.pack(fill="x", padx=5, pady=(0, 4))
    for lvl in levels:
        sp     = lvl['slippage_pips']
        wr     = lvl['win_rate']
        ap     = lvl['avg_pips']
        tp     = lvl['total_pips']
        ok     = lvl['profitable']
        color  = GREEN if ok else RED
        status = "profitable" if ok else "loss"
        line   = (f"{sp:>8.1f}p  {wr:>9.1f}%  {ap:>+9.1f}  {tp:>+10.0f}  {status:>10}")
        tk.Label(table_frame, text=line,
                 font=("Consolas", 8), bg=WHITE, fg=color).pack(anchor="w")

    # Summary card
    sum_card = tk.Frame(_slip_frame, bg="#1a1a2a", padx=14, pady=10)
    sum_card.pack(fill="x", padx=5, pady=(0, 4))
    tk.Label(sum_card,
             text=f"Max safe slippage: {max_safe} pips  |  Estimated breakeven: ~{be_slip} pips",
             font=("Consolas", 8), bg="#1a1a2a", fg="#aaaacc").pack(anchor="w")
    tk.Label(sum_card,
             text=f"Verdict: {verdict}  {verdict_icons.get(verdict, '')}",
             font=("Segoe UI", 9, "bold"),
             bg="#1a1a2a",
             fg=verdict_colors.get(verdict, WHITE)).pack(anchor="w", pady=(6, 0))


def _clear_results():
    for frame in (_wf_frame, _mc_frame, _slip_frame, _verdict_frame):
        if frame:
            for w in frame.winfo_children():
                w.destroy()


def _display_wf_results(wf_result):
    if _wf_frame is None:
        return
    for w in _wf_frame.winfo_children():
        w.destroy()

    windows = wf_result.get('windows', [])
    summary = wf_result.get('summary', {})

    tk.Label(_wf_frame, text="Walk-Forward Results",
             font=("Segoe UI", 11, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(8, 4))

    if not windows:
        tk.Label(_wf_frame, text="Insufficient data for walk-forward validation.",
                 font=("Segoe UI", 9, "italic"), bg=BG, fg=RED).pack(anchor="w", padx=5)
        return

    for w in windows:
        ins  = w['in_sample']
        outs = w['out_sample']
        deg  = w['degradation']
        in_err  = w.get('in_error')
        out_err = w.get('out_error')

        # 0-trade windows get grey/warning — NOT green
        if ins['count'] == 0 and outs['count'] == 0:
            border_color = "#999999"
            deg_color    = GREY
            check        = "⚪"
            deg_text     = "NO TRADES"
        elif outs['count'] == 0:
            border_color = "#996600"
            deg_color    = AMBER
            check        = "⚠️"
            deg_text     = "0 OOS trades"
        elif deg > -15:
            border_color = "#2d8a4e"
            deg_color    = GREEN
            check        = "✅"
            deg_text     = f"{deg:+.1f}%"
        elif deg < -25:
            border_color = "#e94560"
            deg_color    = RED
            check        = "❌"
            deg_text     = f"{deg:+.1f}%"
        else:
            border_color = "#996600"
            deg_color    = AMBER
            check        = "⚠️"
            deg_text     = f"{deg:+.1f}%"

        card = tk.Frame(_wf_frame, bg=WHITE,
                        highlightbackground=border_color, highlightthickness=2,
                        padx=12, pady=8)
        card.pack(fill="x", padx=5, pady=3)

        title_row = tk.Frame(card, bg=WHITE)
        title_row.pack(fill="x")
        tk.Label(title_row,
                 text=f"{w['label']}",
                 font=("Segoe UI", 10, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT)
        tk.Label(title_row,
                 text=f"  {deg_text}  {check}",
                 font=("Segoe UI", 9, "bold"), bg=WHITE, fg=deg_color).pack(side=tk.LEFT)

        tk.Label(card,
                 text=f"  IN:  {ins['count']:3d} trades  WR {ins['win_rate']*100:.1f}%  "
                      f"avg {ins['avg_pips']:+.0f} pips  PF {ins['profit_factor']:.2f}",
                 font=("Consolas", 8), bg=WHITE, fg=MIDGREY).pack(anchor="w")
        tk.Label(card,
                 text=f"  OUT: {outs['count']:3d} trades  WR {outs['win_rate']*100:.1f}%  "
                      f"avg {outs['avg_pips']:+.0f} pips  PF {outs['profit_factor']:.2f}",
                 font=("Consolas", 8), bg=WHITE, fg=DARK if outs['count'] > 0 else GREY).pack(anchor="w")

        # Show errors if run_backtest crashed
        if in_err:
            tk.Label(card, text=f"  ⚠ IN error: {in_err}",
                     font=("Consolas", 8), bg=WHITE, fg=RED).pack(anchor="w")
        if out_err:
            tk.Label(card, text=f"  ⚠ OUT error: {out_err}",
                     font=("Consolas", 8), bg=WHITE, fg=RED).pack(anchor="w")

    # Summary
    verdict = summary.get('verdict', 'INSUFFICIENT_DATA')
    verdict_colors = {
        'LIKELY_REAL':       GREEN,
        'INCONCLUSIVE':      AMBER,
        'LIKELY_OVERFITTING': RED,
        'INSUFFICIENT_DATA': GREY,
    }
    sum_card = tk.Frame(_wf_frame, bg="#f0f8ff",
                        highlightbackground="#b0c4de", highlightthickness=1,
                        padx=12, pady=8)
    sum_card.pack(fill="x", padx=5, pady=(6, 2))
    tk.Label(sum_card, text="Summary", font=("Segoe UI", 9, "bold"),
             bg="#f0f8ff", fg=DARK).pack(anchor="w")
    tk.Label(sum_card,
             text=f"Avg out-of-sample WR: {summary.get('avg_out_wr',0)*100:.1f}%  |  "
                  f"Avg degradation: {summary.get('avg_degradation',0):+.1f}%  |  "
                  f"Edge held: {summary.get('edge_held_count',0)}/{summary.get('windows_completed',0)} windows",
             font=("Segoe UI", 9), bg="#f0f8ff", fg=MIDGREY).pack(anchor="w", pady=(2, 4))
    tk.Label(sum_card,
             text=f"Verdict: {verdict.replace('_', ' ')}",
             font=("Segoe UI", 10, "bold"),
             bg="#f0f8ff",
             fg=verdict_colors.get(verdict, GREY)).pack(anchor="w")


def _display_mc_results(mc_result):
    if _mc_frame is None:
        return
    for w in _mc_frame.winfo_children():
        w.destroy()

    tk.Label(_mc_frame, text="Monte Carlo Robustness Test",
             font=("Segoe UI", 11, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(8, 4))

    if not mc_result or mc_result.get('verdict') == 'INSUFFICIENT_DATA':
        msg = mc_result.get('error', 'No Monte Carlo data.') if mc_result else 'Not run.'
        tk.Label(_mc_frame, text=msg,
                 font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(anchor="w", padx=5)
        return

    hist = mc_result.get('histogram', [])
    n_sims = mc_result.get('n_simulations', 0)
    firm_id = mc_result.get('firm_id', '?').upper()
    baseline = mc_result.get('baseline_pass_rate', 0) * 100
    mean_pr  = mc_result.get('mean_pass_rate', 0) * 100
    p5_pr    = mc_result.get('p5_pass_rate', 0) * 100
    p95_pr   = mc_result.get('p95_pass_rate', 0) * 100
    verdict  = mc_result.get('verdict', '?')

    header_card = tk.Frame(_mc_frame, bg=WHITE, padx=12, pady=8)
    header_card.pack(fill="x", padx=5, pady=(0, 4))
    tk.Label(header_card,
             text=f"Pass Rate Distribution ({n_sims} shuffles, {firm_id}):",
             font=("Segoe UI", 9, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")

    # Text histogram
    hist_frame = tk.Frame(_mc_frame, bg="#1a1a2a", padx=14, pady=10)
    hist_frame.pack(fill="x", padx=5, pady=(0, 4))

    if hist:
        max_pct = max(h['pct'] for h in hist) or 1
        for h in hist:
            bar_len = int(h['pct'] / max_pct * 28)
            bar_str = "█" * bar_len
            is_near_mean = abs(h['pct'] - max_pct) < 5
            color = "#88ddaa" if is_near_mean else "#5588aa"
            row = tk.Frame(hist_frame, bg="#1a1a2a")
            row.pack(anchor="w")
            tk.Label(row, text=f"{h['label']:>8}  |{bar_str:<28}  {h['pct']:5.1f}%",
                     font=("Consolas", 8), bg="#1a1a2a", fg=color).pack(anchor="w")

    tk.Label(hist_frame,
             text=f"\nOriginal: {baseline:.0f}%  |  Mean: {mean_pr:.0f}%  |  "
                  f"Worst (5th%): {p5_pr:.0f}%  |  Best (95th%): {p95_pr:.0f}%",
             font=("Consolas", 8), bg="#1a1a2a", fg="#aaaacc").pack(anchor="w", pady=(4, 0))

    verdict_colors = {'ROBUST': GREEN, 'MODERATE': AMBER, 'FRAGILE': RED}
    verdict_icons  = {'ROBUST': '✅', 'MODERATE': '⚠️', 'FRAGILE': '❌'}
    tk.Label(hist_frame,
             text=f"Verdict: {verdict}  {verdict_icons.get(verdict, '')}",
             font=("Segoe UI", 9, "bold"),
             bg="#1a1a2a",
             fg=verdict_colors.get(verdict, WHITE)).pack(anchor="w", pady=(6, 0))


def _show_estimation(trades, parent_frame):
    """Show payout or eval estimation after validation."""
    if not trades or len(trades) < 20:
        return

    try:
        global _stage_var, _mc_firm_var, _account_var, _scroll_canvas

        stage = _stage_var.get().lower() if _stage_var else "funded"
        acct = float(_account_var.get()) if _account_var else 100000
        firm_name = _mc_firm_var.get() if _mc_firm_var else ""
    except:
        stage = "funded"
        acct = 100000
        firm_name = ""

    risk = 1.0
    pip_value = 10.0
    sl_pips = 150
    lot_size = (acct * risk / 100) / (sl_pips * pip_value)
    dollar_per_pip = pip_value * lot_size

    # Load firm data
    firm_data = None
    profit_split = 80
    try:
        import glob
        prop_dir = os.path.join(project_root, 'prop_firms')
        for fp in glob.glob(os.path.join(prop_dir, '*.json')):
            with open(fp, encoding='utf-8') as f:
                fd = json.load(f)
            if fd.get('firm_name') == firm_name:
                firm_data = fd
                profit_split = fd['challenges'][0].get('funded', {}).get('profit_split_pct', 80)
                break
    except:
        pass

    import pandas as pd
    daily_pnls = {}
    for t in trades:
        try:
            day = str(pd.to_datetime(t.get('entry_time', '')).date())
            pnl = (t.get('net_pips', 0) or 0) * dollar_per_pip
            daily_pnls[day] = daily_pnls.get(day, 0) + pnl
        except:
            continue

    if not daily_pnls:
        return

    days_sorted = sorted(daily_pnls.keys())

    est_frame = tk.LabelFrame(parent_frame,
        text="💰 Payout Estimation" if stage == "funded" else "🎯 Eval Target Estimation",
        font=("Segoe UI", 10, "bold"), bg=WHITE, fg="#4a148c" if stage == "funded" else "#e65100",
        padx=10, pady=8)
    est_frame.pack(fill="x", padx=5, pady=(10, 5))

    # Add source disclaimer
    tk.Label(est_frame,
        text="Based on in-sample backtest trades — validate with walk-forward first",
        bg=WHITE, fg=AMBER, font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(0, 4))

    if stage == "funded":
        # Payout estimation — 14-day windows
        consistency_limit = 20
        min_profit_days_req = 3
        min_day_threshold = acct * 0.005

        if firm_data:
            for rule in firm_data.get('trading_rules', []):
                if rule.get('type') == 'consistency':
                    consistency_limit = rule.get('parameters', {}).get('max_day_pct', 20)
                elif rule.get('type') == 'min_profitable_days':
                    min_profit_days_req = rule.get('parameters', {}).get('min_days', 3)

        windows_total = 0
        windows_pass = 0
        window_profits = []

        for start_i in range(0, len(days_sorted) - 5, 7):
            start_day = pd.to_datetime(days_sorted[start_i])
            window = {}
            for d in days_sorted[start_i:]:
                if (pd.to_datetime(d) - start_day).days >= 14:
                    break
                window[d] = daily_pnls[d]

            if not window:
                continue

            total_profit = sum(v for v in window.values() if v > 0)
            net = sum(window.values())
            windows_total += 1

            if total_profit <= 0:
                continue

            best_day = max(window.values())
            best_pct = best_day / total_profit * 100
            prof_days = sum(1 for v in window.values() if v >= min_day_threshold)

            if best_pct <= consistency_limit and prof_days >= min_profit_days_req and net > 0:
                windows_pass += 1
                window_profits.append(net * profit_split / 100)

        if windows_total > 0 and window_profits:
            pr = windows_pass / windows_total * 100
            avg_p = sum(window_profits) / len(window_profits)
            min_p = min(window_profits)
            max_p = max(window_profits)
            annual = avg_p * (365 / 14)

            tk.Label(est_frame,
                text=f"Pass rate: {pr:.0f}% of 14-day periods  |  "
                     f"Avg payout: ${avg_p:,.0f}  |  Min: ${min_p:,.0f}  |  Max: ${max_p:,.0f}",
                bg=WHITE, fg="#333", font=("Segoe UI", 10)).pack(anchor="w")
            tk.Label(est_frame,
                text=f"Annual estimate: ${annual:,.0f}  |  "
                     f"Consistency: {consistency_limit}% rule  |  "
                     f"Min profitable days: {min_profit_days_req}  |  "
                     f"Split: {profit_split}%",
                bg=WHITE, fg="#666", font=("Segoe UI", 9)).pack(anchor="w")
        else:
            tk.Label(est_frame,
                text="0% of periods pass payout rules — strategy won't generate payouts",
                bg=WHITE, fg="#dc3545", font=("Segoe UI", 10)).pack(anchor="w")

    else:
        # Evaluation: days to reach target
        profit_target_pct = 6.0
        total_dd_limit = 10.0
        try:
            if firm_data:
                phases = firm_data['challenges'][0].get('phases', [])
                if phases:
                    profit_target_pct = phases[0].get('profit_target_pct', 6.0)
                    total_dd_limit = phases[0].get('max_total_drawdown_pct', 10.0)
        except:
            pass

        target_dollars = acct * (profit_target_pct / 100)
        days_to_target = []
        blown_count = 0
        total_attempts = 0

        for start_i in range(0, len(days_sorted) - 5, 7):
            running = 0
            day_count = 0
            reached = False
            total_attempts += 1

            for d in days_sorted[start_i:]:
                running += daily_pnls[d]
                day_count += 1
                if running >= target_dollars:
                    days_to_target.append(day_count)
                    reached = True
                    break
                if running < -(acct * total_dd_limit / 100):
                    blown_count += 1
                    break

        if days_to_target:
            pass_rate = len(days_to_target) / max(total_attempts, 1) * 100
            avg_d = sum(days_to_target) / len(days_to_target)
            blow_rate = blown_count / max(total_attempts, 1) * 100

            tk.Label(est_frame,
                text=f"Pass rate: {pass_rate:.0f}%  |  "
                     f"Avg days to {profit_target_pct}%: {avg_d:.0f}  |  "
                     f"Fastest: {min(days_to_target)}  |  Slowest: {max(days_to_target)}",
                bg=WHITE, fg="#333", font=("Segoe UI", 10)).pack(anchor="w")
            tk.Label(est_frame,
                text=f"Blow rate: {blow_rate:.0f}%  |  "
                     f"Target: ${target_dollars:,.0f}  |  "
                     f"Expected attempts: {100/max(pass_rate,1):.1f}",
                bg=WHITE, fg="#666", font=("Segoe UI", 9)).pack(anchor="w")
        else:
            tk.Label(est_frame,
                text=f"0% pass rate — never reaches {profit_target_pct}% target",
                bg=WHITE, fg="#dc3545", font=("Segoe UI", 10)).pack(anchor="w")

    # Update scroll region to show new content
    if _scroll_canvas:
        try:
            parent_frame.update_idletasks()
            _scroll_canvas.configure(scrollregion=_scroll_canvas.bbox("all"))
        except:
            pass


def _display_verdict(combined, trades=None):
    if _verdict_frame is None:
        return
    for w in _verdict_frame.winfo_children():
        w.destroy()

    score = combined.get('confidence_score', 0)
    grade = combined.get('grade', '?')
    rec   = combined.get('recommendation', '')
    warns = combined.get('warnings', [])
    verdicts = combined.get('verdicts', {})
    grade_color = GRADE_COLORS.get(grade, GREY)

    card = tk.Frame(_verdict_frame, bg=WHITE,
                    highlightbackground=grade_color, highlightthickness=2,
                    padx=16, pady=12)
    card.pack(fill="x", padx=5, pady=8)

    # Title row
    title_row = tk.Frame(card, bg=WHITE)
    title_row.pack(fill="x", pady=(0, 4))
    tk.Label(title_row, text=f"CONFIDENCE: {score}/100",
             font=("Segoe UI", 14, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT)
    tk.Label(title_row, text=f"GRADE: {grade}",
             font=("Segoe UI", 14, "bold"), bg=WHITE, fg=grade_color).pack(side=tk.RIGHT)

    # Progress bar (text-based)
    bar_filled = int(score / 100 * 40)
    bar_empty  = 40 - bar_filled
    bar_str = "█" * bar_filled + "░" * bar_empty
    tk.Label(card, text=bar_str, font=("Consolas", 9),
             bg=WHITE, fg=grade_color).pack(anchor="w", pady=(0, 8))

    # Verdicts
    wf_v   = verdicts.get('walk_forward', 'N/A')
    mc_v   = verdicts.get('monte_carlo', 'N/A')
    slip_v = verdicts.get('slippage', 'N/A')
    verdict_icons = {
        'LIKELY_REAL': '✅', 'INCONCLUSIVE': '⚠️',
        'LIKELY_OVERFITTING': '❌', 'INSUFFICIENT_DATA': '⚪',
        'ROBUST': '✅', 'MODERATE': '⚠️', 'FRAGILE': '❌', 'NO_EDGE': '❌', 'N/A': '—',
    }
    verdict_colors = {
        'LIKELY_REAL': GREEN, 'INCONCLUSIVE': AMBER,
        'LIKELY_OVERFITTING': RED, 'INSUFFICIENT_DATA': GREY,
        'ROBUST': GREEN, 'MODERATE': AMBER, 'FRAGILE': RED, 'NO_EDGE': RED, 'N/A': GREY,
    }
    tk.Label(card,
             text=f"Walk-Forward:  {wf_v.replace('_', ' ')}  {verdict_icons.get(wf_v, '')}",
             font=("Segoe UI", 10), bg=WHITE,
             fg=verdict_colors.get(wf_v, GREY)).pack(anchor="w", pady=1)
    tk.Label(card,
             text=f"Monte Carlo:   {mc_v.replace('_', ' ')}  {verdict_icons.get(mc_v, '')}",
             font=("Segoe UI", 10), bg=WHITE,
             fg=verdict_colors.get(mc_v, GREY)).pack(anchor="w", pady=1)
    tk.Label(card,
             text=f"Slippage Test: {slip_v.replace('_', ' ')}  {verdict_icons.get(slip_v, '')}",
             font=("Segoe UI", 10), bg=WHITE,
             fg=verdict_colors.get(slip_v, GREY)).pack(anchor="w", pady=(1, 8))

    # Recommendation
    tk.Label(card, text=rec, font=("Segoe UI", 10),
             bg=WHITE, fg=MIDGREY, wraplength=600, justify="left").pack(anchor="w", pady=(0, 8))

    # Warnings
    for warn in warns:
        tk.Label(card, text=f"⚠  {warn}",
                 font=("Segoe UI", 9), bg=WHITE, fg=AMBER).pack(anchor="w", pady=1)

    # Navigation buttons
    nav_row = tk.Frame(card, bg=WHITE)
    nav_row.pack(anchor="w", pady=(12, 0))

    tk.Button(nav_row, text="Proceed to Prop Firm Test →",
              command=lambda: state.all_panels.get('p2_prop_test') and _nav('p2_prop_test'),
              bg="#667eea", fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=14, pady=5).pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(nav_row, text="Back to Refiner",
              command=lambda: _nav('p2_refiner'),
              bg=GREY, fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=14, pady=5).pack(side=tk.LEFT)

    # Show stage-aware estimation ONLY if walk-forward shows a real edge
    wf_verdict = verdicts.get('walk_forward', 'N/A')
    if trades and wf_verdict not in ('LIKELY_OVERFITTING', 'N/A', 'INSUFFICIENT_DATA'):
        _show_estimation(trades, _verdict_frame)
    elif trades and wf_verdict == 'LIKELY_OVERFITTING':
        warn_frame = tk.LabelFrame(_verdict_frame,
            text="🎯 Estimation Suppressed",
            font=("Segoe UI", 10, "bold"), bg=WHITE, fg=RED,
            padx=10, pady=8)
        warn_frame.pack(fill="x", padx=5, pady=(10, 5))
        tk.Label(warn_frame,
            text="Walk-forward validation indicates overfitting.\n"
                 "Estimation is hidden because in-sample results are unreliable.\n"
                 "Go back to the Refiner and improve the strategy before estimating payouts.",
            bg=WHITE, fg="#666", font=("Segoe UI", 9), wraplength=550,
            justify="left").pack(anchor="w")


def _nav(panel_name):
    """Navigate to another panel by reusing show_panel from sidebar."""
    try:
        from sidebar import build_sidebar
        for pframe in state.all_panels.values():
            pframe.pack_forget()
        if panel_name in state.all_panels:
            state.all_panels[panel_name].pack(fill="both", expand=True)
        state.active_panel[0] = panel_name
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Run logic
# ─────────────────────────────────────────────────────────────────────────────

def _make_progress_cb(label_text):
    def _cb(step, total, message):
        if _status_lbl and state.window:
            pct = int(step / max(total, 1) * 100)
            state.window.after(0, lambda: _status_lbl.configure(
                text=f"{label_text} | {message}", fg=GREY))
            if _progress_bar:
                state.window.after(0, lambda: _progress_bar.configure(value=pct))
    return _cb


def _run_multi(mode):
    """Run validation on all selected strategies, one at a time."""
    indices = _get_all_selected_indices()
    if not indices:
        messagebox.showwarning("No Selection", "Select at least one strategy from the table.")
        return

    if len(indices) == 1:
        # Single selection — run as before using original _run
        _run(mode)
        return

    # Multiple selection — confirm first
    if not messagebox.askyesno("Batch Validation",
                               f"Run {mode} validation on {len(indices)} selected strategies?\n\n"
                               f"This may take several minutes."):
        return

    # Run sequentially - wait for each validation to complete
    def _worker():
        state.window.after(0, lambda: _set_buttons(True))
        try:
            for i, idx in enumerate(indices):
                strat = next((s for s in _strategies if s['index'] == idx), None)
                if not strat:
                    continue

                if _status_lbl:
                    state.window.after(0, lambda lbl=strat['label'], i=i, total=len(indices):
                                        _status_lbl.config(text=f"[{i+1}/{total}] {lbl}..."))

                # Run validation and WAIT for it to finish
                done = threading.Event()
                _run(mode, override_idx=idx, done_event=done)
                done.wait(timeout=600)  # Wait up to 10 minutes per strategy

            if _status_lbl:
                state.window.after(0, lambda: _status_lbl.config(
                    text=f"✅ Done — validated {len(indices)} strategies"))
        finally:
            state.window.after(0, lambda: _set_buttons(False))

    threading.Thread(target=_worker, daemon=True).start()


def _run(mode, override_idx=None, done_event=None):
    """
    mode: 'wf' | 'mc' | 'full' | 'slip'
    override_idx: if provided, use this index instead of reading from checkboxes/dropdown
    done_event: threading.Event — set when validation completes (for batch mode)
    """
    # Use override index if provided (for batch validation)
    if override_idx is not None:
        idx = override_idx
    else:
        # Get from checkboxes first, fallback to dropdown
        indices = _get_all_selected_indices()
        if indices:
            idx = int(indices[0])  # Use first checked item
        else:
            idx = _get_selected_index()  # Fallback to dropdown

    if idx is None:
        messagebox.showerror("No Strategy", "Select a strategy first.")
        return

    candles_path = _get_candles_path()
    if not candles_path and mode in ('wf', 'full', 'slip'):
        messagebox.showerror("No Candle Data",
                             "H1 candle CSV not found in data/ folder.\n"
                             "Required for walk-forward validation.")
        if mode == 'full':
            pass  # still run MC-only below
        else:
            return

    rules, exit_class, exit_params, trades, spread_meta, comm_meta = _get_strategy_meta(idx)

    try:
        account_size  = int(_account_var.get())
        spread_pips   = float(_spread_var.get())
        comm_pips     = float(_comm_var.get())
        risk_pct      = float(_risk_var.get())
        sl_pips       = float(_sl_var.get())
        pip_val       = float(_pipval_var.get())
        pip_size      = float(_pip_size_var.get())
        n_windows     = int(_windows_var.get())
        train_years   = int(_train_var.get())
        test_years    = int(_test_var.get())
        n_sims        = int(_sims_var.get())
        # Use firm_id mapping instead of string transformation
        mc_firm       = _firm_name_to_id.get(_mc_firm_var.get(),
                        _mc_firm_var.get().lower().replace(' ', '').replace('-', '').replace('_', ''))
    except ValueError:
        messagebox.showerror("Invalid Settings", "Check that all settings are valid numbers.")
        return

    _set_buttons(True)
    _clear_results()
    if _status_lbl:
        _status_lbl.configure(text="Starting...", fg=GREY)
    if _progress_bar:
        _progress_bar.configure(value=0)

    def _worker():
        try:
            from project2_backtesting.strategy_validator import (
                walk_forward_validate, monte_carlo_test, combined_score,
                slippage_stress_test, run_full_validation, _save_validation,
            )

            wf_result   = None
            mc_result   = None
            slip_result = None

            if mode in ('wf', 'full') and candles_path:
                wf_result = walk_forward_validate(
                    rules=rules,
                    candles_path=candles_path,
                    exit_strategy_class=exit_class,
                    exit_strategy_params=exit_params,
                    n_windows=n_windows,
                    train_years=train_years,
                    test_years=test_years,
                    pip_size=pip_size,
                    spread_pips=spread_pips,
                    commission_pips=comm_pips,
                    account_size=account_size,
                    progress_callback=_make_progress_cb("Walk-Forward"),
                )
                state.window.after(0, lambda r=wf_result: _display_wf_results(r))

            if mode in ('mc', 'full'):
                mc_result = monte_carlo_test(
                    trades=trades,
                    firm_id=mc_firm,
                    account_size=account_size,
                    n_simulations=n_sims,
                    risk_per_trade_pct=risk_pct,
                    default_sl_pips=sl_pips,
                    pip_value_per_lot=pip_val,
                    progress_callback=_make_progress_cb("Monte Carlo"),
                )
                state.window.after(0, lambda r=mc_result: _display_mc_results(r))

            if mode in ('slip', 'full') and candles_path:
                slip_result = slippage_stress_test(
                    trades=trades,
                    rules=rules,
                    candles_path=candles_path,
                    exit_strategy_class=exit_class,
                    exit_strategy_params=exit_params,
                    slippage_levels=[0, 1, 2, 3, 5],
                    pip_size=pip_size,
                    spread_pips=spread_pips,
                    commission_pips=comm_pips,
                    account_size=account_size,
                    n_runs_per_level=3,
                    progress_callback=_make_progress_cb("Slippage Test"),
                )
                state.window.after(0, lambda r=slip_result: _display_slip_results(r))

            if wf_result or mc_result or slip_result:
                combined = combined_score(wf_result, mc_result, slip_result)
                state.window.after(0, lambda c=combined, t=trades: _display_verdict(c, t))

                # Save
                result = {
                    'strategy_index': idx,
                    'validated_at':   __import__('datetime').datetime.now().isoformat(),
                    'walk_forward':   wf_result,
                    'monte_carlo':    mc_result,
                    'slippage':       slip_result,
                    'combined':       combined,
                }
                _save_validation(idx, result)
                state.window.after(0, _update_strat_info)

            state.window.after(0, lambda: _status_lbl.configure(
                text="Validation complete.", fg=GREEN))
            state.window.after(0, lambda: _progress_bar.configure(value=100))

        except Exception as e:
            import traceback; traceback.print_exc()
            state.window.after(0, lambda: _status_lbl.configure(
                text=f"Error: {e}", fg=RED))
        finally:
            state.window.after(0, lambda: _set_buttons(False))
            if done_event:
                done_event.set()

    threading.Thread(target=_worker, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Panel builder
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _strategy_var, _strat_info_lbl, _prev_result_lbl
    global _tree, _selected_count
    global _train_var, _test_var, _windows_var, _sims_var, _mc_firm_var, _stage_var
    global _account_var, _spread_var, _comm_var, _risk_var, _sl_var, _pipval_var, _pip_size_var
    global _start_wf_btn, _start_mc_btn, _start_full_btn, _start_slip_btn, _stop_btn
    global _status_lbl, _progress_bar, _scroll_canvas
    global _wf_frame, _mc_frame, _slip_frame, _verdict_frame

    _load_strategies()

    panel = tk.Frame(parent, bg=BG)

    # ── Scrollable canvas wrapping ALL content ────────────────────────────────
    _scroll_canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    vscroll = tk.Scrollbar(panel, orient="vertical", command=_scroll_canvas.yview)
    scroll_frame = tk.Frame(_scroll_canvas, bg=BG)

    scroll_frame.bind("<Configure>",
                      lambda e: _scroll_canvas.configure(
                          scrollregion=_scroll_canvas.bbox("all")))
    cwin = _scroll_canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    _scroll_canvas.configure(yscrollcommand=vscroll.set)
    _scroll_canvas.pack(side="left", fill="both", expand=True)
    vscroll.pack(side="right", fill="y")

    # Safe mousewheel binding
    def _on_enter(event):
        _scroll_canvas.bind("<MouseWheel>",
            lambda e: _scroll_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        _scroll_canvas.bind("<Button-4>", lambda e: _scroll_canvas.yview_scroll(-3, "units"))
        _scroll_canvas.bind("<Button-5>", lambda e: _scroll_canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        _scroll_canvas.unbind("<MouseWheel>")
        _scroll_canvas.unbind("<Button-4>")
        _scroll_canvas.unbind("<Button-5>")

    _scroll_canvas.bind("<Enter>", _on_enter)
    _scroll_canvas.bind("<Leave>", _on_leave)
    _scroll_canvas.bind("<Configure>",
                        lambda e: _scroll_canvas.itemconfig(cwin, width=e.width))

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(scroll_frame, bg=WHITE, pady=16)
    hdr.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(hdr, text="✅ Strategy Validator",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(hdr, text="Prove your strategy is real — not just overfitting",
             bg=WHITE, fg=GREY, font=("Segoe UI", 11)).pack(pady=(4, 0))

    # ── Strategy selector ─────────────────────────────────────────────────────
    sel_frame = tk.Frame(scroll_frame, bg=WHITE, padx=20, pady=12)
    sel_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(sel_frame, text="Strategy", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    _strategy_var = tk.StringVar(value="")

    if not _strategies:
        tk.Label(sel_frame,
                 text="No backtest results. Run the backtest first.",
                 font=("Segoe UI", 10, "italic"), bg=WHITE, fg=RED).pack(anchor="w")
    else:
        # Filter row
        global _filt_wr, _filt_pf, _filt_trades

        filter_frame = tk.Frame(sel_frame, bg=WHITE)
        filter_frame.pack(fill="x", pady=(0, 5))

        tk.Label(filter_frame, text="Filters:", font=("Segoe UI", 9, "bold"),
                 bg=WHITE, fg="#333").pack(side=tk.LEFT)

        tk.Label(filter_frame, text="Min WR:", font=("Segoe UI", 8),
                 bg=WHITE, fg="#555").pack(side=tk.LEFT, padx=(10, 2))
        _filt_wr = tk.StringVar(value="0")
        tk.Entry(filter_frame, textvariable=_filt_wr, width=4, font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Label(filter_frame, text="%", font=("Segoe UI", 8), bg=WHITE, fg="#555").pack(side=tk.LEFT)

        tk.Label(filter_frame, text="Min PF:", font=("Segoe UI", 8),
                 bg=WHITE, fg="#555").pack(side=tk.LEFT, padx=(10, 2))
        _filt_pf = tk.StringVar(value="0")
        tk.Entry(filter_frame, textvariable=_filt_pf, width=4, font=("Segoe UI", 8)).pack(side=tk.LEFT)

        tk.Label(filter_frame, text="Min Trades:", font=("Segoe UI", 8),
                 bg=WHITE, fg="#555").pack(side=tk.LEFT, padx=(10, 2))
        _filt_trades = tk.StringVar(value="0")
        tk.Entry(filter_frame, textvariable=_filt_trades, width=5, font=("Segoe UI", 8)).pack(side=tk.LEFT)

        def _apply_filters():
            _rebuild_tree()

        tk.Button(filter_frame, text="Apply", font=("Segoe UI", 8, "bold"),
                  bg="#667eea", fg="white", relief=tk.FLAT, padx=8,
                  command=_apply_filters).pack(side=tk.LEFT, padx=(10, 2))

        tk.Button(filter_frame, text="Reset", font=("Segoe UI", 8),
                  bg="#6c757d", fg="white", relief=tk.FLAT, padx=8,
                  command=lambda: [_filt_wr.set("0"), _filt_pf.set("0"), _filt_trades.set("0"), _rebuild_tree()]).pack(side=tk.LEFT, padx=2)

        # Sort buttons
        sort_frame = tk.Frame(sel_frame, bg=WHITE)
        sort_frame.pack(fill="x", pady=(5, 5))
        tk.Label(sort_frame, text="Sort by:", font=("Segoe UI", 9),
                 bg=WHITE, fg=GREY).pack(side=tk.LEFT)

        _sort_key = [None]  # current sort column

        def _sort_strategies(key, reverse=True):
            _sort_key[0] = key
            _strategies.sort(key=lambda s: s.get(key, 0), reverse=reverse)
            _rebuild_tree()

        for label, key, rev in [
            ("Profit ↓", "net_total_pips", True),
            ("Win Rate ↓", "win_rate", True),
            ("PF ↓", "net_profit_factor", True),
            ("DD ↑ (lowest)", "max_dd_pips", False),
            ("Trades ↓", "total_trades", True),
        ]:
            tk.Button(sort_frame, text=label, font=("Arial", 8),
                      bg="#667eea", fg="white", relief=tk.FLAT, padx=6, pady=2,
                      command=lambda k=key, r=rev: _sort_strategies(k, r)).pack(side=tk.LEFT, padx=2)

        # Strategy table with Treeview
        tree_frame = tk.Frame(sel_frame, bg=WHITE)
        tree_frame.pack(fill="x", pady=5)

        columns = ("select", "rule", "exit", "trades", "wr", "pf", "net_pips", "dd", "avg_pips")
        _tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                             height=min(len(_strategies), 10), selectmode="extended")

        _tree.heading("select",    text="✓")
        _tree.heading("rule",      text="Rule")
        _tree.heading("exit",      text="Exit Strategy")
        _tree.heading("trades",    text="Trades")
        _tree.heading("wr",        text="Win Rate")
        _tree.heading("pf",        text="PF")
        _tree.heading("net_pips",  text="Net Pips")
        _tree.heading("dd",        text="Max DD")
        _tree.heading("avg_pips",  text="Avg Pips")

        _tree.column("select",    width=30,  anchor="center")
        _tree.column("rule",      width=100, anchor="w")
        _tree.column("exit",      width=130, anchor="w")
        _tree.column("trades",    width=60,  anchor="center")
        _tree.column("wr",        width=70,  anchor="center")
        _tree.column("pf",        width=60,  anchor="center")
        _tree.column("net_pips",  width=90,  anchor="e")
        _tree.column("dd",        width=80,  anchor="e")
        _tree.column("avg_pips",  width=70,  anchor="e")

        # Scrollbar
        tree_scroll = tk.Scrollbar(tree_frame, orient="vertical", command=_tree.yview)
        _tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side=tk.RIGHT, fill="y")
        _tree.pack(fill="x")

        # Style rows with colors
        _tree.tag_configure("profitable", foreground="#28a745")
        _tree.tag_configure("losing", foreground="#dc3545")
        _tree.tag_configure("no_trades", foreground="#888888")

        def _rebuild_tree():
            global _check_vars, _selected_count
            _tree.delete(*_tree.get_children())

            # Get filter values
            try:
                min_wr = float(_filt_wr.get()) / 100
            except:
                min_wr = 0
            try:
                min_pf = float(_filt_pf.get())
            except:
                min_pf = 0
            try:
                min_trades = int(_filt_trades.get())
            except:
                min_trades = 0

            visible = 0
            for s in _strategies:
                wr = s.get('win_rate', 0)
                wr_val = wr if wr <= 1 else wr / 100
                pf = s.get('net_profit_factor', 0)
                trades = s.get('total_trades', 0)

                # Apply filters
                if wr_val < min_wr:
                    continue
                if pf < min_pf:
                    continue
                if trades < min_trades:
                    continue

                wr_str = f"{wr:.1f}%" if wr > 1 else f"{wr*100:.1f}%"
                net = s.get('net_total_pips', 0)
                dd = s.get('max_dd_pips', 0)

                if trades == 0:
                    tag = "no_trades"
                elif net > 0:
                    tag = "profitable"
                else:
                    tag = "losing"

                idx = str(s['index'])
                checked = _check_vars.get(idx, False)
                check_mark = "☑" if checked else "☐"

                _tree.insert("", "end", iid=idx, values=(
                    check_mark,
                    s.get('rule_combo', '?'),
                    s.get('exit_name', '?'),
                    trades,
                    wr_str,
                    f"{pf:.2f}",
                    f"{net:+,.0f}",
                    f"{dd:,.0f}",
                    f"{s.get('net_avg_pips', s.get('avg_pips', 0)):+.1f}",
                ), tags=(tag,))
                visible += 1

            if _selected_count:
                checked_count = sum(1 for v in _check_vars.values() if v)
                _selected_count.config(text=f"{checked_count} selected of {visible} shown ({len(_strategies)} total)")

        _rebuild_tree()

        # Checkbox click handler
        def _on_click(event):
            global _check_vars, _selected_count
            region = _tree.identify_region(event.x, event.y)
            if region == "cell":
                col = _tree.identify_column(event.x)
                item = _tree.identify_row(event.y)
                if col == "#1" and item:  # first column = checkbox
                    current = _check_vars.get(item, False)
                    _check_vars[item] = not current
                    # Update the display
                    values = list(_tree.item(item, "values"))
                    values[0] = "☑" if _check_vars[item] else "☐"
                    _tree.item(item, values=values)
                    # Update count and info
                    checked_count = sum(1 for v in _check_vars.values() if v)
                    visible = len(_tree.get_children())
                    _selected_count.config(text=f"{checked_count} selected of {visible} shown ({len(_strategies)} total)")
                    _update_strat_info()

        _tree.bind("<Button-1>", _on_click)

        # Select All / Deselect All buttons
        btn_frame = tk.Frame(sel_frame, bg=WHITE)
        btn_frame.pack(fill="x", pady=(5, 0))

        def _select_all():
            global _check_vars, _selected_count
            for item in _tree.get_children():
                _check_vars[item] = True
                values = list(_tree.item(item, "values"))
                values[0] = "☑"
                _tree.item(item, values=values)
            checked = sum(1 for v in _check_vars.values() if v)
            visible = len(_tree.get_children())
            _selected_count.config(text=f"{checked} selected of {visible} shown ({len(_strategies)} total)")
            _update_strat_info()

        def _deselect_all():
            global _check_vars, _selected_count
            for item in _tree.get_children():
                _check_vars[item] = False
                values = list(_tree.item(item, "values"))
                values[0] = "☐"
                _tree.item(item, values=values)
            _selected_count.config(text=f"0 selected of {len(_tree.get_children())} shown ({len(_strategies)} total)")
            _update_strat_info()

        tk.Button(btn_frame, text="Select All Visible", font=("Segoe UI", 8),
                  bg="#28a745", fg="white", relief=tk.FLAT, padx=8,
                  command=_select_all).pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(btn_frame, text="Deselect All", font=("Segoe UI", 8),
                  bg="#6c757d", fg="white", relief=tk.FLAT, padx=8,
                  command=_deselect_all).pack(side=tk.LEFT)

        # Selection info
        sel_info = tk.Label(sel_frame, text="Click the checkbox (✓) column to select strategies for validation.",
                             font=("Segoe UI", 8), bg=WHITE, fg=GREY)
        sel_info.pack(anchor="w", pady=(5, 0))

        _selected_count = tk.Label(sel_frame, text="0 selected of 0 shown (0 total)",
                                    font=("Segoe UI", 9, "bold"), bg=WHITE, fg="#667eea")
        _selected_count.pack(anchor="w")

    _strat_info_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 9),
                                bg=WHITE, fg=MIDGREY)
    _strat_info_lbl.pack(anchor="w", pady=(4, 0))

    _prev_result_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 9, "italic"),
                                 bg=WHITE, fg=GREY)
    _prev_result_lbl.pack(anchor="w", pady=(2, 0))

    _update_strat_info()

    # ── Settings ──────────────────────────────────────────────────────────────
    settings_frame = tk.Frame(scroll_frame, bg=WHITE, padx=20, pady=12)
    settings_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(settings_frame, text="Settings", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 8))

    def _field(parent, label, default, width=7):
        var = tk.StringVar(value=default)
        tk.Label(parent, text=label, font=("Segoe UI", 9), bg=WHITE, fg=DARK
                 ).pack(side=tk.LEFT, padx=(0, 3))
        tk.Entry(parent, textvariable=var, width=width).pack(side=tk.LEFT, padx=(0, 15))
        return var

    # Walk-forward row
    wf_row = tk.Frame(settings_frame, bg=WHITE)
    wf_row.pack(fill="x", pady=2)
    tk.Label(wf_row, text="Walk-Forward:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)
    _train_var   = _field(wf_row, "Train years:", "3", 4)
    _test_var    = _field(wf_row, "Test years:", "1", 4)
    _windows_var = _field(wf_row, "Windows:", "4", 4)

    # Monte Carlo row
    mc_row = tk.Frame(settings_frame, bg=WHITE)
    mc_row.pack(fill="x", pady=2)
    tk.Label(mc_row, text="Monte Carlo:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)
    _sims_var = _field(mc_row, "Simulations:", "500", 6)

    # Load firm names from JSON files
    import glob
    prop_dir = os.path.join(project_root, 'prop_firms')
    firm_names_list = []
    global _firm_name_to_id
    _firm_name_to_id = {}  # Reset mapping
    for fp in sorted(glob.glob(os.path.join(prop_dir, '*.json'))):
        try:
            with open(fp, encoding='utf-8') as f:
                fd = json.load(f)
            name = fd.get('firm_name', '?')
            fid  = fd.get('firm_id', '')
            firm_names_list.append(name)
            _firm_name_to_id[name] = fid  # Build name -> id mapping
        except:
            pass

    _mc_firm_var = tk.StringVar(value=firm_names_list[0] if firm_names_list else "FTMO")
    tk.Label(mc_row, text="Target firm:", font=("Segoe UI", 9), bg=WHITE, fg=DARK
             ).pack(side=tk.LEFT, padx=(0, 3))
    ttk.Combobox(mc_row, textvariable=_mc_firm_var,
                 values=firm_names_list if firm_names_list else ["FTMO"],
                 state="readonly", width=18).pack(side=tk.LEFT, padx=5)

    # Add Stage dropdown
    tk.Label(mc_row, text="Stage:", font=("Segoe UI", 9), bg=WHITE, fg=DARK
             ).pack(side=tk.LEFT, padx=(15, 2))
    _stage_var = tk.StringVar(value="Funded")
    ttk.Combobox(mc_row, textvariable=_stage_var,
                 values=["Evaluation", "Funded"], width=12,
                 state="readonly").pack(side=tk.LEFT, padx=5)

    # DD info label
    _val_dd_info = tk.Label(mc_row, text="", font=("Segoe UI", 8), bg=WHITE, fg="#888")
    _val_dd_info.pack(side=tk.LEFT, padx=(15, 0))

    # Common row
    com_row = tk.Frame(settings_frame, bg=WHITE)
    com_row.pack(fill="x", pady=2)
    tk.Label(com_row, text="Common:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)
    _account_var = _field(com_row, "Account size ($):", "100000", 9)
    _spread_var  = _field(com_row, "Spread:", "2.5", 5)
    _comm_var    = _field(com_row, "Commission:", "0.0", 5)

    # Auto-update settings when firm/stage changes
    def _on_val_firm_change(*_):
        firm = _mc_firm_var.get()
        stage = _stage_var.get().lower()  # "evaluation" or "funded"

        for fp in sorted(glob.glob(os.path.join(prop_dir, '*.json'))):
            try:
                with open(fp, encoding='utf-8') as f:
                    fd = json.load(f)
                if fd.get('firm_name') != firm:
                    continue

                ch = fd['challenges'][0]
                sizes = ch.get('account_sizes', [100000])

                # ── Account size ──────────────────────────────────────────────
                # Use largest size by default (same as refiner)
                if sizes and _account_var.get() not in [str(s) for s in sizes]:
                    _account_var.set(str(sizes[-1]))

                # ── DD info label ─────────────────────────────────────────────
                if stage == "funded":
                    funded = ch.get('funded', {})
                    daily_dd = funded.get('max_daily_drawdown_pct', '?')
                    total_dd = funded.get('max_total_drawdown_pct', '?')
                    dd_type  = funded.get('drawdown_type', 'static')
                else:
                    # Evaluation — use phase 1
                    phases = ch.get('phases', [{}])
                    p1 = phases[0] if phases else {}
                    daily_dd = p1.get('max_daily_drawdown_pct', '?')
                    total_dd = p1.get('max_total_drawdown_pct', '?')
                    dd_type  = p1.get('drawdown_type', 'static')

                try:
                    _val_dd_info.config(
                        text=f"Daily DD: {daily_dd}%  |  Total DD: {total_dd}%  ({dd_type})"
                    )
                except Exception:
                    pass

                # ── Risk% from trading_rules ──────────────────────────────────
                trading_rules = fd.get('trading_rules', [])
                risk_set = False

                if stage in ('evaluation', 'eval'):
                    for rule in trading_rules:
                        if rule.get('type') == 'eval_settings' and rule.get('stage') == 'evaluation':
                            rng = rule.get('parameters', {}).get('risk_pct_range') or \
                                  rule.get('parameters', {}).get('risk_per_trade_pct_range')
                            if rng:
                                _risk_var.set(str(rng[0]))  # lower bound (safer)
                                risk_set = True
                            break
                    if not risk_set:
                        _risk_var.set("1.0")

                else:  # funded
                    for rule in trading_rules:
                        if rule.get('type') == 'funded_accumulate' and rule.get('stage') == 'funded':
                            rng = rule.get('parameters', {}).get('risk_pct_range') or \
                                  rule.get('parameters', {}).get('risk_per_trade_pct_range')
                            if rng:
                                _risk_var.set(str(rng[0]))  # lower bound (safest)
                                risk_set = True
                            break
                    if not risk_set:
                        _risk_var.set("0.5")

                break  # found the right firm, done

            except Exception:
                pass

    _mc_firm_var.trace_add("write", _on_val_firm_change)
    _stage_var.trace_add("write", _on_val_firm_change)
    _on_val_firm_change()  # populate values immediately on panel load

    com_row2 = tk.Frame(settings_frame, bg=WHITE)
    com_row2.pack(fill="x", pady=2)
    tk.Label(com_row2, text="", width=16).pack(side=tk.LEFT)
    _risk_var   = _field(com_row2, "Risk %:", "1.0", 5)
    _sl_var     = _field(com_row2, "SL pips:", "150", 5)
    _pipval_var = _field(com_row2, "Pip value/lot:", "10.0", 5)

    com_row3 = tk.Frame(settings_frame, bg=WHITE)
    com_row3.pack(fill="x", pady=2)
    tk.Label(com_row3, text="", width=16).pack(side=tk.LEFT)
    _pip_size_var = _field(com_row3, "Pip size:", "0.01", 7)

    # Show firm rules reminder
    try:
        from shared.firm_rules_reminder import show_reminder_on_firm_change
        _val_reminder = [None]
        show_reminder_on_firm_change(_mc_firm_var, settings_frame, _val_reminder, _stage_var)
    except Exception as e:
        import traceback
        print("Warning: Could not initialize firm rules reminder")
        traceback.print_exc()

    # ── Buttons + progress ────────────────────────────────────────────────────
    btn_frame = tk.Frame(scroll_frame, bg=BG, pady=8)
    btn_frame.pack(fill="x", padx=20)

    _start_wf_btn = tk.Button(btn_frame, text="Run Walk-Forward Only",
                              command=lambda: _run_multi('wf'),
                              bg="#2d8a4e", fg="white", font=("Segoe UI", 9, "bold"),
                              relief=tk.FLAT, cursor="hand2", padx=12, pady=7)
    _start_wf_btn.pack(side=tk.LEFT, padx=(0, 6))

    _start_mc_btn = tk.Button(btn_frame, text="Run Monte Carlo Only",
                              command=lambda: _run_multi('mc'),
                              bg="#764ba2", fg="white", font=("Segoe UI", 9, "bold"),
                              relief=tk.FLAT, cursor="hand2", padx=12, pady=7)
    _start_mc_btn.pack(side=tk.LEFT, padx=(0, 6))

    _start_full_btn = tk.Button(btn_frame, text="Run Full Validation",
                                command=lambda: _run_multi('full'),
                                bg="#667eea", fg="white", font=("Segoe UI", 10, "bold"),
                                relief=tk.FLAT, cursor="hand2", padx=16, pady=7)
    _start_full_btn.pack(side=tk.LEFT, padx=(0, 6))

    _start_slip_btn = tk.Button(btn_frame, text="Slippage Stress Test",
                                command=lambda: _run_multi('slip'),
                                bg="#e67e00", fg="white", font=("Segoe UI", 9, "bold"),
                                relief=tk.FLAT, cursor="hand2", padx=12, pady=7)
    _start_slip_btn.pack(side=tk.LEFT, padx=(0, 6))

    _stop_btn = tk.Button(btn_frame, text="Stop",
                          command=_stop,
                          bg=RED, fg="white", font=("Segoe UI", 9, "bold"),
                          relief=tk.FLAT, cursor="hand2", padx=12, pady=7,
                          state="disabled")
    _stop_btn.pack(side=tk.LEFT)

    _progress_bar = ttk.Progressbar(scroll_frame, mode='determinate', length=400)
    _progress_bar.pack(fill="x", padx=20, pady=(4, 0))

    _status_lbl = tk.Label(scroll_frame, text="Ready",
                            font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY)
    _status_lbl.pack(pady=(2, 5))

    # ── Results frames ────────────────────────────────────────────────────────
    # Separator
    tk.Frame(scroll_frame, bg="#c0c0c0", height=1).pack(fill="x", padx=20, pady=8)

    _wf_frame      = tk.Frame(scroll_frame, bg=BG)
    _wf_frame.pack(fill="x", padx=5, pady=(5, 0))

    tk.Frame(scroll_frame, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=8)

    _mc_frame      = tk.Frame(scroll_frame, bg=BG)
    _mc_frame.pack(fill="x", padx=5)

    tk.Frame(scroll_frame, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=8)

    _slip_frame    = tk.Frame(scroll_frame, bg=BG)
    _slip_frame.pack(fill="x", padx=5)

    tk.Frame(scroll_frame, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=8)

    _verdict_frame = tk.Frame(scroll_frame, bg=BG)
    _verdict_frame.pack(fill="x", padx=5, pady=(0, 20))

    return panel


def refresh():
    global _strategies, _strategy_var
    _load_strategies()
    if _strategy_var is not None and _strategies:
        labels = [s['label'] for s in _strategies]
        if _strategy_var.get() not in labels:
            _strategy_var.set(labels[0])
        _update_strat_info()

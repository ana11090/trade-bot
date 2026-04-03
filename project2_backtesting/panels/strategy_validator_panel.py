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

# Settings vars
_train_var      = None
_test_var       = None
_windows_var    = None
_sims_var       = None
_mc_firm_var    = None
_account_var    = None
_spread_var     = None
_comm_var       = None
_risk_var       = None
_sl_var         = None
_pipval_var     = None

# Widgets
_strat_info_lbl = None
_prev_result_lbl = None
_start_wf_btn   = None
_start_mc_btn   = None
_start_full_btn = None
_stop_btn       = None
_status_lbl     = None
_progress_bar   = None
_scroll_canvas  = None
_wf_frame       = None
_mc_frame       = None
_verdict_frame  = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_strategies():
    global _strategies
    try:
        from project2_backtesting.strategy_refiner import load_strategy_list
        _strategies = load_strategy_list()
    except Exception as e:
        print(f"[validator_panel] {e}")
        _strategies = []


def _get_selected_index():
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _get_strategy_meta(idx):
    """Return (rules, exit_class, exit_params, trades, spread, commission) for strategy idx."""
    try:
        backtest_path = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
        with open(backtest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        r = data['results'][idx]
        rules         = r.get('rules', [])
        exit_class    = r.get('exit_strategy_class', 'FixedSLTP')
        exit_params   = r.get('exit_strategy_params', {'sl_pips': 150, 'tp_pips': 300})
        trades        = r.get('trades', [])
        spread        = r.get('spread_pips', 2.5)
        commission    = r.get('commission_pips', 0.0)
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
    global _strat_info_lbl, _prev_result_lbl
    if not _strat_info_lbl:
        return
    idx = _get_selected_index()
    if idx is None:
        _strat_info_lbl.configure(text="")
        return
    # Strategy stats
    for s in _strategies:
        if s['index'] == idx:
            text = (f"{s['total_trades']} trades  |  WR {s['win_rate']:.1f}%  |  "
                    f"net {s['net_total_pips']:+.0f} pips  |  PF {s['net_profit_factor']:.2f}")
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
    for btn in (_start_wf_btn, _start_mc_btn, _start_full_btn):
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

def _clear_results():
    for frame in (_wf_frame, _mc_frame, _verdict_frame):
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
        deg = w['degradation']
        if deg > -15:
            border_color = "#2d8a4e"
            deg_color    = GREEN
            check        = "✅"
        elif deg < -25:
            border_color = "#e94560"
            deg_color    = RED
            check        = "❌"
        else:
            border_color = "#996600"
            deg_color    = AMBER
            check        = "⚠️"

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
                 text=f"  {deg:+.1f}%  {check}",
                 font=("Segoe UI", 9, "bold"), bg=WHITE, fg=deg_color).pack(side=tk.LEFT)

        ins  = w['in_sample']
        outs = w['out_sample']
        tk.Label(card,
                 text=f"  IN:  {ins['count']:3d} trades  WR {ins['win_rate']*100:.1f}%  "
                      f"avg {ins['avg_pips']:+.0f} pips  PF {ins['profit_factor']:.2f}",
                 font=("Consolas", 8), bg=WHITE, fg=MIDGREY).pack(anchor="w")
        tk.Label(card,
                 text=f"  OUT: {outs['count']:3d} trades  WR {outs['win_rate']*100:.1f}%  "
                      f"avg {outs['avg_pips']:+.0f} pips  PF {outs['profit_factor']:.2f}",
                 font=("Consolas", 8), bg=WHITE, fg=DARK if outs['count'] > 0 else GREY).pack(anchor="w")

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


def _display_verdict(combined):
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
    wf_v = verdicts.get('walk_forward', 'N/A')
    mc_v = verdicts.get('monte_carlo', 'N/A')
    verdict_icons = {
        'LIKELY_REAL': '✅', 'INCONCLUSIVE': '⚠️',
        'LIKELY_OVERFITTING': '❌', 'INSUFFICIENT_DATA': '⚪',
        'ROBUST': '✅', 'MODERATE': '⚠️', 'FRAGILE': '❌', 'N/A': '—',
    }
    verdict_colors = {
        'LIKELY_REAL': GREEN, 'INCONCLUSIVE': AMBER,
        'LIKELY_OVERFITTING': RED, 'INSUFFICIENT_DATA': GREY,
        'ROBUST': GREEN, 'MODERATE': AMBER, 'FRAGILE': RED, 'N/A': GREY,
    }
    tk.Label(card,
             text=f"Walk-Forward:  {wf_v.replace('_', ' ')}  {verdict_icons.get(wf_v, '')}",
             font=("Segoe UI", 10), bg=WHITE,
             fg=verdict_colors.get(wf_v, GREY)).pack(anchor="w", pady=1)
    tk.Label(card,
             text=f"Monte Carlo:   {mc_v.replace('_', ' ')}  {verdict_icons.get(mc_v, '')}",
             font=("Segoe UI", 10), bg=WHITE,
             fg=verdict_colors.get(mc_v, GREY)).pack(anchor="w", pady=(1, 8))

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


def _run(mode):
    """mode: 'wf' | 'mc' | 'full'"""
    idx = _get_selected_index()
    if idx is None:
        messagebox.showerror("No Strategy", "Select a strategy first.")
        return

    candles_path = _get_candles_path()
    if not candles_path and mode in ('wf', 'full'):
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
        n_windows     = int(_windows_var.get())
        train_years   = int(_train_var.get())
        test_years    = int(_test_var.get())
        n_sims        = int(_sims_var.get())
        mc_firm       = _mc_firm_var.get().lower().replace(' ', '').replace('-', '').replace('_', '')
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
                run_full_validation, _save_validation,
            )

            wf_result = None
            mc_result = None

            if mode in ('wf', 'full') and candles_path:
                wf_result = walk_forward_validate(
                    rules=rules,
                    candles_path=candles_path,
                    exit_strategy_class=exit_class,
                    exit_strategy_params=exit_params,
                    n_windows=n_windows,
                    train_years=train_years,
                    test_years=test_years,
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

            if wf_result or mc_result:
                combined = combined_score(wf_result, mc_result)
                state.window.after(0, lambda c=combined: _display_verdict(c))

                # Save
                result = {
                    'strategy_index': idx,
                    'validated_at':   __import__('datetime').datetime.now().isoformat(),
                    'walk_forward':   wf_result,
                    'monte_carlo':    mc_result,
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

    threading.Thread(target=_worker, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Panel builder
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _strategy_var, _strat_info_lbl, _prev_result_lbl
    global _train_var, _test_var, _windows_var, _sims_var, _mc_firm_var
    global _account_var, _spread_var, _comm_var, _risk_var, _sl_var, _pipval_var
    global _start_wf_btn, _start_mc_btn, _start_full_btn, _stop_btn
    global _status_lbl, _progress_bar, _scroll_canvas
    global _wf_frame, _mc_frame, _verdict_frame

    _load_strategies()

    panel = tk.Frame(parent, bg=BG)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(panel, bg=WHITE, pady=16)
    hdr.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(hdr, text="✅ Strategy Validator",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(hdr, text="Prove your strategy is real — not just overfitting",
             bg=WHITE, fg=GREY, font=("Segoe UI", 11)).pack(pady=(4, 0))

    # ── Strategy selector ─────────────────────────────────────────────────────
    sel_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    sel_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(sel_frame, text="Strategy", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    if not _strategies:
        tk.Label(sel_frame,
                 text="No backtest results. Run the backtest first.",
                 font=("Segoe UI", 10, "italic"), bg=WHITE, fg=RED).pack(anchor="w")
        _strategy_var = tk.StringVar(value="")
    else:
        _strategy_var = tk.StringVar(value=_strategies[0]['label'])
        labels = [s['label'] for s in _strategies]
        dd = ttk.Combobox(sel_frame, textvariable=_strategy_var,
                          values=labels, state="readonly", width=70)
        dd.pack(anchor="w")
        dd.bind("<<ComboboxSelected>>", lambda e: _update_strat_info())

    _strat_info_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 9),
                                bg=WHITE, fg=MIDGREY)
    _strat_info_lbl.pack(anchor="w", pady=(4, 0))

    _prev_result_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 9, "italic"),
                                 bg=WHITE, fg=GREY)
    _prev_result_lbl.pack(anchor="w", pady=(2, 0))

    _update_strat_info()

    # ── Settings ──────────────────────────────────────────────────────────────
    settings_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
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

    firm_options = ["FTMO", "Topstep", "Apex", "FundedNext", "The5ers", "Atlas", "Leveraged"]
    _mc_firm_var = tk.StringVar(value="FTMO")
    tk.Label(mc_row, text="Target firm:", font=("Segoe UI", 9), bg=WHITE, fg=DARK
             ).pack(side=tk.LEFT, padx=(0, 3))
    ttk.Combobox(mc_row, textvariable=_mc_firm_var,
                 values=firm_options, state="readonly", width=14).pack(side=tk.LEFT, padx=(0, 15))

    # Common row
    com_row = tk.Frame(settings_frame, bg=WHITE)
    com_row.pack(fill="x", pady=2)
    tk.Label(com_row, text="Common:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)
    _account_var = _field(com_row, "Account size ($):", "100000", 9)
    _spread_var  = _field(com_row, "Spread:", "2.5", 5)
    _comm_var    = _field(com_row, "Commission:", "0.0", 5)

    com_row2 = tk.Frame(settings_frame, bg=WHITE)
    com_row2.pack(fill="x", pady=2)
    tk.Label(com_row2, text="", width=16).pack(side=tk.LEFT)
    _risk_var   = _field(com_row2, "Risk %:", "1.0", 5)
    _sl_var     = _field(com_row2, "SL pips:", "150", 5)
    _pipval_var = _field(com_row2, "Pip value/lot:", "10.0", 5)

    # ── Buttons + progress ────────────────────────────────────────────────────
    btn_frame = tk.Frame(panel, bg=BG, pady=8)
    btn_frame.pack(fill="x", padx=20)

    _start_wf_btn = tk.Button(btn_frame, text="Run Walk-Forward Only",
                              command=lambda: _run('wf'),
                              bg="#2d8a4e", fg="white", font=("Segoe UI", 9, "bold"),
                              relief=tk.FLAT, cursor="hand2", padx=12, pady=7)
    _start_wf_btn.pack(side=tk.LEFT, padx=(0, 6))

    _start_mc_btn = tk.Button(btn_frame, text="Run Monte Carlo Only",
                              command=lambda: _run('mc'),
                              bg="#764ba2", fg="white", font=("Segoe UI", 9, "bold"),
                              relief=tk.FLAT, cursor="hand2", padx=12, pady=7)
    _start_mc_btn.pack(side=tk.LEFT, padx=(0, 6))

    _start_full_btn = tk.Button(btn_frame, text="Run Full Validation",
                                command=lambda: _run('full'),
                                bg="#667eea", fg="white", font=("Segoe UI", 10, "bold"),
                                relief=tk.FLAT, cursor="hand2", padx=16, pady=7)
    _start_full_btn.pack(side=tk.LEFT, padx=(0, 6))

    _stop_btn = tk.Button(btn_frame, text="Stop",
                          command=_stop,
                          bg=RED, fg="white", font=("Segoe UI", 9, "bold"),
                          relief=tk.FLAT, cursor="hand2", padx=12, pady=7,
                          state="disabled")
    _stop_btn.pack(side=tk.LEFT)

    _progress_bar = ttk.Progressbar(panel, mode='determinate', length=400)
    _progress_bar.pack(fill="x", padx=20, pady=(4, 0))

    _status_lbl = tk.Label(panel, text="Ready",
                            font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY)
    _status_lbl.pack(pady=(2, 5))

    # ── Scrollable results area ───────────────────────────────────────────────
    _scroll_canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    vscroll = tk.Scrollbar(panel, orient="vertical", command=_scroll_canvas.yview)
    scroll_frame = tk.Frame(_scroll_canvas, bg=BG)

    scroll_frame.bind("<Configure>",
                      lambda e: _scroll_canvas.configure(
                          scrollregion=_scroll_canvas.bbox("all")))
    cwin = _scroll_canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    _scroll_canvas.configure(yscrollcommand=vscroll.set)
    _scroll_canvas.pack(side="left", fill="both", expand=True, padx=(20, 0))
    vscroll.pack(side="right", fill="y", padx=(0, 20))

    def _mw(e): _scroll_canvas.yview_scroll(int(-1*(e.delta/120)), "units")
    _scroll_canvas.bind_all("<MouseWheel>", _mw)
    _scroll_canvas.bind_all("<Button-4>", lambda e: _scroll_canvas.yview_scroll(-3, "units"))
    _scroll_canvas.bind_all("<Button-5>", lambda e: _scroll_canvas.yview_scroll(3, "units"))
    _scroll_canvas.bind("<Configure>",
                        lambda e: _scroll_canvas.itemconfig(cwin, width=e.width))

    sf = scroll_frame

    _wf_frame      = tk.Frame(sf, bg=BG)
    _wf_frame.pack(fill="x", padx=5, pady=(5, 0))

    tk.Frame(sf, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=8)

    _mc_frame      = tk.Frame(sf, bg=BG)
    _mc_frame.pack(fill="x", padx=5)

    tk.Frame(sf, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=8)

    _verdict_frame = tk.Frame(sf, bg=BG)
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

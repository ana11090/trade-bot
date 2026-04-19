"""
Prop Firm Test Panel — Test backtested strategies against prop firm challenge rules.

Pick a strategy from backtest results, pick which firms/challenges to test,
and see pass rates, expected income, and which firm is best for that strategy.
Also shows the full trade history for the selected strategy with export.
"""

# WHY (Phase 33 Fix 6): Settings defaults were XAUUSD-hardcoded (100k account,
#      150 SL, 10.0 pip_value, 2.5 spread). Load from saved config at module
#      import so non-XAUUSD users see correct defaults in the panel.
# CHANGED: April 2026 — Phase 33 Fix 6 — config-loaded settings defaults
#          (Ref: trade_bot_audit_round2_partC.pdf HIGH item #85 pg.30)
_pft_account_size = 100000
_pft_sl_pips = 150.0
_pft_pip_value = 10.0
_pft_spread_pips = 2.5
try:
    from project2_backtesting.panels.configuration import load_config as _pft_load_config
    _pft_cfg = _pft_load_config()
    _pft_account_size = int(_pft_cfg.get('account_size', 100000))
    _pft_sl_pips = float(_pft_cfg.get('default_sl_pips', 150.0))
    _pft_pip_value = float(_pft_cfg.get('pip_value', 10.0))
    _pft_spread_pips = float(_pft_cfg.get('spread_pips', 2.5))
except Exception:
    pass  # fallback to XAUUSD defaults

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import threading

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

import state

# Design tokens
BG      = "#f0f2f5"
WHITE   = "white"
GREEN   = "#2d8a4e"
RED     = "#e94560"
AMBER   = "#996600"
DARK    = "#1a1a2a"
GREY    = "#666666"
MIDGREY = "#555566"

# ─────────────────────────────────────────────────────────────────────────────
# Validation badge helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_validation_badge(strategy_index):
    """Get validation status for display."""
    try:
        from project2_backtesting.strategy_validator import get_validation_for_strategy
        result = get_validation_for_strategy(strategy_index)
        if result is None:
            return "⚪ Not validated", "#999999"
        combined = result.get('combined', {})
        grade = combined.get('grade', '?')
        score = combined.get('confidence_score', 0)
        if grade in ('A', 'B'):
            return f"✅ Validated: Grade {grade} ({score}/100)", "#2d8a4e"
        elif grade == 'C':
            return f"⚠️ Validated: Grade {grade} ({score}/100)", "#996600"
        else:
            return f"❌ Validated: Grade {grade} ({score}/100)", "#e94560"
    except Exception:
        return "⚪ Not validated", "#999999"


# Module-level state
_strategy_var      = None
_validation_label  = None
_strategies        = []        # list of dicts from load_strategy_list()
_firm_data         = []        # list from load_available_firms()
_firm_challenge_vars = {}      # (firm_id, challenge_id) -> BooleanVar
_firm_frames       = {}        # firm_id -> expandable sub-frame
_account_size_var  = None
_risk_var          = None
_sl_pips_var       = None
_pip_val_var       = None
_daily_dd_var      = None
_spread_var        = None
_commission_var    = None
_run_btn           = None
_progress_bar      = None
_status_label      = None
_results_frame     = None
_trades_frame      = None
_strat_info_label  = None
_current_trades    = []        # trades for the currently selected strategy


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_strategies():
    global _strategies
    try:
        from project2_backtesting.prop_firm_tester import load_strategy_list
        result = load_strategy_list()
        _strategies = result if result else []
    except Exception as e:
        print(f"[prop_firm_test] Error loading strategies: {e}")
        _strategies = []


def _load_firms():
    global _firm_data
    try:
        from project2_backtesting.prop_firm_tester import load_available_firms
        _firm_data = load_available_firms()
    except Exception as e:
        print(f"[prop_firm_test] Error loading firms: {e}")
        _firm_data = []


def _get_unique_firms():
    """Return unique (firm_id, firm_name) pairs preserving order."""
    seen = {}
    for fc in _firm_data:
        if fc['firm_id'] not in seen:
            seen[fc['firm_id']] = fc['firm_name']
    return list(seen.items())


def _get_firm_challenges(firm_id):
    """Return all challenge dicts for a given firm_id."""
    return [fc for fc in _firm_data if fc['firm_id'] == firm_id]


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_selected_index():
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _on_strategy_select(event=None):
    global _strat_info_label, _spread_var, _commission_var, _current_trades, _validation_label
    if not _strat_info_label or not _strategies:
        return
    idx = _get_selected_index()
    if _validation_label and idx is not None:
        badge_text, badge_color = _get_validation_badge(idx)
        _validation_label.configure(text=badge_text, fg=badge_color)
    if idx is None:
        _strat_info_label.configure(text="", fg=GREY)
        return

    # WHY: idx can be int (backtest matrix) or string ('saved_21', 'optimizer_latest').
    #      Old code did _strategies[idx] which crashes on strings.
    # CHANGED: April 2026 — find strategy by index key
    s = None
    for _s in _strategies:
        if _s.get('index') == idx:
            s = _s
            break
    if s is None and isinstance(idx, int) and 0 <= idx < len(_strategies):
        s = _strategies[idx]
    if s is None:
        _strat_info_label.configure(text=f"Strategy {idx} not found", fg=RED)
        return

    has_trades = s.get('has_trades', False)

    # Update spread/commission from backtest data (only for backtest matrix indices)
    if isinstance(idx, int):
        try:
            from project2_backtesting.prop_firm_tester import load_strategy_list
            from project2_backtesting.prop_firm_tester import BACKTEST_MATRIX_PATH
            import json
            with open(BACKTEST_MATRIX_PATH, encoding='utf-8') as f:
                data = json.load(f)
            r = data['results'][idx]
            if _spread_var:
                _spread_var.set(str(r.get('spread_pips', 2.5)))
            if _commission_var:
                _commission_var.set(str(r.get('commission_pips', 0.0)))
        except Exception:
            pass

    if has_trades:
        text = (f"{s['total_trades']} trades  |  WR {s['win_rate']:.1f}%  |  "
                f"net {s['net_total_pips']:+.0f} pips  |  PF {s['net_profit_factor']:.2f}")
        _strat_info_label.configure(text=text, fg=MIDGREY)
        # Load trades and display trade history
        try:
            from project2_backtesting.prop_firm_tester import load_strategy_trades
            trades = load_strategy_trades(idx)
            _current_trades.clear()
            if trades:
                _current_trades.extend(trades)
            _display_trade_history(_current_trades)
        except Exception as e:
            print(f"[prop_firm_test] Could not load trades: {e}")
    else:
        _strat_info_label.configure(
            text="⚠ Trade details missing. Re-run the backtest to include them.",
            fg=RED
        )
        _current_trades.clear()
        _display_trade_history([])


def _select_all_challenges():
    for var in _firm_challenge_vars.values():
        var.set(True)


def _clear_all_challenges():
    for var in _firm_challenge_vars.values():
        var.set(False)


def _closest_size(available, requested):
    if not available:
        return requested
    return min(available, key=lambda s: abs(s - requested))


# ─────────────────────────────────────────────────────────────────────────────
# Run test
# ─────────────────────────────────────────────────────────────────────────────

def _run_test():
    global _run_btn, _progress_bar, _status_label

    idx = _get_selected_index()
    if idx is None:
        messagebox.showerror("No Strategy", "Please select a strategy first.")
        return

    s = _strategies[idx]
    if not s.get('has_trades', False):
        messagebox.showerror(
            "No Trade Data",
            "The selected strategy has no individual trade data.\n\n"
            "Re-run the backtest to include trades in backtest_matrix.json."
        )
        return

    # Validation check
    badge_text, _ = _get_validation_badge(idx)
    if "Not validated" in badge_text:
        if not messagebox.askyesno("Not Validated",
                "This strategy hasn't been validated.\n\n"
                "Run Strategy Validator first to check if the edge is real.\n\n"
                "Proceed anyway?"):
            return
    elif "❌" in badge_text:
        if not messagebox.askyesno("Low Confidence",
                f"Validation result: {badge_text}\n\n"
                "This strategy may be overfitting.\n\nProceed anyway?"):
            return

    # Collect selected (firm_id, challenge_id) pairs
    selected = [(fid, cid) for (fid, cid), var in _firm_challenge_vars.items() if var.get()]
    if not selected:
        messagebox.showerror("No Challenges", "Select at least one challenge to test.")
        return

    try:
        account_size = int(_account_size_var.get())
        risk_pct     = float(_risk_var.get())
        sl_pips      = float(_sl_pips_var.get())
        pip_val      = float(_pip_val_var.get())
        daily_dd_safety = float(_daily_dd_var.get())
    except ValueError:
        messagebox.showerror("Invalid Settings", "Check that all settings are valid numbers.")
        return

    _run_btn.configure(state="disabled", text="Running...")
    _progress_bar['value'] = 0
    _status_label.configure(text="Loading trades...", fg=GREY)

    def _worker():
        try:
            from project2_backtesting.prop_firm_tester import (
                load_strategy_trades, run_multi_firm_test
            )

            trades = load_strategy_trades(idx)
            if not trades:
                state.window.after(0, lambda: _status_label.configure(
                    text="No trades found for selected strategy.", fg=RED))
                return

            # Build firm_challenges from selected checkboxes
            firm_challenges = []
            fc_lookup = {(fc['firm_id'], fc['challenge_id']): fc for fc in _firm_data}
            for fid, cid in selected:
                fc = fc_lookup.get((fid, cid))
                if fc:
                    closest = _closest_size(fc['account_sizes'], account_size)
                    firm_challenges.append({
                        'firm_id':        fid,
                        'firm_name':      fc['firm_name'],
                        'challenge_id':   cid,
                        'challenge_name': fc['challenge_name'],
                        'account_size':   closest,
                    })

            total = len(firm_challenges)
            state.window.after(0, lambda: _status_label.configure(
                text=f"Testing {len(trades)} trades against {total} challenges...", fg=GREY))

            def _progress(cur, tot, label):
                pct = int(cur / max(tot, 1) * 100)
                state.window.after(0, lambda: _progress_bar.configure(value=pct))
                state.window.after(0, lambda: _status_label.configure(
                    text=f"[{cur}/{tot}] {label}", fg=GREY))

            results = run_multi_firm_test(
                trades=trades,
                firm_challenges=firm_challenges,
                risk_per_trade_pct=risk_pct,
                default_sl_pips=sl_pips,
                pip_value_per_lot=pip_val,
                daily_dd_safety_pct=daily_dd_safety,
                progress_callback=_progress,
            )

            state.window.after(0, lambda: _display_results(results, s['label']))
            state.window.after(0, lambda: _progress_bar.configure(value=100))
            state.window.after(0, lambda: _status_label.configure(
                text=f"Done — {len(results)} results, sorted by Expected ROI", fg=GREEN))

        except Exception as e:
            import traceback
            print(f"[prop_firm_test] Error:\n{traceback.format_exc()}")
            state.window.after(0, lambda: _status_label.configure(text=f"Error: {e}", fg=RED))
        finally:
            state.window.after(0, lambda: _run_btn.configure(
                state="normal", text="Run Prop Firm Test"))

    threading.Thread(target=_worker, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Results display
# ─────────────────────────────────────────────────────────────────────────────

def _display_results(results, strategy_label):
    global _results_frame

    for widget in _results_frame.winfo_children():
        widget.destroy()

    if not results:
        tk.Label(_results_frame, text="No results — check settings and try again.",
                font=("Segoe UI", 10, "italic"), bg=BG, fg=GREY).pack(pady=20)
        return

    # Strategy banner
    banner = tk.Frame(_results_frame, bg="#e8f5e9", padx=15, pady=8)
    banner.pack(fill="x", padx=5, pady=(0, 6))
    tk.Label(banner, text=f"Strategy: {strategy_label}",
             font=("Segoe UI", 9, "bold"), bg="#e8f5e9", fg="#2e7d32").pack(anchor="w")

    # Table header
    header_frame = tk.Frame(_results_frame, bg="#f5f5f5", padx=10, pady=5)
    header_frame.pack(fill="x", padx=5)

    col_defs = [
        ("#",          3), ("Firm",      16), ("Challenge", 18),
        ("Size",       8), ("Pass%",      6), ("Sims",       5),
        ("Days (min/avg/max)", 16), ("Max DD%",    8), ("Monthly $", 10),
        ("ROI%",       7), ("Fail Reasons", 25),
    ]
    for text, width in col_defs:
        tk.Label(header_frame, text=text, font=("Segoe UI", 8, "bold"),
                bg="#f5f5f5", fg=GREY, width=width, anchor="w").pack(side=tk.LEFT, padx=1)

    # Rows
    for i, r in enumerate(results, 1):
        pass_rate = r['pass_rate'] or 0
        roi       = r.get('expected_roi_pct') or 0
        monthly   = r.get('funded_avg_monthly') or 0

        if pass_rate >= 0.60:
            row_bg = "#f0fdf4"
            rate_color = GREEN
        elif pass_rate >= 0.40:
            row_bg = "#fffbeb"
            rate_color = AMBER
        else:
            row_bg = "#fef2f2"
            rate_color = RED

        row = tk.Frame(_results_frame, bg=row_bg, padx=10, pady=4)
        row.pack(fill="x", padx=5)

        roi_color = GREEN if roi > 0 else (RED if roi < 0 else GREY)

        # Fail reasons summary
        fail_reasons = r.get('fail_reasons') or {}
        reasons_str = "  ".join(
            f"{k}:{v}" for k, v in sorted(fail_reasons.items(), key=lambda x: -x[1])
        ) if fail_reasons else "—"

        vals = [
            (str(i),                    3,  GREY,      "Segoe UI"),
            (r['firm_name'],            16, DARK,      "Segoe UI"),
            (r['challenge_name'],       18, DARK,      "Segoe UI"),
            (f"${r['account_size']:,}", 8,  GREY,      "Segoe UI"),
            (f"{pass_rate*100:.0f}%",   6,  rate_color,"Segoe UI"),
            (str(r['num_simulations']), 5,  GREY,      "Segoe UI"),
            (f"{r.get('min_days_to_pass', 0):.0f} / {r['avg_days_to_pass'] or 0:.0f} / {r.get('max_days_to_pass', 0):.0f}d", 16, DARK, "Segoe UI"),
            (f"{(r['avg_max_dd_pct'] or 0)*100:.1f}%", 8, DARK, "Segoe UI"),
            (f"${monthly:,.0f}" if monthly else "—", 10, GREEN if monthly else GREY, "Segoe UI"),
            (f"{roi:+.0f}%" if roi else "—", 7, roi_color, "Segoe UI"),
        ]
        for text, width, color, font_name in vals:
            tk.Label(row, text=text, font=(font_name, 8, "bold" if color in (GREEN, RED, rate_color) else "normal"),
                    bg=row_bg, fg=color, width=width, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=reasons_str, font=("Consolas", 7),
                bg=row_bg, fg=GREY, anchor="w").pack(side=tk.LEFT, padx=(4, 0))


# ─────────────────────────────────────────────────────────────────────────────
# Trade history display
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_hold(candles_held, minutes_per_candle=60):
    """
    Convert candles held to human-readable duration.

    WHY (Phase 33 Fix 4): Old code hardcoded 60 minutes (H1 timeframe).
         For strategies on D1, 15M, etc., hold times were wrong. Now caller
         passes the correct minutes_per_candle for the strategy's timeframe.
    CHANGED: April 2026 — Phase 33 Fix 4 — timeframe-aware hold formatting
             (Ref: trade_bot_audit_round2_partC.pdf HIGH item #84 pg.30)
    """
    if not candles_held:
        return "—"
    mins = candles_held * minutes_per_candle
    if mins >= 60:
        h = mins // 60
        m = mins % 60
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{mins}m"


def _display_trade_history(trades):
    global _trades_frame
    if _trades_frame is None:
        return

    for widget in _trades_frame.winfo_children():
        widget.destroy()

    if not trades:
        tk.Label(_trades_frame,
                text="No trade data for this strategy. Re-run the backtest.",
                font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=10)
        return

    # Trade table header
    th = tk.Frame(_trades_frame, bg="#f5f5f5", padx=8, pady=4)
    th.pack(fill="x", padx=5)

    trade_cols = [
        ("#", 3), ("Entry", 17), ("Exit", 17), ("Dir", 5),
        ("Entry$", 7), ("Exit$", 7), ("Gross", 7), ("Spread", 7),
        ("Net", 7), ("Hold", 8), ("Reason", 14),
    ]
    for text, width in trade_cols:
        tk.Label(th, text=text, font=("Segoe UI", 7, "bold"),
                bg="#f5f5f5", fg=GREY, width=width, anchor="w").pack(side=tk.LEFT, padx=1)

    winners = losers = 0
    total_net = 0.0
    total_hold = 0

    for i, t in enumerate(trades, 1):
        net = t.get('net_pips', 0)
        total_net += net
        candles = t.get('candles_held', 0)
        total_hold += candles or 0
        if net > 0:
            winners += 1
            row_bg = "#f0fdf4"
        else:
            losers += 1
            row_bg = "#fef2f2"

        row = tk.Frame(_trades_frame, bg=row_bg, padx=8, pady=2)
        row.pack(fill="x", padx=5)

        net_color  = GREEN if net > 0 else RED
        entry_time = str(t.get('entry_time', ''))[:16]
        exit_time  = str(t.get('exit_time',  ''))[:16]
        direction  = t.get('direction', '')
        # WHY (Phase 33 Fix 5): Old code colored BUY=green, SELL=red — misleading
        #      because direction is not the same as profit outcome. Use DARK
        #      (neutral) so users don't confuse trade direction with P&L.
        # CHANGED: April 2026 — Phase 33 Fix 5 — neutral direction color
        #          (Ref: trade_bot_audit_round2_partC.pdf HIGH item #86 pg.30)
        dir_color = DARK

        tk.Label(row, text=str(i), font=("Segoe UI", 7), bg=row_bg, fg=GREY, width=3, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=entry_time, font=("Consolas", 7), bg=row_bg, fg=DARK, width=17, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=exit_time,  font=("Consolas", 7), bg=row_bg, fg=DARK, width=17, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=direction,  font=("Segoe UI", 7, "bold"), bg=row_bg, fg=dir_color, width=5, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=f"{t.get('entry_price',0):.2f}", font=("Consolas", 7), bg=row_bg, fg=DARK, width=7, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=f"{t.get('exit_price', 0):.2f}", font=("Consolas", 7), bg=row_bg, fg=DARK, width=7, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=f"{t.get('pnl_pips',0):+.1f}", font=("Consolas", 7), bg=row_bg, fg=MIDGREY, width=7, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=f"{t.get('cost_pips',0):.1f}", font=("Consolas", 7), bg=row_bg, fg=GREY, width=7, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=f"{net:+.1f}", font=("Consolas", 7, "bold"), bg=row_bg, fg=net_color, width=7, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=_fmt_hold(candles), font=("Segoe UI", 7), bg=row_bg, fg=GREY, width=8, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=t.get('exit_reason', ''), font=("Segoe UI", 7), bg=row_bg, fg=MIDGREY, width=14, anchor="w").pack(side=tk.LEFT, padx=1)

    # Summary footer
    total = len(trades)
    wr = winners / total * 100 if total else 0
    avg_hold = _fmt_hold(total_hold // total) if total else "—"

    footer = tk.Frame(_trades_frame, bg="#e8f4f8", padx=8, pady=6)
    footer.pack(fill="x", padx=5, pady=(4, 0))
    tk.Label(footer,
             text=f"Total: {total} trades  |  Winners: {winners}  Losers: {losers}  "
                  f"WR: {wr:.1f}%  |  Net pips: {total_net:+.1f}  |  Avg hold: {avg_hold}",
             font=("Segoe UI", 9, "bold"), bg="#e8f4f8", fg=DARK).pack(anchor="w")


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

def _export_trades():
    if not _current_trades:
        messagebox.showinfo("No Trades", "Load a strategy with trade data first.")
        return

    try:
        account_size = int(_account_size_var.get()) if _account_size_var else None
    except ValueError:
        account_size = None

    idx = _get_selected_index()
    label = _strategies[idx]['label'].replace(' × ', '_').replace(' ', '_') if idx is not None else 'trades'
    default_name = f"trades_{label}.csv"

    filepath = filedialog.asksaveasfilename(
        title="Export Trades to CSV",
        defaultextension=".csv",
        initialfile=default_name,
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )
    if not filepath:
        return

    try:
        from project2_backtesting.prop_firm_tester import export_trades_csv
        export_trades_csv(_current_trades, filepath, account_size=account_size)
        messagebox.showinfo("Export Complete",
                            f"Exported {len(_current_trades)} trades to:\n{filepath}")
    except Exception as e:
        messagebox.showerror("Export Error", str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Panel builder
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _strategy_var, _firm_challenge_vars, _account_size_var, _risk_var
    global _sl_pips_var, _pip_val_var, _daily_dd_var, _spread_var, _commission_var
    global _run_btn, _progress_bar, _status_label, _results_frame, _trades_frame
    global _strat_info_label, _validation_label

    _load_strategies()
    _load_firms()

    panel = tk.Frame(parent, bg=BG)

    # ── Header ────────────────────────────────────────────────────────────────
    header = tk.Frame(panel, bg=WHITE, pady=20)
    header.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(header, text="🏦 Prop Firm Challenge Test",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(header, text="Test your backtested strategy against prop firm challenge rules",
             bg=WHITE, fg=GREY, font=("Segoe UI", 11)).pack(pady=(5, 0))

    # ── Strategy selection ────────────────────────────────────────────────────
    strat_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=15)
    strat_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(strat_frame, text="Strategy", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    if not _strategies:
        tk.Label(strat_frame,
                 text="No backtest results found. Run the backtest first (Project 2 → Run Backtest).",
                 font=("Segoe UI", 10, "italic"), bg=WHITE, fg=RED).pack(anchor="w")
        _strategy_var = tk.StringVar(value="")
    else:
        _strategy_var = tk.StringVar(value=_strategies[0]['label'])
        labels = [s['label'] for s in _strategies]
        dropdown = ttk.Combobox(strat_frame, textvariable=_strategy_var,
                                values=labels, state="readonly", width=100)
        dropdown.pack(anchor="w", fill="x")
        dropdown.bind("<<ComboboxSelected>>", _on_strategy_select)

        # Refresh button
        def _refresh_strats():
            global _strategies, _strategy_var
            from project2_backtesting.prop_firm_tester import load_strategy_list
            # Invalidate cache
            import project2_backtesting.prop_firm_tester as _pft_mod
            _pft_mod._cache_mtime = 0
            _pft_mod._strategy_list_cache = None
            _strategies = load_strategy_list() or []
            new_labels = [s['label'] for s in _strategies]
            dropdown['values'] = new_labels
            if new_labels:
                _strategy_var.set(new_labels[0])
            print(f"[PROP FIRM] Refreshed — {len(_strategies)} strategies loaded")

        tk.Button(strat_frame, text="🔄 Refresh", font=("Segoe UI", 8),
                  bg="#3498db", fg="white", relief=tk.FLAT, padx=8,
                  command=_refresh_strats).pack(anchor="w", pady=(4, 0))

    _strat_info_label = tk.Label(strat_frame, text="", font=("Segoe UI", 9),
                                  bg=WHITE, fg=MIDGREY)
    _strat_info_label.pack(anchor="w", pady=(4, 0))

    _validation_label = tk.Label(strat_frame, text="⚪ Not validated",
                                  font=("Segoe UI", 9, "italic"), bg=WHITE, fg="#999999")
    _validation_label.pack(anchor="w", padx=0)

    # ── Settings ──────────────────────────────────────────────────────────────
    settings_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    settings_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(settings_frame, text="Settings", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 8))

    def _field(parent, label, default, width=9):
        var = tk.StringVar(value=default)
        tk.Label(parent, text=label, font=("Segoe UI", 9), bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(0, 3))
        tk.Entry(parent, textvariable=var, width=width).pack(side=tk.LEFT, padx=(0, 15))
        return var

    row1 = tk.Frame(settings_frame, bg=WHITE)
    row1.pack(fill="x", pady=(0, 4))
    _account_size_var = _field(row1, "Account size ($):", str(_pft_account_size), 10)
    _risk_var         = _field(row1, "Risk/trade (%):", "1.0", 6)
    _sl_pips_var      = _field(row1, "Default SL (pips):", str(int(_pft_sl_pips)), 6)

    row2 = tk.Frame(settings_frame, bg=WHITE)
    row2.pack(fill="x", pady=(0, 4))
    _pip_val_var   = _field(row2, "Pip value/lot ($):", str(_pft_pip_value), 6)
    _daily_dd_var  = _field(row2, "Daily DD safety (%):", "80", 5)
    _spread_var    = _field(row2, "Spread (pips):", str(_pft_spread_pips), 5)
    _commission_var = _field(row2, "Commission (pips):", "0.0", 5)

    # ── Firm / Challenge selection ─────────────────────────────────────────────
    firms_outer = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    firms_outer.pack(fill="x", padx=20, pady=(0, 5))

    hdr_row = tk.Frame(firms_outer, bg=WHITE)
    hdr_row.pack(fill="x", pady=(0, 8))
    tk.Label(hdr_row, text="Prop Firms & Challenges", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(side=tk.LEFT)
    tk.Button(hdr_row, text="Select All", command=_select_all_challenges,
              bg=GREEN, fg="white", font=("Segoe UI", 8, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3).pack(side=tk.LEFT, padx=(15, 4))
    tk.Button(hdr_row, text="Clear", command=_clear_all_challenges,
              bg=GREY, fg="white", font=("Segoe UI", 8, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3).pack(side=tk.LEFT)

    _firm_challenge_vars.clear()
    unique_firms = _get_unique_firms()

    if not unique_firms:
        tk.Label(firms_outer, text="No prop firms loaded.",
                 font=("Segoe UI", 9, "italic"), bg=WHITE, fg=RED).pack(anchor="w")
    else:
        for firm_id, firm_name in unique_firms:
            challenges = _get_firm_challenges(firm_id)

            firm_box = tk.Frame(firms_outer, bg="#fafafa", pady=4,
                                highlightbackground="#d0d0d0", highlightthickness=1)
            firm_box.pack(fill="x", pady=3)

            # Check if firm has special trading rules
            import json
            import glob
            prop_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prop_firms')
            rule_count = 0
            firm_data = None
            for fp in glob.glob(os.path.join(prop_dir, '*.json')):
                try:
                    with open(fp, encoding='utf-8') as f:
                        fd = json.load(f)
                    if fd.get('firm_id') == firm_id or fd.get('firm_name') == firm_name:
                        rule_count = len(fd.get('trading_rules', []))
                        firm_data = fd
                        break
                except Exception:
                    pass

            name_text = f"  {firm_name}"
            if rule_count > 0:
                name_text += f"  ⚠️ {rule_count} special rules"

            firm_label = tk.Label(firm_box, text=name_text,
                                  font=("Segoe UI", 10, "bold"), bg="#fafafa", fg=DARK)
            firm_label.pack(anchor="w", padx=8, pady=(4, 2))

            # Add tooltip showing rule details
            if rule_count > 0 and firm_data:
                from shared.tooltip import add_tooltip
                rules = firm_data.get('trading_rules', [])
                tooltip_text = f"⚠️ {firm_name} — Special Trading Rules:\n\n"
                for r in rules:
                    tooltip_text += f"  • {r['name']}\n"
                    tooltip_text += f"    {r['description']}\n\n"
                add_tooltip(firm_label, tooltip_text, wraplength=450)

            for fc in challenges:
                challenge_id = fc['challenge_id']
                key = (firm_id, challenge_id)
                var = tk.BooleanVar(value=True)
                _firm_challenge_vars[key] = var

                ch_row = tk.Frame(firm_box, bg="#fafafa")
                ch_row.pack(fill="x", padx=20, pady=1)

                cb = tk.Checkbutton(ch_row, text=fc['challenge_name'], variable=var,
                                    bg="#fafafa", font=("Segoe UI", 9), anchor="w")
                cb.pack(side=tk.LEFT)

                # Key rules summary
                sizes_str = "/".join(f"${s:,}" for s in fc['account_sizes'][:3])
                rules_text = f"  sizes: {sizes_str}"
                tk.Label(ch_row, text=rules_text,
                         font=("Segoe UI", 8), bg="#fafafa", fg=GREY).pack(side=tk.LEFT, padx=(5, 0))

    # ── Run button + progress ─────────────────────────────────────────────────
    run_frame = tk.Frame(panel, bg=BG, pady=10)
    run_frame.pack(fill="x", padx=20)

    _run_btn = tk.Button(run_frame, text="Run Prop Firm Test",
                         command=_run_test,
                         bg="#667eea", fg="white",
                         font=("Segoe UI", 11, "bold"),
                         relief=tk.FLAT, cursor="hand2", padx=25, pady=10)
    _run_btn.pack(side=tk.LEFT, padx=(0, 15))

    _progress_bar = ttk.Progressbar(run_frame, mode='determinate', length=350)
    _progress_bar.pack(side=tk.LEFT, pady=5)

    _status_label = tk.Label(panel, text="Ready",
                              font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY)
    _status_label.pack(pady=(0, 5))

    # ── Results section ───────────────────────────────────────────────────────
    results_header = tk.Frame(panel, bg=WHITE, padx=20, pady=8)
    results_header.pack(fill="x", padx=20, pady=(5, 0))
    tk.Label(results_header, text="Results (sorted by Expected ROI)",
             font=("Segoe UI", 11, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT)

    # ── Trade history section ─────────────────────────────────────────────────
    trades_header = tk.Frame(panel, bg=WHITE, padx=20, pady=8)
    trades_header.pack(fill="x", padx=20, pady=(8, 0))

    trades_title_row = tk.Frame(trades_header, bg=WHITE)
    trades_title_row.pack(fill="x")
    tk.Label(trades_title_row, text="📋 Trade History (selected strategy)",
             font=("Segoe UI", 11, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT)

    tk.Button(trades_title_row, text="📥 Export Trades to CSV",
              command=_export_trades,
              bg=GREEN, fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(side=tk.RIGHT)

    # ── Single scrollable canvas for Results + Trade History ──────────────────
    canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    scrollbar = tk.Scrollbar(panel, orient="vertical", command=canvas.yview)

    scroll_frame = tk.Frame(canvas, bg=BG)
    scroll_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    content_window_id = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True, padx=(20, 0))
    scrollbar.pack(side="right", fill="y", padx=(0, 20))

    # Safe mousewheel binding — doesn't break other canvases
    def _on_enter(event):
        canvas.bind("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        # Linux
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        canvas.unbind("<MouseWheel>")
        canvas.unbind("<Button-4>")
        canvas.unbind("<Button-5>")

    canvas.bind("<Enter>", _on_enter)
    canvas.bind("<Leave>", _on_leave)

    def _on_canvas_resize(event):
        canvas.itemconfig(content_window_id, width=event.width)

    canvas.bind("<Configure>", _on_canvas_resize)

    # Results and trades live inside the shared scroll_frame
    _results_frame = tk.Frame(scroll_frame, bg=BG)
    _results_frame.pack(fill="x", pady=(0, 10))

    tk.Frame(scroll_frame, bg="#d0d0d0", height=1).pack(fill="x", padx=5, pady=8)

    _trades_frame = tk.Frame(scroll_frame, bg=BG)
    _trades_frame.pack(fill="x")

    # Initial load (also sets validation badge)
    _on_strategy_select()

    return panel


def refresh():
    """Reload strategy list when panel becomes active."""
    global _strategies, _strategy_var, _strat_info_label
    _load_strategies()
    if _strategy_var is not None and _strategies:
        labels = [s['label'] for s in _strategies]
        if _strategy_var.get() not in labels:
            _strategy_var.set(labels[0])
        _on_strategy_select()

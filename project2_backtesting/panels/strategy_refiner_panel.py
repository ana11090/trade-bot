"""
Strategy Refiner Panel — interactive trade filtering + deep optimizer.

Mode 1: Instant filter impact preview. Every slider/checkbox change shows
        how many trades are removed and whether the result improves.

Mode 2: Deep optimizer that searches filter combinations and scores them.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import csv
import threading
import time

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

# ── Module-level state ────────────────────────────────────────────────────────
_base_trades     = []        # enriched trades for selected strategy
_filtered_trades = []        # trades after current filters
_strategy_var    = None
_strategies      = []

# Filter vars
_min_hold_var    = None
_max_hold_var    = None
_max_per_day_var = None
_min_pips_var    = None
_cooldown_var    = None
_session_vars    = {}        # "Asian/London/New York" -> BooleanVar
_day_vars        = {}        # "Mon".."Fri" -> BooleanVar
_custom_filters  = []        # list of {feature, operator, value}

# Widgets
_strat_info_lbl   = None
_base_stats_frame = None
_impact_labels    = {}       # filter_name -> tk.Label for impact text
_results_card     = None
_trade_list_frame = None
_monthly_chart_canvas = None
_monthly_tooltip      = None
_dd_label             = None
_breach_label         = None
_opt_progress_frame = None
_opt_results_frame  = None
_opt_live_labels    = {}
_opt_status_lbl     = None
_opt_start_btn      = None
_opt_stop_btn       = None
_scroll_canvas      = None

_update_pending = False   # debounce flag


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
        print(f"[refiner_panel] {e}")
        _strategies = []


def _get_selected_index():
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    if '───' in val:
        return None  # separator, not a real selection
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _load_selected_strategy():
    global _base_trades, _filtered_trades
    idx = _get_selected_index()
    if idx is None:
        return
    try:
        from project2_backtesting.strategy_refiner import (
            load_trades_from_matrix, enrich_trades
        )
        raw = load_trades_from_matrix(idx)
        if not raw:
            messagebox.showwarning(
                "No Trades",
                "This strategy has no trade data.\n\nRe-run the backtest first."
            )
            return
        _base_trades = enrich_trades(list(raw))
        _filtered_trades = list(_base_trades)
        _update_strat_info()
        _schedule_update()
    except Exception as e:
        import traceback; traceback.print_exc()
        messagebox.showerror("Load Error", str(e))


def _update_strat_info():
    global _strat_info_lbl
    if not _strat_info_lbl or not _base_trades:
        return
    from project2_backtesting.strategy_refiner import compute_stats_summary
    s = compute_stats_summary(_base_trades)
    text = (f"{s['count']} trades  |  WR {s['win_rate']*100:.1f}%  |  "
            f"avg {s['avg_pips']:+.1f} pips  |  {s['trades_per_day']:.1f}/day  |  "
            f"hold {s['avg_hold_minutes']:.0f}m  |  max DD {s['max_dd_pips']:.0f} pips")
    _strat_info_lbl.configure(text=text, fg=MIDGREY)


def _get_current_filters():
    """Build the filters dict from current UI values."""
    filters = {}

    try:
        v = float(_min_hold_var.get()) if _min_hold_var else 0
        if v > 0:
            filters['min_hold_minutes'] = v
    except Exception:
        pass

    try:
        v = float(_max_hold_var.get()) if _max_hold_var else 0
        if v > 0:
            filters['max_hold_minutes'] = v
    except Exception:
        pass

    try:
        v = int(_max_per_day_var.get()) if _max_per_day_var else 0
        if v > 0:
            filters['max_trades_per_day'] = v
    except Exception:
        pass

    try:
        v = float(_min_pips_var.get()) if _min_pips_var else 0
        if v != 0:
            filters['min_pips'] = v
    except Exception:
        pass

    try:
        v = float(_cooldown_var.get()) if _cooldown_var else 0
        if v > 0:
            filters['cooldown_minutes'] = v
    except Exception:
        pass

    sessions = [s for s, var in _session_vars.items() if var.get()]
    if len(sessions) < 3:
        filters['sessions'] = sessions

    days_all = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    days = [d for d, var in _day_vars.items() if var.get()]
    if len(days) < 5:
        filters['days'] = days

    if _custom_filters:
        filters['custom_filters'] = list(_custom_filters)

    return filters


def _schedule_update(event=None):
    """Debounce: schedule a stats update after a short delay."""
    global _update_pending
    if _update_pending:
        return
    _update_pending = True
    if state.window:
        state.window.after(150, _do_update)


def _do_update():
    global _update_pending, _filtered_trades
    _update_pending = False
    if not _base_trades:
        return
    try:
        from project2_backtesting.strategy_refiner import apply_filters, compute_stats_summary
        filters = _get_current_filters()
        kept, removed = apply_filters(_base_trades, filters)
        _filtered_trades = kept
        _update_results_card(kept, removed)
        # Update monthly chart and drawdown display
        if _monthly_chart_canvas:
            _draw_monthly_chart(_monthly_chart_canvas, _monthly_tooltip, kept)
        _update_drawdown_display(kept)
        _update_breach_display(kept)
    except Exception as e:
        print(f"[refiner_panel] update error: {e}")


def _update_results_card(kept, removed):
    global _results_card
    if _results_card is None:
        return
    try:
        from project2_backtesting.strategy_refiner import compute_stats_summary
        b = compute_stats_summary(_base_trades)
        a = compute_stats_summary(kept)
    except Exception:
        return

    for widget in _results_card.winfo_children():
        widget.destroy()

    def _col(parent, title, stats, color):
        f = tk.Frame(parent, bg=WHITE, padx=12, pady=8)
        f.pack(side=tk.LEFT, fill="both", expand=True)
        tk.Label(f, text=title, font=("Segoe UI", 9, "bold"),
                 bg=WHITE, fg=MIDGREY).pack(anchor="w", pady=(0, 4))
        rows = [
            ("Trades",       str(stats['count'])),
            ("Win Rate",     f"{stats['win_rate']*100:.1f}%"),
            ("Avg Pips",     f"{stats['avg_pips']:+.1f}"),
            ("Trades/Day",   f"{stats['trades_per_day']:.1f}"),
            ("Avg Hold",     f"{stats['avg_hold_minutes']:.0f}m"),
            ("Max DD",       f"{stats['max_dd_pips']:.0f} pips"),
            ("Total Pips",   f"{stats['total_pips']:+.0f}"),
        ]
        for label, val in rows:
            r = tk.Frame(f, bg=WHITE)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label + ":", font=("Segoe UI", 8),
                     bg=WHITE, fg=GREY, width=11, anchor="w").pack(side=tk.LEFT)
            tk.Label(r, text=val, font=("Segoe UI", 9, "bold"),
                     bg=WHITE, fg=color).pack(side=tk.LEFT)

    # Determine if after is better
    after_color = GREEN if a['avg_pips'] >= b['avg_pips'] else RED

    _col(_results_card, "BEFORE filters", b, MIDGREY)
    tk.Frame(_results_card, bg="#e0e0e0", width=1).pack(side=tk.LEFT, fill="y", padx=4)
    _col(_results_card, "AFTER filters", a, after_color)

    removed_n = len(removed)
    removed_net = sum(t.get('net_pips', 0) for t in removed)
    tk.Label(_results_card,
             text=f"Removed {removed_n} trades ({removed_net:+.0f} pips removed)",
             font=("Segoe UI", 8, "italic"), bg=WHITE, fg=GREY).pack(side=tk.LEFT, padx=8)


# ─────────────────────────────────────────────────────────────────────────────
# Trade list display
# ─────────────────────────────────────────────────────────────────────────────

def _display_trade_list(trades, parent):
    for widget in parent.winfo_children():
        widget.destroy()

    if not trades:
        tk.Label(parent, text="No trades after filters.",
                font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=10)
        return

    # Header
    hdr = tk.Frame(parent, bg="#f5f5f5", padx=8, pady=4)
    hdr.pack(fill="x", padx=5)
    cols = [("#",3),("Entry",17),("Exit",17),("Dir",5),("Entry$",7),
            ("Exit$",7),("Gross",7),("Spread",6),("Net",7),("Hold",8),("Reason",14)]
    for t, w in cols:
        tk.Label(hdr, text=t, font=("Segoe UI", 7, "bold"),
                bg="#f5f5f5", fg=GREY, width=w, anchor="w").pack(side=tk.LEFT, padx=1)

    MAX_ROWS = 50
    to_show = trades[:MAX_ROWS]
    total_net = 0.0
    winners = 0

    def _row(i, t):
        nonlocal total_net, winners
        net = t.get('net_pips', 0)
        total_net += net
        if net > 0:
            winners += 1
        row_bg = "#f0fdf4" if net > 0 else "#fef2f2"
        net_c = GREEN if net > 0 else RED
        dir_c = GREEN if t.get('direction') == 'BUY' else RED
        r = tk.Frame(parent, bg=row_bg, padx=8, pady=2)
        r.pack(fill="x", padx=5)
        vals = [
            (str(i),                            3,  GREY,   "Segoe UI",   False),
            (str(t.get('entry_time',''))[:16],  17, DARK,   "Consolas",   False),
            (str(t.get('exit_time', ''))[:16],  17, DARK,   "Consolas",   False),
            (t.get('direction',''),             5,  dir_c,  "Segoe UI",   True),
            (f"{t.get('entry_price',0):.2f}",   7,  DARK,   "Consolas",   False),
            (f"{t.get('exit_price', 0):.2f}",   7,  DARK,   "Consolas",   False),
            (f"{t.get('pnl_pips',0):+.1f}",     7,  MIDGREY,"Consolas",   False),
            (f"{t.get('cost_pips',0):.1f}",     6,  GREY,   "Consolas",   False),
            (f"{net:+.1f}",                     7,  net_c,  "Consolas",   True),
            (t.get('hold_display',''),           8,  GREY,   "Segoe UI",   False),
            (t.get('exit_reason',''),           14, MIDGREY,"Segoe UI",   False),
        ]
        for text, w, c, fn, bold in vals:
            tk.Label(r, text=text, font=(fn, 7, "bold" if bold else "normal"),
                    bg=row_bg, fg=c, width=w, anchor="w").pack(side=tk.LEFT, padx=1)

    for i, t in enumerate(to_show, 1):
        _row(i, t)

    # Count remaining for "show all" button
    remaining = len(trades) - MAX_ROWS

    if remaining > 0:
        def _show_rest(btn):
            btn.destroy()
            for i, t in enumerate(trades[MAX_ROWS:], MAX_ROWS + 1):
                _row(i, t)
            _footer()
        show_btn = tk.Button(parent, text=f"Show {remaining} more trades...",
                             bg="#667eea", fg="white", font=("Segoe UI", 8, "bold"),
                             relief=tk.FLAT, cursor="hand2", padx=10, pady=5)
        show_btn.configure(command=lambda b=show_btn: _show_rest(b))
        show_btn.pack(pady=6)
    else:
        _footer()

    def _footer():
        total = len(trades)
        wr = winners / max(total, 1) * 100
        foot = tk.Frame(parent, bg="#e8f4f8", padx=8, pady=6)
        foot.pack(fill="x", padx=5, pady=(4, 0))
        tk.Label(foot,
                 text=f"Total: {total} trades  |  Winners: {winners}  "
                      f"WR: {wr:.1f}%  |  Net: {total_net:+.1f} pips",
                 font=("Segoe UI", 9, "bold"), bg="#e8f4f8", fg=DARK).pack(anchor="w")


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

def _export_csv(trades=None):
    if trades is None:
        trades = _filtered_trades
    if not trades:
        messagebox.showinfo("No Trades", "No trades to export.")
        return
    fp = filedialog.asksaveasfilename(
        title="Export Trades CSV", defaultextension=".csv",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )
    if not fp:
        return
    fieldnames = ['#','entry_time','exit_time','direction','entry_price','exit_price',
                  'pnl_pips','cost_pips','net_pips','hold_minutes','hold_display',
                  'session','day_of_week','exit_reason','rule_id']
    try:
        with open(fp, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            w.writeheader()
            for i, t in enumerate(trades, 1):
                row = {'#': i, **t}
                w.writerow(row)
        messagebox.showinfo("Exported", f"Saved {len(trades)} trades to:\n{fp}")
    except Exception as e:
        messagebox.showerror("Export Error", str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Deep optimizer
# ─────────────────────────────────────────────────────────────────────────────

_opt_target_var    = None
_stage_var         = None
_generate_new_var  = None
_mode_quick_var    = None
_acct_var          = None
_risk_var          = None


def _start_optimization():
    global _opt_start_btn, _opt_stop_btn, _opt_status_lbl

    # Disable button FIRST — before any checks that might fail
    try:
        if _opt_start_btn:
            _opt_start_btn.configure(state="disabled")
        if _opt_stop_btn:
            _opt_stop_btn.configure(state="normal")
    except Exception:
        pass

    if not _base_trades:
        messagebox.showerror("No Data", "Load a strategy first.")
        # Re-enable button since we're not starting
        if _opt_start_btn:
            _opt_start_btn.configure(state="normal")
        if _opt_stop_btn:
            _opt_stop_btn.configure(state="disabled")
        return

    if _opt_status_lbl:
        _opt_status_lbl.configure(text="Running...", fg=GREY)

    target_firm = _opt_target_var.get() if _opt_target_var else None
    if target_firm == "None — maximize pips":
        target_firm = None

    # Clear previous results
    if _opt_results_frame:
        for w in _opt_results_frame.winfo_children():
            w.destroy()

    def _cb(step, total, message, current_best, elapsed_str="",
            candidates_tested=0, improvements_found=0):
        """Update optimizer UI — called from background thread."""
        pct = int(step / max(total, 1) * 100)

        def _update():
            try:
                # Update status label
                if _opt_status_lbl:
                    try:
                        if _opt_status_lbl.winfo_exists():
                            _opt_status_lbl.configure(text=message, fg="#28a745")
                    except Exception:
                        pass

                # Update live labels — check each one individually
                if isinstance(_opt_live_labels, dict):
                    # Message/status
                    msg_lbl = _opt_live_labels.get('msg')
                    if msg_lbl:
                        try:
                            if msg_lbl.winfo_exists():
                                msg_lbl.configure(text=message)
                        except Exception:
                            pass

                    # Progress
                    progress_lbl = _opt_live_labels.get('progress')
                    if progress_lbl:
                        try:
                            if progress_lbl.winfo_exists():
                                progress_lbl.configure(text=f"Step {step}/{total}  ({pct}%)")
                        except Exception:
                            pass

                    # Best name
                    best_name_lbl = _opt_live_labels.get('best_name')
                    if best_name_lbl:
                        try:
                            if best_name_lbl.winfo_exists():
                                best_name_lbl.configure(text=current_best.get('name', '—'))
                        except Exception:
                            pass

                    # Best stats
                    best_stats_lbl = _opt_live_labels.get('best_stats')
                    if best_stats_lbl:
                        try:
                            if best_stats_lbl.winfo_exists():
                                best_stats_lbl.configure(
                                    text=f"{current_best.get('trades',0)} trades  |  "
                                         f"WR {current_best.get('win_rate',0)*100:.1f}%  |  "
                                         f"avg {current_best.get('avg_pips',0):+.1f} pips  |  "
                                         f"{current_best.get('trades_per_day',0):.1f}/day")
                        except Exception:
                            pass

                    # Counters/elapsed time
                    counters_lbl = _opt_live_labels.get('counters')
                    if counters_lbl:
                        try:
                            if counters_lbl.winfo_exists():
                                counters_lbl.configure(
                                    text=f"Tested: {candidates_tested}  |  "
                                         f"Improvements: {improvements_found}  |  "
                                         f"Elapsed: {elapsed_str}")
                        except Exception:
                            pass

            except Exception as e:
                print(f"[OPTIMIZER UI] Update error: {e}")

        # Schedule on main thread
        try:
            if state.window and state.window.winfo_exists():
                state.window.after(0, _update)
            else:
                print(f"[OPTIMIZER UI] Window not available")
        except Exception as e:
            print(f"[OPTIMIZER UI] after() error: {e}")

    def _worker():
        try:
            current_trades = list(_base_trades)
            current_filters = _get_current_filters()

            spread_pips = 2.5
            commission_pips = 0.0
            idx = _get_selected_index()
            if idx is not None:
                for s in _strategies:
                    if s['index'] == idx:
                        spread_pips = s.get('spread_pips', 2.5)
                        commission_pips = s.get('commission_pips', 0.0)
                        break

            all_candidates = []

            # Get stage and account size
            stage = _stage_var.get().lower() if _stage_var else "funded"
            account_size = float(_acct_var.get()) if _acct_var else 100000

            # Pass stage to presets for scoring
            from project2_backtesting.strategy_refiner import get_prop_firm_presets
            if target_firm and isinstance(target_firm, str):
                presets = get_prop_firm_presets()
                target_data = presets.get(target_firm, {})
                target_data['stage'] = stage
            elif target_firm and isinstance(target_firm, dict):
                target_data = target_firm
                target_data['stage'] = stage
            else:
                target_data = {'stage': stage}

            # ── Mode 1: Quick optimize (filter existing trades) ──
            if _mode_quick_var.get():
                print("[OPTIMIZER] Running Quick Optimize mode...")
                state.window.after(0, lambda: _opt_status_lbl.configure(
                    text="Quick Optimize: testing filter combinations...", fg="#e67e22"))

                from project2_backtesting.strategy_refiner import deep_optimize
                quick_results = deep_optimize(
                    trades=current_trades,
                    candles_df=None,
                    indicators_df=None,
                    base_rules=[],
                    exit_strategies=[],
                    target_firm=target_data,
                    account_size=account_size,
                    progress_callback=_cb,
                )
                all_candidates.extend(quick_results)
                print(f"[OPTIMIZER] Quick mode found {len(quick_results)} candidates")

            # ── Mode 2: Generate new trades (modify rules) ──
            if _generate_new_var.get():
                print("[OPTIMIZER] Running Deep Explore mode...")
                state.window.after(0, lambda: _opt_status_lbl.configure(
                    text="Deep Explore: loading indicators and modifying rules...", fg="#e67e22"))

                import json as _json
                from project2_backtesting.strategy_refiner import deep_optimize_generate

                rules_path = os.path.join(
                    project_root, 'project1_reverse_engineering', 'outputs', 'analysis_report.json'
                )
                if not os.path.exists(rules_path):
                    state.window.after(0, lambda: _opt_status_lbl.configure(
                        text="Error: analysis_report.json not found.", fg=RED))
                    return

                with open(rules_path) as f:
                    report = _json.load(f)
                base_rules = [r for r in report.get('rules', []) if r.get('prediction') == 'WIN']

                # Find candle path from config
                from project2_backtesting.panels.configuration import load_config
                cfg = load_config()
                symbol = cfg.get('symbol', 'XAUUSD').lower()
                entry_tf = cfg.get('winning_scenario', 'H1')

                candles_path = None
                for p in [
                    os.path.join(project_root, 'data', f'{symbol}_{entry_tf}.csv'),
                    os.path.join(project_root, 'data', f'xauusd_{entry_tf}.csv'),
                    os.path.join(project_root, 'data', 'xauusd_H1.csv'),
                ]:
                    if os.path.exists(p):
                        candles_path = p
                        break

                if not candles_path:
                    state.window.after(0, lambda: _opt_status_lbl.configure(
                        text=f"Error: candle CSV not found.", fg=RED))
                    return

                feature_matrix_path = os.path.join(
                    project_root, 'project1_reverse_engineering', 'outputs', 'feature_matrix.csv'
                )

                generate_results = deep_optimize_generate(
                    trades=current_trades,
                    base_rules=base_rules,
                    candles_path=candles_path,
                    timeframe=entry_tf,
                    spread_pips=spread_pips,
                    commission_pips=commission_pips,
                    target_firm=target_data,
                    account_size=account_size,
                    filters=current_filters if current_filters else None,
                    progress_callback=_cb,
                    feature_matrix_path=feature_matrix_path,
                )
                all_candidates.extend(generate_results)
                print(f"[OPTIMIZER] Deep Explore found {len(generate_results)} candidates")

            if not _mode_quick_var.get() and not _generate_new_var.get():
                state.window.after(0, lambda: _opt_status_lbl.configure(
                    text="Select at least one optimization mode!", fg=RED))
                return

            # Sort all candidates by score
            all_candidates.sort(key=lambda c: c.get('score', 0), reverse=True)
            results = all_candidates[:20]

            state.window.after(0, lambda: _show_opt_results(results))
            state.window.after(0, lambda: _opt_status_lbl.configure(
                text=f"Complete — {len(results)} candidates from "
                     f"{'Quick' if _mode_quick_var.get() else ''}"
                     f"{' + ' if _mode_quick_var.get() and _generate_new_var.get() else ''}"
                     f"{'Deep Explore' if _generate_new_var.get() else ''}",
                fg=GREEN))
        except Exception as e:
            import traceback
            traceback.print_exc()
            state.window.after(0, lambda: _opt_status_lbl.configure(
                text=f"Error: {e}", fg=RED))
        finally:
            try:
                state.window.after(0, lambda: _opt_start_btn.configure(state="normal"))
                state.window.after(0, lambda: _opt_stop_btn.configure(state="disabled"))
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()


def _stop_optimization():
    from project2_backtesting.strategy_refiner import stop_optimization
    stop_optimization()
    if _opt_status_lbl:
        _opt_status_lbl.configure(text="Stopped by user", fg=AMBER)


def _show_opt_results(candidates):
    """Show optimizer results filtered by minimum WR, with working save buttons."""
    if _opt_results_frame is None:
        return
    for w in _opt_results_frame.winfo_children():
        w.destroy()

    if not candidates:
        tk.Label(_opt_results_frame, text="No candidates found.",
                font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=10)
        return

    from project2_backtesting.strategy_refiner import compute_stats_summary

    # ── WR filter row ──
    filter_row = tk.Frame(_opt_results_frame, bg=BG)
    filter_row.pack(fill="x", padx=5, pady=(4, 6))

    tk.Label(filter_row, text="Min Win Rate:",
             font=("Segoe UI", 9, "bold"), bg=BG, fg=DARK).pack(side=tk.LEFT)

    wr_filter_var = tk.StringVar(value="75")
    wr_entry = tk.Entry(filter_row, textvariable=wr_filter_var, width=5, font=("Segoe UI", 9))
    wr_entry.pack(side=tk.LEFT, padx=5)
    tk.Label(filter_row, text="%", font=("Segoe UI", 9), bg=BG, fg=GREY).pack(side=tk.LEFT)

    # Store candidates for re-filtering
    _all_candidates = list(candidates)

    def _apply_wr_filter(*_):
        try:
            min_wr = float(wr_filter_var.get()) / 100.0  # convert to decimal
        except ValueError:
            min_wr = 0.75

        # Clear old cards (keep filter row)
        for w in _opt_results_frame.winfo_children():
            if w != filter_row:
                w.destroy()

        # Filter
        filtered = []
        for c in _all_candidates:
            s = c.get('stats') or compute_stats_summary(c.get('trades', []))
            wr = s.get('win_rate', 0)
            if wr <= 1:
                wr_check = wr
            else:
                wr_check = wr / 100
            if wr_check >= min_wr:
                filtered.append((c, s))

        # Sort by score
        filtered.sort(key=lambda x: x[0].get('score', 0), reverse=True)

        # Count label
        tk.Label(_opt_results_frame,
                 text=f"Showing {len(filtered)} of {len(_all_candidates)} strategies (WR >= {min_wr*100:.0f}%)",
                 font=("Segoe UI", 10, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(4, 6))

        if not filtered:
            tk.Label(_opt_results_frame, text="No strategies meet the minimum win rate. Try lowering it.",
                     font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=10)
            return

        # Dollar conversion
        try:
            acct = float(_acct_var.get()) if _acct_var else 100000
            risk = float(_risk_var.get()) if _risk_var else 1.0
        except Exception:
            acct = 100000
            risk = 1.0
        pip_value = 10.0
        sl_pips = 150
        lot_size = (acct * risk / 100) / (sl_pips * pip_value)
        dollar_per_pip = pip_value * lot_size

        # Get firm info for challenge fee
        presets = {}
        challenge_fee = 0
        profit_split = 80
        try:
            from project2_backtesting.strategy_refiner import get_prop_firm_presets
            presets = get_prop_firm_presets()
            firm = _opt_target_var.get() if _opt_target_var else ""
            preset = presets.get(firm, {})
            firm_data = preset.get('firm_data')
            if firm_data:
                costs = firm_data['challenges'][0].get('costs', {})
                fee_by_size = costs.get('challenge_fee_by_size', {})
                challenge_fee = fee_by_size.get(str(int(acct)), 0)
                profit_split = firm_data['challenges'][0].get('funded', {}).get('profit_split_pct', 80)
        except Exception:
            pass

        # ── Render cards ──
        for i, (cand, stats) in enumerate(filtered, 1):
            score = cand.get('score', 0)
            rules = cand.get('rules', [])
            filters = cand.get('filters_applied', {})
            changes = cand.get('changes_from_base', '')

            card_bg = "#f0fff0" if score > 0 else "#fff8f8"
            border = "#28a745" if score > 0 else "#dc3545"

            card = tk.Frame(_opt_results_frame, bg=card_bg,
                            highlightbackground=border, highlightthickness=2,
                            padx=12, pady=8)
            card.pack(fill="x", padx=5, pady=4)

            # Header
            strategy_name = cand.get('name', '?')
            tk.Label(card, text=f"#{i}: {strategy_name}  (score: {score:.1f})",
                     font=("Segoe UI", 10, "bold"), bg=card_bg, fg=DARK).pack(anchor="w")

            # Stats
            wr = stats.get('win_rate', 0)
            wr_str = f"{wr*100:.1f}%" if wr <= 1 else f"{wr:.1f}%"
            wr_color = GREEN if (wr if wr <= 1 else wr/100) >= 0.60 else AMBER

            stats_text = (f"Trades: {stats.get('count', 0)}  |  WR: {wr_str}  |  "
                          f"Avg: {stats.get('avg_pips', 0):+.1f} pips  |  "
                          f"Total: {stats.get('total_pips', 0):+,.0f} pips  |  "
                          f"{stats.get('trades_per_day', 0):.1f}/day")
            tk.Label(card, text=stats_text, font=("Segoe UI", 9), bg=card_bg,
                     fg=wr_color).pack(anchor="w", pady=(2, 0))

            # Dollar amounts
            total_pips = stats.get('total_pips', 0)
            total_dollars = total_pips * dollar_per_pip
            total_pct = (total_dollars / acct) * 100
            try:
                trade_list = cand.get('trades', [])
                if trade_list:
                    import pandas as pd
                    first = pd.to_datetime(trade_list[0].get('entry_time', ''))
                    last = pd.to_datetime(trade_list[-1].get('entry_time', ''))
                    months = max((last - first).days / 30, 1)
                    monthly_dollars = total_dollars / months
                else:
                    monthly_dollars = 0
            except Exception:
                monthly_dollars = 0

            your_monthly = monthly_dollars * (profit_split / 100)

            dollar_frame = tk.Frame(card, bg=card_bg)
            dollar_frame.pack(fill="x", pady=(2, 0))

            for label, value, color in [
                ("Total", f"${total_dollars:+,.0f} ({total_pct:+.1f}%)",
                 "#28a745" if total_dollars > 0 else "#dc3545"),
                ("Monthly", f"${monthly_dollars:+,.0f}/mo",
                 "#28a745" if monthly_dollars > 0 else "#dc3545"),
                ("Your share", f"${your_monthly:+,.0f}/mo ({profit_split}%)", "#667eea"),
            ]:
                tk.Label(dollar_frame, text=f"{label}: ", bg=card_bg, fg="#888",
                         font=("Arial", 8)).pack(side=tk.LEFT)
                tk.Label(dollar_frame, text=value, bg=card_bg, fg=color,
                         font=("Arial", 8, "bold")).pack(side=tk.LEFT, padx=(0, 10))

            # Challenge fee ROI
            if challenge_fee > 0 and your_monthly > 0:
                months_to_roi = challenge_fee / max(your_monthly, 1)
                roi_frame = tk.Frame(card, bg="#e8f5e9", padx=6, pady=3)
                roi_frame.pack(fill="x", pady=(3, 0))
                tk.Label(roi_frame,
                         text=f"Fee: ${challenge_fee} | ROI in {months_to_roi:.1f}mo | "
                              f"Year 1: ${(your_monthly * 12 - challenge_fee):+,.0f}",
                         bg="#e8f5e9", fg="#2e7d32", font=("Arial", 8, "bold")).pack(anchor="w")

            # What changed
            if isinstance(filters, dict) and filters:
                changes_frame = tk.Frame(card, bg="#e8f4fd", padx=8, pady=5)
                changes_frame.pack(fill="x", pady=(5, 0))
                tk.Label(changes_frame, text="What was changed:",
                         bg="#e8f4fd", fg="#1565c0", font=("Segoe UI", 9, "bold")).pack(anchor="w")
                explanations = {
                    'max_trades_per_day': lambda v: f"Max {v} trades/day",
                    'min_hold_minutes': lambda v: f"Min hold {v} minutes",
                    'cooldown_minutes': lambda v: f"Wait {v} min between trades",
                    'min_pips': lambda v: f"Skip trades < {v} pips",
                    'sessions': lambda v: f"Sessions: {', '.join(v) if isinstance(v, list) else v}",
                }
                for fk, fv in filters.items():
                    if fk in ('description', 'firm_data', 'stage'):
                        continue
                    fn = explanations.get(fk)
                    txt = fn(fv) if fn else f"{fk} = {fv}"
                    tk.Label(changes_frame, text=f"  - {txt}",
                             bg="#e8f4fd", fg="#333", font=("Segoe UI", 8)).pack(anchor="w")
            elif changes:
                changes_frame = tk.Frame(card, bg="#e8f4fd", padx=8, pady=3)
                changes_frame.pack(fill="x", pady=(3, 0))
                tk.Label(changes_frame, text=f"Changed: {changes}",
                         bg="#e8f4fd", fg="#333", font=("Segoe UI", 8)).pack(anchor="w")

            # Rules display
            if rules:
                win_rules = [r for r in rules if r.get('prediction') == 'WIN']
                if win_rules:
                    rules_frame = tk.Frame(card, bg="#f5f0ff", padx=8, pady=5)
                    rules_frame.pack(fill="x", pady=(5, 0))
                    tk.Label(rules_frame, text=f"Entry Rules ({len(win_rules)}):",
                             bg="#f5f0ff", fg="#8e44ad", font=("Segoe UI", 9, "bold")).pack(anchor="w")
                    for ri, rule in enumerate(win_rules[:3]):
                        conds = rule.get('conditions', [])
                        cond_strs = [f"{c['feature']} {c['operator']} {c['value']}" for c in conds[:3]]
                        tk.Label(rules_frame, text=f"  R{ri+1}: {' AND '.join(cond_strs)}",
                                 bg="#f5f0ff", fg="#555", font=("Courier", 8)).pack(anchor="w")

            # ── Action buttons ──
            btn_row = tk.Frame(card, bg=card_bg)
            btn_row.pack(fill="x", pady=(6, 0))

            trades_snap = list(cand.get('trades', []))
            rules_snap = list(cand.get('rules', []))
            filters_snap = dict(cand.get('filters_applied', {})) if isinstance(cand.get('filters_applied'), dict) else {}
            cand_name = strategy_name
            stats_snap = dict(stats)

            # View Trades
            tk.Button(btn_row, text="📊 Trades",
                      command=lambda t=trades_snap: _show_candidate_trades(t),
                      bg="#667eea", fg="white", font=("Segoe UI", 8, "bold"),
                      relief=tk.FLAT, cursor="hand2", padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 4))

            # 💾 Save — WORKING version
            def _do_save(r=rules_snap, f=filters_snap, n=cand_name, s=stats_snap):
                try:
                    from shared.saved_rules import save_rule
                    save_data = {
                        'conditions': [],
                        'prediction': 'WIN',
                        'win_rate': s.get('win_rate', 0),
                        'avg_pips': s.get('avg_pips', 0),
                        'total_pips': s.get('total_pips', 0),
                        'net_total_pips': s.get('total_pips', 0),
                        'total_trades': s.get('count', 0),
                        'max_dd_pips': s.get('max_dd_pips', 0),
                        'net_profit_factor': s.get('profit_factor', 0),
                        'optimized_rules': r,
                        'filters_applied': f,
                    }
                    for rule in r:
                        if rule.get('prediction') == 'WIN':
                            save_data['conditions'].extend(rule.get('conditions', []))

                    rid = save_rule(save_data, source=f"Optimizer: {n}", notes=str(f))
                    messagebox.showinfo("Saved", f"Strategy saved as rule #{rid}!\n\n"
                                                  f"Find it in 💾 Saved Rules panel.")
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    messagebox.showerror("Save Error", f"Could not save: {e}")

            tk.Button(btn_row, text="💾 Save",
                      command=_do_save,
                      bg="#28a745", fg="white", font=("Segoe UI", 8, "bold"),
                      relief=tk.FLAT, cursor="hand2", padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 4))

            # → Playground
            def _to_playground(r=rules_snap):
                try:
                    import json
                    temp = os.path.join(project_root, 'project2_backtesting', 'outputs', '_playground_rules.json')
                    with open(temp, 'w') as fp:
                        json.dump({'rules': r, 'source': 'optimizer'}, fp, indent=2, default=str)
                    messagebox.showinfo("Ready", "Rules loaded for Playground.\n\n"
                                                  "Go to 🎮 Strategy Playground.")
                except Exception as e:
                    messagebox.showerror("Error", str(e))

            tk.Button(btn_row, text="🎮 Playground",
                      command=_to_playground,
                      bg="#17a2b8", fg="white", font=("Segoe UI", 8, "bold"),
                      relief=tk.FLAT, cursor="hand2", padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 4))

            # → Validator
            def _to_validator(t=trades_snap, r=rules_snap, n=cand_name):
                try:
                    import json
                    temp = os.path.join(project_root, 'project2_backtesting', 'outputs', '_validator_optimized.json')
                    with open(temp, 'w') as fp:
                        json.dump({'rules': r, 'trades': t, 'name': n, 'source': 'optimizer'},
                                  fp, indent=2, default=str)
                    messagebox.showinfo("Ready", f"Strategy '{n}' ready for validation.\n\n"
                                                  "Go to ✅ Strategy Validator.")
                except Exception as e:
                    messagebox.showerror("Error", str(e))

            tk.Button(btn_row, text="✅ Validator",
                      command=_to_validator,
                      bg="#e67e22", fg="white", font=("Segoe UI", 8, "bold"),
                      relief=tk.FLAT, cursor="hand2", padx=8, pady=3).pack(side=tk.LEFT, padx=(0, 4))

            # Export CSV
            def _to_csv(t=trades_snap, n=cand_name):
                path = filedialog.asksaveasfilename(
                    defaultextension=".csv",
                    initialfile=f"optimized_{n.replace(' ', '_')}.csv",
                    filetypes=[("CSV", "*.csv")])
                if path:
                    import pandas as pd
                    pd.DataFrame(t).to_csv(path, index=False)
                    messagebox.showinfo("Exported", f"Saved {len(t)} trades to:\n{path}")

            tk.Button(btn_row, text="📁 CSV",
                      command=_to_csv,
                      bg="#6c757d", fg="white", font=("Segoe UI", 8, "bold"),
                      relief=tk.FLAT, cursor="hand2", padx=8, pady=3).pack(side=tk.LEFT)

    # Apply filter button
    tk.Button(filter_row, text="Filter", font=("Segoe UI", 9, "bold"),
              bg="#667eea", fg="white", relief=tk.FLAT, padx=10,
              command=_apply_wr_filter).pack(side=tk.LEFT, padx=10)

    # Initial render
    _apply_wr_filter()


def _show_candidate_trades(trades):
    """Display candidate trades in the trade list section."""
    if _trade_list_frame is None:
        return
    _display_trade_list(trades, _trade_list_frame)
    # Scroll to trade list
    if _scroll_canvas:
        _scroll_canvas.yview_moveto(0.5)


def _draw_monthly_chart(canvas, tooltip, trades):
    """Draw monthly P&L bar chart with hover tooltips."""
    from project2_backtesting.strategy_refiner import compute_monthly_pnl

    # Load account config for profit % calculations
    try:
        from project2_backtesting.panels.configuration import load_config
        cfg = load_config()
        _acct_size = float(cfg.get('starting_capital', '100000'))
        _risk_pct = float(cfg.get('risk_pct', '1.0'))
        _pip_value = float(cfg.get('pip_value_per_lot', '10.0'))
    except Exception:
        _acct_size = 100000
        _risk_pct = 1.0
        _pip_value = 10.0

    canvas.delete("all")
    monthly = compute_monthly_pnl(trades, account_size=_acct_size,
                                   risk_pct=_risk_pct, pip_value=_pip_value)

    if not monthly:
        canvas.create_text(200, 100, text="No trade data", font=("Arial", 11), fill="#888")
        return

    w = canvas.winfo_width() or 800
    h = canvas.winfo_height() or 200

    n = len(monthly)
    if n == 0:
        return

    margin_left = 60
    margin_right = 20
    margin_top = 20
    margin_bottom = 40
    chart_w = w - margin_left - margin_right
    chart_h = h - margin_top - margin_bottom

    bar_width = max(2, min(20, chart_w // n - 2))

    pnls = [m['pnl_pips'] for m in monthly]
    max_pnl = max(max(pnls), 1)
    min_pnl = min(min(pnls), -1)
    pnl_range = max_pnl - min_pnl

    # Zero line position
    zero_y = margin_top + int(chart_h * max_pnl / pnl_range)

    # Draw zero line
    canvas.create_line(margin_left, zero_y, w - margin_right, zero_y,
                        fill="#aaa", dash=(3, 3))
    canvas.create_text(margin_left - 5, zero_y, text="0", anchor="e",
                        font=("Arial", 7), fill="#888")

    # Draw bars
    bar_items = []  # (rect_id, month_data)

    for i, m in enumerate(monthly):
        x = margin_left + int(i * chart_w / n) + 1
        pnl = m['pnl_pips']

        if pnl >= 0:
            bar_top = zero_y - int((pnl / pnl_range) * chart_h)
            bar_bottom = zero_y
            color = "#28a745"
        else:
            bar_top = zero_y
            bar_bottom = zero_y + int((abs(pnl) / pnl_range) * chart_h)
            color = "#dc3545"

        rect = canvas.create_rectangle(x, bar_top, x + bar_width, bar_bottom,
                                        fill=color, outline=color, width=0)
        bar_items.append((rect, m))

        # Month label (every 3rd or 6th month to avoid crowding)
        if n <= 36 or i % 3 == 0:
            label = m['month'][2:]  # '2020-01' → '20-01'
            canvas.create_text(x + bar_width // 2, h - 10, text=label,
                                font=("Arial", 6), fill="#888", angle=45)

    # Y-axis labels
    for val in [max_pnl, max_pnl // 2, min_pnl // 2, min_pnl]:
        y = zero_y - int((val / pnl_range) * chart_h)
        canvas.create_text(margin_left - 5, y, text=f"{val:+.0f}",
                            anchor="e", font=("Arial", 7), fill="#888")

    # Hover tooltips
    def _on_motion(event):
        for rect, m in bar_items:
            coords = canvas.coords(rect)
            if coords and coords[0] <= event.x <= coords[2]:
                pnl = m['pnl_pips']
                pnl_pct = m.get('pnl_pct', 0)
                pnl_dollars = m.get('pnl_dollars', 0)

                text = (f"{m['month']}: {pnl:+,.0f} pips  ({pnl_pct:+.1f}%  ${pnl_dollars:+,.0f})\n"
                        f"{m['trades']} trades ({m['wins']}W / {m['losses']}L)\n"
                        f"Avg: {m.get('avg_trades_per_day', 0)}/day  "
                        f"Min: {m.get('min_trades_per_day', 0)}/day  "
                        f"Max: {m.get('max_trades_per_day', 0)}/day")
                tooltip.config(text=text)
                tooltip.place(x=event.x + 10, y=event.y - 50)
                return
        tooltip.place_forget()

    def _on_leave(event):
        tooltip.place_forget()

    canvas.bind("<Motion>", _on_motion)
    canvas.bind("<Leave>", _on_leave)


def _update_drawdown_display(trades):
    """Update drawdown analysis display."""
    from project2_backtesting.strategy_refiner import compute_three_drawdowns
    global _dd_label

    if _dd_label is None:
        return

    if not trades:
        _dd_label.config(text="No trade data", fg="#888")
        return

    dd = compute_three_drawdowns(trades, account_size=100000)

    dd_text = (
        f"┌─────────────────────────────────────────────────────────┐\n"
        f"│ 🔴 End-of-Day DD:    {dd['eod_dd_pips']:>8,.0f} pips  ({dd['eod_dd_pct']:>5.1f}%)  │  ← PROP FIRM MEASURES THIS\n"
        f"│    Worst day:        {dd['daily_dd_worst_pips']:>8,.0f} pips  ({dd['daily_dd_worst_pct']:>5.1f}%)  │  date: {dd['daily_dd_worst_date'] or '?'}\n"
        f"│                                                         │\n"
        f"│ 🟡 Realized DD:      {dd['realized_dd_pips']:>8,.0f} pips  ({dd['realized_dd_pct']:>5.1f}%)  │  after trades close\n"
        f"│                                                         │\n"
        f"│ 🟠 Floating DD:      {dd['floating_dd_pips']:>8,.0f} pips  ({dd['floating_dd_pct']:>5.1f}%)  │  during open trades\n"
        f"└─────────────────────────────────────────────────────────┘\n"
    )

    # Color based on prop firm limits
    if dd['daily_dd_worst_pct'] >= 5.0:
        dd_text += "\n⚠️  Worst single day exceeds FTMO 5% daily DD limit!"
        _dd_label.config(fg="#dc3545")
    elif dd['eod_dd_pct'] >= 10.0:
        dd_text += "\n⚠️  Total EOD drawdown exceeds FTMO 10% limit!"
        _dd_label.config(fg="#dc3545")
    else:
        dd_text += f"\n✅  Within FTMO limits (daily: {dd['daily_dd_worst_pct']:.1f}%/5%, total: {dd['eod_dd_pct']:.1f}%/10%)"
        _dd_label.config(fg="#28a745")

    _dd_label.config(text=dd_text)


def _update_breach_display(trades):
    """Update DD breach counter display."""
    from project2_backtesting.strategy_refiner import count_dd_breaches
    global _breach_label

    if _breach_label is None:
        return

    if not trades:
        _breach_label.config(text="No trade data", fg="#888")
        return

    breaches = count_dd_breaches(trades, account_size=100000,
                                  daily_dd_limit_pct=5.0, total_dd_limit_pct=10.0,
                                  daily_dd_safety_pct=4.0, total_dd_safety_pct=8.0)

    blown = breaches['blown_count']
    daily_dd_limit = 5.0
    total_dd_limit = 10.0

    if blown == 0:
        breach_text = (
            f"  ✅ ZERO BREACHES across {breaches['total_months']} months!\n"
            f"     Never exceeded daily {daily_dd_limit}% or total {total_dd_limit}% DD limit.\n"
            f"     Survival rate: {breaches['survival_rate_per_month']}%"
        )
        _breach_label.config(fg="#28a745")
    else:
        breach_text = (
            f"  💀 BLOWN {blown} times in {breaches['total_months']} months\n"
            f"\n"
            f"     Daily DD breaches (≥{daily_dd_limit}%):  {breaches['daily_breaches']} times\n"
            f"     Total DD breaches (≥{total_dd_limit}%): {breaches['total_breaches']} times\n"
            f"\n"
            f"     Worst daily DD:           {breaches['worst_daily_pct']:.1f}%  (limit: {daily_dd_limit}%)\n"
            f"     Worst total DD:           {breaches['worst_total_pct']:.1f}%  (limit: {total_dd_limit}%)\n"
            f"\n"
            f"     Avg days between blows:   {breaches['avg_days_between_blows']} days\n"
            f"     Monthly survival rate:    {breaches['survival_rate_per_month']}%\n"
            f"     Months with blowup:       {breaches['months_blown']} / {breaches['total_months']}\n"
        )

        # Format blow dates as month/year
        import datetime
        all_blow_dates = sorted(set(
            breaches.get('daily_breach_dates', []) +
            breaches.get('total_breach_dates', [])
        ))

        if all_blow_dates:
            breach_text += f"\n\n     Blow timeline:\n"
            for d in all_blow_dates:
                try:
                    dt = datetime.datetime.strptime(d[:10], '%Y-%m-%d')
                    month_str = dt.strftime('%B %Y')  # "October 2008"
                    # Check if daily or total breach
                    breach_type = "daily" if d in breaches.get('daily_breach_dates', []) else "total"
                    breach_text += f"       • {month_str} ({breach_type} DD breach)\n"
                except Exception:
                    breach_text += f"       • {d} (breach)\n"

        # Add safety stops info
        daily_safety = breaches.get('daily_safety_stops', 0)
        total_safety = breaches.get('total_safety_stops', 0)
        total_safety_stops = daily_safety + total_safety

        if total_safety_stops > 0:
            breach_text += f"\n\n  ⚠️ SAFETY STOPS: {total_safety_stops} times (daily:{daily_safety} total:{total_safety})\n"
            breach_text += f"     Bot paused before firm limits — account survived\n"

            # Format safety dates
            all_safety_dates = sorted(set(
                breaches.get('daily_safety_dates', []) +
                breaches.get('total_safety_dates', [])
            ))

            if all_safety_dates:
                breach_text += f"\n     Safety stop timeline:\n"
                for d in all_safety_dates:
                    try:
                        dt = datetime.datetime.strptime(d[:10], '%Y-%m-%d')
                        month_str = dt.strftime('%B %Y')
                        # Check if daily or total safety stop
                        stop_type = "daily" if d in breaches.get('daily_safety_dates', []) else "total"
                        breach_text += f"       • {month_str} ({stop_type} safety limit)\n"
                    except Exception:
                        breach_text += f"       • {d} (safety stop)\n"

        if blown <= 3:
            breach_text += f"\n     🟡 Occasional blows — might pass with good timing"
            _breach_label.config(fg="#e67e22")
        else:
            breach_text += f"\n     🔴 Too many blows — not prop-firm safe"
            _breach_label.config(fg="#dc3545")

    _breach_label.config(text=breach_text)


# ─────────────────────────────────────────────────────────────────────────────
# Panel builder
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _strategy_var, _strat_info_lbl, _base_stats_frame
    global _min_hold_var, _max_hold_var, _max_per_day_var, _min_pips_var, _cooldown_var
    global _session_vars, _day_vars, _results_card, _trade_list_frame
    global _monthly_chart_canvas, _monthly_tooltip, _dd_label, _breach_label
    global _opt_progress_frame, _opt_results_frame, _opt_live_labels
    global _opt_status_lbl, _opt_start_btn, _opt_stop_btn, _opt_target_var, _stage_var
    global _scroll_canvas, _generate_new_var, _mode_quick_var, _acct_var, _risk_var

    _load_strategies()

    panel = tk.Frame(parent, bg=BG)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(panel, bg=WHITE, pady=16)
    hdr.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(hdr, text="🔧 Strategy Refiner",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(hdr, text="Optimize your strategy for prop firm challenges",
             bg=WHITE, fg=GREY, font=("Segoe UI", 11)).pack(pady=(4, 0))

    # ── Strategy selector ─────────────────────────────────────────────────────
    sel_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    sel_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(sel_frame, text="Strategy", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    sel_row = tk.Frame(sel_frame, bg=WHITE)
    sel_row.pack(fill="x")

    if not _strategies:
        tk.Label(sel_row, text="No backtest results. Run the backtest first.",
                 font=("Segoe UI", 10, "italic"), bg=WHITE, fg=RED).pack(side=tk.LEFT)
        _strategy_var = tk.StringVar(value="")
    else:
        _strategy_var = tk.StringVar(value=_strategies[0]['label'])
        labels = [s['label'] for s in _strategies]
        dd = ttk.Combobox(sel_row, textvariable=_strategy_var,
                          values=labels, state="readonly", width=95)
        dd.pack(side=tk.LEFT, padx=(0, 10))

    tk.Button(sel_row, text="Load", command=_load_selected_strategy,
              bg=GREEN, fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=14, pady=4).pack(side=tk.LEFT)

    _strat_info_lbl = tk.Label(sel_frame, text="Click Load to load a strategy.",
                                font=("Segoe UI", 9), bg=WHITE, fg=GREY)
    _strat_info_lbl.pack(anchor="w", pady=(5, 0))

    # ── Scrollable area ───────────────────────────────────────────────────────
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

    # Safe mousewheel binding — doesn't break other canvases
    def _on_enter(event):
        _scroll_canvas.bind("<MouseWheel>",
            lambda e: _scroll_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        # Linux
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

    # Everything below goes inside scroll_frame
    sf = scroll_frame

    # ── MODE 1: Filters ───────────────────────────────────────────────────────
    mode1_hdr = tk.Frame(sf, bg=WHITE, padx=20, pady=8)
    mode1_hdr.pack(fill="x", padx=5, pady=(5, 0))
    tk.Label(mode1_hdr, text="⚡ Quick Filters (instant preview)",
             font=("Segoe UI", 12, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")

    filters_frame = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    filters_frame.pack(fill="x", padx=5, pady=(0, 5))

    def _filter_row(parent, label, var, from_, to_, resolution=1, is_float=False):
        """Create one filter row with label, scale, and value display."""
        row = tk.Frame(parent, bg=WHITE)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, font=("Segoe UI", 9), bg=WHITE, fg=DARK,
                 width=22, anchor="w").pack(side=tk.LEFT)
        scale = tk.Scale(row, variable=var, from_=from_, to=to_,
                         resolution=resolution, orient=tk.HORIZONTAL,
                         bg=WHITE, highlightthickness=0, length=220,
                         command=lambda v: _schedule_update())
        scale.pack(side=tk.LEFT)
        val_lbl = tk.Label(row, textvariable=var, font=("Segoe UI", 8),
                           bg=WHITE, fg=MIDGREY, width=6)
        val_lbl.pack(side=tk.LEFT, padx=4)
        return scale

    _min_hold_var = tk.DoubleVar(value=0)
    _filter_row(filters_frame, "Min hold time (min):", _min_hold_var, 0, 120, resolution=1)

    _max_per_day_var = tk.IntVar(value=0)
    _filter_row(filters_frame, "Max trades/day (0=unlimited):", _max_per_day_var, 0, 20, resolution=1)

    _cooldown_var = tk.DoubleVar(value=0)
    _filter_row(filters_frame, "Cooldown between trades (min):", _cooldown_var, 0, 480, resolution=5)

    _min_pips_var = tk.DoubleVar(value=0)
    _filter_row(filters_frame, "Min net pips (0=no filter):", _min_pips_var, -50, 200, resolution=5)

    # Sessions
    sess_row = tk.Frame(filters_frame, bg=WHITE)
    sess_row.pack(fill="x", pady=3)
    tk.Label(sess_row, text="Sessions:", font=("Segoe UI", 9), bg=WHITE, fg=DARK,
             width=22, anchor="w").pack(side=tk.LEFT)
    for sess in ["Asian", "London", "New York"]:
        var = tk.BooleanVar(value=True)
        _session_vars[sess] = var
        tk.Checkbutton(sess_row, text=sess, variable=var, bg=WHITE,
                       font=("Segoe UI", 9),
                       command=_schedule_update).pack(side=tk.LEFT, padx=5)

    # Days
    day_row = tk.Frame(filters_frame, bg=WHITE)
    day_row.pack(fill="x", pady=3)
    tk.Label(day_row, text="Days:", font=("Segoe UI", 9), bg=WHITE, fg=DARK,
             width=22, anchor="w").pack(side=tk.LEFT)
    for day in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
        var = tk.BooleanVar(value=True)
        _day_vars[day] = var
        tk.Checkbutton(day_row, text=day, variable=var, bg=WHITE,
                       font=("Segoe UI", 9),
                       command=_schedule_update).pack(side=tk.LEFT, padx=3)

    # ── Prop firm presets ─────────────────────────────────────────────────────
    presets_frame = tk.Frame(sf, bg=WHITE, padx=20, pady=8)
    presets_frame.pack(fill="x", padx=5, pady=(0, 5))

    tk.Label(presets_frame, text="Prop firm presets:",
             font=("Segoe UI", 9, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(0, 10))

    from project2_backtesting.strategy_refiner import get_prop_firm_presets
    presets = get_prop_firm_presets()

    def _apply_preset(vals):
        if _min_hold_var:
            _min_hold_var.set(vals.get('min_hold_minutes', 0))
        if _max_per_day_var:
            _max_per_day_var.set(vals.get('max_trades_per_day', 0))
        if _cooldown_var:
            _cooldown_var.set(vals.get('cooldown_minutes', 0))
        if _min_pips_var:
            _min_pips_var.set(vals.get('min_pips', 0))
        for sess, var in _session_vars.items():
            var.set(True)
        for day, var in _day_vars.items():
            var.set(True)
        _schedule_update()

    def _reset_filters():
        _apply_preset({})

    preset_colors = {
        "FTMO-friendly": "#667eea", "Topstep-friendly": "#764ba2",
        "Apex-friendly": "#2d8a4e",
    }
    for pname, pvals in presets.items():
        if pname == "Custom":
            tk.Button(presets_frame, text="Reset", command=_reset_filters,
                      bg=GREY, fg="white", font=("Segoe UI", 8, "bold"),
                      relief=tk.FLAT, cursor="hand2", padx=10, pady=4).pack(side=tk.LEFT, padx=3)
        else:
            col = preset_colors.get(pname, "#667eea")
            filt = {k: v for k, v in pvals.items() if k != 'description'}
            tk.Button(presets_frame, text=pname,
                      command=lambda f=filt: _apply_preset(f),
                      bg=col, fg="white", font=("Segoe UI", 8, "bold"),
                      relief=tk.FLAT, cursor="hand2", padx=10, pady=4).pack(side=tk.LEFT, padx=3)

    # ── Results comparison card ───────────────────────────────────────────────
    rc_outer = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    rc_outer.pack(fill="x", padx=5, pady=(0, 5))
    tk.Label(rc_outer, text="Live results", font=("Segoe UI", 10, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))
    _results_card = tk.Frame(rc_outer, bg=WHITE)
    _results_card.pack(fill="x")
    tk.Label(_results_card, text="Load a strategy to see comparison.",
             font=("Segoe UI", 9, "italic"), bg=WHITE, fg=GREY).pack(anchor="w")

    # ── Action buttons ────────────────────────────────────────────────────────
    actions = tk.Frame(sf, bg=BG, pady=6)
    actions.pack(fill="x", padx=5)

    tk.Button(actions, text="Apply Filters & View Trades",
              command=lambda: _display_trade_list(_filtered_trades, _trade_list_frame),
              bg="#667eea", fg="white", font=("Segoe UI", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=18, pady=8).pack(side=tk.LEFT, padx=(5, 8))

    tk.Button(actions, text="📥 Export Filtered Trades CSV",
              command=lambda: _export_csv(_filtered_trades),
              bg=GREEN, fg="white", font=("Segoe UI", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=18, pady=8).pack(side=tk.LEFT)

    # ── Monthly P&L Chart ─────────────────────────────────────────────────────
    chart_outer = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    chart_outer.pack(fill="x", padx=5, pady=(10, 5))
    tk.Label(chart_outer, text="📊 Monthly P&L", font=("Segoe UI", 10, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    _monthly_chart_canvas = tk.Canvas(chart_outer, bg="#ffffff", height=200,
                                       highlightthickness=1, highlightbackground="#ddd")
    _monthly_chart_canvas.pack(fill="x", pady=5)

    # Tooltip label (hidden until hover) — must not block scrolling
    _monthly_tooltip = tk.Label(chart_outer, text="", font=("Arial", 9, "bold"),
                                 bg="#333333", fg="white", padx=8, pady=4)

    # Forward scroll from tooltip to main canvas
    def _tooltip_scroll(event):
        _monthly_tooltip.place_forget()
        _scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    _monthly_tooltip.bind("<MouseWheel>", _tooltip_scroll)

    # Draw placeholder
    _monthly_chart_canvas.create_text(200, 100, text="Load a strategy to see monthly P&L chart",
                                       font=("Arial", 11), fill="#888")

    # Redraw chart on canvas resize
    _monthly_chart_canvas.bind("<Configure>",
                                lambda e: _draw_monthly_chart(_monthly_chart_canvas, _monthly_tooltip, _filtered_trades))

    # ── Drawdown Analysis ─────────────────────────────────────────────────────
    dd_outer = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    dd_outer.pack(fill="x", padx=5, pady=(5, 5))
    tk.Label(dd_outer, text="📉 Drawdown Analysis", font=("Segoe UI", 10, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    _dd_label = tk.Label(dd_outer, text="Load a strategy to see drawdown analysis",
                          font=("Courier", 9), bg=WHITE, fg="#333",
                          justify=tk.LEFT, anchor="nw")
    _dd_label.pack(fill="x")

    # ── DD Breach Counter ─────────────────────────────────────────────────────
    breach_outer = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    breach_outer.pack(fill="x", padx=5, pady=(5, 5))
    tk.Label(breach_outer, text="💀 Prop Firm Breach Counter", font=("Segoe UI", 10, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    _breach_label = tk.Label(breach_outer, text="Load a strategy to see breach analysis",
                              font=("Courier", 9), bg=WHITE, fg="#333",
                              justify=tk.LEFT, anchor="nw")
    _breach_label.pack(fill="x")

    from shared.tooltip import add_tooltip
    add_tooltip(_breach_label,
                "💀 Prop Firm Breach Counter\n\n"
                "Simulates your strategy across the full backtest period.\n"
                "Every time drawdown exceeds the prop firm limit,\n"
                "the account is 'blown' and restarted — just like\n"
                "a real failed challenge.\n\n"
                "Daily DD breach: lost too much in ONE day\n"
                "Total DD breach: equity dropped too far from peak\n\n"
                "0 blows = strategy never violated limits\n"
                "1-3 blows = occasional, might pass with timing\n"
                "4+ blows = too risky for prop firms")

    # ── Trade list ────────────────────────────────────────────────────────────
    tl_hdr = tk.Frame(sf, bg=WHITE, padx=20, pady=6)
    tl_hdr.pack(fill="x", padx=5, pady=(5, 0))
    tk.Label(tl_hdr, text="📋 Filtered Trade List",
             font=("Segoe UI", 11, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")

    _trade_list_frame = tk.Frame(sf, bg=BG)
    _trade_list_frame.pack(fill="x", padx=5)
    tk.Label(_trade_list_frame,
             text="Click 'Apply Filters & View Trades' to populate.",
             font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=8)

    # ── Separator ─────────────────────────────────────────────────────────────
    tk.Frame(sf, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=12)

    # ── MODE 2: Deep Optimizer ────────────────────────────────────────────────
    opt_hdr = tk.Frame(sf, bg=WHITE, padx=20, pady=8)
    opt_hdr.pack(fill="x", padx=5, pady=(0, 5))
    tk.Label(opt_hdr, text="🧠 Deep Optimizer (10–30 min)",
             font=("Segoe UI", 12, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")
    tk.Label(opt_hdr,
             text="Tests filter combinations and scores them. Runs in background — UI stays responsive.",
             font=("Segoe UI", 9), bg=WHITE, fg=MIDGREY).pack(anchor="w", pady=(2, 0))

    # ── Optimizer Mode Description ────────────────────────────
    mode_desc_frame = tk.Frame(sf, bg="#fff3cd", padx=12, pady=8)
    mode_desc_frame.pack(fill="x", padx=10, pady=(0, 5))

    tk.Label(mode_desc_frame,
             text="🔬 Deep Optimizer — Work In Progress",
             font=("Segoe UI", 10, "bold"), bg="#fff3cd", fg="#856404").pack(anchor="w")
    tk.Label(mode_desc_frame,
             text="The optimizer tests different filter combinations and rule modifications\n"
                  "to find the best version of your strategy for a specific prop firm.\n"
                  "More optimization modes will be added over time.\n"
                  "Select one or both modes below:",
             font=("Segoe UI", 9), bg="#fff3cd", fg="#856404",
             justify=tk.LEFT).pack(anchor="w", pady=(3, 0))

    # ── Mode checkboxes ───────────────────────────────────────
    modes_frame = tk.LabelFrame(sf, text="Optimization Modes",
                                 font=("Segoe UI", 10, "bold"), bg=BG, fg=DARK,
                                 padx=12, pady=8)
    modes_frame.pack(fill="x", padx=10, pady=(0, 5))

    # Mode 1: Quick optimization (filter existing trades)
    _mode_quick_var = tk.BooleanVar(value=True)

    quick_cb = tk.Checkbutton(modes_frame,
        text="⚡ Quick Optimize — filter existing trades (seconds)",
        variable=_mode_quick_var,
        font=("Segoe UI", 9, "bold"), bg=BG, fg="#333",
        selectcolor=BG, activebackground=BG, anchor="w")
    quick_cb.pack(fill="x", pady=(0, 2))

    quick_desc = tk.Label(modes_frame,
        text="Uses only the indicators your current rules need. Tests session filters,\n"
             "max trades/day, cooldown, hold time. Very fast — finishes in seconds.",
        font=("Segoe UI", 8), bg=BG, fg="#888", justify=tk.LEFT)
    quick_desc.pack(fill="x", padx=(24, 0), pady=(0, 8))

    # Mode 2: Generate new trades (modify rules)
    _generate_new_var = tk.BooleanVar(value=False)

    generate_cb = tk.Checkbutton(modes_frame,
        text="🧬 Deep Explore — modify rules, find new entries (minutes)",
        variable=_generate_new_var,
        font=("Segoe UI", 9, "bold"), bg=BG, fg="#333",
        selectcolor=BG, activebackground=BG, anchor="w")
    generate_cb.pack(fill="x", pady=(0, 2))

    generate_desc = tk.Label(modes_frame,
        text="Loads the top 30 most important indicators from Project 1 analysis.\n"
             "Shifts thresholds ±10-20%, adds new conditions, removes weak ones.\n"
             "Re-runs backtests with each modification. Slower but finds NEW trade setups.",
        font=("Segoe UI", 8), bg=BG, fg="#888", justify=tk.LEFT)
    generate_desc.pack(fill="x", padx=(24, 0), pady=(0, 5))

    # ── Add hover tooltips with full details ──────────────────
    from shared.tooltip import add_tooltip

    # Build dynamic tooltip text showing actual indicators
    def _build_quick_tooltip():
        """Build tooltip showing which indicators quick mode uses."""
        text = (
            "⚡ QUICK OPTIMIZE MODE\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "What it does:\n"
            "  • Tests prop firm filter presets (FTMO, Topstep, Apex, etc.)\n"
            "  • Sweeps min hold time: 1, 2, 5, 10, 15, 20, 30 min\n"
            "  • Sweeps max trades/day: 1, 2, 3, 5, 8\n"
            "  • Tests session combos: London, NY, London+NY, Asian+London\n"
            "  • Tests combined filters: hold + max/day together\n\n"
            "Does NOT change:\n"
            "  • Entry rules — same conditions, same thresholds\n"
            "  • Exit strategy — same SL/TP\n"
            "  • Indicators used — no new ones added\n\n"
            "Speed: ~2-5 seconds\n"
            "Best for: fine-tuning a strategy that already works\n\n"
        )

        # Show which indicators the current rules use
        try:
            idx = _get_selected_index()
            if idx is not None:
                for s in _strategies:
                    if s['index'] == idx:
                        text += f"Current strategy: {s.get('rule_combo', '?')} × {s.get('exit_name', '?')}\n"
                        break
        except Exception:
            pass

        return text

    def _build_generate_tooltip():
        """Build tooltip showing which indicators generate mode explores."""
        text = (
            "🧬 DEEP EXPLORE MODE\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "What it does:\n"
            "  • Shifts each condition threshold ±10% and ±20%\n"
            "    Example: H4_adx_14 > 18.5 → tries > 16.7, > 14.8, > 20.4, > 22.2\n\n"
            "  • Adds NEW indicator conditions from the top 30 features\n"
            "    Example: adds 'D1_atr_50 > 12.5' if it improves win rate\n\n"
            "  • Removes weak conditions one by one\n"
            "    Example: drops 'M15_volume_change > -0.35' if it doesn't help\n\n"
            "  • Tests 5 exit strategies with each modified rule set:\n"
            "    - Fixed SL/TP: 150/300, 100/200, 200/400\n"
            "    - Trailing Stop: 100 pip trail, 50 pip trail\n\n"
            "Speed: 2-10 minutes (depends on number of rules)\n"
            "Best for: discovering new trading patterns\n\n"
        )

        # Show which indicators will be explored
        try:
            import json as _json
            report_path = os.path.join(project_root, 'project1_reverse_engineering',
                                        'outputs', 'analysis_report.json')
            if os.path.exists(report_path):
                with open(report_path) as f:
                    report = _json.load(f)

                # Current rules' indicators
                win_rules = [r for r in report.get('rules', []) if r.get('prediction') == 'WIN']
                current_features = set()
                for r in win_rules:
                    for c in r.get('conditions', []):
                        current_features.add(c['feature'])

                text += f"CURRENT rules use {len(current_features)} indicators:\n"
                for feat in sorted(current_features)[:10]:
                    text += f"  • {feat}\n"
                if len(current_features) > 10:
                    text += f"  ... +{len(current_features) - 10} more\n"

                # Top features from importance ranking
                top_features = report.get('feature_importance', {}).get('top_20', [])
                if top_features:
                    text += f"\nTOP features to explore (from Project 1):\n"
                    for feat, score in top_features[:15]:
                        already = "✓ (in rules)" if feat in current_features else "NEW"
                        text += f"  • {feat}  [{already}]\n"
                    if len(top_features) > 15:
                        text += f"  ... +{len(top_features) - 15} more\n"

                text += f"\nRules that will be modified:\n"
                for i, r in enumerate(win_rules[:5]):
                    wr = r.get('win_rate', 0)
                    wr_str = f"{wr:.0%}" if wr <= 1 else f"{wr:.0f}%"
                    conds = [c['feature'] for c in r.get('conditions', [])]
                    text += f"  Rule {i+1} (WR {wr_str}): {', '.join(conds[:3])}\n"
                if len(win_rules) > 5:
                    text += f"  ... +{len(win_rules) - 5} more rules\n"
        except Exception:
            text += "  (Load a strategy to see which indicators will be explored)\n"

        return text

    # Apply tooltips
    add_tooltip(quick_cb, _build_quick_tooltip(), wraplength=450)
    add_tooltip(quick_desc, _build_quick_tooltip(), wraplength=450)
    add_tooltip(generate_cb, _build_generate_tooltip(), wraplength=450)
    add_tooltip(generate_desc, _build_generate_tooltip(), wraplength=450)

    opt_controls = tk.Frame(sf, bg=WHITE, padx=20, pady=8)
    opt_controls.pack(fill="x", padx=5, pady=(0, 5))

    ctrl_row = tk.Frame(opt_controls, bg=WHITE)
    ctrl_row.pack(fill="x", pady=(0, 8))

    # Firm selector
    tk.Label(ctrl_row, text="Target firm:", font=("Segoe UI", 9), bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(0, 8))

    firm_options = ["None — maximize pips", "FTMO", "Topstep", "Apex", "FundedNext", "The5ers", "Get Leveraged"]
    _opt_target_var = tk.StringVar(value=firm_options[0])
    ttk.Combobox(ctrl_row, textvariable=_opt_target_var,
                 values=firm_options, state="readonly", width=25).pack(side=tk.LEFT, padx=(0, 15))

    # Stage selector
    tk.Label(ctrl_row, text="Stage:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg="#333").pack(side=tk.LEFT, padx=(15, 5))

    _stage_var = tk.StringVar(value="Funded")
    stage_combo = ttk.Combobox(ctrl_row, textvariable=_stage_var,
                                values=["Evaluation", "Funded"], width=12, state="readonly")
    stage_combo.pack(side=tk.LEFT, padx=(0, 10))

    stage_info = tk.Label(ctrl_row, text="", font=("Segoe UI", 8), bg=WHITE, fg="#888")
    stage_info.pack(side=tk.LEFT, padx=(0, 10))

    def _on_stage_change(*_):
        stage = _stage_var.get()
        firm = _opt_target_var.get() if _opt_target_var else ""

        # Load trading_rules for this firm
        presets = get_prop_firm_presets()
        preset = presets.get(firm, {})
        firm_data = preset.get('firm_data')
        trading_rules = firm_data.get('trading_rules', []) if firm_data else []

        if stage == "Evaluation":
            stage_info.config(
                text="🎯 Goal: hit profit target fast. No consistency rule. Higher risk OK.",
                fg="#e67e22")
            # Auto-set risk from eval trading_rules
            for rule in trading_rules:
                if rule.get('stage') == 'evaluation' and rule.get('type') == 'eval_settings':
                    params = rule.get('parameters', {})
                    risk_range = params.get('risk_pct_range', [0.8, 1.5])
                    if _risk_var:
                        _risk_var.set(str(risk_range[0]))  # use lower bound
                    break
            else:
                # No firm-specific eval rules — default aggressive
                if _risk_var:
                    _risk_var.set("1.0")
        else:
            stage_info.config(
                text="🛡️ Goal: survive + payouts. 2 wins/day cap. Stop after conditions met.",
                fg="#28a745")
            # Auto-set risk from funded trading_rules
            for rule in trading_rules:
                if rule.get('stage') == 'funded' and rule.get('type') == 'funded_accumulate':
                    params = rule.get('parameters', {})
                    risk_range = params.get('risk_pct_range', [0.3, 0.5])
                    if _risk_var:
                        _risk_var.set(str(risk_range[0]))  # use lower bound (safest)
                    break
            else:
                # No firm-specific funded rules — default conservative
                if _risk_var:
                    _risk_var.set("0.5")

    _stage_var.trace_add("write", _on_stage_change)
    _on_stage_change()  # Initial update

    # ── Account size + risk row ──
    acct_row = tk.Frame(sf, bg=WHITE)
    acct_row.pack(fill="x", padx=10, pady=(0, 5))

    tk.Label(acct_row, text="Account:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg="#333").pack(side=tk.LEFT)

    _acct_var = tk.StringVar(value="100000")
    _acct_combo = ttk.Combobox(acct_row, textvariable=_acct_var,
                                values=["10000", "25000", "50000", "100000", "200000"],
                                width=10)
    _acct_combo.pack(side=tk.LEFT, padx=5)

    tk.Label(acct_row, text="Risk:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg="#333").pack(side=tk.LEFT, padx=(15, 0))

    _risk_var = tk.StringVar(value="1.0")
    tk.Entry(acct_row, textvariable=_risk_var, width=5, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=5)
    tk.Label(acct_row, text="%/trade", font=("Segoe UI", 9), bg=WHITE, fg="#555").pack(side=tk.LEFT)

    # Account info label
    _acct_info = tk.Label(acct_row, text="", font=("Segoe UI", 8), bg=WHITE, fg="#888")
    _acct_info.pack(side=tk.LEFT, padx=(15, 0))

    # Auto-update account sizes when firm changes
    def _on_firm_change_acct(*_):
        firm = _opt_target_var.get() if _opt_target_var else ""
        presets = get_prop_firm_presets()
        preset = presets.get(firm, {})
        firm_data = preset.get('firm_data')
        if firm_data:
            sizes = firm_data['challenges'][0].get('account_sizes', [100000])
            _acct_combo['values'] = [str(s) for s in sizes]
            if sizes and _acct_var.get() not in [str(s) for s in sizes]:
                _acct_var.set(str(sizes[-1]))

            funded = firm_data['challenges'][0].get('funded', {})
            daily = funded.get('max_daily_drawdown_pct', 5)
            total = funded.get('max_total_drawdown_pct', 10)
            dd_type = funded.get('drawdown_type', 'static')
            leverage = firm_data.get('leverage_by_size', {})
            lev = leverage.get(_acct_var.get(), list(leverage.values())[0] if leverage else '—')
            _acct_info.config(text=f"DD: {daily}%/{total}% {dd_type} | Leverage: {lev}")

        # Also update risk based on stage + firm
        _on_stage_change()

    _opt_target_var.trace_add("write", _on_firm_change_acct)
    _acct_var.trace_add("write", lambda *_: _on_firm_change_acct())

    _opt_start_btn = tk.Button(ctrl_row, text="Start Deep Optimization",
                               command=_start_optimization,
                               bg="#667eea", fg="white", font=("Segoe UI", 10, "bold"),
                               relief=tk.FLAT, cursor="hand2", padx=18, pady=7)
    _opt_start_btn.pack(side=tk.LEFT, padx=(0, 8))

    _opt_stop_btn = tk.Button(ctrl_row, text="Stop",
                              command=_stop_optimization,
                              bg=RED, fg="white", font=("Segoe UI", 10, "bold"),
                              relief=tk.FLAT, cursor="hand2", padx=12, pady=7,
                              state="disabled")
    _opt_stop_btn.pack(side=tk.LEFT)

    _opt_status_lbl = tk.Label(opt_controls, text="Ready",
                               font=("Segoe UI", 9, "italic"), bg=WHITE, fg=GREY)
    _opt_status_lbl.pack(anchor="w")

    # Firm rules reminder
    from shared.firm_rules_reminder import show_reminder_on_firm_change

    _reminder = [None]
    show_reminder_on_firm_change(_opt_target_var, sf, _reminder, _stage_var)

    # Live progress box
    prog_box = tk.Frame(sf, bg="#1a1a2a", padx=16, pady=12)
    prog_box.pack(fill="x", padx=5, pady=(0, 5))

    def _live_lbl(key, text, font_size=9, bold=False, color="white"):
        lbl = tk.Label(prog_box, text=text,
                       font=("Segoe UI", font_size, "bold" if bold else "normal"),
                       bg="#1a1a2a", fg=color, anchor="w")
        lbl.pack(anchor="w", pady=1)
        _opt_live_labels[key] = lbl

    _live_lbl("msg",     "Waiting to start...", 9, False, "#aaaacc")
    _live_lbl("progress","",                    8, False, "#8888aa")
    tk.Frame(prog_box, bg="#333355", height=1).pack(fill="x", pady=4)
    tk.Label(prog_box, text="🏆 Current Best Found:",
             font=("Segoe UI", 9, "bold"), bg="#1a1a2a", fg="#ffd700").pack(anchor="w")
    _live_lbl("best_name",  "—", 10, True,  "white")
    _live_lbl("best_stats", "—", 9,  False, "#88ddaa")
    tk.Frame(prog_box, bg="#333355", height=1).pack(fill="x", pady=4)
    _live_lbl("counters", "Tested: 0  |  Improvements: 0  |  Elapsed: 0m 0s",
              8, False, "#aaaacc")

    # Optimizer results
    _opt_results_frame = tk.Frame(sf, bg=BG)
    _opt_results_frame.pack(fill="x", padx=5, pady=(0, 20))

    return panel


def refresh():
    global _strategies, _strategy_var
    _load_strategies()
    if _strategy_var is not None and _strategies:
        labels = [s['label'] for s in _strategies]
        if _strategy_var.get() not in labels:
            _strategy_var.set(labels[0])

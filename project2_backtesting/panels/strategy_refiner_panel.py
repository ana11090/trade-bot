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

def _load_strategies():
    global _strategies
    try:
        from project2_backtesting.strategy_refiner import load_strategy_list
        _strategies = load_strategy_list()
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
_generate_new_var  = None


def _start_optimization():
    global _opt_start_btn, _opt_stop_btn, _opt_status_lbl

    if not _base_trades:
        messagebox.showerror("No Data", "Load a strategy first.")
        return

    _opt_start_btn.configure(state="disabled")
    _opt_stop_btn.configure(state="normal")
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
        pct = int(step / max(total, 1) * 100)

        def _update():
            if _opt_live_labels:
                _opt_live_labels.get('msg',     tk.Label()).configure(text=message)
                _opt_live_labels.get('progress',tk.Label()).configure(
                    text=f"Step {step}/{total}  ({pct}%)")
                _opt_live_labels.get('best_name', tk.Label()).configure(
                    text=current_best.get('name', '—'))
                _opt_live_labels.get('best_stats', tk.Label()).configure(
                    text=f"{current_best.get('trades',0)} trades  |  "
                         f"WR {current_best.get('win_rate',0)*100:.1f}%  |  "
                         f"avg {current_best.get('avg_pips',0):+.1f} pips  |  "
                         f"{current_best.get('trades_per_day',0):.1f}/day")
                _opt_live_labels.get('counters', tk.Label()).configure(
                    text=f"Tested: {candidates_tested}  |  "
                         f"Improvements: {improvements_found}  |  "
                         f"Elapsed: {elapsed_str}")
        state.window.after(0, _update)

    def _worker():
        try:
            current_trades = list(_base_trades)
            current_filters = _get_current_filters()

            # Spread/commission from selected strategy metadata
            spread_pips = 2.5
            commission_pips = 0.0
            idx = _get_selected_index()
            if idx is not None:
                for s in _strategies:
                    if s['index'] == idx:
                        spread_pips = s.get('spread_pips', 2.5)
                        commission_pips = s.get('commission_pips', 0.0)
                        break

            if _generate_new_var and _generate_new_var.get():
                import json as _json
                from project2_backtesting.strategy_refiner import deep_optimize_generate

                rules_path = os.path.join(
                    project_root, 'project1_reverse_engineering', 'outputs', 'analysis_report.json'
                )
                if not os.path.exists(rules_path):
                    state.window.after(0, lambda: _opt_status_lbl.configure(
                        text="Error: analysis_report.json not found. Run project 1 first.", fg=RED))
                    return

                with open(rules_path) as f:
                    report = _json.load(f)
                base_rules = [r for r in report.get('rules', []) if r.get('prediction') == 'WIN']

                candles_path = None
                for p in [
                    os.path.join(project_root, 'data', 'xauusd_H1.csv'),
                    os.path.join(project_root, 'data', 'xauusd', 'H1.csv'),
                ]:
                    if os.path.exists(p):
                        candles_path = p
                        break

                if not candles_path:
                    state.window.after(0, lambda: _opt_status_lbl.configure(
                        text="Error: H1 candle CSV not found in data/ folder.", fg=RED))
                    return

                feature_matrix_path = os.path.join(
                    project_root, 'project1_reverse_engineering', 'outputs', 'feature_matrix.csv'
                )

                results = deep_optimize_generate(
                    trades=current_trades,
                    base_rules=base_rules,
                    candles_path=candles_path,
                    timeframe='H1',
                    spread_pips=spread_pips,
                    commission_pips=commission_pips,
                    target_firm=target_firm,
                    account_size=100000,
                    filters=current_filters if current_filters else None,
                    progress_callback=_cb,
                    feature_matrix_path=feature_matrix_path,
                )
            else:
                from project2_backtesting.strategy_refiner import deep_optimize
                results = deep_optimize(
                    trades=current_trades,
                    candles_df=None,
                    indicators_df=None,
                    base_rules=[],
                    exit_strategies=[],
                    target_firm=target_firm,
                    account_size=100000,
                    progress_callback=_cb,
                )

            state.window.after(0, lambda: _show_opt_results(results))
            state.window.after(0, lambda: _opt_status_lbl.configure(
                text=f"Complete — {len(results)} candidates found", fg=GREEN))
        except Exception as e:
            import traceback; traceback.print_exc()
            state.window.after(0, lambda: _opt_status_lbl.configure(
                text=f"Error: {e}", fg=RED))
        finally:
            state.window.after(0, lambda: _opt_start_btn.configure(state="normal"))
            state.window.after(0, lambda: _opt_stop_btn.configure(state="disabled"))

    threading.Thread(target=_worker, daemon=True).start()


def _stop_optimization():
    from project2_backtesting.strategy_refiner import stop_optimization
    stop_optimization()
    if _opt_status_lbl:
        _opt_status_lbl.configure(text="Stopped by user", fg=AMBER)


def _show_opt_results(candidates):
    if _opt_results_frame is None:
        return
    for w in _opt_results_frame.winfo_children():
        w.destroy()

    if not candidates:
        tk.Label(_opt_results_frame, text="No candidates found.",
                font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=10)
        return

    tk.Label(_opt_results_frame,
             text=f"Top {len(candidates)} candidates (sorted by score):",
             font=("Segoe UI", 10, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(4, 6))

    from project2_backtesting.strategy_refiner import compute_stats_summary

    for i, cand in enumerate(candidates, 1):
        stats = cand.get('stats') or compute_stats_summary(cand.get('trades', []))
        card = tk.Frame(_opt_results_frame, bg=WHITE,
                        highlightbackground="#d0d0d0", highlightthickness=1,
                        padx=12, pady=8)
        card.pack(fill="x", padx=5, pady=3)

        name_row = tk.Frame(card, bg=WHITE)
        name_row.pack(fill="x")
        tk.Label(name_row, text=f"#{i}: {cand['name']}",
                 font=("Segoe UI", 10, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT)

        changes = cand.get('changes_from_base', '')
        if changes:
            tk.Label(card, text=f"Changes: {changes}",
                     font=("Segoe UI", 8, "italic"), bg=WHITE, fg=MIDGREY).pack(anchor="w", pady=(2, 0))

        wr_color = GREEN if stats['win_rate'] >= 0.60 else (AMBER if stats['win_rate'] >= 0.50 else RED)
        tk.Label(card,
                 text=f"{stats['count']} trades  |  WR {stats['win_rate']*100:.1f}%  |  "
                      f"avg {stats['avg_pips']:+.1f} pips  |  {stats['trades_per_day']:.1f}/day  |  "
                      f"total {stats['total_pips']:+.0f} pips",
                 font=("Segoe UI", 9), bg=WHITE, fg=wr_color).pack(anchor="w", pady=(2, 4))

        btn_row = tk.Frame(card, bg=WHITE)
        btn_row.pack(anchor="w")

        trades_snap = list(cand.get('trades', []))

        tk.Button(btn_row, text="View Trades",
                  command=lambda t=trades_snap: _show_candidate_trades(t),
                  bg="#667eea", fg="white", font=("Segoe UI", 8, "bold"),
                  relief=tk.FLAT, cursor="hand2", padx=10, pady=3).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(btn_row, text="Export CSV",
                  command=lambda t=trades_snap: _export_csv(t),
                  bg=GREEN, fg="white", font=("Segoe UI", 8, "bold"),
                  relief=tk.FLAT, cursor="hand2", padx=10, pady=3).pack(side=tk.LEFT, padx=(0, 6))


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
                                  daily_dd_limit_pct=5.0, total_dd_limit_pct=10.0)

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
    global _opt_status_lbl, _opt_start_btn, _opt_stop_btn, _opt_target_var
    global _scroll_canvas, _generate_new_var

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

    opt_controls = tk.Frame(sf, bg=WHITE, padx=20, pady=8)
    opt_controls.pack(fill="x", padx=5, pady=(0, 5))

    _generate_new_var = tk.BooleanVar(value=False)
    generate_cb = tk.Checkbutton(
        opt_controls,
        text="Generate new trades (modifies rules, runs new backtests — slower but finds new entries)",
        variable=_generate_new_var,
        bg=WHITE, font=("Segoe UI", 9),
        anchor="w",
    )
    generate_cb.pack(fill="x", pady=(0, 10))

    ctrl_row = tk.Frame(opt_controls, bg=WHITE)
    ctrl_row.pack(fill="x", pady=(0, 8))
    tk.Label(ctrl_row, text="Target firm:", font=("Segoe UI", 9), bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(0, 8))

    firm_options = ["None — maximize pips", "FTMO", "Topstep", "Apex", "FundedNext", "The5ers"]
    _opt_target_var = tk.StringVar(value=firm_options[0])
    ttk.Combobox(ctrl_row, textvariable=_opt_target_var,
                 values=firm_options, state="readonly", width=25).pack(side=tk.LEFT, padx=(0, 20))

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

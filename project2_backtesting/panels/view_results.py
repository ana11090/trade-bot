"""
Project 2 - View Results Panel
View backtest results and HTML report
"""

# WHY (Phase 33 Fix 1b): Per-row dollar math needs an SL fallback when
#      a strategy row lacks explicit sl_pips. Old code hardcoded 150
#      (XAUUSD). Load from saved config on module import so every
#      _build_card call uses the current instrument's SL default.
#      Falls back to 150 only if config load fails.
# CHANGED: April 2026 — Phase 33 Fix 1b — config-loaded SL fallback
_vr_fallback_sl_pips = 150.0
try:
    from project2_backtesting.panels.configuration import load_config as _vr_load_config
    _vr_cfg = _vr_load_config()
    # default_sl_pips isn't in DEFAULTS but saved configs may have it;
    # fall back to 150.0 explicitly
    _vr_fallback_sl_pips = float(_vr_cfg.get('default_sl_pips', 150.0))
except Exception:
    _vr_fallback_sl_pips = 150.0

import tkinter as tk
from tkinter import scrolledtext, messagebox
import os
import sys
import webbrowser
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Module-level variables
_output_text = None
_summary_frame = None
_sort_key = ['net_total_pips']  # default sort
_sort_reverse = [True]
_current_page = [0]   # pagination: 0-based page index
_PAGE_SIZE = 100      # results per page

# WHY (Phase A.44): Filter state must survive display_summary re-calls
#      (sort buttons, filter changes all call display_summary which
#      destroys and recreates all widgets). Same pattern as _sort_key.
# CHANGED: April 2026 — Phase A.44
_a44_state = {
    'exit_filter':   'All',
    'profit_filter': 'all',
    'min_trades': '',
    'min_wr':     '',
    'min_pf':     '',
    'max_dd':     '',
    'tf_filter':  'All TFs',
    'show_zero':  False,
}


# WHY: Used by both display_summary() and _display_results_inner().
#      Was originally a nested function inside display_summary() but
#      _display_results_inner() is a sibling scope and crashed with NameError.
# CHANGED: April 2026 — promoted to module level; explicit params (no closure)
def _calc_dollar_per_pip(strategy_sl_pips, risk_dollars, pip_value):
    """Return $ per pip for a strategy given its SL distance and risk model."""
    sl  = strategy_sl_pips if strategy_sl_pips and strategy_sl_pips > 0 else 150
    lot = max(0.01, risk_dollars / (sl * pip_value))
    return pip_value * lot


def load_summary_stats():
    """Load backtest matrix results from strategy_backtester output"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    matrix_file = os.path.join(project_root, 'project2_backtesting/outputs/backtest_matrix.json')

    if not os.path.exists(matrix_file):
        return None

    try:
        import json
        with open(matrix_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"Error loading backtest matrix: {e}")
        return None


def open_html_report():
    """Open the HTML report in browser"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    report_file = os.path.join(project_root, 'project2_backtesting/outputs/backtest_report.html')

    if not os.path.exists(report_file):
        messagebox.showerror(
            "Report Not Found",
            "HTML report not found!\n\n"
            "Please run the backtest first."
        )
        return

    # Open in browser
    try:
        webbrowser.open(f'file:///{report_file}')
    except Exception as e:
        messagebox.showerror(
            "Error Opening Report",
            f"Failed to open report:\n{str(e)}"
        )


def open_output_folder():
    """Open the outputs folder in file explorer"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    outputs_folder = os.path.join(project_root, 'project2_backtesting/outputs')

    if not os.path.exists(outputs_folder):
        messagebox.showwarning(
            "Folder Not Found",
            "Outputs folder not found!\n\n"
            "Please run the backtest first."
        )
        return

    # Open folder
    try:
        if sys.platform == 'win32':
            os.startfile(outputs_folder)
        elif sys.platform == 'darwin':  # macOS
            os.system(f'open "{outputs_folder}"')
        else:  # Linux
            os.system(f'xdg-open "{outputs_folder}"')
    except Exception as e:
        messagebox.showerror(
            "Error Opening Folder",
            f"Failed to open folder:\n{str(e)}"
        )


def display_summary(output_text, summary_frame):
    """Display ALL backtest results as sortable cards."""
    # Read account size from config for % calculations
    try:
        from project2_backtesting.panels.configuration import load_config
        cfg = load_config()
        account_size = float(cfg.get('starting_capital', '100000'))
        risk_pct = float(cfg.get('risk_pct', '1.0'))
        pip_value = float(cfg.get('pip_value_per_lot', '10.0'))
    except Exception:
        account_size = 100000
        risk_pct = 1.0
        pip_value = 10.0

    # WHY: Each strategy can have a different SL distance. Hardcoding 150 made
    #      the dollar display wrong for any strategy with SL ≠ 150.
    # CHANGED: April 2026 — per-strategy lot sizing via module-level helper
    risk_dollars   = account_size * (risk_pct / 100)
    dollar_per_pip = _calc_dollar_per_pip(150, risk_dollars, pip_value)

    for widget in summary_frame.winfo_children():
        widget.destroy()

    try:
        data = load_summary_stats()
        if data is None:
            tk.Label(summary_frame, text="No backtest results found. Run the backtest first.",
                     font=("Arial", 10, "italic"), bg="#ffffff", fg="#999").pack(pady=20)
            output_text.delete(1.0, tk.END)
            output_text.insert(tk.END, "No backtest results available.\n\nRun the backtest first.")
            return

        # WHY (Phase A.48 fix): The per-TF backtester saves under "results"
        #      but the combined multi-TF save writes under "matrix". Accept
        #      either key so both single-TF and multi-TF runs display.
        # CHANGED: April 2026 — Phase A.48
        results = data.get('results', []) or data.get('matrix', [])
        if not results:
            output_text.delete(1.0, tk.END)
            output_text.insert(tk.END, "Backtest matrix is empty. Re-run the backtest.\n")
            return

        _display_results_inner(output_text, summary_frame, data, results,
                               account_size, risk_pct, pip_value, dollar_per_pip,
                               risk_dollars)

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"[VIEW RESULTS] ERROR in display_summary: {e}")
        traceback.print_exc()
        tk.Label(summary_frame,
                 text=f"Error displaying results:\n\n{e}\n\nCheck terminal for full traceback.",
                 font=("Arial", 10), bg="#ffffff", fg="#dc3545",
                 wraplength=600, justify="left").pack(pady=20, padx=20)
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, f"Error:\n{err}")
        return



def _display_results_inner(output_text, summary_frame, data, results,
                           account_size, risk_pct, pip_value, dollar_per_pip,
                           risk_dollars=None):
    """Inner display logic — separated so errors are caught by display_summary."""
    if risk_dollars is None:
        risk_dollars = account_size * (risk_pct / 100)
    # ── Header info ──
    info_frame = tk.Frame(summary_frame, bg="#e8f5e9", padx=15, pady=10)
    info_frame.pack(fill="x", padx=10, pady=(0, 5))

    combos = data.get('combinations', len(results))
    elapsed = data.get('elapsed_seconds', 0)
    spread = data.get('spread_pips', 0)
    gen_at = data.get('generated_at', '?')
    with_trades = sum(1 for r in results if r.get('total_trades', 0) > 0)

    tk.Label(info_frame, text=f"Backtest Matrix — {combos} combinations ({with_trades} with trades)",
             bg="#e8f5e9", fg="#2e7d32", font=("Arial", 11, "bold")).pack(anchor="w")
    tk.Label(info_frame, text=f"Generated: {gen_at}  |  Spread: {spread} pips  |  Time: {elapsed:.0f}s",
             bg="#e8f5e9", fg="#555", font=("Arial", 9)).pack(anchor="w")
    tk.Label(info_frame, text=f"Account: ${account_size:,.0f}  |  Risk: {risk_pct}%/trade  |  "
                               f"${dollar_per_pip:.2f}/pip",
             bg="#e8f5e9", fg="#555", font=("Arial", 9)).pack(anchor="w")

    # ── Sort buttons ──
    sort_frame = tk.Frame(summary_frame, bg="#ffffff")
    sort_frame.pack(fill="x", padx=10, pady=(5, 5))

    tk.Label(sort_frame, text="Sort by:", font=("Arial", 9, "bold"),
             bg="#ffffff", fg="#555").pack(side=tk.LEFT)

    def _resort(key, reverse=True):
        _sort_key[0] = key
        _sort_reverse[0] = reverse
        _current_page[0] = 0  # reset to first page on sort change
        display_summary(output_text, summary_frame)

    for label, key, rev in [
        ("Net Pips ↓", "net_total_pips", True),
        ("Win Rate ↓", "win_rate", True),
        ("Profit Factor ↓", "net_profit_factor", True),
        ("Max DD ↑ (lowest first)", "max_dd_pips", False),
        ("Trades ↓", "total_trades", True),
        ("Avg Pips ↓", "net_avg_pips", True),
    ]:
        tk.Button(sort_frame, text=label, font=("Arial", 8),
                  bg="#667eea", fg="white", relief=tk.FLAT, padx=6, pady=2,
                  command=lambda k=key, r=rev: _resort(k, r)).pack(side=tk.LEFT, padx=2)

    # ── Filter: hide 0-trade results ──
    show_zero_var = tk.BooleanVar(value=_a44_state['show_zero'])

    def _toggle_show_zero():
        _a44_state['show_zero'] = show_zero_var.get()
        _current_page[0] = 0
        display_summary(output_text, summary_frame)

    tk.Checkbutton(sort_frame, text="Show 0-trade results", variable=show_zero_var,
                    bg="#ffffff", font=("Arial", 8),
                    command=_toggle_show_zero).pack(side=tk.RIGHT)

    # ── TF filter (only shown when multiple TFs present) ──
    # WHY: Multi-TF backtest produces rows for M5/M15/H1/H4 — user needs to filter
    #      to a single TF or view all.
    # CHANGED: April 2026 — multi-TF support
    all_tfs = sorted(set(r.get('entry_tf', '') for r in results if r.get('entry_tf', '')))

    # WHY (Hotfix): TF filter was a local var that reset to 'All TFs' every
    #      time display_summary was called (e.g., on sort button click).
    #      Now reads/writes _a44_state so the selection persists.
    # CHANGED: April 2026 — persist TF filter
    if _a44_state['tf_filter'] not in (['All TFs'] + all_tfs):
        _a44_state['tf_filter'] = 'All TFs'
    tf_filter_var = tk.StringVar(value=_a44_state['tf_filter'])

    if len(all_tfs) > 1:
        tf_filter_frame = tk.Frame(sort_frame, bg="#ffffff")
        tf_filter_frame.pack(side=tk.RIGHT, padx=(0, 8))
        tk.Label(tf_filter_frame, text="TF:", font=("Arial", 8), bg="#ffffff", fg="#555").pack(side=tk.LEFT)
        tf_choices = ['All TFs'] + all_tfs

        def _on_tf_change(val):
            _a44_state['tf_filter'] = val
            _current_page[0] = 0
            display_summary(output_text, summary_frame)

        tf_menu = tk.OptionMenu(tf_filter_frame, tf_filter_var, *tf_choices,
                                command=_on_tf_change)
        tf_menu.config(font=("Arial", 8), bg="#fff", relief=tk.FLAT, padx=2, pady=1)
        tf_menu.pack(side=tk.LEFT)

    # ═══════════════════════════════════════════════════════════════════
    # Phase A.44: Enhanced filter controls
    # WHY (Phase A.44): With 60+ results (5 TFs × 12 exit strategies),
    #      the user needs to quickly narrow down to what matters. All
    #      filters are AND-combined. State lives in module-level
    #      _a44_state so it survives display_summary re-calls (sort
    #      buttons recreate all widgets from scratch).
    # CHANGED: April 2026 — Phase A.44
    # ═══════════════════════════════════════════════════════════════════
    filter_frame = tk.Frame(summary_frame, bg="#f0f2f5", padx=10, pady=6)
    filter_frame.pack(fill="x", padx=10, pady=(2, 5))

    # ── Row 1: Exit strategy dropdown + profitability radio ──
    row1 = tk.Frame(filter_frame, bg="#f0f2f5")
    row1.pack(fill="x", pady=(0, 4))

    tk.Label(row1, text="Exit Strategy:", font=("Segoe UI", 9),
             bg="#f0f2f5", fg="#333").pack(side=tk.LEFT)

    _all_exits = sorted(set(
        r.get('exit_name', r.get('exit_strategy', '?'))
        for r in results if r.get('total_trades', 0) > 0
    ))
    _exit_choices = ['All'] + _all_exits
    if _a44_state['exit_filter'] not in _exit_choices:
        _a44_state['exit_filter'] = 'All'
    exit_filter_var = tk.StringVar(value=_a44_state['exit_filter'])

    def _on_exit_change(val):
        _a44_state['exit_filter'] = val
        display_summary(output_text, summary_frame)

    # WHY: OptionMenu parent must equal its pack container — using
    #      filter_frame as parent then pack(in_=row1) is invalid in
    #      tkinter. Parent = row1 directly.
    exit_menu = tk.OptionMenu(row1, exit_filter_var, *_exit_choices,
                               command=_on_exit_change)
    exit_menu.config(font=("Segoe UI", 8), bg="#fff", relief=tk.FLAT, width=16)
    exit_menu.pack(side=tk.LEFT, padx=(4, 12))

    tk.Label(row1, text="Show:", font=("Segoe UI", 9),
             bg="#f0f2f5", fg="#333").pack(side=tk.LEFT, padx=(0, 4))

    profit_filter_var = tk.StringVar(value=_a44_state['profit_filter'])

    def _on_profit_change():
        _a44_state['profit_filter'] = profit_filter_var.get()
        display_summary(output_text, summary_frame)

    for _pf_text, _pf_val in [("All", "all"), ("Profitable only", "profit"), ("Losing only", "loss")]:
        tk.Radiobutton(
            row1, text=_pf_text, variable=profit_filter_var, value=_pf_val,
            bg="#f0f2f5", font=("Segoe UI", 8), activebackground="#f0f2f5",
            command=_on_profit_change,
        ).pack(side=tk.LEFT, padx=(0, 6))

    # ── Row 2: Numeric range filters ──
    row2 = tk.Frame(filter_frame, bg="#f0f2f5")
    row2.pack(fill="x")

    _a44_entry_vars = {}
    for _lbl, _key, _w in [
        ("Min trades:", "min_trades", 5),
        ("Min WR%:",    "min_wr",     5),
        ("Min PF:",     "min_pf",     5),
        ("Max DD pips:", "max_dd",    7),
    ]:
        tk.Label(row2, text=_lbl, font=("Segoe UI", 8),
                 bg="#f0f2f5", fg="#555").pack(side=tk.LEFT, padx=(6, 2))
        _var = tk.StringVar(value=_a44_state[_key])
        tk.Entry(row2, textvariable=_var, width=_w,
                 font=("Segoe UI", 8), relief=tk.SOLID, bd=1).pack(side=tk.LEFT, padx=(0, 4))
        _a44_entry_vars[_key] = _var

    def _apply_a44():
        for _k, _v in _a44_entry_vars.items():
            _a44_state[_k] = _v.get().strip()
        _current_page[0] = 0  # reset to first page on filter change
        display_summary(output_text, summary_frame)

    def _reset_a44():
        _a44_state['exit_filter']   = 'All'
        _a44_state['profit_filter'] = 'all'
        for _k in ('min_trades', 'min_wr', 'min_pf', 'max_dd'):
            _a44_state[_k] = ''
        _a44_state['tf_filter'] = 'All TFs'
        _a44_state['show_zero'] = False
        _current_page[0] = 0  # reset to first page on reset
        display_summary(output_text, summary_frame)

    tk.Button(row2, text="Apply", font=("Segoe UI", 8, "bold"),
              bg="#667eea", fg="white", relief=tk.FLAT, padx=8, pady=1,
              command=_apply_a44).pack(side=tk.LEFT, padx=(8, 0))
    tk.Button(row2, text="Reset", font=("Segoe UI", 8),
              bg="#ccc", fg="#333", relief=tk.FLAT, padx=8, pady=1,
              command=_reset_a44).pack(side=tk.LEFT, padx=(4, 0))

    # ── Sort results ──
    sorted_results = sorted(results, key=lambda r: r.get(_sort_key[0], 0), reverse=_sort_reverse[0])

    # Filter 0-trade if checkbox unchecked
    if not show_zero_var.get():
        sorted_results = [r for r in sorted_results if r.get('total_trades', 0) > 0]

    # Filter by TF if selected
    selected_tf = tf_filter_var.get()
    if selected_tf and selected_tf != 'All TFs':
        sorted_results = [r for r in sorted_results if r.get('entry_tf', '') == selected_tf]

    # ── Phase A.44 filters (read from _a44_state for persistence) ──
    _sel_exit = _a44_state['exit_filter']
    if _sel_exit and _sel_exit != 'All':
        sorted_results = [
            r for r in sorted_results
            if _sel_exit in (r.get('exit_name', ''), r.get('exit_strategy', ''))
        ]

    _sel_profit = _a44_state['profit_filter']
    if _sel_profit == 'profit':
        sorted_results = [r for r in sorted_results if r.get('net_total_pips', 0) > 0]
    elif _sel_profit == 'loss':
        sorted_results = [r for r in sorted_results if r.get('net_total_pips', 0) <= 0]

    def _safe_float_a44(key, default=None):
        try:
            val = _a44_state.get(key, '').strip()
            return float(val) if val else default
        except (ValueError, TypeError):
            return default

    _min_trades = _safe_float_a44('min_trades')
    _min_wr     = _safe_float_a44('min_wr')
    _min_pf     = _safe_float_a44('min_pf')
    _max_dd     = _safe_float_a44('max_dd')

    if _min_trades is not None:
        sorted_results = [r for r in sorted_results if r.get('total_trades', 0) >= _min_trades]
    if _min_wr is not None:
        sorted_results = [
            r for r in sorted_results
            if (r.get('win_rate', 0) if r.get('win_rate', 0) >= 1
                else r.get('win_rate', 0) * 100) >= _min_wr
        ]
    if _min_pf is not None:
        sorted_results = [r for r in sorted_results if r.get('net_profit_factor', 0) >= _min_pf]
    if _max_dd is not None:
        sorted_results = [r for r in sorted_results if abs(r.get('max_dd_pips', 0)) <= _max_dd]

    # ── Scrollable results area ──
    results_canvas = tk.Canvas(summary_frame, bg="#ffffff", highlightthickness=0)
    results_scroll = tk.Scrollbar(summary_frame, orient="vertical", command=results_canvas.yview)
    results_canvas.configure(yscrollcommand=results_scroll.set)
    results_scroll.pack(side=tk.RIGHT, fill="y")
    results_canvas.pack(fill="both", expand=True, padx=10)

    results_inner = tk.Frame(results_canvas, bg="#ffffff")
    results_wid = results_canvas.create_window((0, 0), window=results_inner, anchor="nw")
    results_inner.bind("<Configure>", lambda e: results_canvas.configure(scrollregion=results_canvas.bbox("all")))
    results_canvas.bind("<Configure>", lambda e: results_canvas.itemconfig(results_wid, width=e.width))

    def _on_enter(event):
        results_canvas.bind("<MouseWheel>",
            lambda e: results_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        results_canvas.bind("<Button-4>", lambda e: results_canvas.yview_scroll(-3, "units"))
        results_canvas.bind("<Button-5>", lambda e: results_canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        results_canvas.unbind("<MouseWheel>")
        results_canvas.unbind("<Button-4>")
        results_canvas.unbind("<Button-5>")

    results_canvas.bind("<Enter>", _on_enter)
    results_canvas.bind("<Leave>", _on_leave)

    # ═══════════════════════════════════════════════════════════════════
    # Hotfix: Pagination — show 100 results per page
    # WHY: 480+ cards × ~15 widgets = 7200+ tkinter widgets → app freeze.
    #      Show one page at a time with navigation buttons.
    # CHANGED: April 2026 — Hotfix for A.48 multi-TF freeze
    # ═══════════════════════════════════════════════════════════════════
    _total_results = len(sorted_results)
    _total_pages = max(1, (_total_results + _PAGE_SIZE - 1) // _PAGE_SIZE)

    # Clamp current page to valid range
    if _current_page[0] >= _total_pages:
        _current_page[0] = _total_pages - 1
    if _current_page[0] < 0:
        _current_page[0] = 0

    _page_start = _current_page[0] * _PAGE_SIZE
    _page_end = min(_page_start + _PAGE_SIZE, _total_results)
    _page_results = sorted_results[_page_start:_page_end]

    # ── Pagination bar (only if more than 1 page) ──
    if _total_pages > 1:
        page_bar = tk.Frame(results_inner, bg="#ffffff")
        page_bar.pack(fill="x", pady=(0, 5))

        tk.Label(page_bar,
                 text=f"Showing {_page_start + 1}–{_page_end} of {_total_results} results",
                 font=("Segoe UI", 9), bg="#ffffff", fg="#888").pack(side=tk.LEFT)

        # Page number buttons
        btn_frame = tk.Frame(page_bar, bg="#ffffff")
        btn_frame.pack(side=tk.RIGHT)

        def _go_to_page(p):
            _current_page[0] = p
            display_summary(output_text, summary_frame)

        # Previous button
        if _current_page[0] > 0:
            tk.Button(btn_frame, text="◀ Prev", font=("Segoe UI", 8),
                      bg="#667eea", fg="white", relief=tk.FLAT, padx=6, pady=1,
                      command=lambda: _go_to_page(_current_page[0] - 1)
                      ).pack(side=tk.LEFT, padx=1)

        # Page number buttons
        for _pg in range(_total_pages):
            _is_current = (_pg == _current_page[0])
            tk.Button(
                btn_frame,
                text=str(_pg + 1),
                font=("Segoe UI", 8, "bold" if _is_current else "normal"),
                bg="#667eea" if _is_current else "#e0e0e0",
                fg="white" if _is_current else "#333",
                relief=tk.FLAT, padx=6, pady=1,
                command=lambda p=_pg: _go_to_page(p),
            ).pack(side=tk.LEFT, padx=1)

        # Next button
        if _current_page[0] < _total_pages - 1:
            tk.Button(btn_frame, text="Next ▶", font=("Segoe UI", 8),
                      bg="#667eea", fg="white", relief=tk.FLAT, padx=6, pady=1,
                      command=lambda: _go_to_page(_current_page[0] + 1)
                      ).pack(side=tk.LEFT, padx=1)
    else:
        tk.Label(results_inner,
                 text=f"Showing {_total_results} of {len(results)} results",
                 font=("Segoe UI", 9), bg="#ffffff", fg="#888"
                 ).pack(anchor="w", pady=(0, 5))

    # ── Cache is_starred to avoid 100+ disk reads per page ──
    # WHY: is_starred() reads starred_strategies.json from disk on every
    #      call. Loading once and checking in-memory is instant.
    # CHANGED: April 2026 — Hotfix
    _starred_cache = set()
    try:
        from shared.starred import _load as _starred_load
        _starred_cache = set(_starred_load())
    except Exception:
        pass

    # ── Result cards (only current page) ──
    for i, r in enumerate(_page_results):
        # Adjust display index to be global (not per-page)
        _global_idx = _page_start + i
        # FIX 4: per-card error handling — one broken result doesn't kill the display
        try:
            net_pips = r.get('net_total_pips', 0)
            wr = r.get('win_rate', 0)
            pf = r.get('net_profit_factor', 0)
            trades = r.get('total_trades', 0)
            avg = r.get('net_avg_pips', 0)
            dd = r.get('max_dd_pips', 0)
            best = r.get('best_trade', 0)
            worst = r.get('worst_trade', 0)

            is_profitable = net_pips > 0 and trades > 0
            bg_color = "#f8fff8" if is_profitable else "#fff8f8" if trades > 0 else "#f5f5f5"
            border_color = "#28a745" if is_profitable else "#dc3545" if trades > 0 else "#ccc"

            card = tk.Frame(results_inner, bg=bg_color, highlightbackground=border_color,
                             highlightthickness=1, padx=12, pady=6)
            card.pack(fill="x", pady=2)

            header_row = tk.Frame(card, bg=bg_color)
            header_row.pack(fill="x")

            header_text = f"#{_global_idx+1}  {r.get('rule_combo', '?')}  ×  {r.get('exit_strategy', '?')}"
            tk.Label(header_row, text=header_text, bg=bg_color, fg="#333",
                     font=("Arial", 10, "bold")).pack(side=tk.LEFT)

            # TF badge — only shown when entry_tf is present
            # WHY: Multi-TF runs produce rows with different entry_tf values.
            #      Badge makes the TF immediately visible without reading the tooltip.
            # CHANGED: April 2026 — multi-TF support
            card_tf = r.get('entry_tf', '')
            if card_tf:
                tk.Label(header_row, text=f"[{card_tf}]", bg="#667eea", fg="white",
                         font=("Arial", 8, "bold"), padx=4, pady=1).pack(side=tk.LEFT, padx=(6, 0))

            # Run settings badges
            # WHY: Show at a glance what mode produced this result.
            # CHANGED: April 2026 — run settings badges
            _rs = r.get('run_settings', {})
            if _rs:
                _badges = []
                if _rs.get('regime_filter_enabled'):
                    _n_conds = len(_rs.get('regime_filter_conditions', []))
                    _badges.append((f'REGIME ({_n_conds})', '#9b59b6'))
                if _rs.get('multi_tf'):
                    _badges.append(('MULTI-TF', '#3498db'))
                if _rs.get('combine_all_rules'):
                    _badges.append(('ALL COMBOS', '#e67e22'))
                if _rs.get('use_config'):
                    _badges.append(('CONFIG', '#27ae60'))
                if not _rs.get('safety_stops', True):
                    _badges.append(('NO SAFETY', '#e74c3c'))
                _src = _rs.get('rule_source', '')
                if _src and _src != 'auto':
                    if 'Saved' in _src:
                        _badges.append(('SAVED RULES', '#8e44ad'))
                for _bt, _bc in _badges:
                    tk.Label(header_row, text=_bt, bg=_bc, fg="white",
                             font=("Arial", 7, "bold"), padx=3, pady=0
                             ).pack(side=tk.LEFT, padx=(3, 0))

            # Save button
            try:
                from shared.saved_rules import build_save_button
                # WHY (Fix 8): Old save_data was missing exit_class,
                #      exit_params, entry_timeframe, rules/conditions.
                #      The Refiner's stale check flagged every saved
                #      rule as missing data. Include all fields from
                #      the result dict that downstream tools need.
                # CHANGED: April 2026 — complete save data
                save_data = {
                    'rule_combo':        r.get('rule_combo', '?'),
                    'exit_strategy':     r.get('exit_strategy', '?'),
                    'exit_name':         r.get('exit_name', '?'),
                    'exit_class':        r.get('exit_class', ''),
                    'exit_params':       r.get('exit_params', {}),
                    'exit_strategy_params': r.get('exit_params', {}),
                    'prediction':        'WIN',
                    'win_rate':          wr,
                    'net_total_pips':    net_pips,
                    'net_profit_factor': pf,
                    'total_trades':      trades,
                    'max_dd_pips':       dd,
                    'entry_tf':          r.get('entry_tf', ''),
                    'entry_timeframe':   r.get('entry_tf', ''),
                    'rules':             r.get('rules', []),
                    'rule_indices':      r.get('rule_indices', []),
                    # WHY: Old code only saved the first rule's conditions.
                    #      For multi-rule strategies ("All rules combined"),
                    #      the other 8 rules' conditions were lost. Now
                    #      flatten ALL rules' conditions into the conditions list.
                    #      The 'rules' key already has the full rule dicts, so
                    #      'conditions' is used as a fallback by old code only.
                    # CHANGED: April 2026 — save all rules' conditions
                    'conditions':        [c for _rule in r.get('rules', [])
                                         for c in _rule.get('conditions', [])],
                    'spread_pips':       r.get('spread_pips', 2.5),
                    'commission_pips':   r.get('commission_pips', 0.0),
                    'direction':         r.get('direction', ''),
                    'run_settings':      r.get('run_settings', {}),
                    'signals_before_regime_filter': r.get('signals_before_regime_filter', 0),
                    'signals_after_regime_filter':  r.get('signals_after_regime_filter', 0),
                }
                # WHY: Hoist leverage/firm from run_settings to top-level so
                #      saved_rules.json carries them and EA generator/Saved
                #      Rules panel can read them without digging into run_settings.
                # CHANGED: April 2026 — leverage in save_data
                _sv_run = r.get('run_settings', {})
                save_data['leverage']      = _sv_run.get('leverage', 0)
                save_data['contract_size'] = _sv_run.get('contract_size', 100.0)
                save_data['firm_id']       = _sv_run.get('firm_id', '')
                save_data['firm_name']     = _sv_run.get('firm_name', '')

                # Embed regime filter conditions into each rule (Phase A.43)
                # WHY: Per-rule regime_filter key enables the backtester's
                #      Phase A.43 override. Without this, loading a saved
                #      rule with a different config changes regime behavior.
                # CHANGED: April 2026 — embed regime in saved rules
                _sv_rs = r.get('run_settings', {})
                _sv_rf = _sv_rs.get('regime_filter_conditions', [])
                if _sv_rf:
                    for _sv_rule in save_data.get('rules', []):
                        if 'regime_filter' not in _sv_rule:
                            _sv_rule['regime_filter'] = _sv_rf
                elif _sv_rs.get('regime_filter_enabled') is False:
                    # Explicitly mark filter as OFF so A.43 doesn't fall back to global
                    for _sv_rule in save_data.get('rules', []):
                        if 'regime_filter' not in _sv_rule:
                            _sv_rule['regime_filter'] = []

                sb = build_save_button(header_row, save_data, source="Backtest Result", bg=bg_color)
                sb.pack(side=tk.RIGHT, padx=3)
            except Exception:
                pass

            # Star button
            # WHY: Star your best strategies directly from View Results.
            #      Starred strategies show with ⭐ at top of all dropdowns.
            # CHANGED: April 2026 — star from View Results
            try:
                from shared.starred import toggle, is_starred
                rc = r.get('rule_combo', '?')
                es = r.get('exit_strategy', r.get('exit_name', '?'))
                etf = r.get('entry_tf', '')
                # Use cached starred list instead of per-card disk read
                try:
                    from shared.starred import make_key
                    _skey = make_key(rc, es, etf)
                    starred = _skey in _starred_cache or f"{rc}|{es}" in _starred_cache
                except Exception:
                    starred = False

                def _make_star_toggle(combo_name, exit_name, tf, btn_ref):
                    def _toggle():
                        from shared.starred import toggle as _t
                        new_state = _t(combo_name, exit_name, tf)
                        btn_ref[0].configure(
                            text="⭐" if new_state else "☆",
                            bg="#f39c12" if new_state else "#ddd",
                        )
                    return _toggle

                star_btn_ref = [None]
                star_btn_ref[0] = tk.Button(
                    header_row,
                    text="⭐" if starred else "☆",
                    command=_make_star_toggle(rc, es, etf, star_btn_ref),
                    bg="#f39c12" if starred else "#ddd",
                    fg="white" if starred else "#666",
                    font=("Segoe UI", 10), bd=0, padx=6, pady=1, cursor="hand2",
                )
                star_btn_ref[0].pack(side=tk.RIGHT, padx=3)
            except Exception:
                pass

            # ── Phase A.47 + A.48: Export Trades button ──────────────────
            # WHY (Phase A.48): Trades are no longer stored in
            #      backtest_matrix.json (too large). They're saved in
            #      separate per-TF files: backtest_trades_{TF}.json.
            #      The button loads trades from there on demand.
            # CHANGED: April 2026 — Phase A.48
            try:
                _a47_trade_count = r.get('trade_count', r.get('total_trades', 0))
                if _a47_trade_count and _a47_trade_count > 0:
                    def _make_export_fn(result_row, result_idx, all_results):
                        def _export():
                            try:
                                import csv
                                import subprocess

                                _out_dir = os.path.join(
                                    os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')),
                                    'project2_backtesting', 'outputs'
                                )

                                # Load trades from per-TF file
                                _entry_tf = result_row.get('entry_tf', 'H1')
                                _trades_file = os.path.join(_out_dir, f'backtest_trades_{_entry_tf}.json')

                                _trades = []
                                if os.path.exists(_trades_file):
                                    try:
                                        import json as _tj
                                        with open(_trades_file, 'r', encoding='utf-8') as _tf:
                                            _all_trades = _tj.load(_tf)
                                        # Find the matching combo index
                                        # The trades file keys are string indices from
                                        # the per-TF run. We need to match by combo name + exit.
                                        _target_combo = result_row.get('rule_combo', '')
                                        _target_exit = result_row.get('exit_strategy',
                                                        result_row.get('exit_name', ''))

                                        # Try direct index first (works for single-TF runs)
                                        _str_idx = str(result_idx)
                                        if _str_idx in _all_trades:
                                            _trades = _all_trades[_str_idx]
                                        else:
                                            # Search by combo+exit name match
                                            # Load the per-TF matrix to find the right index
                                            _tf_matrix_file = os.path.join(_out_dir, 'backtest_matrix.json')
                                            if os.path.exists(_tf_matrix_file):
                                                with open(_tf_matrix_file, 'r', encoding='utf-8') as _mf:
                                                    _matrix_data = _tj.load(_mf)
                                                _results = _matrix_data.get('results',
                                                            _matrix_data.get('matrix', []))
                                                # Find results for this TF
                                                _tf_results = [r2 for r2 in _results
                                                               if r2.get('entry_tf', '') == _entry_tf]
                                                for _ti, _tr in enumerate(_tf_results):
                                                    if (_tr.get('rule_combo', '') == _target_combo and
                                                        (_tr.get('exit_strategy', '') == _target_exit or
                                                         _tr.get('exit_name', '') == _target_exit)):
                                                        if str(_ti) in _all_trades:
                                                            _trades = _all_trades[str(_ti)]
                                                            break
                                    except Exception as _load_e:
                                        print(f"[A.47] Could not load trades from {_trades_file}: {_load_e}")

                                # Fallback: check if trades are inline (old format)
                                if not _trades:
                                    _trades = result_row.get('trades', [])

                                if not _trades:
                                    messagebox.showwarning(
                                        "No Trades Found",
                                        f"Could not find trade data for this result.\n"
                                        f"Looked in: {_trades_file}\n\n"
                                        f"Try re-running the backtest."
                                    )
                                    return

                                os.makedirs(_out_dir, exist_ok=True)
                                _combo = str(result_row.get('rule_combo', 'unknown'))
                                _exit = str(result_row.get('exit_name',
                                            result_row.get('exit_strategy', 'unknown')))
                                _clean_combo = _combo.replace(' ', '_').replace('/', '_')[:30]
                                _clean_exit = _exit.replace(' ', '_').replace('/', '_')[:20]
                                _tf_tag = f"_{_entry_tf}" if _entry_tf else ""
                                _fname = f"trades_{_clean_combo}_{_clean_exit}{_tf_tag}.csv"
                                _fpath = os.path.join(_out_dir, _fname)

                                if _trades:
                                    _keys = list(_trades[0].keys())
                                    with open(_fpath, 'w', newline='', encoding='utf-8') as _f:
                                        writer = csv.DictWriter(_f, fieldnames=_keys)
                                        writer.writeheader()
                                        for t in _trades:
                                            writer.writerow(t)

                                    messagebox.showinfo(
                                        "Trades Exported",
                                        f"Exported {len(_trades)} trades to:\n{_fpath}"
                                    )
                                    try:
                                        if sys.platform == 'win32':
                                            os.startfile(os.path.dirname(_fpath))
                                        elif sys.platform == 'darwin':
                                            subprocess.Popen(['open', os.path.dirname(_fpath)])
                                        else:
                                            subprocess.Popen(['xdg-open', os.path.dirname(_fpath)])
                                    except Exception:
                                        pass
                            except Exception as _e:
                                messagebox.showerror("Export Error", f"Could not export trades:\n{_e}")
                        return _export

                    # Find this result's index among results with the same TF
                    _a47_tf = r.get('entry_tf', '')
                    _a47_tf_idx = 0
                    for _si, _sr in enumerate(sorted_results[:i]):
                        if _sr.get('entry_tf', '') == _a47_tf:
                            _a47_tf_idx += 1

                    _a47_btn = tk.Button(
                        header_row,
                        text=f"📥 Trades ({_a47_trade_count})",
                        command=_make_export_fn(r, _a47_tf_idx, sorted_results),
                        font=("Segoe UI", 8),
                        bg="#17a2b8", fg="white",
                        relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
                    )
                    _a47_btn.pack(side=tk.RIGHT, padx=3)
            except Exception:
                pass

            if trades > 0:
                # WHY: compute_stats and strategy_backtester.compute_stats
                #      both always return win_rate as percent (0-100).
                #      The old heuristic `wr > 1 else wr * 100` had a
                #      discontinuity at exactly 1.0. Treat wr as percent
                #      when it's >= 1.0 (including the ambiguous 1.0
                #      case) and as fraction only when strictly less
                #      than 1.0. Matches Phase 31 Fix 7 convention.
                # CHANGED: April 2026 — Phase 33 Fix 3 — explicit boundary
                #          (audit Part C HIGH #86)
                wr_normalized = wr if wr >= 1.0 else wr * 100
                wr_str   = f"{wr_normalized:.1f}%"
                wr_color = "#28a745" if wr_normalized >= 55 else "#dc3545"
                pf_color = "#28a745" if pf >= 1.5 else "#dc3545" if pf < 1.0 else "#ff8f00"
                net_color = "#28a745" if net_pips > 0 else "#dc3545"

                # Use this strategy's actual SL for correct $/pip sizing
                # CHANGED: April 2026 — per-row dollar calc
                # WHY (Phase 33 Fix 1): Old fallback was hardcoded 150
                #      (XAUUSD). Non-XAUUSD users with strategies that
                #      didn't carry an explicit sl_pips got the XAUUSD
                #      value applied to their dollar math. Derive the
                #      fallback from the loaded config's default_sl_pips
                #      (populated at panel-build time below).
                # CHANGED: April 2026 — Phase 33 Fix 1 — config-driven SL fallback
                #          (audit Part C HIGH #84)
                strat_sl = (
                    r.get('sl_pips') or
                    r.get('exit_strategy_params', {}).get('sl_pips') or
                    _vr_fallback_sl_pips
                )
                this_dollar_per_pip = _calc_dollar_per_pip(strat_sl, risk_dollars, pip_value)
                profit_dollars = net_pips * this_dollar_per_pip
                profit_pct = (profit_dollars / account_size) * 100

                # FIX 3: safe trades access
                trade_pips = []
                try:
                    if 'trades' in r and r['trades']:
                        trade_pips = [t.get('net_pips', 0) for t in r['trades'] if isinstance(t, dict)]
                except Exception:
                    trade_pips = []

                import statistics
                try:
                    median_pips = statistics.median(trade_pips) if trade_pips else 0
                    avg_pips_calc = sum(trade_pips) / len(trade_pips) if trade_pips else avg
                except Exception:
                    median_pips = 0
                    avg_pips_calc = avg

                metrics_row = tk.Frame(card, bg=bg_color)
                metrics_row.pack(fill="x", pady=(2, 0))

                for label, value, color in [
                    ("Trades", str(trades), "#333"),
                    ("WR", wr_str, wr_color),
                    ("PF", f"{pf:.2f}", pf_color),
                    ("Net", f"{net_pips:+,.0f} pips", net_color),
                    ("MaxDD", f"{dd:,.0f} pips", "#dc3545"),
                    ("Best", f"{best:+.0f}", "#28a745"),
                    ("Worst", f"{worst:+.0f}", "#dc3545"),
                ]:
                    tk.Label(metrics_row, text=f"{label}: ", bg=bg_color, fg="#888",
                             font=("Arial", 8)).pack(side=tk.LEFT)
                    tk.Label(metrics_row, text=value, bg=bg_color, fg=color,
                             font=("Arial", 8, "bold")).pack(side=tk.LEFT, padx=(0, 10))

                extra_row = tk.Frame(card, bg=bg_color)
                extra_row.pack(fill="x", pady=(1, 0))
                pct_color = "#28a745" if profit_pct > 0 else "#dc3545"

                exp = r.get('stats', {}).get('expectancy',
                      r.get('expectancy', 0))
                exp_color = "#28a745" if exp > 0 else "#dc3545"

                for label, value, color in [
                    ("Profit", f"{profit_pct:+.1f}% of ${account_size:,.0f}", pct_color),
                    ("Median", f"{median_pips:+.1f} pips", "#28a745" if median_pips > 0 else "#dc3545"),
                    ("Average", f"{avg_pips_calc:+.1f} pips", "#28a745" if avg_pips_calc > 0 else "#dc3545"),
                    ("Expectancy", f"{exp:+.2f} pips/trade", exp_color),
                ]:
                    tk.Label(extra_row, text=f"{label}: ", bg=bg_color, fg="#888",
                             font=("Arial", 8)).pack(side=tk.LEFT)
                    tk.Label(extra_row, text=value, bg=bg_color, fg=color,
                             font=("Arial", 8, "bold")).pack(side=tk.LEFT, padx=(0, 12))

                # ── Hover tooltip: detailed metrics ────────────────────────────
                # WHY: Expectancy, R:R, streaks, frequency are critical for
                #      evaluating a strategy but too noisy for the main card.
                # CHANGED: April 2026 — detailed metrics on hover
                try:
                    from shared.tooltip import add_tooltip
                    # WHY: The matrix writer in strategy_backtester.py flattens
                    #      m["stats"] into the top-level result dict via
                    #      **m["stats"], so r['stats'] does not exist as a
                    #      sub-dict — the keys live at the top level of r.
                    #      Fall back to r itself when 'stats' is missing.
                    #      Without this, every detailed metric in the hover
                    #      tooltip displays 0 (winners 0, losers 0, expectancy
                    #      +0.00, etc.).
                    # CHANGED: April 2026 — read flattened stats from r top level
                    _s = r.get('stats') or r
                    _exp     = _s.get('expectancy', exp)
                    _rr      = _s.get('risk_reward_ratio', 0)
                    _sharpe  = _s.get('sharpe_ish', 0)
                    _mws     = _s.get('max_win_streak', 0)
                    _mls     = _s.get('max_loss_streak', 0)
                    _tpd     = _s.get('trades_per_day', 0)
                    _dpt     = _s.get('days_per_trade', 0)
                    _rec     = _s.get('recovery_factor', 0)
                    _std     = _s.get('std_pips', 0)
                    _avgw    = _s.get('avg_winner', 0)
                    _avgl    = _s.get('avg_loser', 0)
                    _nw      = _s.get('winners', 0)
                    _nl      = _s.get('losers', 0)

                    verdict = []
                    if _exp > 0:
                        verdict.append(f"  ✓ Positive expectancy ({_exp:+.1f} pips/trade)")
                    else:
                        verdict.append(f"  ✗ NEGATIVE expectancy ({_exp:+.1f}) — loses long-term")
                    if _rr >= 1.5:
                        verdict.append(f"  ✓ Good R:R ({_rr:.2f}) — wins are 1.5x+ losses")
                    elif _rr < 1.0:
                        verdict.append(f"  ✗ Weak R:R ({_rr:.2f}) — needs high WR to survive")
                    if _mls >= 10:
                        verdict.append(f"  ⚠ Long loss streak ({_mls}) — emotionally hard to trade")
                    if _dpt > 30:
                        verdict.append(f"  ⚠ Trades rarely ({_dpt} days between) — slow data")
                    elif _tpd > 5:
                        verdict.append(f"  ⚠ High frequency ({_tpd}/day) — sensitive to slippage")
                    if _rec > 0 and _rec < 2:
                        verdict.append(f"  ⚠ Low recovery factor ({_rec}) — DD large vs profit")

                    tooltip_text = (
                        f"━━━ DETAILED METRICS ━━━\n\n"
                        f"📊 BREAKDOWN\n"
                        f"  Winners:  {_nw}  (avg {_avgw:+.1f} pips)\n"
                        f"  Losers:   {_nl}  (avg {_avgl:+.1f} pips)\n"
                        f"  Std dev:  {_std:.1f} pips per trade\n\n"
                        f"💰 EDGE METRICS\n"
                        f"  Expectancy:      {_exp:+.2f} pips/trade\n"
                        f"  Risk:Reward:     {_rr:.2f}\n"
                        f"  Sharpe-ish:      {_sharpe:.2f}\n"
                        f"  Recovery factor: {_rec:.2f}\n\n"
                        f"📈 STREAKS\n"
                        f"  Max win streak:  {_mws} trades\n"
                        f"  Max loss streak: {_mls} trades\n\n"
                        f"⏱ FREQUENCY\n"
                        f"  Trades/day:  {_tpd}\n"
                        f"  Days/trade:  {_dpt}\n\n"
                        f"━━━ VERDICT ━━━\n"
                        + "\n".join(verdict) +
                        f"\n\n━━━ HOW TO READ ━━━\n"
                        f"Expectancy = avg pips per trade. Must be POSITIVE.\n"
                        f"R:R = avg winner / avg loser. >1.5 is good.\n"
                        f"Sharpe-ish = mean/stdev. >0.5 decent, >1.0 great.\n"
                        f"Recovery = profit ÷ max DD. >3 bounces back fast.\n"
                        f"Streaks = worst case to expect emotionally."
                    )
                    add_tooltip(card, tooltip_text)
                except Exception:
                    pass

                breaches = r.get('breaches', {})
                if breaches:
                    from shared.tooltip import add_tooltip
                    blown = breaches.get('blown_count', 0)
                    breach_row = tk.Frame(card, bg=bg_color)
                    breach_row.pack(fill="x", pady=(2, 0))

                    if blown == 0:
                        breach_lbl = tk.Label(breach_row, text="✅ 0 breaches — prop firm safe",
                                              bg=bg_color, fg="#28a745", font=("Arial", 8, "bold"))
                        breach_lbl.pack(side=tk.LEFT)
                        add_tooltip(breach_lbl,
                                    "This strategy NEVER exceeded the prop firm's\n"
                                    "daily or total drawdown limits across the\n"
                                    "entire backtest period. Safe to trade.")
                    else:
                        daily_b = breaches.get('daily_breaches', 0)
                        total_b = breaches.get('total_breaches', 0)
                        surv = breaches.get('survival_rate_per_month', 0)
                        wd = breaches.get('worst_daily_pct', 0)
                        wt = breaches.get('worst_total_pct', 0)

                        import datetime
                        all_blow_dates = sorted(set(
                            breaches.get('daily_breach_dates', []) +
                            breaches.get('total_breach_dates', [])
                        ))
                        blow_months = []
                        for d in all_blow_dates:
                            try:
                                dt = datetime.datetime.strptime(d[:10], '%Y-%m-%d')
                                blow_months.append(dt.strftime('%b %Y'))
                            except Exception:
                                blow_months.append(d[:7])

                        dates_text = ""
                        if blow_months:
                            dates_text = "\n\nBlown in:\n"
                            for bm in blow_months:
                                dates_text += f"  • {bm}\n"

                        color = "#dc3545" if blown > 3 else "#e67e22"
                        breach_lbl = tk.Label(breach_row,
                                              text=f"💀 {blown} blows (daily:{daily_b} total:{total_b}) — "
                                                   f"worst daily:{wd:.1f}%/5% total:{wt:.1f}%/10% — survival {surv}%/mo",
                                              bg=bg_color, fg=color, font=("Arial", 8, "bold"))
                        breach_lbl.pack(side=tk.LEFT)
                        add_tooltip(breach_lbl,
                                    f"💀 {blown} blows = account blown {blown} times\n"
                                    f"  Each blow = 1 failed challenge = 1 fee lost\n\n"
                                    f"daily:{daily_b} = {daily_b} times lost ≥5% in a single day\n"
                                    f"total:{total_b} = {total_b} times equity dropped ≥10% from peak\n\n"
                                    f"worst daily: {wd:.1f}% (limit: 5%)\n"
                                    f"worst total: {wt:.1f}% (limit: 10%)\n\n"
                                    f"survival {surv}%/mo = {surv}% of months had no blowup"
                                    f"{dates_text}")

                    daily_safety = breaches.get('daily_safety_stops', 0)
                    total_safety = breaches.get('total_safety_stops', 0)
                    total_safety_stops = daily_safety + total_safety

                    if total_safety_stops > 0:
                        safety_row = tk.Frame(card, bg=bg_color)
                        safety_row.pack(fill="x", padx=10, pady=(2, 0))

                        import datetime
                        all_safety_dates = sorted(set(
                            breaches.get('daily_safety_dates', []) +
                            breaches.get('total_safety_dates', [])
                        ))
                        safety_months = []
                        for d in all_safety_dates:
                            try:
                                dt = datetime.datetime.strptime(d[:10], '%Y-%m-%d')
                                safety_months.append(dt.strftime('%b %Y'))
                            except Exception:
                                safety_months.append(d[:7])

                        safety_dates_text = ""
                        if safety_months:
                            safety_dates_text = "\n\nSafety stops in:\n"
                            for sm in safety_months:
                                safety_dates_text += f"  • {sm}\n"

                        safety_lbl = tk.Label(safety_row,
                                              text=f"⚠️ {total_safety_stops} safety stops "
                                                   f"(daily:{daily_safety} total:{total_safety}) — "
                                                   f"bot paused before firm limits",
                                              bg=bg_color, fg="#e67e22", font=("Arial", 8))
                        safety_lbl.pack(side=tk.LEFT)
                        add_tooltip(safety_lbl,
                                    f"⚠️ {total_safety_stops} safety stops = bot self-imposed limits touched\n\n"
                                    f"DIFFERENCE:\n"
                                    f"  💀 Firm breach = account BLOWN, challenge FAILED\n"
                                    f"  ⚠️ Safety stop = bot PAUSED, account SURVIVES\n\n"
                                    f"Safety stops are YOUR conservative limits set BEFORE\n"
                                    f"the prop firm's actual limits. When touched, the bot\n"
                                    f"stops trading to protect the account.\n\n"
                                    f"daily:{daily_safety} = {daily_safety} times touched daily safety limit\n"
                                    f"total:{total_safety} = {total_safety} times touched total safety limit"
                                    f"{safety_dates_text}")

                # ── Regime filter conditions ─────────────────────────
                # WHY: Show what regime filter was active and its conditions.
                #      This is the most important metadata — it determines
                #      which signals were blocked during backtesting.
                # CHANGED: April 2026 — regime filter display
                _rs = r.get('run_settings', {})
                _rf_conds = _rs.get('regime_filter_conditions', [])
                _rf_enabled = _rs.get('regime_filter_enabled', False)

                # Also check per-rule regime_filter (Phase A.43 data)
                if not _rf_conds and not _rf_enabled:
                    _rules_rf = r.get('rules', [])
                    for _rr in _rules_rf:
                        _rrf = _rr.get('regime_filter')
                        if _rrf and isinstance(_rrf, list) and len(_rrf) > 0:
                            _rf_conds = _rrf
                            _rf_enabled = True
                            break

                if _rf_enabled or _rf_conds:
                    _regime_card = tk.Frame(card, bg="#f3e8ff", padx=8, pady=4,
                                           highlightbackground="#9b59b6", highlightthickness=1)
                    _regime_card.pack(fill="x", pady=(4, 0))

                    _sig_before = r.get('signals_before_regime_filter', 0)
                    _sig_after = r.get('signals_after_regime_filter', 0)

                    _regime_header = "🔀 REGIME FILTER: ACTIVE"
                    if _sig_before > 0:
                        _filtered_pct = round((1 - _sig_after / _sig_before) * 100, 1)
                        _regime_header += f"  |  {_sig_before} → {_sig_after} signals ({_filtered_pct}% filtered out)"

                    tk.Label(_regime_card, text=_regime_header,
                             bg="#f3e8ff", fg="#7b2d8e", font=("Arial", 8, "bold")
                             ).pack(anchor="w")

                    if _rf_conds:
                        _cond_lines = []
                        for _c in _rf_conds:
                            _feat = _c.get('feature', '?')
                            _op = _c.get('direction', _c.get('operator', '>'))
                            _thr = _c.get('threshold', _c.get('value', '?'))
                            try:
                                _thr = f"{float(_thr):.4f}"
                            except Exception:
                                _thr = str(_thr)
                            _cond_lines.append(f"    {_feat} {_op} {_thr}")

                        _conds_text = "\n".join(_cond_lines)
                        tk.Label(_regime_card, text=_conds_text,
                                 bg="#f3e8ff", fg="#555", font=("Consolas", 8),
                                 justify=tk.LEFT, anchor="w"
                                 ).pack(anchor="w", padx=(8, 0))

                    if _rs.get('regime_filter_mode'):
                        tk.Label(_regime_card,
                                 text=f"    Mode: {_rs.get('regime_filter_mode', '?')}  |  "
                                      f"Strictness: {_rs.get('regime_filter_strictness', '?')}",
                                 bg="#f3e8ff", fg="#888", font=("Arial", 7)
                                 ).pack(anchor="w", padx=(8, 0))
                elif r.get('run_settings'):
                    # Regime was OFF — show that too
                    _no_regime = tk.Frame(card, bg=bg_color)
                    _no_regime.pack(fill="x", pady=(2, 0))
                    tk.Label(_no_regime, text="🔀 Regime Filter: OFF",
                             bg=bg_color, fg="#aaa", font=("Arial", 8)
                             ).pack(side=tk.LEFT)
            else:
                tk.Label(card, text="0 trades — rule conditions never triggered",
                         bg=bg_color, fg="#888", font=("Arial", 9, "italic")).pack(anchor="w")

        except Exception as e:
            # FIX 4: show error card instead of crashing the entire display
            err_card = tk.Frame(results_inner, bg="#fff3cd", padx=12, pady=6)
            err_card.pack(fill="x", pady=2)
            tk.Label(err_card, text=f"#{i+1} Error displaying result: {e}",
                     bg="#fff3cd", fg="#856404", font=("Arial", 9)).pack(anchor="w")

    # ── Update detailed text output ──
    output_text.delete(1.0, tk.END)
    output_text.insert(tk.END, f"{'Rank':<5} {'Rule Combo':<22} {'Exit Strategy':<28} "
                                f"{'Trades':>6} {'WR':>7} {'PF':>6} {'Net Pips':>10} {'Profit%':>8} "
                                f"{'Median':>8} {'Avg':>8} {'MaxDD':>8} {'Blows':>6}\n")
    output_text.insert(tk.END, "-" * 135 + "\n")

    for i, r in enumerate(sorted_results):
        trades = r.get('total_trades', 0)
        wr = r.get('win_rate', 0)
        # CHANGED: April 2026 — Phase 33 Fix 3b — match Fix 3 boundary
        wr_str = f"{wr:.1f}%" if wr >= 1.0 else f"{wr*100:.1f}%"
        pf = r.get('net_profit_factor', 0)
        net = r.get('net_total_pips', 0)
        avg = r.get('net_avg_pips', 0)
        dd = r.get('max_dd_pips', 0)
        rule = r.get('rule_combo', '?')[:20]
        exit_s = r.get('exit_strategy', '?')[:26]

        profit_dollars = net * dollar_per_pip
        profit_pct = (profit_dollars / account_size) * 100

        trade_list = r.get('trades', [])
        try:
            if trade_list and isinstance(trade_list, list):
                import statistics
                pips_list = [t.get('net_pips', 0) for t in trade_list if isinstance(t, dict)]
                med = statistics.median(pips_list) if pips_list else 0
                avg_calc = sum(pips_list) / len(pips_list) if pips_list else avg
            else:
                med = 0
                avg_calc = avg
        except Exception:
            med = 0
            avg_calc = avg

        blown_str = "-"
        breaches = r.get('breaches', {})
        if breaches:
            blown_str = str(breaches.get('blown_count', 0))

        output_text.insert(tk.END, f"#{i+1:<4} {rule:<22} {exit_s:<28} "
                                    f"{trades:>6} {wr_str:>7} {pf:>5.2f} {net:>+10,.0f} "
                                    f"{profit_pct:>+7.1f}% {med:>+7.1f} {avg_calc:>+7.1f} "
                                    f"{dd:>8,.0f} {blown_str:>6}\n")

    output_text.see("1.0")


def build_panel(parent):
    """Build the view results panel"""
    global _output_text, _summary_frame

    panel = tk.Frame(parent, bg="#ffffff")

    # Title
    title = tk.Label(
        panel,
        text="Backtest Results",
        font=("Arial", 16, "bold"),
        bg="#ffffff",
        fg="#333333"
    )
    title.pack(pady=(20, 10))

    subtitle = tk.Label(
        panel,
        text="View performance metrics and HTML report",
        font=("Arial", 10),
        bg="#ffffff",
        fg="#666666"
    )
    subtitle.pack(pady=(0, 20))

    # Action buttons
    button_frame = tk.Frame(panel, bg="#ffffff")
    button_frame.pack(pady=10)

    open_report_btn = tk.Button(
        button_frame,
        text="Open HTML Report",
        command=open_html_report,
        bg="#667eea",
        fg="white",
        font=("Arial", 10, "bold"),
        relief=tk.FLAT,
        cursor="hand2",
        padx=20,
        pady=8
    )
    open_report_btn.pack(side=tk.LEFT, padx=5)

    open_folder_btn = tk.Button(
        button_frame,
        text="Open Outputs Folder",
        command=open_output_folder,
        bg="#667eea",
        fg="white",
        font=("Arial", 10, "bold"),
        relief=tk.FLAT,
        cursor="hand2",
        padx=20,
        pady=8
    )
    open_folder_btn.pack(side=tk.LEFT, padx=5)

    refresh_btn = tk.Button(
        button_frame,
        text="Refresh Results",
        command=lambda: display_summary(_output_text, _summary_frame),
        bg="#28a745",
        fg="white",
        font=("Arial", 10, "bold"),
        relief=tk.FLAT,
        cursor="hand2",
        padx=20,
        pady=8
    )
    refresh_btn.pack(side=tk.LEFT, padx=5)

    # Summary frame (for key metrics)
    _summary_frame = tk.Frame(panel, bg="#ffffff")
    _summary_frame.pack(fill="both", expand=True, padx=20, pady=10)

    # Output text (for detailed stats)
    output_frame = tk.LabelFrame(
        panel,
        text="Detailed Statistics",
        font=("Arial", 11, "bold"),
        bg="#ffffff",
        fg="#333333",
        padx=10,
        pady=10
    )
    output_frame.pack(fill="both", expand=True, padx=20, pady=10)

    _output_text = scrolledtext.ScrolledText(
        output_frame,
        height=8,
        font=("Courier", 9),
        bg="#f8f9fa",
        fg="#333333",
        wrap=tk.WORD
    )
    _output_text.pack(fill="both", expand=True)

    # Initial load — wrapped so panel always appears even if data is corrupt
    # WHY: If display_summary crashes on initial load, the panel never appears
    #      and clicking the sidebar button does literally nothing.
    # CHANGED: April 2026 — error handling for panel build
    try:
        display_summary(_output_text, _summary_frame)
    except Exception as e:
        import traceback
        traceback.print_exc()
        tk.Label(_summary_frame,
                 text=f"Error loading results: {e}\n\nCheck the terminal for details.",
                 font=("Arial", 10), bg="#ffffff", fg="#dc3545",
                 wraplength=600, justify="left").pack(pady=20, padx=20)

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)"""
    global _output_text, _summary_frame
    if _output_text is not None and _summary_frame is not None:
        display_summary(_output_text, _summary_frame)

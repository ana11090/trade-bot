"""
Prop Firm Test Panel — Test backtested strategies against prop firm challenge rules.

Pick a strategy from backtest results, pick which firms to test,
and see pass rates, expected income, and which firm is best for that strategy.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys
import threading

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

import state

# Design tokens
BG    = "#f0f2f5"
WHITE = "white"
GREEN = "#2d8a4e"
RED   = "#e94560"
AMBER = "#996600"
DARK  = "#1a1a2a"
GREY  = "#666666"
MIDGREY = "#555566"

# Module-level widget refs
_strategy_var     = None
_strategies       = []       # list of dicts from load_strategy_list()
_firm_vars        = {}       # firm_id -> BooleanVar
_firm_data        = []       # list from load_available_firms()
_account_size_var = None
_risk_var         = None
_sl_pips_var      = None
_pip_val_var      = None
_run_btn          = None
_progress_bar     = None
_status_label     = None
_results_frame    = None
_strat_info_label = None


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
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
    """Return unique firms (firm_id, firm_name) from _firm_data."""
    seen = {}
    for fc in _firm_data:
        if fc['firm_id'] not in seen:
            seen[fc['firm_id']] = fc['firm_name']
    return list(seen.items())  # [(firm_id, firm_name), ...]


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _on_strategy_select(event=None):
    """Update the strategy info label when selection changes."""
    global _strat_info_label
    if not _strat_info_label or not _strategies:
        return
    idx = _get_selected_index()
    if idx is None:
        _strat_info_label.configure(text="", fg=GREY)
        return
    s = _strategies[idx]
    has_trades = s.get('has_trades', False)
    if has_trades:
        text = (f"{s['total_trades']} trades  |  WR {s['win_rate']:.1f}%  |  "
                f"net {s['net_total_pips']:+.0f} pips  |  PF {s['net_profit_factor']:.2f}")
        _strat_info_label.configure(text=text, fg=MIDGREY)
    else:
        _strat_info_label.configure(
            text="⚠ Trade details missing. Re-run the backtest to include them.",
            fg=RED
        )


def _get_selected_index():
    """Get the index into _strategies for the current dropdown value."""
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _select_all_firms():
    for var in _firm_vars.values():
        var.set(True)


def _clear_all_firms():
    for var in _firm_vars.values():
        var.set(False)


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

    selected_firm_ids = [fid for fid, var in _firm_vars.items() if var.get()]
    if not selected_firm_ids:
        messagebox.showerror("No Firms", "Select at least one prop firm to test.")
        return

    try:
        account_size = int(_account_size_var.get())
        risk_pct     = float(_risk_var.get())
        sl_pips      = float(_sl_pips_var.get())
        pip_val      = float(_pip_val_var.get())
    except ValueError:
        messagebox.showerror("Invalid Settings", "Check that all settings are valid numbers.")
        return

    _run_btn.configure(state="disabled", text="Running...")
    _progress_bar['value'] = 0
    _status_label.configure(text="Loading trades...", fg=GREY)

    def _worker():
        try:
            from project2_backtesting.prop_firm_tester import (
                load_strategy_trades, run_multi_firm_test, _closest_account_size
            )

            trades = load_strategy_trades(idx)
            if not trades:
                state.window.after(0, lambda: _status_label.configure(
                    text="No trades found for selected strategy.", fg=RED))
                return

            # Build firm_challenges: all challenges for each selected firm
            firm_challenges = []
            for fc in _firm_data:
                if fc['firm_id'] in selected_firm_ids:
                    closest = _closest_account_size(fc['account_sizes'], account_size)
                    firm_challenges.append({
                        'firm_id':       fc['firm_id'],
                        'firm_name':     fc['firm_name'],
                        'challenge_id':  fc['challenge_id'],
                        'challenge_name': fc['challenge_name'],
                        'account_size':  closest,
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
                progress_callback=_progress,
            )

            state.window.after(0, lambda: _display_results(results, s['label']))
            state.window.after(0, lambda: _progress_bar.configure(value=100))
            state.window.after(0, lambda: _status_label.configure(
                text=f"Done — {len(results)} results, sorted by Expected ROI", fg=GREEN))

        except Exception as e:
            import traceback
            err = traceback.format_exc()
            print(f"[prop_firm_test] Error:\n{err}")
            state.window.after(0, lambda: _status_label.configure(
                text=f"Error: {e}", fg=RED))
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
    banner.pack(fill="x", padx=5, pady=(0, 8))
    tk.Label(banner, text=f"Strategy: {strategy_label}",
             font=("Segoe UI", 9, "bold"), bg="#e8f5e9", fg="#2e7d32").pack(anchor="w")

    # Table header
    header_frame = tk.Frame(_results_frame, bg="#f5f5f5", padx=10, pady=5)
    header_frame.pack(fill="x", padx=5)

    col_defs = [
        ("#",            3),
        ("Firm",        16),
        ("Challenge",   18),
        ("Size",         8),
        ("Pass%",        6),
        ("Sims",         5),
        ("Avg Days",     9),
        ("Max DD%",      8),
        ("Monthly $",   10),
        ("ROI%",         7),
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
            row_color = "#f0fdf4"
            rate_color = GREEN
        elif pass_rate >= 0.40:
            row_color = "#fffbeb"
            rate_color = AMBER
        else:
            row_color = "#fef2f2"
            rate_color = RED

        row = tk.Frame(_results_frame, bg=row_color, padx=10, pady=4)
        row.pack(fill="x", padx=5)

        roi_color = GREEN if roi > 0 else (RED if roi < 0 else GREY)

        tk.Label(row, text=f"{i}",
                font=("Segoe UI", 8), bg=row_color, fg=GREY, width=3, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=r['firm_name'],
                font=("Segoe UI", 8, "bold"), bg=row_color, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=r['challenge_name'],
                font=("Segoe UI", 8), bg=row_color, fg=DARK, width=18, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=f"${r['account_size']:,}",
                font=("Segoe UI", 8), bg=row_color, fg=GREY, width=8, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=f"{pass_rate*100:.0f}%",
                font=("Segoe UI", 8, "bold"), bg=row_color, fg=rate_color, width=6, anchor="w").pack(side=tk.LEFT, padx=1)
        tk.Label(row, text=str(r['num_simulations']),
                font=("Segoe UI", 8), bg=row_color, fg=GREY, width=5, anchor="w").pack(side=tk.LEFT, padx=1)
        avg_days = r['avg_days_to_pass'] or 0
        tk.Label(row, text=f"{avg_days:.0f}d",
                font=("Segoe UI", 8), bg=row_color, fg=DARK, width=9, anchor="w").pack(side=tk.LEFT, padx=1)
        avg_dd = (r['avg_max_dd_pct'] or 0) * 100
        tk.Label(row, text=f"{avg_dd:.1f}%",
                font=("Segoe UI", 8), bg=row_color, fg=DARK, width=8, anchor="w").pack(side=tk.LEFT, padx=1)
        monthly_text = f"${monthly:,.0f}" if monthly else "—"
        tk.Label(row, text=monthly_text,
                font=("Segoe UI", 8, "bold"), bg=row_color, fg=GREEN if monthly else GREY,
                width=10, anchor="w").pack(side=tk.LEFT, padx=1)
        roi_text = f"{roi:+.0f}%" if roi else "—"
        tk.Label(row, text=roi_text,
                font=("Segoe UI", 8, "bold"), bg=row_color, fg=roi_color, width=7, anchor="w").pack(side=tk.LEFT, padx=1)


# ─────────────────────────────────────────────────────────────────────────────
# Panel builder
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _strategy_var, _firm_vars, _account_size_var, _risk_var
    global _sl_pips_var, _pip_val_var, _run_btn, _progress_bar
    global _status_label, _results_frame, _strat_info_label

    # Load data
    _load_strategies()
    _load_firms()

    panel = tk.Frame(parent, bg=BG)

    # ── Header ────────────────────────────────────────────────────────────────
    header = tk.Frame(panel, bg=WHITE, pady=20)
    header.pack(fill="x", padx=20, pady=(20, 10))

    tk.Label(header, text="🏦 Prop Firm Challenge Test",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(header,
             text="Test your backtested strategy against prop firm challenge rules",
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
                                values=labels, state="readonly", width=60)
        dropdown.pack(anchor="w")
        dropdown.bind("<<ComboboxSelected>>", _on_strategy_select)

    _strat_info_label = tk.Label(strat_frame, text="", font=("Segoe UI", 9),
                                  bg=WHITE, fg=MIDGREY)
    _strat_info_label.pack(anchor="w", pady=(4, 0))
    _on_strategy_select()

    # ── Settings ──────────────────────────────────────────────────────────────
    settings_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    settings_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(settings_frame, text="Settings", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 8))

    row1 = tk.Frame(settings_frame, bg=WHITE)
    row1.pack(fill="x", pady=(0, 4))

    def _setting(parent, label, default, width=10):
        var = tk.StringVar(value=default)
        tk.Label(parent, text=label, font=("Segoe UI", 9),
                 bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(0, 4))
        tk.Entry(parent, textvariable=var, width=width).pack(side=tk.LEFT, padx=(0, 20))
        return var

    _account_size_var = _setting(row1, "Account size ($):", "100000", 10)
    _risk_var         = _setting(row1, "Risk/trade (%):",  "1.0",    6)
    _sl_pips_var      = _setting(row1, "Default SL (pips):", "150",  7)
    _pip_val_var      = _setting(row1, "Pip value/lot ($):", "10.0", 7)

    # ── Firm selection ────────────────────────────────────────────────────────
    firms_outer = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    firms_outer.pack(fill="x", padx=20, pady=(0, 5))

    header_row = tk.Frame(firms_outer, bg=WHITE)
    header_row.pack(fill="x", pady=(0, 8))

    tk.Label(header_row, text="Prop Firms", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(side=tk.LEFT)

    tk.Button(header_row, text="Select All", command=_select_all_firms,
              bg=GREEN, fg="white", font=("Segoe UI", 8, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3).pack(side=tk.LEFT, padx=(15, 4))
    tk.Button(header_row, text="Clear", command=_clear_all_firms,
              bg=GREY, fg="white", font=("Segoe UI", 8, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3).pack(side=tk.LEFT)

    firms_grid = tk.Frame(firms_outer, bg=WHITE)
    firms_grid.pack(fill="x")

    unique_firms = _get_unique_firms()
    _firm_vars.clear()
    for col_i, (firm_id, firm_name) in enumerate(unique_firms):
        var = tk.BooleanVar(value=True)
        _firm_vars[firm_id] = var
        cb = tk.Checkbutton(firms_grid, text=firm_name, variable=var,
                            bg=WHITE, font=("Segoe UI", 9), anchor="w")
        cb.grid(row=col_i // 4, column=col_i % 4, sticky="w", padx=10, pady=2)

    if not unique_firms:
        tk.Label(firms_grid, text="No prop firms loaded.",
                 font=("Segoe UI", 9, "italic"), bg=WHITE, fg=RED).pack(anchor="w")

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

    # ── Results section header ────────────────────────────────────────────────
    results_header = tk.Frame(panel, bg=WHITE, padx=20, pady=8)
    results_header.pack(fill="x", padx=20, pady=(5, 0))
    tk.Label(results_header, text="Results (sorted by Expected ROI)",
             font=("Segoe UI", 11, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT)

    # ── Scrollable results area ───────────────────────────────────────────────
    canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    scrollbar = tk.Scrollbar(panel, orient="vertical", command=canvas.yview)
    _results_frame = tk.Frame(canvas, bg=BG)

    _results_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    content_window_id = canvas.create_window((0, 0), window=_results_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True, padx=(20, 0))
    scrollbar.pack(side="right", fill="y", padx=(0, 20))

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", _on_mousewheel)
    canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
    canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

    def _on_canvas_resize(event):
        canvas.itemconfig(content_window_id, width=event.width)

    canvas.bind("<Configure>", _on_canvas_resize)

    return panel


def refresh():
    """Reload strategy list when panel becomes active."""
    global _strategies, _strategy_var, _strat_info_label
    _load_strategies()
    if _strategy_var is not None and _strategies:
        current = _strategy_var.get()
        labels = [s['label'] for s in _strategies]
        if current not in labels:
            _strategy_var.set(labels[0])
        _on_strategy_select()

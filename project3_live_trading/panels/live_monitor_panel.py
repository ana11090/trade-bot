"""
Live Monitor Panel — import EA trade log, compare to backtest predictions.

Shows matched/missed/extra trades, average slippage, P&L drift, and overall verdict.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import threading

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
_strategies       = []
_strategy_var     = None
_log_path_var     = None
_strat_info_lbl   = None
_import_lbl       = None
_run_btn          = None
_status_lbl       = None
_scroll_canvas    = None
_results_frame    = None
_verdict_frame    = None


def _load_strategies():
    global _strategies
    try:
        from project2_backtesting.strategy_refiner import load_strategy_list
        _strategies = load_strategy_list()
    except Exception as e:
        print(f"[live_monitor] {e}")
        _strategies = []


def _get_selected_index():
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _get_backtest_trades(idx):
    """Load trades from backtest_matrix.json for the selected strategy."""
    try:
        import json
        path = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data['results'][idx].get('trades', [])
    except Exception:
        return []


def _browse_log():
    path = filedialog.askopenfilename(
        title="Select EA Trade Log CSV",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )
    if path and _log_path_var:
        _log_path_var.set(path)
        _show_log_info(path)


def _show_log_info(path):
    """Show basic info about the loaded log file."""
    if not _import_lbl:
        return
    try:
        import csv
        trades = []
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('direction', '') not in ('SKIP', ''):
                    trades.append(row)
        if not trades:
            _import_lbl.configure(text="No actual trades found in log (only signals).", fg=AMBER)
            return
        dates = [r.get('timestamp', r.get('entry_time', '')) for r in trades]
        date_range = f"{min(dates)[:10]} to {max(dates)[:10]}" if dates else "?"
        _import_lbl.configure(
            text=f"{len(trades)} trades found  |  {date_range}",
            fg=GREEN)
    except Exception as e:
        _import_lbl.configure(text=f"Error reading file: {e}", fg=RED)


def _run_comparison():
    idx = _get_selected_index()
    if idx is None:
        messagebox.showerror("No Strategy", "Select a strategy to compare against.")
        return

    log_path = _log_path_var.get() if _log_path_var else ""
    if not log_path or not os.path.exists(log_path):
        messagebox.showerror("No Log", "Select an EA trade log CSV first.")
        return

    backtest_trades = _get_backtest_trades(idx)
    if not backtest_trades:
        messagebox.showerror("No Backtest Data",
                             "No trade data for this strategy.\nRe-run the backtest first.")
        return

    if _run_btn:    _run_btn.configure(state="disabled")
    if _status_lbl: _status_lbl.configure(text="Comparing...", fg=GREY)
    if _results_frame:
        for w in _results_frame.winfo_children():
            w.destroy()
    if _verdict_frame:
        for w in _verdict_frame.winfo_children():
            w.destroy()

    def _worker():
        try:
            from project3_live_trading.ea_verifier import verify_ea_trades
            result = verify_ea_trades(log_path, backtest_trades)

            state.window.after(0, lambda: _display_results(result))
            state.window.after(0, lambda: _display_verdict(result))
            state.window.after(0, lambda: _status_lbl.configure(
                text="Comparison complete.", fg=GREEN))
        except Exception as e:
            import traceback; traceback.print_exc()
            state.window.after(0, lambda: _status_lbl.configure(
                text=f"Error: {e}", fg=RED))
        finally:
            state.window.after(0, lambda: _run_btn.configure(state="normal"))

    threading.Thread(target=_worker, daemon=True).start()


def _display_results(result):
    if _results_frame is None:
        return
    for w in _results_frame.winfo_children():
        w.destroy()

    summary = result.get('summary', {})

    # Summary counts
    sum_card = tk.Frame(_results_frame, bg=WHITE, padx=16, pady=10)
    sum_card.pack(fill="x", padx=5, pady=(0, 6))
    tk.Label(sum_card, text="Comparison Summary",
             font=("Segoe UI", 10, "bold"), bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    counts = [
        ("Backtest trades",   summary.get('backtest_count', 0),  DARK),
        ("Matched",           summary.get('matched_count', 0),   GREEN),
        ("Missed",            summary.get('missed_count', 0),    AMBER),
        ("Extra (EA-only)",   summary.get('extra_count', 0),     RED),
        ("Avg slippage",      f"{summary.get('avg_slippage_pips', 0):.2f} pips", MIDGREY),
        ("Avg P&L diff",      f"{summary.get('avg_pnl_diff', 0):+.2f} pips", MIDGREY),
        ("Match rate",        f"{summary.get('match_rate', 0)*100:.1f}%", GREEN),
    ]
    for label, val, color in counts:
        r = tk.Frame(sum_card, bg=WHITE)
        r.pack(fill="x", pady=1)
        tk.Label(r, text=f"{label}:", font=("Segoe UI", 9), bg=WHITE, fg=GREY,
                 width=20, anchor="w").pack(side=tk.LEFT)
        tk.Label(r, text=str(val), font=("Segoe UI", 9, "bold"), bg=WHITE, fg=color
                 ).pack(side=tk.LEFT)

    def _trade_table(parent, trades, cols, title, color):
        if not trades:
            return
        tk.Label(parent, text=f"{title} ({len(trades)})",
                 font=("Segoe UI", 10, "bold"), bg=BG, fg=color).pack(anchor="w", padx=5, pady=(8, 2))
        hdr = tk.Frame(parent, bg="#f5f5f5", padx=8, pady=4)
        hdr.pack(fill="x", padx=5)
        for col_text, col_w in cols:
            tk.Label(hdr, text=col_text, font=("Segoe UI", 7, "bold"),
                     bg="#f5f5f5", fg=GREY, width=col_w, anchor="w").pack(side=tk.LEFT, padx=1)

        for t in trades[:30]:
            row_bg = "#f0fdf4" if color == GREEN else ("#fef2f2" if color == RED else "#fffbeb")
            row = tk.Frame(parent, bg=row_bg, padx=8, pady=2)
            row.pack(fill="x", padx=5)
            for (_, w), val in zip(cols, _extract_cols(t, cols)):
                tk.Label(row, text=str(val)[:w*2], font=("Consolas", 7),
                         bg=row_bg, fg=DARK, width=w, anchor="w").pack(side=tk.LEFT, padx=1)

    def _extract_cols(t, cols):
        """Extract values for display columns from a comparison result dict."""
        results = []
        for col_name, _ in cols:
            cn = col_name.lower().replace(' ', '_').replace('(', '').replace(')', '')
            if cn == 'entry_time':
                bt = t.get('backtest', t)
                results.append(str(bt.get('entry_time', ''))[:16])
            elif cn == 'direction':
                bt = t.get('backtest', t.get('ea', t))
                results.append(bt.get('direction', '?'))
            elif cn == 'slippage_pips':
                results.append(f"{t.get('slippage_pips', 0):.2f}")
            elif cn == 'pnl_diff':
                results.append(f"{t.get('pnl_diff', 0):+.2f}")
            elif cn == 'skip_reason':
                results.append(t.get('skip_reason', '?'))
            elif cn == 'bt_pips':
                bt = t.get('backtest', t)
                results.append(f"{float(bt.get('net_pips', 0)):+.1f}")
            elif cn == 'ea_pips':
                ea = t.get('ea', t)
                results.append(f"{float(ea.get('net_pips', 0)):+.1f}")
            else:
                bt = t.get('backtest', t)
                results.append(str(bt.get(cn, t.get(cn, '?')))[:20])
        return results

    matched_cols = [("Entry Time", 17), ("Direction", 6), ("BT pips", 8), ("EA pips", 8), ("Slippage pips", 12), ("PnL diff", 8)]
    missed_cols  = [("Entry Time", 17), ("Direction", 6), ("BT pips", 8), ("Skip Reason", 20)]
    extra_cols   = [("Entry Time", 17), ("Direction", 6), ("EA pips", 8)]

    _trade_table(_results_frame, result.get('matched_trades', []), matched_cols, "✅ Matched Trades", GREEN)
    _trade_table(_results_frame, result.get('missed_trades',  []), missed_cols,  "⚠️ Missed Trades",  AMBER)
    _trade_table(_results_frame, result.get('extra_trades',   []), extra_cols,   "❌ Extra Trades",    RED)


def _display_verdict(result):
    if _verdict_frame is None:
        return
    for w in _verdict_frame.winfo_children():
        w.destroy()

    verdict    = result.get('verdict', 'POOR')
    match_rate = result.get('match_rate', 0.0)
    summary    = result.get('summary', {})

    verdict_map = {
        'EXCELLENT': (GREEN,  "✅ EA matches backtest — safe to continue live trading"),
        'GOOD':      (AMBER,  "⚠️ Minor discrepancies — review missed trades before continuing"),
        'POOR':      (RED,    "❌ Significant mismatch — check indicator calculations before trading"),
        'ERROR':     (RED,    "❌ Error during comparison — check log file format"),
    }
    color, msg = verdict_map.get(verdict, (GREY, "Unknown verdict"))

    card = tk.Frame(_verdict_frame, bg=WHITE,
                    highlightbackground=color, highlightthickness=2,
                    padx=16, pady=12)
    card.pack(fill="x", padx=5, pady=8)

    tk.Label(card, text=f"Match Rate: {match_rate*100:.1f}%  |  Verdict: {verdict}",
             font=("Segoe UI", 12, "bold"), bg=WHITE, fg=color).pack(anchor="w")
    tk.Label(card, text=msg, font=("Segoe UI", 10), bg=WHITE, fg=MIDGREY,
             wraplength=600, justify="left").pack(anchor="w", pady=(4, 0))

    slip = summary.get('avg_slippage_pips', 0)
    if slip > 0.5:
        tk.Label(card, text=f"⚠  Average slippage: {slip:.2f} pips — check broker execution quality",
                 font=("Segoe UI", 9), bg=WHITE, fg=AMBER).pack(anchor="w", pady=(4, 0))


# ─────────────────────────────────────────────────────────────────────────────
# Panel builder
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _strategy_var, _strat_info_lbl, _log_path_var, _import_lbl
    global _run_btn, _status_lbl, _scroll_canvas, _results_frame, _verdict_frame

    _load_strategies()

    panel = tk.Frame(parent, bg=BG)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(panel, bg=WHITE, pady=16)
    hdr.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(hdr, text="📊 Live Monitor",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(hdr, text="Compare EA performance to backtest predictions",
             bg=WHITE, fg=GREY, font=("Segoe UI", 11)).pack(pady=(4, 0))

    # ── Strategy selector ─────────────────────────────────────────────────────
    sel_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    sel_frame.pack(fill="x", padx=20, pady=(0, 5))
    tk.Label(sel_frame, text="Backtest Strategy (for comparison)",
             font=("Segoe UI", 11, "bold"), bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    if not _strategies:
        tk.Label(sel_frame, text="No backtest results. Run the backtest first.",
                 font=("Segoe UI", 10, "italic"), bg=WHITE, fg=RED).pack(anchor="w")
        _strategy_var = tk.StringVar(value="")
    else:
        _strategy_var = tk.StringVar(value=_strategies[0]['label'])
        labels = [s['label'] for s in _strategies]
        ttk.Combobox(sel_frame, textvariable=_strategy_var,
                     values=labels, state="readonly", width=70).pack(anchor="w")

    _strat_info_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 9),
                                bg=WHITE, fg=MIDGREY)
    _strat_info_lbl.pack(anchor="w", pady=(4, 0))

    # ── Log import ────────────────────────────────────────────────────────────
    log_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    log_frame.pack(fill="x", padx=20, pady=(0, 5))
    tk.Label(log_frame, text="EA Trade Log", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    log_row = tk.Frame(log_frame, bg=WHITE)
    log_row.pack(fill="x")
    _log_path_var = tk.StringVar(value="")
    tk.Entry(log_row, textvariable=_log_path_var, width=55,
             font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(log_row, text="Browse...", command=_browse_log,
              bg="#667eea", fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=4).pack(side=tk.LEFT)

    _import_lbl = tk.Label(log_frame, text="No log loaded.",
                            font=("Segoe UI", 9, "italic"), bg=WHITE, fg=GREY)
    _import_lbl.pack(anchor="w", pady=(4, 0))

    # ── Run button ────────────────────────────────────────────────────────────
    run_frame = tk.Frame(panel, bg=BG, pady=8)
    run_frame.pack(fill="x", padx=20)

    _run_btn = tk.Button(run_frame, text="Compare EA vs Backtest",
                         command=_run_comparison,
                         bg="#667eea", fg="white", font=("Segoe UI", 10, "bold"),
                         relief=tk.FLAT, cursor="hand2", padx=18, pady=8)
    _run_btn.pack(side=tk.LEFT, padx=(0, 12))

    _status_lbl = tk.Label(run_frame, text="Ready",
                            font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY)
    _status_lbl.pack(side=tk.LEFT)

    # ── Scrollable results ────────────────────────────────────────────────────
    _scroll_canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    vscroll = tk.Scrollbar(panel, orient="vertical", command=_scroll_canvas.yview)
    scroll_frame = tk.Frame(_scroll_canvas, bg=BG)
    scroll_frame.bind("<Configure>",
                      lambda e: _scroll_canvas.configure(scrollregion=_scroll_canvas.bbox("all")))
    cwin = _scroll_canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    _scroll_canvas.configure(yscrollcommand=vscroll.set)
    _scroll_canvas.pack(side="left", fill="both", expand=True, padx=(20, 0))
    vscroll.pack(side="right", fill="y", padx=(0, 20))

    def _mw(e): _scroll_canvas.yview_scroll(int(-1*(e.delta/120)), "units")
    _scroll_canvas.bind_all("<MouseWheel>", _mw)
    _scroll_canvas.bind_all("<Button-4>", lambda e: _scroll_canvas.yview_scroll(-3, "units"))
    _scroll_canvas.bind_all("<Button-5>", lambda e: _scroll_canvas.yview_scroll(3, "units"))
    _scroll_canvas.bind("<Configure>", lambda e: _scroll_canvas.itemconfig(cwin, width=e.width))

    sf = scroll_frame
    _results_frame = tk.Frame(sf, bg=BG)
    _results_frame.pack(fill="x", padx=5, pady=(5, 0))
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

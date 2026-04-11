"""
Project 4 — Build Strategy from Scratch panel.

Scans ALL historical candles, labels each WIN/LOSS based on what happened
after entry, then trains XGBoost with 670 features to find profitable
entry conditions.  No robot trade history needed.

Per-canvas scroll binding only — no bind_all (breaks other panels).
All heavy computation runs in a daemon thread; UI updates via .after().
"""

import os
import sys
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# WHY: Scrollable tooltips for enhancement checkbox descriptions
# CHANGED: April 2026 — added for discovery enhancements
try:
    from shared.tooltip import _Tooltip
except ImportError:
    class _Tooltip:
        def __init__(self, widget, text):
            self.widget = widget
            self.text = text
            self.tip = None
            widget.bind("<Enter>", self._show)
            widget.bind("<Leave>", self._hide)
        def _show(self, e=None):
            if self.tip:
                return
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
            self.tip = tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            f = tk.Frame(tw, bg="#333", padx=2, pady=2)
            f.pack()
            lines = self.text.count('\n') + 1
            if lines > 12:
                canvas = tk.Canvas(f, bg="#333", highlightthickness=0, width=440, height=300)
                sb = tk.Scrollbar(f, orient="vertical", command=canvas.yview)
                sf = tk.Frame(canvas, bg="#333")
                sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
                canvas.create_window((0, 0), window=sf, anchor="nw")
                canvas.configure(yscrollcommand=sb.set)
                canvas.pack(side="left", fill="both", expand=True)
                sb.pack(side="right", fill="y")
                tk.Label(sf, text=self.text, justify="left", bg="#333", fg="white",
                         font=("Segoe UI", 9), padx=10, pady=6, wraplength=420).pack()
                canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
                sf.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
            else:
                tk.Label(f, text=self.text, justify="left", bg="#333", fg="white",
                         font=("Segoe UI", 9), padx=10, pady=6, wraplength=420).pack()
        def _hide(self, e=None):
            if self.tip:
                self.tip.destroy()
                self.tip = None

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, '..', '..')))

# CHANGED: April 2026 — Phase 26 Fix 5 — UI-safe logging
from shared.logging_setup import get_logger
log = get_logger(__name__)

# ── module-level state (panel is built only once, lazily) ─────────────────────
_panel          = None
_result         = None
_run_thread     = None
_start_time     = [0.0]
_elapsed_var    = [None]   # tk.StringVar — filled after Tk exists
_status_var     = [None]
_progress_var   = [None]
_base_wr_var    = [None]

# widget refs set during build_panel()
_widgets        = {}

# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _panel
    if _panel is not None:
        return _panel
    _panel = _build(parent)
    return _panel


def refresh():
    """Called every time the panel becomes visible."""
    _load_existing_result()
    _render_results()
    _update_comparison()

    # Update canvas scrollregion after refresh
    canvas = _widgets.get('canvas')
    if canvas:
        canvas.after(100, lambda: canvas.configure(scrollregion=canvas.bbox("all")))


# ─────────────────────────────────────────────────────────────────────────────
# BUILD
# ─────────────────────────────────────────────────────────────────────────────

def _build(parent):
    # Root frame fills parent via pack(fill="both", expand=True) in sidebar.py
    root = tk.Frame(parent, bg="#f0f2f5")

    # Use grid layout to ensure proper height filling
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    # Scrollable canvas - fills entire root frame height
    canvas = tk.Canvas(root, bg="#f0f2f5", highlightthickness=0)
    vsb    = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)

    canvas.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    _widgets['canvas'] = canvas

    inner = tk.Frame(canvas, bg="#f0f2f5")
    cw    = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner_resize(e):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_resize(e):
        canvas.itemconfig(cw, width=e.width)

    inner.bind("<Configure>", _on_inner_resize)
    canvas.bind("<Configure>", _on_canvas_resize)

    # Mousewheel scrolling
    def _on_mousewheel(e):
        canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    # WHY: bind_all captures ALL mousewheel events globally, breaking scrolling
    #      in every other panel. Per-canvas bind only scrolls when mouse is over THIS canvas.
    # CHANGED: April 2026 — replaced bind_all with per-widget bind
    canvas.bind("<MouseWheel>", _on_mousewheel)
    canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
    canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))
    # Also bind to inner frame so scrolling works when mouse is over content
    inner.bind("<MouseWheel>", _on_mousewheel)
    inner.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
    inner.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

    _build_inner(inner)
    return root


def _build_inner(inner):
    pad = dict(padx=20, pady=6)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(inner, bg="#1e2d4e", pady=18)
    hdr.pack(fill="x", **dict(padx=0, pady=0))
    tk.Label(hdr, text="Build Strategy from Scratch",
             bg="#1e2d4e", fg="white",
             font=("Segoe UI", 18, "bold")).pack()
    tk.Label(hdr,
             text="Find trading edges directly from price data — no robot needed",
             bg="#1e2d4e", fg="#5a7a99",
             font=("Segoe UI", 10)).pack()

    # ── Explanation card ──────────────────────────────────────────────────────
    card = tk.Frame(inner, bg="#e8f4fd", relief="flat", bd=0)
    card.pack(fill="x", padx=20, pady=(14, 4))
    tk.Label(card,
             text=(
                 "This scans every candle in history and asks:\n"
                 "\"If I entered a trade HERE, would it have been profitable?\"\n\n"
                 "Then uses XGBoost + 670 features to find WHAT CONDITIONS\n"
                 "predict profitable entries. No robot trade history needed.\n\n"
                 "130,000+ candles analyzed  vs  ~1,100 trades from a robot.\n"
                 "More data = more reliable patterns."
             ),
             bg="#e8f4fd", fg="#1a3a5c",
             font=("Segoe UI", 10), justify="left",
             padx=14, pady=12).pack(anchor="w")

    # ── Data status (auto-detected) ───────────────────────────────────────────
    _section(inner, "Data Status")

    # Resolve paths relative to project root
    _project_root = os.path.abspath(os.path.join(_HERE, '..', '..'))
    _data_dir     = os.path.join(_project_root, 'data')

    # Determine entry TF from config
    try:
        import sys as _sys
        _sys.path.insert(0, _project_root)
        from project2_backtesting.panels.configuration import load_config as _load_cfg
        _entry_tf = _load_cfg().get('winning_scenario', 'H1')
    except Exception:
        _entry_tf = 'H1'
    _candles_fname = f"xauusd_{_entry_tf}.csv"
    _candles_path  = os.path.join(_data_dir, _candles_fname)

    status_frame = tk.Frame(inner, bg="#f0f2f5")
    status_frame.pack(fill="x", **pad)

    # Entry TF candle file
    if os.path.exists(_candles_path):
        try:
            # WHY: Old code imported pandas as _pd but only used the builtin
            #      sum(1 for _ in open(...)) which doesn't need pandas.
            # CHANGED: April 2026 — remove dead local import (Phase 19b)
            _nc = sum(1 for _ in open(_candles_path)) - 1
            candle_status = f"\u2705 Found {_entry_tf} candles: {_nc:,} candles  ({_candles_path})"
            candle_color  = "#1e8449"
        except Exception:
            candle_status = f"\u2705 Found {_entry_tf} candles  ({_candles_path})"
            candle_color  = "#1e8449"
    else:
        candle_status = f"\u274c No {_entry_tf} candle data found at data/{_candles_fname}"
        candle_color  = "#922b21"

    tk.Label(status_frame, text=candle_status,
             bg="#f0f2f5", fg=candle_color,
             font=("Segoe UI", 9), anchor="w", wraplength=600,
             justify="left").pack(fill="x")

    # Indicator cache check
    _parquet_files = [f for f in os.listdir(_data_dir)
                      if f.endswith('.parquet')] if os.path.isdir(_data_dir) else []
    if _parquet_files:
        cache_status = f"\u2705 Indicator cache ready ({len(_parquet_files)} parquet files) — fast mode (~5 min total)"
        cache_color  = "#1e8449"
    else:
        cache_status = "\u26a0\ufe0f No indicator cache — first run will compute all indicators (may take longer)"
        cache_color  = "#d35400"

    tk.Label(status_frame, text=cache_status,
             bg="#f0f2f5", fg=cache_color,
             font=("Segoe UI", 9), anchor="w", wraplength=600,
             justify="left").pack(fill="x", pady=(2, 0))

    _widgets['candles_path'] = _candles_path

    # ── Settings (multi-column layout) ────────────────────────────────────────
    _section(inner, "Settings")

    # Container for 3-column layout with grid
    settings_container = tk.Frame(inner, bg="#f0f2f5")
    settings_container.pack(fill="x", **pad)

    # Configure grid columns with equal weight
    settings_container.columnconfigure(0, weight=1)
    settings_container.columnconfigure(1, weight=1)
    settings_container.columnconfigure(2, weight=1)

    # Left column — Trade Definition
    left_col = tk.Frame(settings_container, bg="#f0f2f5")
    left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=5)

    tk.Label(left_col, text="Trade Definition", bg="#f0f2f5", fg="#8e44ad",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

    direction_var = tk.StringVar(value="BUY")
    sl_var        = tk.StringVar(value="150")
    tp_var        = tk.StringVar(value="300")
    hold_var      = tk.StringVar(value="50")
    spread_var    = tk.StringVar(value="2.5")
    rr_var        = tk.StringVar(value="R:R = 2.0:1")

    # WHY: Phase 26 Fix 4 — Track the initial defaults so the prop
    #      firm selector can detect whether the user has manually
    #      edited the fields. If the current values still match the
    #      initial defaults at the time the firm is selected, the
    #      override fires. If they differ, the user has customized
    #      and we leave them alone.
    # CHANGED: April 2026 — Phase 26 Fix 4 (audit Part B #23)
    _sltp_initial_defaults = ("150", "300")

    _widgets.update(dict(direction_var=direction_var, sl_var=sl_var,
                         tp_var=tp_var, hold_var=hold_var,
                         spread_var=spread_var, rr_var=rr_var))

    def _update_rr(*_):
        try:
            tp = float(tp_var.get())
            sl = float(sl_var.get())
            rr_var.set(f"R:R = {tp/sl:.2f}:1" if sl > 0 else "R:R = ?")
        except (ValueError, ZeroDivisionError):
            rr_var.set("R:R = ?")

    sl_var.trace_add("write", _update_rr)
    tp_var.trace_add("write", _update_rr)

    # WHY: Phase 26 Fix 2 — Added XAUUSD scale hints to SL/TP labels.
    #      Defaults (150/300) are gold-scale; forex users need to adjust.
    # CHANGED: April 2026 — Phase 26 Fix 2 — XAUUSD hint (audit Part B #27)
    left_rows = [
        ("Direction:",          direction_var, "combo", ["BUY", "SELL", "BOTH"]),
        ("SL (pips) [XAUUSD: 150]:",  sl_var,  "entry", None),
        ("TP (pips) [XAUUSD: 300]:",  tp_var,  "entry", None),
        ("Max hold:",           hold_var,      "entry", None),
        ("Spread:",             spread_var,    "entry", None),
    ]
    for lbl, var, kind, opts in left_rows:
        row = tk.Frame(left_col, bg="#f0f2f5")
        row.pack(fill="x", pady=2)
        tk.Label(row, text=lbl, bg="#f0f2f5", fg="#333",
                 font=("Segoe UI", 9), width=14, anchor="w").pack(side="left")
        if kind == "combo":
            cb = ttk.Combobox(row, textvariable=var, values=opts,
                              state="readonly", width=10,
                              font=("Segoe UI", 9))
            cb.pack(side="left")
        else:
            tk.Entry(row, textvariable=var, width=10,
                     font=("Segoe UI", 9), bg="white", fg="#333",
                     relief="solid", bd=1).pack(side="left")

    rr_lbl = tk.Label(left_col, textvariable=rr_var,
                      bg="#f0f2f5", fg="#2ecc71",
                      font=("Segoe UI", 9, "bold"))
    rr_lbl.pack(anchor="w", pady=(4, 0))

    # Middle column — ML Settings
    mid_col = tk.Frame(settings_container, bg="#f0f2f5")
    mid_col.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=5)

    tk.Label(mid_col, text="ML Settings", bg="#f0f2f5", fg="#8e44ad",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

    smart_var   = tk.BooleanVar(value=True)
    rules_var   = tk.StringVar(value="25")
    est_var     = tk.StringVar(value="300")
    depth_var   = tk.StringVar(value="4")
    cov_var     = tk.StringVar(value="1.0")
    minwr_var   = tk.StringVar(value="55")
    split_var   = tk.StringVar(value="70")

    _widgets.update(dict(smart_var=smart_var, rules_var=rules_var,
                         est_var=est_var, depth_var=depth_var,
                         cov_var=cov_var, minwr_var=minwr_var,
                         split_var=split_var))

    mid_rows = [
        ("Smart features:", smart_var, "check", None),
        ("Max rules:",      rules_var,  "entry", None),
        ("Estimators:",     est_var,    "entry", None),
        ("Max depth:",      depth_var,  "entry", None),
        ("Min cov. (%):",   cov_var,    "entry", None),
        ("Min WR (%):",     minwr_var,  "entry", None),
        ("Train/test (%):", split_var,  "entry", None),
    ]
    for lbl, var, kind, _ in mid_rows:
        row = tk.Frame(mid_col, bg="#f0f2f5")
        row.pack(fill="x", pady=2)
        tk.Label(row, text=lbl, bg="#f0f2f5", fg="#333",
                 font=("Segoe UI", 9), width=14, anchor="w").pack(side="left")
        if kind == "check":
            tk.Checkbutton(row, variable=var, bg="#f0f2f5",
                           activebackground="#f0f2f5").pack(side="left")
        else:
            tk.Entry(row, textvariable=var, width=10,
                     font=("Segoe UI", 9), bg="white", fg="#333",
                     relief="solid", bd=1).pack(side="left")

    # Right column — Presets
    right_col = tk.Frame(settings_container, bg="#f0f2f5")
    right_col.grid(row=0, column=2, sticky="nsew", pady=5)

    tk.Label(right_col, text="Quick Presets", bg="#f0f2f5", fg="#8e44ad",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

    def _apply_preset(sl, tp, hold, minwr):
        sl_var.set(str(sl))
        tp_var.set(str(tp))
        hold_var.set(str(hold))
        minwr_var.set(str(minwr))

    for label, sl, tp, hold, minwr, color in [
        ("Conservative", 200, 400, 100, 60, "#2980b9"),
        ("Balanced",     150, 300,  50, 55, "#27ae60"),
        ("Aggressive",   100, 150,  20, 55, "#e67e22"),
    ]:
        tk.Button(right_col, text=label,
                  bg=color, fg="white",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=12, pady=6,
                  command=lambda s=sl, t=tp, h=hold, m=minwr:
                      _apply_preset(s, t, h, m)
                  ).pack(fill="x", pady=2)

    # ── Discovery Mode ────────────────────────────────────────────────────────
    # WHY: The current approach (Quick) uses greedy search that can miss
    #      combinations that only work TOGETHER. Deep and Exhaustive search
    #      more thoroughly at the cost of time.
    # CHANGED: April 2026 — added Deep + Exhaustive modes
    _section(inner, "Discovery Mode")

    mode_frame = tk.Frame(inner, bg="#f0f2f5")
    mode_frame.pack(fill="x", padx=20, pady=6)

    discovery_mode_var = tk.StringVar(value="quick")
    _widgets['discovery_mode_var'] = discovery_mode_var

    modes = [
        (
            "quick",
            "Quick  (~5 minutes)",
            "Trains XGBoost once, picks the top 30 most important features,\n"
            "then fits decision trees to extract rules. Fast but greedy — tests\n"
            "features one at a time, so it can miss combinations that only\n"
            "work together. Good for a first look."
        ),
        (
            "deep",
            "Deep  (~20-30 minutes)",
            "Runs XGBoost 10 times with different random feature subsets.\n"
            "Collects the top 50 most important features across ALL runs.\n"
            "Then tests EVERY possible combination of those 50 features\n"
            "(up to your max depth). For depth=4, that's 230,300 combinations\n"
            "— each one scored. Finds combos that greedy search misses."
        ),
        (
            "exhaustive",
            "Exhaustive  (~1-2 hours)",
            "Genetic algorithm that searches ALL 670+ features, not just\n"
            "the top 50. Starts with 500 random combinations, scores each,\n"
            "keeps the best, breeds new combos from winners, mutates by\n"
            "swapping in random features. Repeats for 100 generations.\n"
            "Evolves toward the best combination without testing all 8 billion.\n"
            "Most thorough — highest chance of finding the absolute best rules."
        ),
    ]

    for value, label, description in modes:
        row = tk.Frame(mode_frame, bg="#f0f2f5")
        row.pack(fill="x", pady=3)

        rb = tk.Radiobutton(row, text=label, variable=discovery_mode_var,
                            value=value, bg="#f0f2f5", fg="#2c3e50",
                            font=("Segoe UI", 10, "bold"),
                            activebackground="#f0f2f5", selectcolor="#f0f2f5")
        rb.pack(anchor="w")

        tk.Label(row, text=description, bg="#f0f2f5", fg="#666",
                 font=("Segoe UI", 8), justify="left", padx=28).pack(anchor="w")

    # ── Discovery Enhancements ────────────────────────────────────────────────
    # WHY: Each enhancement makes the search deeper at the cost of time.
    #      They stack with any discovery mode (Quick/Deep/Exhaustive).
    # CHANGED: April 2026 — added 4 enhancement options
    _section(inner, "Discovery Enhancements")

    enhance_frame = tk.Frame(inner, bg="#f0f2f5")
    enhance_frame.pack(fill="x", padx=20, pady=6)

    tk.Label(enhance_frame,
             text="Stack these on top of any discovery mode for deeper search. Each one adds time but improves results.",
             bg="#f0f2f5", fg="#666", font=("Segoe UI", 8, "italic"),
             wraplength=600, justify="left").pack(anchor="w", pady=(0, 6))

    grid_threshold_var   = tk.BooleanVar(value=False)
    multi_exit_var       = tk.BooleanVar(value=False)
    walkforward_score_var = tk.BooleanVar(value=False)
    feature_interact_var = tk.BooleanVar(value=False)

    _widgets.update(dict(
        grid_threshold_var=grid_threshold_var,
        multi_exit_var=multi_exit_var,
        walkforward_score_var=walkforward_score_var,
        feature_interact_var=feature_interact_var,
    ))

    enhancements = [
        (
            grid_threshold_var,
            "Grid Threshold Search",
            "~2x slower",
            "Tests precise threshold values instead of letting the tree pick one greedily",
            "HOW IT WORKS:\n"
            "Normally, the decision tree picks ONE threshold per indicator greedily.\n"
            "Grid search tests every 5th percentile (20 values) for each indicator.\n\n"
            "For a 4-condition rule, that's 20^4 = 160,000 threshold combinations\n"
            "per feature combo. Each test is a fast numpy boolean AND.\n\n"
            "WHY IT HELPS:\n"
            "The greedy threshold might be 'RSI > 55.3' but the actual best is\n"
            "'RSI > 48.7'. Grid search finds the precise sweet spot.\n\n"
            "EXAMPLE:\n"
            "Without: H4_roc_1 > 0.1476 (tree's greedy pick, 68% WR)\n"
            "With:    H4_roc_1 > 0.1180 (grid search found, 72% WR)\n"
            "Same feature, better threshold — 4% WR improvement."
        ),
        (
            multi_exit_var,
            "Multi-Exit Labeling",
            "~5x slower",
            "Tests multiple SL/TP combinations to find the best exit per rule",
            "HOW IT WORKS:\n"
            "Normally, all candles are labeled with ONE fixed SL/TP (e.g., 150/300).\n"
            "Multi-exit labels every candle with ~15 different SL/TP combos:\n"
            "  100/150, 100/200, 100/300, 150/300, 150/450, 200/400, 200/600...\n\n"
            "Discovery runs separately for EACH label set, then compares:\n"
            "  'Rule A with SL 100/TP 200 -> 74% WR, +180 avg pips'\n"
            "  'Rule A with SL 150/TP 300 -> 65% WR, +120 avg pips'\n"
            "The best rule-exit pairing wins.\n\n"
            "WHY IT HELPS:\n"
            "Some indicator combos work better with tight SL (fast scalps),\n"
            "others with wide SL (swing trades). Current approach might miss\n"
            "a 74% WR rule because it only tested the wrong exit for it.\n\n"
            "OUTPUT:\n"
            "Each rule shows its optimal SL/TP, not just the default you set."
        ),
        (
            walkforward_score_var,
            "Walk-Forward Scoring",
            "~3x slower",
            "Filters overfit rules during discovery, not just at validation",
            "HOW IT WORKS:\n"
            "Normally, discovery scores rules on a simple 70/30 train/test split.\n"
            "A rule might get 72% on the test set but fail in 2024-2025.\n\n"
            "Walk-forward scoring splits history into multiple windows:\n"
            "  Window 1: train 2003-2010, test 2011-2012\n"
            "  Window 2: train 2005-2012, test 2013-2014\n"
            "  Window 3: train 2007-2014, test 2015-2016\n"
            "  ... up to 8-10 windows\n\n"
            "Each rule is scored on ALL out-of-sample windows.\n"
            "Final score = average WR across all windows.\n\n"
            "WHY IT HELPS:\n"
            "Rules that only work in one specific period score poorly because\n"
            "they fail in other windows. Only rules that work CONSISTENTLY\n"
            "across 20+ years survive. Like building the Validator into discovery."
        ),
        (
            feature_interact_var,
            "Feature Interactions",
            "~2x slower",
            "Discovers edges in cross-indicator relationships (ratios, divergences)",
            "HOW IT WORKS:\n"
            "The 670 features are individual indicators. But some edges only\n"
            "exist in RELATIONSHIPS between indicators.\n\n"
            "This generates interaction features from the top 50 indicators:\n"
            "  H4_rsi_14 / M15_rsi_14        -> momentum ratio across TFs\n"
            "  H1_atr_14 - H4_atr_14         -> volatility divergence\n"
            "  H1_ema_9 - H1_ema_21          -> EMA spread (trend strength)\n"
            "  abs(H4_roc_1 - D1_roc_1)      -> TF agreement score\n"
            "  H1_rsi_14 * H1_adx_14 / 100   -> momentum x trend strength\n\n"
            "Adds 500-1000 new features to the pool.\n\n"
            "WHY IT HELPS:\n"
            "Individual RSI might be useless alone, but the RATIO of H4 RSI\n"
            "to M15 RSI might be a powerful signal.\n\n"
            "EXAMPLE:\n"
            "H4_rsi_14 alone: 52% WR (useless)\n"
            "M15_rsi_14 alone: 54% WR (useless)\n"
            "H4_rsi_14 / M15_rsi_14 > 1.3: 71% WR (powerful divergence signal)"
        ),
    ]

    for var, title, time_note, short_desc, tooltip_text in enhancements:
        row = tk.Frame(enhance_frame, bg="#f0f2f5")
        row.pack(fill="x", pady=3)

        cb = tk.Checkbutton(row, text=f"{title}  ({time_note})",
                            variable=var, bg="#f0f2f5", fg="#2c3e50",
                            font=("Segoe UI", 9, "bold"),
                            activebackground="#f0f2f5", selectcolor="#f0f2f5")
        cb.pack(anchor="w")

        tk.Label(row, text=f"  {short_desc}", bg="#f0f2f5", fg="#777",
                 font=("Segoe UI", 8), justify="left", padx=28).pack(anchor="w")

        try:
            _Tooltip(cb, tooltip_text)
        except Exception:
            pass

    # Ultimate Mode button — enables all 4 enhancements at once
    def _ultimate_mode():
        grid_threshold_var.set(True)
        multi_exit_var.set(True)
        walkforward_score_var.set(True)
        feature_interact_var.set(True)
        messagebox.showinfo("Ultimate Mode",
            "All 4 enhancements enabled.\n\n"
            "Combined with Exhaustive mode, this is the most thorough\n"
            "search possible. Expect 4-8+ hours depending on your data.\n\n"
            "The results will be the best rules your historical data can produce.")

    ultimate_row = tk.Frame(enhance_frame, bg="#f0f2f5")
    ultimate_row.pack(fill="x", pady=(8, 2))

    tk.Button(ultimate_row, text="Ultimate Mode — Enable All Enhancements",
              command=_ultimate_mode,
              bg="#764ba2", fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, padx=16, pady=8, cursor="hand2").pack(anchor="w")

    tk.Label(ultimate_row,
             text="Exhaustive + all 4 enhancements. Maximum search depth. Takes hours but finds the absolute best.",
             bg="#f0f2f5", fg="#999", font=("Segoe UI", 8, "italic"),
             padx=4).pack(anchor="w")

    # ── Spacer ────────────────────────────────────────────────────────────────
    tk.Frame(inner, bg="#f0f2f5", height=1).pack(fill="x", pady=(10, 0))

    # ── Feature Toggles ────────────────────────────────────────────────────────
    try:
        from shared import feature_toggles
        toggle_widget = feature_toggles.build_toggle_widget(inner, bg="#f0f2f5")
        toggle_widget.pack(fill="x", **pad)
    except ImportError:
        pass  # Shared module not available, skip toggles

    # ── Prop Firm Target (optional) ────────────────────────────────────────────
    _section(inner, "Prop Firm Target (Optional)")

    prop_frame = tk.Frame(inner, bg="#fff8dc", relief="solid", bd=1)
    prop_frame.pack(fill="x", **pad)

    tk.Label(prop_frame, text=(
        "🎯 Optimize for a specific prop firm? Select target to auto-calculate safe SL/TP "
        "and estimate pass probability via Monte Carlo simulation."
    ), bg="#fff8dc", fg="#8B4513", font=("Segoe UI", 9), justify="left", wraplength=900,
             padx=12, pady=8).pack(fill="x")

    # Load available prop firms
    def _load_prop_firms():
        """Scan prop_firms/ directory and return list of (label, firm_data) tuples."""
        firms = []
        prop_dir = os.path.join(_project_root, 'prop_firms')
        if not os.path.isdir(prop_dir):
            return firms

        import json as _json
        for fname in sorted(os.listdir(prop_dir)):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(prop_dir, fname)
            try:
                with open(fpath, encoding='utf-8') as f:
                    data = _json.load(f)
                    firm_name = data.get('firm_name', fname.replace('.json', ''))
                    firms.append((firm_name, data))
            except Exception:
                continue
        return firms

    prop_firms = _load_prop_firms()
    firm_names = ["None (skip prop firm optimization)"] + [f[0] for f in prop_firms]
    firm_data_map = {f[0]: f[1] for f in prop_firms}
    # WHY: Phase 26 Fix 5 — Stash firm_data_map in _widgets so the
    #      _worker closure can build a real prop_data dict instead of
    #      passing None. The map maps display name → full firm JSON
    #      dict (loaded by _load_prop_firms above).
    # CHANGED: April 2026 — Phase 26 Fix 5 (audit Part B #25)
    _widgets['firm_data_map'] = firm_data_map

    prop_firm_var = tk.StringVar(value=firm_names[0])
    prop_challenge_var = tk.StringVar(value="")
    prop_account_var = tk.StringVar(value="")
    prop_limits_var = tk.StringVar(value="")
    prop_safe_sl_var = tk.StringVar(value="")
    prop_safe_tp_var = tk.StringVar(value="")

    _widgets.update(dict(
        prop_firm_var=prop_firm_var,
        prop_challenge_var=prop_challenge_var,
        prop_account_var=prop_account_var,
    ))

    prop_inner = tk.Frame(prop_frame, bg="#fff8dc", padx=12, pady=8)
    prop_inner.pack(fill="x")

    # Row 1: Firm selection
    row1 = tk.Frame(prop_inner, bg="#fff8dc")
    row1.pack(fill="x", pady=4)
    tk.Label(row1, text="Prop Firm:", bg="#fff8dc", fg="#333",
             font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side="left")
    firm_combo = ttk.Combobox(row1, textvariable=prop_firm_var, values=firm_names,
                              state="readonly", width=40, font=("Segoe UI", 9))
    firm_combo.pack(side="left", padx=(0, 10))

    # Firm rules reminder
    from shared.firm_rules_reminder import show_reminder_on_firm_change
    _p4_reminder = [None]
    show_reminder_on_firm_change(prop_firm_var, prop_frame, _p4_reminder)

    # Row 2: Challenge selection (populated when firm selected)
    row2 = tk.Frame(prop_inner, bg="#fff8dc")
    row2.pack(fill="x", pady=4)
    tk.Label(row2, text="Challenge:", bg="#fff8dc", fg="#333",
             font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side="left")
    challenge_combo = ttk.Combobox(row2, textvariable=prop_challenge_var, values=[],
                                   state="readonly", width=40, font=("Segoe UI", 9))
    challenge_combo.pack(side="left", padx=(0, 10))

    # Row 3: Account size selection (populated when challenge selected)
    row3 = tk.Frame(prop_inner, bg="#fff8dc")
    row3.pack(fill="x", pady=4)
    tk.Label(row3, text="Account Size:", bg="#fff8dc", fg="#333",
             font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side="left")
    account_combo = ttk.Combobox(row3, textvariable=prop_account_var, values=[],
                                 state="readonly", width=40, font=("Segoe UI", 9))
    account_combo.pack(side="left", padx=(0, 10))

    # Row 4: Display limits and auto-calculated SL/TP
    limits_label = tk.Label(prop_inner, textvariable=prop_limits_var,
                           bg="#fff8dc", fg="#8B4513", font=("Segoe UI", 9),
                           justify="left", anchor="w")
    limits_label.pack(fill="x", pady=(8, 0))

    safe_params_label = tk.Label(prop_inner, textvariable=prop_safe_sl_var,
                                 bg="#ffffe0", fg="#006400", font=("Segoe UI", 9, "bold"),
                                 justify="left", anchor="w", padx=8, pady=6, relief="solid", bd=1)
    safe_params_label.pack(fill="x", pady=(4, 0))

    # Callback functions
    def _on_firm_selected(*_):
        """When firm selected, populate challenge dropdown."""
        firm_name = prop_firm_var.get()
        if firm_name == "None (skip prop firm optimization)" or firm_name not in firm_data_map:
            challenge_combo['values'] = []
            prop_challenge_var.set("")
            prop_account_var.set("")
            prop_limits_var.set("")
            prop_safe_sl_var.set("")
            return

        firm_data = firm_data_map[firm_name]
        challenges = firm_data.get('challenges', [])
        challenge_labels = [c.get('challenge_name', c.get('challenge_id', '?')) for c in challenges]
        challenge_combo['values'] = challenge_labels
        if challenge_labels:
            challenge_combo.set(challenge_labels[0])
        else:
            prop_challenge_var.set("")

    def _on_challenge_selected(*_):
        """When challenge selected, populate account size dropdown."""
        firm_name = prop_firm_var.get()
        if firm_name not in firm_data_map:
            return

        firm_data = firm_data_map[firm_name]
        challenges = firm_data.get('challenges', [])
        challenge_name = prop_challenge_var.get()

        # Find matching challenge
        challenge = None
        for c in challenges:
            if c.get('challenge_name', c.get('challenge_id')) == challenge_name:
                challenge = c
                break

        if not challenge:
            account_combo['values'] = []
            prop_account_var.set("")
            return

        account_sizes = challenge.get('account_sizes', [])
        account_labels = [f"${s:,}" for s in account_sizes]
        account_combo['values'] = account_labels
        if account_labels:
            account_combo.set(account_labels[0])
        else:
            prop_account_var.set("")

    def _on_account_selected(*_):
        """When account selected, calculate limits and safe SL/TP."""
        firm_name = prop_firm_var.get()
        if firm_name not in firm_data_map:
            prop_limits_var.set("")
            prop_safe_sl_var.set("")
            return

        firm_data = firm_data_map[firm_name]
        challenges = firm_data.get('challenges', [])
        challenge_name = prop_challenge_var.get()

        # Find matching challenge
        challenge = None
        for c in challenges:
            if c.get('challenge_name', c.get('challenge_id')) == challenge_name:
                challenge = c
                break

        if not challenge:
            return

        # Get funded phase limits (what matters for live trading)
        funded = challenge.get('funded', {})
        daily_dd_pct = funded.get('max_daily_drawdown_pct', 5.0)
        total_dd_pct = funded.get('max_total_drawdown_pct', 10.0)

        # Parse account size
        account_str = prop_account_var.get()
        if not account_str:
            return
        account_size = int(account_str.replace('$', '').replace(',', ''))

        # Calculate safe SL/TP
        # WHY: Phase 26 Fix 3 — Old code used 50% of daily DD as risk
        #      per trade (= 2.5% on a 5% daily DD limit). The label
        #      said "Conservative" but 2.5% per trade is industry-
        #      aggressive — two losses in a row hit the daily limit.
        #      The standard "1% rule" is 1% risk per trade, which on
        #      a 5% daily DD is 20% of the daily limit. Phase 26
        #      changes the multiplier 0.5 → 0.2 to match.
        # CHANGED: April 2026 — Phase 26 Fix 3 — 1% rule (audit Part B #24)
        max_daily_dd_dollars = account_size * (daily_dd_pct / 100)
        safe_risk_per_trade = max_daily_dd_dollars * 0.2  # 1% rule: 20% of daily limit

        # WHY: Old code hardcoded pip_value_per_lot=10 for XAUUSD, which
        #      is WRONG for the actual MT5 broker-side calculation.
        #      1 XAUUSD lot = 100 oz × $0.01/pip = $1/pip, not $10.
        #      The cancellation math elsewhere in the codebase hides
        #      this error in P&L computations, but this panel displays
        #      the lot size directly to the user who then enters it
        #      into MT5 — so the displayed value was 10× too small
        #      (user typed 0.07 when they should have typed 0.67).
        # CHANGED: April 2026 — use correct per-symbol pip value
        #                       (audit bug family #1)
        try:
            from project2_backtesting.panels.configuration import INSTRUMENT_SPECS
            _symbol = state.selected_symbol.get() if hasattr(state, 'selected_symbol') else 'XAUUSD'
            _specs = INSTRUMENT_SPECS.get(_symbol, {})
            # INSTRUMENT_SPECS has pip_value=10 for XAUUSD (cancels in backtest math),
            # but for direct-to-MT5 lot size we need the REAL pip value.
            # Override per-symbol here:
            _real_pip_value = {
                'XAUUSD':  1.0,   # 100 oz × $0.01 = $1/pip/lot
                'XAGUSD':  5.0,   # 5000 oz × $0.001 = $5/pip/lot
                'EURUSD': 10.0,   # 100000 × 0.0001 = $10/pip/lot
                'GBPUSD': 10.0,
                'USDJPY':  6.7,   # approximate, depends on rate
                'GBPJPY':  6.7,
                'AUDUSD': 10.0,
                'USDCAD':  7.3,   # approximate
                'USDCHF': 11.0,   # approximate
                'NZDUSD': 10.0,
                'US30':    1.0,
                'NAS100':  1.0,
                'BTCUSD':  1.0,
            }.get(_symbol, 10.0)
        except Exception:
            _symbol = 'XAUUSD'
            _real_pip_value = 1.0

        # Calculate lot size based on safe risk
        # For conservative approach, assume 150-pip SL
        assumed_sl_pips = 150
        pip_value_per_lot = _real_pip_value
        safe_lot_size = safe_risk_per_trade / (assumed_sl_pips * pip_value_per_lot)

        # Calculate actual risk% based on account size
        safe_risk_pct = (safe_risk_per_trade / account_size) * 100

        # Suggest SL/TP (maintaining 2:1 R:R)
        suggested_sl = 150
        suggested_tp = 300

        # Display limits
        prop_limits_var.set(
            f"📊 {firm_name} — {challenge_name} (${account_size:,})\n"
            f"   Daily DD Limit: {daily_dd_pct}% (${max_daily_dd_dollars:,.0f})  |  "
            f"Total DD Limit: {total_dd_pct}% (${account_size * (total_dd_pct/100):,.0f})"
        )

        # Display safe parameters (showing which symbol the lot size is for)
        # WHY: Users need to see that the lot size is symbol-specific
        #      so they don't apply it to a different instrument by mistake.
        # WHY: Phase 26 Fix 5 — Old label said "Auto-Calculated Safe
        #      Parameters" implying the values would flow into
        #      Discovery. They don't (yet) — scratch_discovery doesn't
        #      consume prop_firm_data. Renamed to "Suggested" to be
        #      honest about what currently happens.
        # CHANGED: April 2026 — Phase 26 Fix 5 (audit Part B #25)
        prop_safe_sl_var.set(
            f"💡 Suggested Safe Parameters ({_symbol}) — informational:\n"
            f"   Risk per trade: {safe_risk_pct:.2f}% (${safe_risk_per_trade:,.0f})  |  "
            f"SL: {suggested_sl} pips  |  TP: {suggested_tp} pips  |  "
            f"Lot Size: {safe_lot_size:.2f} lots  "
            f"(use this value when entering trades manually in MT5)"
        )

        # WHY: Phase 26 Fix 4 — Only override SL/TP if the user has
        #      not manually edited them. Old code unconditionally
        #      wiped user choices when picking a firm.
        # CHANGED: April 2026 — Phase 26 Fix 4 (audit Part B #23)
        # Auto-update SL/TP fields in Settings section
        _current_sl = sl_var.get()
        _current_tp = tp_var.get()
        if (_current_sl, _current_tp) == _sltp_initial_defaults:
            # User hasn't touched these — safe to override
            sl_var.set(str(suggested_sl))
            tp_var.set(str(suggested_tp))
        else:
            # User customized — show the suggestion in the info text
            # but don't overwrite their choices
            pass  # The prop_safe_sl_var label already shows suggested values

    # Bind callbacks
    prop_firm_var.trace_add("write", _on_firm_selected)
    prop_challenge_var.trace_add("write", _on_challenge_selected)
    prop_account_var.trace_add("write", _on_account_selected)

    # ── Entry Timeframe Selector ──────────────────────────────────────────────
    _section(inner, "Entry Timeframe")

    tf_frame = tk.Frame(inner, bg="#e8f4fd", relief="solid", bd=1)
    tf_frame.pack(fill="x", **pad)

    tk.Label(tf_frame, text=(
        "⏱️ Select entry timeframe for signal detection. Lower TFs = more trades but more noise. "
        "Higher TFs = fewer trades but stronger signals."
    ), bg="#e8f4fd", fg="#1a3a5c", font=("Segoe UI", 9), justify="left", wraplength=900,
             padx=12, pady=8).pack(fill="x")

    tf_inner = tk.Frame(tf_frame, bg="#e8f4fd", padx=12, pady=8)
    tf_inner.pack(fill="x")

    # Row 1: Timeframe selection
    tf_row1 = tk.Frame(tf_inner, bg="#e8f4fd")
    tf_row1.pack(fill="x", pady=4)
    tk.Label(tf_row1, text="Entry Timeframe:", bg="#e8f4fd", fg="#333",
             font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side="left")

    entry_tf_var = tk.StringVar(value="H1")
    compare_all_tfs_var = tk.BooleanVar(value=False)

    _widgets.update(dict(
        entry_tf_var=entry_tf_var,
        compare_all_tfs_var=compare_all_tfs_var,
    ))

    tf_options = ["M5", "M15", "H1", "H4"]
    tf_combo = ttk.Combobox(tf_row1, textvariable=entry_tf_var, values=tf_options,
                            state="readonly", width=15, font=("Segoe UI", 9))
    tf_combo.pack(side="left", padx=(0, 20))

    # Compare all TFs checkbox
    compare_check = tk.Checkbutton(tf_row1, text="🔍 Compare ALL timeframes (M5/M15/H1/H4)",
                                   variable=compare_all_tfs_var, bg="#e8f4fd",
                                   font=("Segoe UI", 9, "bold"), fg="#1565C0",
                                   activebackground="#e8f4fd", selectcolor="#e8f4fd")
    compare_check.pack(side="left", padx=10)

    # Info label
    tf_info_label = tk.Label(tf_inner, text=(
        "💡 When 'Compare ALL' is checked, discovery runs on each TF and shows a comparison table\n"
        "   with rule counts, win rates, and best strategies per timeframe."
    ), bg="#e8f4fd", fg="#1565C0", font=("Segoe UI", 8), justify="left", anchor="w")
    tf_info_label.pack(fill="x", pady=(4, 0))

    # ── Run button + progress ─────────────────────────────────────────────────
    _section(inner, "Run")

    run_btn = tk.Button(inner,
                        text="Build Strategy from Scratch",
                        bg="#27ae60", fg="white",
                        font=("Segoe UI", 13, "bold"), bd=0, pady=12,
                        command=_on_run)
    run_btn.pack(fill="x", padx=20, pady=(4, 0))
    _widgets['run_btn'] = run_btn

    progress_var = tk.DoubleVar(value=0)
    status_var   = tk.StringVar(value="Ready.")
    elapsed_var  = tk.StringVar(value="")
    base_wr_var  = tk.StringVar(value="")

    _elapsed_var[0] = elapsed_var
    _status_var[0]  = status_var
    _progress_var[0] = progress_var
    _base_wr_var[0] = base_wr_var

    prog_bar = ttk.Progressbar(inner, variable=progress_var,
                               maximum=100, length=400)
    prog_bar.pack(fill="x", padx=20, pady=(8, 0))
    _widgets['prog_bar'] = prog_bar

    tk.Label(inner, textvariable=status_var,
             bg="#f0f2f5", fg="#2c3e50",
             font=("Segoe UI", 10), anchor="w").pack(fill="x", padx=20, pady=2)

    tk.Label(inner, textvariable=elapsed_var,
             bg="#f0f2f5", fg="#7f8c8d",
             font=("Segoe UI", 9)).pack(anchor="w", padx=20)

    info_card = tk.Frame(inner, bg="#eafaf1", relief="flat")
    info_card.pack(fill="x", padx=20, pady=(4, 0))
    tk.Label(info_card, textvariable=base_wr_var,
             bg="#eafaf1", fg="#1e8449",
             font=("Segoe UI", 9, "italic"),
             padx=10, pady=6, justify="left").pack(anchor="w")
    _widgets['info_card'] = info_card

    # ── Results ───────────────────────────────────────────────────────────────
    _section(inner, "Results")
    results_frame = tk.Frame(inner, bg="#f0f2f5")
    results_frame.pack(fill="x", **pad)
    _widgets['results_frame'] = results_frame

    # ── Action buttons ────────────────────────────────────────────────────────
    _section(inner, "Actions")
    action_row = tk.Frame(inner, bg="#f0f2f5")
    action_row.pack(fill="x", **pad)

    use_btn = tk.Button(action_row,
                        text="Use These Rules in Pipeline →",
                        bg="#8e44ad", fg="white",
                        font=("Segoe UI", 10, "bold"), bd=0, padx=12, pady=7,
                        command=_on_activate)
    use_btn.pack(side="left", padx=(0, 8))
    _widgets['use_btn'] = use_btn

    restore_btn = tk.Button(action_row,
                            text="Restore Previous Rules",
                            bg="#7f8c8d", fg="white",
                            font=("Segoe UI", 10), bd=0, padx=12, pady=7,
                            command=_on_restore)
    restore_btn.pack(side="left", padx=(0, 8))

    export_btn = tk.Button(action_row,
                           text="Export Rules JSON",
                           bg="#2980b9", fg="white",
                           font=("Segoe UI", 10), bd=0, padx=12, pady=7,
                           command=_on_export)
    export_btn.pack(side="left")

    # ── Comparison table ──────────────────────────────────────────────────────
    _section(inner, "Method Comparison")
    cmp_frame = tk.Frame(inner, bg="#f0f2f5")
    cmp_frame.pack(fill="x", **pad)
    _widgets['cmp_frame'] = cmp_frame

    # Populate if existing result already exists
    _load_existing_result()
    _render_results()
    _update_comparison()

    # Schedule scrollregion update after widgets are rendered
    canvas = _widgets.get('canvas')
    if canvas:
        inner.after(100, lambda: canvas.configure(scrollregion=canvas.bbox("all")))


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _section(parent, title):
    f = tk.Frame(parent, bg="#f0f2f5")
    f.pack(fill="x", padx=20, pady=(12, 0))
    tk.Label(f, text=title.upper(),
             bg="#f0f2f5", fg="#8e44ad",
             font=("Segoe UI", 8, "bold")).pack(anchor="w")
    tk.Frame(f, bg="#8e44ad", height=1).pack(fill="x")


def _card(parent, **kw):
    f = tk.Frame(parent, bg=kw.get('bg', '#fff'),
                 relief="flat", bd=0)
    f.pack(fill="x", pady=3)
    return f


def _load_existing_result():
    global _result
    try:
        from project4_strategy_creation.scratch_discovery import load_scratch_result
        _result = load_scratch_result()
    except Exception:
        _result = None


def _render_results():
    frame = _widgets.get('results_frame')
    if frame is None:
        return
    for w in frame.winfo_children():
        w.destroy()

    if _result is None:
        tk.Label(frame,
                 text="No results yet. Configure settings above and click Run.",
                 bg="#f0f2f5", fg="#95a5a6",
                 font=("Segoe UI", 10, "italic")).pack(anchor="w")
        return

    r = _result
    metrics = r.get('model_metrics', {})

    # Summary card
    sc = tk.Frame(frame, bg="#2c3e50", pady=10)
    sc.pack(fill="x", pady=(0, 8))
    summary_text = (
        f"Candles analyzed:  {r.get('candles_analyzed', 0):,}\n"
        f"Base win rate:     {r.get('base_win_rate', 0):.1%}  (random entry)\n"
        f"Rules found:       {len(r.get('rules', []))}\n"
        f"Test accuracy:     {metrics.get('test_accuracy', 0):.1%}"
        f"  (train: {metrics.get('train_accuracy', 0):.1%})\n"
        f"Time:              {r.get('computation_time_s', 0):.0f}s\n"
        f"Features used:     {r.get('features_used', 0)}"
        f"  ({r.get('original_features', 0)} standard"
        f" + {r.get('smart_features', 0)} smart"
        + (f" + {r.get('interaction_features', 0)} interactions)"
           if r.get('interaction_features', 0) > 0 else ")") + "\n"
        f"Discovery mode:    {r.get('discovery_mode', 'quick').upper()}"
    )
    tk.Label(sc, text=summary_text,
             bg="#2c3e50", fg="white",
             font=("Courier", 10), justify="left",
             padx=14).pack(anchor="w")

    disc_mode = metrics.get('discovery_mode', r.get('discovery_mode', 'quick'))
    if disc_mode == 'deep':
        extra = (f"XGBoost runs: {metrics.get('xgb_runs', '?')}  |  "
                 f"Feature pool: {metrics.get('features_pool', '?')}  |  "
                 f"Combos tested: {metrics.get('combos_tested', 0):,}  |  "
                 f"Candidates: {metrics.get('candidates_found', 0):,}")
        tk.Label(sc, text=extra, bg="#2c3e50", fg="#7fb3d8",
                 font=("Courier", 9), padx=14).pack(anchor="w")
    elif disc_mode == 'exhaustive':
        extra = (f"Generations: {metrics.get('generations', '?')}  |  "
                 f"Population: {metrics.get('population_size', '?')}  |  "
                 f"Total features: {metrics.get('total_features', '?')}  |  "
                 f"Candidates: {metrics.get('candidates_found', 0):,}")
        tk.Label(sc, text=extra, bg="#2c3e50", fg="#7fb3d8",
                 font=("Courier", 9), padx=14).pack(anchor="w")

    # Top features
    top_feats = metrics.get('feature_importance_top_20', [])
    if top_feats:
        tf_frame = tk.Frame(frame, bg="#f0f2f5")
        tf_frame.pack(fill="x", pady=(4, 0))
        tk.Label(tf_frame, text="Top Features:",
                 bg="#f0f2f5", fg="#2c3e50",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        for name, imp in top_feats[:15]:
            bar_len  = max(2, int(imp * 300))
            is_smart = name.startswith("SMART_")
            row      = tk.Frame(tf_frame, bg="#f0f2f5")
            row.pack(fill="x")
            color = "#9b59b6" if is_smart else "#2980b9"
            tk.Label(row, text=f"  {'*' if is_smart else ' '} {name[:40]:<42}",
                     bg="#f0f2f5", fg=color,
                     font=("Courier", 8)).pack(side="left")
            tk.Frame(row, bg=color, height=10, width=bar_len).pack(side="left")
            tk.Label(row, text=f" {imp:.3f}",
                     bg="#f0f2f5", fg="#555",
                     font=("Courier", 8)).pack(side="left")

    # Rules
    rules = r.get('rules', [])
    if rules:
        tk.Label(frame, text=f"\nRules ({len(rules)}):",
                 bg="#f0f2f5", fg="#2c3e50",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        best_wr = max(rl['win_rate'] for rl in rules)
        for i, rule in enumerate(rules, 1):
            wr     = rule['win_rate']
            cov    = rule['coverage']
            pips_r = rule['avg_pips']
            pred   = rule['prediction']
            bg     = "#eafaf1" if pred == "WIN" else "#fdf2f2"
            fg     = "#1e8449" if pred == "WIN" else "#922b21"

            rc = tk.Frame(frame, bg=bg, pady=4)
            rc.pack(fill="x", pady=1)

            star = " ★" if wr == best_wr else ""
            tk.Label(rc,
                     text=f"  Rule {i}{star}: WR {wr:.1%}  |  "
                          f"{cov:,} candles ({rule.get('coverage_pct', 0):.1f}%)  |  "
                          f"avg {pips_r:+.0f} pips  |  {pred}",
                     bg=bg, fg=fg,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w")

            opt_sl = rule.get('optimal_sl_pips')
            opt_tp = rule.get('optimal_tp_pips')
            if opt_sl and opt_tp:
                tk.Label(rc,
                         text=f"  Optimal exit: SL={opt_sl} / TP={opt_tp}  (R:R {opt_tp/opt_sl:.1f}:1)",
                         bg=bg, fg="#8e44ad",
                         font=("Segoe UI", 8, "bold")).pack(anchor="w")

            wf_score = rule.get('wf_score')
            if wf_score is not None:
                wf_wr     = rule.get('wf_avg_wr', 0)
                wf_min    = rule.get('wf_min_wr', 0)
                wf_recent = rule.get('wf_recent_wr', 0)
                wf_wins   = rule.get('wf_windows', 0)
                wf_color  = "#1e8449" if wf_wr >= 0.60 else ("#996600" if wf_wr >= 0.55 else "#922b21")
                tk.Label(rc,
                         text=f"  Walk-forward: avg {wf_wr:.1%} | min {wf_min:.1%} | "
                              f"recent {wf_recent:.1%} | {wf_wins} windows",
                         bg=bg, fg=wf_color,
                         font=("Segoe UI", 8, "bold")).pack(anchor="w")

            for cond in rule.get('conditions', []):
                feat = cond['feature']
                is_interaction = feat.startswith('INT_')
                color  = "#8e44ad" if is_interaction else "#444"
                prefix = "[x] " if is_interaction else "  "
                tk.Label(rc,
                         text=f"  {prefix}{feat} {cond['operator']} {cond['value']}",
                         bg=bg, fg=color,
                         font=("Courier", 8)).pack(anchor="w")

    # Multi-exit comparison table (if multi-exit labeling was used)
    multi_exit = r.get('multi_exit_comparison', [])
    if multi_exit:
        tk.Label(frame,
                 text=f"\nMulti-Exit Comparison  ({r.get('multi_exit_tested', 0)} SL/TP combos tested):",
                 bg="#f0f2f5", fg="#2c3e50",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 2))

        me_frame = tk.Frame(frame, bg="#f0f2f5")
        me_frame.pack(fill="x", pady=(0, 4))

        # Header row
        hdr = tk.Frame(me_frame, bg="#2c3e50")
        hdr.pack(fill="x")
        for h, w in [("SL", 6), ("TP", 6), ("R:R", 5), ("Rules", 6),
                     ("Best WR", 9), ("Best Pips", 10), ("Score", 8)]:
            tk.Label(hdr, text=h, bg="#2c3e50", fg="white",
                     font=("Courier", 8, "bold"), width=w, anchor="w").pack(side="left")

        for si, s in enumerate(sorted(multi_exit, key=lambda x: x['score'], reverse=True)):
            bg_c = "#eafaf1" if si == 0 else ("#f8f9fa" if si % 2 == 0 else "white")
            row = tk.Frame(me_frame, bg=bg_c)
            row.pack(fill="x")
            star = " *" if si == 0 else ""
            for val, w in [
                (str(s['sl']), 6), (str(s['tp']), 6), (f"{s['rr']}:1", 5),
                (str(s['rules_found']), 6), (f"{s['best_wr']:.1%}", 9),
                (f"{s['best_pips']:+.0f}", 10), (f"{s['score']:.1f}{star}", 8),
            ]:
                tk.Label(row, text=val, bg=bg_c, fg="#2c3e50",
                         font=("Courier", 8), width=w, anchor="w").pack(side="left")


def _update_comparison():
    frame = _widgets.get('cmp_frame')
    if frame is None:
        return
    for w in frame.winfo_children():
        w.destroy()

    # Gather available results from other projects
    rows = []

    _root = os.path.abspath(os.path.join(_HERE, '..', '..'))

    # Project 1 — DT results
    p1_dt_path = os.path.join(_root, 'project1_reverse_engineering',
                              'outputs', 'analysis_report.json')
    if os.path.exists(p1_dt_path):
        try:
            with open(p1_dt_path, encoding='utf-8') as f:
                p1 = json.load(f)
            p1_rules = p1.get('rules', [])
            if p1_rules:
                best_p1 = max(r.get('win_rate', 0) for r in p1_rules)
                rows.append(("Decision Tree (P1)", len(p1_rules),
                             f"{best_p1:.1%}", "—", "~1,100 trades"))
        except Exception:
            pass

    # Project 1 — XGBoost results
    p1_xgb_path = os.path.join(_root, 'project1_reverse_engineering',
                               'outputs', 'xgboost_result.json')
    if os.path.exists(p1_xgb_path):
        try:
            with open(p1_xgb_path, encoding='utf-8') as f:
                p1x = json.load(f)
            p1x_rules = p1x.get('rules', [])
            if p1x_rules:
                best_p1x = max(r.get('win_rate', 0) for r in p1x_rules)
                ta = p1x.get('model_metrics', {}).get('test_accuracy', 0)
                rows.append(("XGBoost (robot, P1)", len(p1x_rules),
                             f"{best_p1x:.1%}", f"{ta:.1%}", "~1,100 trades"))
        except Exception:
            pass

    # Project 4 — Scratch
    if _result:
        srules = _result.get('rules', [])
        best_s = max((r.get('win_rate', 0) for r in srules), default=0)
        ta_s   = _result.get('model_metrics', {}).get('test_accuracy', 0)
        nc     = _result.get('candles_analyzed', 0)
        rows.append(("Scratch Discovery (P4)", len(srules),
                     f"{best_s:.1%}", f"{ta_s:.1%}",
                     f"{nc:,} candles"))

    if not rows:
        tk.Label(frame,
                 text="Run any discovery method to see comparison.",
                 bg="#f0f2f5", fg="#95a5a6",
                 font=("Segoe UI", 9, "italic")).pack(anchor="w")
        return

    headers = ["Method", "Rules", "Best WR", "Test Acc", "Data Points"]
    widths  = [25, 7, 9, 10, 18]

    # Header row
    hrow = tk.Frame(frame, bg="#2c3e50")
    hrow.pack(fill="x")
    for h, w in zip(headers, widths):
        tk.Label(hrow, text=h, bg="#2c3e50", fg="white",
                 font=("Courier", 9, "bold"), width=w, anchor="w").pack(side="left")

    for i, (method, n_rules, best_wr, test_acc, data_pts) in enumerate(rows):
        bg   = "#eafaf1" if "Scratch" in method else ("#f8f9fa" if i % 2 == 0 else "white")
        brow = tk.Frame(frame, bg=bg)
        brow.pack(fill="x")
        for val, w in zip([method, n_rules, best_wr, test_acc, data_pts], widths):
            tk.Label(brow, text=str(val), bg=bg, fg="#2c3e50",
                     font=("Courier", 9), width=w, anchor="w").pack(side="left")

    if any("Scratch" in r[0] for r in rows):
        tk.Label(frame,
                 text="* Scratch Discovery uses 100x more data — patterns are more reliable",
                 bg="#f0f2f5", fg="#8e44ad",
                 font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(2, 0))


# ─────────────────────────────────────────────────────────────────────────────
# RUN LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def _on_run():
    global _run_thread, _result

    if _run_thread and _run_thread.is_alive():
        messagebox.showinfo("Running",
                            "Discovery is already running. Please wait.")
        return

    # WHY: Phase 26 Fix 6 — Recompute candles_path from the CURRENT
    #      entry timeframe selection. Old code used the path set at
    #      panel build time (always H1) so picking M15 in the
    #      dropdown didn't actually change which file was loaded.
    #      The error message also hardcoded H1; now it shows the
    #      actual path that was looked up.
    # CHANGED: April 2026 — Phase 26 Fix 6 (audit Part B #26)
    _entry_tf_widget = _widgets.get('entry_tf_var')
    _entry_tf = _entry_tf_widget.get() if _entry_tf_widget else 'H1'
    _data_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data'
    )
    candles_path = os.path.join(_data_dir, f"xauusd_{_entry_tf}.csv")
    # Update the stash so other code paths see the current selection
    _widgets['candles_path'] = candles_path

    if not os.path.exists(candles_path):
        messagebox.showerror(
            "Missing Data",
            f"{_entry_tf} candle data not found at:\n  {candles_path}\n\n"
            f"Please run the Data Pipeline first to load your candle history "
            f"for the {_entry_tf} timeframe."
        )
        return

    try:
        sl         = float(_widgets['sl_var'].get())
        tp         = float(_widgets['tp_var'].get())
        hold       = int(_widgets['hold_var'].get())
        spread     = float(_widgets['spread_var'].get())
        max_rules  = int(_widgets['rules_var'].get())
        n_est      = int(_widgets['est_var'].get())
        depth      = int(_widgets['depth_var'].get())
        cov_pct    = float(_widgets['cov_var'].get())
        min_wr     = float(_widgets['minwr_var'].get()) / 100.0
        split      = float(_widgets['split_var'].get()) / 100.0
        direction  = _widgets['direction_var'].get()
        use_smart  = _widgets['smart_var'].get()

        # New parameters from Part 2 & 3
        entry_tf       = _widgets['entry_tf_var'].get()
        compare_all    = _widgets['compare_all_tfs_var'].get()
        prop_firm_name = _widgets['prop_firm_var'].get()
        prop_challenge = _widgets['prop_challenge_var'].get()
        prop_account   = _widgets['prop_account_var'].get()

        discovery_mode_widget = _widgets.get('discovery_mode_var')
        discovery_mode = discovery_mode_widget.get() if discovery_mode_widget else 'quick'

        # Read enhancement flags
        # WHY: Each flag enables a deeper search technique that stacks with the mode
        # CHANGED: April 2026 — enhancement checkboxes
        _gt = _widgets.get('grid_threshold_var')
        enhance_grid = _gt.get() if _gt else False
        _me = _widgets.get('multi_exit_var')
        enhance_multi_exit = _me.get() if _me else False
        _wf = _widgets.get('walkforward_score_var')
        enhance_wf_score = _wf.get() if _wf else False
        _fi = _widgets.get('feature_interact_var')
        enhance_interactions = _fi.get() if _fi else False

    except ValueError as exc:
        messagebox.showerror("Invalid Input", f"Check your settings:\n{exc}")
        return

    # Disable run button
    _widgets['run_btn'].configure(state="disabled", bg="#95a5a6",
                                  text="Running...")
    _progress_var[0].set(0)
    _status_var[0].set("Starting...")
    _start_time[0] = time.time()

    # Elapsed timer
    def _tick():
        if _run_thread and _run_thread.is_alive():
            elapsed = time.time() - _start_time[0]
            m, s = divmod(int(elapsed), 60)
            _elapsed_var[0].set(f"Elapsed: {m}m {s:02d}s")
            _panel.after(1000, _tick)

    _panel.after(1000, _tick)

    def _progress(step, total, msg):
        pct = (step / total) * 100
        if _progress_var[0]:
            _panel.after(0, lambda: _progress_var[0].set(pct))
        if _status_var[0]:
            _panel.after(0, lambda m=msg: _status_var[0].set(m))
        # Extract base win rate from labeling step message
        if "WR:" in msg and _base_wr_var[0]:
            try:
                wr_part = msg.split("WR:")[1].split(",")[0].strip()
                _panel.after(0, lambda s=wr_part: _base_wr_var[0].set(
                    f"Base win rate: {s}  (what random entries give you)\n"
                    f"Any rule above this has a real edge."))
            except Exception:
                pass

    def _worker():
        global _result
        try:
            # WHY: Phase 26 Fix 5 — Build a real prop_data dict from
            #      the user-selected firm. Old code set prop_data=None
            #      with a TODO. Now the dict is built from
            #      firm_data_map (stashed in _widgets at panel build).
            #
            #      IMPORTANT: scratch_discovery.run_scratch_discovery
            #      currently accepts prop_firm_data as a parameter but
            #      does NOT consume it (verified in current git). The
            #      Monte Carlo pass-probability estimation that the
            #      docstring promises was never implemented. This fix
            #      builds the dict so when someone wires up
            #      scratch_discovery later, the data is already
            #      flowing — no panel changes needed.
            # CHANGED: April 2026 — Phase 26 Fix 5 (audit Part B #25)
            # Build prop firm data dict if prop firm selected
            prop_data = None
            firm_name_param = None
            if (prop_firm_name and prop_firm_name != "None (skip prop firm optimization)" and
                prop_challenge and prop_account):
                firm_name_param = prop_firm_name
                _firm_map = _widgets.get('firm_data_map', {})
                _firm_full = _firm_map.get(prop_firm_name)
                if _firm_full:
                    # Find the matching challenge by name
                    _challenges = _firm_full.get('challenges', [])
                    _ch = next(
                        (c for c in _challenges
                         if c.get('challenge_name') == prop_challenge),
                        None,
                    )
                    if _ch:
                        try:
                            _account_size_int = int(
                                str(prop_account).replace(', ', '').replace(',', '')
                            )
                        except (ValueError, TypeError):
                            _account_size_int = 0
                        prop_data = {
                            'firm_id':       _firm_full.get('firm_id'),
                            'firm_name':     prop_firm_name,
                            'challenge_id':  _ch.get('challenge_id'),
                            'challenge_name': prop_challenge,
                            'account_size':  _account_size_int,
                            'phases':        _ch.get('phases', []),
                            'funded':        _ch.get('funded', {}),
                            'trading_rules': _firm_full.get('trading_rules', []),
                            'costs':         _firm_full.get('costs', {}),
                        }
                        log.info(
                            f"[scratch_panel] Built prop_data for "
                            f"{prop_firm_name} / {prop_challenge} / "
                            f"${_account_size_int:,} — note: "
                            f"scratch_discovery does not yet consume this data."
                        )
                    else:
                        log.warning(
                            f"[scratch_panel] Could not find challenge "
                            f"'{prop_challenge}' in firm '{prop_firm_name}'."
                        )
                else:
                    log.warning(
                        f"[scratch_panel] Firm '{prop_firm_name}' not found "
                        f"in firm_data_map (loaded {len(_firm_map)} firms)."
                    )

            from project4_strategy_creation.scratch_discovery import run_scratch_discovery
            _result = run_scratch_discovery(
                candles_path=candles_path,
                entry_timeframe=entry_tf if not compare_all else None,
                sl_pips=sl,
                tp_pips=tp,
                direction=direction,
                max_hold_candles=hold,
                pip_size=0.01,
                spread_pips=spread,
                use_smart_features=use_smart,
                max_rules=max_rules,
                max_depth=depth,
                n_estimators=n_est,
                min_coverage_pct=cov_pct,
                min_win_rate=min_wr,
                train_test_split=split,
                prop_firm_name=firm_name_param,
                prop_firm_data=prop_data,
                compare_all_tfs=compare_all,
                discovery_mode=discovery_mode,
                enhance_grid_threshold=enhance_grid,
                enhance_multi_exit=enhance_multi_exit,
                enhance_walkforward_score=enhance_wf_score,
                enhance_feature_interactions=enhance_interactions,
                progress_callback=_progress,
            )
            _panel.after(0, _on_done)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            _panel.after(0, lambda e=str(exc), t=tb: _on_error(e, t))

    _run_thread = threading.Thread(target=_worker, daemon=True)
    _run_thread.start()


def _on_done():
    _widgets['run_btn'].configure(state="normal", bg="#27ae60",
                                  text="Build Strategy from Scratch")
    _progress_var[0].set(100)
    elapsed = time.time() - _start_time[0]
    m, s = divmod(int(elapsed), 60)
    _elapsed_var[0].set(f"Completed in {m}m {s:02d}s")
    n_rules = len(_result.get('rules', [])) if _result else 0
    _status_var[0].set(f"Done! {n_rules} rules found.")
    _render_results()
    _update_comparison()

    # Update canvas scrollregion to include new results
    canvas = _widgets.get('canvas')
    if canvas:
        canvas.configure(scrollregion=canvas.bbox("all"))


def _on_error(msg, tb):
    _widgets['run_btn'].configure(state="normal", bg="#27ae60",
                                  text="Build Strategy from Scratch")
    _progress_var[0].set(0)
    _status_var[0].set(f"Error: {msg}")
    messagebox.showerror("Discovery Failed",
                         f"An error occurred:\n{msg}\n\n{tb[:600]}")


# ─────────────────────────────────────────────────────────────────────────────
# ACTION BUTTONS
# ─────────────────────────────────────────────────────────────────────────────

def _on_activate():
    if _result is None:
        messagebox.showinfo("No Results", "Run discovery first.")
        return
    n = len(_result.get('rules', []))
    confirm = messagebox.askyesno(
        "Use Scratch Rules",
        f"Replace the current analysis_report.json with {n} scratch-discovered rules?\n\n"
        "The original file will be backed up automatically.\n"
        "You can restore it with 'Restore Previous Rules'.")
    if not confirm:
        return
    try:
        from project4_strategy_creation.scratch_discovery import activate_scratch_rules
        count = activate_scratch_rules()
        messagebox.showinfo("Done",
                            f"{count} rules saved to analysis_report.json.\n"
                            "Switch to Backtesting to test them.")
    except Exception as exc:
        messagebox.showerror("Error", str(exc))


def _on_restore():
    confirm = messagebox.askyesno(
        "Restore Previous Rules",
        "Restore analysis_report.json from the backup?\n"
        "This will undo the scratch rules.")
    if not confirm:
        return
    try:
        from project4_strategy_creation.scratch_discovery import restore_previous_rules
        restore_previous_rules()
        messagebox.showinfo("Restored", "Original rules restored.")
    except Exception as exc:
        messagebox.showerror("Error", str(exc))


def _on_export():
    if _result is None:
        messagebox.showinfo("No Results", "Run discovery first.")
        return
    path = filedialog.asksaveasfilename(
        title="Export Rules JSON",
        defaultextension=".json",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        initialfile="scratch_rules.json")
    if not path:
        return
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_result, f, indent=2, default=str)
        messagebox.showinfo("Exported", f"Rules saved to:\n{path}")
    except Exception as exc:
        messagebox.showerror("Error", str(exc))

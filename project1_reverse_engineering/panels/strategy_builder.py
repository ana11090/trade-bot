"""
Strategy Search Panel — Pattern Discovery UI

UI for systematically testing indicator combinations to find profitable entry patterns.
Wraps strategy_search.py with progress tracking and results display.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys
import json
import threading
import pandas as pd

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

import state

# Design tokens
BG = "#f0f2f5"
WHITE = "white"
GREEN = "#2d8a4e"
RED = "#e94560"
AMBER = "#996600"
DARK = "#1a1a2a"
GREY = "#666666"
MIDGREY = "#555566"

# Module-level widgets
_content_frame = None
_status_label = None
_search_btn_quick = None
_search_btn_full = None
_progress_bar = None
_data_status_label = None
_results_frame = None

# Controls
_tf_vars = {}
_max_conds_var = None
_min_coverage_var = None
_min_wr_var = None


_fm_cache = {"valid": None, "message": None, "checked": False}


def _check_feature_matrix():
    """Check if feature_matrix.csv exists and has real data. Result is cached."""
    global _fm_cache

    if _fm_cache["checked"]:
        return _fm_cache["valid"], _fm_cache["message"]

    fm_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'outputs', 'feature_matrix.csv'
    )

    if not os.path.exists(fm_path):
        _fm_cache.update(valid=False, message="Feature matrix file not found", checked=True)
        return False, _fm_cache["message"]

    try:
        # Read only first 5 rows — enough to count columns and spot-check data
        df_sample = pd.read_csv(fm_path, nrows=5)
        feature_cols = [c for c in df_sample.columns
                       if any(c.startswith(p) for p in ['M5_', 'M15_', 'H1_', 'H4_', 'D1_'])]

        # WHY (Phase 48 Fix 1): Old code rejected < 100 features as
        #      "expected 620". 620 is XAUUSD-with-all-TFs specific.
        #      A user running only H1+M15 may have ~250 features;
        #      only H1 may be ~125; only M15 may be ~100. Lower the
        #      threshold to 50 (truly broken) and update the message.
        # CHANGED: April 2026 — Phase 48 Fix 1 — instrument-agnostic threshold
        #          (audit Part D HIGH #67)
        if len(feature_cols) < 50:
            _fm_cache.update(valid=False,
                             message=f"Only {len(feature_cols)} features found (need at least 50)",
                             checked=True)
            return False, _fm_cache["message"]

        # WHY (Phase 48 Fix 2): Old code only sampled the first 5
        #      columns. If those happened to be M5_* but the user only
        #      loaded H1+H4 data, the check reported "empty columns"
        #      for a healthy setup. Sample 10 columns spread across
        #      the full feature list so any populated TF triggers
        #      a positive result.
        # CHANGED: April 2026 — Phase 48 Fix 2 — spread sampling
        #          (audit Part D HIGH #68)
        _step = max(1, len(feature_cols) // 10)
        _sampled = feature_cols[::_step][:10]
        has_data = sum(1 for c in _sampled if df_sample[c].notna().sum() > 0)
        if has_data == 0:
            _fm_cache.update(valid=False,
                             message="Feature matrix has empty columns — run Robot Analysis first",
                             checked=True)
            return False, _fm_cache["message"]

        # WHY (Phase 62 Fix 5): sum(1 for _ in f) counts physical lines.
        #      A CSV field containing a quoted newline (e.g. a trade comment
        #      with line breaks) inflates the count by 1 per embedded newline.
        #      Use csv.reader which correctly handles quoted fields.
        # CHANGED: April 2026 — Phase 62 Fix 5 — csv.reader row count
        #          (audit Part D MEDIUM #72)
        try:
            import csv as _csv
            with open(fm_path, 'r', encoding='utf-8', newline='') as f:
                row_count = sum(1 for _ in _csv.reader(f)) - 1  # minus header
        except Exception:
            row_count = "1000+"

        msg = f"✓ {len(feature_cols)} features × {row_count} trades ready"
        _fm_cache.update(valid=True, message=msg, checked=True)
        return True, msg

    except Exception as e:
        _fm_cache.update(valid=False, message=f"Error: {e}", checked=True)
        return False, _fm_cache["message"]


def _invalidate_cache():
    """Call this after running analysis to force a re-check.

    WHY (Phase 48 Fix 3): This was defined but not consistently
         called from external runners. Phase 49 adds the call from
         robot_analysis.py's _run_full_analysis cascade. Renamed to
         a public-style alias `invalidate_feature_matrix_cache` so
         external callers don't import a leading-underscore name.
    CHANGED: April 2026 — Phase 48 Fix 3 — public alias for external callers
             (audit Part D HIGH #69)
    """
    global _fm_cache
    _fm_cache = {"valid": None, "message": None, "checked": False}


def invalidate_feature_matrix_cache():
    """Public alias for _invalidate_cache. Call from external runners."""
    _invalidate_cache()


def _update_data_status():
    """Update the data status label."""
    global _data_status_label

    is_valid, message = _check_feature_matrix()

    if is_valid:
        _data_status_label.configure(text=message, fg=GREEN)
    else:
        _data_status_label.configure(text=f"⚠ {message}", fg=RED)


def _run_search(mode):
    """Run search in background thread."""
    global _search_btn_quick, _search_btn_full, _status_label, _progress_bar

    # Validate data first
    is_valid, message = _check_feature_matrix()
    if not is_valid:
        messagebox.showerror(
            "Data Not Ready",
            f"{message}\n\nPlease run 'Run Full Analysis' in the Robot Analysis panel first."
        )
        return

    _search_btn_quick.configure(state="disabled")
    _search_btn_full.configure(state="disabled")
    _status_label.configure(text=f"Running {mode} search...", fg=GREY)
    _progress_bar['value'] = 0

    def _worker():
        try:
            # Import here to avoid circular dependencies
            p1_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
            if p1_dir not in sys.path:
                sys.path.insert(0, p1_dir)

            from strategy_search import search_strategies

            # Read UI settings
            tf_filter = [tf for tf, var in _tf_vars.items() if var.get()]
            # WHY (Phase 58 Fix 5): Old code hardcoded the check to 5,
            #      to detect "all selected = no filter". If the TF list
            #      ever has 4 or 6 entries (e.g. user added D1 or removed
            #      M5), the shortcut never triggers and the full list is
            #      passed unnecessarily to strategy_search.
            # CHANGED: April 2026 — Phase 58 Fix 5 — compare against actual TF count
            #          (audit Part D HIGH #70)
            if len(tf_filter) == len(_tf_vars):
                tf_filter = None  # All selected = no filter

            max_conds = int(_max_conds_var.get())
            min_cov = int(_min_coverage_var.get())
            min_wr = float(_min_wr_var.get()) / 100.0

            def _progress(cur, tot, msg):
                pct = int(cur / max(tot, 1) * 100)
                state.window.after(0, lambda: _status_label.configure(
                    text=f"{msg} ({pct}%)", fg=GREY))
                state.window.after(0, lambda: _progress_bar.configure(value=pct))

            results = search_strategies(
                mode=mode,
                timeframe_filter=tf_filter,
                max_conditions=max_conds,
                min_coverage=min_cov,
                min_win_rate=min_wr,
                num_thresholds=5 if mode == "quick" else 10,
                progress_callback=_progress,
            )

            state.window.after(0, lambda: _display_results(results))
            state.window.after(0, lambda: _status_label.configure(
                text=f"Search complete! Found {results['strategies_found']} strategies in {results['search_time_s']:.0f}s",
                fg=GREEN
            ))

        except Exception as e:
            import traceback
            err = traceback.format_exc()
            state.window.after(0, lambda: _status_label.configure(
                text=f"Error: {e}", fg=RED))
            print(f"[strategy_builder] Error:\n{err}")

        finally:
            state.window.after(0, lambda: _search_btn_quick.configure(state="normal"))
            state.window.after(0, lambda: _search_btn_full.configure(state="normal"))
            state.window.after(0, lambda: _progress_bar.configure(value=100))

    threading.Thread(target=_worker, daemon=True).start()


def _load_results():
    """Load and display results from file."""
    results_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'outputs', 'strategy_search_results.json'
    )

    if not os.path.exists(results_path):
        messagebox.showinfo(
            "No Results",
            "No search results found.\n\nRun a search first using 'Quick Search' or 'Full Search'."
        )
        return

    try:
        with open(results_path, 'r', encoding='utf-8') as f:
            results = json.load(f)

        _display_results(results)
        _status_label.configure(
            text=f"Loaded {results['strategies_found']} strategies from file",
            fg=GREEN
        )

    except Exception as e:
        messagebox.showerror("Error Loading Results", str(e))


def _create_strategy_row(parent, index, strat):
    """Create one compact strategy row in the results table."""
    wr = strat['win_rate']
    avg_pips = strat['avg_pips']
    total_pips = strat['total_pips']
    score = strat['score']
    cov = strat['coverage']

    row_bg = "#fafafa" if index % 2 == 0 else WHITE

    # WHY (Phase 54 Fix 2): Old code assumed wr was always a fraction
    #      (0.65), but strategy_search returns percent in some code
    #      paths. Normalize defensively: if wr > 1.0 it's a percent
    #      (e.g., 65.0), divide by 100. Prevents 6500% display bugs.
    # CHANGED: April 2026 — Phase 54 Fix 2 — wr normalization
    #          (audit Part D MED #73)
    if wr > 1.0:
        wr = wr / 100.0
    if wr >= 0.65:
        wr_color = GREEN
    elif wr >= 0.55:
        wr_color = AMBER
    else:
        wr_color = GREY

    pips_color = GREEN if avg_pips > 0 else RED

    row = tk.Frame(parent, bg=row_bg, padx=10, pady=3)
    row.pack(fill="x", padx=5)

    cond_parts = []
    for cond in strat['conditions']:
        feat = cond['feature']
        short_feat = feat if len(feat) <= 25 else feat[:22] + "..."
        cond_parts.append(f"{short_feat}{cond['operator']}{cond['value']:.2f}")
    cond_text = " AND ".join(cond_parts)

    tk.Label(row, text=f"{index}", font=("Segoe UI", 8),
            bg=row_bg, fg=GREY, width=4, anchor="w").pack(side=tk.LEFT, padx=1)
    tk.Label(row, text=f"{wr*100:.1f}%", font=("Segoe UI", 8, "bold"),
            bg=row_bg, fg=wr_color, width=6, anchor="w").pack(side=tk.LEFT, padx=1)
    tk.Label(row, text=f"{cov}", font=("Segoe UI", 8),
            bg=row_bg, fg=DARK, width=5, anchor="w").pack(side=tk.LEFT, padx=1)
    tk.Label(row, text=f"{avg_pips:+.0f}", font=("Segoe UI", 8, "bold"),
            bg=row_bg, fg=pips_color, width=8, anchor="w").pack(side=tk.LEFT, padx=1)
    tk.Label(row, text=f"{total_pips:+.0f}", font=("Segoe UI", 8),
            bg=row_bg, fg=pips_color, width=8, anchor="w").pack(side=tk.LEFT, padx=1)
    tk.Label(row, text=f"{score:.1f}", font=("Segoe UI", 8),
            bg=row_bg, fg=DARK, width=7, anchor="w").pack(side=tk.LEFT, padx=1)
    tk.Label(row, text=cond_text, font=("Consolas", 8),
            bg=row_bg, fg="#333333", anchor="w").pack(side=tk.LEFT, padx=(5, 0), fill="x", expand=True)


def _display_results(results):
    """Display search results as a compact scrollable list."""
    global _results_frame

    for widget in _results_frame.winfo_children():
        widget.destroy()

    strategies = results.get('strategies', [])

    # Summary bar
    summary = tk.Frame(_results_frame, bg="#e8f5e9", padx=15, pady=8)
    summary.pack(fill="x", padx=5, pady=(0, 8))

    mode = results.get('search_mode', '?').upper()
    time_s = results.get('search_time_s', 0)
    n_feat = results.get('features_tested', 0)
    n_singles = results.get('total_singles_tested', 0)
    n_pairs = results.get('total_pairs_tested', 0)
    n_found = results.get('strategies_found', 0)

    tk.Label(summary,
        text=f"{mode} search: {n_found} strategies found  |  "
             f"{n_feat} features  |  {n_singles:,} singles + {n_pairs:,} pairs  |  "
             f"{time_s:.0f}s",
        font=("Segoe UI", 9, "bold"), bg="#e8f5e9", fg="#2e7d32"
    ).pack(anchor="w")

    if not strategies:
        tk.Label(_results_frame, text="No strategies found. Try lower thresholds.",
                font=("Segoe UI", 10, "italic"), bg=BG, fg=GREY).pack(pady=20)
        return

    # Table header
    header_frame = tk.Frame(_results_frame, bg="#f5f5f5", padx=10, pady=5)
    header_frame.pack(fill="x", padx=5)

    headers = [("#", 4), ("WR%", 6), ("Cov", 5), ("Avg Pips", 8), ("Total", 8), ("Score", 7), ("Conditions", 60)]
    for text, width in headers:
        tk.Label(header_frame, text=text, font=("Segoe UI", 8, "bold"),
                bg="#f5f5f5", fg=GREY, width=width, anchor="w").pack(side=tk.LEFT, padx=1)

    # Render first 50 rows immediately; lazy-load the rest
    MAX_DISPLAY = 50
    total_strategies = len(strategies)

    for i, strat in enumerate(strategies[:MAX_DISPLAY], 1):
        _create_strategy_row(_results_frame, i, strat)

    if total_strategies > MAX_DISPLAY:
        remaining = total_strategies - MAX_DISPLAY

        def _show_all(btn=None, strats=strategies, offset=MAX_DISPLAY):
            if btn:
                btn.destroy()
            for i, strat in enumerate(strats[offset:], offset + 1):
                _create_strategy_row(_results_frame, i, strat)

        show_all_btn = tk.Button(
            _results_frame,
            text=f"Show {remaining} more strategies...",
            command=lambda: _show_all(show_all_btn),
            bg="#667eea", fg="white", font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=8
        )
        show_all_btn.pack(pady=10)


def build_panel(parent):
    """Build the strategy search panel."""
    global _content_frame, _status_label, _search_btn_quick, _search_btn_full
    global _progress_bar, _data_status_label, _results_frame
    global _tf_vars, _max_conds_var, _min_coverage_var, _min_wr_var

    panel = tk.Frame(parent, bg=BG)

    # Header
    header = tk.Frame(panel, bg=WHITE, pady=20)
    header.pack(fill="x", padx=20, pady=(20, 10))

    tk.Label(
        header, text="🔍 Strategy Search",
        bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")
    ).pack()

    tk.Label(
        header,
        text="Find profitable indicator combinations — systematic pattern discovery",
        bg=WHITE, fg=GREY, font=("Segoe UI", 11)
    ).pack(pady=(5, 0))

    # Data status
    data_status_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=10)
    data_status_frame.pack(fill="x", padx=20, pady=(0, 10))

    tk.Label(
        data_status_frame,
        text="Data Status:",
        font=("Segoe UI", 10, "bold"),
        bg=WHITE, fg=DARK
    ).pack(side=tk.LEFT, padx=(0, 10))

    _data_status_label = tk.Label(
        data_status_frame,
        text="Checking...",
        font=("Segoe UI", 10),
        bg=WHITE, fg=GREY
    )
    _data_status_label.pack(side=tk.LEFT)

    # Controls section
    controls_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=15)
    controls_frame.pack(fill="x", padx=20, pady=(0, 10))

    tk.Label(
        controls_frame,
        text="Search Controls",
        font=("Segoe UI", 13, "bold"),
        bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 10))

    # Search buttons
    btn_frame = tk.Frame(controls_frame, bg=WHITE)
    btn_frame.pack(fill="x", pady=(0, 15))

    _search_btn_quick = tk.Button(
        btn_frame,
        text="Quick Search (~5 min)",
        command=lambda: _run_search("quick"),
        bg="#667eea", fg="white",
        font=("Segoe UI", 10, "bold"),
        relief=tk.FLAT, cursor="hand2",
        padx=20, pady=10
    )
    _search_btn_quick.pack(side=tk.LEFT, padx=(0, 10))

    _search_btn_full = tk.Button(
        btn_frame,
        text="Full Search (~30-60 min)",
        command=lambda: _run_search("full"),
        bg="#764ba2", fg="white",
        font=("Segoe UI", 10, "bold"),
        relief=tk.FLAT, cursor="hand2",
        padx=20, pady=10
    )
    _search_btn_full.pack(side=tk.LEFT)

    # Timeframe filter
    tf_frame = tk.Frame(controls_frame, bg=WHITE)
    tf_frame.pack(fill="x", pady=(0, 10))

    tk.Label(
        tf_frame,
        text="Timeframes:",
        font=("Segoe UI", 10, "bold"),
        bg=WHITE, fg=DARK
    ).pack(side=tk.LEFT, padx=(0, 10))

    for tf in ['M5', 'M15', 'H1', 'H4', 'D1']:
        var = tk.BooleanVar(value=True)
        _tf_vars[tf] = var
        cb = tk.Checkbutton(
            tf_frame, text=tf, variable=var,
            bg=WHITE, font=("Segoe UI", 9)
        )
        cb.pack(side=tk.LEFT, padx=5)

    # Parameters
    params_frame = tk.Frame(controls_frame, bg=WHITE)
    params_frame.pack(fill="x", pady=(0, 10))

    # Max conditions
    tk.Label(
        params_frame,
        text="Max conditions:",
        font=("Segoe UI", 9),
        bg=WHITE, fg=DARK
    ).pack(side=tk.LEFT, padx=(0, 5))

    _max_conds_var = tk.StringVar(value="2")
    max_conds_dropdown = ttk.Combobox(
        params_frame,
        textvariable=_max_conds_var,
        # Phase 48 Fix 4: extend conditions cap (audit Part D HIGH #71)
        values=["1", "2", "3", "4", "5"],
        state="readonly",
        width=5
    )
    max_conds_dropdown.pack(side=tk.LEFT, padx=(0, 15))

    # Min coverage
    tk.Label(
        params_frame,
        text="Min coverage:",
        font=("Segoe UI", 9),
        bg=WHITE, fg=DARK
    ).pack(side=tk.LEFT, padx=(0, 5))

    # WHY (Phase 54 Fix 3): Old code hardcoded "15" and "55" as the
    #      defaults, ignoring rule_min_coverage and rule_min_confidence
    #      from p1_config.json. Read from config so user-configured
    #      thresholds actually take effect.
    # CHANGED: April 2026 — Phase 54 Fix 3 — defaults from config
    #          (audit Part D MED #74)
    try:
        from project1_reverse_engineering import config_loader as _cl
        _cfg = _cl.load()
        _default_cov = str(int(_cfg.get('rule_min_coverage', '15')))
        _default_wr  = str(int(float(_cfg.get('rule_min_confidence', '0.55')) * 100))
    except Exception:
        _default_cov = "15"
        _default_wr  = "55"
    _min_coverage_var = tk.StringVar(value=_default_cov)
    min_cov_entry = tk.Entry(
        params_frame,
        textvariable=_min_coverage_var,
        width=8
    )
    min_cov_entry.pack(side=tk.LEFT, padx=(0, 15))

    # Min win rate
    tk.Label(
        params_frame,
        text="Min win rate %:",
        font=("Segoe UI", 9),
        bg=WHITE, fg=DARK
    ).pack(side=tk.LEFT, padx=(0, 5))

    _min_wr_var = tk.StringVar(value=_default_wr)   # Phase 54 Fix 3
    min_wr_entry = tk.Entry(
        params_frame,
        textvariable=_min_wr_var,
        width=8
    )
    min_wr_entry.pack(side=tk.LEFT)

    # Progress bar
    _progress_bar = ttk.Progressbar(
        controls_frame,
        mode='determinate',
        length=400
    )
    _progress_bar.pack(fill="x", pady=(10, 5))

    # Status label
    _status_label = tk.Label(
        controls_frame,
        text="Ready",
        font=("Segoe UI", 9, "italic"),
        bg=WHITE, fg=GREY
    )
    _status_label.pack(anchor="w")

    # Results section header
    results_header_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=10)
    results_header_frame.pack(fill="x", padx=20, pady=(10, 0))

    tk.Label(
        results_header_frame,
        text="Results",
        font=("Segoe UI", 13, "bold"),
        bg=WHITE, fg=DARK
    ).pack(side=tk.LEFT)

    load_btn = tk.Button(
        results_header_frame,
        text="Load Results",
        command=_load_results,
        bg=GREEN, fg="white",
        font=("Segoe UI", 9, "bold"),
        relief=tk.FLAT, cursor="hand2",
        padx=15, pady=5
    )
    load_btn.pack(side=tk.RIGHT)

    # Scrollable results area
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

    # Initial data check
    _update_data_status()

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)."""
    _update_data_status()

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


def _check_feature_matrix():
    """Check if feature_matrix.csv exists and has real data."""
    fm_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'outputs', 'feature_matrix.csv'
    )

    if not os.path.exists(fm_path):
        return False, "Feature matrix file not found"

    try:
        df = pd.read_csv(fm_path, nrows=10)
        feature_cols = [c for c in df.columns
                       if any(c.startswith(p) for p in ['M5_', 'M15_', 'H1_', 'H4_', 'D1_'])]

        if len(feature_cols) < 100:
            return False, f"Only {len(feature_cols)} features found (expected 620)"

        # Read full file to check for real data
        df_full = pd.read_csv(fm_path)
        non_null_counts = []
        for col in feature_cols[:50]:  # Check first 50
            non_null_pct = df_full[col].notna().sum() / len(df_full)
            non_null_counts.append(non_null_pct)

        avg_non_null = sum(non_null_counts) / len(non_null_counts)

        if avg_non_null < 0.5:
            return False, "Feature matrix has mostly empty columns"

        return True, f"✓ {len(feature_cols)} features × {len(df_full)} trades ready"

    except Exception as e:
        return False, f"Error reading feature matrix: {e}"


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
            if len(tf_filter) == 5:
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


def _display_results(results):
    """Display search results."""
    global _results_frame

    # Clear existing results
    for widget in _results_frame.winfo_children():
        widget.destroy()

    # Summary section
    summary_frame = tk.Frame(_results_frame, bg=WHITE, padx=20, pady=15)
    summary_frame.pack(fill="x", padx=10, pady=(0, 10))

    tk.Label(
        summary_frame,
        text="Search Results Summary",
        font=("Segoe UI", 13, "bold"),
        bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 10))

    summary_grid = tk.Frame(summary_frame, bg=WHITE)
    summary_grid.pack(fill="x")

    summary_items = [
        ("Search Mode", results['search_mode'].upper()),
        ("Time Elapsed", f"{results['search_time_s']:.0f}s ({results['search_time_s']/60:.1f} min)"),
        ("Features Tested", str(results['features_tested'])),
        ("Singles Tested", f"{results['total_singles_tested']:,}"),
        ("Pairs Tested", f"{results['total_pairs_tested']:,}"),
        ("Strategies Found", str(results['strategies_found'])),
    ]

    for i, (label, value) in enumerate(summary_items):
        row = i // 3
        col = i % 3

        item = tk.Frame(summary_grid, bg=WHITE)
        item.grid(row=row, column=col, sticky="w", padx=15, pady=3)

        tk.Label(item, text=f"{label}:", font=("Segoe UI", 9),
                bg=WHITE, fg=GREY).pack(side=tk.LEFT)
        tk.Label(item, text=value, font=("Segoe UI", 9, "bold"),
                bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(5, 0))

    # Strategies section
    strategies_frame = tk.Frame(_results_frame, bg=WHITE, padx=20, pady=15)
    strategies_frame.pack(fill="both", expand=True, padx=10, pady=5)

    tk.Label(
        strategies_frame,
        text=f"Strategies ({len(results['strategies'])} found, sorted by score)",
        font=("Segoe UI", 13, "bold"),
        bg=WHITE, fg=DARK
    ).pack(anchor="w", pady=(0, 10))

    if not results['strategies']:
        tk.Label(
            strategies_frame,
            text="No strategies found matching criteria.\n\nTry lowering min_win_rate or min_coverage.",
            font=("Segoe UI", 10, "italic"),
            bg=WHITE, fg=GREY,
            justify=tk.CENTER
        ).pack(pady=20)
        return

    # Display each strategy
    for i, strat in enumerate(results['strategies'], 1):
        wr = strat['win_rate']
        cov = strat['coverage']
        avg_pips = strat['avg_pips']
        total_pips = strat['total_pips']
        score = strat['score']
        num_conds = strat['num_conditions']

        # Color based on win rate
        if wr >= 0.65:
            card_bg = "#f0fdf4"
            header_color = GREEN
        elif wr >= 0.55:
            card_bg = "#fffbeb"
            header_color = AMBER
        else:
            card_bg = WHITE
            header_color = GREY

        # Strategy card
        card = tk.Frame(strategies_frame, bg=card_bg,
                       highlightbackground=header_color,
                       highlightthickness=3, padx=20, pady=12)
        card.pack(fill="x", pady=5)

        # Header
        header_text = (f"#{i}: {num_conds}-condition strategy — "
                      f"WR {wr*100:.1f}% | {cov} trades | "
                      f"avg {avg_pips:+.0f} pips | "
                      f"total {total_pips:+.0f} pips | "
                      f"score {score:.1f}")

        tk.Label(
            card, text=header_text,
            font=("Segoe UI", 11, "bold"),
            bg=card_bg, fg=header_color
        ).pack(anchor="w", pady=(0, 8))

        # Conditions
        tk.Label(
            card, text=f"Conditions ({len(strat['conditions'])}):",
            font=("Segoe UI", 10, "bold"),
            bg=card_bg, fg=DARK
        ).pack(anchor="w", pady=(5, 3))

        for j, cond in enumerate(strat['conditions'], 1):
            feat = cond['feature']
            op = cond['operator']
            val = cond['value']
            cond_text = f"  {j}. {feat} {op} {val:.4f}"

            tk.Label(
                card, text=cond_text,
                font=("Consolas", 9, "normal"),
                bg=card_bg, fg="#1a1a1a",
                anchor="w"
            ).pack(fill="x", padx=(20, 0), pady=1)


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
        values=["1", "2", "3"],
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

    _min_coverage_var = tk.StringVar(value="15")
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

    _min_wr_var = tk.StringVar(value="55")
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

    canvas.create_window((0, 0), window=_results_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True, padx=(20, 0))
    scrollbar.pack(side="right", fill="y", padx=(0, 20))

    # Initial data check
    _update_data_status()

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)."""
    _update_data_status()

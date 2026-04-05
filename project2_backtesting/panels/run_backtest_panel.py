"""
Project 2 - Run Backtest Panel
Execute backtest and monitor progress
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import os
import sys
import subprocess
import threading
import json
import tempfile

# Module-level variables
_output_text   = None
_progress_label = None
_progress_bar  = None
_step_label    = None
_run_button    = None
_best_label    = None
_running       = False
_best_result   = [None]  # Track best result
_rule_vars     = []  # list of (BooleanVar, rule_dict) tuples
_current_rules = []  # loaded rules
_current_source_path = [None]
_source_var    = None
_rule_canvas   = None
_rule_inner    = None

# Step weights: loading data, running matrix, completion
_STEP_MILESTONES = [0, 10, 85, 100]   # % at start of each step boundary
_STEP_NAMES = [
    "Loading data and rules...",
    "Running comparison matrix...",
    "Done!",
]


def _set_progress(bar, label, pct, text):
    bar['value'] = pct
    label.config(text=text)


def _animate_to(bar, current, target, step_ms=60):
    """Smoothly animate the progress bar from current to target %."""
    if current >= target:
        bar['value'] = target
        return
    nxt = min(current + 1, target)
    bar['value'] = nxt
    bar.after(step_ms, lambda: _animate_to(bar, nxt, target, step_ms))


def run_backtest_threaded(output_text, progress_label, progress_bar, step_label, run_button):
    """Run backtest in a separate thread"""
    global _running

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    backtest_dir = os.path.join(project_root, 'project2_backtesting')

    def ui(fn):
        """Schedule fn on the main thread."""
        progress_bar.after(0, fn)

    def run_in_thread():
        global _running
        _running = True

        run_button.config(state=tk.DISABLED, text="Running...")
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "=== BACKTEST STARTED ===\n\n")
        output_text.insert(tk.END, "Entry: next candle open (no look-ahead bias)\n\n")
        output_text.see(tk.END)

        ui(lambda: _set_progress(progress_bar, step_label, 5, "Loading data and rules..."))

        try:
            import io
            import contextlib

            # Read entry timeframe from config
            from project2_backtesting.panels.configuration import load_config
            cfg = load_config()
            entry_tf = cfg.get('winning_scenario', 'H1')
            symbol = cfg.get('symbol', 'XAUUSD').lower()

            output_text.insert(tk.END, f"Entry timeframe: {entry_tf}\n")

            # Find candle data for the selected timeframe
            candle_path = None
            candidates = [
                os.path.join(project_root, 'data', f'{symbol}_{entry_tf}.csv'),
                os.path.join(project_root, 'data', symbol, f'{entry_tf}.csv'),
            ]
            for p in candidates:
                if os.path.exists(p):
                    candle_path = p
                    break

            if candle_path is None:
                output_text.insert(tk.END, f"ERROR: {entry_tf} candle data not found!\n")
                output_text.insert(tk.END, f"Looked in:\n")
                for p in candidates:
                    output_text.insert(tk.END, f"  {p}\n")
                progress_label.config(text="Error: candle data not found", fg="#dc3545")
                return

            output_text.insert(tk.END, f"Candle data: {candle_path}\n\n")
            output_text.see(tk.END)

            # Progress callback for the backtester - shows real-time results
            def _progress(cur, tot, name, result_dict=None):
                """Called after each rule × exit combination completes."""
                pct = 10 + int(cur / max(tot, 1) * 75)

                def _update():
                    progress_bar['value'] = pct
                    step_label.config(text=f"{cur}/{tot}: {name}")

                    if result_dict:
                        trades = result_dict.get('total_trades', 0)
                        wr = result_dict.get('win_rate', 0)
                        net = result_dict.get('net_total_pips', 0)
                        pf = result_dict.get('net_profit_factor', 0)

                        # Fix: handle win_rate that might be decimal (0.82) or already percent (82.3)
                        if wr > 1:
                            wr_display = f"{wr:.1f}%"  # already in percent
                        else:
                            wr_display = f"{wr*100:.1f}%"  # convert decimal to percent

                        # Calculate approximate P&L in % (assuming $100K account, 1% risk, 150 pip SL)
                        # Each pip ≈ $6.67 at 1% risk on $100K with 150 pip SL
                        approx_pnl_pct = (net * 6.67) / 1000  # rough % of $100K

                        # Color code: green if profitable, red if not, gray if 0 trades
                        if trades == 0:
                            color = "#888888"
                            line = f"  [{cur}/{tot}] {name}: 0 trades\n"
                        elif net > 0:
                            color = "#28a745"
                            line = f"  [{cur}/{tot}] {name}: {trades} trades, WR {wr_display}, PF {pf:.2f}, {net:+,.0f} pips (~{approx_pnl_pct:+.1f}%) ✅\n"
                        else:
                            color = "#dc3545"
                            line = f"  [{cur}/{tot}] {name}: {trades} trades, WR {wr_display}, PF {pf:.2f}, {net:+,.0f} pips (~{approx_pnl_pct:+.1f}%) ❌\n"

                        output_text.insert(tk.END, line)
                        # Apply color to the last line
                        line_count = int(output_text.index('end-1c').split('.')[0])
                        output_text.tag_add(f"line_{cur}", f"{line_count}.0", f"{line_count}.end")
                        output_text.tag_config(f"line_{cur}", foreground=color)
                        output_text.see(tk.END)

                        # Update best result
                        global _best_result
                        if trades > 0:
                            if _best_result[0] is None or net > _best_result[0].get('net_total_pips', 0):
                                _best_result[0] = result_dict
                                _update_best()

                try:
                    progress_bar.after(0, _update)
                except Exception:
                    pass

            def _update_best():
                """Update the best result display."""
                global _best_result, _best_label
                if _best_result[0] and _best_label:
                    b = _best_result[0]
                    # Fix win rate display
                    wr = b['win_rate']
                    if wr > 1:
                        wr_display = f"{wr:.1f}%"
                    else:
                        wr_display = f"{wr*100:.1f}%"
                    # Add P&L %
                    net = b['net_total_pips']
                    approx_pnl_pct = (net * 6.67) / 1000
                    _best_label.config(
                        text=f"🏆 {b['rule_combo']} × {b['exit_name']}\n"
                             f"   {b['total_trades']} trades | WR {wr_display} | "
                             f"PF {b['net_profit_factor']:.2f} | {b['net_total_pips']:+,.0f} pips (~{approx_pnl_pct:+.1f}%)",
                        fg="#28a745"
                    )

            # Get only the checked rules
            global _rule_vars
            selected_rules = [rule for var, rule in _rule_vars if var.get()]

            if not selected_rules:
                output_text.insert(tk.END, "ERROR: No rules selected! Check at least one rule.\n")
                progress_label.config(text="Error: no rules selected", fg="#dc3545")
                return

            output_text.insert(tk.END, f"Testing {len(selected_rules)} selected rules x exit strategies\n\n")
            output_text.see(tk.END)

            # Write selected rules to a temporary file for the backtester
            temp_rules = {
                'rules': selected_rules,
                'discovery_method': 'selected_subset',
            }
            temp_path = os.path.join(project_root, 'project2_backtesting', 'outputs', '_temp_selected_rules.json')
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(temp_rules, f, indent=2, default=str)

            # Run the backtest with captured output
            ui(lambda: _set_progress(progress_bar, step_label, 10, "Running comparison matrix..."))

            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                sys.path.insert(0, project_root)
                from project2_backtesting.strategy_backtester import run_comparison_matrix
                results = run_comparison_matrix(
                    candles_path=candle_path,
                    timeframe=entry_tf,
                    report_path=temp_path,
                    progress_callback=_progress,
                )

            # Clean up temp file
            try:
                os.remove(temp_path)
            except Exception:
                pass

            output_text.insert(tk.END, capture.getvalue())
            output_text.see(tk.END)

            # Show completion
            ui(lambda: _animate_to(progress_bar, int(progress_bar['value']), 100, 30))
            ui(lambda: progress_bar.config(style="green.Horizontal.TProgressbar"))
            progress_label.config(text="Backtest completed successfully!", fg="#28a745")
            step_label.config(text="Done!")
            output_text.insert(tk.END, "\n=== BACKTEST COMPLETED SUCCESSFULLY ===\n")
            output_text.insert(tk.END, "\nGo to 'View Results' panel to see the comparison matrix!\n")
            output_text.see(tk.END)

            output_text.after(0, lambda: messagebox.showinfo(
                "Backtest Complete",
                "Backtest completed successfully!\n\n"
                "Go to the 'View Results' panel to review results."
            ))

        except Exception as e:
            import traceback
            err = traceback.format_exc()
            output_text.insert(tk.END, f"\n[ERROR] {e}\n{err}\n")
            output_text.see(tk.END)
            progress_label.config(text=f"Error: {str(e)[:60]}", fg="#dc3545")

        finally:
            _running = False
            run_button.config(state=tk.NORMAL, text="Run Backtest")

    threading.Thread(target=run_in_thread, daemon=True).start()


def start_backtest(output_text, progress_label, progress_bar, step_label, run_button):
    global _running, _best_result, _best_label

    if _running:
        messagebox.showwarning("Already Running", "Backtest is already running!")
        return

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    # New backtester uses analysis_report.json
    from project2_backtesting.panels.configuration import load_config
    cfg = load_config()
    symbol = cfg.get('symbol', 'XAUUSD').lower()
    entry_tf = cfg.get('winning_scenario', 'H1')
    rules_file = os.path.join(project_root, 'project1_reverse_engineering/outputs/analysis_report.json')
    price_file = os.path.join(project_root, f'data/{symbol}_{entry_tf}.csv')

    if not os.path.exists(rules_file):
        messagebox.showerror("Rules File Missing",
                             "Rules file not found!\n\n"
                             "Expected: project1_reverse_engineering/outputs/analysis_report.json\n\n"
                             "Please run Project 1 first to generate the rules.")
        return

    if not os.path.exists(price_file):
        messagebox.showerror("Price Data Missing",
                             "Price data file not found!\n\nPlease download XAUUSD data first.")
        return

    if messagebox.askyesno("Run Backtest",
                           "Start backtesting?\n\n"
                           "  1. Simulate trades on discovered rules\n"
                           "  2. Calculate performance statistics\n"
                           "  3. Generate HTML report\n\n"
                           "Estimated time: 2–5 minutes."):
        # Reset bar and best result
        progress_bar['value'] = 0
        progress_bar.config(style="Horizontal.TProgressbar")
        step_label.config(text="")
        _best_result[0] = None
        if _best_label:
            _best_label.config(text="Waiting for results...", fg="#666666")
        run_backtest_threaded(output_text, progress_label, progress_bar, step_label, run_button)


def build_panel(parent):
    global _output_text, _progress_label, _progress_bar, _step_label, _run_button, _best_label

    panel = tk.Frame(parent, bg="#ffffff")

    tk.Label(panel, text="Run Backtest", font=("Arial", 16, "bold"),
             bg="#ffffff", fg="#333333").pack(pady=(20, 5))
    tk.Label(panel, text="Execute backtest and monitor progress",
             font=("Arial", 10), bg="#ffffff", fg="#666666").pack(pady=(0, 15))

    # ── Rule Source Selector ─────────────────────────────────────
    global _source_var, _rule_canvas, _rule_inner, _rule_vars, _current_rules, _current_source_path

    source_frame = tk.LabelFrame(panel, text="📋 Rules to Test",
                                  font=("Arial", 11, "bold"), bg="#ffffff", fg="#333",
                                  padx=15, pady=10)
    source_frame.pack(fill="x", padx=20, pady=(10, 5))

    # Dropdown
    source_row = tk.Frame(source_frame, bg="#ffffff")
    source_row.pack(fill="x")
    tk.Label(source_row, text="Rule Source:", font=("Arial", 10, "bold"),
             bg="#ffffff", fg="#333").pack(side=tk.LEFT)

    _source_var = tk.StringVar(value="auto")

    def _get_available_sources():
        """Scan for all available rule files and return list of (label, path) tuples."""
        sources = []
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
        p1_report = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'analysis_report.json')
        p4_scratch = os.path.join(project_root, 'project4_strategy_creation', 'outputs', 'discovery_scratch.json')
        p1_xgboost = os.path.join(project_root, 'project1_reverse_engineering', 'outputs', 'discovery_xgboost.json')
        saved_path = os.path.join(project_root, 'saved_rules.json')

        if os.path.exists(p1_report):
            try:
                with open(p1_report, encoding='utf-8') as f:
                    d = json.load(f)
                win = [r for r in d.get('rules', []) if r.get('prediction') == 'WIN']
                method = d.get('discovery_method', 'Decision Tree')
                sources.append((f"Active Rules — {method} ({len(win)} WIN)", p1_report))
            except Exception:
                pass

        if os.path.exists(p4_scratch):
            try:
                with open(p4_scratch, encoding='utf-8') as f:
                    d = json.load(f)
                rules = d.get('rules', [])
                sources.append((f"Scratch Discovery ({len(rules)} rules)", p4_scratch))
            except Exception:
                pass

        if os.path.exists(p1_xgboost):
            try:
                with open(p1_xgboost, encoding='utf-8') as f:
                    d = json.load(f)
                rules = d.get('rules', [])
                sources.append((f"XGBoost Discovery ({len(rules)} rules)", p1_xgboost))
            except Exception:
                pass

        if os.path.exists(saved_path):
            try:
                with open(saved_path, encoding='utf-8') as f:
                    d = json.load(f)
                if d:
                    sources.append((f"Saved/Bookmarked Rules ({len(d)} rules)", saved_path))
            except Exception:
                pass

        return sources

    available_sources = _get_available_sources()
    source_labels = [s[0] for s in available_sources]
    source_paths = {s[0]: s[1] for s in available_sources}

    source_combo = ttk.Combobox(source_row, textvariable=_source_var,
                                 values=source_labels, width=45, state="readonly")
    source_combo.pack(side=tk.LEFT, padx=10)
    if source_labels:
        source_combo.set(source_labels[0])

    tk.Button(source_row, text="🔄", font=("Arial", 9),
              command=lambda: _refresh_sources(source_combo, source_paths, _get_available_sources),
              bg="#667eea", fg="white", relief=tk.FLAT, padx=6).pack(side=tk.LEFT)

    # ── Rule List with checkboxes ────────────────────────────────
    rule_list_frame = tk.Frame(source_frame, bg="#ffffff")
    rule_list_frame.pack(fill="x", pady=(10, 0))

    # Canvas for scrollable rule list
    _rule_canvas = tk.Canvas(rule_list_frame, bg="#ffffff", highlightthickness=0, height=200)
    rule_scrollbar = tk.Scrollbar(rule_list_frame, orient="vertical", command=_rule_canvas.yview)
    _rule_canvas.configure(yscrollcommand=rule_scrollbar.set)
    rule_scrollbar.pack(side=tk.RIGHT, fill="y")
    _rule_canvas.pack(side=tk.LEFT, fill="both", expand=True)

    _rule_inner = tk.Frame(_rule_canvas, bg="#ffffff")
    rule_window = _rule_canvas.create_window((0, 0), window=_rule_inner, anchor="nw")

    def _on_rule_canvas_config(event):
        _rule_canvas.configure(scrollregion=_rule_canvas.bbox("all"))
    _rule_inner.bind("<Configure>", _on_rule_canvas_config)

    def _on_rule_canvas_resize(event):
        _rule_canvas.itemconfig(rule_window, width=event.width)
    _rule_canvas.bind("<Configure>", _on_rule_canvas_resize)

    # Safe mousewheel binding for rule list — doesn't break other canvases
    def _on_enter(event):
        _rule_canvas.bind("<MouseWheel>",
            lambda e: _rule_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        # Linux
        _rule_canvas.bind("<Button-4>", lambda e: _rule_canvas.yview_scroll(-3, "units"))
        _rule_canvas.bind("<Button-5>", lambda e: _rule_canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        _rule_canvas.unbind("<MouseWheel>")
        _rule_canvas.unbind("<Button-4>")
        _rule_canvas.unbind("<Button-5>")

    _rule_canvas.bind("<Enter>", _on_enter)
    _rule_canvas.bind("<Leave>", _on_leave)

    def _load_rules_from_source(source_paths):
        """Load rules from selected source and display with checkboxes."""
        global _rule_vars, _current_rules, _current_source_path, _rule_inner, _source_var

        # Clear existing
        for w in _rule_inner.winfo_children():
            w.destroy()
        _rule_vars.clear()
        _current_rules.clear()

        label = _source_var.get()
        path = source_paths.get(label)
        if not path or not os.path.exists(path):
            tk.Label(_rule_inner, text="No rules found for this source.",
                     font=("Arial", 9), bg="#ffffff", fg="#888").pack(pady=10)
            return

        _current_source_path[0] = path

        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return

        # Extract rules depending on file format
        if isinstance(data, list):
            # saved_rules.json format: list of {id, rule, source, ...}
            rules = [entry.get('rule', entry) for entry in data]
        else:
            rules = data.get('rules', [])

        # Filter WIN only
        win_rules = [r for r in rules if r.get('prediction', 'WIN') == 'WIN']
        _current_rules = win_rules

        if not win_rules:
            tk.Label(_rule_inner, text="No WIN rules in this source.",
                     font=("Arial", 9), bg="#ffffff", fg="#888").pack(pady=10)
            return

        # Select all / deselect all buttons
        btn_row = tk.Frame(_rule_inner, bg="#ffffff")
        btn_row.pack(fill="x", pady=(0, 5))
        tk.Button(btn_row, text="Select All", font=("Arial", 8),
                  command=lambda: [v.set(True) for v, _ in _rule_vars],
                  bg="#28a745", fg="white", relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(btn_row, text="Deselect All", font=("Arial", 8),
                  command=lambda: [v.set(False) for v, _ in _rule_vars],
                  bg="#6c757d", fg="white", relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=(0, 5))

        selected_count_label = tk.Label(btn_row, text=f"{len(win_rules)}/{len(win_rules)} selected",
                                         font=("Arial", 8), bg="#ffffff", fg="#666")
        selected_count_label.pack(side=tk.LEFT, padx=10)

        def _update_count(*_):
            sel = sum(1 for v, _ in _rule_vars if v.get())
            selected_count_label.config(text=f"{sel}/{len(win_rules)} selected")

        # Show each rule with checkbox
        for i, rule in enumerate(win_rules):
            var = tk.BooleanVar(value=True)
            var.trace_add("write", _update_count)
            _rule_vars.append((var, rule))

            row = tk.Frame(_rule_inner, bg="#f8f9fa" if i % 2 == 0 else "#ffffff",
                           padx=5, pady=3)
            row.pack(fill="x")

            # Checkbox
            tk.Checkbutton(row, variable=var,
                           bg=row['bg'], selectcolor=row['bg']).pack(side=tk.LEFT)

            # Rule info
            wr = rule.get('win_rate', 0)
            pips = rule.get('avg_pips', 0)
            conds = rule.get('conditions', [])
            features = [c['feature'] for c in conds]
            feat_str = ', '.join(features[:3])
            if len(features) > 3:
                feat_str += f" +{len(features)-3}"

            color = "#28a745" if wr >= 0.65 else "#e67e22" if wr >= 0.55 else "#dc3545"
            info = f"Rule {i+1}: WR {wr:.0%} | {pips:+.0f} pips | {feat_str}"
            tk.Label(row, text=info, font=("Courier", 8), bg=row['bg'],
                     fg=color, anchor="w").pack(side=tk.LEFT, fill="x", expand=True)

            # Delete button (permanently removes from source file)
            def _delete(idx=i, r=rule):
                if messagebox.askyesno("Delete Rule",
                    f"Permanently delete Rule {idx+1} from this source file?\n"
                    f"Features: {', '.join(c['feature'] for c in r.get('conditions',[]))}"):
                    _delete_rule_from_source(idx, source_paths)
                    _load_rules_from_source(source_paths)  # refresh

            tk.Button(row, text="🗑️", font=("Arial", 7),
                      bg="#dc3545", fg="white", relief=tk.FLAT, padx=3,
                      command=_delete).pack(side=tk.RIGHT)

            # Save/bookmark button
            try:
                from shared.saved_rules import build_save_button
                sb = build_save_button(row, rule, source=label, bg=row['bg'])
                sb.pack(side=tk.RIGHT, padx=(0, 3))
            except Exception:
                pass

        _rule_canvas.configure(scrollregion=_rule_canvas.bbox("all"))

    def _delete_rule_from_source(rule_index, source_paths):
        """Delete a specific rule from the current source file."""
        global _current_source_path, _source_var
        path = _current_source_path[0]
        if not path:
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)

            if isinstance(data, list):
                # saved_rules.json format
                if rule_index < len(data):
                    data.pop(rule_index)
            else:
                rules = data.get('rules', [])
                # Find WIN rules and map index
                win_indices = [i for i, r in enumerate(rules) if r.get('prediction', 'WIN') == 'WIN']
                if rule_index < len(win_indices):
                    actual_idx = win_indices[rule_index]
                    rules.pop(actual_idx)
                    data['rules'] = rules

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            messagebox.showerror("Error", f"Could not delete rule:\n{e}")

    def _refresh_sources(combo, paths_dict, get_fn):
        global _source_var
        new_sources = get_fn()
        new_labels = [s[0] for s in new_sources]
        new_paths = {s[0]: s[1] for s in new_sources}
        paths_dict.clear()
        paths_dict.update(new_paths)
        combo['values'] = new_labels
        if new_labels:
            combo.set(new_labels[0])
        _load_rules_from_source(new_paths)

    source_combo.bind("<<ComboboxSelected>>", lambda e: _load_rules_from_source(source_paths))

    # Load initial rules
    _load_rules_from_source(source_paths)

    # ── Feature Toggles ────────────────────────────────────────────────────────
    try:
        from shared import feature_toggles
        toggle_widget = feature_toggles.build_toggle_widget(panel, bg="#ffffff")
        toggle_widget.pack(fill="x", padx=20, pady=(15, 5))
    except ImportError:
        pass  # Shared module not available, skip toggles

    # ── Run button ────────────────────────────────────────────────────────────
    _run_button = tk.Button(
        panel, text="Run Backtest",
        command=lambda: start_backtest(_output_text, _progress_label,
                                       _progress_bar, _step_label, _run_button),
        bg="#28a745", fg="white", font=("Arial", 12, "bold"),
        relief=tk.FLAT, cursor="hand2", padx=30, pady=12
    )
    _run_button.pack(pady=(0, 12))

    # ── Progress area ─────────────────────────────────────────────────────────
    prog_frame = tk.Frame(panel, bg="#ffffff")
    prog_frame.pack(fill="x", padx=40, pady=(0, 4))

    # green style
    style = ttk.Style()
    style.theme_use("default")
    style.configure("green.Horizontal.TProgressbar",
                    troughcolor="#e0e0e0", background="#28a745", thickness=18)
    style.configure("Horizontal.TProgressbar",
                    troughcolor="#e0e0e0", background="#667eea", thickness=18)

    _progress_bar = ttk.Progressbar(prog_frame, orient="horizontal",
                                    mode="determinate", length=500,
                                    style="Horizontal.TProgressbar")
    _progress_bar.pack(fill="x")

    pct_row = tk.Frame(prog_frame, bg="#ffffff")
    pct_row.pack(fill="x", pady=(3, 0))

    _progress_label = tk.Label(pct_row, text="Ready to run backtest",
                               font=("Arial", 10, "italic"),
                               bg="#ffffff", fg="#666666", anchor="w")
    _progress_label.pack(side=tk.LEFT)

    _step_label = tk.Label(pct_row, text="",
                           font=("Arial", 9), bg="#ffffff", fg="#999999", anchor="e")
    _step_label.pack(side=tk.RIGHT)

    # ── Best result so far ────────────────────────────────────────────────────
    best_frame = tk.LabelFrame(panel, text="Best Result So Far",
                                font=("Arial", 10, "bold"), bg="#ffffff", fg="#333333",
                                padx=10, pady=5)
    best_frame.pack(fill="x", padx=40, pady=(5, 0))

    _best_label = tk.Label(best_frame, text="Waiting for results...",
                           font=("Courier", 9), bg="#ffffff", fg="#666666",
                           anchor="w", justify=tk.LEFT)
    _best_label.pack(fill="x")

    # ── Output console ────────────────────────────────────────────────────────
    output_frame = tk.LabelFrame(panel, text="Backtest Output",
                                 font=("Arial", 11, "bold"), bg="#ffffff", fg="#333333",
                                 padx=10, pady=10)
    output_frame.pack(fill="both", expand=True, padx=20, pady=10)

    _output_text = scrolledtext.ScrolledText(output_frame, height=20,
                                             font=("Courier", 9), bg="#f8f9fa",
                                             fg="#333333", wrap=tk.WORD)
    _output_text.pack(fill="both", expand=True)

    _output_text.insert(tk.END, "Click 'Run Backtest' to start...\n\n")
    _output_text.insert(tk.END, "The backtest will:\n")
    _output_text.insert(tk.END, "  1. Simulate trades using discovered rules\n")
    _output_text.insert(tk.END, "  2. Test on in-sample and out-of-sample periods\n")
    _output_text.insert(tk.END, "  3. Calculate performance metrics\n")
    _output_text.insert(tk.END, "  4. Generate visual HTML report\n\n")
    _output_text.insert(tk.END, "Estimated time: 2-5 minutes\n")

    return panel


def refresh():
    global _output_text, _progress_label, _progress_bar, _step_label
    if _progress_label is not None and not _running:
        _progress_label.config(text="Ready to run backtest", fg="#666666")
        _progress_bar['value'] = 0
        _step_label.config(text="")

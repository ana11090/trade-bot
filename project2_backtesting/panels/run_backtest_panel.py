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

# Module-level variables
_output_text   = None
_progress_label = None
_progress_bar  = None
_step_label    = None
_run_button    = None
_running       = False

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
        output_text.insert(tk.END, "Using strategy_backtester with 14 WIN rules x 12 exit strategies\n")
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

            # Progress callback for the backtester
            def _progress(cur, tot, name):
                pct = 10 + int(cur / max(tot, 1) * 75)
                ui(lambda p=pct, n=name: _set_progress(
                    progress_bar, step_label, p, f"Testing {cur}/{tot}: {n}"
                ))

            # Run the backtest with captured output
            ui(lambda: _set_progress(progress_bar, step_label, 10, "Running comparison matrix..."))

            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                sys.path.insert(0, project_root)
                from project2_backtesting.strategy_backtester import run_comparison_matrix
                results = run_comparison_matrix(
                    candles_path=candle_path,
                    timeframe=entry_tf,
                    progress_callback=_progress,
                )

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
    global _running

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
        # Reset bar
        progress_bar['value'] = 0
        progress_bar.config(style="Horizontal.TProgressbar")
        step_label.config(text="")
        run_backtest_threaded(output_text, progress_label, progress_bar, step_label, run_button)


def build_panel(parent):
    global _output_text, _progress_label, _progress_bar, _step_label, _run_button

    panel = tk.Frame(parent, bg="#ffffff")

    tk.Label(panel, text="Run Backtest", font=("Arial", 16, "bold"),
             bg="#ffffff", fg="#333333").pack(pady=(20, 5))
    tk.Label(panel, text="Execute backtest and monitor progress",
             font=("Arial", 10), bg="#ffffff", fg="#666666").pack(pady=(0, 15))

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

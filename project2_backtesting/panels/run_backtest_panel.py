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

# Step weights: backtest engine is heavy, stats and report are fast
_STEP_MILESTONES = [0, 70, 85, 100]   # % at start of each step boundary
_STEP_NAMES = [
    "Step 1 / 3 — Running backtest engine...",
    "Step 2 / 3 — Computing statistics...",
    "Step 3 / 3 — Building HTML report...",
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
        output_text.see(tk.END)

        ui(lambda: _set_progress(progress_bar, step_label, 0, _STEP_NAMES[0]))

        def run_step(script, timeout, step_idx):
            """Run one subprocess step. Returns True on success."""
            label_txt  = _STEP_NAMES[step_idx]
            start_pct  = _STEP_MILESTONES[step_idx]
            end_pct    = _STEP_MILESTONES[step_idx + 1]

            ui(lambda: progress_label.config(text=label_txt, fg="#667eea"))
            output_text.insert(tk.END, f"\n[STEP {step_idx+1}/3] {label_txt}\n")
            output_text.see(tk.END)

            # Animate bar to midpoint while step runs
            mid = start_pct + (end_pct - start_pct) // 2
            ui(lambda m=mid: _animate_to(progress_bar, int(progress_bar['value']), m, 80))

            result = subprocess.run(
                [sys.executable, script],
                cwd=backtest_dir,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output_text.insert(tk.END, result.stdout)
            if result.stderr:
                output_text.insert(tk.END, f"\nErrors:\n{result.stderr}\n")
            output_text.see(tk.END)

            if result.returncode != 0:
                progress_label.config(text=f"Step {step_idx+1} failed!", fg="#dc3545")
                output_text.insert(tk.END, f"\n[ERROR] Step {step_idx+1} failed!\n")
                output_text.see(tk.END)
                return False

            # Snap to exact end milestone
            ui(lambda e=end_pct: _animate_to(progress_bar, int(progress_bar['value']), e, 30))
            return True

        try:
            steps = [
                ('backtest_engine.py', 300),
                ('compute_stats.py',   60),
                ('build_report.py',    60),
            ]

            for i, (script, timeout) in enumerate(steps):
                if not run_step(script, timeout, i):
                    return

            # All done
            ui(lambda: progress_bar.config(style="green.Horizontal.TProgressbar"))
            progress_label.config(text="Backtest completed successfully!", fg="#28a745")
            step_label.config(text="All 3 steps finished.")
            output_text.insert(tk.END, "\n=== BACKTEST COMPLETED SUCCESSFULLY ===\n")
            output_text.insert(tk.END, "\nGo to 'View Results' panel to see the report!\n")
            output_text.see(tk.END)

            output_text.after(0, lambda: messagebox.showinfo(
                "Backtest Complete",
                "Backtest completed successfully!\n\n"
                "Go to the 'View Results' panel to review the HTML report."
            ))

        except subprocess.TimeoutExpired:
            progress_label.config(text="Backtest timed out!", fg="#dc3545")
            output_text.insert(tk.END, "\n[ERROR] Backtest timed out!\n")
            output_text.see(tk.END)

        except Exception as e:
            progress_label.config(text=f"Error: {str(e)}", fg="#dc3545")
            output_text.insert(tk.END, f"\n[ERROR] {str(e)}\n")
            output_text.see(tk.END)

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
    rules_file = os.path.join(project_root, 'project1_reverse_engineering/outputs/analysis_report.json')
    price_file = os.path.join(project_root, 'data/xauusd_H1.csv')

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

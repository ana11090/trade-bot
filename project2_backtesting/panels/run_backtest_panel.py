"""
Project 2 - Run Backtest Panel
Execute backtest and monitor progress
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import os
import sys
import subprocess
import threading

# Module-level variables
_output_text = None
_progress_label = None
_run_button = None
_running = False


def run_backtest_threaded(output_text, progress_label, run_button):
    """Run backtest in a separate thread"""
    global _running

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    backtest_dir = os.path.join(project_root, 'project2_backtesting')

    def run_in_thread():
        global _running
        _running = True

        # Disable button
        run_button.config(state=tk.DISABLED, text="Running...")

        # Clear output
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "=== BACKTEST STARTED ===\n\n")
        output_text.see(tk.END)

        try:
            # Update progress
            progress_label.config(text="Running backtest engine...", fg="#667eea")
            output_text.insert(tk.END, "[STEP 1/3] Running backtest engine...\n")
            output_text.see(tk.END)

            # Run backtest engine
            result = subprocess.run(
                [sys.executable, 'backtest_engine.py'],
                cwd=backtest_dir,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            output_text.insert(tk.END, result.stdout)
            if result.stderr:
                output_text.insert(tk.END, f"\nErrors:\n{result.stderr}\n")
            output_text.see(tk.END)

            if result.returncode != 0:
                progress_label.config(text="Backtest engine failed!", fg="#dc3545")
                output_text.insert(tk.END, "\n[ERROR] Backtest engine failed!\n")
                output_text.see(tk.END)
                return

            # Update progress
            progress_label.config(text="Computing statistics...", fg="#667eea")
            output_text.insert(tk.END, "\n[STEP 2/3] Computing statistics...\n")
            output_text.see(tk.END)

            # Run compute_stats
            result = subprocess.run(
                [sys.executable, 'compute_stats.py'],
                cwd=backtest_dir,
                capture_output=True,
                text=True,
                timeout=60
            )

            output_text.insert(tk.END, result.stdout)
            if result.stderr:
                output_text.insert(tk.END, f"\nErrors:\n{result.stderr}\n")
            output_text.see(tk.END)

            if result.returncode != 0:
                progress_label.config(text="Statistics computation failed!", fg="#dc3545")
                output_text.insert(tk.END, "\n[ERROR] Statistics computation failed!\n")
                output_text.see(tk.END)
                return

            # Update progress
            progress_label.config(text="Building HTML report...", fg="#667eea")
            output_text.insert(tk.END, "\n[STEP 3/3] Building HTML report...\n")
            output_text.see(tk.END)

            # Run build_report
            result = subprocess.run(
                [sys.executable, 'build_report.py'],
                cwd=backtest_dir,
                capture_output=True,
                text=True,
                timeout=60
            )

            output_text.insert(tk.END, result.stdout)
            if result.stderr:
                output_text.insert(tk.END, f"\nErrors:\n{result.stderr}\n")
            output_text.see(tk.END)

            if result.returncode != 0:
                progress_label.config(text="Report generation failed!", fg="#dc3545")
                output_text.insert(tk.END, "\n[ERROR] Report generation failed!\n")
                output_text.see(tk.END)
                return

            # Success!
            progress_label.config(text="Backtest completed successfully!", fg="#28a745")
            output_text.insert(tk.END, "\n=== BACKTEST COMPLETED SUCCESSFULLY ===\n")
            output_text.insert(tk.END, "\nGo to 'View Results' panel to see the report!\n")
            output_text.see(tk.END)

            messagebox.showinfo(
                "Backtest Complete",
                "Backtest completed successfully!\n\n"
                "Go to the 'View Results' panel to review the HTML report."
            )

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

    # Start thread
    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()


def start_backtest(output_text, progress_label, run_button):
    """Start backtest with validation"""
    global _running

    if _running:
        messagebox.showwarning("Already Running", "Backtest is already running!")
        return

    # Check prerequisites
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    rules_file = os.path.join(project_root, 'project1_reverse_engineering/outputs/scenario_H1/rules_report_H1.txt')
    price_file = os.path.join(project_root, 'data/xauusd_H1.csv')

    if not os.path.exists(rules_file):
        messagebox.showerror(
            "Rules File Missing",
            "Rules file not found!\n\n"
            "Please run Project 1 first to discover trading rules."
        )
        return

    if not os.path.exists(price_file):
        messagebox.showerror(
            "Price Data Missing",
            "Price data file not found!\n\n"
            "Please download XAUUSD price data first using Project 1 tools."
        )
        return

    # Confirm
    result = messagebox.askyesno(
        "Run Backtest",
        "Start backtesting?\n\n"
        "This will:\n"
        "1. Simulate trades based on discovered rules\n"
        "2. Calculate performance statistics\n"
        "3. Generate HTML report\n\n"
        "This may take 2-5 minutes."
    )

    if result:
        run_backtest_threaded(output_text, progress_label, run_button)


def build_panel(parent):
    """Build the run backtest panel"""
    global _output_text, _progress_label, _run_button

    panel = tk.Frame(parent, bg="#ffffff")

    # Title
    title = tk.Label(
        panel,
        text="Run Backtest",
        font=("Arial", 16, "bold"),
        bg="#ffffff",
        fg="#333333"
    )
    title.pack(pady=(20, 10))

    subtitle = tk.Label(
        panel,
        text="Execute backtest and monitor progress",
        font=("Arial", 10),
        bg="#ffffff",
        fg="#666666"
    )
    subtitle.pack(pady=(0, 20))

    # Run button
    button_frame = tk.Frame(panel, bg="#ffffff")
    button_frame.pack(pady=10)

    _run_button = tk.Button(
        button_frame,
        text="Run Backtest",
        command=lambda: start_backtest(_output_text, _progress_label, _run_button),
        bg="#28a745",
        fg="white",
        font=("Arial", 12, "bold"),
        relief=tk.FLAT,
        cursor="hand2",
        padx=30,
        pady=12
    )
    _run_button.pack()

    # Progress label
    _progress_label = tk.Label(
        panel,
        text="Ready to run backtest",
        font=("Arial", 10, "italic"),
        bg="#ffffff",
        fg="#666666"
    )
    _progress_label.pack(pady=10)

    # Output text
    output_frame = tk.LabelFrame(
        panel,
        text="Backtest Output",
        font=("Arial", 11, "bold"),
        bg="#ffffff",
        fg="#333333",
        padx=10,
        pady=10
    )
    output_frame.pack(fill="both", expand=True, padx=20, pady=10)

    _output_text = scrolledtext.ScrolledText(
        output_frame,
        height=20,
        font=("Courier", 9),
        bg="#f8f9fa",
        fg="#333333",
        wrap=tk.WORD
    )
    _output_text.pack(fill="both", expand=True)

    # Initial message
    _output_text.insert(tk.END, "Click 'Run Backtest' to start...\n\n")
    _output_text.insert(tk.END, "The backtest will:\n")
    _output_text.insert(tk.END, "  1. Simulate trades using discovered rules\n")
    _output_text.insert(tk.END, "  2. Test on in-sample (2022-2023) and out-of-sample (2024) periods\n")
    _output_text.insert(tk.END, "  3. Calculate performance metrics\n")
    _output_text.insert(tk.END, "  4. Generate visual HTML report\n\n")
    _output_text.insert(tk.END, "Estimated time: 2-5 minutes\n")

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)"""
    global _output_text, _progress_label
    if _output_text is not None and not _running:
        _progress_label.config(text="Ready to run backtest", fg="#666666")

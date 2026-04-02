"""
Project 2 - View Results Panel
View backtest results and HTML report
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import os
import sys
import webbrowser
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from helpers import make_copyable

# Module-level variables
_output_text = None
_summary_frame = None


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
    """Display backtest comparison matrix results"""
    # Clear existing summary
    for widget in summary_frame.winfo_children():
        widget.destroy()

    data = load_summary_stats()

    if data is None:
        no_data_label = tk.Label(
            summary_frame,
            text="No backtest results found. Run the backtest first.",
            font=("Arial", 10, "italic"),
            bg="#ffffff",
            fg="#999999"
        )
        no_data_label.pack(pady=20)

        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "No backtest results available.\n\n")
        output_text.insert(tk.END, "Please run the backtest first to see results.")
        return

    results = data.get('results', [])
    if not results:
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "Backtest matrix is empty. Re-run the backtest.\n")
        return

    # Summary header
    info_frame = tk.Frame(summary_frame, bg="#e8f5e9", padx=15, pady=10)
    info_frame.pack(fill="x", padx=10, pady=(0, 10))

    combos = data.get('combinations', 0)
    elapsed = data.get('elapsed_seconds', 0)
    spread = data.get('spread_pips', 0)
    gen_at = data.get('generated_at', '?')

    tk.Label(info_frame, text=f"Backtest Matrix — {combos} combinations tested",
             bg="#e8f5e9", fg="#2e7d32", font=("Arial", 11, "bold")).pack(anchor="w")
    tk.Label(info_frame, text=f"Generated: {gen_at}  |  Spread: {spread} pips  |  Time: {elapsed:.0f}s",
             bg="#e8f5e9", fg="#555555", font=("Arial", 9)).pack(anchor="w")

    # Top results as cards
    for i, r in enumerate(results[:10]):
        # Determine colors
        net_pips = r.get('net_total_pips', 0)
        wr = r.get('win_rate', 0)
        pf = r.get('net_profit_factor', 0)
        is_profitable = net_pips > 0

        bg_color = "#f8fff8" if is_profitable else "#fff8f8"
        border_color = "#28a745" if is_profitable else "#dc3545"

        card = tk.Frame(summary_frame, bg=bg_color, highlightbackground=border_color,
                       highlightthickness=1, padx=12, pady=8)
        card.pack(fill="x", padx=10, pady=3)

        # Row 1: rank + strategy name
        header = f"#{i+1}  {r.get('rule_combo', '?')}  ×  {r.get('exit_strategy', '?')}"
        tk.Label(card, text=header, bg=bg_color, fg="#333333",
                font=("Arial", 10, "bold")).pack(anchor="w")

        # Row 2: key metrics
        trades = r.get('total_trades', 0)
        net = r.get('net_total_pips', 0)
        avg = r.get('net_avg_pips', 0)
        dd = r.get('max_dd_pips', 0)

        wr_color = "#28a745" if wr >= 55 else "#dc3545" if wr < 45 else "#ff8f00"
        pf_color = "#28a745" if pf >= 1.5 else "#dc3545" if pf < 1.0 else "#ff8f00"
        net_color = "#28a745" if net > 0 else "#dc3545"

        metrics_frame = tk.Frame(card, bg=bg_color)
        metrics_frame.pack(fill="x", pady=(3, 0))

        for label, value, color in [
            ("Trades", str(trades), "#333333"),
            ("WR", f"{wr:.1f}%", wr_color),
            ("PF", f"{pf:.2f}", pf_color),
            ("Net", f"{net:+.0f} pips", net_color),
            ("Avg", f"{avg:+.1f} pips", net_color),
            ("MaxDD", f"{dd:.0f} pips", "#dc3545"),
        ]:
            tk.Label(metrics_frame, text=f"{label}: ", bg=bg_color, fg="#888888",
                    font=("Arial", 8)).pack(side=tk.LEFT)
            tk.Label(metrics_frame, text=value, bg=bg_color, fg=color,
                    font=("Arial", 8, "bold")).pack(side=tk.LEFT, padx=(0, 12))

    # Detailed text output
    output_text.delete(1.0, tk.END)
    output_text.insert(tk.END, f"=== BACKTEST COMPARISON MATRIX ===\n")
    output_text.insert(tk.END, f"Generated: {gen_at}\n")
    output_text.insert(tk.END, f"Combinations: {combos}  |  Spread: {spread} pips\n\n")

    output_text.insert(tk.END, f"{'Rank':<5} {'Rule Combo':<22} {'Exit Strategy':<28} "
                                f"{'Trades':>6} {'WR%':>6} {'PF':>6} {'Net Pips':>10} {'Avg':>8} {'MaxDD':>8}\n")
    output_text.insert(tk.END, "-" * 110 + "\n")

    for i, r in enumerate(results):
        trades = r.get('total_trades', 0)
        wr = r.get('win_rate', 0)
        pf = r.get('net_profit_factor', 0)
        net = r.get('net_total_pips', 0)
        avg = r.get('net_avg_pips', 0)
        dd = r.get('max_dd_pips', 0)
        rule = r.get('rule_combo', '?')[:20]
        exit_s = r.get('exit_strategy', '?')[:26]

        output_text.insert(tk.END, f"#{i+1:<4} {rule:<22} {exit_s:<28} "
                                    f"{trades:>6} {wr:>5.1f}% {pf:>5.2f} {net:>+10.0f} {avg:>+7.1f} {dd:>8.0f}\n")

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
    _summary_frame.pack(fill="x", padx=20, pady=10)

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
        height=15,
        font=("Courier", 9),
        bg="#f8f9fa",
        fg="#333333",
        wrap=tk.WORD
    )
    _output_text.pack(fill="both", expand=True)

    # Initial load
    display_summary(_output_text, _summary_frame)

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)"""
    global _output_text, _summary_frame
    if _output_text is not None and _summary_frame is not None:
        display_summary(_output_text, _summary_frame)

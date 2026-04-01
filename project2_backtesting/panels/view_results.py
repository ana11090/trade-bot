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
    """Load and display summary statistics"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    stats_file = os.path.join(project_root, 'project2_backtesting/outputs/stats_summary.csv')

    if not os.path.exists(stats_file):
        return None

    try:
        df = pd.read_csv(stats_file)
        return df
    except Exception as e:
        print(f"Error loading stats: {e}")
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
    """Display summary statistics in the panel"""
    # Clear existing summary
    for widget in summary_frame.winfo_children():
        widget.destroy()

    # Load stats
    stats_df = load_summary_stats()

    if stats_df is None or len(stats_df) == 0:
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

    # Display summary for each period
    for _, row in stats_df.iterrows():
        period = row['period']
        period_frame = tk.LabelFrame(
            summary_frame,
            text=period,
            font=("Arial", 11, "bold"),
            bg="#ffffff",
            fg="#333333",
            padx=15,
            pady=10
        )
        period_frame.pack(fill="x", padx=10, pady=10)

        # Create grid of key metrics
        metrics = [
            ("Net Profit", f"${row['net_profit']:,.2f}", row['net_profit'] > 0),
            ("Total Trades", f"{int(row['total_trades'])}", None),
            ("Win Rate", f"{row['win_rate_pct']:.1f}%", row['win_rate_pct'] >= 50),
            ("Profit Factor", f"{row['profit_factor']:.2f}", row['profit_factor'] >= 1.5),
            ("Return", f"{row['return_pct']:.1f}%", row['return_pct'] > 0),
            ("Max Drawdown", f"{row['max_drawdown_pct']:.1f}%", row['max_drawdown_pct'] < 20),
        ]

        for i, (label, value, is_good) in enumerate(metrics):
            row_num = i // 2
            col_num = i % 2

            metric_frame = tk.Frame(period_frame, bg="#ffffff")
            metric_frame.grid(row=row_num, column=col_num, padx=10, pady=5, sticky="w")

            label_widget = tk.Label(
                metric_frame,
                text=f"{label}:",
                font=("Arial", 9),
                bg="#ffffff",
                fg="#666666"
            )
            label_widget.pack(side=tk.LEFT)
            make_copyable(label_widget)

            # Determine color
            if is_good is None:
                color = "#333333"
            elif is_good:
                color = "#28a745"
            else:
                color = "#dc3545"

            value_widget = tk.Label(
                metric_frame,
                text=value,
                font=("Arial", 9, "bold"),
                bg="#ffffff",
                fg=color
            )
            value_widget.pack(side=tk.LEFT, padx=(5, 0))
            make_copyable(value_widget)

    # Update output text with detailed stats
    output_text.delete(1.0, tk.END)
    output_text.insert(tk.END, "=== BACKTEST RESULTS SUMMARY ===\n\n")

    for _, row in stats_df.iterrows():
        period = row['period']
        output_text.insert(tk.END, f"{period}:\n", "header")
        output_text.insert(tk.END, f"  Total Trades: {int(row['total_trades'])}\n")
        output_text.insert(tk.END, f"  Winning Trades: {int(row['winning_trades'])}\n")
        output_text.insert(tk.END, f"  Losing Trades: {int(row['losing_trades'])}\n")
        output_text.insert(tk.END, f"  Win Rate: {row['win_rate_pct']:.1f}%\n")
        output_text.insert(tk.END, f"  Net Profit: ${row['net_profit']:,.2f}\n")
        output_text.insert(tk.END, f"  Profit Factor: {row['profit_factor']:.2f}\n")
        output_text.insert(tk.END, f"  Avg Win: ${row['avg_win']:,.2f}\n")
        output_text.insert(tk.END, f"  Avg Loss: ${row['avg_loss']:,.2f}\n")
        output_text.insert(tk.END, f"  Largest Win: ${row['largest_win']:,.2f}\n")
        output_text.insert(tk.END, f"  Largest Loss: ${row['largest_loss']:,.2f}\n")
        output_text.insert(tk.END, f"  Max Drawdown: {row['max_drawdown_pct']:.1f}%\n")
        output_text.insert(tk.END, f"  Sharpe Ratio: {row['sharpe_ratio']:.2f}\n")
        output_text.insert(tk.END, f"  Total Pips: {row['total_pips']:.1f}\n")
        output_text.insert(tk.END, f"  Final Balance: ${row['final_balance']:,.2f}\n")
        output_text.insert(tk.END, f"  Return: {row['return_pct']:.1f}%\n\n")

    output_text.tag_config("header", font=("Courier", 9, "bold"))


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

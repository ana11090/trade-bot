"""
Project 2 - Configuration Panel
Setup and configuration for backtesting
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import os
import sys

# Module-level variables for refresh
_rules_status_label = None
_price_status_label = None
_output_text = None


def check_prerequisites(output_text, rules_label, price_label, silent=True):
    """Check if all required files are available"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

    # Check for rules file (from Project 1)
    rules_file = os.path.join(project_root, 'project1_reverse_engineering/outputs/scenario_H1/rules_report_H1.txt')
    rules_exists = os.path.exists(rules_file)

    # Check for price data
    price_file = os.path.join(project_root, 'data/xauusd_H1.csv')
    price_exists = os.path.exists(price_file)

    # Update labels
    if rules_label:
        if rules_exists:
            rules_label.config(text="Rules File: Found", fg="#28a745")
        else:
            rules_label.config(text="Rules File: NOT FOUND", fg="#dc3545")

    if price_label:
        if price_exists:
            price_label.config(text="Price Data: Found", fg="#28a745")
        else:
            price_label.config(text="Price Data: NOT FOUND", fg="#dc3545")

    # Print status
    if not silent and output_text:
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "=== PREREQUISITES CHECK ===\n\n")

        if rules_exists:
            output_text.insert(tk.END, "Rules File: FOUND\n", "success")
            output_text.insert(tk.END, f"  {rules_file}\n\n")
        else:
            output_text.insert(tk.END, "Rules File: NOT FOUND\n", "error")
            output_text.insert(tk.END, "  Expected location:\n")
            output_text.insert(tk.END, f"  {rules_file}\n")
            output_text.insert(tk.END, "  Run Project 1 first to generate rules!\n\n")

        if price_exists:
            output_text.insert(tk.END, "Price Data: FOUND\n", "success")
            output_text.insert(tk.END, f"  {price_file}\n\n")
        else:
            output_text.insert(tk.END, "Price Data: NOT FOUND\n", "error")
            output_text.insert(tk.END, "  Expected location:\n")
            output_text.insert(tk.END, f"  {price_file}\n")
            output_text.insert(tk.END, "  Download price data using Project 1 tools!\n\n")

        if rules_exists and price_exists:
            output_text.insert(tk.END, "All prerequisites met! Ready to run backtest.\n", "success")
        else:
            output_text.insert(tk.END, "Missing prerequisites. Please complete setup before running backtest.\n", "error")

        # Configure tags
        output_text.tag_config("success", foreground="#28a745")
        output_text.tag_config("error", foreground="#dc3545")

    return rules_exists and price_exists


def build_panel(parent):
    """Build the configuration panel"""
    global _rules_status_label, _price_status_label, _output_text

    panel = tk.Frame(parent, bg="#ffffff")

    # Title
    title = tk.Label(
        panel,
        text="Project 2 - Backtesting Configuration",
        font=("Arial", 16, "bold"),
        bg="#ffffff",
        fg="#333333"
    )
    title.pack(pady=(20, 10))

    subtitle = tk.Label(
        panel,
        text="Validate discovered rules on in-sample and out-of-sample data",
        font=("Arial", 10),
        bg="#ffffff",
        fg="#666666"
    )
    subtitle.pack(pady=(0, 20))

    # Status section
    status_frame = tk.LabelFrame(
        panel,
        text="Prerequisites Status",
        font=("Arial", 11, "bold"),
        bg="#ffffff",
        fg="#333333",
        padx=20,
        pady=15
    )
    status_frame.pack(fill="x", padx=20, pady=10)

    _rules_status_label = tk.Label(
        status_frame,
        text="Rules File: Checking...",
        font=("Arial", 10),
        bg="#ffffff",
        fg="#666666"
    )
    _rules_status_label.pack(anchor="w", pady=5)

    _price_status_label = tk.Label(
        status_frame,
        text="Price Data: Checking...",
        font=("Arial", 10),
        bg="#ffffff",
        fg="#666666"
    )
    _price_status_label.pack(anchor="w", pady=5)

    # Check button
    check_btn = tk.Button(
        status_frame,
        text="Check Prerequisites",
        command=lambda: check_prerequisites(_output_text, _rules_status_label, _price_status_label, silent=False),
        bg="#667eea",
        fg="white",
        font=("Arial", 10, "bold"),
        relief=tk.FLAT,
        cursor="hand2",
        padx=20,
        pady=8
    )
    check_btn.pack(pady=(10, 0))

    # Configuration info
    config_frame = tk.LabelFrame(
        panel,
        text="Backtest Configuration",
        font=("Arial", 11, "bold"),
        bg="#ffffff",
        fg="#333333",
        padx=20,
        pady=15
    )
    config_frame.pack(fill="x", padx=20, pady=10)

    config_text = """
Current Configuration:
  - In-Sample Period: 2022-01-01 to 2023-12-31
  - Out-of-Sample Period: 2024-01-01 to 2024-12-31
  - Starting Capital: $10,000
  - Risk Per Trade: 1% of balance
  - Stop Loss: ATR * 1.5
  - Take Profit 1: ATR * 1.5 (50% position)
  - Take Profit 2: ATR * 3.0 (50% position)
  - Commission: $4 per lot (round trip)
  - Spread: 0.3 pips

To modify these settings, edit backtest_engine.py configuration section.
    """

    config_label = tk.Label(
        config_frame,
        text=config_text,
        font=("Courier", 9),
        bg="#ffffff",
        fg="#333333",
        justify=tk.LEFT
    )
    config_label.pack(anchor="w")

    # Output text
    output_frame = tk.LabelFrame(
        panel,
        text="Status Output",
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

    # Initial check
    check_prerequisites(_output_text, _rules_status_label, _price_status_label, silent=False)

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)"""
    global _rules_status_label, _price_status_label, _output_text
    if _output_text is not None:
        check_prerequisites(_output_text, _rules_status_label, _price_status_label, silent=False)

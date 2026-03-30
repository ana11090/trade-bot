"""
Configuration Panel for Project 1 - Reverse Engineering
Allows setting parameters and downloading price data
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os
import sys
import threading
import subprocess

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

import state

# Module-level variables to store widget references for refresh
_trade_status_label = None
_price_status_label = None
_output_text = None


def build_panel(parent):
    global _trade_status_label, _price_status_label, _output_text
    """Build the configuration panel"""
    panel = tk.Frame(parent, bg="#f0f2f5")

    # Title
    title_frame = tk.Frame(panel, bg="white", pady=20)
    title_frame.pack(fill="x", padx=20, pady=(20, 10))

    tk.Label(title_frame, text="⚙️ Configuration & Data Download",
             bg="white", fg="#16213e",
             font=("Segoe UI", 18, "bold")).pack()

    tk.Label(title_frame, text="Set up parameters and download OHLCV price data",
             bg="white", fg="#666",
             font=("Segoe UI", 11)).pack(pady=(5, 0))

    # Main content
    content_frame = tk.Frame(panel, bg="#f0f2f5")
    content_frame.pack(fill="both", expand=True, padx=20, pady=10)

    # Left column - Parameters
    left_frame = tk.Frame(content_frame, bg="white", padx=20, pady=20)
    left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

    tk.Label(left_frame, text="📋 Parameters",
             bg="white", fg="#16213e",
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 15))

    # Parameters
    params = [
        ("Symbol", "XAUUSD"),
        ("Broker Timezone", "EET"),
        ("Min Lookback Candles", "200"),
        ("Train/Test Split", "0.80"),
        ("Random Forest Trees", "500"),
        ("Max Tree Depth", "6"),
        ("Min Samples per Leaf", "10"),
        ("Rule Min Confidence", "0.65"),
        ("Match Rate Threshold", "0.70"),
    ]

    param_entries = {}

    for label_text, default_value in params:
        row = tk.Frame(left_frame, bg="white")
        row.pack(fill="x", pady=5)

        tk.Label(row, text=label_text + ":",
                 bg="white", fg="#333",
                 font=("Segoe UI", 10), width=22, anchor="w").pack(side="left")

        entry = tk.Entry(row, font=("Segoe UI", 10), width=15)
        entry.insert(0, default_value)
        entry.pack(side="left", padx=(10, 0))

        param_entries[label_text] = entry

    # Status indicators
    status_frame = tk.Frame(left_frame, bg="white", pady=15)
    status_frame.pack(fill="x", pady=(20, 0))

    tk.Label(status_frame, text="Data Status:",
             bg="white", fg="#333",
             font=("Segoe UI", 10, "bold")).pack(anchor="w")

    # Trade data status (from Project 0)
    _trade_status_label = tk.Label(status_frame, text="",
                                   bg="white",
                                   font=("Segoe UI", 9))
    _trade_status_label.pack(anchor="w", pady=(5, 5))

    # Price data status
    _price_status_label = tk.Label(status_frame, text="",
                                   bg="white",
                                   font=("Segoe UI", 9))
    _price_status_label.pack(anchor="w", pady=(0, 0))

    # Right column - Data Download
    right_frame = tk.Frame(content_frame, bg="white", padx=20, pady=20)
    right_frame.pack(side="left", fill="both", expand=True, padx=(10, 0))

    tk.Label(right_frame, text="📊 Download Price Data",
             bg="white", fg="#16213e",
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 15))

    tk.Label(right_frame, text="Download XAUUSD OHLCV data for all timeframes",
             bg="white", fg="#666",
             font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 10))

    # Info box
    info_frame = tk.Frame(right_frame, bg="#fff3cd", padx=10, pady=10)
    info_frame.pack(fill="x", pady=(0, 15))

    tk.Label(info_frame, text="⚠️ Important:",
             bg="#fff3cd", fg="#856404",
             font=("Segoe UI", 9, "bold")).pack(anchor="w")

    tk.Label(info_frame,
             text="• yfinance has limited intraday data\n"
                  "• May not cover your trade dates\n"
                  "• For best results, use MetaTrader 5",
             bg="#fff3cd", fg="#856404",
             font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(5, 0))

    # Download buttons
    download_mt5_btn = tk.Button(right_frame, text="🔽 Download from MT5 (Recommended)",
                                bg="#27ae60", fg="white",
                                font=("Segoe UI", 10, "bold"),
                                bd=0, pady=10, cursor="hand2",
                                command=lambda: download_data_mt5(_output_text))
    download_mt5_btn.pack(fill="x", pady=(0, 5))

    download_yf_btn = tk.Button(right_frame, text="🔽 Download from yfinance (Limited)",
                               bg="#95a5a6", fg="white",
                               font=("Segoe UI", 10, "bold"),
                               bd=0, pady=10, cursor="hand2",
                               command=lambda: download_data(_output_text, None))
    download_yf_btn.pack(fill="x", pady=(0, 10))

    # Check data button
    check_btn = tk.Button(right_frame, text="🔍 Check Data Status",
                         bg="#95a5a6", fg="white",
                         font=("Segoe UI", 10, "bold"),
                         bd=0, pady=10, cursor="hand2",
                         command=refresh)
    check_btn.pack(fill="x", pady=(0, 10))

    # Output text area
    tk.Label(right_frame, text="Output:",
             bg="white", fg="#333",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 5))

    _output_text = scrolledtext.ScrolledText(right_frame,
                                            height=10,
                                            font=("Consolas", 9),
                                            bg="#2c3e50", fg="#ecf0f1",
                                            insertbackground="white")
    _output_text.pack(fill="both", expand=True)

    # Initial check
    check_all_data(_output_text, _trade_status_label, _price_status_label, silent=True)

    return panel


def check_all_data(output_text, trade_status_label, price_status_label, silent=False):
    """Check both trade data (from Project 0) and price data files"""
    if not silent:
        output_text.delete('1.0', tk.END)

    # Check trade data from Project 0
    if state.loaded_data is not None:
        num_trades = len(state.loaded_data)
        trade_status_label.config(
            text=f"✓ Trade Data: {num_trades} trades loaded (from Project 0)",
            fg="#27ae60"
        )
        if not silent:
            output_text.insert(tk.END, f"✓ Trade Data: {num_trades} trades loaded from Project 0 grid\n\n")
    else:
        trade_status_label.config(
            text="⚠️ Trade Data: No data loaded in Project 0",
            fg="#e74c3c"
        )
        if not silent:
            output_text.insert(tk.END, "⚠️ Trade Data: No data loaded\n")
            output_text.insert(tk.END, "   Go to Project 0 → Data Pipeline → Load the data first\n\n")

    # Check price data files
    data_folder = os.path.join(os.path.dirname(__file__), '../../data')
    timeframes = ['M5', 'M15', 'H1', 'H4']

    if not silent:
        output_text.insert(tk.END, "Checking price data files...\n")

    found_count = 0

    for tf in timeframes:
        filepath = os.path.join(data_folder, f'xauusd_{tf}.csv')

        if os.path.exists(filepath):
            found_count += 1
            size = os.path.getsize(filepath)
            if not silent:
                output_text.insert(tk.END, f"  ✓ {tf}: Found ({size:,} bytes)\n")
        else:
            if not silent:
                output_text.insert(tk.END, f"  ✗ {tf}: Not found\n")

    if not silent:
        output_text.insert(tk.END, f"\nFound {found_count}/4 price data files\n")

    # Update price status label
    if found_count == 4:
        price_status_label.config(text=f"✓ Price Data: All 4 timeframes present", fg="#27ae60")
    elif found_count > 0:
        price_status_label.config(text=f"⚠️ Price Data: {found_count}/4 timeframes found", fg="#f39c12")
    else:
        price_status_label.config(text="⚠️ Price Data: No files found", fg="#e74c3c")


def check_data(output_text, status_label, silent=False):
    """Legacy function for compatibility - redirects to check_all_data"""
    # This function signature is used by the download button
    # Create dummy labels if needed
    dummy_trade = tk.Label()
    dummy_price = tk.Label()
    check_all_data(output_text, dummy_trade, dummy_price, silent)


def download_data_mt5(output_text):
    """Download price data using MT5 download script"""
    output_text.delete('1.0', tk.END)
    output_text.insert(tk.END, "Starting MT5 download process...\n")
    output_text.insert(tk.END, "Make sure MetaTrader 5 is running and logged in!\n\n")

    def run_mt5_download():
        try:
            # Run the MT5 download script
            import subprocess

            script_path = os.path.join(os.path.dirname(__file__), '..', 'download_data_mt5.py')

            # Run script and capture output
            process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Stream output to UI
            for line in iter(process.stdout.readline, ''):
                if line:
                    def append_line(l=line):
                        output_text.insert(tk.END, l)
                        output_text.see(tk.END)
                    output_text.after(0, append_line)

            process.wait()

            def show_done():
                if process.returncode == 0:
                    messagebox.showinfo("Download Complete",
                                      "MT5 data download completed successfully!\n\n"
                                      "Click 'Check Data Status' to verify files.")
                    refresh()  # Refresh status displays
                else:
                    messagebox.showwarning("Download Issues",
                                         "Download completed with some issues.\n\n"
                                         "Check the output for details.\n"
                                         "You may need to:\n"
                                         "1. Make sure MT5 is running\n"
                                         "2. Check you're logged in\n"
                                         "3. Try manual export method")

            output_text.after(0, show_done)

        except Exception as e:
            def show_error():
                output_text.insert(tk.END, f"\n\nERROR: {str(e)}\n")
                messagebox.showerror("Error", f"Download failed:\n{str(e)}")

            output_text.after(0, show_error)

    # Run in background thread
    thread = threading.Thread(target=run_mt5_download, daemon=True)
    thread.start()


def download_data(output_text, status_label):
    """Download price data using the download script"""
    output_text.delete('1.0', tk.END)
    output_text.insert(tk.END, "Starting download process...\n")
    output_text.insert(tk.END, "This may take several minutes.\n\n")

    def run_download():
        try:
            # Import and run the download script
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

            # Redirect stdout to capture output
            import io
            old_stdout = sys.stdout
            sys.stdout = buffer = io.StringIO()

            try:
                import download_price_data

                # Run the download
                download_price_data.download_data_yfinance()
                download_price_data.verify_data_coverage()

            except Exception as e:
                buffer.write(f"\nERROR: {str(e)}\n")
                import traceback
                buffer.write(traceback.format_exc())

            finally:
                sys.stdout = old_stdout
                result = buffer.getvalue()

            # Update UI in main thread
            def update_ui():
                output_text.insert(tk.END, result)
                output_text.see(tk.END)
                # Refresh status - note: we don't have direct access to the labels here
                # The check will happen when user manually clicks Check button
                messagebox.showinfo("Download Complete",
                                  "Data download process completed.\nCheck the output for details.\n\nClick 'Check Existing Data' to refresh status.")

            output_text.after(0, update_ui)

        except Exception as e:
            def show_error():
                output_text.insert(tk.END, f"\nERROR: {str(e)}\n")
                messagebox.showerror("Error", f"Download failed:\n{str(e)}")

            output_text.after(0, show_error)

    # Run in background thread
    thread = threading.Thread(target=run_download, daemon=True)
    thread.start()


def refresh():
    """Refresh the panel - update data status when panel is shown"""
    global _trade_status_label, _price_status_label, _output_text

    if _trade_status_label is not None and _price_status_label is not None and _output_text is not None:
        check_all_data(_output_text, _trade_status_label, _price_status_label, silent=False)

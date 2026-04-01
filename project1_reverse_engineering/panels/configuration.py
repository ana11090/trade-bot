"""
Project 1 - Configuration Panel
All Project 1 settings in one place. Saved to p1_config.json and read by all step scripts.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os
import sys
import json
import threading
import subprocess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

import state
from helpers import make_copyable

_trade_status_label = None
_price_status_label = None
_output_text        = None

_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'p1_config.json')

DEFAULTS = {
    'symbol':                   'XAUUSD',
    'broker_timezone':          'EET',
    'pip_value_usd':            '0.01',
    'alignment_tolerance_pips': '150',
    'min_lookback_candles':     '200',
    'train_test_split':         '0.80',
    'rf_trees':                 '500',
    'max_tree_depth':           '6',
    'min_samples_leaf':         '10',
    'rule_min_confidence':      '0.65',
    'rule_min_coverage':        '5',
    'match_rate_threshold':     '0.70',
}

# (key, label, description)
FIELDS = {
    'instrument': [
        ('symbol',
         'Symbol',
         'Trading instrument name. Must match the price data file prefix.\n'
         'Example: XAUUSD → looks for xauusd_M5.csv, xauusd_H1.csv, etc.\n'
         'Change this if you want to analyse EURUSD, GBPUSD, BTCUSD, etc.'),

        ('broker_timezone',
         'Broker Timezone',
         'Timezone your broker uses for trade timestamps (the "Open Date" column).\n'
         'Common values: EET, GMT, Europe/London, Europe/Bucharest, US/Eastern\n'
         'Wrong timezone = trades matched to wrong candles → bad results.'),

        ('pip_value_usd',
         'Pip Value (USD)',
         'Value of 1 pip in USD, used for spread/commission cost calculations.\n'
         'XAUUSD = 0.01  |  EURUSD = 0.0001  |  GBPJPY ≈ 0.00007\n'
         'Check your broker\'s contract spec if unsure.'),

        ('alignment_tolerance_pips',
         'Alignment Tolerance (pips)',
         'How far (in pips) a trade\'s open price can be outside the matched candle\'s\n'
         'high/low range before being flagged as misaligned.\n'
         'XAUUSD needs ~150 (bid/ask vs mid-price diff). EURUSD needs ~5.'),
    ],
    'pipeline': [
        ('min_lookback_candles',
         'Min Lookback Candles',
         'Number of candles before each trade needed to compute all indicators.\n'
         'Trades without enough history are skipped.\n'
         'Minimum 50. Recommended 200. Higher = fewer trades used but safer indicators.'),
    ],
    'ml': [
        ('train_test_split',
         'Train / Test Split',
         'Fraction of trades used for training the model (the rest go to testing).\n'
         '0.80 = 80% train, 20% test. Range: 0.50 – 0.90.\n'
         'Lower = more test data but model sees less history.'),

        ('rf_trees',
         'Random Forest Trees',
         'Number of decision trees in the Random Forest ensemble.\n'
         'More trees = more stable and accurate, but slower to train.\n'
         'Recommended: 200–1000. Start with 500.'),

        ('max_tree_depth',
         'Max Tree Depth',
         'Maximum depth of each decision tree.\n'
         'Deeper = more complex rules, higher risk of overfitting to training data.\n'
         'Recommended: 4–8. If rules are too specific, reduce this.'),

        ('min_samples_leaf',
         'Min Samples per Leaf',
         'Minimum number of trades required at each rule leaf node.\n'
         'Higher = simpler, more robust rules that cover more trades.\n'
         'Recommended: 5–20. Lower = more specific rules, may not generalise.'),
    ],
    'rules': [
        ('rule_min_confidence',
         'Rule Min Confidence',
         'Minimum win rate for a rule to be kept (as a decimal).\n'
         '0.65 = rule must win at least 65% of the time.\n'
         'Raise to get fewer but higher-quality rules. Range: 0.50 – 0.90.'),

        ('rule_min_coverage',
         'Rule Min Coverage (trades)',
         'Minimum number of trades a rule must cover to be kept.\n'
         'Too low = rules based on just 1–2 trades (not reliable).\n'
         'Recommended: 5–20 trades minimum.'),

        ('match_rate_threshold',
         'Match Rate Threshold',
         'Minimum fraction of your historical trades that extracted rules must explain.\n'
         '0.70 = rules must cover at least 70% of trades for the project to pass.\n'
         'Lower if you get 0 rules; raise if you want more coverage.'),
    ],
}

SECTION_TITLES = {
    'instrument': '🌐 Instrument & Data',
    'pipeline':   '⚙️ Pipeline Settings',
    'ml':         '🤖 Machine Learning (Random Forest)',
    'rules':      '📋 Rule Extraction',
}


def load_config():
    cfg = dict(DEFAULTS)
    path = os.path.normpath(_CONFIG_FILE)
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                saved = json.load(f)
            cfg.update({k: str(v) for k, v in saved.items() if k in DEFAULTS})
        except Exception:
            pass
    return cfg


def save_config(entries, output_text):
    values = {k: var.get().strip() for k, var in entries.items()}

    # Validate numerics
    float_keys = ['pip_value_usd', 'alignment_tolerance_pips', 'train_test_split',
                  'rule_min_confidence', 'match_rate_threshold']
    int_keys   = ['min_lookback_candles', 'rf_trees', 'max_tree_depth',
                  'min_samples_leaf', 'rule_min_coverage']

    for k in float_keys:
        try:
            float(values[k])
        except ValueError:
            messagebox.showerror("Invalid Value", f"'{k}' must be a number.\nGot: {values[k]}")
            return
    for k in int_keys:
        try:
            int(values[k])
        except ValueError:
            messagebox.showerror("Invalid Value", f"'{k}' must be a whole number.\nGot: {values[k]}")
            return

    path = os.path.normpath(_CONFIG_FILE)
    try:
        with open(path, 'w') as f:
            json.dump(values, f, indent=2)
    except Exception as e:
        messagebox.showerror("Save Error", f"Could not save config:\n{e}")
        return

    if output_text:
        output_text.delete('1.0', tk.END)
        output_text.insert(tk.END, f"Configuration saved to:\n{path}\n\n")
        for k, v in values.items():
            output_text.insert(tk.END, f"  {k}: {v}\n")
        output_text.insert(tk.END, "\nAll scenarios will use these settings on next run.\n")


def _field_row(parent, key, label_text, description, var):
    """Build one label + entry + description tooltip row."""
    row = tk.Frame(parent, bg="white")
    row.pack(fill="x", pady=(4, 0))

    tk.Label(row, text=label_text + ":", font=("Segoe UI", 9, "bold"),
             bg="white", fg="#333", width=26, anchor="w").pack(side=tk.LEFT)

    entry = tk.Entry(row, textvariable=var, font=("Segoe UI", 9), width=14,
                     relief=tk.SOLID, bd=1)
    entry.pack(side=tk.LEFT, padx=(6, 0))

    # Description below the row
    desc_lbl = tk.Label(parent, text=description, font=("Segoe UI", 8),
                        bg="white", fg="#888", justify=tk.LEFT, anchor="w",
                        wraplength=480)
    desc_lbl.pack(fill="x", padx=(4, 0), pady=(1, 6))


def build_panel(parent):
    global _trade_status_label, _price_status_label, _output_text

    panel = tk.Frame(parent, bg="#f0f2f5")

    # Title
    title_frame = tk.Frame(panel, bg="white", pady=16)
    title_frame.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(title_frame, text="⚙️ Configuration & Data Download",
             bg="white", fg="#16213e", font=("Segoe UI", 18, "bold")).pack()
    tk.Label(title_frame, text="Set up all parameters for the reverse-engineering pipeline. "
             "Settings are saved and applied to all 7 steps automatically.",
             bg="white", fg="#666", font=("Segoe UI", 10)).pack(pady=(4, 0))

    content_frame = tk.Frame(panel, bg="#f0f2f5")
    content_frame.pack(fill="both", expand=True, padx=20, pady=10)

    # ── Left column — Config ──────────────────────────────────────────────────
    left_frame = tk.Frame(content_frame, bg="white", padx=20, pady=16)
    left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

    tk.Label(left_frame, text="📋 Parameters",
             bg="white", fg="#16213e", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 10))

    cfg     = load_config()
    entries = {}

    def _make_var(key):
        v = tk.StringVar(value=cfg[key])
        entries[key] = v
        return v

    # Build each section
    for section_key, fields in FIELDS.items():
        sec = tk.LabelFrame(left_frame, text=SECTION_TITLES[section_key],
                            font=("Segoe UI", 9, "bold"), bg="white", fg="#16213e",
                            padx=12, pady=8)
        sec.pack(fill="x", pady=(0, 10))
        for key, label, desc in fields:
            _field_row(sec, key, label, desc, _make_var(key))

    # Buttons
    btn_row = tk.Frame(left_frame, bg="white")
    btn_row.pack(fill="x", pady=(6, 0))

    tk.Button(btn_row, text="💾 Save Configuration",
              command=lambda: save_config(entries, _output_text),
              bg="#27ae60", fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, padx=16, pady=8, cursor="hand2").pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(btn_row, text="↺ Reset to Defaults",
              command=lambda: [var.set(DEFAULTS[k]) for k, var in entries.items()],
              bg="#95a5a6", fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, padx=16, pady=8, cursor="hand2").pack(side=tk.LEFT)

    # Data status
    status_frame = tk.Frame(left_frame, bg="#e8f4f8", padx=12, pady=10)
    status_frame.pack(fill="x", pady=(16, 0))
    tk.Label(status_frame, text="Data Status:", bg="#e8f4f8", fg="#16213e",
             font=("Segoe UI", 9, "bold")).pack(anchor="w")

    _trade_status_label = tk.Label(status_frame, text="", bg="#e8f4f8", font=("Segoe UI", 9))
    _trade_status_label.pack(anchor="w", pady=(4, 2))
    make_copyable(_trade_status_label)

    _price_status_label = tk.Label(status_frame, text="", bg="#e8f4f8", font=("Segoe UI", 9))
    _price_status_label.pack(anchor="w")
    make_copyable(_price_status_label)

    # ── Right column — Download ───────────────────────────────────────────────
    right_frame = tk.Frame(content_frame, bg="white", padx=20, pady=16)
    right_frame.pack(side="left", fill="both", expand=True, padx=(10, 0))

    tk.Label(right_frame, text="📊 Download Price Data",
             bg="white", fg="#16213e", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 6))

    tk.Label(right_frame,
             text="Price data files must be in the /data/ folder with names like:\n"
                  "  xauusd_M5.csv   xauusd_M15.csv   xauusd_H1.csv   xauusd_H4.csv\n\n"
                  "If you change the Symbol above, the download scripts will look for\n"
                  "the new symbol's files automatically.",
             bg="white", fg="#555", font=("Segoe UI", 9), justify=tk.LEFT).pack(anchor="w", pady=(0, 10))

    info = tk.Frame(right_frame, bg="#fff3cd", padx=10, pady=10)
    info.pack(fill="x", pady=(0, 12))
    tk.Label(info, text="⚠️  Best source: download_autonomous.py",
             bg="#fff3cd", fg="#856404", font=("Segoe UI", 9, "bold")).pack(anchor="w")
    tk.Label(info,
             text="The autonomous Dukascopy downloader (project1/script_download_historicaldata_xauusd/)\n"
                  "covers 2005–present with auto-retry. Run it once to populate /data/.",
             bg="#fff3cd", fg="#856404", font=("Segoe UI", 9), justify=tk.LEFT).pack(anchor="w", pady=(4, 0))

    tk.Button(right_frame, text="🔽  Download from MT5 (Recommended)",
              bg="#27ae60", fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, pady=10, cursor="hand2",
              command=lambda: download_data_mt5(_output_text)).pack(fill="x", pady=(0, 5))

    tk.Button(right_frame, text="🔽  Download from yfinance (Limited)",
              bg="#95a5a6", fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, pady=10, cursor="hand2",
              command=lambda: download_data(_output_text, None)).pack(fill="x", pady=(0, 8))

    tk.Button(right_frame, text="🔍  Check Data Status",
              bg="#3498db", fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, pady=10, cursor="hand2",
              command=refresh).pack(fill="x", pady=(0, 10))

    tk.Label(right_frame, text="Output:", bg="white", fg="#333",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 4))

    _output_text = scrolledtext.ScrolledText(right_frame, height=12,
                                             font=("Consolas", 9),
                                             bg="#2c3e50", fg="#ecf0f1",
                                             insertbackground="white")
    _output_text.pack(fill="both", expand=True)

    check_all_data(_output_text, _trade_status_label, _price_status_label, silent=True)
    return panel


def check_all_data(output_text, trade_status_label, price_status_label, silent=False):
    if not silent:
        output_text.delete('1.0', tk.END)

    if state.loaded_data is not None:
        n = len(state.loaded_data)
        trade_status_label.config(text=f"✓ Trade Data: {n} trades loaded (Project 0)", fg="#27ae60")
        if not silent:
            output_text.insert(tk.END, f"✓ Trade Data: {n} trades loaded from Project 0\n\n")
    else:
        trade_status_label.config(text="⚠️  No trade data — load it in Project 0 first", fg="#e74c3c")
        if not silent:
            output_text.insert(tk.END, "⚠️  No trade data loaded.\n")
            output_text.insert(tk.END, "    Go to Project 0 → Data Pipeline → load your file.\n\n")

    cfg         = load_config()
    symbol      = cfg.get('symbol', 'XAUUSD').lower()
    data_folder = os.path.normpath(os.path.join(os.path.dirname(__file__), '../../data'))
    timeframes  = ['M5', 'M15', 'H1', 'H4']

    if not silent:
        output_text.insert(tk.END, f"Checking price data files (symbol: {symbol.upper()})...\n")

    found = 0
    for tf in timeframes:
        fp = os.path.join(data_folder, f'{symbol}_{tf}.csv')
        if os.path.exists(fp):
            found += 1
            if not silent:
                output_text.insert(tk.END, f"  ✓ {tf}: {os.path.getsize(fp):,} bytes\n")
        else:
            if not silent:
                output_text.insert(tk.END, f"  ✗ {tf}: not found ({fp})\n")

    if not silent:
        output_text.insert(tk.END, f"\n{found}/4 timeframe files found.\n")

    if found == 4:
        price_status_label.config(text=f"✓ Price Data: all 4 timeframes found", fg="#27ae60")
    elif found > 0:
        price_status_label.config(text=f"⚠️  Price Data: {found}/4 timeframes found", fg="#f39c12")
    else:
        price_status_label.config(text="⚠️  Price Data: no files found", fg="#e74c3c")


def download_data_mt5(output_text):
    output_text.delete('1.0', tk.END)
    output_text.insert(tk.END, "Starting MT5 download...\nMake sure MetaTrader 5 is running!\n\n")

    def run():
        try:
            script = os.path.join(os.path.dirname(__file__), '..', 'download_data_mt5.py')
            proc = subprocess.Popen([sys.executable, script],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            for line in iter(proc.stdout.readline, ''):
                if line:
                    output_text.after(0, lambda l=line: (output_text.insert(tk.END, l), output_text.see(tk.END)))
            proc.wait()
            output_text.after(0, lambda: messagebox.showinfo("Done", "MT5 download finished.\nClick 'Check Data Status' to verify."))
            output_text.after(0, refresh)
        except Exception as e:
            output_text.after(0, lambda: output_text.insert(tk.END, f"\nERROR: {e}\n"))

    threading.Thread(target=run, daemon=True).start()


def download_data(output_text, _):
    output_text.delete('1.0', tk.END)
    output_text.insert(tk.END, "Starting yfinance download...\nThis may take several minutes.\n\n")

    def run():
        try:
            import io
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
            old = sys.stdout
            sys.stdout = buf = io.StringIO()
            try:
                import download_price_data
                download_price_data.download_data_yfinance()
                download_price_data.verify_data_coverage()
            except Exception as e:
                buf.write(f"\nERROR: {e}\n")
            finally:
                sys.stdout = old
                result = buf.getvalue()
            output_text.after(0, lambda: (output_text.insert(tk.END, result), output_text.see(tk.END)))
            output_text.after(0, lambda: messagebox.showinfo("Done", "Download finished.\nClick 'Check Data Status' to verify."))
        except Exception as e:
            output_text.after(0, lambda: output_text.insert(tk.END, f"\nERROR: {e}\n"))

    threading.Thread(target=run, daemon=True).start()


def refresh():
    global _trade_status_label, _price_status_label, _output_text
    if _output_text is not None:
        check_all_data(_output_text, _trade_status_label, _price_status_label, silent=False)

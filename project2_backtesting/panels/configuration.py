"""
Project 2 - Configuration Panel
Setup and configuration for backtesting
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import os
import sys
import json

# Module-level variables for refresh
_rules_status_label = None
_price_status_label = None
_output_text = None

# Config file location
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'backtest_config.json')

DEFAULTS = {
    # Instrument
    'symbol':              'XAUUSD',
    'winning_scenario':    'H1',
    'pip_value_per_lot':   '10.0',
    # Date periods
    'insample_start':      '2022-01-01',
    'insample_end':        '2023-12-31',
    'outsample_start':     '2024-01-01',
    'outsample_end':       '2024-12-31',
    # Capital & risk
    'starting_capital':    '10000',
    'risk_pct':            '1.0',
    'lot_size_calc':       'DYNAMIC',
    'fixed_lot_size':      '0.01',
    # SL / TP
    'sl_atr':              '1.5',
    'tp1_atr':             '1.5',
    'tp2_atr':             '3.0',
    # Costs
    'commission':          '4.0',
    'spread':              '0.3',
    # Engine
    'hard_close_hour':     '21',
    'warmup_candles':      '200',
    'max_one_trade':       'True',
    'same_candle_sl_rule': 'LOSS',
}


def load_config():
    """Load saved config from JSON, falling back to defaults for missing keys."""
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
    """Validate entries and write backtest_config.json."""
    values = {k: var.get().strip() for k, var in entries.items()}

    # Basic validation
    date_keys = ['insample_start', 'insample_end', 'outsample_start', 'outsample_end']
    for k in date_keys:
        v = values[k]
        if len(v) != 10 or v[4] != '-' or v[7] != '-':
            messagebox.showerror("Invalid Date", f"'{k}' must be YYYY-MM-DD format.\nGot: {v}")
            return

    float_keys = ['pip_value_per_lot', 'starting_capital', 'risk_pct', 'fixed_lot_size',
                  'sl_atr', 'tp1_atr', 'tp2_atr', 'commission', 'spread']
    for k in float_keys:
        try:
            float(values[k])
        except ValueError:
            messagebox.showerror("Invalid Value", f"'{k}' must be a number.\nGot: {values[k]}")
            return

    int_keys = ['hard_close_hour', 'warmup_candles']
    for k in int_keys:
        try:
            int(values[k])
        except ValueError:
            messagebox.showerror("Invalid Value", f"'{k}' must be a whole number.\nGot: {values[k]}")
            return

    if values['max_one_trade'].strip().lower() not in ('true', 'false'):
        messagebox.showerror("Invalid Value", f"'max_one_trade' must be True or False.\nGot: {values['max_one_trade']}")
        return

    if values['same_candle_sl_rule'].strip().upper() not in ('LOSS', 'WIN'):
        messagebox.showerror("Invalid Value", f"'same_candle_sl_rule' must be LOSS or WIN.\nGot: {values['same_candle_sl_rule']}")
        return

    if values['lot_size_calc'].strip().upper() not in ('DYNAMIC', 'FIXED'):
        messagebox.showerror("Invalid Value", f"'lot_size_calc' must be DYNAMIC or FIXED.\nGot: {values['lot_size_calc']}")
        return

    path = os.path.normpath(_CONFIG_FILE)
    try:
        with open(path, 'w') as f:
            json.dump(values, f, indent=2)
    except Exception as e:
        messagebox.showerror("Save Error", f"Could not save config:\n{e}")
        return

    if output_text:
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "Configuration saved successfully.\n\n")
        for k, v in values.items():
            output_text.insert(tk.END, f"  {k}: {v}\n")
        output_text.insert(tk.END, f"\nSaved to: {path}\n")


def check_prerequisites(output_text, rules_label, price_label, silent=True):
    """Check if all required files are available"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

    cfg = load_config()
    symbol = cfg.get('symbol', 'XAUUSD').upper()
    scenario = cfg.get('winning_scenario', 'H1')

    # New backtester uses analysis_report.json (not the old rules_report txt files)
    rules_file = os.path.join(project_root, 'project1_reverse_engineering/outputs/analysis_report.json')
    rules_exists = os.path.exists(rules_file)

    price_file = os.path.join(project_root, f'data/{symbol.lower()}_{scenario}.csv')
    price_exists = os.path.exists(price_file)

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

    if not silent and output_text:
        output_text.delete(1.0, tk.END)
        output_text.insert(tk.END, "=== PREREQUISITES CHECK ===\n\n")

        if rules_exists:
            output_text.insert(tk.END, "Rules File: FOUND\n", "success")
            output_text.insert(tk.END, f"  {rules_file}\n\n")
        else:
            output_text.insert(tk.END, "Rules File: NOT FOUND\n", "error")
            output_text.insert(tk.END, f"  Expected: {rules_file}\n")
            output_text.insert(tk.END, "  Run Project 1 first to generate rules!\n\n")

        if price_exists:
            output_text.insert(tk.END, "Price Data: FOUND\n", "success")
            output_text.insert(tk.END, f"  {price_file}\n\n")
        else:
            output_text.insert(tk.END, "Price Data: NOT FOUND\n", "error")
            output_text.insert(tk.END, f"  Expected: {price_file}\n")
            output_text.insert(tk.END, "  Download price data using Project 1 tools!\n\n")

        if rules_exists and price_exists:
            output_text.insert(tk.END, "All prerequisites met! Ready to run backtest.\n", "success")
        else:
            output_text.insert(tk.END, "Missing prerequisites. Please complete setup before running backtest.\n", "error")

        output_text.tag_config("success", foreground="#28a745")
        output_text.tag_config("error", foreground="#dc3545")

    return rules_exists and price_exists


def _field_row(parent, label_text, var, description="", bg="#ffffff"):
    """Create a label + entry + description row."""
    row = tk.Frame(parent, bg=bg)
    row.pack(fill="x", pady=(4, 0))
    tk.Label(row, text=label_text, font=("Arial", 9, "bold"), bg=bg,
             fg="#333333", width=30, anchor="w").pack(side=tk.LEFT)
    entry = tk.Entry(row, textvariable=var, font=("Arial", 9), width=16,
                     relief=tk.SOLID, bd=1)
    entry.pack(side=tk.LEFT, padx=(5, 0))
    if description:
        tk.Label(parent, text=description, font=("Arial", 8), bg=bg,
                 fg="#888888", justify=tk.LEFT, anchor="w",
                 wraplength=520).pack(fill="x", padx=(4, 0), pady=(1, 4))
    return entry


def _auto_detect_dates(entries):
    """Read H1 CSV and auto-fill date period fields."""
    import pandas as pd

    cfg = {k: v.get() for k, v in entries.items()}
    symbol = cfg.get('symbol', 'XAUUSD').lower()
    scenario = cfg.get('winning_scenario', 'H1')

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    csv_path = os.path.join(project_root, f'data/{symbol}_{scenario}.csv')

    if not os.path.exists(csv_path):
        messagebox.showwarning("No Data", f"CSV not found:\n{csv_path}")
        return

    try:
        df = pd.read_csv(csv_path, usecols=[0], encoding='utf-8-sig')
        ts_col = df.columns[0]
        dates = pd.to_datetime(df[ts_col], errors='coerce').dropna()

        if len(dates) == 0:
            messagebox.showwarning("No Data", "No valid dates found in CSV.")
            return

        min_date = dates.min()
        max_date = dates.max()
        total_days = (max_date - min_date).days

        # Split: 70% in-sample, 30% out-of-sample
        split_date = min_date + pd.Timedelta(days=int(total_days * 0.7))

        entries['insample_start'].set(min_date.strftime('%Y-%m-%d'))
        entries['insample_end'].set(split_date.strftime('%Y-%m-%d'))
        entries['outsample_start'].set((split_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        entries['outsample_end'].set(max_date.strftime('%Y-%m-%d'))

        messagebox.showinfo("Dates Auto-Filled",
            f"Data range: {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}\n"
            f"Total: {total_days} days ({len(dates):,} candles)\n\n"
            f"In-sample (70%): {min_date.strftime('%Y-%m-%d')} to {split_date.strftime('%Y-%m-%d')}\n"
            f"Out-of-sample (30%): {(split_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
    except Exception as e:
        messagebox.showerror("Error", f"Could not read dates:\n{e}")


def build_panel(parent):
    """Build the configuration panel"""
    global _rules_status_label, _price_status_label, _output_text

    panel = tk.Frame(parent, bg="#ffffff")

    # Title
    tk.Label(panel, text="Project 2 - Backtesting Configuration",
             font=("Arial", 16, "bold"), bg="#ffffff", fg="#333333").pack(pady=(20, 5))
    tk.Label(panel, text="Validate discovered rules on in-sample and out-of-sample data",
             font=("Arial", 10), bg="#ffffff", fg="#666666").pack(pady=(0, 15))

    # ── Prerequisites ────────────────────────────────────────────────────────
    status_frame = tk.LabelFrame(panel, text="Prerequisites Status",
                                 font=("Arial", 11, "bold"), bg="#ffffff", fg="#333333",
                                 padx=20, pady=15)
    status_frame.pack(fill="x", padx=20, pady=8)

    _rules_status_label = tk.Label(status_frame, text="Rules File: Checking...",
                                   font=("Arial", 10), bg="#ffffff", fg="#666666")
    _rules_status_label.pack(anchor="w", pady=5)

    _price_status_label = tk.Label(status_frame, text="Price Data: Checking...",
                                   font=("Arial", 10), bg="#ffffff", fg="#666666")
    _price_status_label.pack(anchor="w", pady=5)

    tk.Button(status_frame, text="Check Prerequisites",
              command=lambda: check_prerequisites(_output_text, _rules_status_label, _price_status_label, silent=False),
              bg="#667eea", fg="white", font=("Arial", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=20, pady=8).pack(pady=(10, 0))

    # ── Editable Config ──────────────────────────────────────────────────────
    cfg = load_config()
    entries = {}

    def _make_var(key):
        v = tk.StringVar(value=cfg[key])
        entries[key] = v
        return v

    config_frame = tk.LabelFrame(panel, text="Backtest Configuration",
                                 font=("Arial", 11, "bold"), bg="#ffffff", fg="#333333",
                                 padx=20, pady=15)
    config_frame.pack(fill="x", padx=20, pady=8)

    # Instrument
    instr_frame = tk.LabelFrame(config_frame, text="🌐 Instrument",
                                font=("Arial", 9, "bold"), bg="#ffffff", fg="#555555",
                                padx=10, pady=8)
    instr_frame.pack(fill="x", pady=(0, 8))
    _field_row(instr_frame, "Symbol", _make_var('symbol'),
               "Trading symbol to backtest. Must match the price data filename prefix.\n"
               "Example: XAUUSD → looks for data/xauusd_H1.csv")
    _field_row(instr_frame, "Winning Scenario", _make_var('winning_scenario'),
               "Which Project 1 scenario's rules to use. Must match a folder in project1/outputs/.\n"
               "Options: M5, M15, H1, H4, H1_M15")
    _field_row(instr_frame, "Pip Value per Lot ($)", _make_var('pip_value_per_lot'),
               "USD profit/loss per pip per standard lot. Used to calculate trade P&L.\n"
               "XAUUSD = 10.0  |  EURUSD = 10.0  |  GBPJPY ≈ 7.0  (check broker contract spec)")

    # Date periods
    period_frame = tk.LabelFrame(config_frame, text="📅 Date Periods",
                                 font=("Arial", 9, "bold"), bg="#ffffff", fg="#555555",
                                 padx=10, pady=8)
    period_frame.pack(fill="x", pady=(0, 8))
    _field_row(period_frame, "In-Sample Start  (YYYY-MM-DD)", _make_var('insample_start'),
               "First date of the training period — should match your trade history start.")
    _field_row(period_frame, "In-Sample End    (YYYY-MM-DD)", _make_var('insample_end'),
               "Last date of the training period.")
    _field_row(period_frame, "Out-of-Sample Start (YYYY-MM-DD)", _make_var('outsample_start'),
               "First date of the validation period — data the model has never seen.")
    _field_row(period_frame, "Out-of-Sample End   (YYYY-MM-DD)", _make_var('outsample_end'),
               "Last date of the validation period.")

    # Auto-detect dates button
    auto_btn_frame = tk.Frame(period_frame, bg="#ffffff")
    auto_btn_frame.pack(fill="x", pady=(8, 0))

    tk.Button(auto_btn_frame, text="📅 Auto-Detect from Data (70/30 split)",
              command=lambda: _auto_detect_dates(entries),
              bg="#667eea", fg="white", font=("Arial", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=15, pady=6).pack(side=tk.LEFT)

    tk.Label(auto_btn_frame, text="Reads your CSV and fills dates automatically",
             font=("Arial", 8), bg="#ffffff", fg="#888888").pack(side=tk.LEFT, padx=(10, 0))

    # Capital & Risk
    risk_frame = tk.LabelFrame(config_frame, text="💰 Capital & Risk",
                               font=("Arial", 9, "bold"), bg="#ffffff", fg="#555555",
                               padx=10, pady=8)
    risk_frame.pack(fill="x", pady=(0, 8))
    _field_row(risk_frame, "Starting Capital ($)", _make_var('starting_capital'),
               "Account balance at the start of the backtest in USD.")
    _field_row(risk_frame, "Risk Per Trade (%)", _make_var('risk_pct'),
               "Percentage of current balance risked on each trade.\n"
               "1.0 = 1% risk per trade. Only used when Lot Size = DYNAMIC.")
    _field_row(risk_frame, "Lot Size Calculation", _make_var('lot_size_calc'),
               "DYNAMIC = lot size calculated from risk % each trade (recommended).\n"
               "FIXED = always use the Fixed Lot Size below regardless of balance.")
    _field_row(risk_frame, "Fixed Lot Size", _make_var('fixed_lot_size'),
               "Lot size to use when Lot Size Calculation = FIXED. Example: 0.01 = micro lot.")

    # SL / TP
    sltp_frame = tk.LabelFrame(config_frame, text="🎯 Stop Loss & Take Profit  (ATR multipliers)",
                               font=("Arial", 9, "bold"), bg="#ffffff", fg="#555555",
                               padx=10, pady=8)
    sltp_frame.pack(fill="x", pady=(0, 8))
    _field_row(sltp_frame, "Stop Loss ATR multiplier", _make_var('sl_atr'),
               "Stop loss distance = ATR × this value. 1.5 = 1.5× the average true range.")
    _field_row(sltp_frame, "Take Profit 1 ATR multiplier", _make_var('tp1_atr'),
               "TP1 distance = ATR × this value. 50% of the position closes at TP1.")
    _field_row(sltp_frame, "Take Profit 2 ATR multiplier", _make_var('tp2_atr'),
               "TP2 distance = ATR × this value. Remaining 50% closes at TP2.")

    # Costs
    cost_frame = tk.LabelFrame(config_frame, text="💸 Costs",
                               font=("Arial", 9, "bold"), bg="#ffffff", fg="#555555",
                               padx=10, pady=8)
    cost_frame.pack(fill="x", pady=(0, 8))
    _field_row(cost_frame, "Commission per lot ($ round trip)", _make_var('commission'),
               "Total commission charged for opening and closing one standard lot.\n"
               "Typical prop firm: $4–$8 per lot round trip.")
    _field_row(cost_frame, "Spread (pips)", _make_var('spread'),
               "Estimated bid/ask spread in pips. XAUUSD typical: 0.2–0.5 pips.")

    # Engine
    engine_frame = tk.LabelFrame(config_frame, text="⚙️ Engine Settings",
                                 font=("Arial", 9, "bold"), bg="#ffffff", fg="#555555",
                                 padx=10, pady=8)
    engine_frame.pack(fill="x", pady=(0, 8))
    _field_row(engine_frame, "Hard Close Hour (UTC)", _make_var('hard_close_hour'),
               "Force-close all open trades at this UTC hour daily.\n"
               "21 = 9pm UTC (end of New York session). Set to 0 to disable.")
    _field_row(engine_frame, "Warmup Candles", _make_var('warmup_candles'),
               "Skip the first N candles before the engine starts trading.\n"
               "Needed to allow indicators (ATR, EMA, etc.) to stabilise. Minimum 50.")
    _field_row(engine_frame, "Max One Trade Open (True/False)", _make_var('max_one_trade'),
               "True = only one trade can be open at a time (recommended for trend systems).\n"
               "False = multiple simultaneous trades allowed.")
    _field_row(engine_frame, "Same-Candle SL Rule (LOSS/WIN)", _make_var('same_candle_sl_rule'),
               "When SL and TP both trigger on the same candle, count it as LOSS or WIN.\n"
               "LOSS is the conservative/realistic default.")

    # Buttons
    btn_row = tk.Frame(config_frame, bg="#ffffff")
    btn_row.pack(fill="x", pady=(8, 0))

    tk.Button(btn_row, text="Save Configuration",
              command=lambda: save_config(entries, _output_text),
              bg="#28a745", fg="white", font=("Arial", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=20, pady=8).pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(btn_row, text="Reset to Defaults",
              command=lambda: [var.set(DEFAULTS[k]) for k, var in entries.items()],
              bg="#6c757d", fg="white", font=("Arial", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=20, pady=8).pack(side=tk.LEFT)

    # ── Output ───────────────────────────────────────────────────────────────
    output_frame = tk.LabelFrame(panel, text="Status Output",
                                 font=("Arial", 11, "bold"), bg="#ffffff", fg="#333333",
                                 padx=10, pady=10)
    output_frame.pack(fill="both", expand=True, padx=20, pady=8)

    _output_text = scrolledtext.ScrolledText(output_frame, height=10,
                                             font=("Courier", 9), bg="#f8f9fa",
                                             fg="#333333", wrap=tk.WORD)
    _output_text.pack(fill="both", expand=True)

    check_prerequisites(_output_text, _rules_status_label, _price_status_label, silent=False)

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)"""
    global _rules_status_label, _price_status_label, _output_text
    if _output_text is not None:
        check_prerequisites(_output_text, _rules_status_label, _price_status_label, silent=False)

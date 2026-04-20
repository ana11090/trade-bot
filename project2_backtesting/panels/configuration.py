"""
Project 2 - Configuration Panel
Setup and configuration for backtesting
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import os
import sys
import json
import glob

# Module-level variables for refresh
_rules_status_label = None
_price_status_label = None
_output_text = None

# WHY: save_config needs firm/stage vars created in build_panel.
#      Declaring them here lets save_config access them as module globals.
# CHANGED: April 2026 — persist firm selection
_config_firm_var  = None
_config_stage_var = None
_config_acct_var  = None

# Config file location
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'backtest_config.json')

# WHY: Many places need to convert candles_held to minutes, or get the MQL5 period
#      constant. One mapping used everywhere — no hardcoded "× 60" assumptions.
TF_MINUTES = {
    'M1': 1, 'M5': 5, 'M15': 15, 'H1': 60, 'H4': 240, 'D1': 1440, 'W1': 10080,
}
TF_MQL5_PERIOD = {
    'M1': 'PERIOD_M1', 'M5': 'PERIOD_M5', 'M15': 'PERIOD_M15',
    'H1': 'PERIOD_H1', 'H4': 'PERIOD_H4', 'D1': 'PERIOD_D1',
}

# Known instrument specs — pip value per standard lot, pip size, typical spread
# WHY (Phase 32 Fix 5): XAGUSD pip_value was 50.0 — 10x too high.
#      Silver standard lot is 5000 oz × 0.001 pip_size = $5 per pip per
#      1 lot, not $50. Users running XAGUSD got lot sizes ~10x smaller
#      than intended and dollar PnL was ~10x too small.
# WHY (Phase 32 Fix 5 cont.): USDJPY / GBPJPY pip_value is rate-dependent:
#      $10 at rate 100 JPY/USD, $6.67 at rate 150. The hardcoded 6.7 is
#      approximate (assumes rate ~149). Users with a specific JPY rate
#      should override pip_value_per_lot in backtest_config.json. The
#      comment below documents the assumption.
# CHANGED: April 2026 — Phase 32 Fix 5 — XAGUSD pip fix + JPY note
#          (audit Part C HIGH #93)
INSTRUMENT_SPECS = {
    'XAUUSD': {'pip_value': 10.0, 'pip_size': 0.01, 'typical_spread': 2.5, 'name': 'Gold'},
    'EURUSD': {'pip_value': 10.0, 'pip_size': 0.0001, 'typical_spread': 0.8, 'name': 'EUR/USD'},
    'GBPUSD': {'pip_value': 10.0, 'pip_size': 0.0001, 'typical_spread': 1.0, 'name': 'GBP/USD'},
    # USDJPY / GBPJPY pip_value drifts with the USD/JPY rate:
    #   pip_value (USD) = 100000 × pip_size / USDJPY_rate
    #   = 1000 / rate    (for pip_size=0.01)
    # Values below assume rate ≈ 149 (→ $6.7). Override in config for
    # other rates.
    'USDJPY': {'pip_value': 6.7,  'pip_size': 0.01, 'typical_spread': 0.9, 'name': 'USD/JPY'},
    'GBPJPY': {'pip_value': 6.7,  'pip_size': 0.01, 'typical_spread': 1.5, 'name': 'GBP/JPY'},
    'AUDUSD': {'pip_value': 10.0, 'pip_size': 0.0001, 'typical_spread': 0.8, 'name': 'AUD/USD'},
    'USDCAD': {'pip_value': 7.3,  'pip_size': 0.0001, 'typical_spread': 1.0, 'name': 'USD/CAD'},
    'USDCHF': {'pip_value': 11.0, 'pip_size': 0.0001, 'typical_spread': 1.0, 'name': 'USD/CHF'},
    'NZDUSD': {'pip_value': 10.0, 'pip_size': 0.0001, 'typical_spread': 1.2, 'name': 'NZD/USD'},
    # XAGUSD: 5000 oz × 0.001 pip_size = $5 per pip per 1 lot (not 50)
    'XAGUSD': {'pip_value': 5.0,  'pip_size': 0.001, 'typical_spread': 2.0, 'name': 'Silver'},
    'US30':   {'pip_value': 1.0,  'pip_size': 1.0, 'typical_spread': 2.0, 'name': 'Dow Jones'},
    'NAS100': {'pip_value': 1.0,  'pip_size': 1.0, 'typical_spread': 1.5, 'name': 'Nasdaq'},
    'BTCUSD': {'pip_value': 1.0,  'pip_size': 1.0, 'typical_spread': 30.0, 'name': 'Bitcoin'},
}

DEFAULTS = {
    # Instrument
    'symbol':              'XAUUSD',
    'winning_scenario':    'H1',  # Entry timeframe: M5, M15, H1, H4
    'pip_value_per_lot':   '10.0',
    # Date periods
    'insample_start':      '2022-01-01',
    'insample_end':        '2023-12-31',
    'outsample_start':     '2024-01-01',
    'outsample_end':       '2024-12-31',
    # Capital & risk
    'starting_capital':    '100000',
    'risk_pct':            '1.0',
    'lot_size_calc':       'DYNAMIC',
    'fixed_lot_size':      '0.66',
    # Costs
    'commission':          '4.0',
    'spread':              '2.5',
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
    global _config_firm_var, _config_stage_var
    values = {k: var.get().strip() for k, var in entries.items()}

    # Basic validation
    date_keys = ['insample_start', 'insample_end', 'outsample_start', 'outsample_end']
    for k in date_keys:
        v = values[k]
        if len(v) != 10 or v[4] != '-' or v[7] != '-':
            messagebox.showerror("Invalid Date", f"'{k}' must be YYYY-MM-DD format.\nGot: {v}")
            return

    float_keys = ['pip_value_per_lot', 'starting_capital', 'risk_pct', 'fixed_lot_size',
                  'commission', 'spread']
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

    # WHY: Resolve firm_name → firm_id from JSON files so run_backtest_panel
    #      can look up firm-specific leverage (e.g., Get Leveraged metals=10).
    #      firm_id is not in the entries dict (it's a dropdown, not a text
    #      field), so resolve and inject it here before writing.
    # CHANGED: April 2026 — persist firm selection (save firm_id)
    if _config_firm_var:
        _firm_name = _config_firm_var.get()
        if _firm_name and _firm_name != "None — manual settings":
            _pdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prop_firms')
            for _fp in glob.glob(os.path.join(_pdir, '*.json')):
                try:
                    with open(_fp, encoding='utf-8') as _ff:
                        _fd = json.load(_ff)
                    if _fd.get('firm_name') == _firm_name:
                        values['firm_id']   = _fd.get('firm_id', '')
                        values['firm_name'] = _firm_name
                        break
                except Exception:
                    pass
        else:
            values['firm_id']   = ''
            values['firm_name'] = ''
    if _config_stage_var:
        values['stage'] = _config_stage_var.get()

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


def _calculate_lot_size(entries):
    """Calculate lot size from capital, risk %, and SL ATR."""
    try:
        capital = float(entries['starting_capital'].get())
        risk_pct = float(entries['risk_pct'].get())
        sl_atr = float(entries['sl_atr'].get())
        pip_value = float(entries['pip_value_per_lot'].get())
    except ValueError:
        messagebox.showwarning("Missing Values",
            "Fill in Starting Capital, Risk %, SL ATR multiplier, and Pip Value first.")
        return

    # Estimate SL in pips using average ATR for XAUUSD H1 (~30 pips)
    # User can adjust — this is a reasonable estimate
    avg_atr_pips = 30  # typical H1 ATR for XAUUSD in pips
    sl_pips = avg_atr_pips * sl_atr

    risk_dollars = capital * (risk_pct / 100.0)
    sl_value_per_lot = sl_pips * pip_value  # how much $ you lose per lot at SL

    if sl_value_per_lot <= 0:
        messagebox.showwarning("Invalid", "SL value per lot is zero — check your settings.")
        return

    lots = risk_dollars / sl_value_per_lot
    lots = round(lots, 2)

    entries['fixed_lot_size'].set(str(lots))

    messagebox.showinfo("Lot Size Calculated",
        f"Capital: ${capital:,.0f}\n"
        f"Risk: {risk_pct}% = ${risk_dollars:,.0f}\n"
        f"Estimated SL: {sl_pips:.0f} pips (ATR ~{avg_atr_pips} × {sl_atr})\n"
        f"Pip value: ${pip_value}/pip/lot\n"
        f"SL cost per lot: ${sl_value_per_lot:,.0f}\n\n"
        f"Lot size: {lots}\n\n"
        f"Note: DYNAMIC mode recalculates this every trade\n"
        f"based on current equity. Recommended over FIXED.")


def _on_symbol_change(entries, info_label):
    """When symbol changes, auto-fill pip value, spread, and show instrument info."""
    symbol = entries['symbol'].get().strip().upper()

    if symbol in INSTRUMENT_SPECS:
        spec = INSTRUMENT_SPECS[symbol]
        entries['pip_value_per_lot'].set(str(spec['pip_value']))
        entries['spread'].set(str(spec['typical_spread']))
        info_label.config(
            text=f"✅ {spec['name']} — Pip value: ${spec['pip_value']}/pip/lot, "
                 f"Typical spread: {spec['typical_spread']} pips",
            fg="#28a745"
        )
    else:
        info_label.config(
            text=f"⚠️ Unknown symbol '{symbol}' — fill pip value and spread manually",
            fg="#e67e22"
        )

    # Recalculate lot size
    _recalculate_lot_size(entries)


def _recalculate_lot_size(entries):
    """Auto-recalculate lot size whenever capital, risk, or pip value changes."""
    try:
        capital = float(entries['starting_capital'].get())
        risk_pct = float(entries['risk_pct'].get())
        pip_value = float(entries['pip_value_per_lot'].get())

        symbol = entries['symbol'].get().strip().upper()
        spec = INSTRUMENT_SPECS.get(symbol, {})

        # Use default SL of 150 pips for calculation (backtester will use actual SL from exit strategies)
        default_sl_pips = 150

        risk_dollars = capital * (risk_pct / 100.0)
        sl_cost_per_lot = default_sl_pips * pip_value

        if sl_cost_per_lot > 0:
            lots = risk_dollars / sl_cost_per_lot
            lots = round(lots, 2)
            entries['fixed_lot_size'].set(str(lots))
    except (ValueError, ZeroDivisionError):
        pass  # fields not filled yet, ignore


def build_panel(parent):
    """Build the configuration panel"""
    global _rules_status_label, _price_status_label, _output_text
    global _config_firm_var, _config_stage_var, _config_acct_var

    # Outer frame
    panel = tk.Frame(parent, bg="#ffffff")

    # Scrollable canvas
    canvas = tk.Canvas(panel, bg="#ffffff", highlightthickness=0)
    scrollbar = tk.Scrollbar(panel, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)

    scrollbar.pack(side=tk.RIGHT, fill="y")
    canvas.pack(side=tk.LEFT, fill="both", expand=True)

    inner = tk.Frame(canvas, bg="#ffffff")
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_configure(event):
        canvas.configure(scrollregion=canvas.bbox("all"))
    inner.bind("<Configure>", _on_configure)

    def _on_canvas_resize(event):
        canvas.itemconfig(window_id, width=event.width)
    canvas.bind("<Configure>", _on_canvas_resize)

    # Safe mousewheel binding — doesn't break other canvases
    def _on_enter(event):
        canvas.bind("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        # Linux
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        canvas.unbind("<MouseWheel>")
        canvas.unbind("<Button-4>")
        canvas.unbind("<Button-5>")

    canvas.bind("<Enter>", _on_enter)
    canvas.bind("<Leave>", _on_leave)

    # Title
    tk.Label(inner, text="Project 2 - Backtesting Configuration",
             font=("Arial", 16, "bold"), bg="#ffffff", fg="#333333").pack(pady=(20, 5))
    tk.Label(inner, text="Validate discovered rules on in-sample and out-of-sample data",
             font=("Arial", 10), bg="#ffffff", fg="#666666").pack(pady=(0, 15))

    # ── Prerequisites ────────────────────────────────────────────────────────
    status_frame = tk.LabelFrame(inner, text="Prerequisites Status",
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

    config_frame = tk.LabelFrame(inner, text="Backtest Configuration",
                                 font=("Arial", 11, "bold"), bg="#ffffff", fg="#333333",
                                 padx=20, pady=15)
    config_frame.pack(fill="x", padx=20, pady=8)

    # Instrument
    instr_frame = tk.LabelFrame(config_frame, text="🌐 Instrument",
                                font=("Arial", 9, "bold"), bg="#ffffff", fg="#555555",
                                padx=10, pady=8)
    instr_frame.pack(fill="x", pady=(0, 8))
    _field_row(instr_frame, "Symbol", _make_var('symbol'),
               "Trading symbol. Type and click Lookup to auto-fill pip value and spread.")

    # Instrument info label (auto-updates)
    instrument_info = tk.Label(instr_frame, text="", font=("Arial", 9),
                                bg="#ffffff", fg="#666666")
    instrument_info.pack(fill="x", padx=4, pady=(0, 4))

    tk.Button(instr_frame, text="🔍 Lookup Symbol",
              command=lambda: _on_symbol_change(entries, instrument_info),
              bg="#667eea", fg="white", font=("Arial", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(anchor="w", padx=4, pady=(0, 4))

    # Entry Timeframe dropdown
    from tkinter import ttk
    tf_row = tk.Frame(instr_frame, bg="#ffffff")
    tf_row.pack(fill="x", pady=(4, 0))
    tk.Label(tf_row, text="Entry Timeframe", font=("Arial", 9, "bold"), bg="#ffffff",
             fg="#333333", width=30, anchor="w").pack(side=tk.LEFT)

    tf_combo = ttk.Combobox(tf_row, textvariable=_make_var('winning_scenario'),
                             values=["M5", "M15", "H1", "H4"], width=14, state="readonly")
    tf_combo.pack(side=tk.LEFT, padx=(5, 0))

    tk.Label(instr_frame,
             text="How often the bot checks for entry signals.\n"
                  "M5 = every 5 min (most trades, most noise)  |  M15 = every 15 min\n"
                  "H1 = every hour (balanced)  |  H4 = every 4 hours (fewest trades, cleanest)",
             font=("Arial", 8), bg="#ffffff", fg="#888888", justify=tk.LEFT,
             wraplength=520).pack(fill="x", padx=(4, 0), pady=(1, 4))

    _field_row(instr_frame, "Pip Value per Lot ($)", _make_var('pip_value_per_lot'),
               "Auto-filled from symbol lookup. Override only if your broker differs.")

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

    # Auto-detect dates button — prominent, full width
    auto_btn_frame = tk.Frame(period_frame, bg="#e8f5e9")  # light green background
    auto_btn_frame.pack(fill="x", pady=(10, 4), padx=2)

    tk.Button(auto_btn_frame, text="📅 Auto-Detect Dates from CSV (70/30 split)",
              command=lambda: _auto_detect_dates(entries),
              bg="#28a745", fg="white", font=("Arial", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=20, pady=8).pack(side=tk.LEFT, padx=5, pady=5)

    tk.Label(auto_btn_frame, text="Reads your price data and fills all 4 dates automatically",
             font=("Arial", 9), bg="#e8f5e9", fg="#555555").pack(side=tk.LEFT, padx=(10, 0))

    # ── Prop Firm Target ──────────────────────────────────────────────────────
    firm_frame = tk.LabelFrame(config_frame, text="🏢 Prop Firm Target (auto-fills settings below)",
                                font=("Arial", 10, "bold"), bg="#ffffff", fg="#333",
                                padx=10, pady=8)
    firm_frame.pack(fill="x", pady=(0, 10))

    firm_row = tk.Frame(firm_frame, bg="#ffffff")
    firm_row.pack(fill="x")

    tk.Label(firm_row, text="Firm:", font=("Arial", 9, "bold"),
             bg="#ffffff", fg="#333").pack(side=tk.LEFT)

    prop_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prop_firms')
    firm_names = ["None — manual settings"]
    for fp in sorted(glob.glob(os.path.join(prop_dir, '*.json'))):
        try:
            with open(fp, encoding='utf-8') as f:
                fd = json.load(f)
            firm_names.append(fd.get('firm_name', '?'))
        except:
            pass

    _config_firm_var = tk.StringVar(value="None — manual settings")
    firm_combo = ttk.Combobox(firm_row, textvariable=_config_firm_var,
                               values=firm_names, width=22, state="readonly")
    firm_combo.pack(side=tk.LEFT, padx=5)

    tk.Label(firm_row, text="Stage:", font=("Arial", 9, "bold"),
             bg="#ffffff", fg="#333").pack(side=tk.LEFT, padx=(15, 0))

    _config_stage_var = tk.StringVar(value="Funded")
    stage_combo = ttk.Combobox(firm_row, textvariable=_config_stage_var,
                                values=["Evaluation", "Funded"], width=12, state="readonly")
    stage_combo.pack(side=tk.LEFT, padx=5)

    tk.Label(firm_row, text="Account:", font=("Arial", 9, "bold"),
             bg="#ffffff", fg="#333").pack(side=tk.LEFT, padx=(15, 0))

    _config_acct_var = tk.StringVar(value="")
    acct_combo = ttk.Combobox(firm_row, textvariable=_config_acct_var,
                               values=[], width=10, state="readonly")
    acct_combo.pack(side=tk.LEFT, padx=5)

    # Info label
    firm_info = tk.Label(firm_frame, text="Select a firm to auto-fill capital, risk, and costs",
                          font=("Arial", 8), bg="#ffffff", fg="#888")
    firm_info.pack(anchor="w", pady=(3, 0))

    # Firm rules reminder
    from shared.firm_rules_reminder import show_reminder_on_firm_change
    _config_reminder = [None]
    show_reminder_on_firm_change(_config_firm_var, firm_frame, _config_reminder, _config_stage_var)

    def _on_firm_stage_change(*_):
        firm = _config_firm_var.get()
        stage = _config_stage_var.get().lower()

        if firm == "None — manual settings":
            firm_info.config(text="Manual mode — set values yourself")
            return

        # Load firm data
        for fp in sorted(glob.glob(os.path.join(prop_dir, '*.json'))):
            try:
                with open(fp, encoding='utf-8') as f:
                    fd = json.load(f)
                if fd.get('firm_name') != firm:
                    continue

                challenge = fd['challenges'][0]
                funded = challenge.get('funded', {})

                # Update account sizes dropdown
                sizes = challenge.get('account_sizes', [100000])
                acct_combo['values'] = [str(s) for s in sizes]
                if sizes:
                    _config_acct_var.set(str(sizes[0]))

                # Auto-fill starting capital
                if entries.get('starting_capital'):
                    entries['starting_capital'].set(_config_acct_var.get())

                # Auto-fill spread from instrument specs (not hardcoded)
                # WHY: Old code hardcoded "2.5" regardless of symbol —
                #      EURUSD users got 2.5 pips applied to a 0.8-pip
                #      market, overstating costs ~3x. Derive from the
                #      current symbol's entry in INSTRUMENT_SPECS. Falls
                #      back to 2.5 only if the symbol isn't in the table.
                # CHANGED: April 2026 — Phase 32 Fix 6 — per-symbol spread
                #          (audit Part C HIGH #94)
                if entries.get('spread'):
                    _cur_symbol = entries['symbol'].get().strip().upper() if entries.get('symbol') else 'XAUUSD'
                    _spec       = INSTRUMENT_SPECS.get(_cur_symbol, INSTRUMENT_SPECS.get('XAUUSD', {}))
                    _spread_val = float(_spec.get('typical_spread', 2.5))
                    entries['spread'].set(f"{_spread_val}")

                # Auto-fill risk from trading_rules
                trading_rules = fd.get('trading_rules', [])
                for rule in trading_rules:
                    if rule.get('stage') not in (stage, 'both'):
                        continue
                    params = rule.get('parameters', {})

                    if rule.get('type') in ('eval_settings', 'eval_strategy'):
                        risk_range = params.get('risk_pct_range', [0.8, 1.5])
                        if entries.get('risk_pct'):
                            entries['risk_pct'].set(str(risk_range[0]))
                        break

                    elif rule.get('type') == 'funded_accumulate':
                        risk_range = params.get('risk_pct_range', [0.3, 0.5])
                        if entries.get('risk_pct'):
                            entries['risk_pct'].set(str(risk_range[1]))
                        break
                else:
                    # No trading_rules — use defaults
                    if stage == "evaluation":
                        if entries.get('risk_pct'):
                            entries['risk_pct'].set("1.0")
                    else:
                        if entries.get('risk_pct'):
                            entries['risk_pct'].set("0.5")

                # Leverage info — per instrument, not per size
                # WHY: leverage_by_size shows the same number regardless of
                #      instrument (it's just a max forex leverage). Show the
                #      actual per-instrument leverage for the configured symbol
                #      so the user sees e.g. "1:10 (metals)" for XAUUSD.
                # CHANGED: April 2026 — instrument-aware leverage display
                try:
                    from shared.prop_firm_engine import get_leverage_for_symbol, get_instrument_type
                    _cur_sym  = entries['symbol'].get().strip().upper() if entries.get('symbol') else 'XAUUSD'
                    _cur_inst = get_instrument_type(_cur_sym)
                    _cur_lev  = get_leverage_for_symbol(fd, _cur_sym)
                    lev = f"1:{_cur_lev} ({_cur_inst})"
                except Exception:
                    lev = fd.get('leverage', '—')

                # DD info
                if stage == "evaluation":
                    phase = challenge.get('phases', [{}])[0]
                    daily = phase.get('max_daily_drawdown_pct', '?')
                    total = phase.get('max_total_drawdown_pct', '?')
                    target = phase.get('profit_target_pct', '?')
                    firm_info.config(
                        text=f"Target: {target}% | DD: {daily}%/{total}% | Leverage: {lev} | "
                             f"Risk auto-set for evaluation")
                else:
                    daily = funded.get('max_daily_drawdown_pct', '?')
                    total = funded.get('max_total_drawdown_pct', '?')
                    firm_info.config(
                        text=f"DD: {daily}%/{total}% | Leverage: {lev} | "
                             f"Risk auto-set for funded (conservative)")

                break
            except Exception:
                continue

    def _on_acct_change(*_):
        """Update capital when account size changes."""
        acct = _config_acct_var.get()
        if acct and entries.get('starting_capital'):
            entries['starting_capital'].set(acct)
        _on_firm_stage_change()

    _config_firm_var.trace_add("write", _on_firm_stage_change)
    _config_stage_var.trace_add("write", _on_firm_stage_change)
    _config_acct_var.trace_add("write", _on_acct_change)

    # WHY: Restore firm/stage selection from previously saved config so the
    #      user doesn't have to re-select their firm every time the panel opens.
    # CHANGED: April 2026 — persist firm selection (Change 4)
    _saved_firm = cfg.get('firm_name', '')
    if _saved_firm and _saved_firm in firm_names:
        _config_firm_var.set(_saved_firm)   # triggers _on_firm_stage_change
    _saved_stage = cfg.get('stage', '')
    if _saved_stage and _saved_stage in ("Evaluation", "Funded"):
        _config_stage_var.set(_saved_stage)

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
               "DYNAMIC = auto-calculates lot size each trade based on current equity and risk %.\n"
               "FIXED = always uses the calculated lot size below. DYNAMIC is recommended.")
    _field_row(risk_frame, "Calculated Lot Size", _make_var('fixed_lot_size'),
               "Auto-calculated from your capital, risk %, and pip value.\n"
               "Used when Lot Size = FIXED. Updates automatically when you change capital or risk.")

    # SL/TP info (backtester handles this automatically)
    sltp_info = tk.Frame(config_frame, bg="#e3f2fd", padx=10, pady=8)
    sltp_info.pack(fill="x", pady=(0, 8))
    tk.Label(sltp_info, text="🎯 Stop Loss & Take Profit",
             font=("Arial", 9, "bold"), bg="#e3f2fd", fg="#1565c0").pack(anchor="w")
    tk.Label(sltp_info,
             text="The backtester automatically tests 12 different exit strategies:\n"
                  "Fixed SL/TP (100/200, 150/300, 200/400), Trailing Stop, ATR-based,\n"
                  "Time-based, Indicator Exit, and Hybrid combinations.\n"
                  "You don't need to set SL/TP here — the best one is found automatically.",
             font=("Arial", 9), bg="#e3f2fd", fg="#333333", justify=tk.LEFT).pack(anchor="w", pady=(4, 0))

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
    output_frame = tk.LabelFrame(inner, text="Status Output",
                                 font=("Arial", 11, "bold"), bg="#ffffff", fg="#333333",
                                 padx=10, pady=10)
    output_frame.pack(fill="both", expand=True, padx=20, pady=8)

    _output_text = scrolledtext.ScrolledText(output_frame, height=10,
                                             font=("Courier", 9), bg="#f8f9fa",
                                             fg="#333333", wrap=tk.WORD)
    _output_text.pack(fill="both", expand=True)

    # Auto-recalculate when dependencies change
    entries['starting_capital'].trace_add('write', lambda *_: _recalculate_lot_size(entries))
    entries['risk_pct'].trace_add('write', lambda *_: _recalculate_lot_size(entries))
    entries['pip_value_per_lot'].trace_add('write', lambda *_: _recalculate_lot_size(entries))

    # Auto-fill instrument info on load
    _on_symbol_change(entries, instrument_info)

    check_prerequisites(_output_text, _rules_status_label, _price_status_label, silent=False)

    return panel


def refresh():
    """Refresh the panel (called when panel becomes active)"""
    global _rules_status_label, _price_status_label, _output_text
    if _output_text is not None:
        check_prerequisites(_output_text, _rules_status_label, _price_status_label, silent=False)

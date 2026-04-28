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
# WHY: This dict is a FALLBACK ONLY. Per-firm symbol specs (spread,
#      commission, swap, pip_value) live in prop_firms/<firm>.json
#      under "instrument_specs". When a firm is selected the panel
#      reads from that JSON. INSTRUMENT_SPECS is used ONLY when no
#      firm is selected or for instruments the firm hasn't documented.
#      Do not edit this dict to track per-firm reality — edit the
#      firm JSON instead. Per-firm specs are authoritative.
# CHANGED: April 2026 — clarify fallback role (per-firm authoritative)
INSTRUMENT_SPECS = {
    # WHY: Confirmed from prop firm broker diagnostic (April 2026).
    #      tickValue=$1.00, not $10.00. Spread 20-30 pips during
    #      London/NY sessions (37 off-hours). pip_size=0.01 confirmed.
    # CHANGED: April 2026 — match Get Leveraged broker specs
    'XAUUSD': {'pip_value': 1.0, 'pip_size': 0.01, 'typical_spread': 25, 'name': 'Gold'},
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
    # XAGUSD: 5000 oz × 0.001 pip_size = $5 per pip per 1 lot.
    # WHY: Scaling proportionally to XAUUSD broker correction (April 2026).
    #      tickValue for silver confirmed ~$0.50/tick at Get Leveraged.
    'XAGUSD': {'pip_value': 0.5,  'pip_size': 0.001, 'typical_spread': 2.0, 'name': 'Silver'},
    'US30':   {'pip_value': 1.0,  'pip_size': 1.0, 'typical_spread': 2.0, 'name': 'Dow Jones'},
    'NAS100': {'pip_value': 1.0,  'pip_size': 1.0, 'typical_spread': 1.5, 'name': 'Nasdaq'},
    'BTCUSD': {'pip_value': 1.0,  'pip_size': 1.0, 'typical_spread': 30.0, 'name': 'Bitcoin'},
}

DEFAULTS = {
    # Instrument
    'symbol':              'XAUUSD',
    'winning_scenario':    'H1',  # Entry timeframe: M5, M15, H1, H4
    'pip_value_per_lot':   '1.0',
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
    'spread':              '25',
    # Engine
    'hard_close_hour':     '21',
    'warmup_candles':      '200',
    'max_one_trade':       'True',
    'same_candle_sl_rule': 'LOSS',
    # WHY: Firm selection determines leverage, DD limits, risk auto-fill.
    #      Without these in DEFAULTS, load_config filters them out and
    #      the entire leverage-from-firm chain breaks.
    # CHANGED: April 2026 — persist firm selection across sessions
    'firm_id':             '',
    'firm_name':           '',
    'stage':               'Evaluation',
    'backtest_start':      '',
    'backtest_end':        '',
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

    # WHY: Commission was saved as 0.4 when pip_value was 10.0 ($4/10).
    #      With pip_value=1.0, it should be 4.0 ($4/1). Auto-fix if
    #      commission < 1.0 and looks like old conversion artifact.
    # CHANGED: April 2026 — auto-fix commission migration
    _comm = float(cfg.get('commission', 0))
    _pv = float(cfg.get('pip_value_per_lot', 1.0))
    if 0 < _comm < 1.0 and _pv == 1.0:
        # Likely old value from pip_value=10 era. Multiply by 10 to restore dollars.
        cfg['commission'] = str(_comm * 10)
        try:
            with open(path, 'w') as f:
                json.dump({k: cfg[k] for k in DEFAULTS.keys()}, f, indent=2)
            print(f"[CONFIG] Auto-fixed commission: {_comm} → {_comm * 10} (pip_value migration)")
        except Exception:
            pass

    # WHY: P2 config doesn't store firm info. Read from P1 so
    #      backtest display shows correct firm name.
    # CHANGED: April 2026 — firm info from P1 config
    if not cfg.get('firm_name'):
        try:
            import importlib.util as _ilu
            _p1_path = os.path.join(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))),
                'project1_reverse_engineering', 'config_loader.py')
            _spec = _ilu.spec_from_file_location('_p1cl', _p1_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _p1 = _mod.load()
            cfg['firm_name'] = _p1.get('prop_firm_name', '')
            cfg['firm_id'] = _p1.get('prop_firm_id', '')
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
        v = values.get(k, '2020-01-01')  # may be hidden
        if len(v) != 10 or v[4] != '-' or v[7] != '-':
            messagebox.showerror("Invalid Date", f"'{k}' must be YYYY-MM-DD format.\nGot: {v}")
            return

    # Validate backtest dates (empty = all time)
    for k in ['backtest_start', 'backtest_end']:
        v = values.get(k, '').strip()
        if v and (len(v) != 10 or v[4] != '-' or v[7] != '-'):
            messagebox.showerror("Invalid Date", f"'{k}' must be YYYY-MM-DD or empty.\nGot: {v}")
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

    tk.Label(instr_frame, text="M5=5min | M15=15min | H1=1hr | H4=4hr",
             font=("Arial", 8), bg="#ffffff", fg="#aaaaaa").pack(anchor="w", padx=4)

    _field_row(instr_frame, "Pip Value per Lot ($)", _make_var('pip_value_per_lot'),
               "Auto-filled from symbol lookup. Override only if your broker differs.")

    # ── Backtest Period ────────────────────────────────────────────────
    # WHY: User needs to control what date range the backtest runs on.
    #      Quick-select buttons for common periods.
    # CHANGED: April 2026 — backtest date range with quick buttons
    period_frame = tk.LabelFrame(config_frame, text="📅 Backtest Period",
                                 font=("Arial", 9, "bold"), bg="#ffffff", fg="#555555",
                                 padx=10, pady=8)
    period_frame.pack(fill="x", pady=(0, 8))

    _bt_start_var = _make_var('backtest_start')
    _bt_end_var = _make_var('backtest_end')

    date_row = tk.Frame(period_frame, bg="#ffffff")
    date_row.pack(fill="x")
    tk.Label(date_row, text="Start:", font=("Arial", 9, "bold"),
             bg="#ffffff", fg="#333", width=6, anchor="w").pack(side=tk.LEFT)
    tk.Entry(date_row, textvariable=_bt_start_var, width=12,
             font=("Courier", 10)).pack(side=tk.LEFT, padx=(0, 10))
    tk.Label(date_row, text="End:", font=("Arial", 9, "bold"),
             bg="#ffffff", fg="#333").pack(side=tk.LEFT)
    tk.Entry(date_row, textvariable=_bt_end_var, width=12,
             font=("Courier", 10)).pack(side=tk.LEFT, padx=(0, 10))
    tk.Label(date_row, text="(empty = all time)",
             font=("Arial", 8), bg="#ffffff", fg="#999").pack(side=tk.LEFT)

    btn_row = tk.Frame(period_frame, bg="#ffffff")
    btn_row.pack(fill="x", pady=(6, 0))

    from datetime import datetime as _dt, timedelta as _td

    def _set_period(years):
        end = _dt.now()
        if years == 0:
            _bt_start_var.set('')
            _bt_end_var.set('')
        else:
            start = end - _td(days=years * 365)
            _bt_start_var.set(start.strftime('%Y-%m-%d'))
            _bt_end_var.set(end.strftime('%Y-%m-%d'))

    for _yrs, _lbl in [(1, "1Y"), (2, "2Y"), (3, "3Y"), (5, "5Y"), (10, "10Y"), (0, "All")]:
        _bg = "#667eea" if _yrs > 0 else "#28a745"
        tk.Button(btn_row, text=_lbl,
                  command=lambda y=_yrs: _set_period(y),
                  bg=_bg, fg="white", font=("Arial", 9, "bold"),
                  relief=tk.FLAT, cursor="hand2", padx=10, pady=3
                  ).pack(side=tk.LEFT, padx=(0, 4))

    # Keep old insample/outsample variables alive for save_config
    _make_var('insample_start')
    _make_var('insample_end')
    _make_var('outsample_start')
    _make_var('outsample_end')

    # ── Prop Firm (from P1 Run Scenarios) ───────────────────────────────────
    # WHY: Firm is now selected in P1 Run Scenarios — single source of truth.
    #      P2 config just displays what P1 has set and auto-fills fields.
    # CHANGED: April 2026 — P1 is source of truth; remove duplicate selector
    _p2_firm_frame = tk.Frame(config_frame, bg="#f5f5fa", padx=10, pady=8)
    _p2_firm_frame.pack(fill="x", pady=(0, 8))

    _p2_firm_name = ''
    _p2_firm_stage = 'Evaluation'
    _p2_firm_account = ''
    try:
        import importlib.util as _p2_ilu
        _p2_cl_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   'project1_reverse_engineering', 'config_loader.py')
        _p2_spec = _p2_ilu.spec_from_file_location('_p2_cl', _p2_cl_path)
        _p2_mod = _p2_ilu.module_from_spec(_p2_spec)
        _p2_spec.loader.exec_module(_p2_mod)
        _p2_p1cfg = _p2_mod.load()
        _p2_firm_name = _p2_p1cfg.get('prop_firm_name', '')
        _p2_firm_stage = _p2_p1cfg.get('prop_firm_stage', 'Evaluation')
        _p2_firm_account = _p2_p1cfg.get('prop_firm_account', '')
    except Exception:
        pass

    _p2_firm_lev = ''
    if _p2_firm_name:
        # Auto-fill capital
        if _p2_firm_account and entries.get('starting_capital'):
            entries['starting_capital'].set(_p2_firm_account)
        # Get leverage + auto-fill risk and spread
        try:
            _p2_prop_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prop_firms')
            for _p2_fp in sorted(glob.glob(os.path.join(_p2_prop_dir, '*.json'))):
                with open(_p2_fp, encoding='utf-8') as _p2_ff:
                    _p2_fd = json.load(_p2_ff)
                if _p2_fd.get('firm_name') != _p2_firm_name:
                    continue
                from shared.prop_firm_engine import get_leverage_for_symbol, get_instrument_type
                _p2_sym = entries['symbol'].get().strip().upper() if entries.get('symbol') else 'XAUUSD'
                _p2_firm_lev = f"1:{get_leverage_for_symbol(_p2_fd, _p2_sym)} ({get_instrument_type(_p2_sym)})"
                # Auto-fill spread
                if entries.get('spread'):
                    _p2_spec_d = INSTRUMENT_SPECS.get(_p2_sym, INSTRUMENT_SPECS.get('XAUUSD', {}))
                    entries['spread'].set(str(float(_p2_spec_d.get('typical_spread', 2.5))))
                # Auto-fill risk from trading_rules
                _p2_stage = _p2_firm_stage.lower()
                for _p2_rule in _p2_fd.get('trading_rules', []):
                    if _p2_rule.get('stage') not in (_p2_stage, 'both'):
                        continue
                    _p2_params = _p2_rule.get('parameters', {})
                    if _p2_rule.get('type') in ('eval_settings', 'eval_strategy'):
                        if entries.get('risk_pct'):
                            entries['risk_pct'].set(str(_p2_params.get('risk_pct_range', [1.0])[0]))
                        break
                    elif _p2_rule.get('type') == 'funded_accumulate':
                        if entries.get('risk_pct'):
                            entries['risk_pct'].set(str(_p2_params.get('risk_pct_range', [0.5])[-1]))
                        break
                break
        except Exception:
            pass
        tk.Label(_p2_firm_frame,
                 text=f"\U0001f3e2 {_p2_firm_name}  |  {_p2_firm_stage}  |  Leverage: {_p2_firm_lev}",
                 font=("Segoe UI", 10, "bold"), bg="#f5f5fa", fg="#333").pack(anchor="w")
        tk.Label(_p2_firm_frame,
                 text="Selected in P1 Run Scenarios \u2014 capital, risk, spread auto-filled below",
                 font=("Arial", 8), bg="#f5f5fa", fg="#888").pack(anchor="w")
    else:
        tk.Label(_p2_firm_frame,
                 text="\U0001f3e2 No firm selected \u2014 go to P1 Run Scenarios to choose a prop firm",
                 font=("Segoe UI", 10, "italic"), bg="#f5f5fa", fg="#999").pack(anchor="w")

    # Keep module-level vars alive for save_config compatibility
    global _config_firm_var, _config_stage_var, _config_acct_var
    _config_firm_var = tk.StringVar(value=_p2_firm_name or "None — manual settings")
    _config_stage_var = tk.StringVar(value=_p2_firm_stage or "Evaluation")
    _config_acct_var = tk.StringVar(value=_p2_firm_account or "")

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
    # WHY: lot_size_calc and fixed_lot_size are not read by Run Backtest —
    #      the backtester always computes lots dynamically from risk%.
    # CHANGED: April 2026 — grayed out unused fields
    _make_var('lot_size_calc')
    _make_var('fixed_lot_size')
    _unused_risk = tk.Frame(risk_frame, bg="#f0f0f0")
    _unused_risk.pack(fill="x", pady=(4, 0))
    tk.Label(_unused_risk,
             text="Lot Size Calc / Fixed Lot \u2014 not used by Run Backtest (always DYNAMIC from risk%)",
             font=("Arial", 8, "italic"), bg="#f0f0f0", fg="#aaaaaa").pack(anchor="w", padx=5, pady=2)

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
    _field_row(engine_frame, "Max One Trade Open (True/False)", _make_var('max_one_trade'),
               "True = only one trade can be open at a time (recommended for trend systems).\n"
               "False = multiple simultaneous trades allowed.")
    _field_row(engine_frame, "Same-Candle SL Rule (LOSS/WIN)", _make_var('same_candle_sl_rule'),
               "When SL and TP both trigger on the same candle, count it as LOSS or WIN.\n"
               "LOSS is the conservative/realistic default.")
    # WHY: hard_close_hour is not implemented in the backtester.
    #      warmup_candles is hardcoded to 200 and not configurable at runtime.
    # CHANGED: April 2026 — grayed out unused fields
    _make_var('hard_close_hour')
    _make_var('warmup_candles')
    _unused_eng = tk.Frame(engine_frame, bg="#f0f0f0")
    _unused_eng.pack(fill="x", pady=(4, 0))
    tk.Label(_unused_eng,
             text="Hard Close Hour / Warmup Candles \u2014 not used by Run Backtest (warmup hardcoded to 200)",
             font=("Arial", 8, "italic"), bg="#f0f0f0", fg="#aaaaaa").pack(anchor="w", padx=5, pady=2)

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

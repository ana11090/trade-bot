"""
Project 1 - Configuration Panel
All Project 1 settings in one place. Saved to p1_config.json and read by all step scripts.
"""

import tkinter as tk
from tkinter import messagebox, scrolledtext
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

# WHY (Phase 47 Fix 1): Old panel DEFAULTS was missing keys that
#      config_loader.DEFAULTS has — align_timeframes, lookback_candles,
#      skip_m1_features. Saving from the panel silently dropped these
#      because save_config whitelist filters by panel DEFAULTS. step1
#      and step2 then fell back to their own hardcoded defaults
#      instead of the user's intent. Sync with config_loader so all
#      keys round-trip through the panel.
# WHY (Phase 47 Fix 2): Renamed pip_value_usd → pip_size to match
#      Phase 46 Fix 6's correct semantics. Added pip_value_per_lot_usd
#      for the actual dollar value. Old key kept as a deprecated alias
#      so users with existing saved configs aren't broken.
# CHANGED: April 2026 — Phase 47 Fix 1+2 — sync with config_loader
#          (audit Part D HIGH #60/61)
DEFAULTS = {
    'symbol':                   'XAUUSD',
    'broker_timezone':          'EET',
    'pip_size':                 '0.01',     # raw price units per pip
    'pip_value_per_lot_usd':    '1.0',      # dollar value per pip per 1 lot
    'pip_value_usd':            '0.01',     # DEPRECATED — kept for backward compat with old configs
    'alignment_tolerance_pips': '150',
    'min_lookback_candles':     '200',
    'align_timeframes':         'M5,M15,H1,H4,D1',
    'lookback_candles':         '200',
    'skip_m1_features':         'true',
    'utc_offset_hours':         '2',        # Phase 60 Fix 1a + Phase 62 Fix 4 sync
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

        # WHY (Phase 47 Fix 2): Old description called this "Pip Value
        #      (USD)" but the values listed (0.01, 0.0001, 0.00007) are
        #      pip SIZES in raw price units, NOT pip values in USD.
        #      A user reading "Pip Value (USD)" for EURUSD might
        #      correctly enter $10 (the actual dollar pip value for 1
        #      lot) but downstream code uses the field as a pip-size
        #      multiplier and would compute wildly wrong dollar costs.
        #      Replace with two correctly-named fields.
        # CHANGED: April 2026 — Phase 47 Fix 2 — semantic field names
        #          (audit Part D HIGH #61)
        ('pip_size',
         'Pip Size (raw price units)',
         'How much a 1-pip move is in raw price units.\n'
         'XAUUSD = 0.01  |  EURUSD = 0.0001  |  GBPJPY = 0.01\n'
         'Used for converting candle high/low to pips and back.'),

        ('pip_value_per_lot_usd',
         'Pip Value per 1 Lot (USD)',
         'Dollar value of a 1-pip move per 1.0 standard lot.\n'
         'XAUUSD = $1.00  |  EURUSD = $10.00  |  GBPJPY ≈ $6.70 (rate-dep)\n'
         'Used for computing dollar P&L. Check broker contract spec if unsure.'),

        # WHY (Phase 53 Fix 6): These three keys were added to DEFAULTS
        #      in Phase 47 Fix 1 but had no FIELDS entry, so the panel
        #      didn't render input widgets for them. User couldn't set
        #      them from the GUI. Add entries so they're editable.
        # CHANGED: April 2026 — Phase 53 Fix 6 — UI for pipeline params
        #          (audit Part D MED #66)
        ('align_timeframes',
         'Align Timeframes',
         'Comma-separated list of timeframes to align trades to.\n'
         'Default: M5,M15,H1,H4,D1\n'
         'Add M1 for ultra-short or W1 for swing strategies. Order does not matter.'),

        ('lookback_candles',
         'Lookback Candles (warmup)',
         'Number of preceding candles required before a trade can have valid indicators.\n'
         'Default 200. Lower = include more warmup-zone trades but with NaN-heavy features.\n'
         'Higher = stricter, fewer trades.'),

        ('skip_m1_features',
         'Skip M1 features',
         'true/false. Skip M1 in step2 even if it appears in align_timeframes.\n'
         'M1 has so many candles it can crash on small machines. Default: true.\n'
         'Set false ONLY if you have RAM and your strategy needs M1 indicators.'),

        ('alignment_tolerance_pips',
         'Alignment Tolerance (pips)',
         'How far (in pips) a trade\'s open price can be outside the matched candle\'s\n'
         'high/low range before being flagged as misaligned.\n'
         'XAUUSD needs ~150 (bid/ask vs mid-price diff). EURUSD needs ~5.'),
    ],
    'pipeline': [
        # WHY (Phase 62 Fix 4): align_timeframes, skip_m1_features,
        #      lookback_candles, and utc_offset_hours were in config_loader
        #      DEFAULTS but had no UI fields. Users could not set them from
        #      the panel; manual JSON edits were silently dropped on next save.
        # CHANGED: April 2026 — Phase 62 Fix 4 — expose pipeline fields in UI
        #          (audit Part D MEDIUM #66)
        ('align_timeframes',
         'Timeframes to Align',
         'Comma-separated list of timeframes to compute indicators for.\n'
         'Example: M5,M15,H1,H4,D1\n'
         'Must match the price data files you have downloaded.'),

        ('lookback_candles',
         'Lookback Candles',
         'Number of candles of history needed before a trade to compute indicators.\n'
         'Minimum 50. Recommended 200.'),

        ('min_lookback_candles',
         'Min Lookback Candles (legacy alias)',
         'Alias for Lookback Candles — kept for backwards compatibility with older configs.'),

        ('skip_m1_features',
         'Skip M1 Features',
         'Set to true to skip M1 (1-minute) indicator computation.\n'
         'M1 has many candles and slows step2 significantly.\n'
         'Set to false only if you have M1 price data and want M1_ features.'),

        ('utc_offset_hours',
         'Broker UTC Offset (hours)',
         'Hours to subtract from broker timestamp to get UTC.\n'
         'EET (winter) = 2, EET (summer/DST) = 3, GMT = 0, EST = 5.\n'
         'Used to align hour_of_day features with the live EA (which uses TimeGMT()).'),
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
    # Phase 47 Fix 2: added pip_size and pip_value_per_lot_usd to validation
    float_keys = ['pip_size', 'pip_value_per_lot_usd', 'pip_value_usd',
                  'alignment_tolerance_pips', 'train_test_split',
                  'rule_min_confidence', 'match_rate_threshold']
    int_keys   = ['min_lookback_candles', 'lookback_candles', 'utc_offset_hours',
                  'rf_trees', 'max_tree_depth',
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
    # WHY (Phase 62 Fix 2, originally Phase 53 Fix 4): Old code
    #      hardcoded "script_download_historicaldata_xauusd/" in the
    #      warning text. Other instruments don't have this directory.
    #      Make the path symbol-aware by reading from config and
    #      noting that the directory may not exist for non-XAUUSD.
    # CHANGED: April 2026 — Phase 62 Fix 2 (confirmed Phase 53 Fix 4)
    #          — instrument-agnostic Dukascopy description
    #          (audit Part D MED #64, #73)
    try:
        from project1_reverse_engineering import config_loader as _cl
        _sym_lower = _cl.load().get('symbol', 'XAUUSD').lower()
    except Exception:
        _sym_lower = 'xauusd'
    _downloader_path = f"project1/script_download_historicaldata_{_sym_lower}/"

    tk.Label(info,
             text=f"The autonomous Dukascopy downloader ({_downloader_path})\n"
                  f"covers 2005–present with auto-retry, IF a downloader exists for your symbol.\n"
                  f"For non-XAUUSD, you may need to create the downloader directory manually.",
             bg="#fff3cd", fg="#856404", font=("Segoe UI", 9), justify=tk.LEFT).pack(anchor="w", pady=(4, 0))

    # WHY (Phase 47 Fix 4): "Download from MT5 (Recommended)" button
    #      didn't check OS. Linux/Mac users clicked it and got a
    #      cryptic subprocess error buried in the output text widget.
    #      MT5 only runs on Windows. Wrap the click handler with an
    #      OS check that shows a clear messagebox on non-Windows.
    # CHANGED: April 2026 — Phase 47 Fix 4 — OS-aware download button
    #          (audit Part D HIGH #63)
    def _mt5_download_with_os_check():
        import platform
        if platform.system() != 'Windows':
            messagebox.showerror(
                "MT5 Download Unavailable",
                f"MetaTrader 5 only runs on Windows. Detected OS: "
                f"{platform.system()}.\n\n"
                f"On Linux/Mac, use one of these alternatives:\n"
                f"  • Run the autonomous Dukascopy downloader\n"
                f"  • Use 'Download from yfinance' (limited history)\n"
                f"  • Copy candle CSVs from a Windows MT5 install"
            )
            return
        download_data_mt5(_output_text)

    # WHY (Phase 62 Fix 1): "Recommended" label was shown to all users
    #      regardless of platform. MT5 only runs on Windows; Linux/Mac
    #      users who clicked the button got a cryptic subprocess failure.
    #      Check platform and update label + tooltip accordingly.
    # CHANGED: April 2026 — Phase 62 Fix 1 — platform-aware MT5 button
    #          (audit Part D MEDIUM #63)
    import platform as _platform
    _is_windows = _platform.system() == 'Windows'
    _mt5_label  = "🔽  Download from MT5 (Recommended)" if _is_windows \
                  else "🔽  Download from MT5 (Windows only)"
    _mt5_color  = "#27ae60" if _is_windows else "#95a5a6"
    tk.Button(right_frame, text=_mt5_label,
              bg=_mt5_color, fg="white", font=("Segoe UI", 10, "bold"),
              bd=0, pady=10, cursor="hand2",
              command=_mt5_download_with_os_check).pack(fill="x", pady=(0, 5))

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
    # WHY (Phase 47 Fix 3): Old code hardcoded a 4-TF list. A user
    #      running only H1 saw "1/4 timeframes found 🟡" — yellow
    #      status light for a healthy setup. Read align_timeframes
    #      from config_loader so the check matches the user's actual
    #      pipeline configuration.
    # CHANGED: April 2026 — Phase 47 Fix 3 — config-driven TF check
    #          (audit Part D HIGH #62)
    try:
        from project1_reverse_engineering import config_loader as _cl
        _cfg = _cl.load()
        _tf_str = _cfg.get('align_timeframes', 'M5,M15,H1,H4,D1')
        timeframes = [t.strip().upper() for t in _tf_str.split(',') if t.strip()]
    except Exception:
        timeframes = ['M5', 'M15', 'H1', 'H4']

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

    _total = len(timeframes)
    if not silent:
        output_text.insert(tk.END, f"\n{found}/{_total} timeframe files found.\n")

    if found == _total:
        price_status_label.config(
            text=f"✓ Price Data: all {_total} timeframes found", fg="#27ae60")
    elif found > 0:
        price_status_label.config(
            text=f"⚠️  Price Data: {found}/{_total} timeframes found", fg="#f39c12")
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
        # WHY (Phase 62 Fix 3): Old code redirected stdout to a bounded buffer
        #      and displayed it all at once at the end. Long downloads
        #      (minutes) left the user staring at a blank output box.
        #      Replace with a queue-based streaming approach: write lines
        #      to a queue, a Tkinter after-loop drains it into the text
        #      widget every 200ms.
        # CHANGED: April 2026 — Phase 62 Fix 3 — streaming stdout output
        #          (audit Part D MEDIUM #65)
        import io, queue as _queue
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        _q = _queue.Queue()

        class _StreamWriter(io.TextIOBase):
            def write(self, s):
                if s:
                    _q.put(s)
                return len(s)
            def flush(self):
                pass

        def _drain():
            _lines = []
            try:
                while True:
                    _lines.append(_q.get_nowait())
            except _queue.Empty:
                pass
            if _lines:
                output_text.after(0, lambda _l=''.join(_lines): (
                    output_text.insert(tk.END, _l),
                    output_text.see(tk.END)
                ))
            if _running[0]:
                output_text.after(200, _drain)

        _running = [True]
        output_text.after(200, _drain)
        old = sys.stdout
        sys.stdout = _StreamWriter()
        try:
            import download_price_data
            download_price_data.download_data_yfinance()
            download_price_data.verify_data_coverage()
        except Exception as e:
            _q.put(f"\nERROR: {e}\n")
        finally:
            sys.stdout = old
            _running[0] = False
            # Drain any remaining output
            import time; time.sleep(0.25)
            _leftover = []
            try:
                while True:
                    _leftover.append(_q.get_nowait())
            except _queue.Empty:
                pass
            if _leftover:
                output_text.after(0, lambda _l=''.join(_leftover): (
                    output_text.insert(tk.END, _l),
                    output_text.see(tk.END)
                ))
        output_text.after(0, lambda: messagebox.showinfo(
            "Done", "Download finished.\nClick 'Check Data Status' to verify."))

    threading.Thread(target=run, daemon=True).start()


def refresh():
    global _trade_status_label, _price_status_label, _output_text
    if _output_text is not None:
        check_all_data(_output_text, _trade_status_label, _price_status_label, silent=False)

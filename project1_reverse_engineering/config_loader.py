"""
Project 1 - Shared Config Loader
All step scripts import this to get their settings.
Values come from p1_config.json if it exists, otherwise from DEFAULTS below.
"""

import os
import json

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'p1_config.json')

# WHY (Phase 46 Fix 6): Old field 'pip_value_usd' = '0.01' was
#      semantically wrong — 0.01 is XAUUSD's pip SIZE, not its pip
#      VALUE in USD (which is ~$1 per mini lot). The field name
#      misled users. Add the correctly-named 'pip_size' alongside
#      and a new 'pip_value_per_lot_usd' carrying the actual dollar
#      value. Old field stays for backward compat — load() reads
#      both into pip_size if pip_size is absent.
# CHANGED: April 2026 — Phase 46 Fix 6 — correct field semantics
#          (audit Part D HIGH #57)
DEFAULTS = {
    # ── Instrument ────────────────────────────────────────────────────────────
    'symbol':                    'XAUUSD',
    'broker_timezone':           'EET',
    'pip_size':                  '0.01',     # XAUUSD: 0.01 raw price = 1 pip
    'pip_value_per_lot_usd':     '1.0',      # XAUUSD: $1 per pip per 1.0 lot
    'pip_value_usd':             '0.01',     # DEPRECATED — use pip_size; kept for backward compat
    'alignment_tolerance_pips':  '150',

    # ── Pipeline ──────────────────────────────────────────────────────────────
    'min_lookback_candles':      '200',

    # ── Alignment ─────────────────────────────────────────────────────────────
    'align_timeframes':          'M5,M15,H1,H4,D1',
    'lookback_candles':          '200',

    # ── Feature Engineering ───────────────────────────────────────────────────
    'skip_m1_features':          'true',   # M1 has too many candles, skip by default

    # ── Machine Learning ──────────────────────────────────────────────────────
    'train_test_split':          '0.80',
    'rf_trees':                  '500',
    'max_tree_depth':            '6',
    'min_samples_leaf':          '10',
    # WHY (Phase 77 Fix 58): allow config control of RF randomness + parallelism
    'rf_random_state':           '42',   # seed for reproducibility; change to test stability
    'rf_n_jobs':                 '-1',   # CPU cores: -1 = all, 1 = single-threaded

    # ── Rule Extraction ───────────────────────────────────────────────────────
    'rule_min_confidence':       '0.65',
    'rule_min_coverage':         '5',
    'match_rate_threshold':      '0.70',
    # WHY (Phase A.29): five new tunables exposed in the Run Scenarios panel
    #      so the user can widen / tighten the rule discovery without
    #      editing analyze.py. Old code hardcoded depth=5, leaf=20,
    #      split=40, leaf-filter=15. With those defaults the resulting
    #      rule set was very narrow — ~10 rules covering only a few
    #      percent of candles. Lowering them produces 30–80 rules
    #      covering a much larger fraction. rule_min_avg_pips is new
    #      and lets mixed-confidence leaves survive if they are still
    #      profitable on average — the user explicitly wants this.
    # CHANGED: April 2026 — Phase A.29
    'rule_tree_max_depth':       '5',
    'rule_tree_min_samples_leaf':'20',
    'rule_tree_min_samples_split':'40',
    'rule_min_leaf_samples':     '15',
    'rule_min_avg_pips':         '0',

    # ── Bot Entry Discovery (Phase A.31) ──────────────────────────────────────
    # WHY: Separate hyperparameters for the candle-level entry discoverer.
    #      Independent from the rule_* keys above which control the
    #      legacy decision-tree on the trade-level dataset.
    # CHANGED: April 2026 — Phase A.31
    'bot_entry_max_rules':       '25',
    'bot_entry_max_depth':       '4',
    'bot_entry_min_coverage':    '20',
    'bot_entry_min_win_rate':    '0.55',

    # ── Regime Analysis ───────────────────────────────────────────────────────
    # WHY (Phase 57 Fix 4): ADX 25 was hardcoded in analyze.py.
    # CHANGED: April 2026 — Phase 57 Fix 4a — ADX threshold to config
    'adx_trend_threshold':       '25',

    # ── Timezone ──────────────────────────────────────────────────────────────
    # WHY (Phase 60 Fix 1): Training hour_of_day was broker-server time;
    #      live EA uses TimeGMT() (UTC). Session features fired at different
    #      hours. Add UTC offset so step2 can normalize hour_of_day to UTC.
    #      Common values: EET=2, GMT=0, EST=-5, CST=-6, PST=-8.
    #      DST: EET alternates between UTC+2 and UTC+3; use 2 (conservative).
    # CHANGED: April 2026 — Phase 60 Fix 1a — UTC offset config
    #          (audit Part D HIGH #7)
    'utc_offset_hours':          '2',   # EET default (broker server UTC+2 in winter)
}


def load():
    """Return config dict — saved values where available, defaults otherwise.

    WHY (Phase 46 Fix 6b): Old code read 'pip_value_usd' as the pip
         size, which was misnamed. New schema uses 'pip_size' for the
         pip-size and 'pip_value_per_lot_usd' for the dollar value.
         If a saved config still has the legacy 'pip_value_usd' field
         and no 'pip_size', migrate the value across so existing
         configs keep working.
    WHY (Phase 46 Fix 7): Old fallback was print() which the GUI
         panel doesn't capture. Use the shared logger if available
         so warnings reach the standard log handlers and any panels
         that subscribe to them. Also: warn when keys in the saved
         file are dropped because they're not in DEFAULTS.
    CHANGED: April 2026 — Phase 46 Fix 6b/7 — migrate + visible failures
             (audit Part D HIGH #57/58/59)
    """
    cfg = dict(DEFAULTS)
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, 'r') as f:
                saved = json.load(f)
            # Migrate legacy field if present
            if 'pip_value_usd' in saved and 'pip_size' not in saved:
                saved['pip_size'] = saved['pip_value_usd']
            # Track dropped keys
            _accepted = {k: str(v) for k, v in saved.items() if k in DEFAULTS}
            _dropped = [k for k in saved.keys() if k not in DEFAULTS]
            cfg.update(_accepted)
            if _dropped:
                _msg = (f"[config_loader] Dropped {len(_dropped)} unknown "
                        f"keys from p1_config.json: {_dropped}. Add them to "
                        f"DEFAULTS in config_loader.py to make them stick.")
                try:
                    from shared.logging_setup import get_logger
                    get_logger(__name__).warning(_msg)
                except Exception:
                    print(_msg)
        except Exception as e:
            _err = f"[config_loader] Could not read p1_config.json: {e}. Using defaults."
            try:
                from shared.logging_setup import get_logger
                get_logger(__name__).error(_err)
            except Exception:
                print(_err)
    return cfg


# WHY (Phase A.29): Run Scenarios panel needs to persist the new
#      Discovery Settings card values back to p1_config.json. Old
#      code only had load() — saving was done by other panels with
#      their own ad-hoc writers. Provide a single shared writer
#      that merges new values into the existing file (preserves
#      every other key untouched), validates against DEFAULTS so
#      typos don't silently disappear via the dropped-key path,
#      and writes atomically (write to .tmp then rename).
# CHANGED: April 2026 — Phase A.29 — save() helper
def save(updates):
    """Merge `updates` (dict of str→str) into p1_config.json on disk.

    Only keys that exist in DEFAULTS are accepted. Unknown keys are
    silently ignored (matching the load() drop semantics) — caller
    should add them to DEFAULTS first if they want them to stick.
    Atomic via write-then-rename.
    """
    cfg = load()  # current state, including any saved overrides
    for k, v in (updates or {}).items():
        if k in DEFAULTS:
            cfg[k] = str(v)
    # Drop the inherited DEFAULTS entries that were never customised
    # — only persist the actual file content. Read the existing file
    # first to know which keys were explicitly saved before, then
    # overlay the new updates.
    on_disk = {}
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, 'r') as f:
                on_disk = json.load(f)
        except Exception:
            on_disk = {}
    for k, v in (updates or {}).items():
        if k in DEFAULTS:
            on_disk[k] = str(v)
    tmp_path = _CONFIG_FILE + '.tmp'
    try:
        with open(tmp_path, 'w') as f:
            json.dump(on_disk, f, indent=2)
        # Atomic replace
        if os.path.exists(_CONFIG_FILE):
            os.replace(tmp_path, _CONFIG_FILE)
        else:
            os.rename(tmp_path, _CONFIG_FILE)
        return True
    except Exception as e:
        try:
            from shared.logging_setup import get_logger
            get_logger(__name__).error(
                f"[config_loader] Could not save p1_config.json: {e}"
            )
        except Exception:
            print(f"[config_loader] Could not save p1_config.json: {e}")
        # Clean up the tmp file if it was written
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False

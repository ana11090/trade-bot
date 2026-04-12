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

    # ── Rule Extraction ───────────────────────────────────────────────────────
    'rule_min_confidence':       '0.65',
    'rule_min_coverage':         '5',
    'match_rate_threshold':      '0.70',

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

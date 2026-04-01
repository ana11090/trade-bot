"""
Project 1 - Shared Config Loader
All step scripts import this to get their settings.
Values come from p1_config.json if it exists, otherwise from DEFAULTS below.
"""

import os
import json

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'p1_config.json')

DEFAULTS = {
    # ── Instrument ────────────────────────────────────────────────────────────
    'symbol':                    'XAUUSD',
    'broker_timezone':           'EET',
    'pip_value_usd':             '0.01',
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
}


def load():
    """Return config dict — saved values where available, defaults otherwise."""
    cfg = dict(DEFAULTS)
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, 'r') as f:
                saved = json.load(f)
            cfg.update({k: str(v) for k, v in saved.items() if k in DEFAULTS})
        except Exception as e:
            print(f"[config_loader] Warning: could not read p1_config.json: {e}")
    return cfg

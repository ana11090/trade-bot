"""
Per-firm settings resolver.

WHY: Run Backtest, Strategy Refiner, and Strategy Validator all run
     fast_backtest with the same cost model — but each was wiring its
     own per-firm logic separately, leading to silent divergence when
     a firm-specific value (max_spread, hard_close) was set on one
     panel and not the others. This module centralises the resolution
     so all three call sites pick up the same values.

CHANGED: April 2026 — per-firm parity for Refiner + Validator
"""
import os
import json
import glob


def _read_bt_config():
    """Load backtest_config.json. Returns {} on any error."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _path = os.path.join(_here, '..', 'project2_backtesting',
                             'backtest_config.json')
        with open(_path, 'r', encoding='utf-8') as _f:
            return json.load(_f)
    except Exception:
        return {}


def _read_firm_json(firm_name):
    """Find the firm's JSON file in prop_firms/. Returns dict or None."""
    if not firm_name:
        return None
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _pf_dir = os.path.join(_here, '..', 'prop_firms')
        # WHY: firm_name in saved rules can carry a "(stage)" suffix
        #      e.g. "Get Leveraged (Evaluation)" — strip it for matching.
        # CHANGED: April 2026 — match the cleaning done in run_backtest_panel
        _key = firm_name.split(' (')[0]
        for _fp in glob.glob(os.path.join(_pf_dir, '*.json')):
            try:
                with open(_fp, 'r', encoding='utf-8') as _f:
                    _data = json.load(_f)
                if _data.get('firm_name', '') == _key:
                    return _data
            except Exception:
                continue
    except Exception:
        pass
    return None


def resolve_firm_settings(firm_name, symbol, use_config=True):
    """
    Resolve per-firm cost/exit parameters with fallback to backtest_config.json.

    Args:
        firm_name: display name from strategy run_settings or '' if none.
        symbol:    e.g. 'XAUUSD'.
        use_config: if False, returns "off" defaults (every value disabled
                    or zero) — pre-A.48 behaviour when Use Config is off.

    Returns dict with these keys (all always present):
        spread_pips                (float)
        commission_pips            (float)
        slippage_pips              (float)
        max_spread_pips            (float)   0 = disabled
        force_close_hour           (int)     -1 = disabled (overnight holds allowed)
        no_trades_window_start_hour (int)    -1 = disabled (start of no-entry window)
        no_trades_window_end_hour   (int)    -1 = disabled (end exclusive; wraps midnight when end<=start)
        allow_overnight_trades     (bool)    documentation-only flag from firm JSON
        variable_spread            (bool)
        session_spread_multipliers (dict|None)
        min_hold_minutes           (int)     0 = no minimum
        cooldown_candles           (int)     0 = no cooldown
        swap_long_pips_per_night   (float)
        swap_short_pips_per_night  (float)
        firm_resolved              (bool)
        symbol                     (str)
    """
    _sym = (symbol or 'XAUUSD').upper()

    if not use_config:
        # WHY: When "Use Config" is OFF, mirror the panel's default zeros.
        # CHANGED: April 2026 — preserve pre-A.48 off-mode behaviour
        return {
            'spread_pips': 25.0,
            'commission_pips': 0.0,
            'slippage_pips': 0.0,
            'max_spread_pips': 0.0,
            'force_close_hour':              -1,
            'no_trades_window_start_hour':   -1,
            'no_trades_window_end_hour':     -1,
            'allow_overnight_trades':        True,  # default: overnight allowed
            # WHY (May 2026): Deprecated alias. Kept in the output dict
            #      so legacy consumers reading _resolved['hard_close_hour']
            #      don't KeyError before they migrate to the split fields.
            #      Mirrors force_close_hour. Remove once all consumers
            #      use the new keys.
            'hard_close_hour':               -1,
            'variable_spread': False,
            'session_spread_multipliers': None,
            'min_hold_minutes': 0,
            'cooldown_candles': 0,
            'swap_long_pips_per_night': 0.0,
            'swap_short_pips_per_night': 0.0,
            'firm_resolved': False,
            'symbol': _sym,
        }

    _bt = _read_bt_config()
    out = {
        'spread_pips':       float(_bt.get('spread', 25.0) or 25.0),
        'commission_pips':   float(_bt.get('commission', 0.0) or 0.0),
        'slippage_pips':     float(_bt.get('slippage_pips', 0.0) or 0.0),
        'max_spread_pips':   float(_bt.get('max_spread_pips', 0) or 0),
        # WHY (May 2026): hard_close_hour was split into two concepts.
        #      Map the legacy backtest_config key into force_close_hour
        #      for backward compatibility. The no-trades window is NOT
        #      derivable from backtest_config — leave it disabled when
        #      no firm JSON applies.
        # CHANGED: May 2026 — split hard_close into force_close + no_trades_window
        'force_close_hour':              int(float(_bt.get('hard_close_hour', -1) or -1)),
        'no_trades_window_start_hour':   -1,
        'no_trades_window_end_hour':     -1,
        'allow_overnight_trades':        True,
        # Deprecated alias — see notes above. Mirrors force_close_hour
        # so legacy readers (refiner/validator panels) keep working
        # until they migrate to the split fields.
        'hard_close_hour':               int(float(_bt.get('hard_close_hour', -1) or -1)),
        'variable_spread':   _bt.get('variable_spread', '0') == '1',
        'session_spread_multipliers': None,
        'min_hold_minutes':  0,
        'cooldown_candles':  int(float(_bt.get('cooldown_candles', 0) or 0)),
        'swap_long_pips_per_night': 0.0,
        'swap_short_pips_per_night': 0.0,
        'firm_resolved':     False,
        'symbol':            _sym,
    }

    _firm = _read_firm_json(firm_name)
    if _firm is None:
        return out

    out['firm_resolved'] = True
    _sym_spec = _firm.get('instrument_specs', {}).get(_sym, {})

    # Per-instrument firm overrides
    _v = _sym_spec.get('typical_spread')
    if _v is not None:
        out['spread_pips'] = float(_v)
    _v = _sym_spec.get('spread_session_multipliers')
    if isinstance(_v, dict):
        out['session_spread_multipliers'] = _v
    _v = _sym_spec.get('max_spread_pips_filter')
    if _v is not None:
        out['max_spread_pips'] = float(_v)
    _v = _sym_spec.get('swap_long_pips_per_night')
    if _v is not None:
        out['swap_long_pips_per_night'] = float(_v)
    _v = _sym_spec.get('swap_short_pips_per_night')
    if _v is not None:
        out['swap_short_pips_per_night'] = float(_v)

    # WHY (May 2026): hard_close_hour_gmt was a single field conflating
    #      "force-close at X" with "block entries from X to reopen".
    #      Get Leveraged allows overnight holds (no force-close) but DOES
    #      block trading 23:00–24:00. The new schema separates these.
    #      Legacy fallback: if the firm JSON still has hard_close_hour_gmt
    #      (other firms not yet migrated), treat it as force_close_hour
    #      for backward compatibility.
    # CHANGED: May 2026 — split hard_close into force_close + no_trades_window

    # Force-close hour (replaces hard_close_hour_gmt semantically)
    _v = _firm.get('force_close_hour_gmt')
    if _v is not None:
        out['force_close_hour'] = int(_v)
    else:
        # Legacy fallback for firms still using hard_close_hour_gmt
        _legacy = _firm.get('hard_close_hour_gmt')
        if _legacy is not None:
            out['force_close_hour'] = int(_legacy)
    # Mirror to deprecated alias so legacy consumers don't KeyError
    # mid-migration.
    out['hard_close_hour'] = out['force_close_hour']

    # No-trades window
    _v = _firm.get('no_trades_window_start_hour_gmt')
    if _v is not None:
        out['no_trades_window_start_hour'] = int(_v)
    _v = _firm.get('no_trades_window_end_hour_gmt')
    if _v is not None:
        out['no_trades_window_end_hour'] = int(_v)

    # Documentation flag
    _v = _firm.get('allow_overnight_trades')
    if _v is not None:
        out['allow_overnight_trades'] = bool(_v)

    # Min-hold from firm challenge restrictions
    try:
        _chs = _firm.get('challenges', [])
        if _chs:
            _sec = int(_chs[0].get('restrictions', {}).get(
                'min_trade_duration_seconds', 0) or 0)
            if _sec > 0:
                out['min_hold_minutes'] = max(1, _sec // 60)
    except Exception:
        pass

    return out

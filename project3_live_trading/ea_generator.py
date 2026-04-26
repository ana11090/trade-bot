"""
EA Generator — converts a validated strategy into MetaTrader 5 (.mq5) or Tradovate (Python) bot.

Input: strategy dict (rules + exit strategy + filters + prop firm settings)
Output: complete .mq5 file OR Python bot folder ready to deploy
"""

import os
import json
import random
import re
from datetime import datetime

from project3_live_trading.indicator_mapper import (
    get_mql_code, get_all_handles_for_rules, get_custom_indicator_list, parse_feature_name,
)

_HERE = os.path.dirname(os.path.abspath(__file__))

OPERATOR_MAP_MQL = {'>': '>', '>=': '>=', '<': '<', '<=': '<=', '==': '==', '!=': '!='}
OPERATOR_MAP_PY  = OPERATOR_MAP_MQL


def _mql_condition_expr(val_expr, op, param_expr):
    """Build an MQL5 condition expression, using tolerance for == and !=.

    WHY: Python backtester uses abs(a-b) < 0.001 for == comparisons.
         MQL5 strict == on doubles almost never matches. Use the same
         tolerance so EA behavior matches Python.
    CHANGED: April 2026 — float equality tolerance for EA/Python parity
    """
    if op == '==':
        return f'MathAbs({val_expr} - {param_expr}) < 0.001'
    elif op == '!=':
        return f'MathAbs({val_expr} - {param_expr}) >= 0.001'
    mql_op = OPERATOR_MAP_MQL.get(op, '>')
    return f'{val_expr} {mql_op} {param_expr}'


def generate_ea(
    strategy,
    platform='mt5',
    prop_firm=None,
    stage='evaluation',
    entry_timeframe='H1',
    symbol='XAUUSD',
    magic_number=None,
    risk_per_trade_pct=1.0,
    # WHY: Default 5 was arbitrary. Should match backtest. 0 = unlimited.
    # CHANGED: April 2026 — match backtest default
    max_trades_per_day=0,
    session_filter=None,
    day_filter=None,
    min_hold_minutes=0,
    cooldown_minutes=0,
    news_filter_minutes=0,
    max_spread_pips=5.0,
    trailing_stop=None,
    output_path=None,
    direction=None,  # NEW: 'BUY' or 'SELL' — if None, read from strategy dict
    leverage=0,
):
    """
    Generate complete EA code for MT5 or Tradovate.

    Returns the code as a string. Also saves to output_path if provided.
    """
    # WHY: Reproducible magic numbers from strategy id. Old code used
    #      random.randint, so re-running generate_ea on the same strategy
    #      produced different magic numbers — confusing if user has multiple
    #      copies. Now: derive deterministically from strategy name+symbol
    #      hash. User can still override via magic_number param.
    # CHANGED: April 2026 — deterministic magic number
    if magic_number is None:
        seed_str = f"{symbol}_{strategy.get('rule_combo', 'default')}_{strategy.get('exit_name', 'default')}"
        # Simple hash → 5-digit magic number, stable across runs
        h = 0
        for c in seed_str:
            h = (h * 31 + ord(c)) & 0xFFFFFFFF
        magic_number = 10000 + (h % 90000)

    rules     = strategy.get('rules', [])
    win_rules = [r for r in rules if r.get('prediction') == 'WIN']
    exit_name = strategy.get('exit_name', strategy.get('exit_strategy', 'FixedSLTP'))

    # ── Normalize exit_params key name ───────────────────────────────────
    # WHY: Some sources (saved rules, optimizer) use 'exit_params', others
    #      use 'exit_strategy_params'. Read from both, prioritize the one
    #      that exists.
    # CHANGED: April 2026 — fix inconsistent key names between save sources
    exit_params = (
        strategy.get('exit_strategy_params') or
        strategy.get('exit_params') or
        {'sl_pips': 150, 'tp_pips': 300}
    )

    # WHY: Strategy direction was silently ignored — every generated EA
    #      emitted trade.Buy() regardless of whether the strategy was BUY
    #      or SELL. SELL strategies ran as their exact opposite on the
    #      live account. Read from the explicit parameter first, then
    #      fall back to strategy dict, then default to BUY with a loud
    #      print so the user sees it.
    # CHANGED: April 2026 — fix hardcoded BUY emission (audit bug #9)
    _dir = (direction or strategy.get('direction') or 'BUY').upper()
    if _dir not in ('BUY', 'SELL'):
        print(f"[EA GEN] WARNING: unknown direction {_dir!r}, defaulting to BUY")
        _dir = 'BUY'
    if not direction and not strategy.get('direction'):
        print(f"[EA GEN] WARNING: strategy has no 'direction' field — defaulting to BUY. "
              f"If this is a SELL strategy, the generated EA will trade the wrong side!")
    print(f"[EA GEN] Direction: {_dir}")

    validation = strategy.get('validation', {})
    grade       = validation.get('grade', 'N/A')
    score       = validation.get('score', 0)
    base_stats  = strategy.get('stats', {})

    dd_daily_pct  = 5.0
    dd_total_pct  = 10.0
    dd_safety_pct = 80.0
    consistency   = 0.0

    if prop_firm:
        dd_daily_pct  = prop_firm.get('daily_dd_pct',    5.0)
        dd_total_pct  = prop_firm.get('total_dd_pct',   10.0)
        dd_safety_pct = prop_firm.get('safety_pct',     80.0)
        consistency   = prop_firm.get('consistency_pct', 0.0)

    # WHY: trading_rules drive what MQL5 code is generated.
    #      drawdown_mechanics drive HOW DD is tracked (different per firm).
    #      No hardcoding — if a field doesn't exist in JSON, that code isn't generated.
    trading_rules = prop_firm.get('trading_rules', []) if prop_firm else []
    dd_mechanics  = prop_firm.get('drawdown_mechanics', {}) if prop_firm else {}
    account_size  = prop_firm.get('account_size', 10000) if prop_firm else 10000

    # WHY: Read min hold from firm restrictions if not explicitly set.
    # CHANGED: April 2026 — min hold from firm
    if min_hold_minutes == 0 and prop_firm:
        try:
            _restrictions = prop_firm.get('restrictions', {})
            if not _restrictions:
                _firm_d = prop_firm.get('firm_data', {})
                _ch = _firm_d.get('challenges', [{}])[0]
                _restrictions = _ch.get('restrictions', {})
            _min_sec = int(_restrictions.get('min_trade_duration_seconds', 0))
            if _min_sec > 0:
                min_hold_minutes = max(1, _min_sec // 60)
                print(f"[EA GEN] Min hold from firm: {_min_sec}s = {min_hold_minutes}min")
        except Exception:
            pass
    restrictions  = prop_firm.get('restrictions', {}) if prop_firm else {}
    challenge     = prop_firm.get('challenge', {}) if prop_firm else {}

    # Read regime filter conditions from strategy
    _regime_conds = strategy.get('regime_filter_conditions', [])

    # Leverage calculation — must happen BEFORE _generate_mt5 call
    # WHY: Prefer explicitly passed leverage (from rule).
    #      Fall back to firm lookup only if not provided.
    # CHANGED: April 2026 — leverage from rule first
    _ea_leverage = leverage if leverage and int(leverage) > 0 else 0
    _ea_contract = 100.0
    _ea_inst = 'metals'
    _max_risk_pct = None
    _old_risk = None
    _max_lots = 0
    try:
        from shared.prop_firm_engine import get_leverage_for_symbol, get_instrument_type
        # WHY: prop_firm is a wrapper dict. The actual firm JSON with
        #      leverage_by_instrument is inside 'firm_data' key.
        # CHANGED: April 2026 — pass inner firm_data for correct leverage
        _firm_json = (prop_firm or {}).get('firm_data', prop_firm or {})
        if _ea_leverage == 0:
            _ea_leverage = get_leverage_for_symbol(_firm_json, symbol)
        _ea_inst = get_instrument_type(symbol)
        _ea_contract = 100.0 if _ea_inst == 'metals' else (1.0 if _ea_inst == 'indices' else 100000.0)
    except Exception:
        pass

    sl_pips = exit_params.get('sl_pips', 150)

    # WHY: The Python backtester already uses pip_size=0.01 for XAUUSD, so
    #      sl_pips values from the backtest are already in the correct scale.
    #      The old 10x metals multiplier (audit bug #10) caused a mismatch:
    #      backtest SL=150 pips (1.50 price) vs EA SL=1500 pips (15.0 price).
    #      GetPipSize() in the EA returns 0.01 for 2-digit metals, so the
    #      pip values must pass through UNSCALED to match the backtest.
    # CHANGED: April 2026 — removed metals 10x scaling (was wrong)

    if _ea_leverage > 0 and account_size > 0 and sl_pips > 0:
        _approx_prices = {'XAUUSD': 3300, 'XAGUSD': 30, 'EURUSD': 1.08, 'GBPUSD': 1.26,
                          'US30': 40000, 'NAS100': 18000, 'DAX': 18000}
        _price = _approx_prices.get(symbol.upper(), 3300 if _ea_inst == 'metals' else
                                    40000 if _ea_inst == 'indices' else 1.1)
        # WHY: Read pip_value from strategy data, not hardcoded.
        # CHANGED: April 2026 — strategy-driven pip_value
        _pip_value = float(strategy.get('pip_value_per_lot', 1.0))
        _margin_per_lot = (_ea_contract * _price) / _ea_leverage
        _max_lots = (account_size * 0.90) / _margin_per_lot
        _max_risk_pct = (_max_lots * sl_pips * _pip_value) / account_size * 100.0
        if risk_per_trade_pct > _max_risk_pct:
            _old_risk = risk_per_trade_pct
            risk_per_trade_pct = round(max(0.1, _max_risk_pct), 1)
            print(f"[EA GEN] ⚠ Risk capped: {_old_risk}% → {risk_per_trade_pct}% "
                  f"(leverage 1:{_ea_leverage}, max lots {_max_lots:.2f}, "
                  f"margin/lot ${_margin_per_lot:,.0f})")
        else:
            print(f"[EA GEN] Risk {risk_per_trade_pct}% OK for leverage 1:{_ea_leverage} "
                  f"(max {_max_risk_pct:.1f}%)")

    if platform == 'mt5':
        code = _generate_mt5(
            win_rules=win_rules,
            exit_name=exit_name,
            exit_params=exit_params,
            symbol=symbol,
            magic_number=magic_number,
            risk_per_trade_pct=risk_per_trade_pct,
            max_trades_per_day=max_trades_per_day,
            session_filter=session_filter or [],
            day_filter=day_filter or [1, 2, 3, 4, 5],
            min_hold_minutes=min_hold_minutes,
            cooldown_minutes=cooldown_minutes,
            news_filter_minutes=news_filter_minutes,
            max_spread_pips=max_spread_pips,
            dd_daily_pct=dd_daily_pct,
            dd_total_pct=dd_total_pct,
            dd_safety_pct=dd_safety_pct,
            consistency_pct=consistency,
            grade=grade,
            score=score,
            base_stats=base_stats,
            prop_firm_name=prop_firm.get('name', 'None') if prop_firm else 'None',
            stage=stage,
            entry_timeframe=entry_timeframe,
            trading_rules=trading_rules,
            dd_mechanics=dd_mechanics,
            account_size=account_size,
            restrictions=restrictions,
            challenge=challenge,
            direction=_dir,  # NEW: pass strategy direction (using _dir from FIX 1A)
            regime_conditions=_regime_conds,
            leverage=_ea_leverage,
        )
    else:
        code = _generate_tradovate(
            win_rules=win_rules,
            exit_name=exit_name,
            exit_params=exit_params,
            symbol=symbol,
            magic_number=magic_number,
            risk_per_trade_pct=risk_per_trade_pct,
            max_trades_per_day=max_trades_per_day,
            session_filter=session_filter or [],
            day_filter=day_filter or [1, 2, 3, 4, 5],
            cooldown_minutes=cooldown_minutes,
            news_filter_minutes=news_filter_minutes,
            max_spread_pips=max_spread_pips,
            dd_daily_pct=dd_daily_pct,
            dd_total_pct=dd_total_pct,
            dd_safety_pct=dd_safety_pct,
            grade=grade,
            score=score,
            base_stats=base_stats,
            direction=direction,  # NEW: pass strategy direction
            entry_timeframe=entry_timeframe,  # NEW: needed for TF subscription fix
        )

    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(code)
        print(f"[EA GEN] Saved to {output_path}")

        # WHY: Verification report used variables from _generate_mt5 scope
        #      (exit_class, mql_period, sl_pips, etc.) which don't exist here.
        #      Compute them from generate_ea's own variables.
        # CHANGED: April 2026 — fix scope error
        _exit_class_map = {
            'Fixed SL/TP': 'FixedSLTP', 'FixedSLTP': 'FixedSLTP',
            'Trailing Stop': 'TrailingStop', 'TrailingStop': 'TrailingStop',
            'ATR-Based': 'ATRBased', 'ATRBased': 'ATRBased',
            'ATR Only': 'ATROnly', 'ATROnly': 'ATROnly',
            'ATR + Trailing': 'ATRTrailing', 'ATRTrailing': 'ATRTrailing',
            'Time-Based': 'TimeBased', 'TimeBased': 'TimeBased',
            'Indicator Exit': 'IndicatorExit', 'IndicatorExit': 'IndicatorExit',
            'Hybrid Exit': 'HybridExit', 'HybridExit': 'HybridExit',
        }
        exit_class = _exit_class_map.get(exit_name, exit_name)
        _mql_periods = {
            'M1': 'PERIOD_M1', 'M5': 'PERIOD_M5', 'M15': 'PERIOD_M15',
            'H1': 'PERIOD_H1', 'H4': 'PERIOD_H4', 'D1': 'PERIOD_D1',
        }
        mql_period = _mql_periods.get(entry_timeframe, 'PERIOD_H1')
        sl_pips = exit_params.get('sl_pips', 150)
        tp_pips = exit_params.get('tp_pips', 0 if exit_class in ('TimeBased', 'IndicatorExit') else 300)

        # WHY: Pip values from backtest are already correct for all instruments.
        #      Old 10x metals scaling removed — it caused SL/TP mismatch.
        # CHANGED: April 2026 — removed metals 10x scaling (was wrong)
        try:
            from shared.prop_firm_engine import get_instrument_type
            _inst_type = get_instrument_type(symbol)
        except Exception:
            _inst_type = 'forex'

        max_candles = exit_params.get('max_candles', 12)

        trail_activation_pips = exit_params.get('trail_activation_pips', exit_params.get('activation_pips', 50))
        trail_distance_pips = exit_params.get('trail_distance_pips', exit_params.get('trail_pips', 100))

        # WHY: Trailing params from backtest are already correct for all instruments.
        #      Old 10x metals scaling removed — it caused trail param mismatch.
        # CHANGED: April 2026 — removed metals 10x scaling (was wrong)

        sl_atr_mult = exit_params.get('sl_atr_mult', 2.0)
        tp_atr_mult = exit_params.get('tp_atr_mult', 3.0)
        breakeven_pips = exit_params.get('breakeven_pips', 50)

        # WHY: Breakeven pips from backtest are already correct for all instruments.
        #      Old 10x metals scaling removed — it caused breakeven mismatch.
        # CHANGED: April 2026 — removed metals 10x scaling (was wrong)

        prop_firm_name = prop_firm.get('name', 'None') if prop_firm else 'None'

        # ── Save verification report as separate .txt ──
        # WHY: Standalone file for easy review alongside the .mq5
        # CHANGED: April 2026
        try:
            _rp = output_path.rsplit('.', 1)[0] + '_verification.txt'
            _rl = []
            _rl.append("=" * 70)
            _rl.append("EA STRATEGY VERIFICATION REPORT")
            _rl.append("=" * 70)
            _rl.append("")
            _rl.append(f"{'Parameter':<30} {'Backtest':<20} {'EA':<20}")
            _rl.append("-" * 70)
            _rl.append(f"{'Direction':<30} {direction or '?':<20} {_dir:<20}")
            _rl.append(f"{'Entry TF':<30} {entry_timeframe:<20} {mql_period:<20}")
            _rl.append(f"{'Exit Strategy':<30} {exit_name:<20} {exit_class:<20}")
            _rl.append(f"{'SL (pips)':<30} {sl_pips:<20} {sl_pips:<20}")
            _rl.append(f"{'TP (pips)':<30} {tp_pips:<20} {tp_pips:<20}")
            _rl.append(f"{'Risk %':<30} {risk_per_trade_pct:<20} {risk_per_trade_pct:<20}")
            _rl.append(f"{'Max trades/day':<30} {max_trades_per_day:<20} {max_trades_per_day:<20}")
            _rl.append(f"{'Min hold (min)':<30} {min_hold_minutes:<20} {min_hold_minutes:<20}")
            _rl.append(f"{'Cooldown (min)':<30} {cooldown_minutes:<20} {cooldown_minutes:<20}")
            _rl.append(f"{'Symbol':<30} {symbol:<20} {symbol:<20}")
            if exit_class == 'TrailingStop':
                _act = trail_activation_pips
                _rl.append(f"{'Trail activation':<30} {_act:<20} {_act:<20}")
                _rl.append(f"{'Trail distance':<30} {trail_distance_pips:<20} {trail_distance_pips:<20}")
            elif exit_class in ('ATRBased', 'ATROnly', 'ATRTrailing'):
                _rl.append(f"{'SL ATR mult':<30} {sl_atr_mult:<20} {sl_atr_mult:<20}")
                _rl.append(f"{'TP ATR mult':<30} {tp_atr_mult:<20} {tp_atr_mult:<20}")
            elif exit_class == 'TimeBased':
                _rl.append(f"{'Max candles':<30} {max_candles:<20} {max_candles:<20}")
            elif exit_class == 'HybridExit':
                _rl.append(f"{'Breakeven at':<30} {breakeven_pips:<20} {breakeven_pips:<20}")
                _rl.append(f"{'Max candles':<30} {max_candles:<20} {max_candles:<20}")
            _rl.append("")
            _rl.append("ENTRY RULES")
            _rl.append("-" * 70)
            _rl.append(f"{len(win_rules)} rules with OR logic (any rule triggers entry)")
            _rl.append("")
            for _ri, _rule in enumerate(win_rules, 1):
                _cs = _rule.get('conditions', [])
                _rl.append(f"Rule {_ri} ({len(_cs)} conditions):")
                for _ci, _c in enumerate(_cs, 1):
                    _f = _c.get('feature', '?')
                    _o = _c.get('operator', '>')
                    _v = _c.get('value', 0)
                    _rl.append(f"  {_ci}. {_f} {_o} {_v:.6f}")
                _rl.append("")

            # Add regime filter info
            if _regime_conds:
                _rl.append("REGIME FILTER (pre-entry gate)")
                _rl.append("-" * 70)
                _rl.append(f"{len(_regime_conds)} conditions (must ALL pass before checking entry rules)")
                _rl.append("")
                for _ri, _rc in enumerate(_regime_conds, 1):
                    _f = _rc.get('feature', '?')
                    _o = _rc.get('direction', _rc.get('operator', '>'))
                    _v = _rc.get('threshold', _rc.get('value', 0))
                    _rl.append(f"  {_ri}. {_f} {_o} {_v:.6f}")
                _rl.append("")

            _rl.append("EXIT: " + exit_class)
            _rl.append(f"  Full params: {exit_params}")
            _rl.append("")
            _rl.append(f"Filters: max_trades/day={max_trades_per_day}, min_hold={min_hold_minutes}min")
            _rl.append(f"Prop firm: {prop_firm_name} ({stage}), DD {dd_daily_pct}%/{dd_total_pct}%")
            _rl.append(f"Validation: Grade {grade} ({score}/100)")
            _rl.append("=" * 70)
            with open(_rp, 'w', encoding='utf-8') as _rf:
                _rf.write('\n'.join(_rl))
            print(f"[EA GEN] Verification report: {_rp}")
        except Exception as _e:
            print(f"[EA GEN] Could not save verification report: {_e}")

        # WHY: The emitted EA reads news_calendar.csv via FileOpen with a
        #      relative path, which in MT5 means MQL5/Files/ — a different
        #      folder from MQL5/Experts/ where the .mq5 file lives.
        #      Users rarely know to copy the CSV manually, so the news
        #      filter silently returned false and trades fired during
        #      NFP/CPI/FOMC. Now we attempt to locate MQL5/Files/ from
        #      the output_path and copy the CSV there.
        # CHANGED: April 2026 — fix missing news_calendar.csv (audit bug #7)
        try:
            import shutil
            _news_src = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'outputs', 'news_calendar.csv',
            )
            if os.path.exists(_news_src):
                # If output_path is .../MQL5/Experts/foo.mq5, MQL5/Files is at
                # .../MQL5/Files. Walk up until we find an "MQL5" directory.
                _out_dir = os.path.dirname(os.path.abspath(output_path))
                _mql5_root = None
                _cur = _out_dir
                for _ in range(6):
                    if os.path.basename(_cur) == 'MQL5':
                        _mql5_root = _cur
                        break
                    _parent = os.path.dirname(_cur)
                    if _parent == _cur:
                        break
                    _cur = _parent

                if _mql5_root:
                    _news_dst = os.path.join(_mql5_root, 'Files', 'news_calendar.csv')
                    os.makedirs(os.path.dirname(_news_dst), exist_ok=True)
                    shutil.copy2(_news_src, _news_dst)
                    print(f"[EA GEN] Copied news calendar to {_news_dst}")
                else:
                    # Fallback: copy next to the EA so the user can move it
                    _news_dst = os.path.join(_out_dir, 'news_calendar.csv')
                    shutil.copy2(_news_src, _news_dst)
                    print(f"[EA GEN] Could not locate MQL5/Files — copied calendar next to EA: {_news_dst}")
                    print(f"[EA GEN] ACTION REQUIRED: move news_calendar.csv into your MQL5/Files folder manually.")
            else:
                print(f"[EA GEN] WARNING: {_news_src} not found — news filter will be disabled. "
                      f"Run download_news_calendar() first to generate it.")
        except Exception as _ex:
            print(f"[EA GEN] WARNING: Could not copy news_calendar.csv: {_ex}. "
                  f"News filter will be disabled until you place the file in MQL5/Files manually.")

    return code


# ─────────────────────────────────────────────────────────────────────────────
# MT5 MQL5 Generator
# ─────────────────────────────────────────────────────────────────────────────

def _generate_mt5(win_rules, exit_name, exit_params, symbol, magic_number,
                  risk_per_trade_pct, max_trades_per_day, session_filter,
                  day_filter, min_hold_minutes, cooldown_minutes,
                  news_filter_minutes, max_spread_pips,
                  dd_daily_pct, dd_total_pct, dd_safety_pct, consistency_pct,
                  grade, score, base_stats, prop_firm_name,
                  stage='evaluation', trading_rules=None,
                  dd_mechanics=None, account_size=10000,
                  restrictions=None, challenge=None,
                  entry_timeframe='H1',
                  direction='BUY',
                  regime_conditions=None,
                  leverage=0):
    """Generate MQL5 EA code. direction must be 'BUY' or 'SELL'."""
    if direction not in ('BUY', 'SELL'):
        raise ValueError(f"_generate_mt5: direction must be BUY or SELL, got {direction!r}")
    is_buy = (direction == 'BUY')

    handles = get_all_handles_for_rules(win_rules, platform='mt5')
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    sl_pips = exit_params.get('sl_pips', 150)
    tp_pips = exit_params.get('tp_pips', 300)

    # WHY: Pip values from backtest are already correct for all instruments.
    #      Old 10x metals scaling removed — it caused SL/TP mismatch.
    #      Python backtester uses pip_size=0.01 for XAUUSD, and the EA's
    #      GetPipSize() also returns 0.01 for 2-digit metals.
    # CHANGED: April 2026 — removed metals 10x scaling (was wrong)

    # ── Trailing stop parameters ──────────────────────────────────────
    # WHY: TrailingStop has two thresholds:
    #      1. activation_pips: profit needed before trailing starts
    #      2. trail_distance_pips: how far behind price the SL stays
    #      The old code used trail_pips for BOTH, which is wrong.
    # CHANGED: April 2026 — separate activation and distance
    trail_activation_pips = exit_params.get('activation_pips', 50)
    trail_distance_pips = exit_params.get('trail_distance_pips', exit_params.get('trail_pips', 100))

    # WHY: Trailing params from backtest are already correct for all instruments.
    #      Old 10x metals scaling removed — it caused trail param mismatch.
    # CHANGED: April 2026 — removed metals 10x scaling (was wrong)

    # WHY: Direction-dependent code fragments. Old code hardcoded BUY logic
    #      everywhere — for SELL strategies, the EA placed BUY orders with
    #      inverted SL/TP and lost money on every trade. Now we build
    #      direction-aware fragments once and substitute them throughout.
    # CHANGED: April 2026 — direction-aware code generation
    if is_buy:
        # For BUY: SL/TP relative to BID (the monitoring price for longs).
        # WHY: Python backtester enters at bar OPEN (≈BID) and checks SL
        #      against bar LOW (≈lowest BID). Using ASK - sl would shrink
        #      the effective SL distance by the spread, causing far more
        #      stop-outs than the backtester predicts.
        _entry_call         = "trade.Buy(lots, _Symbol, 0, slPrice, tpPrice, \"EA_Entry\")"
        _sl_price_expr      = "bid - sl"
        _tp_price_expr      = "bid + tp"
        _entry_price_src    = "ask"
        _profit_pips_expr   = "(_bid - _openP) / GetPipSize()"      # for trailing/hybrid
        _be_new_sl_expr     = "_openP + _Point"                      # breakeven SL
        _trail_new_sl_expr  = "_bid - TrailDistance * GetPipSize()"
        # WHY: SELL has "|| _curSL == 0" fallback for positions opened
        #      without an SL. BUY technically works without it (because
        #      _newSL > 0 + _Point is true anyway), but adding the
        #      fallback makes the two branches symmetric and handles the
        #      edge case more explicitly.
        # CHANGED: April 2026 — symmetric trail fallback (audit HIGH)
        _trail_sl_compare   = "_newSL > _curSL + _Point || _curSL == 0"
        _direction_label    = "BUY"
        _trade_dir_const    = "ORDER_TYPE_BUY"
    else:
        # For SELL: SL/TP relative to ASK (the monitoring price for shorts).
        # WHY: Python backtester checks SL against bar HIGH (≈highest ASK).
        #      Using BID + sl would shrink effective SL distance by the spread.
        _entry_call         = "trade.Sell(lots, _Symbol, 0, slPrice, tpPrice, \"EA_Entry\")"
        _sl_price_expr      = "ask + sl"   # SL above current ask
        _tp_price_expr      = "ask - tp"   # TP below current ask
        _entry_price_src    = "bid"
        _profit_pips_expr   = "(_openP - _ask) / GetPipSize()"      # SELL profits when price drops
        _be_new_sl_expr     = "_openP - _Point"                      # breakeven SL ABOVE entry for sells (wait, see below)
        _trail_new_sl_expr  = "_ask + TrailDistance * GetPipSize()"     # SL above ask for sell
        _trail_sl_compare   = "_newSL < _curSL - _Point || _curSL == 0"
        _direction_label    = "SELL"
        _trade_dir_const    = "ORDER_TYPE_SELL"

    # NOTE: For a SELL position, "breakeven" means moving the SL DOWN to entry
    #       price (since SL was originally ABOVE entry). The SL value moves
    #       toward the entry, which is BELOW the current SL. So new SL = entry.
    #       (Same as BUY: new SL = entry, but for sells _openP < _curSL.)
    # Override the breakeven SL expressions to be just _openP (entry) — same
    # for both directions, since "move SL to entry" is direction-agnostic.
    _be_new_sl_expr = "_openP"

    # ── Determine exit strategy type ──────────────────────────────────────
    # WHY: Different exit strategies need different MQL5 code.
    #      FixedSLTP: set SL/TP at entry, done.
    #      ATRBased: read ATR at entry, compute SL/TP dynamically.
    #      TimeBased: fixed SL + close after N candles.
    #      HybridExit: trailing + breakeven + time limit.
    # CHANGED: April 2026 — support all backtester exit strategies
    exit_class = exit_name  # e.g., 'FixedSLTP', 'ATRBased', 'TimeBased', etc.
    # Normalize common names
    exit_class_map = {
        'Fixed SL/TP': 'FixedSLTP', 'FixedSLTP': 'FixedSLTP',
        'Trailing Stop': 'TrailingStop', 'TrailingStop': 'TrailingStop',
        'ATR-Based': 'ATRBased', 'ATRBased': 'ATRBased',
        'ATR Only': 'ATROnly', 'ATROnly': 'ATROnly',
        'ATR + Trailing': 'ATRTrailing', 'ATRTrailing': 'ATRTrailing',
        'Time-Based': 'TimeBased', 'TimeBased': 'TimeBased',
        'Indicator Exit': 'IndicatorExit', 'IndicatorExit': 'IndicatorExit',
        'Hybrid': 'HybridExit', 'HybridExit': 'HybridExit',
    }
    exit_class = exit_class_map.get(exit_class, 'FixedSLTP')

    # WHY: TimeBased and IndicatorExit don't use TP — the backtest exits
    #      by time or indicator signal, not by TP. Setting TP=300 creates
    #      a phantom exit that closes trades early — different from backtest.
    # CHANGED: April 2026 — no phantom TP for time/indicator exits
    if exit_class in ('TimeBased', 'IndicatorExit'):
        tp_pips = 0

    # WHY: TimeBased, IndicatorExit, and FixedSLTP exits don't use trailing.
    #      Non-zero defaults activate ManageTrailingStop which closes trades
    #      early — untested behavior not in the backtest.
    # CHANGED: April 2026 — disable trailing for non-trailing exits
    if exit_class in ('TimeBased', 'IndicatorExit', 'FixedSLTP', 'ATROnly'):
        trail_activation_pips = 0
        trail_distance_pips = 0

    # Exits that legitimately use trailing stop — for all others, Trail* must be
    # constants (not inputs) so the user cannot accidentally enable trailing in the
    # MT5 tester and corrupt the exit logic defined by the exit strategy.
    _has_trailing = exit_class in ('TrailingStop', 'ATRTrailing', 'ATRBased', 'HybridExit')

    # ATR params
    sl_atr_mult = exit_params.get('sl_atr_mult', 1.5)
    tp_atr_mult = exit_params.get('tp_atr_mult', 3.0)
    atr_column = exit_params.get('atr_column', 'H1_atr_14')

    # Time params
    max_candles = exit_params.get('max_candles', 12)

    # Indicator exit params
    exit_indicator = exit_params.get('exit_indicator', 'H1_rsi_14')
    exit_threshold = exit_params.get('exit_threshold', 70)
    exit_direction = exit_params.get('exit_direction', 'above' if is_buy else 'below')
    _ind_compare = '>=' if exit_direction == 'above' else '<='

    # Hybrid params
    breakeven_pips = exit_params.get('breakeven_activation_pips', 50)

    # WHY: EA checks for new bar on the entry timeframe, not always H1.
    _mql_periods = {
        'M1': 'PERIOD_M1', 'M5': 'PERIOD_M5', 'M15': 'PERIOD_M15',
        'H1': 'PERIOD_H1', 'H4': 'PERIOD_H4', 'D1': 'PERIOD_D1',
    }
    mql_period = _mql_periods.get(entry_timeframe, 'PERIOD_H1')

    # NOTE: TSI is now implemented inline for MT5 (April 2026 — double-EMA
    #       computation in indicator_mapper._mql5_sub_expr). The previous
    #       warning block was removed as SMART_tsi_bullish/tsi_strong now
    #       work in live trading.

    # WHY: Old code AND'd ALL conditions from ALL rules. For multi-rule
    #      strategies the EA required all 42 conditions true simultaneously.
    #      The backtest uses OR between rules. Fix: per-rule blocks.
    # CHANGED: April 2026
    condition_inputs = []
    condition_checks = []

    # Declare all input parameters first
    for ri, rule in enumerate(win_rules, 1):
        for ci, cond in enumerate(rule.get('conditions', []), 1):
            feat = cond['feature']
            op   = cond.get('operator', '>')
            val  = cond.get('value', 0)
            safe_name = re.sub(r'[^a-zA-Z0-9]', '_', feat)
            param_name = f"Rule{ri}_Cond{ci}_{safe_name[:20]}"
            condition_inputs.append(f'input double {param_name} = {val:.6f}; // Rule {ri}: {feat} {op} threshold')

    # Build check blocks
    if len(win_rules) == 1:
        # Single rule — simple AND
        rule = win_rules[0]
        for ci, cond in enumerate(rule.get('conditions', []), 1):
            feat = cond['feature']
            op   = cond.get('operator', '>')
            safe_name = re.sub(r'[^a-zA-Z0-9]', '_', feat)
            param_name = f"Rule1_Cond{ci}_{safe_name[:20]}"
            mql = get_mql_code(feat, 'mt5')
            var_n = mql['var_name']
            cond_expr = _mql_condition_expr(f'val_{var_n}', op, param_name)
            condition_checks.append(
                f'   // Condition {ci}: {feat} {op} {cond.get("value", 0):.4f}\n'
                f'   {mql["read_code"]}\n'
                f'   if(!({cond_expr})) entrySignal = false;\n'
            )
    else:
        # Multiple rules — OR between rules, AND within each
        condition_checks.append(
            f'   // {len(win_rules)} rules combined with OR logic\n'
            f'   entrySignal = false;\n'
        )
        for ri, rule in enumerate(win_rules, 1):
            conds = rule.get('conditions', [])
            if not conds:
                continue
            condition_checks.append(
                f'   if(!entrySignal) {{ // Rule {ri} ({len(conds)} conditions)\n'
                f'      bool rule{ri} = true;\n'
            )
            for ci, cond in enumerate(conds, 1):
                feat = cond['feature']
                op   = cond.get('operator', '>')
                safe_name = re.sub(r'[^a-zA-Z0-9]', '_', feat)
                param_name = f"Rule{ri}_Cond{ci}_{safe_name[:20]}"
                mql = get_mql_code(feat, 'mt5')
                var_n = mql['var_name']
                cond_expr = _mql_condition_expr(f'val_{var_n}', op, param_name)
                condition_checks.append(
                    f'      {mql["read_code"]}\n'
                    f'      if(!({cond_expr})) rule{ri} = false;\n'
                )
            condition_checks.append(
                f'      if(rule{ri}) entrySignal = true;\n'
                f'   }}\n'
            )

    # Handle variable declarations
    handle_vars  = '\n'.join(h['handle_var'] for h in handles if h.get('handle_var'))
    handle_inits = '\n   '.join(h['handle_init'] for h in handles if h.get('handle_init'))

    # ── Build dynamic session filter code ─────────────────────────────────
    # WHY: The optimizer might find that only London+NY sessions are profitable.
    #      The EA must enforce this — otherwise it trades Asian session too,
    #      losing the edge the optimizer found.
    # CHANGED: April 2026 — dynamic session filter in generated EA
    #
    # Session hours (GMT):
    #   Asian:   00:00 - 08:00
    #   London:  07:00 - 16:00
    #   New York: 13:00 - 22:00
    #   Sydney:  22:00 - 07:00 (wraps midnight)

    # WHY: If all major sessions are checked (Asian+London+NY), they cover
    #      hours 0-22 but MISS 22-24 GMT. This is NOT "all sessions" — it's
    #      a 2-hour gap the backtest didn't have. Treat all-sessions as no filter.
    # CHANGED: April 2026 — all sessions = no filter
    _all_sessions = {'asian', 'london', 'new york'}
    _selected = {s.strip().lower() for s in (session_filter or [])}
    _is_all_sessions = _all_sessions.issubset(_selected)

    if session_filter and len(session_filter) > 0 and not _is_all_sessions:
        session_checks = []
        for sess in session_filter:
            s = sess.strip().lower()
            if s in ('london', 'london session'):
                session_checks.append('(hour >= 7 && hour < 16)')
            elif s in ('new york', 'ny', 'new york session'):
                session_checks.append('(hour >= 13 && hour < 22)')
            elif s in ('asian', 'asia', 'asian session', 'tokyo'):
                session_checks.append('(hour >= 0 && hour < 8)')
            elif s in ('sydney', 'sydney session'):
                session_checks.append('(hour >= 22 || hour < 7)')

        if session_checks:
            session_body = ' || '.join(session_checks)
            session_code = f'return ({session_body});'
        else:
            session_code = 'return true; // All sessions allowed'
    else:
        session_code = 'return true; // All sessions allowed'

    # ── Build dynamic day filter code ─────────────────────────────────────
    # WHY: The optimizer might find that Mon/Fri are unprofitable (news days).
    #      The EA must skip those days to preserve the edge.
    # CHANGED: April 2026 — dynamic day filter in generated EA
    #
    # MQL5 day_of_week: 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
    if day_filter and len(day_filter) > 0 and set(day_filter) != {1,2,3,4,5}:
        # Only generate filter if not all weekdays are selected
        allowed_days = ', '.join(str(d) for d in sorted(day_filter))
        day_checks = ' || '.join(f'dow == {d}' for d in sorted(day_filter))
        day_code = f'return ({day_checks});  // Allowed: {allowed_days}'
    else:
        day_code = 'if(dow == 0 || dow == 6) return false;\n   return true;  // All weekdays allowed'

    # ── Build comments for verification report ────────────────────────────
    # WHY: Comments must reflect whether filters are active. If all sessions
    #      or all weekdays are selected, show "All sessions"/"All days" not
    #      a confusing list.
    # CHANGED: April 2026 — accurate filter comments
    if not session_filter or _is_all_sessions:
        session_comment = 'All sessions'
    else:
        session_comment = ', '.join(session_filter)

    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    if not day_filter or set(day_filter) == {1, 2, 3, 4, 5}:
        day_comment = 'All days'
    else:
        day_comment = ', '.join(day_names[d - 1] for d in day_filter if 1 <= d <= 7)

    conditions_block = '\n'.join(condition_inputs)

    conditions_check_block = '\n'.join(condition_checks)

    # ══════════════════════════════════════════════════════════════════════
    # RULES-DRIVEN MQL5 CODE GENERATION
    # WHY: Each trading_rule in the JSON produces specific MQL5 code.
    #      The generator checks rule['type'] and rule['parameters'] to decide
    #      what code to produce. If a param doesn't exist, that code isn't generated.
    #      This means adding a new firm = adding a JSON, not changing Python code.
    # ══════════════════════════════════════════════════════════════════════

    if trading_rules is None:
        trading_rules = []
    if dd_mechanics is None:
        dd_mechanics = {}
    if restrictions is None:
        restrictions = {}
    if challenge is None:
        challenge = {}

    stage_rules = [r for r in trading_rules if r.get('stage', '') == stage]

    extra_inputs    = []
    extra_globals   = []
    extra_init      = []
    extra_daily_reset = []
    extra_tick_checks = []   # runs every tick BEFORE entry check
    extra_functions = []
    extra_on_trade  = []     # runs when a trade closes (OnTradeTransaction)

    # WHY: g_periodProfit is referenced in GetPayoutStatus() but was only
    #      declared inside the consistency rule handler. If protect_phase or
    #      period_reset rules exist without consistency, it's undeclared.
    # CHANGED: April 2026 — always declare payout globals
    extra_globals.append('double g_periodProfit = 0.0;  // Always declared for payout tracking')

    # Track what capabilities are needed
    has_consistency     = False
    has_min_profit_days = False
    has_protect_phase   = False
    has_period_reset    = False
    funded_risk_pct     = None

    for rule in stage_rules:
        rtype  = rule.get('type', '')
        params = rule.get('parameters', {})
        rname  = rule.get('name', rule.get('id', ''))

        # ── eval_settings: DD buffers only (risk/trades are from optimizer) ──
        # WHY: The firm blows the account at X%. The EA stops at X-buffer%.
        #      This protects against a final bad trade pushing past the limit.
        if rtype == 'eval_settings':
            daily_alert = params.get('daily_dd_alert_pct')
            total_alert = params.get('total_dd_alert_pct')

            if daily_alert is not None:
                extra_inputs.append(f'input double EvalDailyDDAlert = {daily_alert}; // EA stops here (firm blows at {dd_daily_pct}%)')
                extra_tick_checks.append(
                    f'   // [{rname}] Daily DD alert at {daily_alert}% (firm limit: {dd_daily_pct}%)\n'
                    f'   // WHY: Daily DD is % of starting balance per firm rules.\n'
                    f'   if(UsePropFirmMode && !g_stopForDay)\n'
                    f'   {{\n'
                    f'      double dailyLossPct = (g_startingBalance > 0) ? (g_dailyReference - equity) / g_startingBalance * 100.0 : 0;\n'
                    f'      if(dailyLossPct >= EvalDailyDDAlert)\n'
                    f'      {{\n'
                    f'         g_stopForDay = true;\n'
                    f'         CloseAllPositions("DailyDDBuffer");\n'
                    f'         Print("[EVAL] Daily DD buffer hit: ", DoubleToString(dailyLossPct,1), "%");\n'
                    f'         return;\n'
                    f'      }}\n'
                    f'   }}')

            if total_alert is not None:
                extra_inputs.append(f'input double EvalTotalDDAlert = {total_alert}; // Alert only (firm blows at {dd_total_pct}%)')
                extra_globals.append('bool g_totalDDAlertSent = false;')
                extra_tick_checks.append(
                    f'   // [{rname}] Total DD alert at {total_alert}% (firm limit: {dd_total_pct}%)\n'
                    f'   // NOTE: Alert only (once) — does NOT stop trading.\n'
                    f'   if(UsePropFirmMode && !g_totalDDAlertSent)\n'
                    f'   {{\n'
                    f'      double totalDDPct = (g_startingBalance > 0) ? (g_hwm - equity) / g_startingBalance * 100.0 : 0;\n'
                    f'      if(totalDDPct >= EvalTotalDDAlert)\n'
                    f'      {{\n'
                    f'         Alert("[EVAL] Total DD buffer hit: " + DoubleToString(totalDDPct,1) + "% — trading continues");\n'
                    f'         g_totalDDAlertSent = true;\n'
                    f'      }}\n'
                    f'   }}')

        # ── funded_accumulate: DD alerts with payout status ──────────────────
        elif rtype == 'funded_accumulate':
            if 'risk_pct' in params:
                funded_risk_pct = params['risk_pct']

            # DD alert with payout condition status
            # WHY: The alert includes whether payout conditions are met,
            #      so the user knows: "met → stop & collect" or "not met → careful"
            if 'daily_dd_alert_pct' in params:
                da = params['daily_dd_alert_pct']
                extra_inputs.append(f'input double FundedDailyDDAlert = {da}; // [{rname}]')
                extra_globals.append(f'bool g_dailyAlertSent = false;')
                extra_daily_reset.append(f'   g_dailyAlertSent = false;')
                extra_tick_checks.append(
                    f'   // [{rname}] Daily DD alert at {da}%\n'
                    f'   // WHY: Daily DD is % of starting balance per firm rules.\n'
                    f'   if(UsePropFirmMode && !g_dailyAlertSent)\n'
                    f'   {{\n'
                    f'      double dailyLossPct = (g_startingBalance > 0) ? (g_dailyReference - equity) / g_startingBalance * 100.0 : 0;\n'
                    f'      if(dailyLossPct >= FundedDailyDDAlert)\n'
                    f'      {{\n'
                    f'         string cs = GetPayoutStatus();\n'
                    f'         SendNotification("[DD] Daily " + DoubleToString(dailyLossPct,1) + "% | " + cs);\n'
                    f'         SendMail("[DD] " + _Symbol, "Daily DD: " + DoubleToString(dailyLossPct,1) + "% | " + cs);\n'
                    f'         g_stopForDay = true;\n'
                    f'         g_dailyAlertSent = true;\n'
                    f'         return;\n'
                    f'      }}\n'
                    f'   }}')

            if 'emergency_total_dd_pct' in params:
                em = params['emergency_total_dd_pct']
                extra_inputs.append(f'input double EmergencyDDPct = {em}; // [{rname}] Stop for PERIOD')
                extra_globals.append(f'bool g_stoppedForPeriod = false;')
                extra_tick_checks.append(
                    f'   // [{rname}] Emergency: stop for rest of period at {em}%\n'
                    f'   if(g_stoppedForPeriod) {{ LogSkip("stopped_for_period", 0); return; }}\n'
                    f'   // WHY: Total DD is % of starting balance per firm rules.\n'
                    f'   if(UsePropFirmMode)\n'
                    f'   {{\n'
                    f'      double totalDDPct = (g_startingBalance > 0) ? (g_hwm - equity) / g_startingBalance * 100.0 : 0;\n'
                    f'      if(totalDDPct >= EmergencyDDPct)\n'
                    f'      {{\n'
                    f'         string cs = GetPayoutStatus();\n'
                    f'         SendNotification("[EMERGENCY] Total DD " + DoubleToString(totalDDPct,1) + "% — stopped for period | " + cs);\n'
                    f'         SendMail("[EMERGENCY] " + _Symbol, "Total DD " + DoubleToString(totalDDPct,1) + "% — stopped for period\\n" + cs);\n'
                    f'         g_stoppedForPeriod = true;\n'
                    f'         CloseAllPositions("EmergencyDD");\n'
                    f'         return;\n'
                    f'      }}\n'
                    f'   }}')

        # ── funded_protect: stop when conditions met ──────────────────────────
        elif rtype == 'funded_protect':
            if params.get('stop_trading'):
                has_protect_phase = True
                extra_globals.append(f'bool g_payoutCondsMet = false;')
                extra_tick_checks.append(
                    f'   // [{rname}] STOP when payout conditions met\n'
                    f'   if(g_payoutCondsMet)\n'
                    f'   {{ LogSkip("CONDITIONS_MET_STOP", 0); return; }}')

        # ── consistency: track best day % of total ───────────────────────────
        elif rtype == 'consistency':
            has_consistency = True
            mp = params.get('max_day_pct', 20)
            extra_inputs.append(f'input double ConsistencyMaxPct = {mp}; // [{rname}]')
            extra_globals.append(f'double g_bestDayProfit = 0.0;')
            # g_periodProfit now declared globally at top (line 650)

        # ── min_profitable_days ───────────────────────────────────────────────
        elif rtype == 'min_profitable_days':
            has_min_profit_days = True
            md = params.get('min_days', 3)
            mp = params.get('min_pct_per_day', 0.5)
            extra_inputs.append(f'input int MinProfitDays = {md}; // [{rname}]')
            extra_inputs.append(f'input double MinDayProfitPct = {mp}; // Min % per day')
            extra_globals.append(f'int g_profitDayCount = 0;')

        # ── period_reset: what resets every 14 days ──────────────────────────
        elif rtype == 'period_reset':
            has_period_reset = True
            pd_days = params.get('period_days', 14)
            extra_inputs.append(f'input int PayoutPeriodDays = {pd_days}; // [{rname}]')
            extra_globals.append(f'datetime g_periodStart = 0;')

    # ── Override risk for funded stage if specified in JSON ───────────────
    if funded_risk_pct is not None and stage == 'funded':
        risk_per_trade_pct = funded_risk_pct

    # ── DD tracking — generic for any firm ───────────────────────────────
    # WHY: Works for any prop firm. Reads behavior from drawdown_mechanics JSON.
    #      Falls back to safe defaults (equity trailing, no lock) if not defined.
    # CHANGED: April 2026 — full payout cycle system, generic + persistent
    trailing_dd   = dd_mechanics.get('trailing_dd', {})
    daily_dd_mech = dd_mechanics.get('daily_dd', {})
    post_payout   = dd_mechanics.get('post_payout', {})

    basis    = trailing_dd.get('basis', 'equity')
    lock_pct = trailing_dd.get('lock_after_gain_pct')

    # Core DD globals (always)
    extra_globals.append('double  g_hwm                     = 0.0;   // High water mark')
    extra_globals.append('double  g_ddFloor                 = 0.0;   // Equity level that triggers breach')
    extra_globals.append('double  g_dailyReference          = 0.0;   // Daily DD reference point')
    extra_globals.append('double  g_startingBalance         = 0.0;   // Balance when current period started (resets at payout)')
    # WHY: g_startingBalance gets overwritten at every payout. The DD
    #      floor anchor must stay fixed at the ORIGINAL starting balance
    #      forever — the firm rule is "floor at original, any drop
    #      below = breach". Without this, each payout raises the floor
    #      by the payout amount and the EA stops trading prematurely.
    # CHANGED: April 2026 — fix post-payout floor drift (audit bug #3)
    extra_globals.append('double  g_originalStartingBalance = 0.0;   // Original starting balance — NEVER overwritten')
    extra_globals.append('bool    g_ddLocked                = false; // True when trailing DD has locked')

    # WHY: Payout cycle tracking only applies to funded accounts. In eval
    #      mode there is no payout — you pass the eval and move to funded.
    #      g_stopForever must be permanent in eval (no payout-based recovery).
    # CHANGED: April 2026 — skip payout logic for eval stage
    # Globals are always declared (needed by SaveDDState for compilation)
    extra_globals.append('bool     g_postPayoutLockApplied = false;')
    if stage != 'evaluation':
        extra_globals.append('datetime g_payoutWaitStart    = 0;     // When bot entered waiting state')
        extra_globals.append('datetime g_lastReminderSent   = 0;     // Last reminder email time')
        extra_globals.append('bool     g_initialAlertSent   = false; // True after first stopped-state email')
        extra_globals.append('bool     g_prevPayoutFlag     = false; // Rising-edge for PayoutReceived')
        extra_globals.append('bool     g_prevWithdrawnFlag     = false;')

        # Payout inputs (funded only — manual confirmation flow)
        extra_inputs.append('input bool   PayoutReceived          = false; // Set TRUE after confirming payout in firm dashboard')
        extra_inputs.append('input int    ReminderFirstAfterDays  = 5;     // Send first reminder after N days')
        extra_inputs.append('input int    ReminderRepeatEveryDays = 2;     // Repeat every N days until confirmed')

        if post_payout.get('dd_locks_at') == 'initial_balance':
            extra_inputs.append('input bool   PayoutWithdrawn         = false; // Set TRUE after withdrawing payout — locks DD floor permanently')

    # OnInit: init all globals + restore from GlobalVariables (survives MT5 restart)
    # WHY: g_originalStartingBalance is set ONCE at the very first OnInit
    #      and persisted via GlobalVariables. Every subsequent restart and
    #      every payout cycle reads the persisted value, never overwrites.
    # CHANGED: April 2026 — fix post-payout floor drift (audit bug #3)
    extra_init.append(f'   g_startingBalance  = AccountInfoDouble(ACCOUNT_BALANCE);')
    # WHY: Old code keyed GlobalVariables by _Symbol only. Two EAs with
    #      different magic numbers on the same chart would overwrite each
    #      other's DD state. Now keyed by _Symbol + "_" + MagicNumber so
    #      each EA has isolated state.
    # CHANGED: April 2026 — per-magic GlobalVariable keys (audit HIGH)
    extra_init.append(f'   string _gvPrefix = _Symbol + "_" + IntegerToString(MagicNumber);')
    extra_init.append(f'   // WHY: Phase 11 migrated GlobalVariable keys from _Symbol to')
    extra_init.append(f'   //      _Symbol + "_" + MagicNumber. Old deployments will see a')
    extra_init.append(f'   //      fresh state on first run after upgrade.')
    extra_init.append(f'   Print("[INIT] Per-magic state: ", _Symbol, " magic=", MagicNumber);')
    extra_init.append(f'   if(GlobalVariableCheck("EA_origStartBal_" + _gvPrefix))')
    extra_init.append(f'      g_originalStartingBalance = GlobalVariableGet("EA_origStartBal_" + _gvPrefix);')
    extra_init.append(f'   else')
    extra_init.append(f'   {{')
    extra_init.append(f'      g_originalStartingBalance = g_startingBalance;')
    extra_init.append(f'      GlobalVariableSet("EA_origStartBal_" + _gvPrefix, g_originalStartingBalance);')
    extra_init.append(f'      Print("[DD] Original starting balance locked at $", DoubleToString(g_originalStartingBalance, 2));')
    extra_init.append(f'   }}')
    extra_init.append(f'   g_hwm              = g_startingBalance;')
    extra_init.append(f'   g_ddFloor          = g_originalStartingBalance * (1.0 - {dd_total_pct}/100.0);')
    extra_init.append(f'   g_dailyReference   = MathMax(AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY));')
    if stage != 'evaluation':
        extra_init.append(f'   g_payoutWaitStart  = 0;')
        extra_init.append(f'   g_lastReminderSent = 0;')
        extra_init.append(f'   g_initialAlertSent = false;')
        extra_init.append(f'   g_prevPayoutFlag   = PayoutReceived;')
        if post_payout.get('dd_locks_at') == 'initial_balance':
            extra_init.append(f'   g_prevWithdrawnFlag     = PayoutWithdrawn;')
            extra_init.append(f'   g_postPayoutLockApplied = false;')
    # WHY: _gvPrefix already set above — same per-magic key pattern.
    # CHANGED: April 2026 — per-magic GlobalVariable keys (audit HIGH)
    extra_init.append(f'   if(GlobalVariableCheck("EA_ddLocked_" + _gvPrefix))')
    extra_init.append(f'   {{')
    extra_init.append(f'      g_ddLocked              = (GlobalVariableGet("EA_ddLocked_" + _gvPrefix) > 0.5);')
    extra_init.append(f'      g_ddFloor               = GlobalVariableGet("EA_ddFloor_" + _gvPrefix);')
    extra_init.append(f'      g_hwm                   = GlobalVariableGet("EA_hwm_" + _gvPrefix);')
    extra_init.append(f'      g_postPayoutLockApplied = (GlobalVariableGet("EA_postPayout_" + _gvPrefix) > 0.5);')
    extra_init.append(f'      Print("[DD] Restored from globals. ddFloor=$", DoubleToString(g_ddFloor,2),')
    extra_init.append(f'            " hwm=$", DoubleToString(g_hwm,2), " locked=", g_ddLocked);')
    extra_init.append(f'   }}')

    # HWM + ddFloor update logic
    if basis == 'closed_balance':
        extra_on_trade.append(
            f'   // DD basis: closed balance (HWM moves only on closed trades)\n'
            f'   double newBalance = AccountInfoDouble(ACCOUNT_BALANCE);\n'
            f'   if(newBalance > g_hwm && !g_ddLocked)\n'
            f'   {{\n'
            f'      g_hwm     = newBalance;\n'
            f'      g_ddFloor = g_hwm * (1.0 - {dd_total_pct}/100.0);\n'
            f'      SaveDDState();\n'
            f'   }}')
        if lock_pct:
            extra_on_trade.append(
                f'   // Lock-after-gain: when balance reaches +{lock_pct}%, lock DD floor\n'
                f'   if(!g_ddLocked && newBalance >= g_startingBalance * (1.0 + {lock_pct}/100.0))\n'
                f'   {{\n'
                f'      g_ddLocked = true;\n'
                f'      g_ddFloor  = g_originalStartingBalance;\n'
                f'      SaveDDState();\n'
                f'      Print("[DD] LOCKED — floor at $", DoubleToString(g_ddFloor, 2));\n'
                f'      string subj = "[DD] " + _Symbol + " — Drawdown LOCKED";\n'
                f'      string body = "Reached +{lock_pct}% on starting balance.\\n";\n'
                f'      body += "DD floor locked at: $" + DoubleToString(g_ddFloor, 2);\n'
                f'      SendMail(subj, body);\n'
                f'   }}')
    else:
        extra_tick_checks.insert(0,
            f'   // DD basis: floating equity (standard trailing)\n'
            f'   if(equity > g_hwm && !g_ddLocked)\n'
            f'   {{\n'
            f'      g_hwm     = equity;\n'
            f'      g_ddFloor = g_hwm * (1.0 - {dd_total_pct}/100.0);\n'
            f'      SaveDDState();\n'
            f'   }}')
        if lock_pct:
            extra_tick_checks.insert(1,
                f'   // Lock-after-gain: when equity reaches +{lock_pct}%, lock DD floor\n'
                f'   if(!g_ddLocked && equity >= g_startingBalance * (1.0 + {lock_pct}/100.0))\n'
                f'   {{\n'
                f'      g_ddLocked = true;\n'
                f'      g_ddFloor  = g_originalStartingBalance;\n'
                f'      SaveDDState();\n'
                f'      Print("[DD] LOCKED — floor at $", DoubleToString(g_ddFloor, 2));\n'
                f'      string subj = "[DD] " + _Symbol + " — Drawdown LOCKED";\n'
                f'      string body = "Reached +{lock_pct}% on starting balance.\\n";\n'
                f'      body += "DD floor locked at: $" + DoubleToString(g_ddFloor, 2);\n'
                f'      SendMail(subj, body);\n'
                f'   }}')


    # Daily reference depends on firm's daily DD mechanic
    if daily_dd_mech.get('reference') == 'max_balance_equity':
        reset_time = daily_dd_mech.get('reset_time', '00:00')
        reset_tz   = daily_dd_mech.get('reset_timezone', 'GMT+3')
        extra_daily_reset.insert(0,
            f'   // Daily DD reference = max(balance, equity) at {reset_time} {reset_tz}\n'
            f'   g_dailyReference = MathMax(AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY));')
    else:
        extra_daily_reset.insert(0,
            f'   g_dailyReference = AccountInfoDouble(ACCOUNT_EQUITY);')

    # ── Payout condition tracking ─────────────────────────────────────────
    if has_consistency or has_min_profit_days:
        if has_min_profit_days:
            # WHY: g_sessionEquity holds yesterday's starting balance at this point
            #      because the template now resets g_sessionEquity AFTER this block.
            #      So ydayPnl is computed correctly from yesterday's baseline.
            # CHANGED: April 2026 — explicit ordering + Print diagnostic
            extra_daily_reset.append(
                f'   // Count profitable days (payout condition)\n'
                f'   // g_sessionEquity is still yesterday\'s baseline here (reset happens after this block)\n'
                f'   double ydayPnl = AccountInfoDouble(ACCOUNT_BALANCE) - g_sessionEquity;\n'
                f'   if(g_sessionEquity > 0 && ydayPnl >= g_sessionEquity * MinDayProfitPct / 100.0)\n'
                f'   {{\n'
                f'      g_profitDayCount++;\n'
                f'      Print("[PAYOUT] Profitable day #", g_profitDayCount,\n'
                f'             " P&L=$", DoubleToString(ydayPnl, 2),\n'
                f'             " start=$", DoubleToString(g_sessionEquity, 2));\n'
                f'   }}')
        if has_consistency:
            extra_daily_reset.append(
                f'   // Track best day and total profit (consistency)\n'
                f'   double dayProf = AccountInfoDouble(ACCOUNT_BALANCE) - g_sessionEquity;\n'
                f'   if(dayProf > g_bestDayProfit) g_bestDayProfit = dayProf;\n'
                f'   g_periodProfit = AccountInfoDouble(ACCOUNT_BALANCE) - g_startingBalance;')

        if has_protect_phase:
            conds = ['g_periodProfit > 0']
            if has_min_profit_days:
                conds.append('g_profitDayCount >= MinProfitDays')
            if has_consistency:
                conds.append('(g_bestDayProfit / g_periodProfit * 100.0 <= ConsistencyMaxPct)')
            check = ' && '.join(conds)
            extra_daily_reset.append(
                f'   // Check ALL payout conditions\n'
                f'   if(!g_payoutCondsMet && ({check}))\n'
                f'   {{\n'
                f'      g_payoutCondsMet = true;\n'
                f'      CloseAllPositions("PayoutCondsMet");\n'
                f'      string s = GetPayoutStatus();\n'
                f'      SendNotification("[PAYOUT] CONDITIONS MET — STOP | " + s);\n'
                f'      SendMail("[PAYOUT] " + _Symbol, "Conditions met. STOP trading.\\n" + s);\n'
                f'      Alert("[PAYOUT] CONDITIONS MET. Collect your payout!");\n'
                f'   }}')

    # ── SaveDDState() helper — persist DD state across EA restarts ──────────
    # WHY: g_ddFloor must survive MT5 restarts. Without GlobalVariable saves
    #      the floor reverts to the raw formula value and may be wrong.
    # CHANGED: April 2026 — payout cycle system
    extra_functions.append(
        f'//+------------------------------------------------------------------+\n'
        f'//| SaveDDState — persist DD floor/lock state to GlobalVariables    |\n'
        f'//+------------------------------------------------------------------+\n'
        f'void SaveDDState()\n'
        f'{{\n'
        # WHY: Per-magic keys. SaveDDState is a standalone function so
        #      _gvPrefix from OnInit is out of scope; rebuild it here.
        # CHANGED: April 2026 — per-magic GlobalVariable keys (audit HIGH)
        f'   string _gvPrefix = _Symbol + "_" + IntegerToString(MagicNumber);\n'
        f'   GlobalVariableSet("EA_ddLocked_"   + _gvPrefix, g_ddLocked ? 1.0 : 0.0);\n'
        f'   GlobalVariableSet("EA_ddFloor_"    + _gvPrefix, g_ddFloor);\n'
        f'   GlobalVariableSet("EA_hwm_"        + _gvPrefix, g_hwm);\n'
        f'   GlobalVariableSet("EA_postPayout_" + _gvPrefix, g_postPayoutLockApplied ? 1.0 : 0.0);\n'
        f'}}'
    )

    # ── CheckPayoutFlag() — manual payout confirmation via input toggle ───
    # WHY: Eval has no payout cycle — pass the eval and you're done.
    #      g_stopForever is permanent in eval mode. Payout logic only
    #      applies to funded accounts where the trader collects payouts.
    # CHANGED: April 2026 — skip payout logic for eval stage
    if stage != 'evaluation':
        payout_reset_items = []
        if has_min_profit_days:
            payout_reset_items.append('g_profitDayCount      = 0;')
        if has_consistency:
            payout_reset_items.append('g_bestDayProfit       = 0;')
            payout_reset_items.append('g_periodProfit        = 0;')
        if has_protect_phase:
            payout_reset_items.append('g_payoutCondsMet      = false;')
        payout_reset_items.append('g_stopForever         = false;')
        payout_reset_items.append('g_startingBalance     = AccountInfoDouble(ACCOUNT_BALANCE);')
        _dd_reset = True
        try:
            _firm_d = (prop_firm or {}).get('firm_data', {})
            _challenge = _firm_d.get('challenges', [{}])[0]
            _dd_reset = _challenge.get('funded', {}).get('dd_reset_on_payout', True)
        except Exception:
            pass
        if _dd_reset:
            payout_reset_items.append(f'g_ddFloor             = g_originalStartingBalance * (1.0 - {dd_total_pct}/100.0);')
            payout_reset_items.append('g_hwm                 = g_startingBalance;')
        else:
            payout_reset_items.append('// DD does NOT reset on payout (firm rule: dd_reset_on_payout=false)')
            payout_reset_items.append('// g_ddFloor and g_hwm are preserved -- trailing DD continues')
        if post_payout.get('dd_locks_at') == 'initial_balance':
            payout_reset_items.append('// g_ddLocked preserved (permanent post-payout lock firm)')
            payout_reset_items.append('// g_postPayoutLockApplied preserved (lock already applied)')
        else:
            payout_reset_items.append('g_ddLocked            = false;')
            payout_reset_items.append('g_postPayoutLockApplied = false;')
        payout_reset_items.append('g_payoutWaitStart     = 0;')
        payout_reset_items.append('g_lastReminderSent    = 0;')
        payout_reset_items.append('g_initialAlertSent    = false;')
        payout_reset_items.append('SaveDDState();')
        payout_reset_block = '\n      '.join(payout_reset_items)

        waiting_cond = 'g_payoutCondsMet' if has_protect_phase else 'g_stopForever'

        extra_functions.append(
            f'//+------------------------------------------------------------------+\n'
            f'//| CheckPayoutFlag — detect rising edge of PayoutReceived input    |\n'
            f'//| WHY: Bot halts after payout conditions met. User manually sets  |\n'
            f'//|      PayoutReceived=true once payout is confirmed. This detects |\n'
            f'//|      that toggle and starts a fresh cycle.                      |\n'
            f'//| CHANGED: April 2026 — manual confirmation + reminder system    |\n'
            f'//+------------------------------------------------------------------+\n'
            f'void CheckPayoutFlag()\n'
            f'{{\n'
            f'   // ── Rising edge: payout just confirmed ───────────────────────\n'
            f'   if(PayoutReceived && !g_prevPayoutFlag)\n'
            f'   {{\n'
            f'      g_prevPayoutFlag = true;\n'
            f'      Print("[PAYOUT] Confirmed — starting new cycle. Balance: $",\n'
            f'            DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2));\n'
            f'      {payout_reset_block}\n'
            f'      Alert("[CYCLE] Payout confirmed — new trading cycle started!");\n'
            f'      SendNotification("[PAYOUT] " + _Symbol + " — New cycle started after payout confirmation");\n'
            f'      return;\n'
            f'   }}\n'
            f'   g_prevPayoutFlag = PayoutReceived;\n'
            f'\n'
            f'   // ── While stopped and awaiting payout confirmation ───────────\n'
            f'   if(!{waiting_cond} || PayoutReceived) return;\n'
            f'\n'
            f'   // Send initial alert once when we first enter waiting state\n'
            f'   if(!g_initialAlertSent)\n'
            f'   {{\n'
            f'      g_payoutWaitStart  = TimeCurrent();\n'
            f'      g_lastReminderSent = TimeCurrent();\n'
            f'      g_initialAlertSent = true;\n'
            f'      string s0 = GetPayoutStatus();\n'
            f'      SendNotification("[PAYOUT] WAITING — set PayoutReceived=true when collected | " + s0);\n'
            f'      SendMail("[PAYOUT] " + _Symbol + " — Waiting for confirmation",\n'
            f'               "Payout conditions met. Bot stopped.\\n" + s0 +\n'
            f'               "\\n\\nSet PayoutReceived=true in EA inputs once payout is in your account.");\n'
            f'   }}\n'
            f'\n'
            f'   // Send scheduled reminder emails\n'
            f'   if(g_payoutWaitStart <= 0) return;\n'
            f'   double waitDays    = (TimeCurrent() - g_payoutWaitStart)  / 86400.0;\n'
            f'   double sinceRemind = (TimeCurrent() - g_lastReminderSent) / 86400.0;\n'
            f'   bool sendFirst  = (waitDays  >= ReminderFirstAfterDays && sinceRemind >= ReminderFirstAfterDays);\n'
            f'   bool sendRepeat = (waitDays  >  ReminderFirstAfterDays && sinceRemind >= ReminderRepeatEveryDays\n'
            f'                      && ReminderRepeatEveryDays > 0);\n'
            f'   if(sendFirst || sendRepeat)\n'
            f'   {{\n'
            f'      g_lastReminderSent = TimeCurrent();\n'
            f'      string sr = GetPayoutStatus();\n'
            f'      SendMail("[PAYOUT REMINDER] " + _Symbol,\n'
            f'               "Waiting " + DoubleToString(waitDays, 1) + " days for payout confirmation.\\n"\n'
            f'               + sr + "\\n\\nSet PayoutReceived=true in EA inputs to resume trading.");\n'
            f'   }}\n'
            f'}}'
        )

        # Insert CheckPayoutFlag at the very start of tick checks so it runs
        # BEFORE the g_stopForever guard — allows the bot to resume if payout confirmed.
        extra_tick_checks.insert(0,
            f'   // ── Payout confirmation (MUST run before g_stopForever guard) ─\n'
            f'   // WHY: Clears g_stopForever when user confirms payout received.\n'
            f'   // CHANGED: April 2026 — manual confirmation system\n'
            f'   CheckPayoutFlag();'
        )

        # ── CheckPostPayoutLock() — only if firm locks DD at initial_balance ──
        if post_payout.get('dd_locks_at') == 'initial_balance':
            extra_functions.append(
                f'//+------------------------------------------------------------------+\n'
                f'//| CheckPostPayoutLock — lock DD floor after withdrawal confirmed  |\n'
                f'//| WHY: After first withdrawal, firm locks trailing DD floor to    |\n'
                f'//|      initial balance. User confirms via PayoutWithdrawn toggle. |\n'
                f'//| CHANGED: April 2026 — post-payout DD lock                      |\n'
                f'//+------------------------------------------------------------------+\n'
                f'void CheckPostPayoutLock()\n'
                f'{{\n'
                f'   if(g_postPayoutLockApplied) return;\n'
                f'   // Rising edge: withdrawal confirmed\n'
                f'   if(PayoutWithdrawn && !g_prevWithdrawnFlag)\n'
                f'   {{\n'
                f'      g_prevWithdrawnFlag     = true;\n'
                f'      g_postPayoutLockApplied = true;\n'
                f'      g_ddLocked              = true;\n'
                f'      // WHY: Post-payout lock anchors to ORIGINAL starting\n'
                f'      //      balance, not the current (post-payout) balance.\n'
                f'      // CHANGED: April 2026 — fix post-payout floor drift\n'
                f'      g_ddFloor               = g_originalStartingBalance;\n'
                f'      SaveDDState();\n'
                f'      Print("[DD] Post-payout lock applied: floor = $",\n'
                f'            DoubleToString(g_originalStartingBalance, 2));\n'
                f'      SendMail("[DD] " + _Symbol + " — Post-payout DD floor locked",\n'
                f'               "Withdrawal confirmed. DD floor locked at starting balance $" +\n'
                f'               DoubleToString(g_originalStartingBalance, 2) + ".");\n'
                f'   }}\n'
                f'   g_prevWithdrawnFlag = PayoutWithdrawn;\n'
                f'}}'
            )
            extra_tick_checks.insert(1,
                f'   CheckPostPayoutLock();'
            )

    # ── GetPayoutStatus function (funded only — eval doesn't need it) ──
    if stage != 'evaluation':
        parts = ['"Status: "']
        if has_min_profit_days:
            parts.append('"Days=" + IntegerToString(g_profitDayCount) + "/" + IntegerToString(MinProfitDays)')
        if has_consistency:
            parts.append('"Best=" + DoubleToString(g_periodProfit>0 ? g_bestDayProfit/g_periodProfit*100 : 0, 1) + "%"')
        if has_consistency or has_protect_phase:
            parts.append('"Profit=$" + DoubleToString(g_periodProfit, 0)')
        if has_protect_phase:
            parts.append('(g_payoutCondsMet ? " MET" : " NOT MET")')
        extra_functions.append(
            f'string GetPayoutStatus()\n'
            f'{{ return {" + ".join(parts)}; }}')

    # ── OnTradeTransaction ────────────────────────────────────────────────
    on_trade_body = '\n'.join(extra_on_trade)
    if on_trade_body:
        extra_functions.append(
            f'void OnTradeTransaction(const MqlTradeTransaction &trans,\n'
            f'                         const MqlTradeRequest &request,\n'
            f'                         const MqlTradeResult &result)\n'
            f'{{\n'
            f'   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;\n'
            f'   double dealProfit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);\n'
            f'{on_trade_body}\n'
            f'}}')

    # ── Build daily reset timing from drawdown_mechanics ──────────────────
    # WHY: Leveraged resets daily DD at 23:00 GMT+3 (= 20:00 GMT), not midnight.
    #      Using midnight means the DD reference is wrong for 1 hour, which
    #      could trigger a false breach or miss a real one.
    # CHANGED: April 2026 — dynamic reset timing from JSON
    reset_hour_gmt = 0  # default: midnight GMT
    if dd_mechanics:
        daily_dd_mech = dd_mechanics.get('daily_dd', {})
        reset_time_str = daily_dd_mech.get('reset_time', '00:00')
        reset_tz = daily_dd_mech.get('reset_timezone', 'GMT')

        # Parse reset time
        try:
            parts = reset_time_str.split(':')
            reset_hour_local = int(parts[0])
        except Exception:
            reset_hour_local = 0

        # Convert to GMT
        tz_offset = 0
        if 'GMT+' in reset_tz:
            tz_offset = int(reset_tz.replace('GMT+', ''))
        elif 'GMT-' in reset_tz:
            tz_offset = -int(reset_tz.replace('GMT-', ''))
        reset_hour_gmt = (reset_hour_local - tz_offset) % 24

    use_custom_reset = reset_hour_gmt != 0

    # ── Exit strategy specific code blocks ────────────────────────────────
    # WHY: Each exit type needs different inputs, globals, and management code.
    # CHANGED: April 2026 — all exit strategies supported
    exit_inputs = ''
    exit_globals = ''
    exit_on_entry = ''
    exit_management = ''

    if exit_class in ('ATRBased', 'ATROnly', 'ATRTrailing'):
        # WHY: ATR-Based reads ATR at entry and computes SL/TP as multiples.
        #      Adapts to current volatility — wide SL in volatile markets,
        #      tight SL in quiet markets.
        atr_tf = atr_column.split('_')[0] if '_' in atr_column else 'H1'
        atr_period_str = atr_column.split('_')[-1] if '_' in atr_column else '14'
        atr_mql_tf = _mql_periods.get(atr_tf, 'PERIOD_H1')

        exit_inputs = (
            f'input double SL_ATR_Mult     = {sl_atr_mult};              // SL = ATR × this\n'
            f'input double TP_ATR_Mult     = {tp_atr_mult};              // TP = ATR × this\n'
            f'input int    ATR_Period      = {atr_period_str};            // ATR period\n'
        )
        exit_globals = (
            f'int handle_exit_atr;\n'
            f'double g_entrySL = 0.0;\n'
            f'double g_entryTP = 0.0;\n'
        )
        extra_init.append(
            f'   handle_exit_atr = iATR(NULL, {atr_mql_tf}, ATR_Period);\n'
            f'   if(handle_exit_atr == INVALID_HANDLE) return(INIT_FAILED);'
        )
        # WHY: trade.Buy/Sell already receives slPrice/tpPrice computed from ATR in the
        #      lot-sizing block above — SL/TP are set atomically with the order, no
        #      PositionModify needed. The old PositionModify used trade.ResultOrder()
        #      (order ticket) not a position ticket, which is wrong in netting mode and
        #      re-reads ATR at shift 0 (forming bar) risking a different value than the
        #      lot-sizing read. We store the ATR at entry in globals only for reference.
        # CHANGED: April 2026 — remove redundant/buggy PositionModify on ATR entry
        exit_on_entry = (
            f'      // ATR SL/TP set atomically in trade.Buy/Sell (slPrice/tpPrice).\n'
            f'      // Record ATR at entry in globals for management reference.\n'
            f'      {{\n'
            f'         double _atrEntry[1];\n'
            f'         CopyBuffer(handle_exit_atr, 0, 1, 1, _atrEntry);  // shift=1: last closed bar\n'
            f'         g_entrySL = _atrEntry[0] * SL_ATR_Mult;\n'
            f'         g_entryTP = _atrEntry[0] * TP_ATR_Mult;\n'
            f'      }}\n'
        )

    elif exit_class == 'TimeBased':
        # WHY: Time-Based closes the trade after N candles regardless of profit.
        #      Prevents trades from lingering in choppy markets.
        exit_inputs = (
            f'input int MaxHoldCandles    = {max_candles};               // Close after N candles\n'
        )
        exit_globals = (
            f'int g_entryBarIndex = 0;\n'
        )
        exit_on_entry = (
            f'      // Time-Based: record entry bar index\n'
            f'      g_entryBarIndex = Bars(_Symbol, {mql_period});\n'
        )
        # Guard TimeBased exit with min hold check
        _time_guard = 'if(IsMinHoldMet()) ' if min_hold_minutes > 0 else ''
        exit_management = (
            f'   // Time-Based exit: close after MaxHoldCandles\n'
            f'   if(g_entryBarIndex > 0)\n'
            f'   {{\n'
            f'      int barsHeld = Bars(_Symbol, {mql_period}) - g_entryBarIndex;\n'
            f'      if(barsHeld >= MaxHoldCandles)\n'
            f'      {{\n'
            f'         {_time_guard}CloseAllPositions("TimeExit");\n'
            f'         g_entryBarIndex = 0;\n'
            f'         // WHY: Python backtester enforces occupied_until_idx which\n'
            f'         //      prevents re-entry on the same bar a trade exits.\n'
            f'         //      Without this, the EA re-enters within seconds on the\n'
            f'         //      same M15 bar, taking far more trades than the backtest.\n'
            f'         // CHANGED: April 2026 — block same-bar re-entry after TimeExit\n'
            f'         g_lastBarTime = iTime(_Symbol, {mql_period}, 0);\n'
            f'         Print("[EA] Time exit after ", barsHeld, " candles");\n'
            f'         return;\n'
            f'      }}\n'
            f'   }}\n'
        )

    elif exit_class == 'IndicatorExit':
        # WHY: Indicator Exit closes when an indicator crosses a threshold.
        #      E.g., close BUY when RSI goes above 70 (overbought).
        ind_parts = exit_indicator.split('_', 1)
        ind_tf = ind_parts[0] if len(ind_parts) > 1 else 'H1'
        ind_name = ind_parts[1] if len(ind_parts) > 1 else exit_indicator
        ind_mql_tf = _mql_periods.get(ind_tf, 'PERIOD_H1')

        ind_code = get_mql_code(exit_indicator, 'mt5')

        exit_inputs = (
            f'input double ExitThreshold   = {exit_threshold};            // Exit when indicator crosses this\n'
        )
        if ind_code.get('handle_var'):
            exit_globals = ind_code['handle_var'] + '\n'
        if ind_code.get('handle_init'):
            extra_init.append(f'   {ind_code["handle_init"]}')
        # Guard IndicatorExit with min hold check
        _ind_guard = 'if(IsMinHoldMet()) ' if min_hold_minutes > 0 else ''
        exit_management = (
            f'   // Indicator Exit: close when {exit_indicator} crosses threshold\n'
            f'   {{\n'
            f'      {ind_code["read_code"]}\n'
            f'      if(val_{ind_code["var_name"]} {_ind_compare} ExitThreshold)\n'
            f'      {{\n'
            f'         {_ind_guard}CloseAllPositions("IndicatorExit_{exit_indicator}");\n'
            f'         Print("[EA] Indicator exit: {exit_indicator} = ", val_{ind_code["var_name"]});\n'
            f'         // Block same-bar re-entry (match Python backtester behavior)\n'
            f'         g_lastBarTime = iTime(_Symbol, {mql_period}, 0);\n'
            f'         return;\n'
            f'      }}\n'
            f'   }}\n'
        )

    elif exit_class == 'HybridExit':
        # WHY: Hybrid combines trailing stop + breakeven + time limit.
        #      Most sophisticated exit — protects profits while limiting time exposure.
        exit_inputs = (
            f'input double BreakevenPips   = {breakeven_pips};            // Move SL to entry after this profit\n'
            f'input int    MaxHoldCandles  = {max_candles};               // Force close after N candles\n'
        )
        exit_globals = (
            f'int g_entryBarIndex = 0;\n'
            f'bool g_breakevenSet = false;\n'
        )
        exit_on_entry = (
            f'      // Hybrid: record entry for time-based component\n'
            f'      g_entryBarIndex = Bars(_Symbol, {mql_period});\n'
            f'      g_breakevenSet = false;\n'
        )
        # Guard HybridExit time limit with min hold check
        _hybrid_time_guard = 'if(IsMinHoldMet()) ' if min_hold_minutes > 0 else ''
        # Per-position min hold check for breakeven/trailing
        _hybrid_pos_check = (
            f'      // Per-position min hold check: skip breakeven/trailing if not aged enough\n'
            f'      if(MinHoldMinutes > 0)\n'
            f'      {{\n'
            f'         datetime _openT = (datetime)PositionGetInteger(POSITION_TIME);\n'
            f'         int _holdSec = (int)(TimeCurrent() - _openT);\n'
            f'         if(_holdSec < MinHoldMinutes * 60) continue;  // Skip this position\n'
            f'      }}\n'
        ) if min_hold_minutes > 0 else ''
        exit_management = (
            f'   // Hybrid exit: breakeven + trailing + time limit ({_direction_label})\n'
            f'   // WHY: Old code hardcoded BUY math here. For SELL trades, breakeven\n'
            f'   //      and trailing direction is inverted: profit grows as price\n'
            f'   //      DROPS, SL moves DOWN. April 2026 fix.\n'
            f'   for(int _hi = PositionsTotal() - 1; _hi >= 0; _hi--)\n'
            f'   {{\n'
            f'      ulong _ht = PositionGetTicket(_hi);\n'
            f'      if(_ht <= 0 || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;\n'
            f'      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;\n'
            f'{_hybrid_pos_check}'
            f'      double _openP = PositionGetDouble(POSITION_PRICE_OPEN);\n'
            f'      double _curSL = PositionGetDouble(POSITION_SL);\n'
            f'      double _curTP = PositionGetDouble(POSITION_TP);\n'
            f'      double _bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);\n'
            f'      double _ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);\n'
            f'      double _profitPips = {_profit_pips_expr};\n'
            f'      // Breakeven: move SL to entry once profit reaches threshold\n'
            f'      if(!g_breakevenSet && _profitPips >= BreakevenPips)\n'
            f'      {{\n'
            f'         trade.PositionModify(_ht, {_be_new_sl_expr}, _curTP);\n'
            f'         g_breakevenSet = true;\n'
            f'         Print("[EA] Breakeven set at ", _openP);\n'
            f'      }}\n'
            f'      // Trailing: move SL toward profit as price moves\n'
            f'      if(g_breakevenSet && TrailDistance > 0)\n'
            f'      {{\n'
            f'         double _newSL = {_trail_new_sl_expr};\n'
            f'         if({_trail_sl_compare})\n'
            f'            trade.PositionModify(_ht, _newSL, _curTP);\n'
            f'      }}\n'
            f'   }}\n'
            f'   // Time limit\n'
            f'   if(g_entryBarIndex > 0)\n'
            f'   {{\n'
            f'      int _barsHeld = Bars(_Symbol, {mql_period}) - g_entryBarIndex;\n'
            f'      if(_barsHeld >= MaxHoldCandles)\n'
            f'      {{\n'
            f'         {_hybrid_time_guard}CloseAllPositions("HybridTimeExit");\n'
            f'         g_entryBarIndex = 0;\n'
            f'         // Block same-bar re-entry (match Python backtester behavior)\n'
            f'         g_lastBarTime = iTime(_Symbol, {mql_period}, 0);\n'
            f'         return;\n'
            f'      }}\n'
            f'   }}\n'
        )

    # ── Regime filter check (pre-entry gate) ─────────────────────────────
    # WHY: Must be AFTER exit_globals/extra_init are initialized by exit
    #      strategy code. Old placement was before — exit code reset them.
    # CHANGED: April 2026 — move after exit strategy init
    regime_check_block = ''
    if regime_conditions:
        _regime_lines = []
        _regime_lines.append('   // ── Regime Filter (from backtest settings) ──')
        _regime_lines.append('   bool regimePass = true;')
        for ri, rcond in enumerate(regime_conditions, 1):
            _feat = rcond.get('feature', '')
            _op = rcond.get('direction', rcond.get('operator', '>'))
            _val = rcond.get('threshold', rcond.get('value', 0))
            if not _feat:
                continue
            try:
                _mql = get_mql_code(_feat, 'mt5')
                _var_n = _mql['var_name']
                _cond_expr = _mql_condition_expr(f'val_{_var_n}', _op, f'{float(_val):.6f}')
                _regime_lines.append(f'   // Regime {ri}: {_feat} {_op} {_val}')
                _regime_lines.append(f'   {_mql["read_code"]}')
                _regime_lines.append(f'   if(!({_cond_expr})) regimePass = false;')
                # Add handle if needed
                if _mql.get('handle_var') and _mql['handle_var'] not in exit_globals:
                    exit_globals += _mql['handle_var'] + '\n'
                if _mql.get('handle_init'):
                    extra_init.append(f'   {_mql["handle_init"]}')
            except Exception as _re:
                _regime_lines.append(f'   // Regime {ri}: {_feat} — SKIPPED (no MQL mapping: {_re})')

        _regime_lines.append('   if(!regimePass) { LogSkip("regime_filter", 0); return; }')
        _regime_lines.append('')
        regime_check_block = '\n'.join(_regime_lines)

    # WHY: Daily DD enforcement — firm blows account at daily limit.
    #      Must check every tick, not just on bar close.
    # CHANGED: April 2026 — add daily DD check
    extra_tick_checks.append(
        f'   // ── Daily DD check (firm blows at {dd_daily_pct}%) ──\n'
        f'   if(UsePropFirmMode && !g_stopForDay)\n'
        f'   {{\n'
        f'      double _dailyLoss = g_dailyReference - equity;\n'
        f'      double _dailyLossPct = (_dailyLoss / g_dailyReference) * 100.0;\n'
        f'      if(_dailyLossPct >= DailyDDLimitPct)\n'
        f'      {{\n'
        f'         CloseAllPositions("DailyDDBreach");\n'
        f'         g_stopForDay = true;\n'
        f'         Print("[DD] Daily DD breach: ", DoubleToString(_dailyLossPct, 1), "% >= ", DailyDDLimitPct, "%");\n'
        f'         SendNotification("[DD] " + _Symbol + " — Daily DD limit hit. Stopped for day.");\n'
        f'      }}\n'
        f'   }}'
    )

    # ── Build injection strings ───────────────────────────────────────────
    extra_inputs_block      = '\n'.join(extra_inputs)      if extra_inputs      else ''
    extra_globals_block     = '\n'.join(extra_globals)     if extra_globals     else ''
    extra_init_block        = '\n'.join(extra_init)        if extra_init        else '   // No special init'
    extra_daily_reset_block = '\n'.join(extra_daily_reset) if extra_daily_reset else ''
    extra_tick_checks_block = '\n'.join(extra_tick_checks) if extra_tick_checks else ''
    extra_functions_block   = '\n\n'.join(extra_functions) if extra_functions   else 'string GetPayoutStatus() { return "N/A"; }'

    # Exit-specific blocks (computed outside f-string to avoid backslash issues)
    # WHY: The old fallback called trade.PositionModify(trade.ResultOrder(), entryPrice-sl, entryPrice+tp)
    #      which has two bugs:
    #      1. Direction: BUY math hardcoded — for SELL, SL/TP are inverted, overwriting the
    #         correct values already set atomically by trade.Sell().
    #      2. Ticket: trade.ResultOrder() returns the ORDER ticket, not the POSITION ticket
    #         (wrong in netting mode).
    #      SL/TP are always set atomically in trade.Buy/Sell via slPrice/tpPrice, so no
    #      post-fill PositionModify is needed for any exit type.
    # CHANGED: April 2026 — remove direction-buggy fallback PositionModify
    exit_on_entry_block = exit_on_entry

    # ── MinHoldMinutes enforcement helper ──
    # WHY: Backtester post-filters trades < N minutes. EA must prevent voluntary
    #      early exits (trailing, time, indicator) but allow SL/TP to work normally.
    # CHANGED: April 2026 — conditional code generation
    min_hold_check = ''
    if min_hold_minutes > 0:
        min_hold_check = f'''//+------------------------------------------------------------------+
//| Check if position meets minimum hold time                          |
//| WHY: Backtester post-filters trades < N minutes. EA must prevent  |
//|      voluntary early exits (trailing, time, indicator) but allow  |
//|      SL/TP to work normally.                                       |
//| CHANGED: April 2026 — MinHoldMinutes enforcement                  |
//+------------------------------------------------------------------+
bool IsMinHoldMet()
{{
   if(MinHoldMinutes <= 0) return true;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {{
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      datetime now = TimeCurrent();
      int holdSec = (int)(now - openTime);

      if(holdSec < MinHoldMinutes * 60)
      {{
         // At least one position hasn't met min hold time
         return false;
      }}
   }}

   return true;  // All positions met min hold OR no positions open
}}

'''

    # ── Build verification report for EA header ──
    # WHY: User needs to verify at a glance that every rule, condition,
    #      exit param, and filter was correctly translated to MQL5.
    # CHANGED: April 2026
    _vr = []
    _vr.append("STRATEGY VERIFICATION REPORT")
    _vr.append(f"Generated: {generated_at}")
    _vr.append("")
    _vr.append(f"ENTRY: {len(win_rules)} rules ({'OR logic' if len(win_rules) > 1 else 'single rule'}), Direction: {_direction_label}, TF: {entry_timeframe}")
    _vr.append("")
    for _i, _r in enumerate(win_rules, 1):
        _cs = _r.get('conditions', [])
        _vr.append(f"Rule {_i}: {_direction_label} when ALL of:")
        for _c in _cs:
            _vr.append(f"  {_c.get('feature','?')} {_c.get('operator','>')} {_c.get('value',0):.6f}")
        _vr.append("")
    _vr.append(f"EXIT: {exit_class}  SL={sl_pips}  TP={tp_pips}")
    if exit_class == 'TrailingStop':
        _vr.append(f"  Activation: +{trail_activation_pips} pips, Trail: {trail_distance_pips} pips")
    elif exit_class in ('ATRBased', 'ATROnly', 'ATRTrailing'):
        _vr.append(f"  SL: {sl_atr_mult}x ATR, TP: {tp_atr_mult}x ATR")
    elif exit_class == 'TimeBased':
        _vr.append(f"  Max hold: {max_candles} candles")
    elif exit_class == 'HybridExit':
        _vr.append(f"  Breakeven: +{breakeven_pips}, Trail: {trail_distance_pips}, Max: {max_candles} candles")
    _vr.append("")
    if leverage > 0:
        try:
            from shared.prop_firm_engine import get_instrument_type as _vr_git
            _vr_inst = _vr_git(symbol)
        except Exception:
            _vr_inst = 'metals'
        _vr_contract = 100.0 if _vr_inst == 'metals' else (1.0 if _vr_inst == 'indices' else 100000.0)
        _vr.append(f"LEVERAGE: 1:{leverage} ({_vr_inst})  |  Contract size: {_vr_contract}")
        _vr.append("")
    _vr.append(f"FILTERS: max_trades/day={max_trades_per_day}, min_hold={min_hold_minutes}min, cooldown={cooldown_minutes}min")
    _vr.append(f"  Sessions: {session_comment}  |  Days: {day_comment}")
    _vr.append(f"  Max spread: {max_spread_pips} pips  |  News: {news_filter_minutes}min")
    _vr.append("")
    _vr.append(f"SETTINGS: {symbol}, Risk {risk_per_trade_pct}%, Account ${account_size:,.0f}, Magic {magic_number}")
    _vr.append(f"  Firm: {prop_firm_name} ({stage}), DD: {dd_daily_pct}%/{dd_total_pct}%")
    if stage == 'evaluation':
        _vr.append("")
        _vr.append("EVAL RULES (IMPORTANT):")
        _vr.append("  * Hit profit target -> STOP IMMEDIATELY (don't overtrade)")
        _vr.append("  * Trailing DD floor RISES with equity -- profits tighten the rope")
        _vr.append("  * No payout cycle in eval -- pass the eval, then move to funded")
        _vr.append("  * Total DD breach -> PERMANENT STOP (no payout recovery in eval)")
        _vr.append("  * Daily DD alert: stop BEFORE limit (save the account)")
        _vr.append("")
    _vr.append(f"  Validation: Grade {grade} ({score}/100)")
    _vr.append(f"  Backtest: WR {base_stats.get('win_rate',0)*100:.1f}%, PF {base_stats.get('profit_factor',0):.2f}, {base_stats.get('total_pips',0):+,.0f} pips")
    # Warnings
    _vrw = []
    if not win_rules: _vrw.append("NO RULES — EA will never trade!")
    if exit_class == 'FixedSLTP' and exit_name not in ('FixedSLTP', 'Fixed SL/TP'): _vrw.append(f"Exit defaulted to FixedSLTP but strategy uses '{exit_name}'")
    if min_hold_minutes == 0: _vrw.append("No min hold — scalping trades included")
    if _vrw:
        _vr.append("")
        _vr.append("WARNINGS:")
        for _w in _vrw: _vr.append(f"  ⚠ {_w}")

    _vr_header = '//+------------------------------------------------------------------+\n'
    for _l in _vr:
        _vr_header += f'//| {_l:<66} |\n'
    _vr_header += '//+------------------------------------------------------------------+\n'

    # ── Build indicator release block for OnDeinit ────────────────────────
    # WHY: Old code called IndicatorRelease(0). 0 is INVALID_HANDLE — does
    #      nothing. Handles leak until EA restart. Fix: release all actual
    #      handles created by the EA.
    # CHANGED: April 2026 — explicit per-handle release in OnDeinit
    _release_lines = []
    _seen_handles = set()

    # Release entry condition handles
    for h in handles:
        hv = h.get('handle_var', '').strip().rstrip(';').strip()
        if hv:
            # Extract actual handle name: "int handle_macd_H4" → "handle_macd_H4"
            hname = hv.split()[-1] if hv else ''
            if hname and hname not in _seen_handles:
                _seen_handles.add(hname)
                _release_lines.append(f'   if({hname} != INVALID_HANDLE) IndicatorRelease({hname});')

    # Release regime filter handles
    if regime_conditions:
        for rcond in regime_conditions:
            _feat = rcond.get('feature', '')
            if _feat:
                try:
                    _mql = get_mql_code(_feat, 'mt5')
                    hv = _mql.get('handle_var', '').strip().rstrip(';').strip()
                    if hv:
                        hname = hv.split()[-1] if hv else ''
                        if hname and hname not in _seen_handles:
                            _seen_handles.add(hname)
                            _release_lines.append(f'   if({hname} != INVALID_HANDLE) IndicatorRelease({hname});')
                except Exception:
                    pass

    _indicator_release_block = '\n'.join(_release_lines) if _release_lines else '   // No indicator handles to release'

    # WHY: Exit-strategy handles (ATR, IndicatorExit) are registered via exit_globals
    #      and extra_init, completely outside the `handles` list that the release-block
    #      builder above iterates. They would be silently skipped, leaking the handle
    #      until the terminal restarts. Append them directly to the built block string.
    # CHANGED: April 2026 — fix exit-handle leaks in OnDeinit

    if exit_class in ('ATRBased', 'ATROnly', 'ATRTrailing'):
        _indicator_release_block += '\n   if(handle_exit_atr != INVALID_HANDLE) IndicatorRelease(handle_exit_atr);'

    if exit_class == 'IndicatorExit' and exit_globals.strip():
        # exit_globals first line = IndicatorExit handle declaration (regime handles follow after, already released above)
        _ind_hv_raw = exit_globals.strip().split('\n')[0].rstrip(';').strip()
        _ind_hname  = _ind_hv_raw.split()[-1] if _ind_hv_raw and ' ' in _ind_hv_raw else ''
        if _ind_hname and _ind_hname not in _seen_handles:
            _indicator_release_block += f'\n   if({_ind_hname} != INVALID_HANDLE) IndicatorRelease({_ind_hname});'

    # WHY: ATR-based exit uses ATR × mult for SL, not fixed SLPips.
    #      Lot sizing must match the ACTUAL SL, not the configured one.
    #      Without this, lots are 20× too big and one loss = 6% DD.
    # CHANGED: April 2026 — ATR-aware lot sizing
    if exit_class in ('ATRBased', 'ATROnly', 'ATRTrailing'):
        _lot_sizing_code = (
            '   double pipSize = GetPipSize();\n'
            '   // ATR-aware: size lots for ACTUAL ATR SL, not fixed SLPips\n'
            '   double _atrSizeBuf[1];\n'
            '   CopyBuffer(handle_exit_atr, 0, 1, 1, _atrSizeBuf);  // shift=1: last closed bar, matches Python training\n'
            '   double sl = _atrSizeBuf[0] * SL_ATR_Mult;  // ATR SL in price units\n'
            '   double tp = _atrSizeBuf[0] * TP_ATR_Mult;\n'
            '   double lots = CalculateLots(sl);\n'
            '   if(lots <= 0.0) return;\n'
        )
    else:
        _lot_sizing_code = (
            '   double pipSize = GetPipSize();\n'
            '   double sl = SLPips * pipSize;\n'
            '   double tp = TPPips * pipSize;\n'
            '   double lots = CalculateLots(sl);\n'
            '   if(lots <= 0.0) return;\n'
        )

    # ── Trailing inputs: editable for trailing exits, locked constants otherwise ──
    # WHY: For non-trailing exits (ATROnly, FixedSLTP, TimeBased, IndicatorExit)
    #      the MT5 Strategy Tester shows every `input` parameter and lets the user
    #      type any value. If TrailDistance is an input defaulting to 0, the user
    #      can change it to 100 and ManageTrailingStop() will fire every tick —
    #      silently overriding the ATR/fixed SL with an unintended trailing stop.
    #      Declaring them as `const double` keeps them in the code (so the
    #      ManageTrailingStop() function compiles) but hides them from the tester.
    # CHANGED: April 2026 — lock trailing inputs for non-trailing exits
    if _has_trailing:
        _trail_inputs_block = (
            f'input double TrailActivation    = {trail_activation_pips};   '
            f'// Activate trailing after this profit (pips)\n'
            f'input double TrailDistance      = {trail_distance_pips};     '
            f'// Trailing distance behind price (pips, 0=off)'
        )
        _manage_trail_call = (
            ('if(IsMinHoldMet()) ' if min_hold_minutes > 0 else '')
            + 'ManageTrailingStop();'
        )
    else:
        _trail_inputs_block = (
            f'// Trailing disabled for {exit_class} — SL/TP managed by the exit strategy.\n'
            f'// Declared as const so ManageTrailingStop() compiles but never runs.\n'
            f'const double TrailActivation    = 0;\n'
            f'const double TrailDistance      = 0;'
        )
        _manage_trail_call = (
            '// ManageTrailingStop() suppressed: trailing not used for '
            + exit_class
        )

    # DD floor behavior: eval = log once + continue, funded = close + halt
    if stage == 'evaluation':
        _dd_floor_action = (
            '            " — WARNING: DD floor breached, trading continues.");\n'
            '      g_ddFloor = 0.0;  // Clear floor so this warning only fires once\n'
        )
    else:
        _dd_floor_action = (
            '            " — closing all positions and halting.");\n'
            '      CloseAllPositions("DDFloorBreach");\n'
            '      g_stopForever = true;\n'
            '      SaveDDState();\n'
            '      SendNotification("[DD BREACH] " + _Symbol + " — Account protection triggered. Bot halted.");\n'
            '      SendMail("[DD BREACH] " + _Symbol,\n'
            '               "Equity $" + DoubleToString(equity, 2) +\n'
            '               " dropped below DD floor $" + DoubleToString(g_ddFloor, 2) +\n'
            '               ". All positions closed. Bot halted permanently.\\n\\n" +\n'
            '               "Restart the EA manually after investigating the cause.");\n'
            '      return;\n'
        )

    code = f"""\
{_vr_header}\
#property copyright "Generated by Trade Bot"
#property version   "1.00"
#property strict

#include <Trade\\Trade.mqh>
#include <Trade\\PositionInfo.mqh>

//--- Input parameters
input double RiskPercent        = {risk_per_trade_pct};     // Risk per trade % (capped for leverage)
input int    Leverage           = {leverage};                // Account leverage for this instrument (0=not set)
input int    MaxTradesPerDay    = {max_trades_per_day};      // Max trades per day
input int    MagicNumber        = {magic_number};            // Magic number
input double MaxSpreadPips      = {max_spread_pips};         // Max spread to allow entry
input int    CooldownMinutes    = {cooldown_minutes};        // Min minutes between trades
input int    MinHoldMinutes     = {min_hold_minutes};        // Min hold time
input bool   UseNewsFilter      = {'true' if news_filter_minutes > 0 else 'false'};  // Skip trading around news
input int    NewsFilterMinutes  = {news_filter_minutes};     // Minutes before/after news
input bool   UsePropFirmMode    = true;                      // Enable prop firm safety
input double DailyDDLimitPct    = {dd_daily_pct};           // Daily DD blow limit % (firm closes account here)
input double TotalDDLimitPct    = {dd_total_pct};           // Total DD blow limit % (firm closes account here)
input bool   LogTrades          = true;                      // Log trades to CSV
input string LogFilePath        = "trades_log_{magic_number}.csv"; // Log file path
// WHY: The value is computed as a GMT hour in Python (line ~809), so we
//      must compare against TimeGMT(), not TimeCurrent() (server time).
//      Old name "Server" was misleading — a Cyprus broker's server time
//      is GMT+3, so the reset fired 3 hours early every day.
// CHANGED: April 2026 — fix reset hour timezone (audit bug #6)
input int    DailyResetHourGMT    = {reset_hour_gmt};        // Daily reset hour (GMT — already DST-adjusted by firm rules)
input int    DailyResetMinute     = 0;                       // Daily reset minute
//--- Exit parameters
input double SLPips             = {sl_pips};                 // Stop loss (pips)
input double TPPips             = {tp_pips};                 // Take profit (pips)
{_trail_inputs_block}
{exit_inputs}
//--- Entry rule thresholds (one per condition — tweak without recompiling)
{conditions_block}
{extra_inputs_block}

//--- Global variables
CTrade         trade;
CPositionInfo  pos;
{handle_vars}

int    g_dailyTrades     = 0;
double g_sessionEquity   = 0.0;
double g_dailyHighEquity = 0.0;
bool   g_stopForDay      = false;
bool   g_stopForever     = false;
bool   g_dailyResetDone  = false;   // DST-safe: prevents double-reset within same hour
datetime g_lastTradeTime = 0;
datetime g_lastBarTime   = 0;
int    g_logHandle       = INVALID_HANDLE;
{extra_globals_block}
{exit_globals}

//+------------------------------------------------------------------+
//| Expert initialization                                              |
//+------------------------------------------------------------------+
int OnInit()
{{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);

   //--- Create indicator handles
   {handle_inits}

   //--- Open log file
   if(LogTrades)
   {{
      g_logHandle = FileOpen(LogFilePath, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
      if(g_logHandle != INVALID_HANDLE)
         FileWrite(g_logHandle, "timestamp","symbol","direction","lots",
                   "entry_price","exit_price","net_pips","exit_reason",
                   "entry_time","exit_time","skip_reason");
   }}

   g_sessionEquity   = AccountInfoDouble(ACCOUNT_EQUITY);
   g_dailyHighEquity = g_sessionEquity;
{extra_init_block}
   Print("[EA] Started. Magic=", MagicNumber, " Equity=", g_sessionEquity);
   return(INIT_SUCCEEDED);
}}

//+------------------------------------------------------------------+
//| Expert deinitialization                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{{
   if(g_logHandle != INVALID_HANDLE)
      FileClose(g_logHandle);

   // Release all indicator handles
{_indicator_release_block}

   Print("[EA] Stopped. Reason=", reason);
}}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{{
   if(g_stopForever) return;

   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);

   //--- Update daily high equity
   if(equity > g_dailyHighEquity) g_dailyHighEquity = equity;

   //--- DD floor enforcement — fires every tick, before anything else
   if(g_ddFloor > 0.0 && equity < g_ddFloor)
   {{
      Print("[DD BREACH] Equity $", DoubleToString(equity, 2),
            " dropped below floor $", DoubleToString(g_ddFloor, 2),
{_dd_floor_action}\
   }}

   //--- DST-safe daily reset — MUST run before g_stopForDay guard
   // WHY: g_stopForDay blocks everything below it. If the daily reset
   //      was placed after the guard, g_stopForDay would never be cleared
   //      and the EA would stay stopped permanently after the first DD day.
   // CHANGED: April 2026 — moved daily reset above g_stopForDay guard
   MqlDateTime _now_gmt;
   TimeToStruct(TimeGMT(), _now_gmt);
   if(_now_gmt.hour == DailyResetHourGMT && _now_gmt.min >= DailyResetMinute
      && !g_dailyResetDone)
   {{
      g_dailyResetDone  = true;
      g_dailyTrades     = 0;
      g_stopForDay      = false;
      g_dailyHighEquity = equity;
      // NOTE: g_sessionEquity is reset AFTER extra_daily_reset_block so that
      //       profit-day counters still read yesterday's baseline correctly.
{extra_daily_reset_block}
      g_sessionEquity   = equity;   // NOW reset: tomorrow's baseline
      Print("[DD] Daily reset at ", DailyResetHourGMT, ":", DailyResetMinute,
            " GMT. Equity=$", DoubleToString(equity, 2));
   }}
   if(_now_gmt.hour != DailyResetHourGMT)
      g_dailyResetDone = false;   // re-arm for next day

   if(g_stopForDay) return;

   // WHY: Trailing stop must be checked every tick, not just on new bars.
   //      Price can hit the trail level between bars.
   // CHANGED: April 2026 — trailing stop management
   {_manage_trail_call}

   // WHY: Exit-specific management (time-based, indicator-based, hybrid).
   //      Must be checked every tick for time/indicator exits.
   // CHANGED: April 2026 — all exit strategies supported
{exit_management}

   //--- Per-tick safety checks — run BEFORE the new-bar gate
   // WHY: Daily DD alerts, total DD alerts, emergency stop, and payout
   //      confirmation are per-tick safety checks. If they only fire on
   //      bar close, an H1 EA can be up to 60 minutes late reacting to
   //      a DD breach — the broker closes the account first. Moved
   //      above the new-bar gate so they run on every tick.
   // CHANGED: April 2026 — fix per-bar safety delay (audit bug #5)
{extra_tick_checks_block}

   //--- Check for new bar
   datetime currentBarTime = iTime(_Symbol, {mql_period}, 0);
   if(currentBarTime == g_lastBarTime) return;
   g_lastBarTime = currentBarTime;

   //--- Skip checks
   double spreadPips = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) / 10.0;
   if(spreadPips > MaxSpreadPips)
   {{ LogSkip("spread_too_wide", spreadPips); return; }}

   // WHY: MaxTradesPerDay=0 means unlimited. Old code checked
   //      g_dailyTrades >= 0 which is always true → blocked all trades.
   // CHANGED: April 2026 — 0 = unlimited
   if(MaxTradesPerDay > 0 && g_dailyTrades >= MaxTradesPerDay)
   {{ LogSkip("max_trades_per_day", g_dailyTrades); return; }}

   if(TimeCurrent() - g_lastTradeTime < CooldownMinutes * 60)
   {{ LogSkip("cooldown", (TimeCurrent()-g_lastTradeTime)/60.0); return; }}

   if(!CheckSession())
   {{ LogSkip("outside_session", 0); return; }}

   // WHY: _now_gmt is in scope here (declared above in daily-reset block).
   //      Use GMT time for day-of-week logging. CheckDayFilter() internally
   //      uses TimeCurrent() for the real filter, so the log value is cosmetic.
   // CHANGED: April 2026 — use GMT time consistently (audit bug #6 follow-up)
   if(!CheckDayFilter())
   {{ LogSkip("day_filtered", (double)_now_gmt.day_of_week); return; }}

   if(UseNewsFilter && IsNewsImminent())
   {{ LogSkip("news_filter", 0); return; }}

   //--- Check entry conditions
   // WHY: Any indicator that returns EMPTY_VALUE means it's not ready or
   //      failed to read. Skip this signal entirely instead of trading on
   //      garbage data.
   // CHANGED: April 2026 — short-circuit on indicator failure
   bool entrySignal = true;
   bool indicatorFailed = false;

{regime_check_block}
{conditions_check_block}

   if(indicatorFailed) {{ LogSkip("indicator_not_ready", 0); return; }}
   if(!entrySignal) return;

   //--- No existing position with our magic
   // WHY: PositionSelectByTicket(0) always fails — ticket 0 doesn't exist.
   //      Must loop through all positions and check our MagicNumber.
   //      Without this, EA opens multiple trades simultaneously = 2-3x risk.
   // CHANGED: April 2026 — correct position check
   for(int _pi = PositionsTotal() - 1; _pi >= 0; _pi--)
   {{
      ulong _ticket = PositionGetTicket(_pi);
      if(_ticket > 0 && PositionGetInteger(POSITION_MAGIC) == MagicNumber
         && PositionGetString(POSITION_SYMBOL) == _Symbol)
      {{
         return; // already have an open position
      }}
   }}

   //--- Position sizing
   // WHY: GetPipSize() detects instrument-specific pip size (5-digit forex vs
   //      4-digit vs metals etc.) rather than hardcoding _Point * 10.
   // CHANGED: April 2026 — proper pip size + atomic SL/TP
{_lot_sizing_code}

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double slPrice = NormalizeDouble({_sl_price_expr}, _Digits);
   // WHY: TPPips=0 means "no take profit" (TimeBased, IndicatorExit).
   //      Passing 0 to trade.Buy/Sell = no TP on the order.
   //      Old code computed ask+0 = entry price = instant TP close.
   // CHANGED: April 2026 — handle no-TP exits
   double tpPrice = (TPPips > 0) ? NormalizeDouble({_tp_price_expr}, _Digits) : 0;

   //--- Place order WITH SL and TP attached
   if({_entry_call})
   {{
      double entryPrice = trade.ResultPrice();
{exit_on_entry_block}
      g_dailyTrades++;
      g_lastTradeTime = TimeCurrent();
      LogTrade("OPEN", "{_direction_label}", lots, entryPrice, 0, 0, "entry_signal");
      Print("[EA] {_direction_label} opened @ ", entryPrice, " lots=", lots);
   }}
}}

//+------------------------------------------------------------------+
//| Get pip size in price units (handles all instrument types)        |
//| WHY: _Point * 10 hardcoded only works for 5-digit forex. Metals, |
//|      JPY pairs, and indices all have different digit counts.      |
//| CHANGED: April 2026 — proper instrument-aware pip detection       |
//+------------------------------------------------------------------+
double GetPipSize()
{{
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point  = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   // 3 or 5 digit pricing: pip = 10 points; 2 or 4 digit: pip = 1 point
   return (digits == 3 || digits == 5) ? point * 10.0 : point;
}}

//+------------------------------------------------------------------+
//| Global: Entry timeframe for shift calculation                     |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES g_entryTF = {mql_period};

//+------------------------------------------------------------------+
//| Calculate correct bar shift for multi-timeframe indicators        |
//| WHY: ALL timeframes use shift=1 (previous completed bar).         |
//|      Python backtester shifts higher-TF timestamps forward so     |
//|      merge_asof picks the last COMPLETED bar. Using shift=1 here  |
//|      achieves the same result: iCustom reads the prior closed bar.|
//| CHANGED: April 2026 — shift=1 for all TFs to match Python fix    |
//+------------------------------------------------------------------+
int GetBarShift(ENUM_TIMEFRAMES indicatorTF)
{{
   return 1;
}}

//+------------------------------------------------------------------+
//| SafeCopyBuffer — wrapper with shift=1 for all timeframes          |
//| WHY: All indicator reads use shift=1 (previous completed bar)     |
//|      to match the Python backtester's look-ahead prevention.      |
//| CHANGED: April 2026 — uniform shift=1 for EA/Python parity       |
//+------------------------------------------------------------------+
double SafeCopyBuf(int handle, int bufNum, ENUM_TIMEFRAMES indicatorTF)
{{
   if(handle == INVALID_HANDLE) return EMPTY_VALUE;
   double tmp[1];
   int shift = GetBarShift(indicatorTF);
   int copied = CopyBuffer(handle, bufNum, shift, 1, tmp);
   if(copied <= 0) return EMPTY_VALUE;
   return tmp[0];
}}

//+------------------------------------------------------------------+
//| Calculate position size from risk %                                |
//+------------------------------------------------------------------+
double CalculateLots(double slDistance)
{{
   //--- Read account and symbol info
   double equity     = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskAmount = equity * RiskPercent / 100.0;
   double tickValue  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double lotStep    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot     = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot     = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   //--- Validate symbol info
   // WHY: Without these checks, bad symbol info silently returned minLot
   //      and the user never knew their risk model was broken.
   // CHANGED: April 2026 — validation + diagnostic logging
   if(tickValue <= 0 || tickSize <= 0 || slDistance <= 0 || lotStep <= 0)
   {{
      Print("[LOTS] WARNING: invalid symbol info"
            " tickValue=", tickValue, " tickSize=", tickSize,
            " slDistance=", slDistance, " lotStep=", lotStep, ". Using minLot.");
      return minLot;
   }}

   //--- Risk-based sizing: lots = riskAmount / ((slDistance / tickSize) * tickValue)
   double lotsRaw     = riskAmount / ((slDistance / tickSize) * tickValue);
   double lotsRounded = MathFloor(lotsRaw / lotStep) * lotStep;

   //--- Apply broker limits with diagnostic logging
   double lots       = lotsRounded;
   if(lots < minLot)
   {{
      Print("[LOTS] Risk requested ", DoubleToString(lotsRaw, 4),
            " lots but minLot=", minLot, " — risking MORE than configured!");
      lots = minLot;
   }}
   if(lots > maxLot)
   {{
      Print("[LOTS] Risk requested ", DoubleToString(lotsRaw, 4),
            " lots but maxLot=", maxLot, " — risking LESS than configured.");
      lots = maxLot;
   }}

   //--- Margin check: ask broker if account can hold this position
   // WHY: Risk-based sizing can exceed account margin. A $10K account
   //      at 1:10 leverage on XAUUSD can hold ~0.18 lots. Without this,
   //      trade.Buy() fails with "not enough money" every signal.
   //      OrderCalcMargin asks the broker directly — not a guess.
   // CHANGED: April 2026 — margin-aware lot sizing
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double marginNeeded = 0.0;
   if(OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lots,
                       SymbolInfoDouble(_Symbol, SYMBOL_ASK), marginNeeded))
   {{
      if(marginNeeded > freeMargin * 0.90)
      {{
         double maxByMargin = lots * (freeMargin * 0.90) / marginNeeded;
         maxByMargin = MathFloor(maxByMargin / lotStep) * lotStep;
         if(maxByMargin < minLot)
         {{
            Print("[LOTS] MARGIN BLOCK: need $", DoubleToString(marginNeeded, 0),
                  " but only $", DoubleToString(freeMargin, 0),
                  " free. Even minLot exceeds margin. Skipping trade.");
            return 0.0;
         }}
         Print("[LOTS] Margin cap: ", DoubleToString(lots, 2), " -> ",
               DoubleToString(maxByMargin, 2), " lots (free=$",
               DoubleToString(freeMargin, 0), ", need=$",
               DoubleToString(marginNeeded, 0), ")");
         lots = maxByMargin;
      }}
   }}

   //--- Auto-detect decimal places from lotStep
   int decimals = (lotStep >= 1.0) ? 0 : (lotStep >= 0.1) ? 1 : (lotStep >= 0.01) ? 2 : 3;
   return NormalizeDouble(lots, decimals);
}}

{min_hold_check}//+------------------------------------------------------------------+
//| Manage trailing stop on open position                              |
//| WHY: TrailingStop has two thresholds:                              |
//|      - activation: profit needed before trailing starts            |
//|      - distance: how far behind price the SL stays                 |
//| CHANGED: April 2026 — separate activation and distance             |
//+------------------------------------------------------------------+
void ManageTrailingStop()
{{
   if(TrailDistance <= 0) return; // trailing stop disabled

   double trailDistance = TrailDistance * GetPipSize();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {{
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      // Per-position min hold check: skip trailing if this position hasn't aged enough
      if(MinHoldMinutes > 0)
      {{
         datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
         int holdSec = (int)(TimeCurrent() - openTime);
         if(holdSec < MinHoldMinutes * 60) continue;  // Skip this position
      }}

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);
      long   posType   = PositionGetInteger(POSITION_TYPE);
      double bid       = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask       = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      if(posType == POSITION_TYPE_BUY)
      {{
         // Only trail if price has moved past activation threshold
         double profitPips = (bid - openPrice) / GetPipSize();
         if(profitPips >= TrailActivation)
         {{
            double newSL = bid - trailDistance;
            // Only move SL up, never down (|| currentSL==0 handles positions opened without SL)
            // WHY: SELL has "|| currentSL==0" fallback; BUY needs it too for symmetry.
            // CHANGED: April 2026 — symmetric trail fallback (audit HIGH)
            if(newSL > currentSL + _Point || currentSL == 0)
            {{
               trade.PositionModify(ticket, newSL, currentTP);
            }}
         }}
      }}
      else if(posType == POSITION_TYPE_SELL)
      {{
         double profitPips = (openPrice - ask) / GetPipSize();
         if(profitPips >= TrailActivation)
         {{
            double newSL = ask + trailDistance;
            if(newSL < currentSL - _Point || currentSL == 0)
            {{
               trade.PositionModify(ticket, newSL, currentTP);
            }}
         }}
      }}
   }}
}}

//+------------------------------------------------------------------+
//| Close all open positions                                           |
//+------------------------------------------------------------------+
void CloseAllPositions(string reason)
{{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {{
      ulong ticket = PositionGetTicket(i);
      if(PositionGetInteger(POSITION_MAGIC) == MagicNumber)
      {{
         trade.PositionClose(ticket);
         Print("[EA] Closed position ", ticket, " reason=", reason);
      }}
   }}
}}

//+------------------------------------------------------------------+
//| Session filter                                                     |
//+------------------------------------------------------------------+
bool CheckSession()
{{
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   int hour = dt.hour;
   // Sessions: {session_comment}
   {session_code}
}}

//+------------------------------------------------------------------+
//| Day of week filter                                                 |
//+------------------------------------------------------------------+
bool CheckDayFilter()
{{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int dow = dt.day_of_week;
   // Days: {day_comment}
   {day_code}
}}

//+------------------------------------------------------------------+
//| News imminent check (reads news_calendar.csv)                      |
//+------------------------------------------------------------------+
bool IsNewsImminent()
{{
   // Read news_calendar.csv from MQL5 Files folder
   string newsFile = "news_calendar.csv";
   if(!FileIsExist(newsFile)) return false;

   int fh = FileOpen(newsFile, FILE_READ|FILE_CSV|FILE_ANSI, ',');
   if(fh == INVALID_HANDLE) return false;

   datetime now = TimeCurrent();
   datetime window = NewsFilterMinutes * 60;
   bool found = false;

   FileReadString(fh); // skip header
   while(!FileIsEnding(fh) && !found)
   {{
      string dtStr  = FileReadString(fh);
      string cur    = FileReadString(fh);
      string ev     = FileReadString(fh);
      string impact = FileReadString(fh);
      if(impact != "HIGH") continue;
      // Parse datetime (YYYY-MM-DDTHH:MM:SS)
      datetime evTime = StringToTime(dtStr);
      if(MathAbs((double)(evTime - now)) < (double)window) found = true;
   }}
   FileClose(fh);
   return found;
}}

//+------------------------------------------------------------------+
//| Log a trade to CSV                                                 |
//+------------------------------------------------------------------+
void LogTrade(string action, string dir, double lots, double entry, double exitP, double pips, string reason)
{{
   if(!LogTrades || g_logHandle == INVALID_HANDLE) return;
   FileWrite(g_logHandle,
      TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES),
      _Symbol, dir, DoubleToString(lots,2),
      DoubleToString(entry,5), DoubleToString(exitP,5),
      DoubleToString(pips,1), reason,
      TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES), "", "");
   FileFlush(g_logHandle);
}}

//+------------------------------------------------------------------+
//| Log a skipped signal                                               |
//+------------------------------------------------------------------+
void LogSkip(string reason, double val)
{{
   if(!LogTrades || g_logHandle == INVALID_HANDLE) return;
   FileWrite(g_logHandle,
      TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES),
      _Symbol, "SKIP", "0", "0", "0", "0", "",
      TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES), "", reason);
   FileFlush(g_logHandle);
}}
//+------------------------------------------------------------------+
{extra_functions_block}
"""
    return code


# ─────────────────────────────────────────────────────────────────────────────
# Tradovate Python Bot Generator
# ─────────────────────────────────────────────────────────────────────────────

def _generate_tradovate(win_rules, exit_name, exit_params, symbol, magic_number,
                        risk_per_trade_pct, max_trades_per_day, session_filter,
                        day_filter, cooldown_minutes, news_filter_minutes,
                        max_spread_pips, dd_daily_pct, dd_total_pct, dd_safety_pct,
                        grade, score, base_stats,
                        direction='BUY',           # NEW
                        entry_timeframe='H1'):     # NEW
    """Generate Tradovate Python bot. direction must be 'BUY' or 'SELL'."""
    if direction not in ('BUY', 'SELL'):
        raise ValueError(f"_generate_tradovate: direction must be BUY or SELL, got {direction!r}")

    # Map entry_timeframe to Tradovate minute interval + dataframe variable
    _tf_intervals = {'M5': 5, 'M15': 15, 'H1': 60, 'H4': 240, 'D1': 1440}
    tf_minutes = _tf_intervals.get(entry_timeframe, 60)
    tf_df_var  = f'df_m{tf_minutes}'

    # Tradovate API uses "Buy"/"Sell" capitalization
    tradovate_action = 'Buy' if direction == 'BUY' else 'Sell'

    handles  = get_all_handles_for_rules(win_rules, platform='tradovate')
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    sl_pips  = exit_params.get('sl_pips', 150)
    tp_pips  = exit_params.get('tp_pips', 300)

    # WHY: Pip values from backtest are already correct for all instruments.
    #      Old 10x metals scaling removed — it caused SL/TP mismatch.
    # CHANGED: April 2026 — removed metals 10x scaling (was wrong)

    # Build condition checks
    indicator_lines = []
    condition_lines = []
    for ri, rule in enumerate(win_rules, 1):
        for ci, cond in enumerate(rule.get('conditions', []), 1):
            feat = cond.get('feature', '')
            op   = cond.get('operator', '>')
            val  = cond.get('value', 0)
            tv   = get_mql_code(feat, 'tradovate')
            var_n = tv['var_name']
            # WHY: 8-space indent so these lines sit inside the try: block
            #      in check_entry_conditions (try: is at 4-space indent).
            #      Old 4-space indent placed them outside the try — unhandled
            #      exceptions from indicator computation would crash the thread.
            # CHANGED: April 2026 — fix indicator/condition indent in try block
            indicator_lines.append(f'        {tv.get("python_code", f"val_{var_n} = 0.0")}  # {feat}')
            mql_op = OPERATOR_MAP_PY.get(op, '>')
            condition_lines.append(f'        if not (val_{var_n} {mql_op} {val}):')
            condition_lines.append(f'            return False  # Rule {ri} cond {ci}: {feat} {op} {val:.4f}')

    indicator_block   = '\n'.join(indicator_lines) or '        pass'
    condition_block   = '\n'.join(condition_lines) or '        pass'

    code = f'''\
#!/usr/bin/env python3
"""
Tradovate Bot — Generated by Trade Bot
Strategy: {exit_name}
Generated: {generated_at}
Validation: Grade {grade} ({score}/100)
Backtest: WR {base_stats.get("win_rate", 0)*100:.1f}%, {base_stats.get("total_pips", 0):.0f} net pips

Requires:
  pip install pandas pandas-ta websockets requests

Edit config.json with your Tradovate API credentials before running.
Run:  python bot_main.py
"""

import asyncio
import json
import time
import csv
import os
import threading
import datetime
import pandas as pd

try:
    import pandas_ta as ta
except ImportError:
    print("ERROR: Install pandas_ta: pip install pandas-ta")
    raise

# ── Configuration ──────────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
LOG_PATH    = os.path.join(os.path.dirname(__file__), "trades_log_{magic_number}.csv")

with open(CONFIG_PATH) as f:
    config = json.load(f)

SYMBOL             = config.get("symbol", "{symbol}")
RISK_PCT           = config.get("risk_pct", {risk_per_trade_pct})
MAX_TRADES_PER_DAY = config.get("max_trades_per_day", {max_trades_per_day})
COOLDOWN_MINUTES   = config.get("cooldown_minutes", {cooldown_minutes})
SL_PIPS            = config.get("sl_pips", {sl_pips})
TP_PIPS            = config.get("tp_pips", {tp_pips})
DD_DAILY_PCT       = config.get("dd_daily_pct", {dd_daily_pct})
DD_TOTAL_PCT       = config.get("dd_total_pct", {dd_total_pct})
DD_SAFETY_PCT      = config.get("dd_safety_pct", {dd_safety_pct})
MAX_SPREAD_PIPS    = config.get("max_spread_pips", {max_spread_pips})

# ── State ───────────────────────────────────────────────────────────────────
# WHY: session_equity tracks the reference for daily DD. It must be
#      refreshed at every day rollover, not set once at startup.
#      session_date lets us detect the rollover via UTC date change.
# CHANGED: April 2026 — track day rollover (audit HIGH #33)
daily_trades      = 0
last_trade_time   = None
session_equity    = None
session_date      = None    # UTC date when session_equity was last set
stop_for_day      = False
stop_forever      = False
# WHY: HWM tracks the peak equity across the entire account lifetime.
#      Used for trailing total DD + lock-at-starting-balance rule.
#      Populated on first check_drawdown call.
# CHANGED: April 2026 — add HWM state (audit HIGH #34)
account_hwm       = None   # highest equity seen so far
hwm_locked        = False  # True after +6% gain → DD floor frozen
starting_equity   = None   # captured at first check_drawdown call

# DataFrame buffers per timeframe (populated from Tradovate WebSocket)
df_m5   = pd.DataFrame(columns=["open","high","low","close","volume"])
df_m15  = pd.DataFrame(columns=["open","high","low","close","volume"])
df_m60  = pd.DataFrame(columns=["open","high","low","close","volume"])
df_m240 = pd.DataFrame(columns=["open","high","low","close","volume"])
df_m1440= pd.DataFrame(columns=["open","high","low","close","volume"])

# ── Trade log ───────────────────────────────────────────────────────────────
_log_file = open(LOG_PATH, "a", newline="", buffering=1, encoding="utf-8")
_log_writer = csv.writer(_log_file)
if os.path.getsize(LOG_PATH) == 0:
    _log_writer.writerow(["timestamp","symbol","direction","lots","entry_price",
                          "exit_price","net_pips","exit_reason","entry_time","exit_time","skip_reason"])

def log_trade(direction, lots, entry, exit_p, pips, reason, skip=""):
    _log_writer.writerow([datetime.datetime.utcnow().isoformat(), SYMBOL,
                          direction, lots, entry, exit_p, pips, reason,
                          datetime.datetime.utcnow().isoformat(), "", skip])
    _log_file.flush()

# ── Risk Manager ────────────────────────────────────────────────────────────
# WHY: Old version had three bugs:
#      1. session_equity set once, never refreshed on day rollover
#         → daily DD reference permanently anchored to bot startup equity
#      2. total_dd = session_equity - current_equity with no HWM tracking
#         → profitable bot that later drew down would NOT trip trailing DD
#         even when the firm's trailing rule would have breached
#      3. No lock-at-starting-balance after +6% gain (user's Leveraged
#         firm rule)
#      Fix: refresh session_equity on UTC day rollover, track HWM
#      explicitly, apply lock-at-starting-balance after +6% gain.
# CHANGED: April 2026 — day rollover + HWM + lock (audit HIGH #33 + #34)
def check_drawdown(current_equity):
    global stop_for_day, stop_forever, session_equity, session_date
    global account_hwm, hwm_locked, starting_equity

    today = datetime.datetime.utcnow().date()

    # Initialize on first call
    if starting_equity is None:
        starting_equity = current_equity
    if session_equity is None:
        session_equity = current_equity
        session_date   = today
    if account_hwm is None:
        account_hwm = current_equity

    # Day rollover: refresh session_equity to current equity
    if today != session_date:
        print(f"[RISK] Day rollover UTC {{session_date}} -> {{today}}, "
              f"session_equity refreshed from {{session_equity:.2f}} to {{current_equity:.2f}}")
        session_equity = current_equity
        session_date   = today
        # Reset daily stop flag at day rollover
        stop_for_day = False

    # Daily DD check (same logic as before, but against fresh session_equity)
    daily_loss = session_equity - current_equity
    daily_limit = session_equity * DD_DAILY_PCT / 100.0
    if daily_loss >= daily_limit * DD_SAFETY_PCT / 100.0:
        stop_for_day = True
        print(f"[RISK] Daily DD limit reached ({{daily_loss:.2f}}). Stopping for today.")

    # HWM tracking + lock-at-starting-balance after +6% gain
    # WHY: User's Leveraged firm uses trailing DD that locks at starting
    #      balance once the account gains +6%. Before the lock, HWM
    #      trails the equity peak. After the lock, the DD floor is
    #      frozen at starting_equity (not at the current peak).
    if not hwm_locked:
        account_hwm = max(account_hwm, current_equity)
        gain_pct = (current_equity - starting_equity) / starting_equity * 100.0
        if gain_pct >= 6.0:
            hwm_locked = True
            account_hwm = starting_equity * (1.0 + DD_TOTAL_PCT / 100.0)
            print(f"[RISK] HWM locked at starting balance +{{DD_TOTAL_PCT}}% "
                  f"(floor = {{account_hwm:.2f}})")

    # Total DD check — uses account_hwm (trailing or locked)
    total_dd = account_hwm - current_equity
    if total_dd >= starting_equity * DD_TOTAL_PCT / 100.0:
        stop_forever = True
        print(f"[RISK] TOTAL DD LIMIT REACHED (hwm={{account_hwm:.2f}}, "
              f"equity={{current_equity:.2f}}, dd={{total_dd:.2f}}). Bot disabled.")

# ── Entry conditions ─────────────────────────────────────────────────────────
def check_entry_conditions():
    """
    Compute all indicator values and check entry rules.
    Returns True if all conditions are met.
    """
    try:
        if len(df_m60) < 50:
            return False  # not enough data yet

{indicator_block}

{condition_block}
        return True

    except Exception as e:
        print(f"[CONDITIONS] Error: {{e}}")
        return False

# ── Position sizing ──────────────────────────────────────────────────────────
# WHY: Old stub hardcoded pip_value_per_lot=10.0 for XAUUSD, which is
#      WRONG. 1 XAUUSD lot = 100 oz, 1 pip = $0.01 price move → $1/pip/lot.
#      The 10× error made computed lot sizes 10× too small. This is
#      the same bug Phase 5 fixed in scratch_panel.py — caught here
#      in the Tradovate stub which was missed.
# CHANGED: April 2026 — per-symbol pip value (audit MED — Family #1)
_SYMBOL_PIP_VALUE = {{
    'XAUUSD':  1.0,   # 100 oz × $0.01 = $1/pip/lot
    'XAGUSD':  5.0,   # 5000 oz × $0.001 = $5/pip/lot
    'EURUSD': 10.0,   # 100000 × 0.0001 = $10/pip/lot
    'GBPUSD': 10.0,
    'USDJPY':  6.7,   # approximate, depends on JPY rate
    'GBPJPY':  6.7,
    'AUDUSD': 10.0,
    'USDCAD':  7.3,   # approximate
    'USDCHF': 11.0,   # approximate
    'NZDUSD': 10.0,
    'US30':    1.0,
    'NAS100':  1.0,
    'BTCUSD':  1.0,
}}

_SYMBOL_PIP_SIZE = {{
    'XAUUSD': 0.01,
    'XAGUSD': 0.001,
    'EURUSD': 0.0001,
    'GBPUSD': 0.0001,
    'USDJPY': 0.01,
    'GBPJPY': 0.01,
    'AUDUSD': 0.0001,
    'USDCAD': 0.0001,
    'USDCHF': 0.0001,
    'NZDUSD': 0.0001,
    'US30':   1.0,
    'NAS100': 1.0,
    'BTCUSD': 1.0,
}}

def calculate_lots(account_balance, sl_distance_price):
    """Risk-based position sizing with per-symbol pip value."""
    risk_amount       = account_balance * RISK_PCT / 100.0
    pip_value_per_lot = _SYMBOL_PIP_VALUE.get(SYMBOL, 10.0)
    pip_size          = _SYMBOL_PIP_SIZE.get(SYMBOL, 0.0001)
    sl_pips_actual    = sl_distance_price / pip_size
    lots              = risk_amount / (sl_pips_actual * pip_value_per_lot)
    lots              = round(lots, 2)
    return max(0.01, min(lots, 100.0))

# ── Main on-bar logic ────────────────────────────────────────────────────────
async def on_new_bar(api_client):
    global daily_trades, last_trade_time, stop_for_day

    if stop_forever or stop_for_day:
        return

    # New day reset
    now = datetime.datetime.utcnow()
    if hasattr(on_new_bar, "_last_day") and on_new_bar._last_day != now.day:
        daily_trades = 0
        stop_for_day = False
    on_new_bar._last_day = now.day

    # Skip checks
    if daily_trades >= MAX_TRADES_PER_DAY:
        log_trade("SKIP", 0, 0, 0, 0, "", "max_trades_per_day"); return

    if last_trade_time and (now - last_trade_time).total_seconds() < COOLDOWN_MINUTES * 60:
        log_trade("SKIP", 0, 0, 0, 0, "", "cooldown"); return

    if not check_entry_conditions():
        return

    # Place order
    try:
        balance = await api_client.get_balance()
        price   = {tf_df_var}["close"].iloc[-1]
        sl_dist = SL_PIPS * 0.01

        lots    = calculate_lots(balance, sl_dist)

        order = await api_client.place_order(
            symbol=SYMBOL,
            action="{tradovate_action}",
            qty=lots,
            order_type="Market",
        )
        if order:
            daily_trades += 1
            last_trade_time = now
            log_trade("{direction}", lots, price, 0, 0, "entry_signal")
            print(f"[TRADE] {direction} {{{{lots}}}} lots @ {{{{price:.2f}}}} SL={{{{SL_PIPS}}}}pips TP={{{{TP_PIPS}}}}pips")
    except Exception as e:
        print(f"[ORDER] Error: {{e}}")

# ── Tradovate API client (stub — implement with tradovate_api.py) ─────────────
class TradovateAPIClient:
    def __init__(self, api_key, api_secret):
        self.api_key    = api_key
        self.api_secret = api_secret

    async def connect(self):
        print("[API] Connected to Tradovate (stub)")

    async def get_balance(self):
        return 100000.0  # replace with real API call

    async def place_order(self, symbol, action, qty, order_type):
        print(f"[API] Place order: {{action}} {{qty}} {{symbol}} (stub)")
        return {{"orderId": "stub_123"}}

    async def subscribe_candles(self, symbol, interval, callback):
        """Subscribe to candle updates via WebSocket."""
        print(f"[API] Subscribed to {{symbol}} {{interval}}m candles (stub)")

# ── Main entry point ─────────────────────────────────────────────────────────
async def main():
    client = TradovateAPIClient(
        api_key=config.get("api_key", "YOUR_API_KEY"),
        api_secret=config.get("api_secret", "YOUR_API_SECRET"),
    )
    await client.connect()
    print(f"[BOT] Started. Symbol={{SYMBOL}} Risk={{RISK_PCT}}%")

    # WHY: Old code hardcoded H1 (60 minutes). Now subscribe to the
    #      strategy's actual entry timeframe.
    # CHANGED: April 2026 — entry_timeframe-aware subscription
    async def on_entry_candle(candle):
        global {tf_df_var}
        new_row = pd.DataFrame([candle])[["open","high","low","close","volume"]]
        {tf_df_var} = pd.concat([{tf_df_var}, new_row], ignore_index=True).tail(500)
        await on_new_bar(client)

    await client.subscribe_candles(SYMBOL, {tf_minutes}, on_entry_candle)

    # Keep running
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
'''
    return code


def generate_tradovate_config(output_dir):
    """Generate config.json template for Tradovate bot."""
    config = {
        "api_key":          "YOUR_TRADOVATE_API_KEY",
        "api_secret":       "YOUR_TRADOVATE_API_SECRET",
        "username":         "your_username",
        "password":         "your_password",
        # WHY (Phase 64 Fix 5): Example config hardcoded XAUUSD/150/300.
        #      A user generating for EURUSD would see wrong defaults in their
        #      Tradovate bot and might ship them without noticing.
        #      Use the symbol from generate_ea's argument and the exit_params
        #      sl/tp that were already resolved upstream.
        # CHANGED: April 2026 — Phase 64 Fix 5 — instrument-agnostic template
        "symbol":           symbol,
        "risk_pct":         1.0,
        "max_trades_per_day": 5,
        "cooldown_minutes": 60,
        "sl_pips":          sl_pips,
        "tp_pips":          tp_pips,
        "dd_daily_pct":     5.0,
        "dd_total_pct":     10.0,
        "dd_safety_pct":    80.0,
        "max_spread_pips":  5.0,
    }
    path = os.path.join(output_dir, 'config.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
    return path


def generate_tradovate_requirements(output_dir):
    """Generate requirements.txt for Tradovate bot."""
    reqs = "pandas>=2.0\npandas-ta>=0.3.14b\nwebsockets>=11.0\nrequests>=2.28\naiohttp>=3.8\n"
    path = os.path.join(output_dir, 'requirements.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(reqs)
    return path

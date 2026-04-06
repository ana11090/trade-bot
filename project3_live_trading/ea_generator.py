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


def generate_ea(
    strategy,
    platform='mt5',
    prop_firm=None,
    stage='evaluation',
    entry_timeframe='H1',
    symbol='XAUUSD',
    magic_number=None,
    risk_per_trade_pct=1.0,
    max_trades_per_day=5,
    session_filter=None,
    day_filter=None,
    min_hold_minutes=0,
    cooldown_minutes=60,
    news_filter_minutes=5,
    max_spread_pips=5.0,
    trailing_stop=None,
    output_path=None,
):
    """
    Generate complete EA code for MT5 or Tradovate.

    Returns the code as a string. Also saves to output_path if provided.
    """
    if magic_number is None:
        magic_number = random.randint(10000, 99999)

    rules     = strategy.get('rules', [])
    win_rules = [r for r in rules if r.get('prediction') == 'WIN']
    exit_name = strategy.get('exit_name', strategy.get('exit_strategy', 'FixedSLTP'))
    exit_params = strategy.get('exit_strategy_params', {'sl_pips': 150, 'tp_pips': 300})

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
    restrictions  = prop_firm.get('restrictions', {}) if prop_firm else {}
    challenge     = prop_firm.get('challenge', {}) if prop_firm else {}

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
        )

    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(code)

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
                  entry_timeframe='H1'):

    handles = get_all_handles_for_rules(win_rules, platform='mt5')
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    sl_pips = exit_params.get('sl_pips', 150)
    tp_pips = exit_params.get('tp_pips', 300)
    trail_pips = exit_params.get('trail_pips', exit_params.get('trail_distance_pips', 100))

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
        'Time-Based': 'TimeBased', 'TimeBased': 'TimeBased',
        'Indicator Exit': 'IndicatorExit', 'IndicatorExit': 'IndicatorExit',
        'Hybrid': 'HybridExit', 'HybridExit': 'HybridExit',
    }
    exit_class = exit_class_map.get(exit_class, 'FixedSLTP')

    # ATR params
    sl_atr_mult = exit_params.get('sl_atr_mult', 1.5)
    tp_atr_mult = exit_params.get('tp_atr_mult', 3.0)
    atr_column = exit_params.get('atr_column', 'H1_atr_14')

    # Time params
    max_candles = exit_params.get('max_candles', 12)

    # Indicator exit params
    exit_indicator = exit_params.get('exit_indicator', 'H1_rsi_14')
    exit_threshold = exit_params.get('exit_threshold', 70)

    # Hybrid params
    breakeven_pips = exit_params.get('breakeven_activation_pips', 50)

    # WHY: EA checks for new bar on the entry timeframe, not always H1.
    _mql_periods = {
        'M1': 'PERIOD_M1', 'M5': 'PERIOD_M5', 'M15': 'PERIOD_M15',
        'H1': 'PERIOD_H1', 'H4': 'PERIOD_H4', 'D1': 'PERIOD_D1',
    }
    mql_period = _mql_periods.get(entry_timeframe, 'PERIOD_H1')

    # Build condition input params
    condition_inputs = []
    condition_checks = []
    for ri, rule in enumerate(win_rules, 1):
        for ci, cond in enumerate(rule.get('conditions', []), 1):
            feat = cond['feature']
            op   = cond.get('operator', '>')
            val  = cond.get('value', 0)
            safe_name = re.sub(r'[^a-zA-Z0-9]', '_', feat)
            param_name = f"Rule{ri}_Cond{ci}_{safe_name[:20]}"
            condition_inputs.append(f'input double {param_name} = {val:.6f}; // {feat} {op} threshold')

            mql = get_mql_code(feat, 'mt5')
            var_n = mql['var_name']
            mql_op = OPERATOR_MAP_MQL.get(op, '>')
            condition_checks.append(
                f'   // Rule {ri}, Condition {ci}: {feat} {op} {val:.4f}\n'
                f'   {mql["read_code"]}\n'
                f'   if(!(val_{var_n} {mql_op} {param_name})) entrySignal = false;\n'
            )

    # Handle variable declarations
    handle_vars  = '\n'.join(h['handle_var'] for h in handles if h.get('handle_var'))
    handle_inits = '\n   '.join(h['handle_init'] for h in handles if h.get('handle_init'))

    session_comment = ', '.join(session_filter) if session_filter else 'All sessions'
    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    day_comment = ', '.join(day_names[d - 1] for d in day_filter if 1 <= d <= 7) if day_filter else 'All days'

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
    if session_filter and len(session_filter) > 0:
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
                    f'   if(UsePropFirmMode && !g_stopForDay)\n'
                    f'   {{\n'
                    f'      double dailyLossPct = (g_dailyReference > 0) ? (g_dailyReference - equity) / g_dailyReference * 100.0 : 0;\n'
                    f'      if(dailyLossPct >= EvalDailyDDAlert)\n'
                    f'      {{\n'
                    f'         g_stopForDay = true;\n'
                    f'         CloseAllPositions("DailyDDBuffer");\n'
                    f'         Print("[EVAL] Daily DD buffer hit: ", DoubleToString(dailyLossPct,1), "%");\n'
                    f'         return;\n'
                    f'      }}\n'
                    f'   }}')

            if total_alert is not None:
                extra_inputs.append(f'input double EvalTotalDDAlert = {total_alert}; // EA stops here (firm blows at {dd_total_pct}%)')
                extra_tick_checks.append(
                    f'   // [{rname}] Total DD alert at {total_alert}% (firm limit: {dd_total_pct}%)\n'
                    f'   if(UsePropFirmMode)\n'
                    f'   {{\n'
                    f'      double totalDDPct = (g_hwm > 0) ? (g_hwm - equity) / g_hwm * 100.0 : 0;\n'
                    f'      if(totalDDPct >= EvalTotalDDAlert)\n'
                    f'      {{\n'
                    f'         g_stopForever = true;\n'
                    f'         CloseAllPositions("TotalDDBuffer");\n'
                    f'         Alert("[EVAL] Total DD buffer hit: " + DoubleToString(totalDDPct,1) + "%");\n'
                    f'         return;\n'
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
                    f'   if(UsePropFirmMode && !g_dailyAlertSent)\n'
                    f'   {{\n'
                    f'      double dailyLossPct = (g_dailyReference > 0) ? (g_dailyReference - equity) / g_dailyReference * 100.0 : 0;\n'
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
                    f'   if(UsePropFirmMode)\n'
                    f'   {{\n'
                    f'      double totalDDPct = (g_hwm > 0) ? (g_hwm - equity) / g_hwm * 100.0 : 0;\n'
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
            extra_globals.append(f'double g_periodProfit = 0.0;')

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

    # ── DD tracking — depends on drawdown_mechanics from JSON ────────────
    # WHY: Different firms calculate DD differently. Leveraged uses trailing on
    #      closed balance with HWM lock after +6%. Other firms use static or equity-based DD.
    #      The JSON describes HOW, the generator produces the matching MQL5 code.
    trailing_dd  = dd_mechanics.get('trailing_dd', {})
    daily_dd_mech = dd_mechanics.get('daily_dd', {})

    # Always add DD tracking globals
    extra_globals.append(f'double g_hwm = 0.0;            // High water mark for total DD')
    extra_globals.append(f'double g_dailyReference = 0.0; // Daily DD reference point')
    extra_globals.append(f'double g_startingBalance = 0.0; // Balance at period/session start')
    extra_globals.append(f'bool   g_ddLocked = false;      // True when trailing DD locks at starting balance')

    # Init
    extra_init.append(f'   g_startingBalance = AccountInfoDouble(ACCOUNT_BALANCE);')
    extra_init.append(f'   g_hwm = g_startingBalance;')
    extra_init.append(f'   g_dailyReference = MathMax(AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY));')

    # HWM update logic depends on trailing type
    if trailing_dd.get('basis') == 'closed_balance':
        # WHY: Leveraged tracks HWM on closed trades, not floating equity.
        #      HWM only moves up when balance increases from a closed trade.
        extra_on_trade.append(
            f'   // DD Mechanic: trailing on closed balance (not floating equity)\n'
            f'   double newBalance = AccountInfoDouble(ACCOUNT_BALANCE);\n'
            f'   if(newBalance > g_hwm && !g_ddLocked)\n'
            f'      g_hwm = newBalance;')

        lock_pct = trailing_dd.get('lock_after_gain_pct')
        if lock_pct:
            extra_on_trade.append(
                f'   // DD lock: after +{lock_pct}% gain, floor locks at starting balance\n'
                f'   if(!g_ddLocked && newBalance >= g_startingBalance * (1.0 + {lock_pct}/100.0))\n'
                f'   {{\n'
                f'      g_ddLocked = true;\n'
                f'      g_hwm = g_startingBalance + g_startingBalance * ({lock_pct}/100.0);\n'
                f'      Print("[DD] Trailing DD LOCKED at starting balance $", g_startingBalance);\n'
                f'   }}')
    else:
        # Default: HWM updates on equity (standard trailing)
        extra_tick_checks.insert(0,
            f'   // DD Mechanic: standard trailing on equity\n'
            f'   if(equity > g_hwm && !g_ddLocked) g_hwm = equity;')

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
            extra_daily_reset.append(
                f'   // Count profitable days (payout condition)\n'
                f'   double ydayPnl = AccountInfoDouble(ACCOUNT_BALANCE) - g_sessionEquity;\n'
                f'   if(ydayPnl >= g_sessionEquity * MinDayProfitPct / 100.0 && g_sessionEquity > 0)\n'
                f'      g_profitDayCount++;')
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

    # ── Period reset (14 days) ────────────────────────────────────────────
    if has_period_reset:
        reset_items = []
        if has_min_profit_days:
            reset_items.append('g_profitDayCount = 0;')
        if has_consistency:
            reset_items.append('g_bestDayProfit = 0;')
            reset_items.append('g_periodProfit = 0;')
        if has_protect_phase:
            reset_items.append('g_payoutCondsMet = false;')
        if any('g_stoppedForPeriod' in e for e in extra_globals):
            reset_items.append('g_stoppedForPeriod = false;')
        reset_items.append('g_startingBalance = AccountInfoDouble(ACCOUNT_BALANCE);')
        reset_block = '\n      '.join(reset_items)
        extra_daily_reset.append(
            f'   // Period reset check (every {{}}-day cycle)\n'
            f'   if(g_periodStart == 0) g_periodStart = TimeCurrent();\n'
            f'   if(TimeCurrent() - g_periodStart >= PayoutPeriodDays * 86400)\n'
            f'   {{\n'
            f'      g_periodStart = TimeCurrent();\n'
            f'      {reset_block}\n'
            f'      Print("[PERIOD] New period started. Balance: $", g_startingBalance);\n'
            f'   }}')

    # ── GetPayoutStatus function ──────────────────────────────────────────
    parts = ['"Status: "']
    if has_min_profit_days:
        parts.append('"Days=" + IntegerToString(g_profitDayCount) + "/" + IntegerToString(MinProfitDays)')
    if has_consistency:
        parts.append('"Best=" + DoubleToString(g_periodProfit>0 ? g_bestDayProfit/g_periodProfit*100 : 0, 1) + "%"')
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

    if exit_class == 'ATRBased':
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
        exit_on_entry = (
            f'      // ATR-Based: compute SL/TP from ATR at entry\n'
            f'      double atrBuf[1];\n'
            f'      CopyBuffer(handle_exit_atr, 0, 0, 1, atrBuf);\n'
            f'      double atrVal = atrBuf[0];\n'
            f'      g_entrySL = atrVal * SL_ATR_Mult;\n'
            f'      g_entryTP = atrVal * TP_ATR_Mult;\n'
            f'      trade.PositionModify(trade.ResultOrder(),\n'
            f'         entryPrice - g_entrySL,\n'
            f'         entryPrice + g_entryTP);\n'
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
        exit_management = (
            f'   // Time-Based exit: close after MaxHoldCandles\n'
            f'   if(g_entryBarIndex > 0)\n'
            f'   {{\n'
            f'      int barsHeld = Bars(_Symbol, {mql_period}) - g_entryBarIndex;\n'
            f'      if(barsHeld >= MaxHoldCandles)\n'
            f'      {{\n'
            f'         CloseAllPositions("TimeExit");\n'
            f'         g_entryBarIndex = 0;\n'
            f'         Print("[EA] Time exit after ", barsHeld, " candles");\n'
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
        exit_management = (
            f'   // Indicator Exit: close when {exit_indicator} crosses {exit_threshold}\n'
            f'   {{\n'
            f'      {ind_code["read_code"]}\n'
            f'      if(val_{ind_code["var_name"]} >= ExitThreshold)\n'
            f'      {{\n'
            f'         CloseAllPositions("IndicatorExit_{exit_indicator}");\n'
            f'         Print("[EA] Indicator exit: {exit_indicator} = ", val_{ind_code["var_name"]});\n'
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
        exit_management = (
            f'   // Hybrid exit: breakeven + trailing + time limit\n'
            f'   for(int _hi = PositionsTotal() - 1; _hi >= 0; _hi--)\n'
            f'   {{\n'
            f'      ulong _ht = PositionGetTicket(_hi);\n'
            f'      if(_ht <= 0 || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;\n'
            f'      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;\n'
            f'      double _openP = PositionGetDouble(POSITION_PRICE_OPEN);\n'
            f'      double _curSL = PositionGetDouble(POSITION_SL);\n'
            f'      double _curTP = PositionGetDouble(POSITION_TP);\n'
            f'      double _bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);\n'
            f'      double _profitPips = (_bid - _openP) / (_Point * 10);\n'
            f'      // Breakeven: move SL to entry once profit reaches threshold\n'
            f'      if(!g_breakevenSet && _profitPips >= BreakevenPips)\n'
            f'      {{\n'
            f'         trade.PositionModify(_ht, _openP + _Point, _curTP);\n'
            f'         g_breakevenSet = true;\n'
            f'         Print("[EA] Breakeven set at ", _openP);\n'
            f'      }}\n'
            f'      // Trailing: move SL up as price moves\n'
            f'      if(g_breakevenSet && TrailPips > 0)\n'
            f'      {{\n'
            f'         double _newSL = _bid - TrailPips * _Point * 10;\n'
            f'         if(_newSL > _curSL + _Point)\n'
            f'            trade.PositionModify(_ht, _newSL, _curTP);\n'
            f'      }}\n'
            f'   }}\n'
            f'   // Time limit\n'
            f'   if(g_entryBarIndex > 0)\n'
            f'   {{\n'
            f'      int _barsHeld = Bars(_Symbol, {mql_period}) - g_entryBarIndex;\n'
            f'      if(_barsHeld >= MaxHoldCandles)\n'
            f'      {{\n'
            f'         CloseAllPositions("HybridTimeExit");\n'
            f'         g_entryBarIndex = 0;\n'
            f'      }}\n'
            f'   }}\n'
        )

    # ── Build injection strings ───────────────────────────────────────────
    extra_inputs_block      = '\n'.join(extra_inputs)      if extra_inputs      else ''
    extra_globals_block     = '\n'.join(extra_globals)     if extra_globals     else ''
    extra_init_block        = '\n'.join(extra_init)        if extra_init        else '   // No special init'
    extra_daily_reset_block = '\n'.join(extra_daily_reset) if extra_daily_reset else ''
    extra_tick_checks_block = '\n'.join(extra_tick_checks) if extra_tick_checks else ''
    extra_functions_block   = '\n\n'.join(extra_functions) if extra_functions   else 'string GetPayoutStatus() { return "N/A"; }'

    # Exit-specific blocks (computed outside f-string to avoid backslash issues)
    exit_on_entry_block = exit_on_entry if exit_on_entry else '      trade.PositionModify(trade.ResultOrder(),\n         entryPrice - sl,\n         entryPrice + tp);'

    code = f"""\
//+------------------------------------------------------------------+
//| Strategy: {exit_name}                                             |
//| Generated: {generated_at}                                         |
//| Platform: MetaTrader 5 (MQL5)                                     |
//| Validation: Grade {grade} ({score}/100)                           |
//| Backtest: WR {base_stats.get('win_rate', 0)*100:.1f}%, {base_stats.get('total_pips', 0):.0f} net pips, PF {base_stats.get('profit_factor', 0):.2f}  |
//| Prop Firm: {prop_firm_name}                                        |
//| Sessions: {session_comment}                                        |
//| Days: {day_comment}                                               |
//+------------------------------------------------------------------+
#property copyright "Generated by Trade Bot"
#property version   "1.00"
#property strict

#include <Trade\\Trade.mqh>
#include <Trade\\PositionInfo.mqh>

//--- Input parameters
input double RiskPercent        = {risk_per_trade_pct};     // Risk per trade %
input int    MaxTradesPerDay    = {max_trades_per_day};      // Max trades per day
input int    MagicNumber        = {magic_number};            // Magic number
input double MaxSpreadPips      = {max_spread_pips};         // Max spread to allow entry
input int    CooldownMinutes    = {cooldown_minutes};        // Min minutes between trades
input int    MinHoldMinutes     = {min_hold_minutes};        // Min hold time
input bool   UseNewsFilter      = true;                      // Skip trading around news
input int    NewsFilterMinutes  = {news_filter_minutes};     // Minutes before/after news
input bool   UsePropFirmMode    = true;                      // Enable prop firm safety
input double DailyDDLimitPct    = {dd_daily_pct};           // Daily DD blow limit % (firm closes account here)
input double TotalDDLimitPct    = {dd_total_pct};           // Total DD blow limit % (firm closes account here)
input bool   LogTrades          = true;                      // Log trades to CSV
input string LogFilePath        = "trades_log_{magic_number}.csv"; // Log file path
//--- Exit parameters
input double SLPips             = {sl_pips};                 // Stop loss (pips)
input double TPPips             = {tp_pips};                 // Take profit (pips)
input double TrailPips          = {trail_pips};              // Trailing stop (pips, 0=off)
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
   IndicatorRelease(0);
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

   if(g_stopForDay) return;

   // WHY: Trailing stop must be checked every tick, not just on new bars.
   //      Price can hit the trail level between bars.
   // CHANGED: April 2026 — trailing stop management
   ManageTrailingStop();

   // WHY: Exit-specific management (time-based, indicator-based, hybrid).
   //      Must be checked every tick for time/indicator exits.
   // CHANGED: April 2026 — all exit strategies supported
{exit_management}

   //--- Check for new bar
   datetime currentBarTime = iTime(_Symbol, {mql_period}, 0);
   if(currentBarTime == g_lastBarTime) return;
   g_lastBarTime = currentBarTime;

   //--- Reset daily counter
   MqlDateTime now_gmt;
   TimeToStruct(TimeGMT(), now_gmt);
   int _resetHour = {reset_hour_gmt}; // From firm JSON ({daily_dd_mech.get('reset_time', '00:00')} {daily_dd_mech.get('reset_timezone', 'GMT')} = {reset_hour_gmt}:00 GMT)
   static int _lastResetDay = -1;
   // WHY: Leveraged resets at 23:00 GMT+3 (= 20:00 GMT), not midnight.
   //      We check if current GMT hour >= reset hour AND we haven't reset today.
   bool _shouldReset = false;
   if(_resetHour == 0)
   {{
      // Standard midnight reset
      if(now_gmt.day != _lastResetDay) _shouldReset = true;
   }}
   else
   {{
      // Custom reset hour (e.g., 20:00 GMT for Leveraged)
      if(now_gmt.hour >= _resetHour && now_gmt.day != _lastResetDay)
         _shouldReset = true;
      // Handle day wrap: if reset hour is late (e.g., 23), also check next day
      if(now_gmt.hour < _resetHour && now_gmt.day != _lastResetDay && _lastResetDay != -1)
         _shouldReset = false; // wait until reset hour
   }}
   if(_shouldReset)
   {{
      _lastResetDay    = now_gmt.day;
      g_dailyTrades    = 0;
      g_stopForDay     = false;
      g_sessionEquity  = equity;
      g_dailyHighEquity = equity;
{extra_daily_reset_block}
   }}

   //--- Skip checks
   double spreadPips = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) / 10.0;
   if(spreadPips > MaxSpreadPips)
   {{ LogSkip("spread_too_wide", spreadPips); return; }}

   if(g_dailyTrades >= MaxTradesPerDay)
   {{ LogSkip("max_trades_per_day", g_dailyTrades); return; }}

   if(TimeCurrent() - g_lastTradeTime < CooldownMinutes * 60)
   {{ LogSkip("cooldown", (TimeCurrent()-g_lastTradeTime)/60.0); return; }}

   if(!CheckSession())
   {{ LogSkip("outside_session", 0); return; }}

   if(!CheckDayFilter())
   {{ LogSkip("day_filtered", now_struct.day_of_week); return; }}

   if(UseNewsFilter && IsNewsImminent())
   {{ LogSkip("news_filter", 0); return; }}

{extra_tick_checks_block}
   //--- Check entry conditions
   bool entrySignal = true;

{conditions_check_block}

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
   double sl = SLPips * _Point * 10;
   double tp = TPPips * _Point * 10;
   double lots = CalculateLots(sl);
   if(lots <= 0.0) return;

   //--- Place order
   if(trade.Buy(lots, _Symbol, 0, 0, 0, "EA_Entry"))
   {{
      double entryPrice = trade.ResultPrice();
{exit_on_entry_block}
      g_dailyTrades++;
      g_lastTradeTime = TimeCurrent();
      LogTrade("OPEN", "BUY", lots, entryPrice, 0, 0, "entry_signal");
      Print("[EA] BUY opened @ ", entryPrice, " lots=", lots);
   }}
}}

//+------------------------------------------------------------------+
//| Calculate position size from risk %                                |
//+------------------------------------------------------------------+
double CalculateLots(double slDistance)
{{
   double equity     = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskAmount = equity * RiskPercent / 100.0;
   double tickValue  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double lotStep    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot     = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot     = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   if(tickValue == 0 || slDistance == 0) return minLot;

   double lots = riskAmount / (slDistance / tickSize * tickValue);
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(lots, minLot);
   lots = MathMin(lots, maxLot);
   return NormalizeDouble(lots, 2);
}}

//+------------------------------------------------------------------+
//| Manage trailing stop on open position                              |
//+------------------------------------------------------------------+
void ManageTrailingStop()
{{
   if(TrailPips <= 0) return; // trailing stop disabled

   double trailDistance = TrailPips * _Point * 10;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {{
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);
      long   posType   = PositionGetInteger(POSITION_TYPE);
      double bid       = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask       = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      if(posType == POSITION_TYPE_BUY)
      {{
         // Only trail if price has moved enough in profit
         double profitPips = (bid - openPrice) / (_Point * 10);
         if(profitPips >= TrailPips)
         {{
            double newSL = bid - trailDistance;
            // Only move SL up, never down
            if(newSL > currentSL + _Point)
            {{
               trade.PositionModify(ticket, newSL, currentTP);
            }}
         }}
      }}
      else if(posType == POSITION_TYPE_SELL)
      {{
         double profitPips = (openPrice - ask) / (_Point * 10);
         if(profitPips >= TrailPips)
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
                        grade, score, base_stats):

    handles  = get_all_handles_for_rules(win_rules, platform='tradovate')
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    sl_pips  = exit_params.get('sl_pips', 150)
    tp_pips  = exit_params.get('tp_pips', 300)

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
            indicator_lines.append(f'    {tv.get("python_code", f"val_{var_n} = 0.0")}  # {feat}')
            mql_op = OPERATOR_MAP_PY.get(op, '>')
            condition_lines.append(f'    if not (val_{var_n} {mql_op} {val}):')
            condition_lines.append(f'        return False  # Rule {ri} cond {ci}: {feat} {op} {val:.4f}')

    indicator_block   = '\n'.join(indicator_lines) or '    pass'
    condition_block   = '\n'.join(condition_lines) or '    pass'

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
daily_trades      = 0
last_trade_time   = None
session_equity    = None
stop_for_day      = False
stop_forever      = False

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
def check_drawdown(current_equity):
    global stop_for_day, stop_forever, session_equity
    if session_equity is None:
        session_equity = current_equity
    daily_loss = session_equity - current_equity
    daily_limit = session_equity * DD_DAILY_PCT / 100.0
    if daily_loss >= daily_limit * DD_SAFETY_PCT / 100.0:
        stop_for_day = True
        print(f"[RISK] Daily DD limit reached ({{daily_loss:.2f}}). Stopping for today.")
    total_dd = session_equity - current_equity
    if total_dd >= session_equity * DD_TOTAL_PCT / 100.0:
        stop_forever = True
        print("[RISK] TOTAL DD LIMIT REACHED. Bot disabled.")

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
def calculate_lots(account_balance, sl_distance_price):
    """Risk-based position sizing."""
    risk_amount = account_balance * RISK_PCT / 100.0
    # For XAUUSD: 1 pip = $0.01 price move, pip value ≈ $10/lot
    pip_value_per_lot = 10.0  # adjust for your broker/contract
    pip_size = 0.01
    sl_pips_actual = sl_distance_price / pip_size
    lots = risk_amount / (sl_pips_actual * pip_value_per_lot)
    lots = round(lots, 2)
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

    if last_trade_time and (now - last_trade_time).seconds < COOLDOWN_MINUTES * 60:
        log_trade("SKIP", 0, 0, 0, 0, "", "cooldown"); return

    if not check_entry_conditions():
        return

    # Place order
    try:
        balance = await api_client.get_balance()
        price   = df_m60["close"].iloc[-1]
        sl_dist = SL_PIPS * 0.01
        lots    = calculate_lots(balance, sl_dist)

        order = await api_client.place_order(
            symbol=SYMBOL,
            action="Buy",
            qty=lots,
            order_type="Market",
        )
        if order:
            daily_trades += 1
            last_trade_time = now
            log_trade("BUY", lots, price, 0, 0, "entry_signal")
            print(f"[TRADE] BUY {{lots}} lots @ {{price:.2f}} SL={{SL_PIPS}}pips TP={{TP_PIPS}}pips")
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

    # Subscribe to H1 candles (main timeframe)
    async def on_h1_candle(candle):
        global df_m60
        new_row = pd.DataFrame([candle])[["open","high","low","close","volume"]]
        df_m60 = pd.concat([df_m60, new_row], ignore_index=True).tail(500)
        await on_new_bar(client)

    await client.subscribe_candles(SYMBOL, 60, on_h1_candle)

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
        "symbol":           "XAUUSD",
        "risk_pct":         1.0,
        "max_trades_per_day": 5,
        "cooldown_minutes": 60,
        "sl_pips":          150,
        "tp_pips":          300,
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

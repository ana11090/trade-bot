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
                  grade, score, base_stats, prop_firm_name):

    handles = get_all_handles_for_rules(win_rules, platform='mt5')
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    sl_pips = exit_params.get('sl_pips', 150)
    tp_pips = exit_params.get('tp_pips', 300)
    trail_pips = exit_params.get('trail_pips', exit_params.get('trail_distance_pips', 100))

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

    conditions_block = '\n'.join(condition_inputs)
    conditions_check_block = '\n'.join(condition_checks)

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
input double DailyDDLimitPct    = {dd_daily_pct};           // Daily drawdown limit %
input double TotalDDLimitPct    = {dd_total_pct};           // Total drawdown limit %
input double DailySafetyPct     = {dd_safety_pct};          // Stop at X% of daily limit
input bool   LogTrades          = true;                      // Log trades to CSV
input string LogFilePath        = "trades_log_{magic_number}.csv"; // Log file path
//--- Exit parameters
input double SLPips             = {sl_pips};                 // Stop loss (pips)
input double TPPips             = {tp_pips};                 // Take profit (pips)
input double TrailPips          = {trail_pips};              // Trailing stop (pips, 0=off)
//--- Entry rule thresholds (one per condition — tweak without recompiling)
{conditions_block}

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

   //--- Check drawdown limits
   if(UsePropFirmMode)
   {{
      double dailyLoss = g_sessionEquity - equity;
      double dailyLimit = g_sessionEquity * DailyDDLimitPct / 100.0;
      if(dailyLoss >= dailyLimit * DailySafetyPct / 100.0 && !g_stopForDay)
      {{
         g_stopForDay = true;
         CloseAllPositions("DailyDDLimit");
         Print("[RISK] Daily DD limit reached. Stopping for today.");
         return;
      }}
      double totalDD = g_sessionEquity - equity;
      if(totalDD >= g_sessionEquity * TotalDDLimitPct / 100.0)
      {{
         g_stopForever = true;
         CloseAllPositions("TotalDDLimit");
         Alert("[RISK] TOTAL DD LIMIT REACHED. EA disabled.");
         return;
      }}
   }}

   if(g_stopForDay) return;

   //--- Check for new bar
   datetime currentBarTime = iTime(_Symbol, PERIOD_H1, 0);
   if(currentBarTime == g_lastBarTime) return;
   g_lastBarTime = currentBarTime;

   //--- Reset daily counter on new day
   MqlDateTime now_struct;
   TimeToStruct(TimeCurrent(), now_struct);
   static int lastDay = -1;
   if(now_struct.day != lastDay)
   {{
      lastDay          = now_struct.day;
      g_dailyTrades    = 0;
      g_stopForDay     = false;
      g_sessionEquity  = equity;
      g_dailyHighEquity = equity;
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

   //--- Check entry conditions
   bool entrySignal = true;

{conditions_check_block}

   if(!entrySignal) return;

   //--- No existing position with our magic
   if(PositionSelectByTicket(0)) return; // already in position

   //--- Position sizing
   double sl = SLPips * _Point * 10;
   double tp = TPPips * _Point * 10;
   double lots = CalculateLots(sl);
   if(lots <= 0.0) return;

   //--- Place order
   if(trade.Buy(lots, _Symbol, 0, 0, 0, "EA_Entry"))
   {{
      double entryPrice = trade.ResultPrice();
      trade.PositionModify(trade.ResultOrder(),
         entryPrice - sl,
         entryPrice + tp);
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
   // London: 7-16, NewYork: 12-21, Asian: 0-8
   // Adjust based on selected sessions: {session_comment}
   return true; // TODO: customise per session_filter setting
}}

//+------------------------------------------------------------------+
//| Day of week filter                                                 |
//+------------------------------------------------------------------+
bool CheckDayFilter()
{{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   // dow: 0=Sun,1=Mon,2=Tue,3=Wed,4=Thu,5=Fri,6=Sat
   // Allowed days: {day_comment}
   int dow = dt.day_of_week;
   if(dow == 0 || dow == 6) return false; // skip weekends by default
   return true;
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

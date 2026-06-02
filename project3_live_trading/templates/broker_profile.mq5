//+------------------------------------------------------------------+
//| broker_profile.mq5                                                |
//| Emits ONE machine-parseable JSON block describing the broker,     |
//| account, symbol specs, GMT offset, trading-session schedule, and  |
//| a sampled per-session spread profile — everything the backtester  |
//| and EA generator need to be calibrated to THIS prop firm's broker.|
//|                                                                    |
//| HOW TO USE:                                                        |
//|  1. Copy to [MT5 Data Folder]/MQL5/Scripts/                        |
//|  2. Compile in MetaEditor (F7)                                     |
//|  3. Attach to a chart of the symbol you trade (e.g. XAUUSD)        |
//|     - For a quick profile: run once (SpreadSampleSeconds=0)        |
//|     - For session-spread calibration: set SpreadSampleSeconds=3600 |
//|       and leave it running across London/NY/Asian hours, or run    |
//|       several times at different hours.                            |
//|  4. Open the Experts tab (Ctrl+E)                                  |
//|  5. Copy EVERYTHING between the BEGIN/END markers (inclusive) and  |
//|     paste it back into the app / to Claude.                        |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input string Symbol_Override     = "";    // blank = chart symbol
input int    SpreadSampleSeconds = 0;      // 0 = single snapshot; >0 = sample spread for N seconds
input int    SpreadSampleEveryMs = 1000;   // sampling interval while sampling

// Classify a GMT hour into a session bucket (matches Python session map:
// NY 13-21, London 7-12, Asian 0-6 & 22-23; "late" overlaps handled by caller).
string SessionOf(int gmt_hour)
{
   if(gmt_hour >= 13 && gmt_hour < 22) return "ny";
   if(gmt_hour >= 7  && gmt_hour < 13) return "london";
   return "asian";
}

void OnStart()
{
   string sym = (Symbol_Override != "") ? Symbol_Override : _Symbol;

   //── Symbol specs ───────────────────────────────────────────────────
   double tickValue    = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSize     = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double contractSize = SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE);
   double pointSize    = SymbolInfoDouble(sym, SYMBOL_POINT);
   int    digits       = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double minLot       = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double maxLot       = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double lotStep      = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);

   double pipSize = tickSize;
   if(digits == 5 || digits == 3) pipSize = pointSize * 10;
   double pipValuePerLot = (tickSize > 0) ? (tickValue / tickSize) * pipSize : 0.0;

   //── Swap / rollover (overnight cost — matters when holding positions) ──
   // SYMBOL_SWAP_LONG/SHORT units depend on SYMBOL_SWAP_MODE; report raw + mode
   // + the triple-swap weekday so the parser can convert to pips/night.
   double swapLong   = SymbolInfoDouble(sym, SYMBOL_SWAP_LONG);
   double swapShort  = SymbolInfoDouble(sym, SYMBOL_SWAP_SHORT);
   long   swapMode   = SymbolInfoInteger(sym, SYMBOL_SWAP_MODE);
   long   swap3xDay  = SymbolInfoInteger(sym, SYMBOL_SWAP_ROLLOVER3DAYS);
   double swapLongPips  = (swapMode == SYMBOL_SWAP_MODE_POINTS && pipSize>0)
                          ? (swapLong  * pointSize)/pipSize : swapLong;
   double swapShortPips = (swapMode == SYMBOL_SWAP_MODE_POINTS && pipSize>0)
                          ? (swapShort * pointSize)/pipSize : swapShort;

   //── Execution constraints (affect SL/TP placement + fills) ─────────
   long   stopsLevel  = SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
   long   freezeLevel = SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL);
   double stopsPips   = (pipSize>0) ? (stopsLevel  * pointSize)/pipSize : 0;
   double freezePips  = (pipSize>0) ? (freezeLevel * pointSize)/pipSize : 0;
   long   execMode    = SymbolInfoInteger(sym, SYMBOL_TRADE_EXEMODE);

   //── Account ────────────────────────────────────────────────────────
   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   int    acctLev  = (int)AccountInfoInteger(ACCOUNT_LEVERAGE);
   string acctCcy  = AccountInfoString(ACCOUNT_CURRENCY);
   string company  = AccountInfoString(ACCOUNT_COMPANY);
   string server   = AccountInfoString(ACCOUNT_SERVER);

   //── GMT offset (server time vs GMT, in hours) ──────────────────────
   double gmtOffsetH = ((double)TimeTradeServer() - (double)TimeGMT()) / 3600.0;

   //── Trading session schedule (THIS is what reveals the 00:00 gap) ──
   // For each weekday, list the broker's TRADE sessions [from,to) in minutes.
   // Day index: 0=Sun .. 6=Sat (ENUM_DAY_OF_WEEK).
   string sessionsJson = "";
   for(int d = 0; d <= 6; d++)
   {
      string daySess = "";
      datetime from, to;
      int idx = 0;
      while(SymbolInfoSessionTrade(sym, (ENUM_DAY_OF_WEEK)d, idx, from, to))
      {
         int fmin = (int)(from / 60); // minutes from midnight
         int tmin = (int)(to   / 60);
         if(daySess != "") daySess += ",";
         daySess += StringFormat("[%d,%d]", fmin, tmin);
         idx++;
      }
      if(sessionsJson != "") sessionsJson += ",";
      sessionsJson += StringFormat("\"%d\":[%s]", d, daySess);
   }

   //── Optional spread sampling (per GMT-hour bucket) ─────────────────
   // Accumulate spread (in pips) per session bucket so we can compute
   // per-session multipliers. Snapshot-only when SpreadSampleSeconds<=0.
   double sumLondon=0, sumNY=0, sumAsian=0;
   long   cntLondon=0, cntNY=0, cntAsian=0;
   double snapSpreadPips = 0;
   double spMin=1e9, spMax=0;            // spread distribution (slippage proxy)
   double spAll[]; ArrayResize(spAll, 0); // collected samples for percentile
   {
      long spreadPts = SymbolInfoInteger(sym, SYMBOL_SPREAD);
      snapSpreadPips = (pipSize>0) ? (spreadPts * pointSize)/pipSize : 0;
   }

   datetime tEnd = TimeCurrent() + (SpreadSampleSeconds > 0 ? SpreadSampleSeconds : 0);
   bool sampling = (SpreadSampleSeconds > 0);
   do
   {
      double bid = SymbolInfoDouble(sym, SYMBOL_BID);
      double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
      double sp  = (pipSize>0) ? (ask - bid)/pipSize : 0;
      if(sp < spMin) spMin = sp;
      if(sp > spMax) spMax = sp;
      int _n = ArraySize(spAll); ArrayResize(spAll, _n+1); spAll[_n] = sp;
      MqlDateTime g; TimeToStruct(TimeGMT(), g);
      string b = SessionOf(g.hour);
      if(b=="london"){ sumLondon+=sp; cntLondon++; }
      else if(b=="ny"){ sumNY+=sp; cntNY++; }
      else { sumAsian+=sp; cntAsian++; }
      if(!sampling) break;
      Sleep(SpreadSampleEveryMs);
   }
   while(sampling && TimeCurrent() < tEnd && !IsStopped());

   double medLondon = (cntLondon>0)? sumLondon/cntLondon : snapSpreadPips;
   double medNY     = (cntNY>0)?     sumNY/cntNY         : snapSpreadPips;
   double medAsian  = (cntAsian>0)?  sumAsian/cntAsian   : snapSpreadPips;
   double overall   = ((cntLondon+cntNY+cntAsian)>0)
                      ? (sumLondon+sumNY+sumAsian)/(cntLondon+cntNY+cntAsian)
                      : snapSpreadPips;
   double baseSp    = (overall>0)? overall : 1.0;

   // Spread distribution (slippage proxy). p95 from collected samples.
   double spP95 = snapSpreadPips;
   if(ArraySize(spAll) > 0)
   {
      ArraySort(spAll);
      int p95i = (int)MathFloor(0.95 * (ArraySize(spAll) - 1));
      spP95 = spAll[p95i];
   }
   if(spMin > 1e8) { spMin = snapSpreadPips; spMax = snapSpreadPips; }

   //── Emit ONE JSON block between markers ────────────────────────────
   Print(">>>BROKER_PROFILE_BEGIN>>>");
   string j = "{";
   j += "\"schema\":\"broker_profile_v1\",";
   j += StringFormat("\"symbol\":\"%s\",", sym);
   j += StringFormat("\"broker_company\":\"%s\",", company);
   j += StringFormat("\"broker_server\":\"%s\",", server);
   j += StringFormat("\"account_currency\":\"%s\",", acctCcy);
   j += StringFormat("\"account_balance\":%.2f,", balance);
   j += StringFormat("\"account_equity\":%.2f,", equity);
   j += StringFormat("\"account_leverage\":%d,", acctLev);
   j += StringFormat("\"gmt_offset_hours\":%.2f,", gmtOffsetH);
   j += StringFormat("\"digits\":%d,", digits);
   j += StringFormat("\"point\":%.10f,", pointSize);
   j += StringFormat("\"pip_size\":%.10f,", pipSize);
   j += StringFormat("\"tick_value\":%.6f,", tickValue);
   j += StringFormat("\"tick_size\":%.10f,", tickSize);
   j += StringFormat("\"contract_size\":%.2f,", contractSize);
   j += StringFormat("\"pip_value_per_lot\":%.6f,", pipValuePerLot);
   j += StringFormat("\"min_lot\":%.4f,", minLot);
   j += StringFormat("\"lot_step\":%.4f,", lotStep);
   j += StringFormat("\"max_lot\":%.2f,", maxLot);
   j += StringFormat("\"swap_long_raw\":%.4f,", swapLong);
   j += StringFormat("\"swap_short_raw\":%.4f,", swapShort);
   j += StringFormat("\"swap_mode\":%d,", (int)swapMode);
   j += StringFormat("\"swap_long_pips_per_night\":%.4f,", swapLongPips);
   j += StringFormat("\"swap_short_pips_per_night\":%.4f,", swapShortPips);
   j += StringFormat("\"swap_rollover3days_weekday\":%d,", (int)swap3xDay);
   j += StringFormat("\"stops_level_pips\":%.2f,", stopsPips);
   j += StringFormat("\"freeze_level_pips\":%.2f,", freezePips);
   j += StringFormat("\"trade_exec_mode\":%d,", (int)execMode);
   j += StringFormat("\"spread_snapshot_pips\":%.2f,", snapSpreadPips);
   j += StringFormat("\"spread_distribution_pips\":{\"min\":%.2f,\"max\":%.2f,\"p95\":%.2f},", spMin, spMax, spP95);
   j += StringFormat("\"spread_sampled_seconds\":%d,", SpreadSampleSeconds);
   j += StringFormat("\"spread_samples\":{\"london\":%d,\"ny\":%d,\"asian\":%d},",
                     cntLondon, cntNY, cntAsian);
   j += StringFormat("\"spread_median_pips\":{\"london\":%.2f,\"ny\":%.2f,\"asian\":%.2f,\"overall\":%.2f},",
                     medLondon, medNY, medAsian, overall);
   j += StringFormat("\"spread_session_multipliers\":{\"london\":%.3f,\"ny\":%.3f,\"asian\":%.3f,\"default\":1.000},",
                     medLondon/baseSp, medNY/baseSp, medAsian/baseSp);
   j += StringFormat("\"trade_sessions_minutes\":{%s}", sessionsJson);
   j += "}";
   Print(j);
   Print(">>>BROKER_PROFILE_END>>>");

   Print("");
   Print("Copy everything between BROKER_PROFILE_BEGIN and BROKER_PROFILE_END (inclusive) and paste it back.");
   Print("Tip: for accurate per-session spreads, run with SpreadSampleSeconds=3600 during London, NY, and Asian hours separately.");
}
//+------------------------------------------------------------------+

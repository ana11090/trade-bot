//+------------------------------------------------------------------+
//| Broker Diagnostic Script                                          |
//| Prints symbol specs, pip value, lot size, margin, spread,        |
//| and indicator values so you can calibrate the Python backtester. |
//|                                                                    |
//| HOW TO USE:                                                        |
//| 1. Copy to [MT5 Data Folder]/MQL5/Scripts/                        |
//| 2. Compile in MetaEditor (F7)                                      |
//| 3. Drag onto chart for the symbol you want to test               |
//| 4. Open Experts tab (Ctrl+E) and read the output                 |
//| 5. Copy "RECOMMENDED PYTHON CONFIG" values to backtest_config.json|
//+------------------------------------------------------------------+
#property script_show_inputs

input string  Symbol_Override = "";   // Leave blank to use chart symbol
input double  TestLots        = 0.1;  // Lot size for margin/P&L test
input int     VolumePeriod    = 20;   // Period for volume ratio test

void OnStart()
{
   string sym = (Symbol_Override != "") ? Symbol_Override : _Symbol;

   Print("==========================================================");
   Print("BROKER DIAGNOSTIC — Symbol: ", sym);
   Print("==========================================================");

   // ── Symbol specs ──────────────────────────────────────────────────
   double tickValue    = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSize     = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double contractSize = SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE);
   double pointSize    = SymbolInfoDouble(sym, SYMBOL_POINT);
   int    digits       = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   long   spreadPts    = SymbolInfoInteger(sym, SYMBOL_SPREAD);
   double minLot       = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double maxLot       = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double lotStep      = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);

   Print("── SYMBOL SPECS ──");
   Print("  tickValue    = $", DoubleToString(tickValue, 5));
   Print("  tickSize     = ",  DoubleToString(tickSize, 8));
   Print("  contractSize = ",  DoubleToString(contractSize, 2));
   Print("  pointSize    = ",  DoubleToString(pointSize, 8));
   Print("  digits       = ",  digits);
   Print("  spread       = ",  spreadPts, " points (", DoubleToString(spreadPts * pointSize, digits), " price)");
   Print("  minLot/step/maxLot = ", minLot, " / ", lotStep, " / ", maxLot);

   // ── Pip value calculation ──────────────────────────────────────────
   // pip = tickSize for most instruments; for JPY pairs pip = 0.01
   double pipSize = tickSize;
   // Detect 5-digit forex: pip = 10 × point
   if(digits == 5 || digits == 3)
      pipSize = pointSize * 10;

   double pipValuePerLot = (tickValue / tickSize) * pipSize;

   Print("");
   Print("── PIP VALUE ──");
   Print("  pipSize          = ", DoubleToString(pipSize, 8));
   Print("  pipValuePerLot   = $", DoubleToString(pipValuePerLot, 5), " per pip per standard lot");
   Print("  (Python should use pip_value_per_lot = ", DoubleToString(NormalizeDouble(pipValuePerLot, 2), 2), ")");

   // ── Spread in pips ────────────────────────────────────────────────
   double spreadPips = (pipSize > 0) ? (spreadPts * pointSize) / pipSize : 0;
   Print("");
   Print("── SPREAD ──");
   Print("  Current spread   = ", DoubleToString(spreadPips, 1), " pips");
   Print("  Spread cost/lot  = $", DoubleToString(spreadPips * pipValuePerLot, 4));
   Print("  (Python typical_spread recommendation: ", DoubleToString(MathRound(spreadPips), 0), " pips)");
   Print("  NOTE: Spread during off-hours is wider. Run during London/NY for session spread.");

   // ── P&L check: 1 pip move on TestLots ────────────────────────────
   double pnlPerPip = TestLots * pipValuePerLot;
   Print("");
   Print("── P&L CHECK (", DoubleToString(TestLots, 2), " lots) ──");
   Print("  1 pip move P&L  = $", DoubleToString(pnlPerPip, 4));
   Print("  10 pip move P&L = $", DoubleToString(pnlPerPip * 10, 4));
   Print("  50 pip SL cost  = $", DoubleToString(pnlPerPip * 50, 4));

   // ── Margin check ──────────────────────────────────────────────────
   double ask          = SymbolInfoDouble(sym, SYMBOL_ASK);
   double marginNeeded = 0.0;
   Print("");
   Print("── MARGIN CHECK ──");
   Print("  Current ASK = ", DoubleToString(ask, digits));
   for(int i = 0; i < 5; i++)
   {
      double lots = NormalizeDouble(minLot + i * lotStep * 2, 2);
      if(lots > maxLot) break;
      if(OrderCalcMargin(ORDER_TYPE_BUY, sym, lots, ask, marginNeeded))
         Print("  ", DoubleToString(lots, 2), " lots → margin = $", DoubleToString(marginNeeded, 2));
   }
   double freeMar = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   int    leverage = (int)AccountInfoInteger(ACCOUNT_LEVERAGE);
   Print("  Account balance  = $", DoubleToString(balance, 2));
   Print("  Free margin      = $", DoubleToString(freeMar, 2));
   Print("  Account leverage = 1:", leverage);

   // ── Volume ratio test ─────────────────────────────────────────────
   Print("");
   Print("── VOLUME RATIO (last ", VolumePeriod, " closed bars) ──");
   double volSum = 0;
   for(int vi = 1; vi <= VolumePeriod; vi++)
      volSum += (double)iVolume(sym, PERIOD_CURRENT, vi);
   double volAvg  = volSum / VolumePeriod;
   double curVol  = (double)iVolume(sym, PERIOD_CURRENT, 1);
   double volRatio = (volAvg > 0) ? curVol / volAvg : 1.0;
   Print("  curVol = ", curVol, " | avgVol = ", DoubleToString(volAvg, 1), " | ratio = ", DoubleToString(volRatio, 4));

   // ── Day of month ──────────────────────────────────────────────────
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   Print("");
   Print("── CALENDAR ──");
   Print("  day_of_month = ", dt.day, " | hour = ", dt.hour, " | day_of_week = ", dt.day_of_week);

   // ── Summary / recommended config ─────────────────────────────────
   Print("");
   Print("==========================================================");
   Print("RECOMMENDED PYTHON CONFIG for ", sym, ":");
   Print("  pip_value_per_lot = ", DoubleToString(NormalizeDouble(pipValuePerLot, 4), 4));
   Print("  pip_size          = ", DoubleToString(pipSize, 8));
   Print("  typical_spread    = ", DoubleToString(MathRound(spreadPips), 0));
   Print("  (set these in P2 Configuration → Backtest Costs)");
   Print("==========================================================");
}
//+------------------------------------------------------------------+

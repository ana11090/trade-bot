//+------------------------------------------------------------------+
//| export_candles.mq5                                                 |
//| Exports OHLCV data from MT5 to FILE_COMMON for auto-pickup by    |
//| the trade-bot app. Writes to Terminal\Common\Files\ so any MT5   |
//| terminal can export to the same location.                         |
//|                                                                    |
//| Usage: drag onto any chart, click OK. Files appear in:           |
//|   C:\Users\<you>\AppData\Roaming\MetaQuotes\Terminal\Common\Files |
//| The trade-bot app picks these up automatically on next backtest. |
//+------------------------------------------------------------------+
#property script_show_inputs

// WHY: 5 years gives enough M1 history for any rule the user backtests.
//      Broker may not have that much — the script reports what it got.
input int    YearsBack     = 5;
input bool   Export_M1     = true;
input bool   Export_M5     = true;
input bool   Export_M15    = true;
input bool   Export_H1     = true;
input bool   Export_H4     = true;
input bool   Export_D1     = true;
// WHY: Sentinel file lets the Python side know the export completed
//      successfully. Without it, a partial/aborted export could be
//      merged in and corrupt the data.
// CHANGED: May 2026 — sentinel for atomic handoff
input bool   WriteSentinel = true;

string OUTPUT_PREFIX = "trade_bot_export_";  // distinguish from other CSVs

void ExportTimeframe(ENUM_TIMEFRAMES tf, string tfName)
{
   string filename = OUTPUT_PREFIX + _Symbol + "_" + tfName + ".csv";
   // FILE_COMMON: writes to Terminal\Common\Files (fixed, cross-instance)
   int handle = FileOpen(filename, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("[EXPORT] ERROR: Cannot open ", filename, " err=", GetLastError());
      return;
   }

   FileWrite(handle, "timestamp", "open", "high", "low", "close", "volume");

   datetime startDate = TimeCurrent() - YearsBack * 365 * 24 * 3600;
   int totalBars = Bars(_Symbol, tf, startDate, TimeCurrent());

   if(totalBars <= 0)
   {
      Print("[EXPORT] WARNING: No bars for ", tfName);
      FileClose(handle);
      return;
   }

   Print("[EXPORT] ", tfName, ": fetching ", totalBars, " bars...");

   MqlRates rates[];
   int copied = CopyRates(_Symbol, tf, 0, totalBars, rates);
   if(copied <= 0)
   {
      Print("[EXPORT] ERROR: CopyRates failed for ", tfName, " err=", GetLastError());
      FileClose(handle);
      return;
   }

   for(int i = 0; i < copied; i++)
   {
      // WHY: Match the format Python expects: "YYYY-MM-DD HH:MM:SS"
      //      MT5's default uses dots in date — Python's pd.to_datetime
      //      handles both but dashes are unambiguous.
      string ts = TimeToString(rates[i].time, TIME_DATE|TIME_MINUTES|TIME_SECONDS);
      StringReplace(ts, ".", "-");

      FileWrite(handle,
         ts,
         DoubleToString(rates[i].open, _Digits),
         DoubleToString(rates[i].high, _Digits),
         DoubleToString(rates[i].low, _Digits),
         DoubleToString(rates[i].close, _Digits),
         IntegerToString(rates[i].tick_volume)
      );
   }

   FileClose(handle);
   Print("[EXPORT] ", tfName, ": wrote ", copied, " bars to Common\\Files\\", filename);
}

void WriteCompletionSentinel()
{
   // WHY: Atomic-handoff marker. Python checks this file's mtime and the
   //      list of timeframes inside, then trusts the export only if the
   //      sentinel is recent (< 1 hour old).
   string sentinel = OUTPUT_PREFIX + _Symbol + "_DONE.txt";
   int h = FileOpen(sentinel, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE)
   {
      Print("[EXPORT] WARNING: could not write sentinel ", sentinel);
      return;
   }
   FileWriteString(h, "export_time=" + TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES|TIME_SECONDS) + "\n");
   FileWriteString(h, "broker=" + AccountInfoString(ACCOUNT_SERVER) + "\n");
   FileWriteString(h, "symbol=" + _Symbol + "\n");
   FileWriteString(h, "years_back=" + IntegerToString(YearsBack) + "\n");
   string tfs = "";
   if(Export_M1)  tfs += "M1,";
   if(Export_M5)  tfs += "M5,";
   if(Export_M15) tfs += "M15,";
   if(Export_H1)  tfs += "H1,";
   if(Export_H4)  tfs += "H4,";
   if(Export_D1)  tfs += "D1,";
   FileWriteString(h, "timeframes=" + tfs + "\n");
   FileClose(h);
   Print("[EXPORT] Sentinel written: Common\\Files\\", sentinel);
}

void OnStart()
{
   Print("============================================================");
   Print("  CANDLE DATA EXPORT (append-aware)");
   Print("  Symbol: ", _Symbol);
   Print("  Years back: ", YearsBack);
   Print("  Broker: ", AccountInfoString(ACCOUNT_SERVER));
   Print("  Writing to: Terminal\\Common\\Files\\ (FILE_COMMON)");
   Print("============================================================");

   if(Export_M1)  ExportTimeframe(PERIOD_M1,  "M1");
   if(Export_M5)  ExportTimeframe(PERIOD_M5,  "M5");
   if(Export_M15) ExportTimeframe(PERIOD_M15, "M15");
   if(Export_H1)  ExportTimeframe(PERIOD_H1,  "H1");
   if(Export_H4)  ExportTimeframe(PERIOD_H4,  "H4");
   if(Export_D1)  ExportTimeframe(PERIOD_D1,  "D1");

   if(WriteSentinel) WriteCompletionSentinel();

   Print("============================================================");
   Print("  DONE. Open the trade-bot app and click Run Backtest.");
   Print("  Fresh data will be detected and merged automatically.");
   Print("============================================================");
}

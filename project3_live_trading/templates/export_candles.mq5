//+------------------------------------------------------------------+
//| export_candles.mq5                                                 |
//| Exports OHLCV data from MT5 to CSV for Python backtester          |
//+------------------------------------------------------------------+
#property script_show_inputs

input int    YearsBack     = 5;
// WHY: M1 candles provide 60 sub-bars per H1 candle for intra-candle
//      exit simulation. Smaller than tick data (~750K rows for 2 years
//      vs 25M rows for ticks) but resolves 95%+ of ambiguity.
// CHANGED: April 2026 — M1 export
input bool   Export_M1     = true;
input bool   Export_M5     = true;
input bool   Export_M15    = true;
input bool   Export_H1     = true;
input bool   Export_H4     = true;
input bool   Export_D1     = true;

void ExportTimeframe(ENUM_TIMEFRAMES tf, string tfName)
{
   string filename = _Symbol + "_" + tfName + ".csv";
   int handle = FileOpen(filename, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("ERROR: Cannot open file ", filename);
      return;
   }

   FileWrite(handle, "timestamp", "open", "high", "low", "close", "volume");

   datetime startDate = TimeCurrent() - YearsBack * 365 * 24 * 3600;
   int totalBars = Bars(_Symbol, tf, startDate, TimeCurrent());

   if(totalBars <= 0)
   {
      Print("WARNING: No bars for ", tfName);
      FileClose(handle);
      return;
   }

   Print("Exporting ", totalBars, " bars for ", _Symbol, " ", tfName, "...");

   MqlRates rates[];
   int copied = CopyRates(_Symbol, tf, 0, totalBars, rates);

   if(copied <= 0)
   {
      Print("ERROR: CopyRates failed for ", tfName);
      FileClose(handle);
      return;
   }

   for(int i = 0; i < copied; i++)
   {
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
   Print("Exported ", copied, " bars to: MQL5/Files/", filename);
}

void OnStart()
{
   Print("============================================================");
   Print("  CANDLE DATA EXPORT");
   Print("  Symbol: ", _Symbol);
   Print("  Years back: ", YearsBack);
   Print("  Broker: ", AccountInfoString(ACCOUNT_SERVER));
   Print("  Server time: ", TimeToString(TimeCurrent()));
   Print("  GMT time: ", TimeToString(TimeGMT()));
   Print("  Offset: server - GMT = ", (int)(TimeCurrent() - TimeGMT()) / 3600, " hours");
   Print("============================================================");

   if(Export_M1)  ExportTimeframe(PERIOD_M1,  "M1");
   if(Export_M5)  ExportTimeframe(PERIOD_M5,  "M5");
   if(Export_M15) ExportTimeframe(PERIOD_M15, "M15");
   if(Export_H1)  ExportTimeframe(PERIOD_H1,  "H1");
   if(Export_H4)  ExportTimeframe(PERIOD_H4,  "H4");
   if(Export_D1)  ExportTimeframe(PERIOD_D1,  "D1");

   Print("DONE — Files saved in MQL5/Files/ folder");
}

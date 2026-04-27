//+------------------------------------------------------------------+
//| export_ticks.mq5                                                   |
//| Exports tick data from MT5 to CSV for Python backtester            |
//| WHY: Tick data resolves intra-candle exit ambiguity (breakeven     |
//|      vs SL ordering). Stored in the same data source folder as     |
//|      candle CSVs.                                                   |
//| CHANGED: April 2026 — tick data export for backtester parity       |
//+------------------------------------------------------------------+
#property script_show_inputs

input int    YearsBack     = 3;     // How many years of tick data to export
input bool   SplitMonthly  = true;  // Split into monthly files (recommended)

void OnStart()
{
   Print("============================================================");
   Print("  TICK DATA EXPORT");
   Print("  Symbol: ", _Symbol);
   Print("  Years back: ", YearsBack);
   Print("  Broker: ", AccountInfoString(ACCOUNT_SERVER));
   Print("  Split monthly: ", SplitMonthly ? "YES" : "NO");
   Print("============================================================");

   datetime endDate   = TimeCurrent();
   datetime startDate = endDate - YearsBack * 365 * 24 * 3600;

   if(SplitMonthly)
   {
      MqlDateTime dtStart, dtEnd;
      TimeToStruct(startDate, dtStart);
      TimeToStruct(endDate, dtEnd);

      int startYear  = dtStart.year;
      int startMonth = dtStart.mon;
      int endYear    = dtEnd.year;
      int endMonth   = dtEnd.mon;

      for(int year = startYear; year <= endYear; year++)
      {
         int mStart = (year == startYear) ? startMonth : 1;
         int mEnd   = (year == endYear)   ? endMonth   : 12;

         for(int month = mStart; month <= mEnd; month++)
         {
            datetime monthStart = StringToTime(StringFormat("%04d.%02d.01 00:00:00", year, month));
            datetime monthEnd;
            if(month == 12)
               monthEnd = StringToTime(StringFormat("%04d.01.01 00:00:00", year + 1));
            else
               monthEnd = StringToTime(StringFormat("%04d.%02d.01 00:00:00", year, month + 1));

            if(monthStart < startDate) monthStart = startDate;
            if(monthEnd   > endDate)   monthEnd   = endDate;

            ExportTickRange(monthStart, monthEnd,
                            StringFormat("%s_ticks_%04d_%02d", _Symbol, year, month));
         }
      }
   }
   else
   {
      ExportTickRange(startDate, endDate, _Symbol + "_ticks");
   }

   Print("DONE — Files saved in MQL5/Files/ folder");
}

void ExportTickRange(datetime from, datetime to, string baseName)
{
   MqlTick ticks[];
   int copied = CopyTicksRange(_Symbol, ticks, (long)from * 1000, (long)to * 1000, COPY_TICKS_ALL);

   if(copied <= 0)
   {
      Print("No ticks for ", baseName, " (", TimeToString(from), " to ", TimeToString(to), ")");
      return;
   }

   string filename = baseName + ".csv";
   int handle = FileOpen(filename, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("ERROR: Cannot open file ", filename);
      return;
   }

   // WHY: Only export bid and ask — that's all the backtester needs
   //      for SL/TP simulation. Keeps file size manageable.
   // CHANGED: April 2026 — minimal tick format
   FileWrite(handle, "timestamp_ms", "bid", "ask");

   for(int i = 0; i < copied; i++)
   {
      FileWrite(handle,
         IntegerToString(ticks[i].time_msc),
         DoubleToString(ticks[i].bid, _Digits),
         DoubleToString(ticks[i].ask, _Digits)
      );
   }

   FileClose(handle);
   Print("Exported ", copied, " ticks to: MQL5/Files/", filename);
}

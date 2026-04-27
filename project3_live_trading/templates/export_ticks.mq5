//+------------------------------------------------------------------+
//| export_ticks.mq5                                                   |
//| Exports tick data from MT5 to CSV for Python backtester            |
//| WHY: Tick data resolves intra-candle exit ambiguity (breakeven     |
//|      vs SL ordering). Stored in the same data source folder as     |
//|      candle CSVs.                                                   |
//| CHANGED: April 2026 — tick data export for backtester parity       |
//| CHANGED: April 2026 — weekly chunks + sync + retry for older data  |
//+------------------------------------------------------------------+
#property script_show_inputs

input int    YearsBack     = 3;     // How many years of tick data to export
input int    ChunkDays     = 7;     // Days per chunk (7=weekly, smaller=more reliable)
input int    RetryCount    = 3;     // Retries per chunk if sync timeout
input int    SyncWaitSec   = 10;    // Seconds to wait between retries for sync

void OnStart()
{
   Print("============================================================");
   Print("  TICK DATA EXPORT (chunked + sync retry)");
   Print("  Symbol: ", _Symbol);
   Print("  Years back: ", YearsBack);
   Print("  Chunk size: ", ChunkDays, " days");
   Print("  Retries: ", RetryCount, " per chunk");
   Print("  Broker: ", AccountInfoString(ACCOUNT_SERVER));
   Print("  Server time: ", TimeToString(TimeCurrent()));
   Print("============================================================");

   datetime endDate   = TimeCurrent();
   datetime startDate = endDate - YearsBack * 365 * 24 * 3600;

   // WHY: Pre-trigger tick sync by requesting a small batch from the
   //      earliest date. This tells the broker to start downloading
   //      tick history. Without this, CopyTicksRange on old dates
   //      returns 0 because the data hasn't been synced yet.
   // CHANGED: April 2026 — pre-sync trigger
   Print("Pre-syncing tick data from ", TimeToString(startDate), "...");
   Print("This may take a few minutes. Please wait.");
   MqlTick presync[];
   CopyTicksRange(_Symbol, presync, (long)startDate * 1000,
                  (long)(startDate + 86400) * 1000, COPY_TICKS_ALL);
   Sleep(5000);  // Give the terminal time to start background sync

   // Export month by month, but each month is split into weekly chunks
   MqlDateTime dtStart, dtEnd;
   TimeToStruct(startDate, dtStart);
   TimeToStruct(endDate, dtEnd);

   int startYear  = dtStart.year;
   int startMonth = dtStart.mon;
   int endYear    = dtEnd.year;
   int endMonth   = dtEnd.mon;

   int totalExported = 0;
   int totalSkipped  = 0;

   for(int year = startYear; year <= endYear; year++)
   {
      int mStart = (year == startYear) ? startMonth : 1;
      int mEnd   = (year == endYear)   ? endMonth   : 12;

      for(int month = mStart; month <= mEnd; month++)
      {
         if(IsStopped()) { Print("Script stopped by user"); return; }

         datetime monthStart = StringToTime(StringFormat("%04d.%02d.01 00:00:00", year, month));
         datetime monthEnd;
         if(month == 12)
            monthEnd = StringToTime(StringFormat("%04d.01.01 00:00:00", year + 1));
         else
            monthEnd = StringToTime(StringFormat("%04d.%02d.01 00:00:00", year, month + 1));

         // Clamp to requested range
         if(monthStart < startDate) monthStart = startDate;
         if(monthEnd   > endDate)   monthEnd   = endDate;
         if(monthStart >= monthEnd) continue;

         string filename = StringFormat("%s_ticks_%04d_%02d", _Symbol, year, month);

         Print("--- ", filename, " ---");

         // Open file for this month
         string filepath = filename + ".csv";
         int handle = FileOpen(filepath, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
         if(handle == INVALID_HANDLE)
         {
            Print("ERROR: Cannot open file ", filepath);
            continue;
         }
         FileWrite(handle, "timestamp_ms", "bid", "ask");

         int monthTicks = 0;

         // Process in weekly chunks
         datetime chunkStart = monthStart;
         while(chunkStart < monthEnd)
         {
            if(IsStopped()) { FileClose(handle); Print("Script stopped by user"); return; }

            datetime chunkEnd = chunkStart + ChunkDays * 86400;
            if(chunkEnd > monthEnd) chunkEnd = monthEnd;

            // Retry loop for sync timeouts
            int copied = 0;
            for(int attempt = 1; attempt <= RetryCount; attempt++)
            {
               MqlTick ticks[];
               copied = CopyTicksRange(_Symbol, ticks,
                                       (long)chunkStart * 1000,
                                       (long)chunkEnd * 1000,
                                       COPY_TICKS_ALL);

               if(copied > 0)
               {
                  // Write ticks to file
                  for(int i = 0; i < copied; i++)
                  {
                     FileWrite(handle,
                        IntegerToString(ticks[i].time_msc),
                        DoubleToString(ticks[i].bid, _Digits),
                        DoubleToString(ticks[i].ask, _Digits)
                     );
                  }
                  monthTicks += copied;
                  break;  // success, move to next chunk
               }

               if(attempt < RetryCount)
               {
                  // WHY: CopyTicksRange returns 0 when data needs syncing
                  //      from the broker server. Sleep and retry gives the
                  //      terminal time to complete the background download.
                  // CHANGED: April 2026 — sync retry
                  Print("  Chunk ", TimeToString(chunkStart), "-", TimeToString(chunkEnd),
                        ": 0 ticks (attempt ", attempt, "/", RetryCount,
                        "), waiting ", SyncWaitSec, "s for sync...");
                  Sleep(SyncWaitSec * 1000);
               }
               else
               {
                  Print("  Chunk ", TimeToString(chunkStart), "-", TimeToString(chunkEnd),
                        ": 0 ticks after ", RetryCount, " attempts — skipping");
                  totalSkipped++;
               }
            }

            chunkStart = chunkEnd;
         }

         FileClose(handle);

         if(monthTicks > 0)
         {
            Print("Exported ", monthTicks, " ticks to: MQL5/Files/", filepath);
            totalExported += monthTicks;
         }
         else
         {
            // Delete empty file
            FileDelete(filepath);
            Print("No ticks for ", filename, " — file removed");
         }
      }
   }

   Print("============================================================");
   Print("  DONE");
   Print("  Total ticks exported: ", totalExported);
   Print("  Chunks skipped (no data): ", totalSkipped);
   Print("  Files saved in MQL5/Files/ folder");
   Print("============================================================");
}

//+------------------------------------------------------------------+
//| export_ticks.mq5                                                   |
//| Exports tick data from MT5 to CSV for Python backtester            |
//|                                                                    |
//| WHY: Tick data resolves intra-candle exit ambiguity (breakeven    |
//|      vs SL ordering). Stored in the same data source folder as    |
//|      candle CSVs.                                                  |
//|                                                                    |
//| CHANGED: April 2026 — tick data export for backtester parity      |
//| CHANGED: April 2026 — weekly chunks + sync + retry for older data |
//| CHANGED: April 2026 — atomic writes + skip-completed-months +     |
//|                       no-delete on empty (Ana's 0-byte loop bug)  |
//+------------------------------------------------------------------+
#property script_show_inputs

input int    YearsBack       = 3;     // How many years of tick data to export
input int    ChunkDays       = 7;     // Days per chunk (smaller=more reliable)
input int    RetryCount      = 3;     // Retries per chunk if sync timeout
input int    SyncWaitSec     = 10;    // Seconds to wait between retries for sync
input bool   SkipExisting    = true;  // Skip months that already have a populated file
input int    MinTicksToKeep  = 100;   // Treat any existing file with >= N ticks as complete

//+------------------------------------------------------------------+
//| Helper: count rows in an existing CSV (excluding header).         |
//| Returns -1 if file doesn't exist or can't be opened.              |
//+------------------------------------------------------------------+
int CountExistingRows(string filepath)
{
   if(!FileIsExist(filepath))
      return -1;

   int handle = FileOpen(filepath, FILE_READ|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
      return -1;

   int rows = 0;
   while(!FileIsEnding(handle))
   {
      string line = FileReadString(handle);
      if(line == "" && FileIsEnding(handle)) break;
      rows++;
   }
   FileClose(handle);

   // Subtract 1 for the header row, but never return negative.
   return (rows > 0) ? rows - 1 : 0;
}

void OnStart()
{
   Print("============================================================");
   Print("  TICK DATA EXPORT (atomic + resumable)");
   Print("  Symbol: ", _Symbol);
   Print("  Years back: ", YearsBack);
   Print("  Chunk size: ", ChunkDays, " days");
   Print("  Retries: ", RetryCount, " per chunk");
   Print("  Skip existing: ", SkipExisting ? "YES" : "NO");
   Print("  Broker: ", AccountInfoString(ACCOUNT_SERVER));
   Print("  Server time: ", TimeToString(TimeCurrent()));
   Print("============================================================");

   datetime endDate   = TimeCurrent();
   datetime startDate = endDate - YearsBack * 365 * 24 * 3600;

   // WHY: Pre-trigger tick sync. Without this, CopyTicksRange on old
   //      dates returns 0 because the data hasn't been synced yet.
   // CHANGED: April 2026 — pre-sync trigger
   Print("Pre-syncing tick data from ", TimeToString(startDate), "...");
   Print("This may take a few minutes. Please wait.");
   {
      MqlTick presyncTicks[];
      CopyTicksRange(_Symbol, presyncTicks,
                     (long)startDate * 1000,
                     (long)(startDate + 86400) * 1000,
                     COPY_TICKS_ALL);
      Sleep(5000);
   }

   MqlDateTime dtStart, dtEnd;
   TimeToStruct(startDate, dtStart);
   TimeToStruct(endDate,   dtEnd);

   int startYear  = dtStart.year;
   int startMonth = dtStart.mon;
   int endYear    = dtEnd.year;
   int endMonth   = dtEnd.mon;

   long totalExported = 0;
   int  totalSkipped  = 0;
   int  monthsSkipped = 0;
   int  monthsEmpty   = 0;

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

         if(monthStart < startDate) monthStart = startDate;
         if(monthEnd   > endDate)   monthEnd   = endDate;
         if(monthStart >= monthEnd) continue;

         string filename  = StringFormat("%s_ticks_%04d_%02d", _Symbol, year, month);
         string filepath  = filename + ".csv";
         string tmppath   = filename + ".csv.partial";
         string emptypath = filename + ".empty";

         // ─── BUG FIX 1: Skip months that already have a populated file ───
         // WHY: Without this guard the script truncates and re-downloads
         //      every month on every restart, including months that
         //      already completed successfully. MT5 auto-restarts a
         //      script when it's recompiled in MetaEditor — combined
         //      with this missing guard, the user saw "loops by itself."
         // CHANGED: April 2026 — skip-existing guard
         if(SkipExisting)
         {
            int existing = CountExistingRows(filepath);
            if(existing >= MinTicksToKeep)
            {
               Print("--- ", filename, " — already has ", existing,
                     " rows, skipping ---");
               monthsSkipped++;
               continue;
            }
            // Also skip if we already know this month has no broker data.
            if(FileIsExist(emptypath))
            {
               Print("--- ", filename, " — .empty marker exists, skipping ",
                     "(delete ", emptypath, " to retry) ---");
               monthsSkipped++;
               continue;
            }
         }

         // Clean up any leftover .partial file from a previous interrupted run.
         if(FileIsExist(tmppath))
            FileDelete(tmppath);

         Print("--- ", filename, " ---");

         // ─── BUG FIX 2: Write to .partial first, rename when done ───
         // WHY: FileOpen(..., FILE_WRITE) truncates the target file to
         //      0 bytes IMMEDIATELY, before any data is fetched. While
         //      the chunk loop runs (seconds to minutes per month), the
         //      real file sits at 0 bytes — that's what the user saw in
         //      Explorer. Writing to .partial leaves the previous .csv
         //      (if any) intact until we have something better.
         // CHANGED: April 2026 — atomic write via .partial
         int handle = FileOpen(tmppath, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
         if(handle == INVALID_HANDLE)
         {
            Print("ERROR: Cannot open file ", tmppath);
            continue;
         }
         FileWrite(handle, "timestamp_ms", "bid", "ask");

         long monthTicks = 0;
         datetime chunkStart = monthStart;
         while(chunkStart < monthEnd)
         {
            if(IsStopped())
            {
               FileClose(handle);
               // Don't promote partial to final on user-stop.
               Print("Script stopped by user (partial kept at ", tmppath, ")");
               return;
            }

            datetime chunkEnd = chunkStart + ChunkDays * 86400;
            if(chunkEnd > monthEnd) chunkEnd = monthEnd;

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
                  for(int i = 0; i < copied; i++)
                  {
                     FileWrite(handle,
                        IntegerToString(ticks[i].time_msc),
                        DoubleToString(ticks[i].bid, _Digits),
                        DoubleToString(ticks[i].ask, _Digits)
                     );
                  }
                  monthTicks += copied;
                  break;
               }

               if(attempt < RetryCount)
               {
                  // WHY: CopyTicksRange returns 0 when data needs syncing
                  //      from the broker server. Sleep and retry gives the
                  //      terminal time to complete the background download.
                  // CHANGED: April 2026 — sync retry
                  Print("  Chunk ", TimeToString(chunkStart), "-",
                        TimeToString(chunkEnd),
                        ": 0 ticks (attempt ", attempt, "/", RetryCount,
                        "), waiting ", SyncWaitSec, "s for sync...");
                  Sleep(SyncWaitSec * 1000);
               }
               else
               {
                  Print("  Chunk ", TimeToString(chunkStart), "-",
                        TimeToString(chunkEnd),
                        ": 0 ticks after ", RetryCount, " attempts — skipping");
                  totalSkipped++;
               }
            }

            chunkStart = chunkEnd;
         }

         FileClose(handle);

         // ─── BUG FIX 3: Don't delete on empty — write .empty marker ───
         // WHY: The old code did FileDelete(filepath) when monthTicks==0.
         //      Combined with no skip-existing guard, this caused
         //      "file deleted, another file appears at 0 bytes" — files
         //      were being repeatedly deleted and recreated each restart.
         //      Now: if a month genuinely has no broker data, we keep
         //      a .empty marker so the next run skips it automatically.
         //      Delete the .empty file manually to force a retry.
         // CHANGED: April 2026 — no-delete on empty + .empty marker
         if(monthTicks > 0)
         {
            // Promote .partial → .csv (overwrites any stale file).
            if(FileIsExist(filepath))
               FileDelete(filepath);
            if(!FileMove(tmppath, 0, filepath, 0))
               Print("WARNING: could not rename ", tmppath, " to ", filepath);
            Print("Exported ", monthTicks, " ticks to: MQL5/Files/", filepath);
            totalExported += monthTicks;
         }
         else
         {
            // Move .partial to .empty as a marker so next pass skips this month.
            if(FileIsExist(emptypath))
               FileDelete(emptypath);
            FileMove(tmppath, 0, emptypath, 0);
            Print("No ticks for ", filename,
                  " — marked as .empty (delete the marker to retry)");
            monthsEmpty++;
         }
      }
   }

   Print("============================================================");
   Print("  DONE");
   Print("  Total ticks exported: ", totalExported);
   Print("  Months skipped (already complete): ", monthsSkipped);
   Print("  Months with no broker data (.empty): ", monthsEmpty);
   Print("  Chunks skipped (sync failed): ", totalSkipped);
   Print("  Files saved in MQL5/Files/ folder");
   Print("============================================================");
}

"""
STEP 1 — ALIGN PRICE DATA (Multi-Timeframe)
Aligns trade timestamps with OHLCV candle data across ALL available timeframes.
For each trade, finds the corresponding candle at M5, M15, H1, H4, and D1.
"""

import sys
import os
import pandas as pd

# Add parent directory to path to import from shared
PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, PROJECT_ROOT)

from shared.trade_history_manager import get_active_history, get_history_trades_path
from config_loader import load as _load_cfg

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

# ── Paths (always relative to this file) ─────────────────────────────────────
# WHY: Data source comes from P1 config (set by dropdown in Run Scenarios).
#      Hardcoded 'data/' ignored the selection. Fall back to 'data/' if
#      config doesn't have a data source path.
# CHANGED: April 2026 — read data path from P1 config
def _get_price_data_folder():
    # WHY (June 2026 portability fix): The UI's "Historical Data Source"
    #      dropdown stores `data_source_id`; the repo-relative folder
    #      `data/sources/<id>/` is what actually holds the CSVs and is
    #      independent of which drive/path the repo was cloned to.
    #      The previous code read the absolute `data_source_path` directly,
    #      so moving the repo (or re-cloning) silently broke alignment.
    #      Priority now: data_source_id (portable) → absolute path (if it
    #      exists on THIS machine) → generic PROJECT_ROOT/data (last resort).
    # CHANGED: June 2026 — id-first portable data-source resolution
    try:
        import importlib.util
        _cl_path = os.path.join(PROJECT_ROOT, 'project1_reverse_engineering', 'config_loader.py')
        _spec = importlib.util.spec_from_file_location('_cl', _cl_path)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _cfg = _mod.load()

        # 1) PORTABLE: resolve by data_source_id relative to the repo.
        _sid = _cfg.get('data_source_id', '')
        if _sid:
            try:
                from shared.data_sources import get_source_path
                _by_id = get_source_path(_sid)
                if _by_id and os.path.isdir(_by_id):
                    # only accept if it actually has candle CSVs (not the
                    # generic fallback / a placeholder directory)
                    import glob as _glob
                    if _glob.glob(os.path.join(_by_id, '*.csv')):
                        try:
                            log.info("    [DATA] Using data source by id '" + str(_sid)
                                     + "': " + _by_id)
                        except Exception:
                            pass
                        return _by_id
            except Exception as _se:
                try:
                    log.info("    [DATA] get_source_path('" + str(_sid)
                             + "') failed: " + str(_se))
                except Exception:
                    pass

        # 2) configured absolute path, only if it exists on THIS machine
        _path = _cfg.get('data_source_path', '')
        if _path and os.path.isdir(_path):
            return _path
        if _path:
            try:
                log.warning("    [DATA] Configured data_source_path does not exist: "
                            + str(_path) + " — and id-based lookup didn't resolve. "
                            "Falling back to PROJECT_ROOT/data. Fix the dataset in "
                            "Configuration & Data.")
            except Exception:
                pass
    except Exception:
        pass
    # 3) generic fallback
    return os.path.join(PROJECT_ROOT, 'data')

PRICE_DATA_FOLDER = _get_price_data_folder()
OUTPUT_FOLDER     = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

# ── Configuration ─────────────────────────────────────────────────────────────
_cfg                 = _load_cfg()
SYMBOL               = _cfg['symbol']
ALIGN_TIMEFRAMES     = _cfg['align_timeframes'].split(',')
MIN_LOOKBACK_CANDLES = int(_cfg['min_lookback_candles'])
ALIGNMENT_TOLERANCE  = float(_cfg['alignment_tolerance_pips'])
# WHY: Hardcoded 0.01 only applies to XAUUSD with 2-decimal pricing.
#      Reading from config lets forex (0.0001) and JPY pairs (0.01) work correctly.
# CHANGED: April 2026 — pip size from config
PIP_SIZE             = float(_cfg.get('pip_size', '0.01'))
# WHY (Phase 63 Fix 2): Old code computed ALIGNMENT_TOLERANCE * PIP_SIZE at
#      two different call sites. If config changed between them the two could
#      diverge. Pre-compute once at module level.
# CHANGED: April 2026 — Phase 63 Fix 2 — DRY tolerance constant
#          (audit Part D LOW #32)
ALIGNMENT_TOLERANCE_RAW = ALIGNMENT_TOLERANCE * PIP_SIZE   # in price units


def _parse_candle_timestamps(series):
    """Parse a candle 'timestamp' column ROBUSTLY and CONSISTENTLY.

    WHY: candle CSVs here use US M/D/YYYY H:MM (e.g. '5/3/2021 0:00'). Calling
         pd.to_datetime with NO format lets pandas guess PER VALUE — it can read
         '5/3' as May-3 or Mar-5 and may even switch mid-column. Wrong dates make
         trades align to wrong-date candles, producing huge price gaps (~2849-pip
         median miss). Fix: pick the format that parses the MOST rows for THIS file
         and parse the whole column with that ONE format, so every row is
         interpreted identically. Adapts per file (US, ISO, dot) — not hardcoded.
    CHANGED: June 2026 — consistent candle timestamp parsing (date-format bug)
    """
    s = series.astype(str).str.strip()
    sample = s.head(200)
    candidate_formats = [
        '%m/%d/%Y %H:%M',    # 5/3/2021 0:00   (US, these files)
        '%m/%d/%Y %H:%M:%S',
        '%Y-%m-%d %H:%M:%S', # ISO
        '%Y-%m-%d %H:%M',
        '%Y.%m.%d %H:%M:%S', # dot (MT5 exports)
        '%d/%m/%Y %H:%M',    # EU day-first (only if it wins)
    ]
    best_fmt, best_ok = None, -1
    for fmt in candidate_formats:
        ok = pd.to_datetime(sample, format=fmt, errors='coerce').notna().sum()
        if ok > best_ok:
            best_ok, best_fmt = ok, fmt
    if best_fmt is not None and best_ok > 0:
        out = pd.to_datetime(s, format=best_fmt, errors='coerce')
        if out.isna().mean() > 0.02:   # fill stragglers the chosen format missed
            _gen = pd.to_datetime(s[out.isna()], errors='coerce', dayfirst=False)
            out.loc[out.isna()] = _gen
        return out
    return pd.to_datetime(s, errors='coerce', dayfirst=False)


def _parse_trade_timestamps(series):
    """Parse trade open/close timestamps ROBUSTLY.
    WHY: canonical trades are YYYY.MM.DD HH:MM:SS (year-first, unambiguous). The old
         dayfirst=True read 2025.03.12 as Dec-3 instead of Mar-12, aligning trades to
         wrong-date candles (the ~2849-pip miss root cause). Detect the format that
         parses the most rows and use it for the whole column.
    CHANGED: June 2026 — fix dayfirst date flip on YYYY.MM.DD trade times
    """
    s = series.astype(str).str.strip()
    sample = s.head(200)
    candidate_formats = [
        '%Y.%m.%d %H:%M:%S',  # 2025.03.12 14:05:00  (canonical — year first)
        '%Y.%m.%d %H:%M',
        '%Y-%m-%d %H:%M:%S',  # ISO
        '%Y-%m-%d %H:%M',
        '%m/%d/%Y %H:%M:%S',  # US slash
        '%m/%d/%Y %H:%M',
        '%d/%m/%Y %H:%M:%S',  # EU day-first (only if it wins)
        '%d/%m/%Y %H:%M',
    ]
    best_fmt, best_ok = None, -1
    for fmt in candidate_formats:
        ok = pd.to_datetime(sample, format=fmt, errors='coerce').notna().sum()
        if ok > best_ok:
            best_ok, best_fmt = ok, fmt
    if best_fmt is not None and best_ok > 0:
        out = pd.to_datetime(s, format=best_fmt, errors='coerce')
        if out.isna().mean() > 0.02:
            # year-first files are unambiguous; fall back month-first (NOT dayfirst)
            _gen = pd.to_datetime(s[out.isna()], errors='coerce', dayfirst=False)
            out.loc[out.isna()] = _gen
        return out
    return pd.to_datetime(s, errors='coerce', dayfirst=False)


def _get_trades_path():
    """Get trades CSV path from workspace system, fallback to legacy path."""
    active = get_active_history()
    if active:
        return get_history_trades_path(active["history_id"])
    # Fallback to legacy path
    legacy = os.path.join(PROJECT_ROOT, 'trade_histories', 'original_bot', 'trades_clean.csv')
    if os.path.exists(legacy):
        return legacy
    raise FileNotFoundError("No active trade history found. Load trades first.")


# WHY: Single source of truth for "how many trades agree with the candle clock".
#      Both candidate clocks (legacy auto-detect offset vs firm-tz DST) need to
#      be scored with EXACTLY the same metric before we decide which to keep,
#      otherwise the comparison is unfair. _detect_best_offset uses this for
#      each offset; the align_all_timeframes block uses it for the firm-tz path.
# CHANGED: June 2026 — extracted verification scorer
# CHANGED: June 2026 — open OR high-low range match (market-fill aware)
def _verify_count(trades_df, candles_sorted, tol_price, return_detail=False):
    """Count trades whose entry_price is consistent with the matched candle.
    Verifies if EITHER: matches candle OPEN within tol (bar-open logs), OR falls
    within candle HIGH-LOW +/- tol (intra-bar MARKET fills).
    WHY: open-only matching made market-fill exports (Gold Reaper) look ~28%
         unverified when they were aligned correctly. open-OR-range keeps bar-open
         files unchanged (open is inside the range) and correctly credits fills.
    CHANGED: June 2026 — open OR high-low range match (market-fill aware)

    trades_df must already be on the clock under test (open_time shifted /
    converted as appropriate). candles_sorted must be sorted by 'timestamp'.
    Returns int, or (int, int, int) when return_detail=True (total, open, range).
    """
    ts = trades_df.sort_values('open_time')
    have_ohl = all(c in candles_sorted.columns for c in ('open', 'high', 'low'))
    if have_ohl:
        cols = ['timestamp', 'open', 'high', 'low']
    elif 'open' in candles_sorted.columns:
        cols = ['timestamp', 'open']
    else:
        cols = ['timestamp', 'high', 'low']
    merged = pd.merge_asof(
        ts[['open_time', 'entry_price']],
        candles_sorted[cols],
        left_on='open_time',
        right_on='timestamp',
        direction='forward',
        tolerance=pd.Timedelta(hours=2),
    )
    ep = merged['entry_price']
    open_ok  = ((ep - merged['open']).abs() <= tol_price
                if 'open' in merged.columns else None)
    range_ok = (((ep >= merged['low'] - tol_price) & (ep <= merged['high'] + tol_price))
                if ('high' in merged.columns and 'low' in merged.columns) else None)
    if open_ok is not None and range_ok is not None:
        verified = open_ok | range_ok
    elif open_ok is not None:
        verified = open_ok
    else:
        verified = range_ok
    if return_detail:
        return (int(verified.sum()),
                int(open_ok.sum())  if open_ok  is not None else 0,
                int(range_ok.sum()) if range_ok is not None else 0)
    return int(verified.sum())


def _detect_best_offset(trades_df, candles_dict, candidate_offsets=None):
    """
    Find the timezone offset (in hours) that maximizes verification rate.

    WHY: Broker server timezone often differs from candle data timezone.
         Instead of asking the user to figure it out, we try each offset
         from -12 to +12 hours and pick the one where the most trades
         have entry_price inside the matching candle's high-low range.

    Args:
        trades_df: DataFrame with 'open_time' and 'entry_price'
        candles_dict: dict of {tf: candles_df} for sampling
        candidate_offsets: list of hours to try (default: -12 to +12)

    Returns:
        Best offset in hours (int).

    CHANGED: April 2026 — auto-detect timezone offset
    """
    if candidate_offsets is None:
        candidate_offsets = list(range(-12, 13))  # -12 to +12 hours

    # Use H1 for the detection (faster than M5, more precise than D1)
    detect_tf = 'H1' if 'H1' in candles_dict else list(candles_dict.keys())[0]
    detect_candles = candles_dict[detect_tf]

    if detect_candles is None or len(detect_candles) == 0:
        log.info(f"    No {detect_tf} candles for offset detection — using offset 0")
        return 0

    # WHY (Phase 44 Fix 1): Old code did a random sample of 200 trades
    #      across the entire history. A London-only strategy got a
    #      London-biased sample → auto-detect picked the offset that
    #      maximized London alignment, which may be wrong for trades
    #      executed at other hours (e.g., overnight position
    #      management). Stratify by hour-of-day bin so every hour
    #      contributes proportionally.
    # WHY: 200 trades gave weak confidence (64.5% vs 53.5% = 22 trade diff).
    #      Using all trades gives definitive results.
    # CHANGED: April 2026 — use all trades for timezone detection
    sample_size = len(trades_df)
    if len(trades_df) > sample_size:
        try:
            _hours = pd.to_datetime(trades_df['open_time']).dt.hour
            _per_bin = max(1, sample_size // 24)
            _samples = []
            for h in range(24):
                _bin = trades_df[_hours == h]
                if len(_bin) == 0:
                    continue
                _take = min(_per_bin, len(_bin))
                _samples.append(_bin.sample(n=_take, random_state=42))
            sample = pd.concat(_samples) if _samples else trades_df.sample(n=sample_size, random_state=42)
            # If stratification under-fills, top up with random
            if len(sample) < sample_size:
                _extra = trades_df.drop(sample.index, errors='ignore')
                if len(_extra) > 0:
                    _need = sample_size - len(sample)
                    sample = pd.concat([sample, _extra.sample(n=min(_need, len(_extra)), random_state=42)])
        except Exception:
            sample = trades_df.sample(n=sample_size, random_state=42)
    else:
        sample = trades_df

    log.info(f"    Auto-detecting timezone offset (testing {len(candidate_offsets)} offsets on {len(sample)} trades)...")

    best_offset = 0
    best_verified = 0
    results = []

    # WHY: Old code used merge_asof(direction='backward') which assigns each
    #      trade to its CONTAINING candle, then checks if the trade price is
    #      in that candle's OHLC range. Since the trade happened INSIDE that
    #      candle, it's definitionally in the range — every offset scored
    #      ~100% and the "best" was just whichever won the tie. Detector was
    #      statistically broken.
    #
    #      Correct test: trade entry_price should match the OPEN of the
    #      candle that STARTS at or just after open_time. If we shift the
    #      trade time by `offset_hours` and the entry_price matches the
    #      open of the candle immediately following, the offset is correct.
    #      This is NOT something we can satisfy for every offset by accident
    #      — there's one right answer.
    # CHANGED: April 2026 — fix tautological offset detection (audit bug #14)
    candles_sorted = detect_candles.sort_values('timestamp').reset_index(drop=True)

    for offset_hours in candidate_offsets:
        shifted = sample.copy()
        shifted['open_time'] = pd.to_datetime(shifted['open_time']) + pd.Timedelta(hours=offset_hours)

        try:
            shifted_sorted = shifted.sort_values('open_time')

            # Use direction='forward' to find the candle that starts AT or
            # AFTER the shifted trade time — that's the candle whose OPEN
            # price is what the trader saw.
            merged = pd.merge_asof(
                shifted_sorted[['open_time', 'entry_price']],
                candles_sorted[['timestamp', 'open']] if 'open' in candles_sorted.columns
                    else candles_sorted[['timestamp', 'high', 'low']],
                left_on='open_time',
                right_on='timestamp',
                direction='forward',
                tolerance=pd.Timedelta(hours=2),
            )

            if 'open' in merged.columns:
                # Match if entry_price is within a few pips of the candle open
                match_tol = ALIGNMENT_TOLERANCE * PIP_SIZE * 5  # looser than range check
                in_range = (merged['entry_price'] - merged['open']).abs() <= match_tol
            else:
                # Fallback: candles_df has no 'open' column — fall back to the
                # looser check (entry_price in low..high of NEXT candle). Still
                # meaningful because we use direction='forward', so the candle
                # is the one AFTER the trade, not the containing one.
                # WHY (Phase 44 Fix 3): Old tolerance was
                #      ALIGNMENT_TOLERANCE * PIP_SIZE. With config
                #      defaults (ALIGNMENT_TOLERANCE=150,
                #      PIP_SIZE=0.01) this is 1.5 raw price units —
                #      correct for XAUUSD but absurd for EURUSD where
                #      it becomes 15000 real pips. Cap the tolerance
                #      at a sensible maximum derived from the actual
                #      candle range so it can't trivially succeed.
                # CHANGED: April 2026 — Phase 44 Fix 3 — bounded tolerance
                #          (audit Part D HIGH #27)
                tolerance = ALIGNMENT_TOLERANCE_RAW
                # Cap at half the median candle range so it can't pass everything
                try:
                    _median_range = float((merged['high'] - merged['low']).median())
                    if _median_range > 0:
                        tolerance = min(tolerance, _median_range * 0.5)
                except Exception:
                    pass
                in_range = (
                    (merged['entry_price'] >= merged['low'] - tolerance) &
                    (merged['entry_price'] <= merged['high'] + tolerance)
                )
            verified = int(in_range.sum())
            results.append((offset_hours, verified))

            # WHY (Phase 44 Fix 2): Old strict > comparison meant ties
            #      were broken by first-tested offset. range(-12, 13)
            #      starts at -12, so -12 silently won every tie.
            #      Prefer the offset closest to 0 (broker tz typically
            #      within a few hours of UTC) when verification counts
            #      are equal.
            # CHANGED: April 2026 — Phase 44 Fix 2 — tie-break by |offset|
            #          (audit Part D HIGH #26)
            if verified > best_verified or (
                verified == best_verified and abs(offset_hours) < abs(best_offset)
            ):
                best_verified = verified
                best_offset = offset_hours
        except Exception:
            results.append((offset_hours, 0))
            continue

    # Print top 5 offsets
    results.sort(key=lambda x: x[1], reverse=True)
    log.info(f"    Top offsets (verified count out of {len(sample)}):")
    for off, ver in results[:10]:
        marker = " <- BEST" if off == best_offset else ""
        pct = ver / len(sample) * 100
        log.info(f"      Offset {off:+d}h: {ver:3d} ({pct:5.1f}%){marker}")

    # WHY: If the best verification rate is still low, the detector didn't
    #      find a reliable match. Before the FIX 1C rewrite, every offset
    #      scored ~100% so there was no way to detect failure. Now we can
    #      actually tell when the detection is unreliable.
    # CHANGED: April 2026 — warn on low verification (audit bug #14)
    best_pct = best_verified / len(sample) * 100 if len(sample) > 0 else 0
    if best_pct < 50:
        log.warning(f"    best offset only verified {best_pct:.1f}% of trades.")
        log.warning(f"       The candles file and the trades file may use incompatible")
        log.warning(f"       time formats, have a DST mismatch, or the 'open' price in")
        log.warning(f"       the candles file may not match the broker execution price.")
        log.warning(f"       Consider manually setting the timezone offset.")
    elif best_pct < 80:
        log.warning(f"    Moderate confidence: best offset verified {best_pct:.1f}%.")

    log.info(f"    -> Using offset: {best_offset:+d} hours")
    return best_offset


def align_all_timeframes(trades_csv_path=None, output_dir=None):
    """
    Align trades against ALL timeframes at once.
    For each trade, find the corresponding candle at each timeframe.

    Output: single CSV with columns like:
      trade_id, open_time, close_time, action, pips, profit, lots,
      M5_candle_idx, M5_candle_time,
      M15_candle_idx, M15_candle_time,
      H1_candle_idx, H1_candle_time,
      H4_candle_idx, H4_candle_time,
      D1_candle_idx, D1_candle_time

    Args:
        trades_csv_path: Optional path to trades CSV (uses workspace system if None)
        output_dir: Optional output directory (uses default if None)

    Returns:
        DataFrame with aligned trades, or None if failed
    """
    log.info(f"\n{'=' * 70}")
    log.info(f"[STEP 1/2] ALIGNING TRADES TO CANDLES (Multi-Timeframe)")
    log.info(f"{'=' * 70}\n")

    try:
        # Get trades path
        if trades_csv_path is None:
            trades_csv_path = _get_trades_path()

        # CHANGED: June 2026 — show active-history name, not the internal filename
        #          (manager stores every history as trades_clean.csv, so the old
        #           log looked like the OLD file even when it was e.g. Gold Reaper).
        _disp_name = os.path.basename(trades_csv_path)
        try:
            from shared.trade_history_manager import get_active_history
            _active = get_active_history()
            if _active and _active.get('robot_name'):
                _disp_name = "%s (%s)" % (_active.get('robot_name'),
                                          os.path.basename(trades_csv_path))
        except Exception:
            pass
        log.info(f"  Loading trades from: {_disp_name}")

        # WHY (Phase 58 Fix 1): pd.read_csv with no dtype= lets pandas
        #      infer types. A single stray string or #N/A in the timestamp
        #      column promotes the whole column from datetime to object,
        #      which pd.to_datetime then converts to NaT silently.
        #      merge_asof on NaT timestamps fails in confusing ways — trades
        #      are silently dropped. Force timestamp columns to str so
        #      pd.to_datetime gets the raw text and can produce useful errors.
        # CHANGED: April 2026 — Phase 58 Fix 1 — explicit dtype for timestamp cols
        #          (audit Part D HIGH #29)
        trades_df = pd.read_csv(
            trades_csv_path,
            dtype={'Open Date': str, 'Close Date': str,
                   'open_time': str, 'close_time': str},
            low_memory=False,
        )

        # Normalize column names (handle different CSV formats)
        column_mapping = {
            'Open Date': 'open_time',
            'Close Date': 'close_time',
            'Open Price': 'entry_price',
            'Close Price': 'exit_price',
            'Action': 'action',
            'Lots': 'lots',
            'Pips': 'pips',
            'Profit': 'profit',
        }
        trades_df.rename(columns=column_mapping, inplace=True)

        # CHANGED: June 2026 — canonical trades are YYYY.MM.DD (year-first). Old
        #          dayfirst=True flipped 2025.03.12 -> Dec-3, mis-aligning trades.
        trades_df['open_time']  = _parse_trade_timestamps(trades_df['open_time'])
        trades_df['close_time'] = _parse_trade_timestamps(trades_df['close_time'])

        # Add trade_id if not present
        if 'trade_id' not in trades_df.columns:
            trades_df['trade_id'] = range(len(trades_df))

        log.info(f"  Loaded {len(trades_df)} trades\n")

        # ── AUTO-DETECT TIMEZONE OFFSET ───────────────────────────────────
        # WHY: Broker server timezone often differs from candle CSV timezone.
        #      Try every offset and pick the one with the best verification.
        # CHANGED: April 2026 — auto-detect timezone
        _candles_for_detection = {}
        for _tf in ['H1']:
            # WHY: MT5 exports uppercase (XAUUSD_M5.csv), old data uses
            #      lowercase (xauusd_M5.csv). Try both.
            # CHANGED: April 2026 — case-insensitive candle file lookup
            _candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.lower()}_{_tf}.csv')
            if not os.path.exists(_candle_file):
                _candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.upper()}_{_tf}.csv')
            if os.path.exists(_candle_file):
                try:
                    # WHY (Phase 44 Fix 5): Old code used pd.read_csv
                    #      with no dtype hints. Stray non-numeric tokens
                    #      promoted the timestamp column to object dtype
                    #      and pd.to_datetime then produced NaT silently
                    #      → merge_asof failed in confusing ways.
                    #      low_memory=False forces a single-pass parse
                    #      so dtypes are stable. Post-load NaT check
                    #      surfaces parse failures.
                    # CHANGED: April 2026 — Phase 44 Fix 5 — robust read
                    #          (audit Part D HIGH #29)
                    _cdf = pd.read_csv(_candle_file, low_memory=False)
                    if 'timestamp' not in _cdf.columns:
                        for _col in _cdf.columns:
                            if _col.lower() in ('time', 'date', 'datetime', 'open_time'):
                                _cdf = _cdf.rename(columns={_col: 'timestamp'})
                                break
                    _cdf['timestamp'] = _parse_candle_timestamps(_cdf['timestamp'])  # CHANGED: June 2026 — consistent date parse
                    # WHY: Phase 44 Fix 5 — surface NaT rows so users see parse failures
                    _nat_count = int(_cdf['timestamp'].isna().sum())
                    if _nat_count > 0:
                        log.warning(
                            f"    [STEP1] {_tf} candle file has {_nat_count} unparseable "
                            f"timestamp rows (now NaT). Check {_candle_file} for stray data."
                        )
                        _cdf = _cdf.dropna(subset=['timestamp'])
                    _candles_for_detection[_tf] = _cdf
                except Exception:
                    pass

        if _candles_for_detection and 'entry_price' in trades_df.columns:
            # WHY (June 2026): we used to ASSUME firm-tz DST was always better
            #      than the legacy offset. Real measurement on a 1106-trade log
            #      showed DST conversion did NOT improve verification for the
            #      TRADE LOG (separate from candle side, which IS DST-correct).
            #      So now: score BOTH candidates with the SAME _verify_count
            #      metric, log both rates, keep the winner. _detect_best_offset
            #      stays untouched — it just produces candidate A. If the firm
            #      has no broker_timezone, only A runs (no change vs before).
            # CHANGED: June 2026 — measure both clocks, keep the winner
            _detect_tf = ('M5' if 'M5' in _candles_for_detection
                          else next(iter(_candles_for_detection)))
            _cand = _candles_for_detection[_detect_tf].sort_values('timestamp').reset_index(drop=True)
            _tol = ALIGNMENT_TOLERANCE * PIP_SIZE * 5
            _n = len(trades_df)

            # Candidate A: legacy fixed-offset auto-detect (UNCHANGED algorithm)
            _best_off = _detect_best_offset(trades_df, _candles_for_detection)
            _ta = trades_df.copy()
            _ta['open_time'] = pd.to_datetime(_ta['open_time']) + pd.Timedelta(hours=_best_off)
            _va = _verify_count(_ta, _cand, _tol)

            # Candidate B: firm-tz DST-aware (only if the firm specifies a zone)
            _vb = -1
            _tz = None
            try:
                import config_loader as _cl2
                from shared.tz_offset import resolve_broker_tz
                from shared.prop_firm_engine import load_all_firms
                _c2 = _cl2.load()
                _fid = _c2.get('prop_firm_id', '')
                _firms = load_all_firms()
                _fd = _firms[_fid].config if _fid and _fid in _firms else None
                if _fd and _fd.get('broker_timezone'):
                    _tz = resolve_broker_tz(firm_data=_fd)
                    _tb = trades_df.copy()
                    _ot = pd.to_datetime(_tb['open_time'])
                    _tb['open_time'] = (_ot.dt.tz_localize(_tz, ambiguous='NaT',
                                                          nonexistent='NaT')
                                           .dt.tz_convert('UTC').dt.tz_localize(None))
                    _tb = _tb.dropna(subset=['open_time'])
                    _vb = _verify_count(_tb, _cand, _tol)
            except Exception as _e:
                log.info("    [TZ] firm-tz scoring skipped: " + str(_e))
                _tz = None

            log.info("    [TZ] Verification — auto-detect offset %+dh: %d/%d (%.1f%%)"
                     % (_best_off, _va, _n, 100.0 * _va / max(_n, 1)))
            if _tz is not None:
                log.info("    [TZ] Verification — firm tz '%s': %d/%d (%.1f%%)"
                         % (_tz, _vb, _n, 100.0 * _vb / max(_n, 1)))

            # WHY: the firm config stores the broker timezone (e.g. Europe/Athens =
            #      EET/EEST, DST-correct) which is the AUTHORITATIVE clock for that
            #      firm's data. Per user spec: when a firm timezone IS stored, USE IT
            #      as the default — do NOT let the flat auto-detect override it — but
            #      still SHOW both verification scores so a mismatch is visible, and
            #      WARN loudly if the firm tz verifies poorly (the export may not be
            #      in broker time). If NO firm tz is stored, fall back to auto-detect
            #      exactly as before.
            # CHANGED: June 2026 — firm tz is the default clock (was: only if it
            #          out-scored auto-detect). Diagnostic shows both + warning.
            if _tz is not None:
                _fr = 100.0 * _vb / max(_n, 1)
                _ar = 100.0 * _va / max(_n, 1)
                log.info("    [TZ] CHOSEN: firm tz '%s' (firm config is authoritative "
                         "for this firm's data)." % _tz)
                if _vb < _va:
                    log.info("    [TZ] NOTE: auto-detect offset %+dh scored higher "
                             "(%.1f%% vs firm %.1f%%). Using firm tz per config; if "
                             "alignment looks wrong, the trade export may not be in "
                             "broker time — consider checking the export's timezone."
                             % (_best_off, _ar, _fr))
                if _fr < 50.0:
                    log.info("    [TZ] ⚠ WARNING: firm tz '%s' only verified %.1f%% of "
                             "trades. Alignment may be unreliable — discovery will read "
                             "indicators from possibly-wrong candles. Verify the trade "
                             "file's timezone matches the firm's broker time." % (_tz, _fr))
                _chosen_mode = 'firm_tz'
                _chosen_tz = str(_tz)
                _ot = pd.to_datetime(trades_df['open_time'])
                _ct = pd.to_datetime(trades_df['close_time'])
                trades_df['open_time']  = (_ot.dt.tz_localize(_tz, ambiguous='NaT', nonexistent='NaT')
                                              .dt.tz_convert('UTC').dt.tz_localize(None))
                trades_df['close_time'] = (_ct.dt.tz_localize(_tz, ambiguous='NaT', nonexistent='NaT')
                                              .dt.tz_convert('UTC').dt.tz_localize(None))
                trades_df = trades_df.dropna(subset=['open_time']).reset_index(drop=True)
            else:
                log.info("    [TZ] CHOSEN: auto-detect offset %+dh (no firm timezone "
                         "stored — automatic detection as before)." % _best_off)
                _chosen_mode = 'auto_detect'
                _chosen_tz = ("offset%+dh" % _best_off)
                if _best_off != 0:
                    trades_df['open_time']  = pd.to_datetime(trades_df['open_time'])  + pd.Timedelta(hours=_best_off)
                    trades_df['close_time'] = pd.to_datetime(trades_df['close_time']) + pd.Timedelta(hours=_best_off)

            # WHY: Globals on the module are read by the run-history recorder
            #      hook so the chosen mode + both verification rates are
            #      surfaced in the Run History panel. Marker file kept for
            #      compatibility with the prior recorder hook.
            try:
                globals()['_LAST_TZ_MODE']           = _chosen_mode
                globals()['_LAST_TZ_VALUE']          = _chosen_tz
                globals()['_LAST_TZ_VERIFY_OFFSET']  = int(_va)
                globals()['_LAST_TZ_VERIFY_DST']     = (int(_vb) if _tz is not None else None)
                globals()['_LAST_TZ_VERIFY_TOTAL']   = int(_n)
                _meta = {
                    'tz_mode': _chosen_mode,
                    'tz_value': _chosen_tz,
                    'tz_verify_offset':  int(_va),
                    'tz_verify_dst':     (int(_vb) if _tz is not None else None),
                    'tz_verify_total':   int(_n),
                }
                _meta_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
                os.makedirs(_meta_dir, exist_ok=True)
                import json as _json
                with open(os.path.join(_meta_dir, '_last_tz_mode.json'),
                          'w', encoding='utf-8') as _mf:
                    _json.dump(_meta, _mf)
            except Exception:
                pass
        else:
            log.info(f"    Could not load candles for offset detection — proceeding without offset")

        # Create output directory
        if output_dir is None:
            output_dir = OUTPUT_FOLDER
        os.makedirs(output_dir, exist_ok=True)

        # Process each timeframe
        aligned_counts = {}
        verified_counts = {}

        for tf in ALIGN_TIMEFRAMES:
            # WHY: Cannot use logging here — end=" " creates inline progress
            #      (e.g., "Aligning to M5... done") which logging always breaks
            #      with newlines. Keep print() for UX.
            # PRESERVED: April 2026 — Phase 19d Fix 3
            print(f"  Aligning to {tf}...", end=" ", flush=True)

            # Load candle data
            # WHY: MT5 exports uppercase (XAUUSD_M5.csv), old data uses
            #      lowercase (xauusd_M5.csv). Try both.
            # CHANGED: April 2026 — case-insensitive candle file lookup
            candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.lower()}_{tf}.csv')
            if not os.path.exists(candle_file):
                candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.upper()}_{tf}.csv')

            if not os.path.exists(candle_file):
                log.info(f"SKIPPED (file not found)")
                continue

            # Phase 44 Fix 5: low_memory=False for stable dtype inference
            candles_df = pd.read_csv(candle_file, low_memory=False)
            candles_df['timestamp'] = _parse_candle_timestamps(candles_df['timestamp'])  # CHANGED: June 2026 — consistent date parse
            # Phase 44 Fix 5 cont.: validate timestamps
            _nat_count = int(candles_df['timestamp'].isna().sum())
            if _nat_count > 0:
                log.warning(
                    f"    [STEP1] {tf} candle file has {_nat_count} unparseable "
                    f"timestamp rows (now NaT). Check {candle_file} for stray data."
                )
                candles_df = candles_df.dropna(subset=['timestamp'])

            # Sort candles by timestamp and reset index
            candles_df = candles_df.sort_values('timestamp').reset_index(drop=True)

            # Use merge_asof to find the candle CONTAINING each trade's open_time,
            # then shift by -1 to get the PREVIOUS (closed) candle.
            # WHY: direction='backward' finds the candle whose timestamp ≤ open_time,
            #      which is the CURRENTLY OPEN candle containing the trade. Its OHLC
            #      includes data from AFTER the trade was placed — a look-ahead leak.
            #      The trader only had data through the end of the PREVIOUS candle,
            #      so that's the candle we must read features from.
            # CHANGED: April 2026 — fix containing-candle look-ahead (audit bug #10)
            aligned = pd.merge_asof(
                trades_df[['trade_id', 'open_time', 'entry_price']].sort_values('open_time'),
                candles_df.reset_index().rename(columns={'index': 'candle_idx'})[['timestamp', 'candle_idx', 'high', 'low']],
                left_on='open_time',
                right_on='timestamp',
                direction='backward',
                # WHY (Phase 44 Fix 4): Old tolerance was 7 days. A trade
                #      6 days past the last candle aligned to a 6-day-old
                #      candle silently — verification then ran on that
                #      ancient candle's H/L producing nonsense, but the
                #      trade was marked aligned. Tighten to 24h. Trades
                #      beyond that range are dropped (the next dropna
                #      step removes them) — they were producing garbage
                #      anyway.
                # CHANGED: April 2026 — Phase 44 Fix 4 — tighten to 24h
                #          (audit Part D HIGH #28)
                tolerance=pd.Timedelta(hours=24)
            )

            # Shift candle_idx to point at the PREVIOUS (closed) candle.
            # high/low/timestamp stay pointing at the containing candle — they
            # are used only for verification (checking entry_price is in the
            # range of the candle the trader saw the PRICE of, which is the
            # containing one). Features are read from (candle_idx - 1) downstream.
            aligned['containing_candle_idx']  = aligned['candle_idx']
            aligned['containing_candle_time'] = aligned['timestamp']
            aligned['candle_idx'] = aligned['candle_idx'] - 1

            # Drop trades that would map to candle_idx < 0 (trade occurred
            # before any closed candle existed — not enough warmup).
            pre_count = len(aligned)
            aligned = aligned[aligned['candle_idx'] >= 0]
            dropped = pre_count - len(aligned)
            if dropped > 0:
                log.info(f"    Dropped {dropped} trades with no prior closed candle")

            # Add columns to main DataFrame
            # WHY: merge_asof can produce duplicate trade_id rows if two candles
            #      have identical timestamps. drop_duplicates keeps the first
            #      match (closest candle) and prevents index ambiguity.
            # CHANGED: April 2026 — drop_duplicates before set_index
            aligned_dedup = aligned.drop_duplicates(subset=['trade_id'])
            trades_df[f'{tf}_candle_idx']  = aligned_dedup.set_index('trade_id')['candle_idx']
            # WHY: Store the CONTAINING candle's timestamp for display. The
            #      previously-closed candle's time is (containing_time - tf)
            #      but downstream code displays trade time vs "current candle"
            #      in the UI, so the containing time is what the user expects.
            # CHANGED: April 2026 — store containing time for display
            trades_df[f'{tf}_candle_time'] = aligned_dedup.set_index('trade_id')['containing_candle_time']

            # Count aligned trades
            aligned_count = trades_df[f'{tf}_candle_idx'].notna().sum()
            aligned_counts[tf] = aligned_count

            # Verify alignment: check if entry_price falls within the CONTAINING
            # candle's high-low range. (Not the previously-closed candle that we
            # use for features — the trader's actual entry price happens INSIDE
            # the containing candle, so verification must use the containing
            # candle's OHLC.)
            # WHY: Old code verified against whatever candle merge_asof returned.
            #      After FIX 1A, candle_idx points at the previous candle, so
            #      the verification now uses containing_candle_idx — the bar
            #      where the trade actually happened.
            # CHANGED: April 2026 — verify against containing bar (audit bug #10)
            # Phase 63 Fix 2: use pre-computed constant
            tolerance = ALIGNMENT_TOLERANCE_RAW

            # WHY (Phase 63 Fix 1): Old code used iterrows() — one row per trade.
            #      For 10 000 trades this was the slowest remaining loop in step1.
            #      Vectorise with boolean masking: same logic, ~100× faster.
            # CHANGED: April 2026 — Phase 63 Fix 1 — vectorised verification
            #          (audit Part D LOW #31)
            if aligned_count > 0:
                _has_candle  = aligned['candle_idx'].notna()
                _in_range    = (
                    (aligned['entry_price'] >= aligned['low']  - tolerance) &
                    (aligned['entry_price'] <= aligned['high'] + tolerance)
                )
                verified = int((_has_candle & _in_range).sum())
            else:
                verified = 0

            verified_counts[tf] = verified

            # CHANGED: June 2026 — stash one fine-grained TF's aligned rows so the
            #          in-app diagnostic (after the summary) can explain WHY misses
            #          miss (sub-hour time skew vs price-feed mismatch).
            if tf in ('M5', 'M15') and '_diag_aligned' not in dir():
                _diag_aligned = aligned.copy()
                _diag_tf = tf

            log.info(f"{aligned_count} trades aligned ({verified} verified)")

        # Print summary
        log.info(f"\n  Alignment Summary:")
        for tf in ALIGN_TIMEFRAMES:
            if tf in aligned_counts:
                align_pct = (aligned_counts[tf] / len(trades_df) * 100) if len(trades_df) > 0 else 0
                verify_pct = (verified_counts[tf] / aligned_counts[tf] * 100) if aligned_counts[tf] > 0 else 0
                log.info(f"    {tf:4s}: {aligned_counts[tf]:4d}/{len(trades_df)} aligned ({align_pct:5.1f}%), "
                      f"{verified_counts[tf]:4d} verified ({verify_pct:5.1f}%)")

                # Warn if verification is low
                if verify_pct < 80:
                    log.warning(f"         Low verification rate - possible timezone mismatch!")

        # CHANGED: June 2026 — in-app alignment DIAGNOSTIC (prints to the same
        #          Console Output as the rest of Step 1). Explains WHY trades fail
        #          verification: sub-hour time skew (price lands in an adjacent
        #          candle) vs price-feed mismatch (price in NO nearby candle).
        try:
            if '_diag_aligned' in dir() and _diag_aligned is not None and len(_diag_aligned):
                _d = _diag_aligned
                _d = _d[_d['candle_idx'].notna()] if 'candle_idx' in _d.columns else _d
                _inb = ((_d['entry_price'] >= _d['low'] - ALIGNMENT_TOLERANCE_RAW) &
                        (_d['entry_price'] <= _d['high'] + ALIGNMENT_TOLERANCE_RAW))
                _miss = _d[~_inb]
                _n = len(_d); _nm = len(_miss)
                log.info("\n  ── Alignment Diagnostic (%s) ──" % _diag_tf)
                log.info("    %d/%d trades have entry price inside their candle range; %d miss."
                         % (_n - _nm, _n, _nm))
                if _nm:
                    # how far is each miss's price from its candle range? (in pips)
                    _below = (_d['low'] - _d['entry_price']).clip(lower=0)
                    _above = (_d['entry_price'] - _d['high']).clip(lower=0)
                    _dist_price = (_below + _above)[~_inb]
                    _dist_pips = (_dist_price / PIP_SIZE)
                    _med = float(_dist_pips.median())
                    _p90 = float(_dist_pips.quantile(0.90))
                    log.info("    Miss distance from candle range: median %.0f pips, "
                             "90th pct %.0f pips (tolerance %.0f pips)."
                             % (_med, _p90, ALIGNMENT_TOLERANCE))
                    # small distance => likely adjacent-bar / sub-hour skew;
                    # large => price-feed mismatch (different vendor than broker fills)
                    if _med <= 50:
                        log.info("    → Misses are CLOSE to the range — likely a sub-hour / "
                                 "bar-stamp skew or normal bid/ask spread, not a wrong clock.")
                    else:
                        log.info("    → Misses are FAR from the range — the candle price feed "
                                 "likely differs from the broker's fill prices (different data "
                                 "vendor). Timezone changes will NOT fix this.")
                    log.info("    (This is informational — discovery still runs; it tells you "
                             "how trustworthy the candle↔trade alignment is.)")
        except Exception as _diag_e:
            log.info("  [diag] alignment diagnostic skipped: %s" % _diag_e)

        # Save aligned trades
        output_file = os.path.join(output_dir, 'aligned_trades.csv')
        trades_df.to_csv(output_file, index=False)
        log.info(f"\n  Saved: {output_file}")

        log.info(f"\n[STEP 1/2] COMPLETE\n")

        return trades_df

    except Exception as e:
        log.error(f"\n in step1: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Main entry point for command-line usage."""
    import argparse
    parser = argparse.ArgumentParser(description='Align trade data with price candles (multi-timeframe)')
    parser.add_argument('--trades', type=str, help='Path to trades CSV (optional, uses workspace if omitted)')
    parser.add_argument('--output', type=str, help='Output directory (optional)')

    args = parser.parse_args()

    result = align_all_timeframes(trades_csv_path=args.trades, output_dir=args.output)

    if result is None:
        sys.exit(1)


if __name__ == '__main__':
    main()

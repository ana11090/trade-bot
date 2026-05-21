"""
Auto-merge fresh MT5 exports into the trade-bot's data source folder.

Detects when export_candles.mq5 has run by checking for a sentinel
file at %APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\trade_bot_export_<SYMBOL>_DONE.txt.

If the sentinel is fresh (<24 hours old) and not yet processed, merges
the exported CSVs into the target data_dir, dropping duplicates by
timestamp. Marks the sentinel as processed by appending a line.

Safe to call before every backtest — does nothing if no fresh export
exists.
"""

import os
import sys
import pandas as pd
from datetime import datetime, timedelta
import logging

log = logging.getLogger(__name__)


def get_common_files_path():
    """Return the MT5 Common\\Files path on Windows."""
    appdata = os.environ.get('APPDATA')
    if not appdata:
        return None
    p = os.path.join(appdata, 'MetaQuotes', 'Terminal', 'Common', 'Files')
    return p if os.path.isdir(p) else None


def find_fresh_export(symbol='XAUUSD', max_age_hours=24):
    """Check for a fresh sentinel file. Returns (sentinel_path, metadata) or (None, None)."""
    common = get_common_files_path()
    if common is None:
        return None, None

    sentinel = os.path.join(common, f'trade_bot_export_{symbol}_DONE.txt')
    if not os.path.exists(sentinel):
        return None, None

    # Check freshness
    age_sec = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(sentinel))).total_seconds()
    if age_sec > max_age_hours * 3600:
        return None, None

    # Read metadata
    meta = {}
    try:
        with open(sentinel, 'r', encoding='utf-8') as f:
            for line in f:
                if '=' in line:
                    k, v = line.strip().split('=', 1)
                    meta[k] = v
    except Exception as e:
        log.warning(f"[MT5-MERGE] Could not read sentinel {sentinel}: {e}")
        return None, None

    # Check if already processed
    if meta.get('processed_at'):
        return None, None  # already merged

    return sentinel, meta


def merge_fresh_export(data_dir, symbol='XAUUSD'):
    """Merge fresh MT5 export into data_dir. Returns (merged_count, total_added_rows).

    Called at the start of every backtest. No-op if no fresh export.
    """
    sentinel, meta = find_fresh_export(symbol)
    if sentinel is None:
        return 0, 0  # nothing to do

    common = get_common_files_path()
    tfs = [t for t in meta.get('timeframes', '').split(',') if t]
    if not tfs:
        log.warning(f"[MT5-MERGE] Sentinel has no timeframes list — skipping.")
        return 0, 0

    log.info(f"[MT5-MERGE] Found fresh MT5 export from {meta.get('export_time')} ({meta.get('broker')})")
    log.info(f"[MT5-MERGE] Merging into {data_dir}")

    os.makedirs(data_dir, exist_ok=True)
    merged_count = 0
    total_added = 0

    for tf in tfs:
        src = os.path.join(common, f'trade_bot_export_{symbol}_{tf}.csv')
        if not os.path.exists(src):
            log.warning(f"[MT5-MERGE]   {tf}: export file missing — skip")
            continue

        # Find the target CSV in data_dir (handle naming variations)
        target_candidates = [
            os.path.join(data_dir, f'{symbol}_{tf}.csv'),
            os.path.join(data_dir, f'{symbol.lower()}_{tf}.csv'),
            os.path.join(data_dir, f'{tf}.csv'),
        ]
        target = next((p for p in target_candidates if os.path.exists(p)), target_candidates[0])

        try:
            fresh = pd.read_csv(src)
            fresh['timestamp'] = pd.to_datetime(fresh['timestamp'])
        except Exception as e:
            log.warning(f"[MT5-MERGE]   {tf}: cannot read fresh export — {e}")
            continue

        if os.path.exists(target):
            try:
                existing = pd.read_csv(target)
                # Refuse to touch LFS stubs
                if len(existing) < 5:
                    with open(target, 'r') as f:
                        first = f.readline()
                    if 'git-lfs' in first:
                        log.warning(f"[MT5-MERGE]   {tf}: {target} is an LFS stub. Run `git lfs pull` first. Skipping.")
                        continue
                existing['timestamp'] = pd.to_datetime(existing['timestamp'])
                before = len(existing)
                combined = pd.concat([existing, fresh], ignore_index=True)
                combined = combined.drop_duplicates(subset=['timestamp'], keep='last')
                combined = combined.sort_values('timestamp').reset_index(drop=True)
                added = len(combined) - before
                log.info(f"[MT5-MERGE]   {tf}: existing={before:,} fresh={len(fresh):,} → merged={len(combined):,} ({added:+,} new)")
                total_added += added
            except Exception as e:
                log.warning(f"[MT5-MERGE]   {tf}: merge failed — {e}. Skipping.")
                continue
        else:
            combined = fresh.sort_values('timestamp').reset_index(drop=True)
            log.info(f"[MT5-MERGE]   {tf}: created new {target} with {len(combined):,} bars")
            total_added += len(combined)

        # Write back (atomic via rename)
        tmp = target + '.tmp'
        combined.to_csv(tmp, index=False)
        os.replace(tmp, target)

        # Invalidate indicator caches in this folder for this TF
        for cache_pattern in (f'.cache_{tf}_indicators.parquet', f'.cache_{tf}_partial_'):
            for fn in os.listdir(data_dir):
                if fn.startswith(cache_pattern):
                    try:
                        os.remove(os.path.join(data_dir, fn))
                    except Exception:
                        pass

        merged_count += 1

    # Mark sentinel as processed
    try:
        with open(sentinel, 'a', encoding='utf-8') as f:
            f.write(f"processed_at={datetime.now().isoformat()}\n")
            f.write(f"processed_into={data_dir}\n")
    except Exception:
        pass

    log.info(f"[MT5-MERGE] Done: {merged_count} timeframes updated, {total_added:+,} total new rows.")
    return merged_count, total_added

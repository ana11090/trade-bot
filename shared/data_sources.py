"""
Data source management — discovers and manages historical data folders.

WHY: Different brokers have different candle data (timezone, volume, OHLC).
     Rules must be trained and tested on the SAME data source for consistency.
CHANGED: April 2026 — data source selector
"""
import os
import json
import shutil
from datetime import datetime

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
_SOURCES_DIR = os.path.join(_DATA_DIR, 'sources')

def get_sources_dir():
    """Return path to data/sources/."""
    os.makedirs(_SOURCES_DIR, exist_ok=True)
    return _SOURCES_DIR

def list_sources():
    """Return list of available data sources as dicts.

    Returns:
        [{'id': 'original', 'name': 'Original Data', 'path': '...',
          'symbol': 'XAUUSD', 'timeframes': ['M5','H1',...], 'candle_count': 1523831,
          'date_range': '2003-2026'}, ...]
    """
    sources_dir = get_sources_dir()
    result = []

    # Also check the legacy data/ folder (flat files)
    _migrate_legacy_if_needed()

    for name in sorted(os.listdir(sources_dir)):
        src_path = os.path.join(sources_dir, name)
        if not os.path.isdir(src_path):
            continue

        # Find candle files
        timeframes = []
        candle_count = 0
        symbol = ''
        date_range = ''

        # WHY: Tick data files follow naming XAUUSD_ticks_YYYY_MM.csv.
        #      Detect them before the timeframe loop so they're not
        #      mistaken for a timeframe called "TICKS".
        # CHANGED: April 2026 — tick data detection
        has_ticks = False
        tick_files = []
        for _tf in sorted(os.listdir(src_path)):
            if _tf.endswith('.csv') and '_ticks' in _tf.lower():
                has_ticks = True
                tick_files.append(_tf)

        for f in sorted(os.listdir(src_path)):
            if f.endswith('.csv') and '_ticks' not in f.lower():  # skip tick files
                # Parse filename: XAUUSD_M5.csv or xauusd_M5.csv
                parts = f.replace('.csv', '').split('_')
                if len(parts) >= 2:
                    symbol = parts[0].upper()
                    tf = parts[1].upper()
                    timeframes.append(tf)

                    # Count lines in first file for info
                    if not candle_count:
                        try:
                            with open(os.path.join(src_path, f)) as fh:
                                candle_count = sum(1 for _ in fh) - 1  # minus header
                        except Exception:
                            pass

                    # Get date range from first and last line
                    if not date_range:
                        try:
                            with open(os.path.join(src_path, f)) as fh:
                                lines = fh.readlines()
                                if len(lines) > 2:
                                    first = lines[1].split(',')[0][:10]
                                    last = lines[-1].split(',')[0][:10]
                                    date_range = f"{first} to {last}"
                        except Exception:
                            pass

        if timeframes:
            # Read metadata if exists
            meta_path = os.path.join(src_path, '_source_info.json')
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                except Exception:
                    pass

            result.append({
                'id': name,
                'name': meta.get('display_name', name.replace('_', ' ').title()),
                'path': src_path,
                'symbol': symbol,
                'timeframes': sorted(timeframes),
                'candle_count': candle_count,
                'date_range': date_range,
                'broker': meta.get('broker', ''),
                'timezone_offset': meta.get('timezone_offset', ''),
                'imported_at': meta.get('imported_at', ''),
                # WHY: Expose tick availability so UI and backtester can
                #      show status and enable tick-aware exit simulation.
                # CHANGED: April 2026 — tick data detection
                'has_ticks': has_ticks,
                'tick_files': tick_files,
            })

    return result


def get_source_path(source_id):
    """Get the folder path for a data source."""
    if not source_id:
        source_id = 'original'
    path = os.path.join(get_sources_dir(), source_id)
    if os.path.isdir(path):
        return path
    # Fallback to legacy data/ folder
    return _DATA_DIR


def resolve_data_dir(rule=None):
    """Resolve the candle data directory.

    Priority: rule → P1 config → default data/ folder.

    WHY: Single source of truth for data path resolution.
         Every panel calls this instead of hardcoding paths.
    CHANGED: April 2026 — centralized data dir resolution
    """
    # Priority 1: rule's embedded data source
    if rule:
        ds_path = rule.get('data_source_path', '')
        ds_id = rule.get('data_source_id', '')
        if ds_path and os.path.isdir(ds_path):
            return ds_path
        if ds_id:
            resolved = get_source_path(ds_id)
            if os.path.isdir(resolved):
                return resolved

    # Priority 2: P1 config
    try:
        import importlib.util
        cl_path = os.path.join(os.path.dirname(_DATA_DIR),
                               'project1_reverse_engineering', 'config_loader.py')
        spec = importlib.util.spec_from_file_location('_cl_rdd', cl_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cfg = mod.load()
        ds_path = cfg.get('data_source_path', '')
        if ds_path and os.path.isdir(ds_path):
            return ds_path
        ds_id = cfg.get('data_source_id', '')
        if ds_id:
            resolved = get_source_path(ds_id)
            if os.path.isdir(resolved):
                return resolved
    except Exception:
        pass

    # Priority 3: default
    return _DATA_DIR


def import_data_source(source_folder, source_id, display_name='', broker='', timezone_offset=''):
    """Import candle CSVs from a folder into data/sources/{source_id}/.

    Args:
        source_folder: path containing CSV files to import
        source_id: short name (e.g. 'get_leveraged')
        display_name: human-readable name
        broker: broker name for metadata
        timezone_offset: e.g. 'GMT+3'

    Returns:
        dict with import results
    """
    dest = os.path.join(get_sources_dir(), source_id)
    os.makedirs(dest, exist_ok=True)

    copied = 0
    for f in os.listdir(source_folder):
        if f.endswith('.csv'):
            shutil.copy2(os.path.join(source_folder, f), os.path.join(dest, f))
            copied += 1

    # Save metadata
    meta = {
        'display_name': display_name or source_id.replace('_', ' ').title(),
        'broker': broker,
        'timezone_offset': timezone_offset,
        'imported_at': datetime.now().isoformat(),
        'source_folder': source_folder,
    }
    with open(os.path.join(dest, '_source_info.json'), 'w') as f:
        json.dump(meta, f, indent=2)

    return {'source_id': source_id, 'path': dest, 'files_copied': copied}


def import_tick_data(tick_folder, source_id):
    """Import tick CSV files into an existing data source.

    WHY: Tick files are imported separately from candle data because
         they're much larger and optional. Goes into the same source
         folder so the backtester finds them via data_source_id.
    CHANGED: April 2026 — tick data import

    Args:
        tick_folder: path containing XAUUSD_ticks_*.csv files
        source_id: existing data source ID to add ticks to

    Returns:
        dict with import results
    """
    dest = os.path.join(get_sources_dir(), source_id)
    if not os.path.isdir(dest):
        return {'error': f'Data source {source_id} not found at {dest}'}

    copied = 0
    for f in os.listdir(tick_folder):
        if f.endswith('.csv') and '_ticks' in f.lower():
            shutil.copy2(os.path.join(tick_folder, f), os.path.join(dest, f))
            copied += 1

    # Update metadata
    meta_path = os.path.join(dest, '_source_info.json')
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as fh:
                meta = json.load(fh)
        except Exception:
            pass
    meta['has_ticks'] = True
    meta['tick_files_imported'] = copied
    meta['ticks_imported_at'] = datetime.now().isoformat()
    with open(meta_path, 'w') as fh:
        json.dump(meta, fh, indent=2)

    return {'source_id': source_id, 'tick_files_copied': copied}


def _migrate_legacy_if_needed():
    """Move flat CSV files from data/ to data/sources/original/ (one-time)."""
    original_dir = os.path.join(get_sources_dir(), 'original')
    if os.path.exists(original_dir):
        return  # already migrated

    # Check if there are CSV files directly in data/
    csv_files = [f for f in os.listdir(_DATA_DIR) if f.endswith('.csv') and 'xauusd' in f.lower()]
    if not csv_files:
        return

    os.makedirs(original_dir, exist_ok=True)
    for f in csv_files:
        src = os.path.join(_DATA_DIR, f)
        dst = os.path.join(original_dir, f)
        # Copy, don't move — keep originals as backup
        shutil.copy2(src, dst)

    # Save metadata
    meta = {
        'display_name': 'Original Data',
        'broker': 'Unknown (original CSV files)',
        'timezone_offset': '',
        'imported_at': datetime.now().isoformat(),
    }
    with open(os.path.join(original_dir, '_source_info.json'), 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"[DATA] Migrated {len(csv_files)} CSV files to data/sources/original/")

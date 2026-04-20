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

        for f in sorted(os.listdir(src_path)):
            if f.endswith('.csv'):
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

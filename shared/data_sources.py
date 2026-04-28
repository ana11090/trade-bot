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

# WHY: Historical accident produced three competing names for the
#      same data source. Centralise the canonical name here so
#      get_source_path(), migrate_source_names(), and config_loader
#      all reference the same constant.
# CHANGED: April 2026 — source-name unification
_CANONICAL_SOURCE_ID = 'unlimited_leveraged_data'
_LEGACY_SOURCE_NAMES = (
    'xauusd_unlimited_levereged',   # typo that existed on disk
    'xauusd_unlimited_leveraged',   # if user fixed the typo halfway
    'original',                     # P1 default placeholder
)
# WHY: Throttle "source not found" warnings to once per key to avoid
#      spamming the console during long backtest sessions.
# CHANGED: April 2026 — per-key warning suppression
_WARNED_KEYS: set = set()

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
    """Get the folder path for a data source.

    WHY: The old silent fallback to _DATA_DIR masked source-name
         mismatches (typo'd folders, missing IDs, drive moves). Now
         we still fall back so the app keeps working, but we warn
         loudly so the user sees what's happening.
    CHANGED: April 2026 — log on silent fallback
    """
    if not source_id:
        source_id = _CANONICAL_SOURCE_ID
    path = os.path.join(get_sources_dir(), source_id)
    if os.path.isdir(path):
        return path
    # Source ID set but folder missing — log once, still fall back so
    # the app doesn't crash.
    _key = ('get_source_path_fallback', source_id)
    if _key not in _WARNED_KEYS:
        _WARNED_KEYS.add(_key)
        print(f"[data_sources] WARNING: source '{source_id}' not found at "
              f"{path}. Falling back to {_DATA_DIR}. "
              f"Run shared.data_sources.migrate_source_names() to fix.")
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


# WHY: MT5's Symbols-window export (Ctrl+U → Bars/Ticks → Export) writes
#      ONE tab-separated file with header
#      "<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>".
#      Bid/Ask cells are individually empty when only one side updated;
#      forward-fill is required so every emitted row has both. We split
#      into monthly files so the lazy tick loader can locate ticks for
#      a given candle's timestamp without scanning the full history.
# CHANGED: April 2026 — single-file MT5 Symbols-window export support
def _split_mt5_symbols_export_to_monthlies(src_csv, dest_dir, symbol):
    """Split a single MT5 Symbols-window tick CSV into monthly files.

    Args:
        src_csv:   path to the source tab-separated CSV.
        dest_dir:  data source folder where monthly files will be written.
        symbol:    e.g. "XAUUSD" — used in the output filename.

    Returns:
        dict with keys: 'months_written', 'rows_in', 'rows_out',
                        'rows_dropped_no_quote'.
    """
    import csv as _csv
    from datetime import datetime as _dt

    rows_in   = 0
    rows_out  = 0
    rows_drop = 0

    last_bid = None
    last_ask = None

    buffers = {}  # (yyyy, mm) -> list of (timestamp_ms, bid, ask)

    with open(src_csv, 'r', encoding='utf-8', errors='replace', newline='') as fh:
        first_line = fh.readline()
        if '\t' in first_line:
            sep = '\t'
        elif ',' in first_line:
            sep = ','
        else:
            return {'error': f'Could not detect separator in {src_csv}'}
        fh.seek(0)

        reader = _csv.reader(fh, delimiter=sep)
        header = next(reader, None)
        if header is None:
            return {'error': f'Empty file: {src_csv}'}

        norm = [h.strip().strip('<>').lower() for h in header]
        try:
            i_date = norm.index('date')
            i_time = norm.index('time')
            i_bid  = norm.index('bid')
            i_ask  = norm.index('ask')
        except ValueError:
            return {'error': f'Unexpected header in {src_csv}: {header}'}

        for row in reader:
            rows_in += 1
            if len(row) <= max(i_date, i_time, i_bid, i_ask):
                continue
            ts_str = row[i_date].strip() + ' ' + row[i_time].strip()
            bid_s  = row[i_bid].strip()
            ask_s  = row[i_ask].strip()

            # Forward-fill missing bid / ask.
            if bid_s != '':
                try: last_bid = float(bid_s)
                except ValueError: pass
            if ask_s != '':
                try: last_ask = float(ask_s)
                except ValueError: pass

            if last_bid is None or last_ask is None:
                rows_drop += 1
                continue

            # Parse timestamp. MT5 emits "YYYY.MM.DD HH:MM:SS.fff" or
            # "YYYY.MM.DD HH:MM:SS" depending on broker.
            try:
                time_part = ts_str.split(' ', 1)[1] if ' ' in ts_str else ''
                if '.' in time_part:
                    dt = _dt.strptime(ts_str, '%Y.%m.%d %H:%M:%S.%f')
                else:
                    dt = _dt.strptime(ts_str, '%Y.%m.%d %H:%M:%S')
            except Exception:
                continue

            ts_ms = int(dt.timestamp() * 1000)
            key   = (dt.year, dt.month)
            if key not in buffers:
                buffers[key] = []
            buffers[key].append((ts_ms, last_bid, last_ask))
            rows_out += 1

    # Write one file per (year, month). Skip months that already exist
    # so a re-import doesn't overwrite files from the script export.
    months_written          = 0
    months_skipped_existing = 0
    for (yy, mm), rows in sorted(buffers.items()):
        out_name = f'{symbol}_ticks_{yy:04d}_{mm:02d}.csv'
        out_path = os.path.join(dest_dir, out_name)
        if os.path.exists(out_path):
            months_skipped_existing += 1
            continue
        with open(out_path, 'w', encoding='utf-8', newline='') as out_fh:
            w = _csv.writer(out_fh)
            w.writerow(['timestamp_ms', 'bid', 'ask'])
            for ts_ms, bid, ask in rows:
                w.writerow([ts_ms, f'{bid:.5f}', f'{ask:.5f}'])
        months_written += 1

    return {
        'months_written':          months_written,
        'months_skipped_existing': months_skipped_existing,
        'rows_in':                 rows_in,
        'rows_out':                rows_out,
        'rows_dropped_no_quote':   rows_drop,
    }


def import_tick_data(tick_source, source_id, symbol='XAUUSD'):
    """Import tick data into an existing data source.

    Accepts EITHER:
      - a folder containing per-month files like XAUUSD_ticks_YYYY_MM.csv
        (what export_ticks.mq5 produces — copied as-is)
      - a single CSV file from MT5's Symbols window (Ctrl+U) export
        — parsed, forward-filled, and split into monthly files.

    WHY: Two upstream paths produce tick data; we accept both.
    CHANGED: April 2026 — tick data import
    CHANGED: April 2026 — accept single-file MT5 Symbols-window export

    Args:
        tick_source: folder OR single CSV file path
        source_id:   existing data source ID to add ticks to
        symbol:      symbol name for output filenames (default XAUUSD)

    Returns:
        dict with import results
    """
    dest = os.path.join(get_sources_dir(), source_id)
    if not os.path.isdir(dest):
        return {'error': f'Data source {source_id} not found at {dest}'}

    # WHY: Detect whether the user picked a single CSV or a folder.
    #      Folder = existing behaviour (copy per-month files as-is).
    #      File   = MT5 Symbols-window export (parse + split).
    # CHANGED: April 2026 — branch on file vs folder
    if os.path.isfile(tick_source) and tick_source.lower().endswith('.csv'):
        result = _split_mt5_symbols_export_to_monthlies(
            tick_source, dest, symbol)
        if 'error' in result:
            return result
        copied     = result['months_written']
        extra_info = {
            'mode':                    'single_file_split',
            'months_written':          result['months_written'],
            'months_skipped_existing': result['months_skipped_existing'],
            'rows_in':                 result['rows_in'],
            'rows_out':                result['rows_out'],
            'rows_dropped_no_quote':   result['rows_dropped_no_quote'],
        }
    elif os.path.isdir(tick_source):
        copied = 0
        for f in os.listdir(tick_source):
            if f.endswith('.csv') and '_ticks' in f.lower():
                shutil.copy2(os.path.join(tick_source, f),
                             os.path.join(dest, f))
                copied += 1
        extra_info = {'mode': 'folder_copy'}
    else:
        return {'error':
                f'tick_source must be a folder or .csv file: {tick_source}'}

    # Update metadata
    meta_path = os.path.join(dest, '_source_info.json')
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as fh:
                meta = json.load(fh)
        except Exception:
            pass
    meta['has_ticks']          = True
    meta['tick_files_imported'] = copied
    meta['ticks_imported_at']  = datetime.now().isoformat()
    with open(meta_path, 'w') as fh:
        json.dump(meta, fh, indent=2)

    return {
        'source_id':         source_id,
        'tick_files_copied': copied,
        **extra_info,
    }


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


# WHY: Historical accident produced three competing names for the
#      same data source. Unify everything on _CANONICAL_SOURCE_ID
#      and never run the silent fallback again without a warning.
# CHANGED: April 2026 — source-name unification
def migrate_source_names(project_root=None, log=None):
    """Run-once: unify every reference to the leveraged data folder.

    1. Rename xauusd_unlimited_levereged/ → unlimited_leveraged_data/
    2. Patch p1_config.json
    3. Patch saved_rules.json (missing or legacy IDs)
    4. Patch every JSON under project1_reverse_engineering/outputs/

    Idempotent — re-running does nothing once unified.

    CHANGED: April 2026 — source-name unification
    """
    def _log(msg):
        if log is not None:
            log(msg)
        else:
            print(f'[migrate_source_names] {msg}')

    if project_root is None:
        project_root = os.path.dirname(_DATA_DIR)

    sources_dir   = get_sources_dir()
    canonical_path = os.path.join(sources_dir, _CANONICAL_SOURCE_ID)
    typo_path      = os.path.join(sources_dir, 'xauusd_unlimited_levereged')

    rename_count         = 0
    p1_config_changes    = 0
    saved_rules_changes  = 0
    output_files_changed = 0

    # 1. Disk rename.
    if os.path.isdir(typo_path) and os.path.isdir(canonical_path):
        _log(f"WARNING: both {typo_path} and {canonical_path} exist. "
             "Manual merge required. Skipping migration.")
        return {
            'rename_count': 0, 'p1_config_changes': 0,
            'saved_rules_changes': 0, 'output_files_changed': 0,
            'error': 'both legacy and canonical folders exist; manual merge required',
        }
    if os.path.isdir(typo_path) and not os.path.isdir(canonical_path):
        try:
            os.rename(typo_path, canonical_path)
            rename_count = 1
            _log(f"renamed: {typo_path} -> {canonical_path}")
        except OSError as e:
            _log(f"WARNING: could not rename {typo_path}: {e}")
            return {
                'rename_count': 0, 'p1_config_changes': 0,
                'saved_rules_changes': 0, 'output_files_changed': 0,
                'error': str(e),
            }

    # 2. Patch p1_config.json.
    p1_config_path = os.path.join(
        project_root, 'project1_reverse_engineering', 'p1_config.json')
    if os.path.exists(p1_config_path):
        try:
            with open(p1_config_path, 'r', encoding='utf-8') as fh:
                cfg = json.load(fh)
            changed = False
            if cfg.get('data_source_id', '') in _LEGACY_SOURCE_NAMES:
                cfg['data_source_id'] = _CANONICAL_SOURCE_ID
                changed = True
            ds_path = cfg.get('data_source_path', '') or ''
            new_path = ds_path
            for leg in _LEGACY_SOURCE_NAMES:
                if leg and leg in new_path:
                    new_path = new_path.replace(leg, _CANONICAL_SOURCE_ID)
            if new_path != ds_path:
                cfg['data_source_path'] = new_path
                changed = True
            if changed:
                with open(p1_config_path, 'w', encoding='utf-8') as fh:
                    json.dump(cfg, fh, indent=2)
                p1_config_changes = 1
                _log("patched p1_config.json")
        except Exception as e:
            _log(f"WARNING: could not patch p1_config.json: {e}")

    # 3. Patch saved_rules.json.
    saved_rules_path = os.path.join(project_root, 'saved_rules.json')
    if os.path.exists(saved_rules_path):
        try:
            with open(saved_rules_path, 'r', encoding='utf-8') as fh:
                rules = json.load(fh)
            if isinstance(rules, list):
                for r in rules:
                    if not isinstance(r, dict):
                        continue
                    for target in [r, r.get('rule') if isinstance(r.get('rule'), dict) else None]:
                        if target is None:
                            continue
                        ds_id   = target.get('data_source_id', '')
                        ds_path = target.get('data_source_path', '') or ''
                        chg = False
                        if not ds_id or ds_id in _LEGACY_SOURCE_NAMES:
                            target['data_source_id'] = _CANONICAL_SOURCE_ID
                            chg = True
                        new_p = ds_path
                        for leg in _LEGACY_SOURCE_NAMES:
                            if leg and leg in new_p:
                                new_p = new_p.replace(leg, _CANONICAL_SOURCE_ID)
                        if new_p != ds_path:
                            target['data_source_path'] = new_p
                            chg = True
                        if chg:
                            saved_rules_changes += 1
                if saved_rules_changes > 0:
                    with open(saved_rules_path, 'w', encoding='utf-8') as fh:
                        json.dump(rules, fh, indent=2)
                    _log(f"patched saved_rules.json: {saved_rules_changes} entries")
        except Exception as e:
            _log(f"WARNING: could not patch saved_rules.json: {e}")

    # 4. Patch JSON files under project1_reverse_engineering/outputs/.
    outputs_dir = os.path.join(
        project_root, 'project1_reverse_engineering', 'outputs')
    if os.path.isdir(outputs_dir):
        for root, _dirs, files in os.walk(outputs_dir):
            for f in files:
                if not f.endswith('.json'):
                    continue
                fp = os.path.join(root, f)
                try:
                    with open(fp, 'r', encoding='utf-8') as fh:
                        contents = fh.read()
                    new_contents = contents
                    for leg in _LEGACY_SOURCE_NAMES:
                        if leg and leg in new_contents:
                            new_contents = new_contents.replace(leg, _CANONICAL_SOURCE_ID)
                    if new_contents != contents:
                        with open(fp, 'w', encoding='utf-8') as fh:
                            fh.write(new_contents)
                        output_files_changed += 1
                except Exception as e:
                    _log(f"WARNING: could not patch {fp}: {e}")
        if output_files_changed > 0:
            _log(f"patched {output_files_changed} output JSON files")

    return {
        'rename_count':         rename_count,
        'p1_config_changes':    p1_config_changes,
        'saved_rules_changes':  saved_rules_changes,
        'output_files_changed': output_files_changed,
    }

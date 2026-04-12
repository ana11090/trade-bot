"""
shared/dataset_registry.py

First-class Dataset abstraction for trade-bot. A Dataset is a named,
registered, hashed bundle of candle data for one instrument. Every
rule discovered in Project 1 gets stamped with the ID of the dataset
it was found on, and downstream pipeline stages (backtest, refine,
validate, EA gen) will hard-block if the active dataset doesn't match
the rule's home dataset.

WHY: Before Phase A the pipeline had a single global SYMBOL='XAUUSD'
     and a flat data/ folder. This made it impossible to (a) support
     multiple instruments, (b) guarantee that a rule was backtested
     against the same data it was discovered on. A silent rebuild of
     candles with different parameters would make every saved rule
     quietly backtest against different data. Dataset IDs + content
     hashes make that impossible.
CHANGED: April 2026 — Phase A — new module
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# WHY: Module-level globals per user rule #3 (no class refactors).
# CHANGED: April 2026 — Phase A
_REGISTRY_CACHE = None  # dict[dataset_id -> dataset dict], lazy-loaded
_DATASETS_ROOT = None   # set by config.py at import time


# -------- paths --------

def set_datasets_root(path):
    """Called once from config.py at import time."""
    global _DATASETS_ROOT, _REGISTRY_CACHE
    _DATASETS_ROOT = path
    _REGISTRY_CACHE = None  # invalidate
    os.makedirs(path, exist_ok=True)


def get_datasets_root():
    if _DATASETS_ROOT is None:
        raise RuntimeError(
            "dataset_registry: datasets_root not set. "
            "config.py must call set_datasets_root() at import."
        )
    return _DATASETS_ROOT


def dataset_dir(dataset_id):
    return os.path.join(get_datasets_root(), dataset_id)


def dataset_candles_dir(dataset_id):
    return os.path.join(dataset_dir(dataset_id), 'candles')


def dataset_json_path(dataset_id):
    return os.path.join(dataset_dir(dataset_id), 'dataset.json')


def manifest_json_path(dataset_id):
    return os.path.join(dataset_dir(dataset_id), 'manifest.json')


# -------- hashing --------

def compute_candle_hash(candles_dir):
    """Content hash of all *.csv files in a candles dir.

    WHY: Stamped on rules so Phase E can detect if candles were
         regenerated under the same dataset ID. If hash drifts, the
         rule is considered orphaned and the pipeline hard-blocks.
    CHANGED: April 2026 — Phase A
    """
    if not os.path.isdir(candles_dir):
        return None
    h = hashlib.sha256()
    try:
        files = sorted(f for f in os.listdir(candles_dir) if f.endswith('.csv'))
    except OSError:
        return None
    for fname in files:
        fpath = os.path.join(candles_dir, fname)
        try:
            with open(fpath, 'rb') as f:
                # WHY: stream in chunks — candle files can be 100MB+
                # CHANGED: April 2026 — Phase A
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
            h.update(fname.encode('utf-8'))
        except OSError as e:
            log.warning("hash: skipping %s: %s", fpath, e)
            continue
    return h.hexdigest()


def is_lfs_stub(csv_path):
    """Return True if the file is a Git LFS pointer stub, not real data.

    WHY: The user's repo has data/xauusd_*.csv committed as LFS pointers.
         A fresh clone pulls the stub, not the candle content, and step1
         crashes with KeyError: 'timestamp'. Migration uses this to mark
         the default dataset as candles_missing instead of pretending
         the files are usable.
    CHANGED: April 2026 — Phase A
    """
    try:
        with open(csv_path, 'rb') as f:
            head = f.read(200)
        return head.startswith(b'version https://git-lfs.github.com/spec/v1')
    except OSError:
        return False


# -------- registry CRUD --------

def _load_registry():
    """Scan datasets_root and build the in-memory registry dict."""
    global _REGISTRY_CACHE
    root = get_datasets_root()
    registry = {}
    try:
        entries = os.listdir(root)
    except OSError:
        entries = []
    for name in entries:
        dpath = os.path.join(root, name)
        if not os.path.isdir(dpath):
            continue
        jpath = os.path.join(dpath, 'dataset.json')
        if not os.path.isfile(jpath):
            continue
        try:
            with open(jpath, 'r', encoding='utf-8') as f:
                ds = json.load(f)
            registry[ds['id']] = ds
        except (OSError, json.JSONDecodeError, KeyError) as e:
            # WHY: per-row try/except per user rule #5
            # CHANGED: April 2026 — Phase A
            log.warning("registry: skipping %s: %s", jpath, e)
            continue
    _REGISTRY_CACHE = registry
    return registry


def list_datasets():
    if _REGISTRY_CACHE is None:
        _load_registry()
    return dict(_REGISTRY_CACHE)


def get_dataset(dataset_id):
    if _REGISTRY_CACHE is None:
        _load_registry()
    return _REGISTRY_CACHE.get(dataset_id)


def register_dataset(
    dataset_id,
    symbol,
    display_label,
    tick_root=None,
    start_date=None,
    end_date=None,
    pip_value=None,
    contract_spec=None,
    status='candles_missing',
):
    """Create a dataset directory and write dataset.json.

    WHY: Single entry point for creating datasets. Phase B's "+ Add Dataset"
         dialog will call this; migration in dataset_migration.py also
         calls it for the auto-created xauusd_default.
    CHANGED: April 2026 — Phase A
    """
    ddir = dataset_dir(dataset_id)
    cdir = dataset_candles_dir(dataset_id)
    os.makedirs(ddir, exist_ok=True)
    os.makedirs(cdir, exist_ok=True)

    record = {
        'id': dataset_id,
        'display_label': display_label,
        'symbol': symbol.upper(),
        'tick_root': tick_root,
        'start_date': start_date,
        'end_date': end_date,
        'pip_value': pip_value,
        'contract_spec': contract_spec or {},
        'status': status,  # candles_missing | building | ready | stale | error
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'candle_hash': None,
    }
    _atomic_write_json(dataset_json_path(dataset_id), record)
    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None  # force reload
    return record


def update_dataset(dataset_id, **fields):
    ds = get_dataset(dataset_id)
    if ds is None:
        raise KeyError(f"unknown dataset_id: {dataset_id}")
    ds.update(fields)
    ds['updated_at'] = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(dataset_json_path(dataset_id), ds)
    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None
    return ds


def write_manifest(dataset_id, row_counts, build_duration_seconds):
    """Write manifest.json with per-TF row counts and build metadata."""
    manifest = {
        'dataset_id': dataset_id,
        'built_at': datetime.now(timezone.utc).isoformat(),
        'build_duration_seconds': build_duration_seconds,
        'row_counts': row_counts,  # dict: tf -> int
        'candle_hash': compute_candle_hash(dataset_candles_dir(dataset_id)),
    }
    _atomic_write_json(manifest_json_path(dataset_id), manifest)
    # mirror hash into dataset.json and flip status to ready
    update_dataset(
        dataset_id,
        candle_hash=manifest['candle_hash'],
        status='ready',
    )
    return manifest


def _atomic_write_json(path, obj):
    """Write JSON atomically (tmp + rename) to prevent torn writes.

    WHY: If the app crashes mid-write, a corrupted dataset.json would
         break the registry load on next launch.
    CHANGED: April 2026 — Phase A
    """
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


# -------- active dataset pointer --------

_ACTIVE_FILE = '.active_dataset'

def get_active_dataset_id():
    """Read the active dataset pointer from disk."""
    path = os.path.join(get_datasets_root(), _ACTIVE_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip() or None
    except OSError:
        return None


def set_active_dataset_id(dataset_id):
    if dataset_id is not None and get_dataset(dataset_id) is None:
        raise KeyError(f"cannot activate unknown dataset: {dataset_id}")
    path = os.path.join(get_datasets_root(), _ACTIVE_FILE)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(dataset_id or '')
    os.replace(tmp, path)

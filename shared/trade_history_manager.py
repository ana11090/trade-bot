"""
Trade History Manager — handles multi-trade-history workspace operations.

Users load trade histories (CSV exports) from different trading robots.
Each trade history gets its own isolated folder under trade_histories/ with
its own trades, config, and analysis results. Shared resources (price data,
indicators, prop firm profiles) live in common folders.

Naming convention:
  - "trade history" = a set of trades from a specific robot
  - The robot name is metadata identifying the source
  - We do NOT import robot code — only trade results
"""

import os
import json
import shutil
import re
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
_SHARED_DIR   = os.path.dirname(os.path.abspath(__file__))  # shared/
_PROJECT_ROOT = os.path.dirname(_SHARED_DIR)                 # trade-bot/
_HISTORIES_DIR = os.path.join(_PROJECT_ROOT, "trade_histories")
_REGISTRY_PATH = os.path.join(_HISTORIES_DIR, "_registry.json")


def get_project_root() -> str:
    return _PROJECT_ROOT


# ── Registry ──────────────────────────────────────────────────────────────────

def get_registry() -> dict:
    os.makedirs(_HISTORIES_DIR, exist_ok=True)
    if not os.path.exists(_REGISTRY_PATH):
        registry = {"trade_histories": [], "active_history_id": None}
        save_registry(registry)
        return registry
    try:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"Registry is corrupted or unreadable: {e}")


def save_registry(registry: dict):
    os.makedirs(_HISTORIES_DIR, exist_ok=True)
    with open(_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)


# ── Listing ───────────────────────────────────────────────────────────────────

def list_trade_histories() -> list:
    registry = get_registry()
    result = []
    for entry in registry["trade_histories"]:
        history_id = entry["history_id"]
        config_path = os.path.join(_HISTORIES_DIR, history_id, "history_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                result.append({
                    "history_id":  cfg.get("history_id"),
                    "robot_name":  cfg.get("robot_name"),
                    "symbol":      cfg.get("symbol"),
                    "trade_count": cfg.get("trade_count"),
                    "date_loaded": cfg.get("date_loaded"),
                    "date_range":  cfg.get("date_range"),
                    "status":      cfg.get("status"),
                })
            except (json.JSONDecodeError, OSError):
                pass  # skip corrupted entries
    return result


# ── Active history ────────────────────────────────────────────────────────────

def get_active_history() -> dict | None:
    registry = get_registry()
    history_id = registry.get("active_history_id")
    if not history_id:
        return None
    try:
        return get_history_config(history_id)
    except FileNotFoundError:
        return None


def set_active_history(history_id: str):
    registry = get_registry()
    ids = {entry["history_id"] for entry in registry["trade_histories"]}
    if history_id not in ids:
        raise ValueError(f"Trade history '{history_id}' not found in registry.")
    registry["active_history_id"] = history_id
    save_registry(registry)


# ── Load trades ───────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s]", "", slug)
    slug = re.sub(r"\s+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug


def _parse_trades_csv(csv_path: str) -> tuple[int, dict, str | None]:
    """
    Return (trade_count, date_range, detected_symbol).
    Uses pandas for date parsing; falls back gracefully if unavailable.
    """
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        trade_count = len(df)
        date_range = {}
        if "Open Date" in df.columns:
            dates = pd.to_datetime(df["Open Date"], dayfirst=True, errors="coerce").dropna()
            if not dates.empty:
                date_range = {
                    "start": dates.min().strftime("%Y-%m-%d"),
                    "end":   dates.max().strftime("%Y-%m-%d"),
                }
        detected_symbol = None
        if "Symbol" in df.columns:
            symbols = df["Symbol"].dropna().unique()
            if len(symbols) == 1:
                detected_symbol = str(symbols[0])
        return trade_count, date_range, detected_symbol
    except ImportError:
        # Fallback: pure stdlib CSV parsing
        import csv
        from datetime import datetime as dt
        dates = []
        count = 0
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                count += 1
                raw = row.get("Open Date", "").strip()
                if raw:
                    try:
                        dates.append(dt.strptime(raw, "%d/%m/%Y %H:%M"))
                    except ValueError:
                        pass
        date_range = {}
        if dates:
            date_range = {
                "start": min(dates).strftime("%Y-%m-%d"),
                "end":   max(dates).strftime("%Y-%m-%d"),
            }
        return count, date_range, None


def load_trades(
    robot_name: str,
    trades_csv_path: str,
    symbol: str = "XAUUSD",
    description: str = "",
) -> dict:
    """
    Load a new trade history from a CSV file.
    Returns the full history_config dict.
    """
    # Resolve path relative to project root if not absolute
    if not os.path.isabs(trades_csv_path):
        trades_csv_path = os.path.join(_PROJECT_ROOT, trades_csv_path)

    if not os.path.exists(trades_csv_path):
        raise FileNotFoundError(f"Trades file not found: {trades_csv_path}")

    # Generate unique history_id
    base_slug = _slugify(robot_name)
    registry = get_registry()
    existing_ids = {entry["history_id"] for entry in registry["trade_histories"]}
    history_id = base_slug
    counter = 2
    while history_id in existing_ids:
        history_id = f"{base_slug}_{counter}"
        counter += 1

    # Create folder structure
    history_dir = os.path.join(_HISTORIES_DIR, history_id)
    try:
        os.makedirs(history_dir, exist_ok=True)
        os.makedirs(os.path.join(history_dir, "project0_results"), exist_ok=True)
        os.makedirs(os.path.join(history_dir, "project1_results", "scenarios"), exist_ok=True)
        os.makedirs(os.path.join(history_dir, "project2_results"), exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"Could not create workspace folder: {e}")

    # Copy trades files
    dest_original = os.path.join(history_dir, "trades_original.csv")
    dest_clean    = os.path.join(history_dir, "trades_clean.csv")
    shutil.copy2(trades_csv_path, dest_original)
    shutil.copy2(trades_csv_path, dest_clean)

    # Parse CSV for metadata
    try:
        trade_count, date_range, detected_symbol = _parse_trades_csv(dest_original)
    except Exception as e:
        trade_count, date_range, detected_symbol = 0, {}, None

    # Use detected symbol if provided symbol is default and CSV has one
    if symbol == "XAUUSD" and detected_symbol:
        symbol = detected_symbol

    # Build config
    config = {
        "history_id":   history_id,
        "robot_name":   robot_name,
        "symbol":       symbol,
        "description":  description,
        "date_loaded":  datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        "trade_count":  trade_count,
        "date_range":   date_range,
        "status":       "loaded",
        "source_file":  os.path.basename(trades_csv_path),
        "pipeline_progress": {
            "project0": "pending",
            "project1": "pending",
            "project2": "pending",
        },
    }

    config_path = os.path.join(history_dir, "history_config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    # Update registry
    registry["trade_histories"].append({"history_id": history_id})
    registry["active_history_id"] = history_id
    save_registry(registry)

    return config


# ── Path helpers ──────────────────────────────────────────────────────────────

def get_history_path(history_id: str) -> str:
    return os.path.join(_HISTORIES_DIR, history_id)


def get_history_trades_path(history_id: str) -> str:
    return os.path.join(_HISTORIES_DIR, history_id, "trades_clean.csv")


def get_history_config(history_id: str) -> dict:
    config_path = os.path.join(_HISTORIES_DIR, history_id, "history_config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"No config found for trade history '{history_id}'.")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def update_history_config(history_id: str, updates: dict):
    """Merge updates into an existing history_config.json and save."""
    config = get_history_config(history_id)
    config.update(updates)
    config_path = os.path.join(_HISTORIES_DIR, history_id, "history_config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_trade_history(history_id: str) -> bool:
    registry = get_registry()
    before = len(registry["trade_histories"])
    registry["trade_histories"] = [
        e for e in registry["trade_histories"] if e["history_id"] != history_id
    ]
    if len(registry["trade_histories"]) == before:
        return False  # not found

    if registry.get("active_history_id") == history_id:
        registry["active_history_id"] = None

    save_registry(registry)

    history_dir = os.path.join(_HISTORIES_DIR, history_id)
    if os.path.exists(history_dir):
        shutil.rmtree(history_dir)

    return True

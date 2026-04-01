"""
Robot Manager — handles multi-robot workspace operations.
Each robot gets its own isolated folder under robots/ with its own
trades, config, and results. Shared resources (price data, indicators,
prop firm profiles) live in common folders.
"""

import os
import json
import shutil
import re
import csv
from datetime import datetime

# ── Paths ────────────────────────────────────────────────────────────────────
_HERE        = os.path.dirname(os.path.abspath(__file__))   # shared/
PROJECT_ROOT = os.path.dirname(_HERE)                        # trade-bot/
ROBOTS_DIR   = os.path.join(PROJECT_ROOT, "robots")
REGISTRY_PATH = os.path.join(ROBOTS_DIR, "_registry.json")


def _ensure_robots_dir():
    os.makedirs(ROBOTS_DIR, exist_ok=True)


# ── Registry ─────────────────────────────────────────────────────────────────

def get_registry() -> dict:
    _ensure_robots_dir()
    if not os.path.exists(REGISTRY_PATH):
        registry = {"robots": [], "active_robot_id": None}
        save_registry(registry)
        return registry
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(registry: dict):
    _ensure_robots_dir()
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)


# ── Robot listing ─────────────────────────────────────────────────────────────

def list_robots() -> list:
    registry = get_registry()
    result = []
    for entry in registry["robots"]:
        robot_id = entry["robot_id"]
        config_path = os.path.join(ROBOTS_DIR, robot_id, "robot_config.json")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            result.append({
                "robot_id":      cfg.get("robot_id"),
                "name":          cfg.get("name"),
                "symbol":        cfg.get("symbol"),
                "trade_count":   cfg.get("trade_count"),
                "date_imported": cfg.get("date_imported"),
                "status":        cfg.get("status"),
            })
    return result


# ── Active robot ──────────────────────────────────────────────────────────────

def get_active_robot() -> dict | None:
    registry = get_registry()
    robot_id = registry.get("active_robot_id")
    if not robot_id:
        return None
    return get_robot_config(robot_id)


def set_active_robot(robot_id: str):
    registry = get_registry()
    ids = [r["robot_id"] for r in registry["robots"]]
    if robot_id not in ids:
        raise ValueError(f"Robot '{robot_id}' not found in registry.")
    registry["active_robot_id"] = robot_id
    save_registry(registry)


# ── Import ────────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug)
    return slug


def _parse_trades_csv(csv_path: str) -> tuple[int, dict]:
    """Return (trade_count, date_range) from a trades CSV."""
    dates = []
    count = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            count += 1
            raw = row.get("Open Date", "").strip()
            if raw:
                # Format: DD/MM/YYYY HH:MM
                try:
                    dt = datetime.strptime(raw, "%d/%m/%Y %H:%M")
                    dates.append(dt)
                except ValueError:
                    pass
    date_range = {}
    if dates:
        date_range = {
            "start": min(dates).strftime("%Y-%m-%d"),
            "end":   max(dates).strftime("%Y-%m-%d"),
        }
    return count, date_range


def import_robot(
    name: str,
    trades_csv_path: str,
    symbol: str = "XAUUSD",
    description: str = "",
) -> dict:
    """
    Import a new robot from a trades CSV file.
    Returns the robot_config dict.
    """
    # Resolve trades path relative to project root if not absolute
    if not os.path.isabs(trades_csv_path):
        trades_csv_path = os.path.join(PROJECT_ROOT, trades_csv_path)

    if not os.path.exists(trades_csv_path):
        raise FileNotFoundError(f"Trades file not found: {trades_csv_path}")

    # Generate unique robot_id
    base_slug = _slugify(name)
    registry = get_registry()
    existing_ids = {r["robot_id"] for r in registry["robots"]}
    robot_id = base_slug
    counter = 2
    while robot_id in existing_ids:
        robot_id = f"{base_slug}_{counter}"
        counter += 1

    # Create folder structure
    robot_dir = os.path.join(ROBOTS_DIR, robot_id)
    os.makedirs(robot_dir, exist_ok=True)
    os.makedirs(os.path.join(robot_dir, "project0_results"), exist_ok=True)
    os.makedirs(os.path.join(robot_dir, "project1_results", "scenarios"), exist_ok=True)
    os.makedirs(os.path.join(robot_dir, "project2_results"), exist_ok=True)

    # Copy trades
    dest_original = os.path.join(robot_dir, "trades_original.csv")
    dest_clean    = os.path.join(robot_dir, "trades_clean.csv")
    shutil.copy2(trades_csv_path, dest_original)
    shutil.copy2(trades_csv_path, dest_clean)

    # Parse CSV for metadata
    trade_count, date_range = _parse_trades_csv(dest_original)

    # Build config
    config = {
        "robot_id":      robot_id,
        "name":          name,
        "symbol":        symbol,
        "description":   description,
        "date_imported": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        "trade_count":   trade_count,
        "date_range":    date_range,
        "status":        "imported",
        "source_file":   os.path.basename(trades_csv_path),
    }

    config_path = os.path.join(robot_dir, "robot_config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    # Update registry
    registry["robots"].append({"robot_id": robot_id})
    registry["active_robot_id"] = robot_id
    save_registry(registry)

    return config


# ── Paths ─────────────────────────────────────────────────────────────────────

def get_robot_path(robot_id: str) -> str:
    return os.path.join(ROBOTS_DIR, robot_id)


def get_robot_trades_path(robot_id: str) -> str:
    return os.path.join(ROBOTS_DIR, robot_id, "trades_clean.csv")


def get_robot_config(robot_id: str) -> dict:
    config_path = os.path.join(ROBOTS_DIR, robot_id, "robot_config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"No config found for robot '{robot_id}'.")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_robot(robot_id: str) -> bool:
    registry = get_registry()
    before = len(registry["robots"])
    registry["robots"] = [r for r in registry["robots"] if r["robot_id"] != robot_id]
    if len(registry["robots"]) == before:
        return False  # not found

    if registry.get("active_robot_id") == robot_id:
        registry["active_robot_id"] = None

    save_registry(registry)

    robot_dir = os.path.join(ROBOTS_DIR, robot_id)
    if os.path.exists(robot_dir):
        shutil.rmtree(robot_dir)

    return True

"""
One-time migration: loads the original trade history into the workspace system.
Run once from the project root: python migrate_to_workspaces.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.trade_history_manager import load_trades, get_history_path, list_trade_histories, get_active_history

TRADES_PATH = os.path.join(
    "project0_data_pipeline", "Data Files for data mining", "trades_clean.csv"
)
EXPECTED_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "trade_histories", "original_bot"
)

if __name__ == "__main__":
    if os.path.exists(EXPECTED_FOLDER):
        print("Already migrated — trade_histories/original_bot/ exists. Skipping.")
        sys.exit(0)

    print("Migrating original trade history to workspace system...")

    # WHY: Old code hardcoded "2020-2026" which breaks when users migrate
    #      newer datasets or re-run the migration in future years. Generic
    #      description "(full history)" is always correct.
    # CHANGED: April 2026 — remove hardcoded year (audit Part B #25, Phase 27 Fix 5)
    history = load_trades(
        robot_name="Original Bot",
        trades_csv_path=TRADES_PATH,
        symbol="XAUUSD",
        description="First loaded trade history (full history)",
    )

    print(f"Loaded  : {history['robot_name']} (id: {history['history_id']})")
    print(f"Trades  : {history['trade_count']}")
    print(f"Dates   : {history['date_range']}")
    print(f"Path    : {get_history_path(history['history_id'])}")

    history_path = get_history_path(history["history_id"])
    for fname in ("trades_original.csv", "trades_clean.csv", "history_config.json"):
        full = os.path.join(history_path, fname)
        assert os.path.exists(full), f"MISSING: {full}"
        print(f"  OK  {fname}")

    histories = list_trade_histories()
    print(f"\nTotal trade histories in registry: {len(histories)}")

    active = get_active_history()
    print(f"Active trade history: {active['robot_name']}")
    print()
    print("Original file kept at its current location for backward compatibility.")
    print("It will be deprecated in a future update.")
    print("\nMigration complete.")

"""
One-time migration: imports the original robot's trades into the workspace system.
Run once from the project root: python migrate_to_workspaces.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.robot_manager import import_robot, get_robot_path, list_robots, get_active_robot

TRADES_PATH = os.path.join(
    "project0_data_pipeline", "Data Files for data mining", "trades_clean.csv"
)

if __name__ == "__main__":
    print("Migrating original bot to workspace system...")

    robot = import_robot(
        name="Original Bot",
        trades_csv_path=TRADES_PATH,
        symbol="XAUUSD",
        description="First imported robot - original trade history",
    )

    print(f"Imported : {robot['name']} (id: {robot['robot_id']})")
    print(f"Trades   : {robot['trade_count']}")
    print(f"Date range: {robot['date_range']}")
    print(f"Path     : {get_robot_path(robot['robot_id'])}")

    robot_path = get_robot_path(robot["robot_id"])
    for fname in ("trades_original.csv", "trades_clean.csv", "robot_config.json"):
        full = os.path.join(robot_path, fname)
        assert os.path.exists(full), f"MISSING: {full}"
        print(f"  OK  {fname}")

    robots = list_robots()
    print(f"\nTotal robots in registry: {len(robots)}")

    active = get_active_robot()
    print(f"Active robot: {active['name']}")
    print("\nMigration complete.")

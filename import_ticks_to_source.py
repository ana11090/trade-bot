#!/usr/bin/env python3
"""One-time helper: import tick files into an existing data source.

Usage:
    python import_ticks_to_source.py <tick_source_path> <source_id>

Example:
    python import_ticks_to_source.py "D:/traiding data/trade-bot/xauusd/2026.05.03 levereged" levereged_2026.05.03
    python import_ticks_to_source.py "C:/MT5/MQL5/Files" levereged_2026.05.03

The first argument can be either:
  - A folder containing XAUUSD_ticks_YYYY_MM.csv files, or
  - A single MT5 Symbols-window export CSV (will be split into months).

After running, commit + push so other machines get the LFS-tracked
tick files when they pull.
"""
import os
import sys

# Make the repo root importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.data_sources import import_tick_data

def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    tick_source = sys.argv[1]
    source_id   = sys.argv[2]
    print(f"Importing ticks from: {tick_source}")
    print(f"            into:    data/sources/{source_id}/")
    print()
    result = import_tick_data(tick_source, source_id)
    if 'error' in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)
    print(f"Done.")
    print(f"  tick_files_copied: {result.get('tick_files_copied', 0)}")
    print(f"  mode:              {result.get('mode')}")
    if 'months_written' in result:
        print(f"  months_written:    {result['months_written']}")
        print(f"  rows_in / rows_out: {result.get('rows_in')} / {result.get('rows_out')}")
    print()
    print("Next:")
    print("  git add data/sources/")
    print("  git commit -m 'data: import tick files for spread-filter parity'")
    print("  git push origin main")
    print()
    print("(Tick files use LFS, so the push will upload them properly.")
    print(" Other machines that pull will receive them automatically.)")

if __name__ == '__main__':
    main()

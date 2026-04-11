#!/usr/bin/env python3
"""
Setup script for running trade-bot on a new computer.
Run this ONCE after cloning the repo.

Usage:
    python setup_new_machine.py
"""

import os
import sys
import subprocess
import glob

ROOT = os.path.dirname(os.path.abspath(__file__))


def check_python():
    v = sys.version_info
    print(f"Python {v.major}.{v.minor}.{v.micro}")
    if v.major < 3 or (v.major == 3 and v.minor < 10):
        print("ERROR: Python 3.10+ required")
        sys.exit(1)
    print("  Python version OK")


def install_packages():
    req = os.path.join(ROOT, 'requirements.txt')
    if not os.path.exists(req):
        print("WARNING: requirements.txt not found")
        return
    print("\nInstalling Python packages...")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', req])
    print("  Packages installed")


def check_data_files():
    """Check that candle CSVs exist for the required timeframes.

    WHY: Phase 27 Fix 4 — Old code hardcoded xauusd_*.csv filenames.
         Users with EURUSD or any other instrument got false MISSING
         errors. Now globs for any *_<TF>.csv per required timeframe.
         Reports the actual filenames found.
    CHANGED: April 2026 — Phase 27 Fix 4 (audit Part B #31)
    """
    import glob
    data_dir = os.path.join(ROOT, 'data')
    required_tfs = ['H1', 'M5', 'M15', 'H4', 'D1']

    print("\nChecking data files...")

    if not os.path.isdir(data_dir):
        print(f"  ERROR: data directory not found: {data_dir}")
        print(f"  Create it and copy your candle CSVs there.")
        return False

    problems = []
    for tf in required_tfs:
        # Match any symbol prefix: xauusd_H1.csv, eurusd_H1.csv, etc.
        candidates = glob.glob(os.path.join(data_dir, f"*_{tf}.csv"))
        if not candidates:
            problems.append(f"  MISSING: no *_{tf}.csv file found")
            continue

        # Pick the largest non-pointer file for this TF
        valid_files = []
        for fpath in candidates:
            fname = os.path.basename(fpath)
            size = os.path.getsize(fpath)
            if size < 10000:
                with open(fpath, 'r') as f:
                    first_line = f.readline()
                if 'git-lfs' in first_line or size < 200:
                    problems.append(
                        f"  LFS POINTER: {fname} ({size} bytes — needs real data)"
                    )
                else:
                    problems.append(f"  TOO SMALL: {fname} ({size} bytes)")
            else:
                valid_files.append((fpath, size))

        if valid_files:
            # Report the largest valid file for this timeframe
            valid_files.sort(key=lambda x: -x[1])
            best_fpath, best_size = valid_files[0]
            best_fname = os.path.basename(best_fpath)
            size_mb = best_size / 1024 / 1024
            extra = ""
            if len(valid_files) > 1:
                extra = f"  (+ {len(valid_files) - 1} other {tf} files)"
            print(f"  OK: {best_fname} ({size_mb:.1f} MB){extra}")

    if problems:
        print("\n  DATA FILES NEED ATTENTION:")
        for p in problems:
            print(p)
        print(f"\n  Copy your real CSV files from your original machine to: {data_dir}/")
        print(f"  Required timeframes: {', '.join(required_tfs)}")
        print(f"  Any symbol prefix is accepted (xauusd_*, eurusd_*, etc.)")
        return False
    else:
        print("  All required timeframes present and valid")
        return True


def check_outputs():
    print("\nChecking output files...")
    files = {
        'project1_reverse_engineering/outputs/analysis_report.json': 'Entry rules',
        'project2_backtesting/outputs/backtest_matrix.json': 'Backtest results',
    }
    for fpath, desc in files.items():
        full = os.path.join(ROOT, fpath)
        if os.path.exists(full):
            size = os.path.getsize(full)
            print(f"  OK: {fpath} ({size:,} bytes) — {desc}")
        else:
            print(f"  MISSING: {fpath} — {desc}")
            print(f"     Re-run the analysis/backtest to generate this.")


def create_dirs():
    dirs = [
        'project2_backtesting/outputs',
        'project1_reverse_engineering/outputs',
        'project4_strategy_creation/outputs',
    ]
    for d in dirs:
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    print("\n  Output directories OK")


def main():
    print("=" * 60)
    print("  Trade-Bot — New Machine Setup")
    print("=" * 60)

    check_python()
    install_packages()
    create_dirs()
    data_ok = check_data_files()
    check_outputs()

    print("\n" + "=" * 60)
    if data_ok:
        print("  READY — run: python main_app.py")
    else:
        print("  ALMOST READY — copy your data files first, then run:")
        print("    python main_app.py")
    print("=" * 60)


if __name__ == '__main__':
    main()

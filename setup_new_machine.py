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
    data_dir = os.path.join(ROOT, 'data')
    required = ['xauusd_H1.csv', 'xauusd_M5.csv', 'xauusd_M15.csv',
                'xauusd_H4.csv', 'xauusd_D1.csv']

    print("\nChecking data files...")
    problems = []
    for fname in required:
        fpath = os.path.join(data_dir, fname)
        if not os.path.exists(fpath):
            problems.append(f"  MISSING: {fname}")
            continue
        size = os.path.getsize(fpath)
        if size < 10000:
            with open(fpath, 'r') as f:
                first_line = f.readline()
            if 'git-lfs' in first_line or size < 200:
                problems.append(f"  LFS POINTER: {fname} ({size} bytes — needs real data)")
            else:
                problems.append(f"  TOO SMALL: {fname} ({size} bytes)")
        else:
            size_mb = size / 1024 / 1024
            print(f"  OK: {fname} ({size_mb:.1f} MB)")

    if problems:
        print("\n  DATA FILES NEED ATTENTION:")
        for p in problems:
            print(p)
        print(f"\n  Copy your real CSV files from your original machine to: {data_dir}/")
        return False
    else:
        print("  All data files present and valid")
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

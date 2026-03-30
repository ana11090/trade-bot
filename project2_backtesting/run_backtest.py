"""
PROJECT 2 - RUN BACKTEST
Orchestrator script that runs all backtest steps in sequence
"""

import os
import sys
import subprocess
from datetime import datetime

def run_step(script_name, description):
    """Run a script and handle errors"""
    print(f"\n{'=' * 60}")
    print(f"STEP: {description}")
    print(f"{'=' * 60}\n")

    try:
        result = subprocess.run(
            [sys.executable, script_name],
            capture_output=False,
            text=True,
            check=True
        )
        print(f"\n[SUCCESS] {description} completed")
        return True

    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] {description} failed")
        print(f"Error: {e}")
        return False

    except FileNotFoundError:
        print(f"\n[ERROR] Script not found: {script_name}")
        return False


def main():
    """Main entry point"""
    start_time = datetime.now()

    print("=" * 60)
    print("PROJECT 2 - RUN COMPLETE BACKTEST")
    print("=" * 60)
    print(f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Step 1: Run backtest engine
    if not run_step('backtest_engine.py', 'Backtest Engine'):
        print("\n[ABORT] Backtest failed. Stopping execution.")
        return

    # Step 2: Compute statistics
    if not run_step('compute_stats.py', 'Compute Statistics'):
        print("\n[ABORT] Statistics computation failed. Stopping execution.")
        return

    # Step 3: Build HTML report
    if not run_step('build_report.py', 'Build HTML Report'):
        print("\n[ABORT] Report generation failed. Stopping execution.")
        return

    # Success!
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    print("\n" + "=" * 60)
    print("BACKTEST COMPLETE - ALL STEPS SUCCESSFUL")
    print("=" * 60)
    print(f"Started:  {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Finished: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Duration: {duration:.1f} seconds")
    print("\n" + "=" * 60)
    print("NEXT STEPS")
    print("=" * 60)
    print("1. Open the HTML report:")
    print(f"   {os.path.abspath('./outputs/backtest_report.html')}")
    print("\n2. Review the performance metrics:")
    print("   - IN-SAMPLE vs OUT-OF-SAMPLE comparison")
    print("   - Win rate, profit factor, drawdown")
    print("   - Monthly and day-of-week analysis")
    print("\n3. Decide if the strategy is viable:")
    print("   - If OUT-OF-SAMPLE performs well: Strategy may be robust")
    print("   - If OUT-OF-SAMPLE fails: Likely overfitting, return to Project 1")
    print("=" * 60)


if __name__ == '__main__':
    main()

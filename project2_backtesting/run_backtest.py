"""
PROJECT 2 - RUN BACKTEST
Orchestrator script that runs all backtest steps in sequence
"""

import os
import sys
import subprocess
from datetime import datetime

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

def run_step(script_name, description):
    """Run a script and handle errors"""
    log.info(f"\n{'=' * 60}")
    log.info(f"STEP: {description}")
    log.info(f"{'=' * 60}\n")

    try:
        result = subprocess.run(
            [sys.executable, script_name],
            capture_output=False,
            text=True,
            check=True
        )
        log.info(f"\n[SUCCESS] {description} completed")
        return True

    except subprocess.CalledProcessError as e:
        log.info(f"\n[ERROR] {description} failed")
        log.info(f"Error: {e}")
        return False

    except FileNotFoundError:
        log.info(f"\n[ERROR] Script not found: {script_name}")
        return False


def main():
    """Main entry point"""
    start_time = datetime.now()

    log.info("=" * 60)
    log.info("PROJECT 2 - RUN COMPLETE BACKTEST")
    log.info("=" * 60)
    log.info(f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Step 1: Run backtest engine
    if not run_step('backtest_engine.py', 'Backtest Engine'):
        log.info("\n[ABORT] Backtest failed. Stopping execution.")
        return

    # Step 2: Compute statistics
    if not run_step('compute_stats.py', 'Compute Statistics'):
        log.info("\n[ABORT] Statistics computation failed. Stopping execution.")
        return

    # Step 3: Build HTML report
    if not run_step('build_report.py', 'Build HTML Report'):
        log.info("\n[ABORT] Report generation failed. Stopping execution.")
        return

    # Success!
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    log.info("\n" + "=" * 60)
    log.info("BACKTEST COMPLETE - ALL STEPS SUCCESSFUL")
    log.info("=" * 60)
    log.info(f"Started:  {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Finished: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Duration: {duration:.1f} seconds")
    log.info("\n" + "=" * 60)
    log.info("NEXT STEPS")
    log.info("=" * 60)
    log.info("1. Open the HTML report:")
    log.info(f"   {os.path.abspath('./outputs/backtest_report.html')}")
    log.info("\n2. Review the performance metrics:")
    log.info("   - IN-SAMPLE vs OUT-OF-SAMPLE comparison")
    log.info("   - Win rate, profit factor, drawdown")
    log.info("   - Monthly and day-of-week analysis")
    log.info("\n3. Decide if the strategy is viable:")
    log.info("   - If OUT-OF-SAMPLE performs well: Strategy may be robust")
    log.info("   - If OUT-OF-SAMPLE fails: Likely overfitting, return to Project 1")
    log.info("=" * 60)


if __name__ == '__main__':
    main()

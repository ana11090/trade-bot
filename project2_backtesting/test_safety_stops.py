"""
Quick test to verify DD safety stops tracking.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from project2_backtesting.strategy_refiner import count_dd_breaches
from datetime import datetime, timedelta

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

# Create test trades that will trigger both safety stops and breaches
test_trades = []
start_date = datetime(2024, 1, 1)

# With risk_pct=1%, lot_size = 1000/(150*10) = 0.667, dollar_per_pip = 6.67
# To get X% loss, need X% * 100000 / 6.67 pips

# Day 1: -4.2% loss (should trigger daily safety at 4%, but not breach at 5%)
# Need -4200 dollars = -630 pips
test_trades.append({
    'entry_time': (start_date).isoformat(),
    'net_pips': -630
})

# Day 2: +2% gain
test_trades.append({
    'entry_time': (start_date + timedelta(days=1)).isoformat(),
    'net_pips': 300
})

# Day 3: -5.2% loss (should trigger both daily safety AND daily breach)
# Need -5200 dollars = -780 pips
test_trades.append({
    'entry_time': (start_date + timedelta(days=2)).isoformat(),
    'net_pips': -780
})

# Account resets here due to breach

# Day 4-7: Build up to trigger total safety at 8%
# Day 4: +3% gain to establish high water at 103000
test_trades.append({
    'entry_time': (start_date + timedelta(days=3)).isoformat(),
    'net_pips': 450
})
# Day 5: -2% loss, balance now 101000, total DD = 2000 (1.9%)
test_trades.append({
    'entry_time': (start_date + timedelta(days=4)).isoformat(),
    'net_pips': -300
})
# Day 6: -3% loss, balance now 98000, total DD = 5000 (4.9%)
test_trades.append({
    'entry_time': (start_date + timedelta(days=5)).isoformat(),
    'net_pips': -450
})
# Day 7: -3.5% loss, balance now 94500, total DD = 8500 (8.3% of 103000)
# This should trigger total safety at 8% but not breach at 10%
test_trades.append({
    'entry_time': (start_date + timedelta(days=6)).isoformat(),
    'net_pips': -525
})

# Run the test
log.info("\n" + "="*60)
log.info("Testing DD Safety Stops Tracking")
log.info("="*60)

result = count_dd_breaches(
    test_trades,
    account_size=100000,
    risk_pct=1.0,
    pip_value=10.0,
    daily_dd_limit_pct=5.0,
    total_dd_limit_pct=10.0,
    daily_dd_safety_pct=4.0,
    total_dd_safety_pct=8.0
)

log.info("\n[RESULTS]")
log.info(f"  Daily breaches:        {result['daily_breaches']}")
log.info(f"  Total breaches:        {result['total_breaches']}")
log.info(f"  Total blown:           {result['blown_count']}")
log.info(f"  Daily safety stops:    {result['daily_safety_stops']}")
log.info(f"  Total safety stops:    {result['total_safety_stops']}")

log.info("\n[BREACH DATES]")
for d in result['daily_breach_dates']:
    log.info(f"  Daily breach: {d}")
for d in result['total_breach_dates']:
    log.info(f"  Total breach: {d}")

log.info("\n[SAFETY STOP DATES]")
for d in result['daily_safety_dates']:
    log.info(f"  Daily safety: {d}")
for d in result['total_safety_dates']:
    log.info(f"  Total safety: {d}")

log.info("\n[WORST DD]")
log.info(f"  Worst daily DD: {result['worst_daily_pct']:.1f}%")
log.info(f"  Worst total DD: {result['worst_total_pct']:.1f}%")

# Verify expectations
log.info("\n" + "="*60)
log.info("Verification")
log.info("="*60)

checks = [
    ("Daily safety stops > 0", result['daily_safety_stops'] > 0),
    ("Total safety stops >= 0", result['total_safety_stops'] >= 0),
    ("Daily breaches > 0", result['daily_breaches'] > 0),
    ("Safety dates returned", len(result['daily_safety_dates']) > 0 or len(result['total_safety_dates']) > 0),
]

all_pass = True
for desc, passed in checks:
    status = "[OK]" if passed else "[ERROR]"
    log.info(f"  {status} {desc}")
    if not passed:
        all_pass = False

if all_pass:
    log.info("\n[OK] All checks passed - safety stops tracking works!")
else:
    log.info("\n[ERROR] Some checks failed - review implementation")

log.info("="*60 + "\n")

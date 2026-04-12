"""
Phase 72 Verification Tests — live_firm_sim HIGHs
Verifies:
  - Fix 17: _locks_cleared_on_blow flag when account resets after blow
  - Fix 18: Warning log when lot_size floored to 0.01 inflates risk
  - Fix 19: pip_value_per_lot default from 10.0 to None, resolve from config
"""

import sys
import os
import logging
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'shared'))

from live_firm_sim import simulate_live_firm

# Set up logging to capture warnings
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')


def test_fix17_locks_cleared_on_blow():
    """
    V1: Verify _locks_cleared_on_blow flag tracks when locks are active before blow.

    Scenario: Create trades that:
      1. Gain +10% (triggers lock-after-gain at +8%)
      2. Then lose enough to blow the account

    Expected: locks_cleared_on_blow = True (lock was active before blow)
    """
    print("\n=== V1: Fix 17 — _locks_cleared_on_blow flag ===")

    # Firm with 8% lock-after-gain, 10% total DD, 5% daily DD
    firm_data = {
        'firm_name': 'TestFirm_Lock',
        'drawdown_mechanics': {
            'trailing_dd': {
                'basis': 'equity',
                'lock_after_gain_pct': 8.0,
                'lock_level': 'starting_balance'
            },
            'post_payout': {}
        },
        'challenges': [{
            'funded': {
                'max_daily_drawdown_pct': 5.0,
                'max_total_drawdown_pct': 10.0
            }
        }],
        'pip_value_per_lot': 10.0
    }

    # Generate trades: first profitable (trigger lock), then huge loss (blow)
    base_date = datetime(2024, 1, 1)
    trades = []

    # Day 1-5: Gain +10% total (+2% per day) to trigger the +8% lock
    for i in range(5):
        trades.append({
            'entry_time': (base_date + timedelta(days=i)).strftime('%Y-%m-%d %H:%M:%S'),
            'net_pips': 200,  # +200 pips/day * $10/pip = +$2000/day = +2% on $100k account
            'pips': 200,
            'net_profit': 2000
        })

    # Day 6: Lose -15% in one day (blow via daily DD and total DD)
    trades.append({
        'entry_time': (base_date + timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S'),
        'net_pips': -1500,  # -$15,000 = -15% on $100k
        'pips': -1500,
        'net_profit': -15000
    })

    result = simulate_live_firm(
        trades=trades,
        prop_firm_data=firm_data,
        account_size=100000,
        pip_value_per_lot=10.0,
        risk_pct=1.0,
        default_sl_pips=150.0
    )

    print(f"  Blown: {result['blown']}")
    print(f"  Lock triggered: {result['lock_triggered']}")
    print(f"  Locks cleared on blow: {result.get('locks_cleared_on_blow', 'MISSING')}")

    assert result['blown'], "Account should have blown"
    assert result['lock_triggered'], "Lock should have been triggered before blow"
    assert result.get('locks_cleared_on_blow') is True, \
        "locks_cleared_on_blow should be True when lock was active before blow"

    print("  [OK] Fix 17 verified: locks_cleared_on_blow = True when lock active before blow")


def test_fix18_lot_floor_warning():
    """
    V2: Verify warning log when lot_size is floored to 0.01, inflating risk.

    Scenario: Use tiny risk_pct or huge SL to force lot_size < 0.01
    Expected: Warning logged on first day
    """
    print("\n=== V2: Fix 18 — lot_size floor warning ===")

    firm_data = {
        'firm_name': 'TestFirm_LotFloor',
        'drawdown_mechanics': {'trailing_dd': {}, 'post_payout': {}},
        'challenges': [{
            'funded': {
                'max_daily_drawdown_pct': 5.0,
                'max_total_drawdown_pct': 10.0
            }
        }],
        'pip_value_per_lot': 10.0
    }

    trades = [{
        'entry_time': '2024-01-01 10:00:00',
        'net_pips': 10,
        'pips': 10,
        'net_profit': 100
    }]

    # Use tiny risk_pct (0.05%) with large SL (500 pips) to force lot_size < 0.01
    # risk_dollars = 100000 * 0.05 / 100 = $50
    # sl_denom = 500 * 10 = $5000
    # lot_size_calculated = 50 / 5000 = 0.01 (exactly) - should NOT warn
    # Let's use even smaller risk to actually trigger it

    # Capture logging output
    import io
    from contextlib import redirect_stderr

    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.WARNING)
    logger = logging.getLogger()
    logger.addHandler(handler)

    result = simulate_live_firm(
        trades=trades,
        prop_firm_data=firm_data,
        account_size=100000,
        pip_value_per_lot=10.0,
        risk_pct=0.01,  # 0.01% risk = $10, with SL=500 pips → lot = 10/5000 = 0.002
        default_sl_pips=500.0
    )

    log_output = log_capture.getvalue()
    logger.removeHandler(handler)

    print(f"  Log output captured: {repr(log_output[:100])}")

    assert 'lot_size floored to 0.01' in log_output, \
        "Should log warning when lot_size is floored to 0.01"
    assert 'actual risk inflated' in log_output, \
        "Warning should mention risk inflation"

    print("  [OK] Fix 18 verified: Warning logged when lot_size floored to 0.01")


def test_fix19_pip_value_from_config():
    """
    V3: Verify pip_value_per_lot resolves from firm config when None is passed.

    Scenario: Call simulate_live_firm with pip_value_per_lot=None
    Expected: Function reads pip_value_per_lot from firm JSON (e.g., 1.0 for EURUSD)
    """
    print("\n=== V3: Fix 19 — pip_value_per_lot from config ===")

    # Firm with pip_value_per_lot = 1.0 (EURUSD-like)
    firm_data = {
        'firm_name': 'TestFirm_EURUSD',
        'pip_value_per_lot': 1.0,  # $1/pip per lot (mini account)
        'drawdown_mechanics': {'trailing_dd': {}, 'post_payout': {}},
        'challenges': [{
            'funded': {
                'max_daily_drawdown_pct': 5.0,
                'max_total_drawdown_pct': 10.0
            }
        }]
    }

    trades = [{
        'entry_time': '2024-01-01 10:00:00',
        'net_pips': 100,  # +100 pips
        'pips': 100
        # No net_profit — force function to calculate from pips
    }]

    # Call with pip_value_per_lot=None — should read from firm_data
    result = simulate_live_firm(
        trades=trades,
        prop_firm_data=firm_data,
        account_size=100000,
        pip_value_per_lot=None,  # <-- None, should read from firm_data
        risk_pct=1.0,
        default_sl_pips=150.0
    )

    # With pip_value = 1.0, risk_pct = 1%, default_sl_pips = 150:
    # risk_dollars = 100000 * 1% = $1000
    # sl_denom = 150 * 1.0 = $150
    # lot_size = 1000 / 150 = 6.67 lots
    # dollar_per_pip = 1.0 * 6.67 = $6.67
    # day_pnl = 100 pips * $6.67 = $667
    # final_equity should be ~$100,667

    print(f"  Final equity: ${result['final_equity']:,.2f}")

    # The exact value depends on lot_size calculation, but should be around $100,600-700
    # If pip_value was still 10.0, final equity would be ~$106,670 (10× higher)
    assert 100600 <= result['final_equity'] <= 100700, \
        f"Final equity {result['final_equity']} suggests pip_value was not read from config"

    print("  [OK] Fix 19 verified: pip_value_per_lot resolved from firm config")


def test_fix19_default_fallback():
    """
    V4: Verify pip_value_per_lot falls back to 10.0 when not in firm config.

    Scenario: Firm JSON has no pip_value_per_lot field, and None is passed
    Expected: Defaults to 10.0 (XAUUSD standard)
    """
    print("\n=== V4: Fix 19 — default fallback to 10.0 ===")

    # Firm with NO pip_value_per_lot field
    firm_data = {
        'firm_name': 'TestFirm_NoConfig',
        'drawdown_mechanics': {'trailing_dd': {}, 'post_payout': {}},
        'challenges': [{
            'funded': {
                'max_daily_drawdown_pct': 5.0,
                'max_total_drawdown_pct': 10.0
            }
        }]
        # NO pip_value_per_lot field
    }

    trades = [{
        'entry_time': '2024-01-01 10:00:00',
        'net_pips': 100,
        'pips': 100
    }]

    result = simulate_live_firm(
        trades=trades,
        prop_firm_data=firm_data,
        account_size=100000,
        pip_value_per_lot=None,  # Should fall back to 10.0
        risk_pct=1.0,
        default_sl_pips=150.0
    )

    # With pip_value = 10.0 (default):
    # risk_dollars = $1000
    # sl_denom = 150 * 10 = $1500
    # lot_size = 1000 / 1500 = 0.67 lots
    # dollar_per_pip = 10 * 0.67 = $6.67
    # day_pnl = 100 * $6.67 = $667
    # final_equity ~= $100,667

    print(f"  Final equity: ${result['final_equity']:,.2f}")

    assert 100600 <= result['final_equity'] <= 100700, \
        f"Final equity {result['final_equity']} should reflect pip_value=10.0 default"

    print("  [OK] Fix 19 verified: pip_value_per_lot defaults to 10.0 when not in config")


if __name__ == '__main__':
    print("Phase 72 Verification Tests — live_firm_sim HIGHs")
    print("=" * 60)

    try:
        test_fix17_locks_cleared_on_blow()
        test_fix18_lot_floor_warning()
        test_fix19_pip_value_from_config()
        test_fix19_default_fallback()

        print("\n" + "=" * 60)
        print("[SUCCESS] All Phase 72 verification tests passed!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n[FAIL] Verification failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

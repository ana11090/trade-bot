"""
Refresh-all coordinator.

Calls refresh() on every registered panel module. Safe to call from
any thread — internally schedules each refresh on the Tk main thread
via state.window.after(0, ...).

Why a central coordinator: avoids tight coupling between
run_backtest_panel and every other panel. Adding a new panel = one
line in _REFRESH_TARGETS.
"""
import logging

log = logging.getLogger(__name__)

# Panel modules whose refresh() should be called after a backtest
# finishes. ORDER MATTERS only slightly — data-producing panels first
# is conventional. Each panel reads independently from disk so any
# order works.
_REFRESH_TARGETS = [
    # Project 2 — primary consumers of backtest_matrix.json
    'project2_backtesting.panels.view_results',
    'project2_backtesting.panels.strategy_refiner_panel',
    'project2_backtesting.panels.strategy_validator_panel',
    'project2_backtesting.panels.prop_firm_test',
    'project2_backtesting.panels.saved_rules_panel',
    'project2_backtesting.panels.strategy_playground',

    # Project 3 — consumes matrix for EA generation
    'project3_live_trading.panels.ea_generator_panel',

    # Project 0 — most don't read matrix but safe to refresh (no-op
    # when their data hasn't changed)
    'project0_data_pipeline.panels.compare_histories',
    'project0_data_pipeline.panels.lifecycle_simulator',
    'project0_data_pipeline.panels.prop_explorer',
    'project0_data_pipeline.panels.prop_compliance_v2',
    'project0_data_pipeline.panels.cost_spread',
]


def refresh_all_panels(reason='backtest_complete'):
    """Call refresh() on every panel module that has one.

    Schedules each call on the Tk main thread. Errors are logged but
    don't propagate — one broken panel must not prevent the others
    from refreshing.

    reason: a short string for the log line.
    Returns: integer count of refreshes scheduled (best-effort).
    """
    try:
        import state
        win = getattr(state, 'window', None)
    except Exception:
        win = None

    log.info(f"[REFRESH-ALL] Triggered by: {reason}")
    print(f"[REFRESH-ALL] Triggered by: {reason}")  # also to stdout so
                                                     # user sees it
                                                     # without log config

    scheduled = 0
    skipped   = 0

    for mod_path in _REFRESH_TARGETS:
        try:
            import importlib
            mod = importlib.import_module(mod_path)
        except ImportError:
            skipped += 1
            continue
        except Exception as e:
            log.warning(f"[REFRESH-ALL] cannot import {mod_path}: {e}")
            skipped += 1
            continue

        if not hasattr(mod, 'refresh'):
            skipped += 1
            continue

        _refresh_fn = mod.refresh

        def _safe_call(fn=_refresh_fn, name=mod_path):
            try:
                fn()
                log.info(f"[REFRESH-ALL]   ✓ {name}")
            except Exception as e:
                log.warning(f"[REFRESH-ALL]   ✗ {name}: {e}")
                print(f"[REFRESH-ALL]   ✗ {name}: {e}")

        if win is not None:
            try:
                win.after(0, _safe_call)
                scheduled += 1
            except Exception as e:
                log.warning(f"[REFRESH-ALL] window.after failed for {mod_path}: {e}")
                skipped += 1
        else:
            # No Tk window — call directly (testing, headless)
            _safe_call()
            scheduled += 1

    msg = f"[REFRESH-ALL] {scheduled} scheduled, {skipped} skipped"
    log.info(msg)
    print(msg)
    return scheduled

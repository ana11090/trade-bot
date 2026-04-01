"""
Shared utilities for trade-bot project.
Provides common functions for data processing, indicator computation,
and trade history management.

Modules are imported lazily to avoid pulling in heavy dependencies
(like ta, pytz, scikit-learn) when only lightweight utilities are needed.
"""

__all__ = ['data_utils', 'indicator_utils', 'trade_history_manager', 'prop_firm_engine', 'prop_firm_simulator']


def __getattr__(name):
    """Lazy import — modules load only when first accessed."""
    if name == 'data_utils':
        from . import data_utils
        return data_utils
    elif name == 'indicator_utils':
        from . import indicator_utils
        return indicator_utils
    elif name == 'trade_history_manager':
        from . import trade_history_manager
        return trade_history_manager
    elif name == 'prop_firm_engine':
        from . import prop_firm_engine
        return prop_firm_engine
    elif name == 'prop_firm_simulator':
        from . import prop_firm_simulator
        return prop_firm_simulator
    raise AttributeError(f"module 'shared' has no attribute {name!r}")

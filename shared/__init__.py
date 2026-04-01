"""
Shared utilities for trade-bot project.
Provides common functions for data processing, indicator computation,
and trade history management.

Modules are imported lazily to avoid pulling in heavy dependencies
(like ta, pytz, scikit-learn) when only lightweight utilities are needed.
"""

__all__ = ['data_utils', 'indicator_utils', 'trade_history_manager', 'prop_firm_engine', 'prop_firm_simulator', 'data_validator']


def __getattr__(name):
    """Lazy import — modules load only when first accessed."""
    import importlib

    if name in __all__:
        # Use importlib to avoid recursion
        module = importlib.import_module(f'.{name}', package=__package__)
        # Cache it in globals to avoid re-importing
        globals()[name] = module
        return module

    raise AttributeError(f"module 'shared' has no attribute {name!r}")

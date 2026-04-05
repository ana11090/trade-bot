"""
Global feature toggle — controls SMART and REGIME features across all projects.
Saved to feature_settings.json so settings persist between restarts.
"""
import os
import json
import tkinter as tk

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'feature_settings.json')
_DEFAULTS = {'smart_features': True, 'regime_features': True}
_current = dict(_DEFAULTS)


def load():
    global _current
    if os.path.exists(_PATH):
        try:
            with open(_PATH) as f:
                _current.update({k: v for k, v in json.load(f).items() if k in _DEFAULTS})
        except Exception:
            pass
    return dict(_current)


def save(**kw):
    global _current
    _current.update({k: v for k, v in kw.items() if k in _DEFAULTS})
    try:
        with open(_PATH, 'w') as f:
            json.dump(_current, f, indent=2)
    except Exception:
        pass


def get_smart():
    return _current.get('smart_features', True)


def get_regime():
    return _current.get('regime_features', True)


def build_toggle_widget(parent, bg="#ffffff"):
    """Compact toggle widget — embed in any panel. Auto-saves on change."""
    settings = load()
    frame = tk.LabelFrame(parent, text="🔬 Feature Settings (global — applies to all projects)",
                          font=("Arial", 9, "bold"), bg=bg, fg="#555555", padx=10, pady=5)
    sv = tk.BooleanVar(value=settings['smart_features'])
    rv = tk.BooleanVar(value=settings['regime_features'])

    def _c(*_):
        save(smart_features=sv.get(), regime_features=rv.get())

    row = tk.Frame(frame, bg=bg)
    row.pack(fill="x")
    tk.Checkbutton(row, text="SMART features (50: divergences, sessions, momentum)",
                   variable=sv, command=_c, bg=bg, fg="#333", font=("Arial", 9),
                   selectcolor=bg).pack(side=tk.LEFT, padx=(0, 15))
    tk.Checkbutton(row, text="Regime-aware (14: price-relative, market structure)",
                   variable=rv, command=_c, bg=bg, fg="#333", font=("Arial", 9),
                   selectcolor=bg).pack(side=tk.LEFT)
    tk.Label(frame, text="SMART: cross-TF divergences, session timing, volatility regimes, momentum quality\n"
                         "Regime: normalizes for gold $400→$5000 — ATR as % of price, trend alignment, vol buckets",
             font=("Arial", 8), bg=bg, fg="#888", justify=tk.LEFT).pack(fill="x", pady=(2, 0))
    return frame


load()

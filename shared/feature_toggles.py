"""
Global feature toggle — controls SMART and REGIME features across all projects.
Saved to feature_settings.json so settings persist between restarts.
"""
import os
import json
import tempfile
import threading
import tkinter as tk

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'feature_settings.json')
_DEFAULTS = {'smart_features': True, 'regime_features': True}
_current = dict(_DEFAULTS)

# WHY (Phase 73 Fix 47): Global _current dict is modified by save() without
#      locks. Two concurrent save() calls (e.g., from two panels) can corrupt
#      _current or write inconsistent JSON. Add a lock for load/save ops.
# CHANGED: April 2026 — Phase 73 Fix 47 — feature toggle lock
#          (audit Part F HIGH #47)
_settings_lock = threading.Lock()


def load():
    # Phase 73 Fix 47: Lock around global _current modification
    global _current
    with _settings_lock:
        if os.path.exists(_PATH):
            try:
                with open(_PATH) as f:
                    _current.update({k: v for k, v in json.load(f).items() if k in _DEFAULTS})
            except Exception:
                pass
        return dict(_current)


def save(**kw):
    # WHY (Phase 73 Fix 48): Old code used open('w') which truncates the file
    #      immediately. A crash between truncate and json.dump completing left
    #      feature_settings.json empty — all toggles reset to defaults. Write
    #      to tempfile in same directory, then os.replace (atomic).
    # CHANGED: April 2026 — Phase 73 Fix 48 — atomic write via tempfile
    #          (audit Part F HIGH #48)
    # Phase 73 Fix 47: Lock around global _current modification + write
    global _current
    with _settings_lock:
        _current.update({k: v for k, v in kw.items() if k in _DEFAULTS})
        dir_name = os.path.dirname(_PATH) or '.'
        fd, tmp_path = tempfile.mkstemp(
            suffix='.json', prefix='.tmp_features_', dir=dir_name
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(_current, fh, indent=2)
            os.replace(tmp_path, _PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
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

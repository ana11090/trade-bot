import tkinter as tk
import pandas as pd
import state


def normalize_condition(c):
    """Convert a condition to dict form regardless of source format.

    Handles:
      - dict already: {"feature": ..., "operator": ..., "value": ...} → returned as-is
      - string: "M15_roc_5 <= 0.1920" → {"feature": "M15_roc_5", "operator": "<=", "value": 0.192}
    """
    if isinstance(c, dict):
        return c
    # Parse string like "FEATURE OP VALUE"
    s = str(c).strip()
    for op in ['<=', '>=', '<', '>', '==', '!=']:
        if op in s:
            parts = s.split(op, 1)
            feat = parts[0].strip()
            try:
                val = float(parts[1].strip())
            except ValueError:
                val = parts[1].strip()
            return {'feature': feat, 'operator': op, 'value': val}
    # Fallback: treat whole string as feature name
    return {'feature': s, 'operator': '>', 'value': 0}


def normalize_conditions(rule):
    """Return rule with conditions normalized to list-of-dicts. Does not mutate original."""
    conds = rule.get('conditions', [])
    if not conds or isinstance(conds[0], dict):
        return rule  # already correct format
    return {**rule, 'conditions': [normalize_condition(c) for c in conds]}


def make_copyable(widget):
    """Add right-click 'Copy' context menu to any tk.Label (or similar widget)."""
    menu = tk.Menu(widget, tearoff=0)

    def _copy():
        try:
            text = widget.cget("text")
            widget.clipboard_clear()
            widget.clipboard_append(text)
        except Exception:
            pass

    menu.add_command(label="Copy", command=_copy)
    widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

# canvas -> mpl connection id; disconnect before re-attaching
_hover_cids = {}


def get_scaled_df():
    """Return loaded_data with profit_scaled and open_dt columns added. None if no data."""
    if state.loaded_data is None:
        return None
    df = state.loaded_data.copy()
    scale = {"Standard": 1.0, "Cent": 0.01, "Micro": 0.1}.get(state.account_type.get(), 1.0)
    if "Profit" in df.columns:
        df["profit_scaled"] = pd.to_numeric(df["Profit"], errors="coerce").fillna(0) * scale
    else:
        df["profit_scaled"] = 0.0
    col0 = df.columns[0]
    df["open_dt"] = pd.to_datetime(df[col0], format="%d/%m/%Y %H:%M", errors="coerce")
    return df


def _make_annot(ax):
    """Create a styled annotation on ax; caller must set visible/text/position."""
    return ax.annotate(
        "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
        bbox=dict(boxstyle="round,pad=0.45", fc="white", ec="#888888",
                  alpha=0.92, linewidth=0.8),
        fontsize=8, zorder=10
    )


def _reconnect(canvas, event_name, handler):
    """Disconnect previous handler (if any) then connect the new one."""
    if canvas in _hover_cids:
        try:
            canvas.mpl_disconnect(_hover_cids[canvas])
        except Exception:
            pass
    _hover_cids[canvas] = canvas.mpl_connect(event_name, handler)


def _dur_to_secs(v):
    try:
        p = str(v).strip().split(':')
        if len(p) == 4:
            return int(p[0])*86400 + int(p[1])*3600 + int(p[2])*60 + int(p[3])
    except Exception:
        pass
    return None


def _dur_col(df):
    for c in ["Duration (DD:HH:MM:SS)", "Duration (DDHHMMSS)"]:
        if c in df.columns:
            return c
    return None

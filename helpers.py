import pandas as pd
import state

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

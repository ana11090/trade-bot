import tkinter as tk
import pandas as pd
import state


def _parse_single_condition(s):
    """Parse a single 'FEATURE OP VALUE' string into a dict.

    Returns None if the string can't be parsed (no operator or non-numeric value).
    Checks 2-char operators before 1-char to avoid 'rsi <= 50' splitting on '<'.
    """
    s = s.strip()
    for op in ['<=', '>=', '==', '!=', '<', '>']:
        if op in s:
            parts = s.split(op, 1)
            if len(parts) != 2:
                continue
            feat = parts[0].strip()
            val_str = parts[1].strip()
            try:
                val = float(val_str)
            except ValueError:
                return None
            return {'feature': feat, 'operator': op, 'value': val}
    return None


def normalize_condition(c):
    """Convert a condition to dict form regardless of source format.

    Handles:
      - dict already: {"feature": ..., "operator": ..., "value": ...} → returned as-is
      - string: "M15_roc_5 <= 0.1920" → single dict
      - compound string: "rsi <= 0.5 and adx > 20" → LIST of dicts

    WHY: Old version used split on the first operator found and then
         tried float() on the rest of the string. For compound
         conditions like "rsi <= 0.5 and adx > 20" the split produced
         parts[1]=" 0.5 and adx > 20" which can't convert to float,
         and the except clause silently stored the garbage string as
         the value. Downstream rule evaluation compared a number to
         a string and either crashed or returned False.
         Fix: detect compound strings (containing ' and ' or ' or ')
         and split into a list of sub-conditions, each parsed
         individually.
    CHANGED: April 2026 — compound condition handling (audit MED #72)
    """
    if isinstance(c, dict):
        return c
    s = str(c).strip()

    # Detect compound conditions (case-insensitive "and"/"or" as a word)
    import re as _re
    if _re.search(r'\s+(?:and|or|AND|OR|And|Or)\s+', s):
        parts = _re.split(r'\s+(?:and|or|AND|OR|And|Or)\s+', s)
        sub_conditions = []
        for part in parts:
            parsed = _parse_single_condition(part)
            if parsed is not None:
                sub_conditions.append(parsed)
            else:
                # Phase 66 Fix 1: skip unparseable sub-parts instead of inserting garbage
                pass
        return sub_conditions

    # Single-condition path
    parsed = _parse_single_condition(s)
    if parsed is not None:
        return parsed

    # WHY (Phase 66 Fix 1): Old code returned a garbage condition dict when
    #      a string couldn't be parsed as FEATURE OP VALUE. The garbage
    #      condition was silently passed to the backtester where `col_vals > 0`
    #      on a non-existent feature name caused KeyErrors or all-False masks.
    #      Return None so callers can detect unparseable conditions explicitly.
    # CHANGED: April 2026 — Phase 66 Fix 1 — return None on parse failure
    #          (audit Part E HIGH #1)
    return None


def normalize_conditions(rule):
    """Return rule with conditions normalized to list-of-dicts. Does not mutate original."""
    conds = rule.get('conditions', [])
    if not conds:
        return rule
    # WHY (Phase 66 Fix 3): Old code checked only conds[0]. A list like
    #      [dict, string, dict] — possible during partial migration —
    #      was returned without normalising the string element.
    #      Check whether ALL conditions are dicts; if so, skip.
    # CHANGED: April 2026 — Phase 66 Fix 3 — iterate type check
    #          (audit Part E HIGH #3)
    if all(isinstance(c, dict) for c in conds):
        return rule  # already fully normalised

    # WHY: normalize_condition may return a dict OR a list (for compound
    #      conditions like "rsi > 50 and adx > 20"). Flatten lists into
    #      the result so every element in 'conditions' is a dict.
    # CHANGED: April 2026 — flatten compound conditions (audit MED #72)
    flat = []
    for c in conds:
        normalized = normalize_condition(c)
        if normalized is None:
            # Phase 66 Fix 1: skip conditions that can't be parsed
            continue
        if isinstance(normalized, list):
            # Filter out any None sub-conditions from compound parsing
            flat.extend(nc for nc in normalized if nc is not None)
        else:
            flat.append(normalized)
    return {**rule, 'conditions': flat}


def make_copyable(widget):
    """Add right-click 'Copy' context menu to any tk.Label (or similar widget).

    WHY: Tkinter has a hard limit on menu objects (~200-500). When many panels
         with many labels are built, we can hit "No more menus can be allocated."
         Catch this gracefully and skip adding the menu rather than crashing.
    CHANGED: April 2026 — guard against menu allocation limit
    """
    try:
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
    except Exception:
        # Hit menu allocation limit or other Tk error - skip this widget
        pass

# canvas -> mpl connection id; disconnect before re-attaching
_hover_cids = {}


def get_scaled_df():
    """Return loaded_data with profit_scaled and open_dt columns added. None if no data."""
    if state.loaded_data is None:
        return None
    df = state.loaded_data.copy()
    # WHY: Micro broker exports report P&L already in account currency — no
    #      further scaling needed. Old 0.1 factor was wrong (100× too small for
    #      the typical $1 pip micro account).
    # CHANGED: April 2026 — Micro scale 1.0 (audit MED — Family #1)
    scale = {"Standard": 1.0, "Cent": 0.01, "Micro": 1.0}.get(state.account_type.get(), 1.0)
    if "Profit" in df.columns:
        df["profit_scaled"] = pd.to_numeric(df["Profit"], errors="coerce").fillna(0) * scale
    else:
        df["profit_scaled"] = 0.0
    # WHY: First column is not always the date column — some exports lead with
    #      "Ticket", "Order", or other non-date fields, causing silent parse errors.
    #      Hardcoded EU format also fails for US broker exports (MM/DD/YYYY).
    # CHANGED: April 2026 — explicit date column + dual EU/US parse (audit MED)
    _date_candidates = ["Open Date", "Open Time", "OpenTime", "Open_Date",
                        "open_date", "open_time", "opentime", "Date", "date"]
    _date_col = next((c for c in _date_candidates if c in df.columns), df.columns[0])
    _dt_eu = pd.to_datetime(df[_date_col], format="%d/%m/%Y %H:%M", errors="coerce")
    _dt_us = pd.to_datetime(df[_date_col], format="%m/%d/%Y %H:%M", errors="coerce")
    df["open_dt"] = _dt_eu if _dt_eu.notna().sum() >= _dt_us.notna().sum() else _dt_us
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

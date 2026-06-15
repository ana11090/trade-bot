# WHY: expose batch EA generation + MT5 run-file emit + report compare in one panel.
# CHANGED: June 2026 — new panel; reuses batch_ea_tools + batch_compare_reports.
# CHANGED: June 2026 — added checkbox rule grid (same interaction as Strategy Refiner)
# CHANGED: June 2026 — grid columns + filters match Strategy Refiner grid
# CHANGED: June 2026 — load via load_strategy_list() so backtest-matrix rows (with stats) appear
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog

from shared.my_rules import load_all as _load_my_rules
try:
    from shared.saved_rules import load_all as _load_saved_rules
except Exception:
    _load_saved_rules = None

# WHY: load_strategy_list() returns backtest-matrix rows (with full stats) merged with saved/my
#   rules — the same source the refiner grid uses. Without this the batch grid only sees raw
#   saved/my_rules which are discovery-only and have no trade stats.
# CHANGED: June 2026 — load via strategy_refiner.load_strategy_list
try:
    from project2_backtesting.strategy_refiner import load_strategy_list as _load_strategy_list
except Exception:
    _load_strategy_list = None

# WHY: reuse the refiner's display formatters so money/win-pass/prop-score formatting
#   matches exactly across panels.
# CHANGED: June 2026 — batch grid parity with refiner grid
try:
    from project2_backtesting.panels.strategy_refiner_panel import (
        _money_for_strategy, _format_win_pass, _format_prop_score, _compute_prop_score,
    )
except Exception:
    _money_for_strategy  = None
    _compute_prop_score  = None
    _format_win_pass     = lambda s: "—"
    _format_prop_score   = lambda s: "—"

BG      = "#f0f2f5"
DARK    = "#1a1a2a"
MIDGREY = "#555566"
WHITE   = "#ffffff"

# CHANGED: June 2026 — checkbox grid state
_log            = None
_grid_tree      = None      # the Treeview
_grid_entries   = []        # strategy dicts (flat, from load_strategy_list)
_batch_sel_iids = set()     # iids (str) currently ticked
# WHY: iid is now r.get('id') or a counter — not a positional index — so we need
#   a direct iid→entry map for _do_generate to look up selected rows safely.
# CHANGED: June 2026 — iid_to_entry avoids int(iid)-as-index assumption
_iid_to_entry   = {}        # iid_str -> strategy dict
# Filter widgets/vars — set in build_panel as module globals, read by _populate_grid.
# WHY: must be module-level (not build_panel locals) so _populate_grid can write the
#   global declaration and read them reliably regardless of call site.
# CHANGED: June 2026 — promoted to module globals; scope fix
_f_stage      = None
_f_tf         = None
_f_dir        = None
_f_mintr      = None
_f_minwr      = None
_f_sort       = None
_f_profitable = None   # BooleanVar — profitable-only filter (parity with refiner)
_nostats_lbl  = None
_cur_source   = "all"  # tracks the active source so filter-widget callbacks don't need src_var
# CHANGED: June 2026 — multi-select exit-strategy filter
_f_exit_vars  = {}     # exit_name -> tk.BooleanVar
_f_exit_all   = None   # tk.BooleanVar for the "All" toggle
_f_exit_menu  = None   # the Menubutton (so we can rebuild its menu when rows change)
_f_exit_menuobj = None # the tk.Menu object itself (NOT _f_exit_menu['menu'], which is a str)


# CHANGED: June 2026 — helper returns checked exits or None for 'all'
# WHY: exit filter logic needs a stable query function; returns None when "All" is checked.
def _selected_exits():
    """Return set of checked exit names, or None if "All" is checked (or nothing is checked)."""
    global _f_exit_all, _f_exit_vars
    if _f_exit_all is not None and _f_exit_all.get():
        return None  # All → no filtering
    picked = {n for n, v in _f_exit_vars.items() if v.get()}
    return picked or None  # If nothing is picked, treat as "All"


# CHANGED: June 2026 — rebuild exit filter menu from current grid rows
# WHY: each source has different exit strategies; rebuild the checkboxes dynamically.
# HOTFIX: June 2026 — use _f_exit_menuobj (the real Menu object), not _f_exit_menu['menu'] (a str)
# FIX: June 2026 — proper checkbuttons with independent BooleanVars; menu stays open on toggle.
def _rebuild_exit_menu(all_rows=None):
    """Scan rows for unique exits, rebuild the Menubutton's menu with checkboxes."""
    global _f_exit_vars, _f_exit_all, _f_exit_menu, _f_exit_menuobj
    # WHY: panel not built yet → menu object doesn't exist; skip safely to prevent blank panel.
    m = _f_exit_menuobj  # the real Menu object, never _f_exit_menu['menu'] (which is a str)
    if m is None:
        return

    # Extract unique exit names from rows (all_rows if provided, else _grid_entries)
    rows = all_rows if all_rows is not None else _grid_entries
    names = sorted({
        (r.get('exit_name') or r.get('exit_class') or
         r.get('exit_strategy') or '').strip()
        for r in rows
        if (r.get('exit_name') or r.get('exit_class') or r.get('exit_strategy'))
    })

    # Clear old menu
    m.delete(0, 'end')

    # "All" as a CHECKBUTTON (keeps menu open, shows a check)
    if _f_exit_all is None:
        _f_exit_all = tk.BooleanVar(value=True)

    def _on_all():
        """When All is toggled, clear individual picks (or re-enable All if nothing else is checked)."""
        if _f_exit_all.get():
            # Turning All on clears individual picks
            for v in _f_exit_vars.values():
                v.set(False)
        else:
            # Don't allow All to be the only thing unchecked with nothing else → re-check it
            if not any(v.get() for v in _f_exit_vars.values()):
                _f_exit_all.set(True)
        _refresh_exit_label()
        _populate_grid(_cur_source)

    m.add_checkbutton(label="All", variable=_f_exit_all, command=_on_all)
    m.add_separator()

    # One CHECKBUTTON per exit
    for nm in names:
        if nm not in _f_exit_vars:
            _f_exit_vars[nm] = tk.BooleanVar(value=False)

        def _on_one(_nm=nm):
            """When a specific exit is toggled, turn All off (or back on if nothing is checked)."""
            if any(v.get() for v in _f_exit_vars.values()):
                _f_exit_all.set(False)  # Picking specific exits turns All off
            else:
                _f_exit_all.set(True)   # Nothing left → back to All
            _refresh_exit_label()
            _populate_grid(_cur_source)

        m.add_checkbutton(label=nm, variable=_f_exit_vars[nm], command=_on_one)

    # Drop stale vars (exits that no longer exist in the current source)
    for stale in [k for k in list(_f_exit_vars) if k not in names]:
        _f_exit_vars.pop(stale, None)

    _refresh_exit_label()


# CHANGED: June 2026 — update Menubutton label to show current selection
# WHY: "All ▾" / "PSAR Only ▾" / "3 exits ▾" tells you what's filtered without opening menu.
def _refresh_exit_label():
    """Update the Menubutton text to reflect current selection."""
    global _f_exit_all, _f_exit_vars, _f_exit_menu
    if _f_exit_menu is None:
        return
    # If "All" is checked, show "All ▾"
    if _f_exit_all is None or _f_exit_all.get():
        _f_exit_menu.config(text="All ▾")
        return
    # Otherwise, show the specific exits that are checked
    picked = [n for n, v in _f_exit_vars.items() if v.get()]
    if not picked:
        _f_exit_menu.config(text="All ▾")
    elif len(picked) == 1:
        # Truncate long names to 14 chars
        _f_exit_menu.config(text=picked[0][:14] + " ▾")
    else:
        _f_exit_menu.config(text="%d exits ▾" % len(picked))


# CHANGED: June 2026 — remember last generate out_dir so 1b/2 default to it
_last_out_dir    = None
# CHANGED: June 2026 — remember run artifacts so "Run Tests" / Compare need no prompts
_last_bat_path   = None
_last_reports_dir = None
# CHANGED: June 2026 — persist the MT5 data dir per session so canonical_batch_dir never re-asks
_last_data_dir   = None


# CHANGED: June 2026 — derive MT5 data dir: folder above the FIRST 'MQL5' segment.
#   Idempotent: works even on doubled paths like ...MQL5\Experts\batch\MQL5\Experts\batch.
def _derive_data_dir(path):
    """Return the MT5 data dir (folder directly above the first 'MQL5' segment).
    If no MQL5 segment exists, treat the path itself as the data dir."""
    if not path:
        return None
    p = os.path.abspath(path)
    parts = p.replace('/', '\\').split('\\')
    for i, seg in enumerate(parts):
        if seg.lower() == 'mql5':
            if i == 0:
                return None
            return '\\'.join(parts[:i])   # everything before the first MQL5
    return p   # no MQL5 found — treat as data dir


# CHANGED: June 2026 — single source of truth for the batch EA folder. All steps (generate,
#   compile, build-run-files, run) call this so the .ini's Expert=batch\name.ex5 always resolves.
#   Self-correcting: collapses any doubled path back to the true data dir before appending once.
def _canonical_batch_dir():
    """Return <data_dir>\\MQL5\\Experts\\batch. Never doubles the path."""
    global _last_data_dir
    # Derive data dir from whatever we know, collapsing any nesting via _derive_data_dir.
    dd = _last_data_dir or (_derive_data_dir(_last_out_dir) if _last_out_dir else None)
    if not dd or not os.path.isdir(dd):
        picked = filedialog.askdirectory(
            title="Pick MT5 DATA folder (the long-hash folder containing MQL5\\Experts)")
        if not picked:
            return None
        # Collapse whatever they picked back to the real data dir (handles picking MQL5 or deeper)
        dd = _derive_data_dir(picked) or picked
    _last_data_dir = dd
    return os.path.join(dd, "MQL5", "Experts", "batch")


# CHANGED: June 2026 — detect a running MT5 terminal before launching the tester bat
def _mt5_is_running():
    try:
        import subprocess as _sp
        out = _sp.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
                      capture_output=True, text=True, timeout=10)
        return "terminal64.exe" in (out.stdout or "")
    except Exception:
        return False  # if we can't tell, don't block


# ── grid helpers ──────────────────────────────────────────────────────────────

# WHY: matrix net/pf/etc. can arrive as str or non-native numeric. The refiner coerces with
#   float() (strategy_refiner_panel.py:6498) — the strict isinstance() gate was rejecting
#   those values, so losing rows never got the ❌/losing tag and "Profitable only" had nothing
#   to filter. Coerce the same way the refiner does.
# CHANGED: June 2026 — tolerant numeric coercion (handles str like "1,234" / "-1234")
def _as_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(',', '').replace('+', '').replace('−', '-')
        try:
            return float(s)
        except ValueError:
            return None
    return None

# CHANGED: June 2026 — stage cell; strategy dicts from load_strategy_list are flat
def _stage_cell(r):
    # WHY: prop_firm_stage lives at the top level of load_strategy_list() dicts (flat).
    #   For saved/my_rules paths it may also be in a nested 'saved_rule' sub-dict.
    v = r.get('prop_firm_stage')
    if v:
        return str(v)
    saved = r.get('saved_rule') or {}
    v = saved.get('prop_firm_stage')
    if v:
        return str(v)
    rs = r.get('run_settings') or {}
    v = rs.get('prop_firm_stage')
    return str(v) if v else "—"


def _rule_label(r):
    # WHY: strategy dicts from load_strategy_list() carry rule_combo at the top level;
    #   saved rows may use rule_label, name, or id. Tolerant — never crashes.
    # CHANGED: June 2026 — wider fallback chain; saved rows vary
    return (r.get('rule_combo') or r.get('rule') or r.get('rule_label') or
            r.get('label') or r.get('name') or str(r.get('id', '—')))


def _populate_grid(source):
    # WHY: reload the grid from load_strategy_list() (same merged source the refiner uses),
    #   filter by source tag, apply UI filters, sort, then insert.
    # CHANGED: June 2026 — load_strategy_list + filter by source tag + refiner-parity columns
    # CHANGED: June 2026 — defensive per-row try/except so one bad row can't blank the grid;
    #   stats sub-dict fallback for saved rows; non-empty fallback when source yields 0 rows
    # CHANGED: June 2026 — scope fix: declare filter globals explicitly so _passes always sees
    #   the live widget values; filter applied as list comprehension before insert loop
    # CHANGED: June 2026 — added exit filter globals
    global _grid_entries, _batch_sel_iids, _iid_to_entry, _cur_source
    global _f_stage, _f_tf, _f_dir, _f_mintr, _f_minwr, _f_sort, _f_profitable
    global _f_exit_vars, _f_exit_all, _f_exit_menu, _f_exit_menuobj
    if _grid_tree is None:
        return
    _cur_source = source
    for _i in _grid_tree.get_children():
        _grid_tree.delete(_i)
    _batch_sel_iids = set()
    _iid_to_entry = {}

    # ── diagnostic: confirm import worked and how many rows we have ──
    print("[BATCH-GRID] _load_strategy_list =", _load_strategy_list,
          " loaded =", (len(_load_strategy_list()) if _load_strategy_list else 'n/a'),
          flush=True)

    # ── load merged strategy list ──
    if _load_strategy_list is not None:
        _all = _load_strategy_list() or []
    elif _load_saved_rules:
        _all = _load_saved_rules() or []
    else:
        _all = []

    # WHY: separator rows are section dividers with no id/rule/stats — they crash the insert
    #   loop if they reach it. Strip them unconditionally before any further processing.
    _all = [r for r in _all if r.get('source') != 'separator']

    # Filter by the source dropdown.
    # Source tags set by load_strategy_list: 'backtest', 'optimizer', 'saved', 'my_rules'.
    # WHY: 'saved_rules' catches everything that isn't backtest/optimizer (source values from
    #   saved entries vary: 'saved', 'my_rules', '?', or sometimes absent).
    if source == 'backtest':
        _grid_entries = [r for r in _all if r.get('source') == 'backtest']
    elif source == 'optimizer':
        _grid_entries = [r for r in _all if r.get('source') == 'optimizer']
    elif source == 'saved_rules':
        _grid_entries = [r for r in _all
                         if r.get('source') not in ('backtest', 'optimizer')]
    elif source == 'my_rules':
        _grid_entries = [r for r in _all if r.get('source') == 'my_rules']
    else:  # 'all'
        _grid_entries = list(_all)
        # WHY: load_strategy_list() can return the same rule id under multiple sources
        #   (e.g. backtest copy id=41 AND its saved copy id=41). De-dupe by id, keeping
        #   the copy with the best source (backtest > optimizer > saved > my_rules) so
        #   the row with real stats wins and 'all' always has ≥ rows as any single source.
        # CHANGED: June 2026 — de-dupe 'all' to prevent iid collision and show each rule once
        _src_rank = {'backtest': 0, 'optimizer': 1, 'saved': 2, 'my_rules': 3}
        _best = {}
        for _r in _grid_entries:
            _rid = str(_r.get('id', id(_r)))
            _cur = _best.get(_rid)
            if (_cur is None or
                    _src_rank.get(_r.get('source'), 9) < _src_rank.get(_cur.get('source'), 9)):
                _best[_rid] = _r
        _grid_entries = list(_best.values())

    # WHY: if the chosen source is empty (e.g. backtest_matrix.json is a Git LFS pointer /
    #   not pulled), fall back to 'all' so the page is never blank when data exists elsewhere.
    # CHANGED: June 2026 — non-empty fallback
    if not _grid_entries and _all:
        print("[BATCH-GRID] source '%s' empty — falling back to all" % source, flush=True)
        _grid_entries = list(_all)

    # CHANGED: June 2026 — rebuild exit filter menu based on current rows (before filtering)
    # WHY: each source has different exit strategies; dynamically populate the checkboxes.
    # HOTFIX: June 2026 — wrap in try/except so exit-menu issues can't blank the whole panel.
    try:
        _rebuild_exit_menu()
    except Exception as _e:
        print("[BATCH-GRID] exit menu rebuild skipped:", _e, flush=True)

    # ── helpers used by filter and sort ──
    def _val(var, default=""):
        # Read a Tk variable/widget safely; return default if None or widget destroyed.
        try:
            return var.get() if var is not None else default
        except Exception:
            return default

    def _num(r, *keys):
        # CHANGED: June 2026 — coerce via _as_num (str/non-native ok), matches refiner behaviour
        stats = r.get('stats') or {}
        for k in keys:
            raw = r.get(k) if r.get(k) is not None else stats.get(k)
            val = _as_num(raw)
            if val is not None:
                return val
        return None

    # ── filter predicate — applied to _grid_entries before the insert loop ──
    # WHY: filtering the list before insert (not per-row in the loop) means the
    #   insert loop never skips rows mid-flight, making _shown count accurate and
    #   the iid mapping stable.
    # CHANGED: June 2026 — filter applied as list comprehension; _val() guards None vars
    # CHANGED: June 2026 — added exit-strategy filter (multi-select)
    def _passes(r):
        st = _stage_cell(r)
        tf = r.get('entry_tf') or r.get('entry_timeframe') or '—'
        dr = r.get('direction') or r.get('dir') or '—'
        # CHANGED: June 2026 — strip whitespace from exit name for consistent matching
        exit_ = (r.get('exit_name') or r.get('exit_class') or
                 r.get('exit_strategy') or '').strip()
        tr = _num(r, 'total_trades') or 0
        wr = _num(r, 'win_rate') or 0
        net = _num(r, 'net_pips', 'net_total_pips', 'total_pips')

        if _val(_f_stage, "All") not in ("All", "", st):
            return False
        if _val(_f_tf, "All") not in ("All", "", tf):
            return False
        if _val(_f_dir, "All") not in ("All", "", dr):
            return False
        # CHANGED: June 2026 — exit filter: if specific exits are checked, hide others
        # WHY: _selected_exits() returns None when "All" is on, set of names otherwise.
        _sel_exits = _selected_exits()
        if _sel_exits is not None and exit_ and exit_ not in _sel_exits:
            return False
        try:
            if _val(_f_mintr) and tr < float(_val(_f_mintr)):
                return False
            if _val(_f_minwr) and wr < float(_val(_f_minwr)):
                return False
        except ValueError:
            pass
        # CHANGED: June 2026 — profitable-only filter; matches refiner: hides rules that HAVE
        #   data and are ≤ 0; keeps no-data rows visible (same guard as refiner uses)
        if _val(_f_profitable, False):
            if net is not None and net <= 0:
                return False
        return True

    _grid_entries = [r for r in _grid_entries if _passes(r)]

    # ── sort ──
    # CHANGED: June 2026 — sort after filter; field names match matrix rows (flat, top-level)
    _sort_choice = _val(_f_sort, "Net Pips")
    _sort_map = {
        "Prop Score": "prop_score",
        "Net Pips":   "net_total_pips",
        "Win Rate":   "win_rate",
        "PF":         "net_profit_factor",
        "Trades":     "total_trades",
    }
    _sk = _sort_map.get(_sort_choice, "net_total_pips")

    def _sort_key(r):
        if _sort_choice == "Prop Score" and _compute_prop_score is not None:
            try:
                score, _ = _compute_prop_score(r)
                return score or 0
            except Exception:
                return 0
        # CHANGED: June 2026 — _num already coerces via _as_num; string vals sort correctly
        return _num(r, _sk) or 0.0

    _grid_entries.sort(key=_sort_key, reverse=True)

    # ── insert rows ──
    # WHY: strategy dicts from load_strategy_list can be flat (backtest rows) or have a
    #   'stats' sub-dict (some saved rows). Read flat first, fall back to sub-dict.
    #   Each row is wrapped in try/except so one bad entry can never blank the whole grid.
    # CHANGED: June 2026 — per-row guard; iid = id/counter stored in _iid_to_entry
    # CHANGED: June 2026 — safe number formatter (_fmt) replaces inline :+,.Nf specs which
    #   crash when Python sees the ',' flag combined with '+' in some value paths.
    def _fmt(v, dec=1, sign=False, comma=False):
        if not isinstance(v, (int, float)):
            return "—"
        try:
            s = ("%.{d}f".replace("{d}", str(dec)) % v) if not comma else (
                "{:,.{d}f}".format(v, d=dec))
            if sign and v >= 0:
                s = "+" + s
            elif sign:
                pass  # negative sign already in s
            return s
        except (ValueError, TypeError):
            return "—"

    _no_stats = 0
    _shown = 0
    for r in _grid_entries:
        # separators were stripped earlier; this is a belt-and-braces guard only
        if r.get('source') == 'separator':
            continue
        try:
            stats  = r.get('stats') or {}
            # CHANGED: June 2026 — coerce all numeric fields via _as_num so string values
            #   (e.g. "1,234" stored in matrix JSON) don't produce None and blank the cells.
            trades = _as_num(r.get('total_trades') if r.get('total_trades') is not None else stats.get('total_trades'))
            wr     = _as_num(r.get('win_rate')     if r.get('win_rate')     is not None else stats.get('win_rate'))
            pf     = _as_num(r.get('net_profit_factor') or r.get('profit_factor') or
                             stats.get('net_profit_factor') or stats.get('profit_factor'))
            net    = _as_num(r.get('net_total_pips') or r.get('net_pips') or
                             stats.get('net_total_pips') or stats.get('net_pips'))
            avg    = _as_num(r.get('net_avg_pips') or r.get('avg_pips') or
                             stats.get('net_avg_pips') or stats.get('avg_pips'))
            tf     = (r.get('entry_tf') or r.get('entry_timeframe') or
                      stats.get('entry_tf') or '—')
            exit_  = (r.get('exit_name') or r.get('exit_class') or
                      r.get('exit_strategy') or stats.get('exit_name') or '?')
            direction = r.get('direction') or r.get('dir') or '—'
            try:
                off = "N" if int(r.get('entry_bar_offset', 0) or 0) == 0 else "N+1"
            except (TypeError, ValueError):
                off = "N"

            has_stats = trades is not None
            if not has_stats:
                _no_stats += 1

            # CHANGED: June 2026 — coerce net (matches refiner net>0 definition); net_total_pips
            #   listed first (same priority as refiner line 6571). _as_num handles string values.
            _nv = None
            for _nk in ('net_total_pips', 'net_pips', 'total_pips'):
                _nv = _as_num(stats.get(_nk) if stats.get(_nk) is not None else r.get(_nk))
                if _nv is not None:
                    break
            if not has_stats or _nv is None:
                profit_cell = "—";  profit_tag = "neutral"
            elif _nv > 0:
                profit_cell = "✅"; profit_tag = "profitable"
            else:
                profit_cell = "❌"; profit_tag = "losing"

            if has_stats and _money_for_strategy is not None:
                _nd, _np, _ad, _ap = _money_for_strategy(r, net or 0, avg or 0)
                net_d = ("$" + _fmt(_nd, 0, sign=True, comma=True)) if _nd is not None else "—"
                net_p = (_fmt(_np, 1, sign=True) + "%")             if _np is not None else "—"
                avg_d = ("$" + _fmt(_ad, 2, sign=True, comma=True)) if _ad is not None else "—"
                avg_p = (_fmt(_ap, 2, sign=True) + "%")             if _ap is not None else "—"
            else:
                net_d = net_p = avg_d = avg_p = "—"

            vals = (
                "☐",
                r.get('id', r.get('index', _shown)),
                _stage_cell(r),
                str(_rule_label(r))[:60],
                exit_, tf, direction,
                (int(trades) if has_stats else "—"),
                (_fmt(wr, 1) + "%") if (has_stats and wr is not None) else "—",
                _fmt(pf, 2)         if (has_stats and pf is not None) else "—",
                _fmt(net, 0, sign=True, comma=True) if (has_stats and net is not None) else "—",
                net_d, net_p,
                _fmt(avg, 1, sign=True) if (has_stats and avg is not None) else "—",
                avg_d, avg_p,
                profit_cell,
                (_format_win_pass(r)   if has_stats else "—"),
                (_format_prop_score(r) if has_stats else "—"),
                off,
            )
            # WHY: the same rule id appears under multiple sources (backtest + saved copy).
            #   Treeview.insert raises TclError on a duplicate iid — swallowed by the except
            #   and silently dropped rows, making 'all' < 'backtest'. Unique iid per row.
            # CHANGED: June 2026 — unique iid = source::id::counter (no TclError collisions)
            _src = str(r.get('source', 'x'))
            _rid = str(r.get('id', r.get('index', _shown)))
            _iid = "%s::%s::%d" % (_src, _rid, _shown)
            _iid_to_entry[_iid] = r
            _grid_tree.insert('', 'end', iid=_iid, values=vals, tags=(profit_tag,))
            _shown += 1
        except Exception as _row_err:
            print("[BATCH-GRID] skipped a row:", repr(_row_err), flush=True)
            continue

    print("[BATCH-GRID] source=%s shown=%d no_stats=%d" % (source, _shown, _no_stats),
          flush=True)
    _grid_tree.heading("sel", text="☐", command=_toggle_all)

    # ── caveat label ──
    if _nostats_lbl is not None:
        if _no_stats:
            _nostats_lbl.config(
                text="(%d of %d rules have no backtest data — re-run backtest to fill stats)"
                     % (_no_stats, _shown))
        else:
            _nostats_lbl.config(text="")


def _toggle_row(event):
    # WHY: column-#1 click toggles one row's tick, matching the Refiner pattern.
    if _grid_tree is None:
        return
    if _grid_tree.identify_region(event.x, event.y) != 'cell':
        return
    if _grid_tree.identify_column(event.x) != '#1':
        return
    iid = _grid_tree.identify_row(event.y)
    if not iid:
        return
    if iid in _batch_sel_iids:
        _batch_sel_iids.discard(iid)
        glyph = "☐"
    else:
        _batch_sel_iids.add(iid)
        glyph = "☑"
    vals = list(_grid_tree.item(iid, 'values'))
    if vals:
        vals[0] = glyph
        _grid_tree.item(iid, values=vals)
    return "break"


def _toggle_all():
    # WHY: header click selects or deselects every visible row at once.
    if _grid_tree is None:
        return
    kids = list(_grid_tree.get_children())
    all_on = bool(kids) and all(k in _batch_sel_iids for k in kids)
    glyph = "☐" if all_on else "☑"
    for k in kids:
        if all_on:
            _batch_sel_iids.discard(k)
        else:
            _batch_sel_iids.add(k)
        vals = list(_grid_tree.item(k, 'values'))
        if vals:
            vals[0] = glyph
            _grid_tree.item(k, values=vals)
    _grid_tree.heading("sel", text=("☑" if not all_on else "☐"), command=_toggle_all)


# ── log helper ────────────────────────────────────────────────────────────────

def _append(msg):
    # WHY: thread-safe append; marshal via after() since Tk is single-threaded.
    if _log is None:
        return
    def _do():
        _log.configure(state="normal")
        _log.insert("end", msg + "\n")
        _log.see("end")
        _log.configure(state="disabled")
    _log.after(0, _do)


def _run_bg(fn):
    # WHY: keep the UI responsive during slow operations.
    threading.Thread(target=fn, daemon=True).start()


# ── button callbacks ──────────────────────────────────────────────────────────

# CHANGED: June 2026 — synchronous step functions for "Run All" chain
# WHY: each step returns True/False so the chain can stop on failure.
def _step_generate(source_var):
    """Synchronous: returns True on success, False on error."""
    try:
        from project3_live_trading.batch_ea_tools import batch_generate
        _selected = [_iid_to_entry[i] for i in _batch_sel_iids if i in _iid_to_entry]
        if not _selected:
            _append("Tick at least one rule in the grid (or the header ☐ to select all).")
            return False
        out_dir = _canonical_batch_dir()
        if not out_dir:
            _append("Generate cancelled (no MT5 data folder).")
            return False
        os.makedirs(out_dir, exist_ok=True)
        global _last_out_dir
        _last_out_dir = out_dir
        _nested = os.path.join(out_dir, "batch")
        if os.path.isdir(_nested):
            _append("NOTE: stale folder found: %s — delete it so old EAs don't confuse the "
                    "tester. Only files directly in %s are used." % (_nested, out_dir))
        src = source_var.get()
        _append("Generating %d EA(s) into %s ..." % (len(_selected), out_dir))
        results = batch_generate(out_dir, source=src, entries=_selected)
        ok = sum(1 for r in results if r.get("ok"))
        _append("Done: %d/%d EAs written." % (ok, len(results)))
        for r in results:
            if not r.get("ok"):
                _append("  FAILED %s: %s" % (r.get("name"), r.get("error")))
        _append("Manifest: %s" % os.path.join(out_dir, "batch_manifest.json"))
        return ok > 0
    except Exception as e:
        _append("ERROR during generate: %s" % e)
        return False

def _do_generate(source_var):
    _run_bg(lambda: _step_generate(source_var))


def _step_compile():
    """Synchronous: returns True on success, False on error."""
    try:
        from project3_live_trading.batch_ea_tools import (
            batch_compile, get_saved_metaeditor_path,
            save_metaeditor_path, _find_metaeditor,
        )
        global _last_out_dir
        out_dir = _canonical_batch_dir()
        if not out_dir:
            _append("Compile cancelled (no MT5 data folder known).")
            return False
        _last_out_dir = out_dir
        data_dir = _derive_data_dir(out_dir)
        if not data_dir:
            _append("Compile failed: couldn't derive MT5 data dir from %s" % out_dir)
            return False
        _append("Compiling from: %s" % out_dir)

        me = get_saved_metaeditor_path() or _find_metaeditor()
        if not me:
            _append("Compile failed: metaeditor64.exe not found. Run '1b. Compile EAs' manually once to set path.")
            return False

        _append("Compiling EAs with metaeditor64 (headless) ...")
        results = batch_compile(out_dir, data_dir, metaeditor_path=me)
        ok = sum(1 for r in results if r.get("ok"))
        _append("Compiled %d/%d EA(s) to .ex5." % (ok, len(results)))
        for r in results:
            if not r.get("ok"):
                _append("  FAILED %s: %s" % (r.get("name"), r.get("error")))
        if any("lock" in (r.get("error") or "").lower() or
               "WinError 32" in (r.get("error") or "") for r in results):
            _append("Some files were LOCKED. Close MT5 terminal and try again.")
            return False
        return ok == len(results) and ok > 0
    except Exception as e:
        _append("ERROR during compile: %s" % e)
        return False


def _step_build_run_files():
    """Synchronous: returns True on success, False on error."""
    try:
        from project3_live_trading.batch_ea_tools import emit_tester_inis, resolve_terminal_path
        global _last_out_dir, _last_bat_path, _last_reports_dir
        batch_dir = _canonical_batch_dir()
        if not batch_dir:
            _append("Build run files cancelled (no MT5 data folder known).")
            return False
        _last_out_dir = batch_dir
        manifest = os.path.join(batch_dir, "batch_manifest.json")
        if not os.path.isfile(manifest):
            _append("No manifest — run '1. Generate EAs' first.")
            return False
        data_dir = _derive_data_dir(batch_dir)
        if not data_dir:
            _append("Build failed: couldn't derive MT5 data dir")
            return False
        reports_dir = os.path.join(batch_dir, "reports")
        term = resolve_terminal_path()
        if not term:
            _append("Build failed: terminal64.exe not found. Run '2. Build Run Files' manually once to set path.")
            return False
        emit_tester_inis(manifest, data_dir, "Experts\\batch", reports_dir, terminal_exe=term)
        _last_bat_path = os.path.join(batch_dir, "run_all_tests.bat")
        _last_reports_dir = reports_dir
        _append("Run files written.")
        return True
    except Exception as e:
        _append("ERROR during build: %s" % e)
        return False


def _step_run_tests():
    """Synchronous: BLOCKS until bat completes. Returns True on success."""
    try:
        import subprocess, threading, time, glob as _glob, json as _json
        global _last_bat_path, _last_reports_dir
        bat = _last_bat_path
        if not bat or not os.path.isfile(bat):
            _append("Run Tests failed: no .bat (run '2. Build Run Files' first).")
            return False
        if _mt5_is_running():
            _append("MT5 is OPEN — close the terminal first.")
            return False

        _total_eas = 0
        try:
            _man = os.path.join(os.path.dirname(bat), "batch_manifest.json")
            if os.path.isfile(_man):
                with open(_man, encoding="utf-8") as _f:
                    _total_eas = len(_json.load(_f))
        except Exception:
            pass

        if _last_reports_dir and os.path.isdir(_last_reports_dir):
            _cleared = 0
            # CHANGED: June 2026 — also clear .xlsx reports (tester now writes xlsx)
            for _f in (_glob.glob(os.path.join(_last_reports_dir, "*.xlsx")) +
                       _glob.glob(os.path.join(_last_reports_dir, "*.htm")) +
                       _glob.glob(os.path.join(_last_reports_dir, "*.html"))):
                try:
                    os.remove(_f)
                    _cleared += 1
                except Exception:
                    pass

        _append("Running %d EA(s) via %s ..." % (_total_eas, os.path.basename(bat)))
        proc = subprocess.Popen(["cmd", "/c", bat], cwd=os.path.dirname(bat),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)

        _seen = {"n": 0}
        def _pump():
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                if "=== RUNNING" in line:
                    _seen["n"] += 1
                    if _total_eas:
                        line = line.replace("=== RUNNING",
                                            "=== RUNNING [%d/%d]" % (_seen["n"], _total_eas))
                _append("  " + line)
        threading.Thread(target=_pump, daemon=True).start()

        while proc.poll() is None:
            time.sleep(5)
            # CHANGED: June 2026 — count .xlsx reports (full deal data) instead of .htm (summary only)
            # WHY: tester now writes .xlsx with complete trade details.
            done_n = (len(_glob.glob(os.path.join(_last_reports_dir, "*.xlsx"))) +
                      len(_glob.glob(os.path.join(_last_reports_dir, "*.htm"))) +
                      len(_glob.glob(os.path.join(_last_reports_dir, "*.html"))))             if (_last_reports_dir and os.path.isdir(_last_reports_dir)) else 0
            _prog = "%d/%d done" % (done_n, _total_eas) if _total_eas else "%d done" % done_n
            _append("  [heartbeat] %s" % _prog)

        _append("All tester passes finished." if proc.returncode == 0 else
                "Tester exited code %d" % proc.returncode)
        return proc.returncode == 0
    except Exception as e:
        _append("ERROR during run: %s" % e)
        return False


def _step_compare():
    """Synchronous: returns True on success."""
    try:
        from project3_live_trading.batch_compare_reports import compare_reports
        global _last_reports_dir, _last_out_dir
        reports = (_last_reports_dir if (_last_reports_dir and os.path.isdir(_last_reports_dir))
                   else None)
        if not reports:
            bdir = _last_out_dir or _canonical_batch_dir()
            if bdir:
                cand = os.path.join(bdir, "reports")
                if os.path.isdir(cand):
                    reports = cand
        if not reports:
            _append("No reports folder — run tests first.")
            return False
        manifest = ""
        bdir = _last_out_dir or _canonical_batch_dir()
        if bdir:
            m = os.path.join(bdir, "batch_manifest.json")
            if os.path.isfile(m):
                manifest = m
        _append("Comparing (%d reports)..." % len(os.listdir(reports)))
        rows = compare_reports(reports, "", manifest)
        for line in rows:
            _append(line)
        return True
    except Exception as e:
        _append("ERROR during compare: %s" % e)
        return False


def _write_debug_dump():
    """Write MT5 + Python trades + comparison to debug_dump folder."""
    import json, glob, datetime, csv as _csv, shutil, io as _io
    try:
        from project3_live_trading.batch_compare_reports import (
            _load_py_trades_for, _py_rules_dir, _parse_mt5_xlsx, _parse_mt5_html,
            _find_report, _parse_mt5_html_stats, _read_mt5_entries)
    except Exception:
        _append("Debug dump skipped (import failed).")
        return
    reports = _last_reports_dir
    bdir = _last_out_dir or _canonical_batch_dir()
    if not reports or not bdir:
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dump = os.path.join(bdir, "debug_dump_%s" % stamp)
    os.makedirs(dump, exist_ok=True)

    # CHANGED: June 2026 — write comprehensive debug log to file for diagnostics
    # WHY: in-app _append may scroll off; file captures exact state for analysis.
    # HOTFIX: June 2026 — guard open + safe _d so a missing/failing open never crashes the dump.
    _dbg_path = os.path.join(dump, "_DEBUG.txt")
    _dbg = None
    try:
        _dbg = open(_dbg_path, "w", encoding="utf-8")
    except Exception:
        pass
    def _d(msg):
        try:
            if _dbg is not None:
                _dbg.write(msg + "\n"); _dbg.flush()
        except Exception:
            pass
        _append(msg)

    man = {}
    mpath = os.path.join(bdir, "batch_manifest.json")
    if os.path.isfile(mpath):
        with open(mpath, encoding="utf-8") as f:
            for rec in json.load(f):
                man[rec.get("name")] = rec

    # CHANGED: June 2026 — iterate over actual report files, parse directly
    # HOTFIX: June 2026 — also include .xlsx.htm files from old buggy runs
    # WHY: we already have the report path, so parse it directly instead of re-finding.
    report_files = (glob.glob(os.path.join(reports, "*.xlsx.htm")) +
                    glob.glob(os.path.join(reports, "*.xlsx")) +
                    glob.glob(os.path.join(reports, "*.htm")) +
                    glob.glob(os.path.join(reports, "*.html")))

    # DEBUG: comprehensive logging to _DEBUG.txt
    _d("reports dir: %s" % reports)
    _d("files found: %s" % ([os.path.basename(p) for p in report_files] or "NONE"))
    for p in report_files:
        _d("  %s  (%d bytes, ext=%s)" %
           (os.path.basename(p), os.path.getsize(p), os.path.splitext(p)[1]))

    # CHANGED: June 2026 — consolidated single-file output (batch_debug.json) instead of N subfolders
    # WHY: ~20 EAs × 4 files = 80+ files blows past the 100-file upload limit per conversation.
    WRITE_PER_EA_FILES = False  # set True only when you need raw per-EA CSVs for deep dives
    batch = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "reports_dir": reports,
        "window": {"from": "2026-01-01", "to": "2026-04-08"},
        "eas": [],
    }

    for rpt in sorted(report_files):
        # CHANGED: June 2026 — EA name is the filename WITHOUT extension(s)
        # WHY: MT5 can create double extensions like ".xlsx.htm" if Report= had .xlsx in it.
        #   Single splitext turns "name.xlsx.htm" → "name.xlsx" (wrong).
        #   Strip up to 3 extensions to handle any multi-dot cases.
        base = os.path.basename(rpt)
        for _ in range(3):
            base, ext = os.path.splitext(base)
            if not ext:
                break
        ea = base

        sub = os.path.join(dump, ea)  # only created if WRITE_PER_EA_FILES

        # DEBUG: log detailed parse results for each report
        _d("---- %s ----" % os.path.basename(rpt))
        _d("  EA name (stripped): %s" % ea)

        # CHANGED: June 2026 — parse the report we already have (don't re-find by name)
        # WHY: we're iterating over actual files, so parse them directly.
        # CHANGED: June 2026 — fallback to proven entry reader if full parser returns 0
        # WHY: compare_reports uses _read_mt5_entries (proven: got 308), so if _parse_mt5_xlsx
        #   returns 0, fall back to entry-only rows to match compare's count.
        mt5_trades = []
        mt5_stats = {}

        # Test proven entry reader first
        try:
            entries = _read_mt5_entries(rpt)
            _d("  _read_mt5_entries: %d entries" % len(entries))
        except Exception as e:
            _d("  _read_mt5_entries FAILED: %r" % e)
            entries = []

        # Test full parser
        try:
            if rpt.lower().endswith(".xlsx"):
                mt5_trades, mt5_stats = _parse_mt5_xlsx(rpt)
                _d("  _parse_mt5_xlsx: %d trades, stats=%s" %
                   (len(mt5_trades), {k: v for k, v in mt5_stats.items() if v}))

                # If full parser got 0 but proven reader found entries, use entry-only rows
                if not mt5_trades and entries:
                    mt5_trades = [{"entry_time": e.strftime("%Y-%m-%d %H:%M:%S") if hasattr(e, 'strftime') else str(e)}
                                  for e in entries]
                    _d("  → FALLBACK to entry reader: %d entry-only rows" % len(mt5_trades))
            elif rpt.lower().endswith((".htm", ".html")):
                # CHANGED: June 2026 — parse HTML reports FULLY (trades + stats)
                # WHY: detailed HTML reports (635KB+) contain full deals table with in/out pairs
                mt5_trades, mt5_stats = _parse_mt5_html(rpt)
                _d("  _parse_mt5_html: %d trades, stats=%s" %
                   (len(mt5_trades), {k: v for k, v in mt5_stats.items() if v}))
                if not mt5_stats.get("bars") or not mt5_stats.get("initial_deposit"):
                    _d("  WARNING: stats incomplete (bars=%r deposit=%r) — report may be short/summary-only: %s (%d bytes)"
                       % (mt5_stats.get("bars"), mt5_stats.get("initial_deposit"),
                          os.path.basename(rpt), os.path.getsize(rpt)))
        except Exception as e:
            _d("  parse FAILED: %r" % e)
            print("[DEBUG-DUMP] %s parse failed: %s" % (ea, e), flush=True)

        if WRITE_PER_EA_FILES:
            os.makedirs(sub, exist_ok=True)
            with open(os.path.join(sub, "mt5_trades.csv"), "w", newline="", encoding="utf-8") as f:
                if mt5_trades:
                    w = _csv.DictWriter(f, fieldnames=list(mt5_trades[0].keys()))
                    w.writeheader()
                    w.writerows(mt5_trades)

        # CHANGED: June 2026 — hex-based fallback for manifest matching
        # WHY: if EA name differs slightly from manifest key, match by combo hex ID.
        rec = man.get(ea)
        if rec is None:
            import re as _re
            m = _re.search(r"[0-9a-f]{8}", ea.lower())
            hexid = m.group(0) if m else None
            if hexid:
                for k, v in man.items():
                    if hexid in k.lower() or hexid in str(v.get("rule_combo", "")).lower():
                        rec = v
                        break
        # CHANGED: June 2026 — when manifest record still missing after hex match, derive
        #   combo/exit/tf from EA filename so summary.txt shows config not '?'.
        if rec is None:
            _toks = ea.split('_')
            _tf_tokens = {'M5', 'M15', 'H1', 'H4', 'D1'}
            _tf_idx = next((i for i, t in enumerate(_toks) if t in _tf_tokens and i >= 2), None)
            if _tf_idx is not None:
                _combo = '_'.join(_toks[:_tf_idx])
                _tf    = _toks[_tf_idx]
                _exit  = '_'.join(_toks[_tf_idx + 1:])
                rec = {"rule_combo": _combo, "entry_tf": _tf, "exit_name": _exit}
                _d("  rec fallback from filename: combo=%s tf=%s exit=%s" % (_combo, _tf, _exit))
            else:
                _d("  rec fallback FAILED: no TF token in '%s'" % ea)
        rec = rec or {}

        py, meta = (_load_py_trades_for(rec.get("rule_combo", ""), rec.get("exit_name", ""),
                                        rec.get("entry_tf", "")) if rec else (None, None))
        # Build flattened trade list for both JSON and optional CSV (rename _edbg to avoid
        # shadowing the outer _dbg file-handle variable)
        _flat_py = []
        if py:
            for _t in py:
                _row = dict(_t)
                _edbg = _row.pop("entry_debug", None)
                _row.pop("entry_indicators", None)
                if isinstance(_edbg, dict):
                    for _feat, _info in _edbg.items():
                        _sf = _feat.replace(' ', '_').replace('-', '_')
                        if isinstance(_info, dict):
                            _row["ind_" + _sf + "_val"] = _info.get('value')
                            _row["ind_" + _sf + "_ts"]  = _info.get('entry_row_ts')
                        else:
                            _row["ind_" + _sf] = _info
                _flat_py.append(_row)
        if WRITE_PER_EA_FILES:
            with open(os.path.join(sub, "python_trades.csv"), "w", newline="", encoding="utf-8") as f:
                if _flat_py:
                    _all_keys = list(dict.fromkeys(k for _r in _flat_py for k in _r.keys()))
                    w = _csv.DictWriter(f, fieldnames=_all_keys, extrasaction='ignore')
                    w.writeheader()
                    w.writerows(_flat_py)

        # CHANGED: June 2026 — Part 3: join Python entry_debug with EA entrylog → entry_compare.csv
        # WHY: entry_bar_open (MT5 signal bar) vs entry_row_ts (Python signal bar) must match;
        #   BAR_MISMATCH means TF bar alignment bug; VALUE_DIFF with same TS = calc diff.
        _data_dir = _derive_data_dir(_last_out_dir) if _last_out_dir else None
        _elog_candidates = sorted(
            glob.glob(os.path.join(_data_dir or '', 'Tester', '*', 'Agent-*', 'MQL5', 'Files', 'entrylog_*.csv')),
            key=os.path.getmtime, reverse=True
        ) if _data_dir else []
        _elog_path = _elog_candidates[0] if _elog_candidates else None
        _d("  entry_compare: %s" % (_elog_path or "no entrylog_*.csv in Tester sandbox"))
        _mt5_elog = {}  # {(entry_bar_open[:16], feature): {value, bar_ts}}
        if _elog_path:
            try:
                with open(_elog_path, encoding='cp1252', errors='replace') as _ef:
                    for _erow in _csv.DictReader(_ef):
                        _ekey = (_erow.get('entry_bar_open', '')[:16], _erow.get('feature', ''))
                        _mt5_elog[_ekey] = {
                            'value': _erow.get('value', ''),
                            'bar_ts': _erow.get('bar_ts', ''),
                        }
            except Exception as _ee:
                _d("  entry_compare: entrylog load error: %r" % _ee)
        _cmp_rows = []
        for _t in (py or []):
            _edbg = _t.get('entry_debug') or {}
            _etime = str(_t.get('entry_time', ''))[:16]
            for _feat, _info in _edbg.items():
                if not isinstance(_info, dict):
                    continue
                _py_val = _info.get('value')
                _py_ts  = str(_info.get('entry_row_ts', ''))[:16]
                _mt5    = _mt5_elog.get((_py_ts, _feat), {})
                _mt5_val = _mt5.get('value', '')
                _mt5_ts  = _mt5.get('bar_ts', '')[:16]
                _note = ''
                if _mt5:
                    if _py_ts and _mt5_ts and _py_ts != _mt5_ts:
                        _note = 'BAR_MISMATCH'
                    elif _py_val is not None and _mt5_val:
                        try:
                            if abs(float(_py_val) - float(_mt5_val)) > 1e-4:
                                _note = 'VALUE_DIFF'
                        except Exception:
                            pass
                else:
                    _note = 'MT5_MISSING'
                _cmp_rows.append({
                    'entry_time': _etime,
                    'feature': _feat,
                    'py_value': _py_val,
                    'py_src_ts': _py_ts,
                    'mt5_value': _mt5_val,
                    'mt5_bar_ts': _mt5_ts,
                    'note': _note,
                })
        _d("  entry_compare: %d rows" % len(_cmp_rows))
        if WRITE_PER_EA_FILES:
            _cmp_path = os.path.join(sub, "entry_compare.csv")
            with open(_cmp_path, 'w', newline='', encoding='utf-8') as _ef:
                _cw = _csv.DictWriter(
                    _ef,
                    fieldnames=['entry_time', 'feature', 'py_value', 'py_src_ts', 'mt5_value', 'mt5_bar_ts', 'note']
                )
                _cw.writeheader()
                _cw.writerows(_cmp_rows)

        # CHANGED: June 2026 — enriched summary with profitability + date ranges both sides
        # WHY: shows PROFITABLE/LOSS, first/last trade dates, and in-window Python stats
        #   so you can see if trade-count gaps are date-range issues (not logic bugs).
        def _dates(trades, ekey="entry_time"):
            """Extract (first, last) date strings from trades list."""
            ts = [t.get(ekey) for t in (trades or []) if t.get(ekey)]
            ts = sorted(str(x) for x in ts)
            return (ts[0], ts[-1]) if ts else ("-", "-")

        # MT5 stats already parsed above (from xlsx or HTML)
        mt5_net = mt5_stats.get("net_profit")
        mt5_pf  = mt5_stats.get("profit_factor")
        mt5_first, mt5_last = _dates(mt5_trades)

        # Python stats: recompute from trades we already have
        def _f(x):
            try:
                return float(x)
            except Exception:
                return None

        py_net_pips = sum((_f(t.get("net_pips")) or 0) for t in (py or [])) if py else None
        # Profit factor from trades: sum wins / abs(sum losses)
        py_pf = None
        if py:
            wins = sum((_f(t.get("net_pips")) or 0) for t in py if (_f(t.get("net_pips")) or 0) > 0)
            losses = sum((_f(t.get("net_pips")) or 0) for t in py if (_f(t.get("net_pips")) or 0) < 0)
            py_pf = (wins / abs(losses)) if losses else None
        py_first, py_last = _dates(py)

        # In-window Python stats (restricted to MT5 test window)
        # WHY: Python JSON spans years; MT5 test is ~3 months. Compare like-for-like.
        def _in_window(t, lo="2026-01-01", hi="2026-04-08"):
            et = str(t.get("entry_time") or "")[:10]
            return lo <= et <= hi
        py_in = [t for t in (py or []) if _in_window(t)]
        py_in_pips = sum((_f(t.get("net_pips")) or 0) for t in py_in) if py_in else 0

        def _verdict(v):
            """Return (label, float_val) — PROFITABLE/LOSS/? based on sign."""
            try:
                v = float(v)
                return ("PROFITABLE" if v > 0 else "LOSS"), v
            except Exception:
                return ("?", None)

        mt5_verd, mt5_v = _verdict(mt5_net)
        py_verd,  py_v  = _verdict(py_net_pips)

        # CHANGED: June 2026 — build summary as a string (for batch JSON) instead of writing
        #   directly to a file; gate file write behind WRITE_PER_EA_FILES.
        py_cfg = {}
        if meta and meta.get("file"):
            try:
                import json as _json
                with open(os.path.join(_py_rules_dir(), meta["file"]), encoding="utf-8") as _fh:
                    _rd = _json.load(_fh)
                py_cfg = {
                    "spread_pips": _rd.get("spread_pips"),
                    "commission_pips": _rd.get("commission_pips"),
                    "entry_tf": _rd.get("entry_tf"),
                }
            except Exception:
                pass

        f = _io.StringIO()
        f.write("EA: %s\n" % ea)
        f.write("combo: %s\nexit: %s\ntf: %s\n\n" %
                (rec.get("rule_combo", "?"), rec.get("exit_name", "?"),
                 rec.get("entry_tf", "?")))

        f.write("MT5:\n")
        f.write("  trades: %d\n" % len(mt5_trades))
        f.write("  net_profit: %s  (%s)\n" %
                (mt5_net if mt5_net is not None else "?", mt5_verd))
        f.write("  profit_factor: %s\n" %
                (mt5_pf if mt5_pf is not None else "?"))
        f.write("  first trade: %s\n  last  trade: %s\n\n" % (mt5_first, mt5_last))

        f.write("Python:\n")
        f.write("  trades: %d\n" % len(py or []))
        f.write("  net_pips: %s  (%s)\n" %
                (("%.1f" % py_net_pips) if py_net_pips is not None else "?", py_verd))
        f.write("  profit_factor: %s\n" %
                (("%.2f" % py_pf) if py_pf is not None else "?"))
        f.write("  first trade: %s\n  last  trade: %s\n" % (py_first, py_last))
        f.write("  in-window (2026-01-01..2026-04-08): %d trades, net_pips %.1f\n\n"
                % (len(py_in), py_in_pips))

        if meta:
            f.write("python source file: %s\npython tf: %s\n\n" %
                    (meta.get("file"), meta.get("py_tf")))

        f.write("----- DATA CONFIG (confirm both sides match) -----\n")
        f.write("MT5 (from report/ini):\n")
        f.write("  symbol: %s\n  period: %s\n  bars: %s\n  initial_deposit: %s\n  broker/server: %s\n"
                % (mt5_stats.get("symbol") or "?",
                   mt5_stats.get("period") or "?",
                   mt5_stats.get("bars") or "?",
                   mt5_stats.get("initial_deposit") or "?",
                   mt5_stats.get("broker") or "?"))
        f.write("  requested: symbol=%s from=%s to=%s deposit=%s leverage=1:%s\n\n"
                % (rec.get("test_symbol") or "?",
                   rec.get("test_from") or "?",
                   rec.get("test_to") or "?",
                   rec.get("test_deposit") or "?",
                   rec.get("test_leverage") or "?"))
        f.write("Python (intended config):\n")
        f.write("  prop_firm: ?\n  account_size: ?\n  symbol: ?\n")
        f.write("  spread_pips: %s\n  commission_pips: %s\n  entry_tf: %s\n\n"
                % (py_cfg.get("spread_pips") or "?",
                   py_cfg.get("commission_pips") or "?",
                   py_cfg.get("entry_tf") or "?"))
        f.write("CHECK: symbol, date window, spread/commission, and deposit should match across\n"
                "both sides. A different symbol/window/costs will change trade counts and net.\n\n")
        f.write("NOTE: compare the date ranges above. If Python's first/last span is much wider\n"
                "than MT5's test window, the trade-count gap is mostly out-of-range trades,\n"
                "not a logic divergence.\n")

        summary_text = f.getvalue()
        if WRITE_PER_EA_FILES:
            os.makedirs(sub, exist_ok=True)
            with open(os.path.join(sub, "summary.txt"), "w", encoding="utf-8") as _sf:
                _sf.write(summary_text)

        batch["eas"].append({
            "ea": ea,
            "combo": rec.get("rule_combo") if rec else None,
            "exit": rec.get("exit_name") if rec else None,
            "tf": rec.get("entry_tf") if rec else None,
            "mt5": {"trades": mt5_trades, "stats": mt5_stats},
            "python": {"trades": _flat_py, "meta": meta or {}},
            "entry_compare": _cmp_rows,
            "summary_text": summary_text,
        })
    cs = os.path.join(reports, "comparison_summary.csv")
    if os.path.isfile(cs):
        shutil.copy(cs, os.path.join(dump, "comparison_summary.csv"))

    # CHANGED: June 2026 — broader tester log patterns + journal summary in _DEBUG.txt
    # WHY: original globs assumed Tester\<hash>\Agent-* nesting; local agents live at
    #   Tester\Agent-127.0.0.1-3000\ or Tester\Core01\ directly under Tester\.
    _copy_data_dir = _derive_data_dir(_last_out_dir) if _last_out_dir else None
    _journal_summary = {"path": None}  # populated below if a journal is found

    def _summarize_journal(journal_path):
        import re as _re, collections as _coll
        skip = _coll.Counter()
        sig_true = sig_false = indfail = 0
        shiftdiag = gmtdiag = ""
        try:
            with open(journal_path, encoding="utf-16", errors="ignore") as f:
                txt = f.read()
            if "[DIAG]" not in txt and "[SKIP]" not in txt:
                with open(journal_path, encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
        except Exception as e:
            _d("  journal read error: %s" % e)
            return {"path": journal_path, "error": str(e)}
        for line in txt.splitlines():
            m = _re.search(r'\[SKIP\] (\w+)', line)
            if m:
                skip[m.group(1)] += 1
            if "signal=true"  in line: sig_true  += 1
            if "signal=false" in line: sig_false += 1
            if "indFail=true" in line: indfail   += 1
            if "[SHIFT-DIAG]" in line and not shiftdiag:
                shiftdiag = line.strip()[-200:]
            if "[GMT-DIAG]" in line and not gmtdiag:
                gmtdiag = line.strip()[-200:]
        _d("  JOURNAL SUMMARY: signal_true=%d  signal_false=%d  indFail_true=%d" %
           (sig_true, sig_false, indfail))
        _d("  SKIP counts: %s" % dict(skip))
        if shiftdiag: _d("  %s" % shiftdiag)
        if gmtdiag:   _d("  %s" % gmtdiag)
        return {
            "path": journal_path,
            "signal_true": sig_true, "signal_false": sig_false, "indFail_true": indfail,
            "skip_counts": dict(skip),
            "shift_diag": shiftdiag, "gmt_diag": gmtdiag,
        }

    if _copy_data_dir:
        import glob as _g
        # condlog_*.csv — search every plausible tester sandbox depth + terminal Files
        _clog_pats = [
            os.path.join(_copy_data_dir, 'Tester', 'Agent-*', 'MQL5', 'Files', 'condlog_*.csv'),
            os.path.join(_copy_data_dir, 'Tester', '*', 'Agent-*', 'MQL5', 'Files', 'condlog_*.csv'),
            os.path.join(_copy_data_dir, 'Tester', '*', 'MQL5', 'Files', 'condlog_*.csv'),
            os.path.join(_copy_data_dir, 'MQL5', 'Files', 'condlog_*.csv'),
            os.path.join(_copy_data_dir, 'Tester', '**', 'condlog_*.csv'),
        ]
        _clogs = []
        for _p in _clog_pats:
            _clogs += _g.glob(_p, recursive=True)
        _clogs = sorted(set(_clogs), key=os.path.getmtime, reverse=True)
        if _clogs:
            shutil.copy(_clogs[0], os.path.join(dump, "condlog.csv"))
            _d("Copied condlog: %s" % _clogs[0])
        else:
            _d("condlog: not found (run with DebugConditions=true; searched %d patterns)"
               % len(_clog_pats))
            for _p in _clog_pats: _d("    tried: %s" % _p)

        # tester journal *.log — agents live DIRECTLY under Tester\ (no extra wildcard level)
        # and on disk 'logs' may be lowercase or uppercase; include both. Also the data-dir-root
        # \Logs\ (terminal journal) which also carries [DIAG]/[SKIP] lines.
        _jlog_pats = [
            os.path.join(_copy_data_dir, 'Tester', 'Agent-*', 'Logs', '*.log'),
            os.path.join(_copy_data_dir, 'Tester', 'Agent-*', 'logs', '*.log'),
            os.path.join(_copy_data_dir, 'Tester', 'Agent-*', '*.log'),
            os.path.join(_copy_data_dir, 'Tester', 'Core*', 'logs', '*.log'),
            os.path.join(_copy_data_dir, 'Tester', '*', 'logs', '*.log'),
            os.path.join(_copy_data_dir, 'Tester', '*', 'Agent-*', 'logs', '*.log'),
            os.path.join(_copy_data_dir, 'Logs', '*.log'),
            os.path.join(_copy_data_dir, 'MQL5', 'Logs', '*.log'),
            os.path.join(_copy_data_dir, 'Tester', '**', '*.log'),
        ]
        _jlogs = []
        for _p in _jlog_pats:
            _jlogs += _g.glob(_p, recursive=True)
        _jlogs = sorted(set(_jlogs), key=os.path.getmtime, reverse=True)
        if _jlogs:
            for _i, _src in enumerate(_jlogs[:2]):
                shutil.copy(_src, os.path.join(dump, "mt5_journal_%d.log" % _i))
            _d("Copied journal(s): %s" % [os.path.basename(x) for x in _jlogs[:2]])
            _journal_summary = _summarize_journal(_jlogs[0])
        else:
            _d("journal: not found (searched %d patterns under %s)"
               % (len(_jlog_pats), os.path.join(_copy_data_dir, 'Tester')))
            for _p in _jlog_pats: _d("    tried: %s" % _p)
    else:
        _d("condlog/journal copy skipped: data dir unknown")

    batch["journal"] = _journal_summary

    _json_path = os.path.join(dump, "batch_debug.json")
    try:
        with open(_json_path, "w", encoding="utf-8") as _jf:
            json.dump(batch, _jf, indent=2, default=str)
        _d("Consolidated dump: %s (%d EAs, one file)" % (_json_path, len(batch["eas"])))
    except Exception as _je:
        _d("batch_debug.json write FAILED: %s" % _je)

    if _dbg is not None:
        try: _dbg.close()
        except Exception: pass

    _append("DEBUG DUMP: %s" % dump)
    _append("DEBUG LOG: %s" % _dbg_path)
    try:
        os.startfile(dump)
    except Exception:
        pass


def _do_run_all(source_var):
    """Chain: Generate → Compile → Build → Run → Compare → Debug Dump."""
    def chain():
        _append("\n===== RUN ALL =====")
        if not _step_generate(source_var):
            _append("===== STOPPED (generate) =====")
            return
        if not _step_compile():
            _append("===== STOPPED (compile) =====")
            return
        if not _step_build_run_files():
            _append("===== STOPPED (build) =====")
            return
        if not _step_run_tests():
            _append("===== STOPPED (run) =====")
            return
        if not _step_compare():
            _append("===== STOPPED (compare) =====")
            return
        _write_debug_dump()
        _append("===== RUN ALL COMPLETE =====")
    _run_bg(chain)


# CHANGED: June 2026 — headless compile via metaeditor64; remembers the .exe path per machine
# WHY: user runs on two PCs — the path differs; save it once per hostname in gitignored
#   p1_config.json so neither machine inherits the other's path.
def _do_compile():
    _run_bg(_step_compile)


def _do_build_run_files():
    def work():
        try:
            from project3_live_trading.batch_ea_tools import emit_tester_inis
            # CHANGED: June 2026 — always recompute canonical (self-correcting); no dialogs.
            global _last_out_dir, _last_bat_path, _last_reports_dir
            batch_dir = _canonical_batch_dir()
            if not batch_dir:
                _append("Build run files cancelled (no MT5 data folder known).")
                return
            _last_out_dir = batch_dir
            manifest = os.path.join(batch_dir, "batch_manifest.json")
            if not os.path.isfile(manifest):
                _append("No manifest at %s — run '1. Generate EAs' first." % manifest)
                return
            data_dir = _derive_data_dir(batch_dir)
            if not data_dir:
                _append("Couldn't derive MT5 data dir from %s — pick it." % batch_dir)
                data_dir = filedialog.askdirectory(
                    title="Pick MT5 DATA folder (contains MQL5\\Experts)")
                if not data_dir:
                    _append("Build run files cancelled (no data dir).")
                    return
            # CHANGED: June 2026 — reports in a subfolder of Experts\batch, not the EA folder
            reports_dir = os.path.join(batch_dir, "reports")
            _append("Using batch dir : %s" % batch_dir)
            _append("Reports will be written to: %s" % os.path.abspath(reports_dir))
            # CHANGED: June 2026 — resolve terminal64 (derive from metaeditor → ask once → save)
            from project3_live_trading.batch_ea_tools import (
                resolve_terminal_path, save_terminal_path)
            term = resolve_terminal_path()
            if not term:
                _append("terminal64.exe not found automatically on this machine.")
                term = filedialog.askopenfilename(
                    title="Locate terminal64.exe — saved for THIS computer only",
                    filetypes=[("terminal64", "terminal64.exe"), ("All exe", "*.exe")])
                if term and save_terminal_path(term):
                    _append("Saved terminal64 path for this machine (%s)."
                            % __import__('socket').gethostname())
            emit_tester_inis(manifest, data_dir, "Experts\\batch", reports_dir,
                             terminal_exe=term or None)
            # CHANGED: June 2026 — record bat + reports dir for the Run Tests / Compare steps
            _last_bat_path    = os.path.join(batch_dir, "run_all_tests.bat")
            _last_reports_dir = reports_dir
            _append("Run files written.")
            _append("If you used '1b. Compile EAs', the .ex5 are already in MQL5\\Experts\\batch.")
            if term:
                _append("Terminal: %s" % term)
            _append("Next: click '2b. Run Tests' (make sure MT5 is closed).")
        except Exception as e:
            _append("ERROR during build run files: %s" % e)
    _run_bg(work)


def _do_run_tests():
    def work():
        try:
            import subprocess
            global _last_bat_path, _last_reports_dir
            bat = _last_bat_path
            if not bat or not os.path.isfile(bat):
                bat = filedialog.askopenfilename(
                    title="Pick run_all_tests.bat",
                    initialdir=(_last_out_dir or None),
                    filetypes=[("Batch", "*.bat")])
                if not bat:
                    _append("Run Tests cancelled (no .bat).")
                    return

            if _mt5_is_running():
                _append("MT5 is OPEN — close the MetaTrader 5 terminal first, then click "
                        "2b. Run Tests again. The Strategy Tester can't run while the "
                        "terminal holds the files.")
                return

            import subprocess, threading, time, glob as _glob

            # CHANGED: June 2026 — read total EA count from manifest for progress readout
            # WHY: show done/total and remaining so you know how many rules are left.
            _total_eas = 0
            try:
                import json as _json
                _man = os.path.join(os.path.dirname(bat), "batch_manifest.json")
                if os.path.isfile(_man):
                    with open(_man, encoding="utf-8") as _f:
                        _total_eas = len(_json.load(_f))
            except Exception:
                _total_eas = 0

            # CHANGED: June 2026 — clear old reports so the done/total count is accurate
            # WHY: stale reports from prior runs pollute the counter (5/4 done) and Compare.
            if _last_reports_dir and os.path.isdir(_last_reports_dir):
                _cleared = 0
                # CHANGED: June 2026 — clear all report formats including old .xlsx.htm double-ext files
                for _f in (_glob.glob(os.path.join(_last_reports_dir, "*.xlsx.htm")) +
                           _glob.glob(os.path.join(_last_reports_dir, "*.xlsx")) +
                           _glob.glob(os.path.join(_last_reports_dir, "*.htm")) +
                           _glob.glob(os.path.join(_last_reports_dir, "*.html"))):
                    try:
                        os.remove(_f)
                        _cleared += 1
                    except Exception:
                        pass
                if _cleared:
                    _append("Cleared %d old report(s) from %s" % (_cleared, _last_reports_dir))

            _append("Running backtests via %s ..." % os.path.basename(bat))
            if _total_eas:
                _append("Running %d EA(s). Each takes ~1-2 min headless." % _total_eas)
            _append("MT5 runs each EA headless (no visible window). Watch the heartbeat below.")

            proc = subprocess.Popen(
                ["cmd", "/c", bat],
                cwd=os.path.dirname(bat),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)

            # CHANGED: June 2026 — number the RUNNING lines as they stream so you know position
            # WHY: each RUNNING line gets tagged [k/N] so you see which EA is in progress.
            _seen = {"n": 0}
            def _pump():
                for line in proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    if "=== RUNNING" in line:
                        _seen["n"] += 1
                        if _total_eas:
                            line = line.replace("=== RUNNING",
                                                "=== RUNNING [%d/%d]" % (_seen["n"], _total_eas))
                    _append("  " + line)
            _t = threading.Thread(target=_pump, daemon=True)
            _t.start()

            # Heartbeat: every 5s prove work is happening — MT5 alive + newest report growing.
            # CHANGED: June 2026 — heartbeat replaces silent wait; gives live proof per-EA
            rdir = _last_reports_dir
            _beat = 0
            while proc.poll() is None:
                time.sleep(5)
                _beat += 1
                alive = _mt5_is_running()
                newest = ""
                size = 0
                if rdir and os.path.isdir(rdir):
                    # CHANGED: June 2026 — tester now writes .xlsx (full deal data)
                    _files = (_glob.glob(os.path.join(rdir, "*.xlsx")) +
                              _glob.glob(os.path.join(rdir, "*.htm")) +
                              _glob.glob(os.path.join(rdir, "*.html")) +
                              _glob.glob(os.path.join(rdir, "*.xml")))
                    if _files:
                        newest = max(_files, key=os.path.getmtime)
                        try:
                            size = os.path.getsize(newest)
                        except OSError:
                            size = 0
                done_n = (len(_glob.glob(os.path.join(rdir, "*.xlsx"))) +
                          len(_glob.glob(os.path.join(rdir, "*.htm"))) +
                          len(_glob.glob(os.path.join(rdir, "*.html")))) if (rdir and os.path.isdir(rdir)) else 0
                # CHANGED: June 2026 — show done/total and remaining so you know how many left
                # WHY: clear progress readout (3/6 done, 3 left) instead of just count.
                if _total_eas:
                    _remaining = max(0, _total_eas - done_n)
                    _prog = "%d/%d done, %d left" % (done_n, _total_eas, _remaining)
                else:
                    _prog = "%d done" % done_n
                _append("  [heartbeat %d] MT5 %s | %s%s"
                        % (_beat,
                           "running" if alive else "starting/closing",
                           _prog,
                           (" | writing %s (%d KB)" % (os.path.basename(newest), size // 1024))
                           if newest else ""))
                # A2: tail the MT5 tester log for the "bars processed" line
                _tlog_dir = os.path.join(_derive_data_dir(_last_out_dir) or "", "Tester", "logs")
                if os.path.isdir(_tlog_dir):
                    _tlogs = _glob.glob(os.path.join(_tlog_dir, "*.log"))
                    if _tlogs:
                        _tl = max(_tlogs, key=os.path.getmtime)
                        try:
                            with open(_tl, "rb") as _f:
                                _f.seek(max(0, os.path.getsize(_tl) - 400))
                                _tail = _f.read().decode("utf-16", "ignore").strip().splitlines()
                            if _tail:
                                _append("    tester: " + _tail[-1][-120:])
                        except Exception:
                            pass
            _t.join(timeout=2)
            rc = proc.returncode

            if rc == 0:
                _append("All tester passes finished.")
            else:
                _append("Tester batch exited with code %d (some passes may have failed)." % rc)

            if _last_reports_dir and os.path.isdir(_last_reports_dir):
                # CHANGED: June 2026 — count .xlsx/.htm/.html (tester now writes .xlsx)
                n_rep = len([f for f in os.listdir(_last_reports_dir)
                             if f.lower().endswith((".xlsx", ".htm", ".html"))])
                # CHANGED: June 2026 — explicit done/total count in final summary
                _append("Done: %d/%d reports written." % (n_rep, _total_eas or n_rep))
                _append("Reports folder: %s" % _last_reports_dir)
                _append("Next: click '3. Compare Reports'.")
            else:
                _append("Done. Next: click '3. Compare Reports' and pick the reports folder.")
        except Exception as e:
            _append("ERROR during run tests: %s" % e)
    _run_bg(work)


def _do_compare():
    def work():
        try:
            from project3_live_trading.batch_compare_reports import compare_reports
            import glob as _g
            # CHANGED: June 2026 — auto-resolve ALL paths (reports, manifest); NO dialogs
            # CHANGED: June 2026 — Python trades now loaded from stored backtest files; no CSV dir
            # WHY: trades exist in backtest_trades_{TF}.json from backtesting; fully automatic.
            global _last_reports_dir, _last_out_dir

            # 1) REPORTS DIR — remembered from Run Tests/Build Run Files; else derive from batch dir
            reports = (_last_reports_dir
                       if (_last_reports_dir and os.path.isdir(_last_reports_dir)) else None)
            if not reports:
                bdir = _last_out_dir or _canonical_batch_dir()
                if bdir:
                    cand = os.path.join(bdir, "reports")
                    if os.path.isdir(cand):
                        reports = cand
            if not reports or not os.path.isdir(reports):
                _append("No reports folder found — run 2b. Run Tests first.")
                return

            # 2) MANIFEST — sits next to the batch dir; auto-resolve, never ask
            manifest = ""
            bdir = _last_out_dir or _canonical_batch_dir()
            if bdir:
                m = os.path.join(bdir, "batch_manifest.json")
                if os.path.isfile(m):
                    manifest = m
            # also try one level up from reports (…\batch\reports → …\batch)
            if not manifest:
                m2 = os.path.join(os.path.dirname(reports), "batch_manifest.json")
                if os.path.isfile(m2):
                    manifest = m2
            # manifest is optional for compare_reports; empty string is fine if not found

            # 3) PYTHON TRADES — pulled automatically from stored outputs/rules/*.json
            # WHY: trades already exist on disk from backtesting; no manual export needed.
            #   Each rule_*_{combo}_{exit}_{hash}_{TF}.json has a trades list.
            _append("Comparing reports in %s ..." % reports)
            if manifest:
                _append("Manifest: %s" % manifest)
            else:
                _append("(No manifest found — will use EA names only for matching)")
            _append("Python trades loaded from outputs/rules/*.json (automatic).")

            # No python_dir needed; trades come from outputs/rules/*.json via manifest
            rows = compare_reports(reports, "", manifest)
            for line in rows:
                _append(line)
            _append("comparison_summary.csv written in the reports folder.")
        except Exception as e:
            _append("ERROR during compare: %s" % e)
    _run_bg(work)


# ── panel builder ─────────────────────────────────────────────────────────────

def build_panel(parent):
    global _log, _grid_tree, _cur_source
    global _f_stage, _f_tf, _f_dir, _f_mintr, _f_minwr, _f_sort, _f_profitable, _nostats_lbl
    global _f_exit_vars, _f_exit_all, _f_exit_menu, _f_exit_menuobj
    panel = tk.Frame(parent, bg=BG)

    hdr = tk.Frame(panel, bg=DARK)
    hdr.pack(fill="x")
    tk.Label(hdr, text="Batch EAs", bg=DARK, fg="white",
             font=("Segoe UI", 13, "bold"), padx=12, pady=8).pack(side=tk.LEFT)

    # Source + action buttons row
    # CHANGED: June 2026 — default 'all' so grid is never blank if backtest_matrix.json
    #   is a Git LFS stub (not yet pulled). Switch to 'backtest' once matrix is available.
    src_var = tk.StringVar(value="all")
    bar = tk.Frame(panel, bg=BG)
    bar.pack(fill="x", padx=10, pady=(8, 2))
    tk.Label(bar, text="Source:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    # CHANGED: June 2026 — add backtest + optimizer + all options
    src_combo = ttk.Combobox(bar, textvariable=src_var, width=14, state="readonly",
                              values=["all", "backtest", "optimizer", "saved_rules", "my_rules"])
    src_combo.pack(side=tk.LEFT, padx=6)
    src_combo.bind("<<ComboboxSelected>>", lambda e: _populate_grid(src_var.get()))
    # WHY: src_var is local to build_panel; filter-widget lambdas use _cur_source (module
    #   global updated at the top of _populate_grid) so they always pass the active source.

    def _btn(text, cmd):
        return tk.Button(bar, text=text, command=cmd, bg=MIDGREY, fg="white",
                         relief=tk.FLAT, cursor="hand2", padx=12, pady=4,
                         font=("Segoe UI", 9, "bold"))

    _btn("1. Generate EAs",    lambda: _do_generate(src_var)).pack(side=tk.LEFT, padx=4)
    # CHANGED: June 2026 — 1b runs metaeditor64 headlessly; no manual MetaEditor needed
    _btn("1b. Compile EAs",   _do_compile).pack(side=tk.LEFT, padx=4)
    _btn("2. Build Run Files", _do_build_run_files).pack(side=tk.LEFT, padx=4)
    # CHANGED: June 2026 — 2b launches run_all_tests.bat in-app; streams progress to log
    _btn("2b. Run Tests",      _do_run_tests).pack(side=tk.LEFT, padx=4)
    _btn("3. Compare Reports", _do_compare).pack(side=tk.LEFT, padx=4)
    # CHANGED: June 2026 — Run All chains all steps + writes debug dump
    _btn("▶ Run All", lambda: _do_run_all(src_var)).pack(side=tk.LEFT, padx=8)

    # CHANGED: June 2026 — filter row (parity with Strategy Refiner)
    filt_row = tk.Frame(panel, bg=BG)
    filt_row.pack(fill="x", padx=10, pady=(0, 4))

    tk.Label(filt_row, text="Stage:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    _f_stage = ttk.Combobox(filt_row, width=10, state="readonly",
                             values=["All", "Evaluation", "Funded", "Phase 1", "Phase 2"])
    _f_stage.set("All")
    _f_stage.pack(side=tk.LEFT, padx=(2, 6))

    tk.Label(filt_row, text="TF:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    _f_tf = ttk.Combobox(filt_row, width=6, state="readonly",
                          values=["All", "M5", "M15", "H1", "H4", "D1"])
    _f_tf.set("All")
    _f_tf.pack(side=tk.LEFT, padx=(2, 6))

    tk.Label(filt_row, text="Dir:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    _f_dir = ttk.Combobox(filt_row, width=6, state="readonly",
                           values=["All", "BUY", "SELL"])
    _f_dir.set("All")
    _f_dir.pack(side=tk.LEFT, padx=(2, 6))

    # CHANGED: June 2026 — multi-select exit-strategy filter via Menubutton
    # WHY: dropdown of checkboxes (All / specific exits) — compact, fits in filter row.
    # HOTFIX: June 2026 — store Menu object in _f_exit_menuobj (not _f_exit_menu['menu'], which is a str)
    tk.Label(filt_row, text="Exits:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    _f_exit_menu = tk.Menubutton(filt_row, text="All ▾", relief=tk.RAISED, bg=WHITE,
                                 font=("Segoe UI", 9), padx=6, pady=2, cursor="hand2")
    _f_exit_menuobj = tk.Menu(_f_exit_menu, tearoff=0)  # keep the OBJECT
    _f_exit_menu.config(menu=_f_exit_menuobj)           # attach it
    _f_exit_menu.pack(side=tk.LEFT, padx=(2, 6))

    tk.Label(filt_row, text="Min Trades:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    _f_mintr = tk.Entry(filt_row, width=5, font=("Segoe UI", 9))
    _f_mintr.pack(side=tk.LEFT, padx=(2, 6))

    tk.Label(filt_row, text="Min Win%:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    _f_minwr = tk.Entry(filt_row, width=5, font=("Segoe UI", 9))
    _f_minwr.pack(side=tk.LEFT, padx=(2, 6))

    tk.Label(filt_row, text="Sort:", bg=BG, fg=DARK,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    _f_sort = ttk.Combobox(filt_row, width=12, state="readonly",
                            values=["Prop Score", "Net Pips", "Win Rate", "PF", "Trades"])
    _f_sort.set("Net Pips")
    _f_sort.pack(side=tk.LEFT, padx=(2, 8))

    for _w in (_f_stage, _f_tf, _f_dir, _f_sort):
        _w.bind("<<ComboboxSelected>>", lambda e: _populate_grid(_cur_source))
    _f_mintr.bind("<Return>", lambda e: _populate_grid(_cur_source))
    _f_minwr.bind("<Return>", lambda e: _populate_grid(_cur_source))

    # CHANGED: June 2026 — profitable-only checkbox (parity with refiner, default ON)
    _f_profitable = tk.BooleanVar(value=True)
    tk.Checkbutton(filt_row, text="Profitable only", variable=_f_profitable,
                   bg=BG, fg=DARK, font=("Segoe UI", 9),
                   command=lambda: _populate_grid(_cur_source)).pack(side=tk.LEFT, padx=(4, 8))

    _nostats_lbl = tk.Label(filt_row, text="", bg=BG, fg="#888",
                             font=("Segoe UI", 8))
    _nostats_lbl.pack(side=tk.LEFT, padx=4)

    # CHANGED: June 2026 — grid + log in a vertical PanedWindow so the log box is drag-resizable
    # WHY: fixed-height log can't be resized; PanedWindow sash lets you drag to grow/shrink.
    _split = tk.PanedWindow(panel, orient="vertical", sashwidth=6,
                            sashrelief="raised", bg=BG)
    _split.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # CHANGED: June 2026 — rule selection grid with refiner-parity columns
    _grid_frame = tk.Frame(_split, bg=WHITE)
    # CHANGED: June 2026 — grid becomes first pane of PanedWindow (was pack with expand=True)

    # CHANGED: June 2026 — batch grid columns mirror the Rule Refiner strategy grid
    # CHANGED: June 2026 — added "profit" column (✅/❌/—) matching refiner's net>0 definition
    cols = ("sel", "id", "stage", "rule", "exit", "tf", "dir", "trades", "wr", "pf",
            "net_pips", "net_dollars", "net_pct", "avg_pips", "avg_dollars", "avg_pct",
            "profit", "win_pass", "prop_score", "off")
    _grid_tree = ttk.Treeview(_grid_frame, columns=cols, show="headings", height=10)
    _col_spec = [
        ("sel",        "☐",             34,  "center"),
        ("id",         "ID",            60,  "center"),
        ("stage",      "Stage",         70,  "center"),
        ("rule",       "Rule",         180,  "w"),
        ("exit",       "Exit Strategy",120,  "w"),
        ("tf",         "TF",            45,  "center"),
        ("dir",        "Dir",           50,  "center"),
        ("trades",     "Trades",        60,  "center"),
        ("wr",         "Win Rate",      70,  "center"),
        ("pf",         "PF",            55,  "center"),
        ("net_pips",   "Net Pips",      85,  "e"),
        ("net_dollars","Net $",         85,  "e"),
        ("net_pct",    "Net %",         65,  "e"),
        ("avg_pips",   "Avg Pips",      70,  "e"),
        ("avg_dollars","Avg $",         75,  "e"),
        ("avg_pct",    "Avg %",         65,  "e"),
        ("profit",     "Profit",        60,  "center"),
        ("win_pass",   "Win Pass",      95,  "center"),
        ("prop_score", "Prop Score",    80,  "center"),
        ("off",        "N/N+1",         60,  "center"),
    ]
    for c, t, w, a in _col_spec:
        _grid_tree.heading(c, text=t)
        # CHANGED: June 2026 — minwidth + stretch=False so columns hold their size and the
        #   horizontal scrollbar has real range (matches refiner grid feel)
        _grid_tree.column(c, width=w, minwidth=max(40, w // 2), anchor=a, stretch=False)
    _grid_tree.heading("sel", text="☐", command=_toggle_all)
    # CHANGED: June 2026 — row colors match refiner: green=profitable, red=losing, grey=no-data
    _grid_tree.tag_configure("profitable", foreground="#28a745")
    _grid_tree.tag_configure("losing",     foreground="#dc3545")
    _grid_tree.tag_configure("neutral",    foreground="#888888")
    _grid_tree.bind("<Button-1>", _toggle_row, add="+")
    _gsb = ttk.Scrollbar(_grid_frame, orient="vertical", command=_grid_tree.yview)
    _grid_tree.configure(yscrollcommand=_gsb.set)
    _gsb_h = ttk.Scrollbar(_grid_frame, orient="horizontal", command=_grid_tree.xview)
    _grid_tree.configure(xscrollcommand=_gsb_h.set)
    # CHANGED: June 2026 — pack scrollbars BEFORE the tree (refiner order) so each bar
    #   reserves its strip before the tree claims the remaining space.
    _gsb_h.pack(side="bottom", fill="x")   # h-bar first → owns the bottom strip
    _gsb.pack(side="right",  fill="y")     # v-bar next  → owns the right strip
    _grid_tree.pack(side="left", fill="both", expand=True)
    # CHANGED: June 2026 — wheel scroll on hover (parity with refiner); bind() not bind_all()
    #   so it only fires over this grid and doesn't hijack other panels' scrolling.
    def _grid_wheel(event):
        try:
            if event.num == 4:
                _grid_tree.yview_scroll(-3, "units")
            elif event.num == 5:
                _grid_tree.yview_scroll(3, "units")
            else:
                _grid_tree.yview_scroll(int(-1 * (event.delta / 120)) * 3, "units")
        except Exception:
            pass
        return "break"
    _grid_tree.bind("<MouseWheel>", _grid_wheel)
    _grid_tree.bind("<Button-4>",   _grid_wheel)
    _grid_tree.bind("<Button-5>",   _grid_wheel)
    _grid_tree.bind("<Shift-MouseWheel>",
                    lambda e: (_grid_tree.xview_scroll(int(-1*(e.delta/120))*3, "units"), "break")[1])

    # CHANGED: June 2026 — add grid frame to PanedWindow (was .pack(fill="both", expand=True))
    # WHY: minsize=120 prevents grid from collapsing; stretch="always" shares space with log.
    _split.add(_grid_frame, minsize=120, stretch="always")

    _populate_grid(src_var.get())

    # CHANGED: June 2026 — log becomes second pane of PanedWindow (was pack with height=6, expand=False)
    # WHY: drag the sash up → log grows taller, grid shrinks; drag down → reverse.
    _log = tk.Text(_split, font=("Consolas", 9), bg=WHITE, fg=DARK, wrap="word", height=8)
    _split.add(_log, minsize=60, stretch="always")
    _log.insert("end",
                "Batch flow: 1. Tick rules -> Generate EAs -> 1b. Compile EAs (headless) -> "
                "2. Build Run Files -> run .bat in MT5 -> 3. Compare Reports.\n\n")
    _log.configure(state="disabled")

    return panel


def refresh():
    pass

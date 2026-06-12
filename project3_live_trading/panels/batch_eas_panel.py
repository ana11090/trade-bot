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
    global _grid_entries, _batch_sel_iids, _iid_to_entry, _cur_source
    global _f_stage, _f_tf, _f_dir, _f_mintr, _f_minwr, _f_sort, _f_profitable
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

    # WHY: if the chosen source is empty (e.g. backtest_matrix.json is a Git LFS pointer /
    #   not pulled), fall back to 'all' so the page is never blank when data exists elsewhere.
    # CHANGED: June 2026 — non-empty fallback
    if not _grid_entries and _all:
        print("[BATCH-GRID] source '%s' empty — falling back to all" % source, flush=True)
        _grid_entries = list(_all)

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
    def _passes(r):
        st = _stage_cell(r)
        tf = r.get('entry_tf') or r.get('entry_timeframe') or '—'
        dr = r.get('direction') or r.get('dir') or '—'
        tr = _num(r, 'total_trades') or 0
        wr = _num(r, 'win_rate') or 0
        net = _num(r, 'net_pips', 'net_total_pips', 'total_pips')

        if _val(_f_stage, "All") not in ("All", "", st):
            return False
        if _val(_f_tf, "All") not in ("All", "", tf):
            return False
        if _val(_f_dir, "All") not in ("All", "", dr):
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
            _iid = str(r.get('id', _shown))
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

def _do_generate(source_var):
    def work():
        try:
            from project3_live_trading.batch_ea_tools import batch_generate
            # CHANGED: June 2026 — generate only the rules ticked in the grid
            # WHY: iid is now r.get('id') or a counter — not a positional index — so
            #   use _iid_to_entry which was populated during _populate_grid.
            _selected = [_iid_to_entry[i] for i in _batch_sel_iids
                         if i in _iid_to_entry]
            if not _selected:
                _append("Tick at least one rule in the grid (or the header ☐ to select all).")
                return
            out_dir = filedialog.askdirectory(title="Choose output folder for .mq5 + manifest")
            if not out_dir:
                _append("Generate cancelled (no folder).")
                return
            src = source_var.get()
            _append("Generating %d EA(s) into %s ..." % (len(_selected), out_dir))
            results = batch_generate(out_dir, source=src, entries=_selected)
            ok = sum(1 for r in results if r.get("ok"))
            _append("Done: %d/%d EAs written." % (ok, len(results)))
            for r in results:
                if not r.get("ok"):
                    _append("  FAILED %s: %s" % (r.get("name"), r.get("error")))
            _append("Manifest: %s" % os.path.join(out_dir, "batch_manifest.json"))
        except Exception as e:
            _append("ERROR during generate: %s" % e)
    _run_bg(work)


# CHANGED: June 2026 — headless compile via metaeditor64; remembers the .exe path per machine
# WHY: user runs on two PCs — the path differs; save it once per hostname in gitignored
#   p1_config.json so neither machine inherits the other's path.
def _do_compile():
    def work():
        try:
            from project3_live_trading.batch_ea_tools import (
                batch_compile, get_saved_metaeditor_path,
                save_metaeditor_path, _find_metaeditor,
            )
            out_dir = filedialog.askdirectory(
                title="Pick the folder with generated .mq5 files")
            if not out_dir:
                _append("Compile cancelled (no .mq5 folder).")
                return
            data_dir = filedialog.askdirectory(
                title="Pick MT5 DATA folder (contains MQL5\\Experts)")
            if not data_dir:
                _append("Compile cancelled (no data dir).")
                return

            # Resolve metaeditor: saved (this machine) → auto-detect → ask once → save.
            me = get_saved_metaeditor_path() or _find_metaeditor()
            if not me:
                _append("metaeditor64.exe not found automatically on this machine.")
                me = filedialog.askopenfilename(
                    title="Locate metaeditor64.exe — saved for THIS computer only",
                    filetypes=[("metaeditor64", "metaeditor64.exe"), ("All exe", "*.exe")])
                if not me:
                    _append("Compile cancelled (no metaeditor64.exe picked).")
                    return
                if save_metaeditor_path(me):
                    _append("Saved metaeditor64 path for this machine (%s)."
                            % __import__('socket').gethostname())
                else:
                    _append("Warning: could not save the path; will ask again next time.")

            _append("Compiling EAs with metaeditor64 (headless) ...")
            results = batch_compile(out_dir, data_dir, metaeditor_path=me)
            ok = sum(1 for r in results if r.get("ok"))
            _append("Compiled %d/%d EA(s) to .ex5." % (ok, len(results)))
            for r in results:
                if not r.get("ok"):
                    _append("  FAILED %s: %s" % (r.get("name"), r.get("error")))
            if ok == len(results) and ok > 0:
                _append("All compiled. Next: 2. Build Run Files.")
        except Exception as e:
            _append("ERROR during compile: %s" % e)
    _run_bg(work)


def _do_build_run_files():
    def work():
        try:
            from project3_live_trading.batch_ea_tools import emit_tester_inis
            manifest = filedialog.askopenfilename(
                title="Pick batch_manifest.json",
                filetypes=[("JSON", "*.json")])
            if not manifest:
                _append("Build run files cancelled.")
                return
            data_dir = filedialog.askdirectory(
                title="Pick MT5 DATA folder (contains MQL5\\Experts)")
            if not data_dir:
                _append("Build run files cancelled (no data dir).")
                return
            reports_dir = filedialog.askdirectory(title="Pick folder for MT5 reports output")
            if not reports_dir:
                _append("Build run files cancelled (no reports dir).")
                return
            emit_tester_inis(manifest, data_dir, "Experts\\batch", reports_dir)
            _append("Run files written.")
            _append("If you used '1b. Compile EAs', the .ex5 are already in MQL5\\Experts\\batch.")
            _append("Otherwise compile manually in MetaEditor first.")
            _append("Set terminal64.exe path in run_all_tests.bat, then run it.")
        except Exception as e:
            _append("ERROR during build run files: %s" % e)
    _run_bg(work)


def _do_compare():
    def work():
        try:
            from project3_live_trading.batch_compare_reports import compare_reports
            reports = filedialog.askdirectory(title="Pick folder of MT5 .xlsx reports")
            if not reports:
                _append("Compare cancelled.")
                return
            python_dir = filedialog.askdirectory(title="Pick folder of Python trade CSVs")
            if not python_dir:
                _append("Compare cancelled (no python dir).")
                return
            manifest = filedialog.askopenfilename(
                title="Pick batch_manifest.json (recommended)",
                filetypes=[("JSON", "*.json")])
            _append("Comparing reports in %s ..." % reports)
            rows = compare_reports(reports, python_dir, manifest or "")
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
    _btn("3. Compare Reports", _do_compare).pack(side=tk.LEFT, padx=4)

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

    # CHANGED: June 2026 — rule selection grid with refiner-parity columns
    _grid_frame = tk.Frame(panel, bg=WHITE)
    # CHANGED: June 2026 — expand=True so the grid + scrollbars get real vertical space
    _grid_frame.pack(fill="both", expand=True, padx=6, pady=(2, 4))

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
    _populate_grid(src_var.get())

    # Log box — expand=False so the grid above gets the vertical space
    _log = tk.Text(panel, font=("Consolas", 9), bg=WHITE, fg=DARK, wrap="word", height=6)
    _log.pack(fill="x", expand=False, padx=10, pady=(0, 10))
    _log.insert("end",
                "Batch flow: 1. Tick rules -> Generate EAs -> 1b. Compile EAs (headless) -> "
                "2. Build Run Files -> run .bat in MT5 -> 3. Compare Reports.\n\n")
    _log.configure(state="disabled")

    return panel


def refresh():
    pass

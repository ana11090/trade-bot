"""
Strategy Refiner Panel — interactive trade filtering + deep optimizer.

Mode 1: Instant filter impact preview. Every slider/checkbox change shows
        how many trades are removed and whether the result improves.

Mode 2: Deep optimizer that searches filter combinations and scores them.
"""

# WHY (Phase 33 Fix 10): Optimizer-card dollar math hardcoded XAUUSD
#      pip_value (10.0) and SL (150). Load from config at module import
#      so non-XAUUSD users see correct dollar projections in optimizer.
# CHANGED: April 2026 — Phase 33 Fix 10 — config-driven optimizer dollars
#          (Ref: trade_bot_audit_round2_partC.pdf HIGH item #90 pg.31)
# WHY: Read pip_value from P2 config. Old hardcoded 10.0 was wrong for XAUUSD.
# CHANGED: April 2026 — read from config
_srp_pip_value = 1.0
_srp_sl_pips = 150.0
try:
    from project2_backtesting.panels.configuration import load_config as _srp_load_config
    _srp_cfg = _srp_load_config()
    _srp_pip_value = float(_srp_cfg.get('pip_value_per_lot', _srp_cfg.get('pip_value', 1.0)))
    _srp_sl_pips = float(_srp_cfg.get('default_sl_pips', 150.0))
except Exception:
    pass  # fallback to XAUUSD defaults

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import csv
import threading
import time

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

import state

BG      = "#f0f2f5"
WHITE   = "white"
GREEN   = "#2d8a4e"
RED     = "#e94560"
AMBER   = "#996600"
DARK    = "#1a1a2a"
GREY    = "#666666"
MIDGREY = "#555566"

# ── Module-level state ────────────────────────────────────────────────────────
_base_trades     = []        # enriched trades for selected strategy
_filtered_trades = []        # trades after current filters
_strategy_var    = None
_strategies      = []
# WHY: _get_selected_index() used to match by label string, which breaks when
#      labels gain/lose the ⚠️ stale prefix between selection and lookup.
#      _selected_strat_iid stores the Treeview row iid directly (e.g. "saved_1"
#      or "0") so the index is always available regardless of label changes.
# CHANGED: April 2026 — iid-based selection
_selected_strat_iid = None
# WHY: Background trade-loading thread races with instant saved-rule loads.
#      Clicking a backtest row starts a thread; immediately clicking a saved
#      rule clears _base_trades, but the thread finishes later and overwrites
#      with stale data. Token prevents stale writes: thread only commits if
#      its token still matches the current one.
# CHANGED: April 2026 — load token to cancel stale background loads
_load_token = 0

# Filter vars
_min_hold_var    = None
_max_hold_var    = None
_max_per_day_var = None
_cooldown_var    = None
_session_vars    = {}        # "Asian/London/New York" -> BooleanVar
_day_vars        = {}        # "Mon".."Fri" -> BooleanVar
_custom_filters  = []        # list of {feature, operator, value}

# Widgets
_strat_info_lbl   = None
_rule_info_lbl    = None
_base_stats_frame = None
_eval_info_lbl    = None
_impact_labels    = {}       # filter_name -> tk.Label for impact text
_results_card     = None
_trade_list_frame = None
_monthly_chart_canvas = None
_monthly_tooltip      = None
_dd_label             = None
_breach_label         = None
_opt_progress_frame = None
_opt_results_frame  = None
_opt_live_labels    = {}
_opt_status_lbl     = None
_opt_start_btn      = None
_opt_stop_btn       = None
_opt_worker_running = False   # guard against double-start
_scroll_canvas      = None

_update_pending = False   # debounce flag

# Optimizer lock vars (set in build_panel)
_lock_entry_var   = None
_lock_exit_var    = None
_lock_sltp_var    = None
_lock_filters_var = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Cache to prevent reloading 43MB file every time panel is shown
_strategies_cache = []
_cache_mtime = 0


# WHY: Diagnose "No Trades" lookup failures by simulating the refiner's
#      trade lookup for every matrix row and reporting which keys are missing.
# CHANGED: April 2026 — lookup diagnostic tool
def _run_lookup_diagnostic():
    """Generate a comprehensive diagnostic report for refiner trade lookup failures."""
    global _strat_info_lbl

    # WHY: Import BACKTEST_MATRIX_PATH to locate output directory and trades files
    # CHANGED: April 2026 — diagnostic imports
    try:
        from project2_backtesting.strategy_refiner import BACKTEST_MATRIX_PATH
    except ImportError:
        messagebox.showerror("Import Error", "Cannot import BACKTEST_MATRIX_PATH")
        return

    import json
    import glob
    from datetime import datetime

    matrix_dir = os.path.dirname(BACKTEST_MATRIX_PATH)
    output_path = os.path.join(matrix_dir, 'refiner_lookup_diagnostic.txt')

    # WHY: Run heavy diagnostic work on background thread to prevent UI freeze
    # CHANGED: April 2026 — threaded diagnostic
    def _do_diagnostic():
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("=" * 79 + "\n")
                f.write("REFINER LOOKUP DIAGNOSTIC\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 79 + "\n\n")

                # [1] MATRIX FILE
                f.write("[1] MATRIX FILE\n")
                try:
                    f.write(f"    Path:          {BACKTEST_MATRIX_PATH}\n")
                    f.write(f"    Exists:        {os.path.exists(BACKTEST_MATRIX_PATH)}\n")

                    if not os.path.exists(BACKTEST_MATRIX_PATH):
                        f.write("    ERROR: Matrix file does not exist\n\n")
                        if state.window:
                            state.window.after(0, lambda: messagebox.showwarning(
                                "Diagnostic Complete", "Matrix file not found.\n\nSee report for details."))
                        return

                    file_size = os.path.getsize(BACKTEST_MATRIX_PATH)
                    f.write(f"    Size:          {file_size} bytes\n")

                    if file_size == 0:
                        f.write("    ERROR: Matrix file is empty\n\n")
                        if state.window:
                            state.window.after(0, lambda: messagebox.showwarning(
                                "Diagnostic Complete", "Matrix file is empty.\n\nSee report for details."))
                        return

                    mtime = datetime.fromtimestamp(os.path.getmtime(BACKTEST_MATRIX_PATH))
                    f.write(f"    Modified:      {mtime.strftime('%Y-%m-%d %H:%M:%S')}\n")

                    # Check for LFS pointer
                    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as mf:
                        first_line = mf.readline()
                        is_lfs = first_line.startswith('version https://git-lfs.github.com/spec/v1')
                        f.write(f"    Is LFS ptr:    {is_lfs}\n")

                        if is_lfs:
                            f.write("\n    ERROR: Matrix is an LFS pointer. Run 'git lfs pull'\n\n")
                            if state.window:
                                state.window.after(0, lambda: messagebox.showwarning(
                                    "Diagnostic Complete",
                                    "Matrix is an LFS pointer.\n\nRun: git lfs pull\n\nSee report for details."))
                            return

                    # Load matrix
                    with open(BACKTEST_MATRIX_PATH, 'r', encoding='utf-8') as mf:
                        matrix_data = json.load(mf)

                    # Determine top-level key
                    if 'results' in matrix_data:
                        top_key = 'results'
                        all_results = matrix_data['results']
                    elif 'matrix' in matrix_data:
                        top_key = 'matrix'
                        all_results = matrix_data['matrix']
                    else:
                        f.write("    ERROR: No 'results' or 'matrix' key found\n\n")
                        return

                    f.write(f"    Top-level key: '{top_key}'\n")
                    f.write(f"    Row count:     {len(all_results)}\n")
                    f.write(f"    entry_timeframe field: {matrix_data.get('entry_timeframe', '<not set>')}\n")
                    f.write(f"    tested_timeframes:     {matrix_data.get('tested_timeframes', '<not set>')}\n")

                    # TF distribution
                    tf_counts = {}
                    rows_with_tt_gt_0 = 0
                    rows_with_tc_gt_0 = 0
                    rows_with_either_gt_0 = 0

                    for row in all_results:
                        try:
                            etf = row.get('entry_tf', row.get('entry_timeframe', ''))
                            if not etf:
                                etf = '<blank>'
                            tf_counts[etf] = tf_counts.get(etf, 0) + 1

                            tt = row.get('total_trades', 0)
                            tc = row.get('trade_count', 0)
                            if tt > 0:
                                rows_with_tt_gt_0 += 1
                            if tc > 0:
                                rows_with_tc_gt_0 += 1
                            if tt > 0 or tc > 0:
                                rows_with_either_gt_0 += 1
                        except Exception as e:
                            f.write(f"    ERROR processing row: {e}\n")

                    f.write("    entry_tf distribution in rows:\n")
                    for etf in sorted(tf_counts.keys()):
                        f.write(f"        {etf:12} : {tf_counts[etf]}\n")

                    f.write(f"    Rows with total_trades>0: {rows_with_tt_gt_0}\n")
                    f.write(f"    Rows with trade_count>0:  {rows_with_tc_gt_0}\n")
                    f.write(f"    Rows with EITHER > 0:     {rows_with_either_gt_0}\n\n")

                except Exception as e:
                    f.write(f"    ERROR: {e}\n\n")
                    import traceback
                    traceback.print_exc()

                # [2] TRADES FILES
                f.write("[2] TRADES FILES\n")
                f.write(f"    Directory: {matrix_dir}\n")
                f.write("    Files matching backtest_trades_*.json:\n")

                trades_files = {}
                try:
                    for tf_file in glob.glob(os.path.join(matrix_dir, 'backtest_trades_*.json')):
                        try:
                            fname = os.path.basename(tf_file)
                            fsize = os.path.getsize(tf_file)
                            fmtime = datetime.fromtimestamp(os.path.getmtime(tf_file))

                            with open(tf_file, 'r', encoding='utf-8') as tf:
                                trades_data = json.load(tf)

                            key_list = list(trades_data.keys())
                            key_count = len(key_list)

                            # Determine key range
                            numeric_keys = []
                            for k in key_list:
                                try:
                                    numeric_keys.append(int(k))
                                except ValueError:
                                    pass

                            if numeric_keys:
                                key_range = f"{min(numeric_keys)}..{max(numeric_keys)}"
                            else:
                                key_range = "non-numeric"

                            f.write(f"        {fname:40} size={fsize:8}  keys={key_count:4}  "
                                   f"key_range={key_range:12}  mtime={fmtime.strftime('%Y-%m-%d %H:%M')}\n")

                            # Extract TF from filename
                            tf_name = fname.replace('backtest_trades_', '').replace('.json', '')
                            trades_files[tf_name] = trades_data
                        except Exception as e:
                            f.write(f"        ERROR reading {tf_file}: {e}\n")
                except Exception as e:
                    f.write(f"    ERROR: {e}\n")

                f.write("\n")

                # [3] PER-ROW LOOKUP SIMULATION
                f.write("[3] PER-ROW LOOKUP SIMULATION\n")
                f.write("    Columns (tab-separated):\n")
                f.write("    gidx\tentry_tf\trule_combo\texit_strategy\ttt\ttc\ttf_local_idx\t"
                       "primary_key_hit\tfb_direct_hit\tfb_rule_combo_hit\tfinal\n")

                loadable_count = 0
                not_loadable_count = 0
                bug_set = []  # rows with tt>0 or tc>0 but lookup returns NONE

                try:
                    for gidx, row in enumerate(all_results):
                        try:
                            etf = row.get('entry_tf', row.get('entry_timeframe', ''))
                            rc = row.get('rule_combo', '?')
                            es = row.get('exit_strategy', row.get('exit_name', '?'))
                            tt = row.get('total_trades', 0)
                            tc = row.get('trade_count', 0)

                            # Calculate tf_local_idx
                            tf_local_idx = sum(1 for i in range(gidx)
                                             if all_results[i].get('entry_tf', all_results[i].get('entry_timeframe', '')) == etf)

                            # Simulate lookup
                            primary_hit = "-"
                            fb_direct_hit = "-"
                            fb_rule_combo_hit = "-"
                            final = "NONE"

                            if etf in trades_files:
                                tdata = trades_files[etf]

                                # Primary key: tf_local_idx
                                if str(tf_local_idx) in tdata:
                                    primary_hit = "YES"
                                    trades_found = tdata[str(tf_local_idx)]
                                    if trades_found:
                                        final = f"OK({len(trades_found)} trades)"
                                        loadable_count += 1
                                    else:
                                        final = "NONE"
                                        not_loadable_count += 1
                                else:
                                    primary_hit = "NO"

                                    # Fallback 1: direct gidx
                                    if str(gidx) in tdata:
                                        fb_direct_hit = "YES"
                                        trades_found = tdata[str(gidx)]
                                        if trades_found:
                                            final = f"OK({len(trades_found)} trades)"
                                            loadable_count += 1
                                        else:
                                            final = "NONE"
                                            not_loadable_count += 1
                                    else:
                                        fb_direct_hit = "NO"
                                        fb_rule_combo_hit = "NO"
                                        not_loadable_count += 1
                            else:
                                primary_hit = "NO(file missing)"
                                not_loadable_count += 1

                            # Track bug set
                            if (tt > 0 or tc > 0) and final == "NONE":
                                bug_set.append({
                                    'gidx': gidx, 'etf': etf, 'rc': rc, 'es': es,
                                    'tt': tt, 'tc': tc, 'tf_local_idx': tf_local_idx,
                                    'primary_hit': primary_hit, 'row': row
                                })

                            f.write(f"    {gidx}\t{etf}\t{rc[:30]}\t{es[:20]}\t{tt}\t{tc}\t{tf_local_idx}\t"
                                   f"{primary_hit}\t{fb_direct_hit}\t{fb_rule_combo_hit}\t{final}\n")

                        except Exception as e:
                            f.write(f"    {gidx}\tERROR\t{e}\n")
                except Exception as e:
                    f.write(f"    ERROR: {e}\n")

                f.write("\n")

                # [4] SUMMARY
                f.write("[4] SUMMARY\n")
                f.write(f"    Rows the refiner CAN load:    {loadable_count}\n")
                f.write(f"    Rows the refiner CANNOT load: {not_loadable_count}\n")
                bug_count = len(bug_set)
                expected_none = not_loadable_count - bug_count
                f.write(f"      ...of which have tt>0 or tc>0 (THE BUG SET): {bug_count}\n")
                f.write(f"      ...of which truly have no trades (expected NONE): {expected_none}\n\n")

                # [5] BUG SET
                f.write("[5] BUG SET — first 20 rows where we expect trades but lookup returns NONE\n")
                f.write("    gidx\tentry_tf\trule_combo\texit_strategy\ttt\ttc\ttf_local_idx\twhy_primary_failed\n")

                for bug in bug_set[:20]:
                    why = ""
                    if 'file missing' in bug['primary_hit']:
                        why = "trades_file_missing"
                    elif bug['etf'] == '<blank>':
                        why = "entry_tf_blank"
                    elif bug['primary_hit'] == "NO":
                        why = "key_missing"
                    else:
                        why = "unknown"

                    f.write(f"    {bug['gidx']}\t{bug['etf']}\t{bug['rc'][:30]}\t{bug['es'][:20]}\t"
                           f"{bug['tt']}\t{bug['tc']}\t{bug['tf_local_idx']}\t{why}\n")

                f.write("\n")

                # [6] REPRODUCIBLE SAMPLE
                f.write("[6] REPRODUCIBLE SAMPLE\n")
                if bug_set:
                    first_bug = bug_set[0]
                    f.write("    For the FIRST bug-set row:\n")
                    f.write(f"    gidx: {first_bug['gidx']}\n")
                    f.write(f"    entry_tf: {first_bug['etf']}\n")
                    f.write(f"    tf_local_idx: {first_bug['tf_local_idx']}\n")
                    f.write(f"    rule_combo: {first_bug['rc']}\n")
                    f.write(f"    exit_strategy: {first_bug['es']}\n")
                    f.write(f"    total_trades: {first_bug['tt']}\n")
                    f.write(f"    Matrix row keys (first 30, sorted):\n")
                    row_keys = sorted(list(first_bug['row'].keys()))[:30]
                    f.write(f"        {', '.join(row_keys)}\n")

                    if first_bug['etf'] in trades_files:
                        tdata = trades_files[first_bug['etf']]
                        numeric_keys = sorted([int(k) for k in tdata.keys() if k.isdigit()])
                        f.write(f"    Trades file keys (numeric, first 10 + last 10):\n")
                        if len(numeric_keys) > 20:
                            f.write(f"        First 10: {numeric_keys[:10]}\n")
                            f.write(f"        Last 10:  {numeric_keys[-10:]}\n")
                        else:
                            f.write(f"        All: {numeric_keys}\n")

                        f.write(f"    Trades at keys around tf_local_idx={first_bug['tf_local_idx']}:\n")
                        for offset in [-1, 0, 1]:
                            check_idx = first_bug['tf_local_idx'] + offset
                            if str(check_idx) in tdata:
                                f.write(f"        [{check_idx}]: {len(tdata[str(check_idx)])} trades\n")
                            else:
                                f.write(f"        [{check_idx}]: MISSING\n")

                        f.write(f"    Matrix rows at positions around tf_local_idx={first_bug['tf_local_idx']}:\n")
                        for offset in [-1, 0, 1]:
                            check_gidx = first_bug['tf_local_idx'] + offset
                            if 0 <= check_gidx < len(all_results):
                                r = all_results[check_gidx]
                                f.write(f"        [{check_gidx}]: rc={r.get('rule_combo', '?')[:30]} "
                                       f"es={r.get('exit_strategy', '?')[:20]} "
                                       f"etf={r.get('entry_tf', '?')} tt={r.get('total_trades', 0)}\n")
                else:
                    f.write("    No bug-set rows found.\n")

                f.write("\n" + "=" * 79 + "\n")

            # Show summary dialog
            if state.window:
                summary = (f"Matrix rows: {len(all_results)}\n"
                          f"Expected-has-trades rows: {rows_with_either_gt_0}\n"
                          f"Refiner CAN load: {loadable_count}\n"
                          f"Refiner CANNOT load (BUG): {len(bug_set)}\n\n"
                          f"Full report written to:\n{output_path}\n\n"
                          f"Paste the contents into your Claude chat to continue the fix.")
                state.window.after(0, lambda: messagebox.showinfo("Diagnostic Complete", summary))
                state.window.after(0, lambda: _strat_info_lbl.configure(text="✓ Diagnostic complete", fg=GREEN))

        except Exception as e:
            import traceback
            traceback.print_exc()
            if state.window:
                state.window.after(0, lambda: messagebox.showerror("Diagnostic Error", str(e)))

    # Update UI and start background thread
    if _strat_info_lbl:
        _strat_info_lbl.configure(text="⏳ Running diagnostic...", fg=AMBER)
    threading.Thread(target=_do_diagnostic, daemon=True).start()


def _build_eval_settings(firm_name, stage, proj_root):
    """Return the firm's eval phase parameters as a dict to embed in saved rules.

    WHY: Freezing eval params (target%, DD limits, max days, DD type) at save
    time means the rule is self-contained. Displaying the eval simulation later
    uses the exact parameters the rule was saved for — not whatever firm happens
    to be selected in the UI at that moment.
    max_calendar_days=None means unlimited (e.g. Get Leveraged).
    CHANGED: April 2026 — freeze eval params into saved rule
    """
    if not firm_name:
        return {}
    try:
        import glob as _ev_glob, json as _ev_json, os as _ev_os
        _prop_dir = _ev_os.path.join(proj_root, 'prop_firms')
        for _fp in _ev_glob.glob(_ev_os.path.join(_prop_dir, '*.json')):
            with open(_fp, encoding='utf-8') as _ff:
                _fd = _ev_json.load(_ff)
            if _fd.get('firm_name') != firm_name:
                continue
            _ch = _fd.get('challenges', [{}])[0]
            # Pick the phase matching stage ('evaluation' or 'funded')
            _phases = _ch.get('phases', [{}])
            _stage_l = stage.lower()
            _ph = next(
                (p for p in _phases
                 if _stage_l in str(p.get('phase_name', '')).lower()),
                _phases[0] if _phases else {}
            )
            return {
                'firm_name':         firm_name,
                'stage':             stage,
                'target_pct':        _ph.get('profit_target_pct', 6.0),
                'dd_total_pct':      _ph.get('max_total_drawdown_pct', 6.0),
                'dd_daily_pct':      _ph.get('max_daily_drawdown_pct', 5.0),
                'max_calendar_days': _ph.get('max_calendar_days'),   # None = unlimited
                'dd_type':           _ph.get('drawdown_type', 'static'),
            }
    except Exception:
        pass
    return {}


def _load_strategies(force=False):
    """Load strategy list from backtest matrix + saved rules.

    WHY: The dropdown needs to show all available strategies:
      1. Backtest results (from Run Backtest)
      2. Optimizer results (if any)
      3. Saved rules (from Save button in refiner/optimizer)
    If any source fails, the others still load (handled inside load_strategy_list).

    The cache is keyed on backtest_matrix.json mtime to avoid re-parsing the
    44MB file on every panel switch. But changes that don't touch that file
    (e.g. star/unstar a strategy, which writes to shared.starred storage)
    are invisible to the cache. Pass force=True to bypass the cache and
    re-run load_strategy_list, which re-applies the star sort.

    CHANGED: April 2026 — always call load_strategy_list so saved rules load
             even when backtest_matrix.json doesn't exist.
    CHANGED: April 2026 — force parameter for star toggle refresh
    """
    global _strategies, _strategies_cache, _cache_mtime
    try:
        backtest_path = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
        # WHY (Phase A.48 fix): Check BOTH backtest_matrix.json AND
        #      saved_rules.json mtimes. Saving a new rule changes
        #      saved_rules.json but not the matrix — without this,
        #      the cache doesn't invalidate and the new rule doesn't
        #      appear in the dropdown until restart.
        # CHANGED: April 2026 — Phase A.48 fix
        saved_path = os.path.join(project_root, 'saved_rules.json')
        current_mtime = 0
        if os.path.exists(backtest_path):
            current_mtime += os.path.getmtime(backtest_path)
        if os.path.exists(saved_path):
            current_mtime += os.path.getmtime(saved_path)
        if not force and current_mtime == _cache_mtime and _strategies_cache:
            _strategies = _strategies_cache
            return
        _cache_mtime = current_mtime

        from project2_backtesting.strategy_refiner import load_strategy_list
        _strategies = load_strategy_list()
        _strategies_cache = _strategies
    except Exception as e:
        print(f"[refiner_panel] Error loading strategies: {e}")
        import traceback; traceback.print_exc()
        _strategies = []


def _get_selected_index():
    # Primary: use the iid stored on last Treeview click — immune to label changes.
    if _selected_strat_iid is not None:
        # Backtest rows have numeric iids stored as strings ("0", "1", ...).
        try:
            return int(_selected_strat_iid)
        except (ValueError, TypeError):
            pass
        return _selected_strat_iid  # "saved_N", "optimizer_latest", etc.
    # Fallback: label matching (initial load before any row is clicked).
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    if '───' in val:
        return None  # separator, not a real selection
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _load_selected_strategy(silent=False):
    """Load the selected strategy's trades for filtering/optimizing.

    silent=True suppresses informational popups (used by auto-load on select).

    WHY: Different strategy sources need different loading:
      - Backtest results (int index) → load trades from matrix directly
      - Optimizer results ('optimizer_latest') → load from optimizer output
      - Saved rules ('saved_X') → MATCH by rule_combo + exit_strategy against
        the matrix, then load those trades. Saved rules don't store trades,
        but they remember which backtest result they came from.

    CHANGED: April 2026 — saved rules match to matrix by name
    """
    global _base_trades, _filtered_trades, _load_token
    # Increment token so any in-flight background thread knows it's stale.
    _load_token += 1
    my_token = _load_token

    idx = _get_selected_index()
    if idx is None:
        return

    # ── Saved rule: find matching strategy in backtest matrix ─────────────
    # WHY: Saved rules store rule_combo + exit_strategy but no trades.
    #      We search the matrix for a strategy with the same name and load
    #      its trades. This way the user can save a strategy, come back later,
    #      and load it without re-running the backtest.
    # CHANGED: April 2026 — match saved rules to matrix
    if isinstance(idx, str) and idx.startswith('saved_'):
        saved_rule = None
        is_stale_rule = False
        stale_issues_list = []
        for s in _strategies:
            if s.get('index') == idx:
                saved_rule = s.get('saved_rule', {})
                is_stale_rule = s.get('is_stale', False)
                stale_issues_list = s.get('stale_issues', [])
                break

        if not saved_rule:
            messagebox.showwarning("No Data", "Saved rule data not found.")
            return

        # ── Stale saved rule warning ──────────────────────────────────────────
        # WHY: Saved rules from before the fixes are missing exit_class, filters,
        #      entry_timeframe. The user needs to know so they can re-save.
        # CHANGED: April 2026 — stale saved rule detection
        if is_stale_rule and stale_issues_list:
            issues_text = '\n  • '.join([''] + stale_issues_list)
            messagebox.showinfo(
                "Stale Saved Rule",
                f"⚠️ This saved rule is missing some data:\n{issues_text}\n\n"
                f"To fix: make any change in the Refiner, then click Save again.\n"
                f"The new save will capture all fields correctly.",
            )

        # Try to match against backtest matrix
        rule_combo = saved_rule.get('rule_combo', '')
        exit_strategy = saved_rule.get('exit_strategy', '')
        exit_name = saved_rule.get('exit_name', '')

        # WHY: ALWAYS use saved rule's conditions for display, not the
        #      matched matrix entry. The matrix match is only for loading
        #      trades. Without this, a fuzzy match shows wrong conditions.
        # CHANGED: April 2026 — saved rule is source of truth
        for _sr_s in _strategies:
            if _sr_s.get('index') == idx:
                global _loaded_row
                _loaded_row = _sr_s
                break

        matched_idx = None

        # WHY: Match priority matters. Multiple backtest entries can share the
        #      same feature-name set (BUY-only vs BUY+SELL-combined both use
        #      features {X, Y, Z}). Old code tried feature-set first and hit
        #      the wrong entry. The rule_combo is the unique name recorded at
        #      save time — always try it first.
        # CHANGED: April 2026 — rule_combo-first matching

        _saved_exit = exit_name or exit_strategy or ''

        # ── Pass 1: exact rule_combo + exit_name (most specific) ─────────────
        if rule_combo:
            for s in _strategies:
                if s.get('source') != 'backtest':
                    continue
                _m_combo = s.get('rule_combo', '')
                _m_exit  = s.get('exit_name', s.get('exit_strategy', ''))
                if _m_combo == rule_combo:
                    if _saved_exit and _saved_exit not in ('?', 'Default', ''):
                        if _m_exit == _saved_exit:
                            matched_idx = s.get('index')
                            break
                    else:
                        # No exit info saved — combo name alone is enough
                        matched_idx = s.get('index')
                        break

        # ── Pass 1b: check INNER rules' rule_combo ───────────────────────────
        # WHY: The top-level rule_combo in the matrix has been auto-generated
        #      to a format like "#31_BUY_H1_2c_cf54_Trailing_Sto_2f57", but
        #      the INNER rules still carry the original name like "Rule 1 (BUY)".
        #      Saved rules use the original name. Match against inner rules too.
        # CHANGED: April 2026 — match inner rules' rule_combo
        if matched_idx is None and rule_combo:
            for s in _strategies:
                if s.get('source') != 'backtest':
                    continue
                _m_exit = s.get('exit_name', s.get('exit_strategy', ''))
                # Check inner rules for matching rule_combo
                for _inner_rule in s.get('rules', []):
                    _inner_combo = _inner_rule.get('rule_combo', '')
                    if _inner_combo == rule_combo:
                        if _saved_exit and _saved_exit not in ('?', 'Default', ''):
                            if _m_exit == _saved_exit:
                                matched_idx = s.get('index')
                                break
                        else:
                            matched_idx = s.get('index')
                            break
                if matched_idx is not None:
                    break

        # ── Pass 2: feature-set + exit (fallback when combo name changed) ────
        # WHY: Backtest may have been re-run with a different combo naming
        #      convention. If the combo name no longer exists in the matrix,
        #      fall back to matching by condition features + exit.
        #      Still require BOTH to match — features alone hits wrong combos.
        if matched_idx is None:
            _saved_conds = set(c.get('feature', '')
                               for c in saved_rule.get('conditions', []))
            if _saved_conds:
                for s in _strategies:
                    if s.get('source') != 'backtest':
                        continue
                    _m_rules = s.get('rules', [])
                    _m_conds = set(c.get('feature', '')
                                   for _mr in _m_rules
                                   for c in _mr.get('conditions', []))
                    _m_exit  = s.get('exit_name', s.get('exit_strategy', ''))
                    if _m_conds != _saved_conds:
                        continue
                    if _saved_exit and _saved_exit not in ('?', 'Default', ''):
                        if _m_exit == _saved_exit:
                            matched_idx = s.get('index')
                            break
                    else:
                        matched_idx = s.get('index')
                        break

        # WHY: Diagnostic log so user can check which entry was matched.
        # CHANGED: April 2026 — match diagnostic
        if matched_idx is not None:
            _matched_combo = ''
            for _ms in _strategies:
                if _ms.get('index') == matched_idx:
                    _matched_combo = _ms.get('rule_combo', '?')
                    break
            print(f"[REFINER] Saved rule '{rule_combo}' → matched matrix entry '{_matched_combo}' (idx={matched_idx})")
        else:
            print(f"[REFINER] Saved rule '{rule_combo}' → NO MATCH in matrix")

        # WHY: Helper to load trades from the saved rule's embedded data.
        #      Saved rules can carry their own trades array (saved at bookmark
        #      time). This is the most reliable source — it's the exact trades
        #      that were displayed when the user clicked Save. Use this when:
        #        1. Matrix match found but backtest_trades file has no data
        #        2. No matrix match at all (matrix was re-run or cleared)
        # CHANGED: April 2026 — use embedded trades from saved rule
        def _try_load_embedded_trades():
            """Try to load trades from the saved rule's own embedded data.
            Returns True if trades were loaded, False otherwise."""
            _embedded = saved_rule.get('trades', [])
            if not _embedded:
                return False

            print(f"[REFINER] Loading {len(_embedded)} embedded trades from saved rule")
            _strat_info_lbl.configure(text=f"⏳ Loading {len(_embedded)} embedded trades...", fg="#e67e22")
            filters = saved_rule.get('filters_applied')

            def _do_load_embedded(_raw=_embedded, _filters=filters, _tok=my_token):
                global _base_trades, _filtered_trades
                try:
                    from project2_backtesting.strategy_refiner import enrich_trades
                    _enriched = enrich_trades(list(_raw))
                    if _load_token != _tok:
                        return
                    _base_trades = _enriched
                    _filtered_trades = list(_base_trades)
                    if _filters:
                        try:
                            from project2_backtesting.strategy_refiner import apply_filters
                            kept, _ = apply_filters(_base_trades, _filters)
                            _filtered_trades = list(kept)
                        except Exception:
                            pass
                    if state.window:
                        state.window.after(0, _update_strat_info)
                        state.window.after(50, _schedule_update)
                except Exception as _e:
                    import traceback; traceback.print_exc()
                    if state.window:
                        state.window.after(0, lambda: messagebox.showerror("Load Error", str(_e)))

            import threading
            threading.Thread(target=_do_load_embedded, daemon=True).start()
            return True

        if matched_idx is not None:
            # WHY: Found the matching backtest result — load its trades
            try:
                from project2_backtesting.strategy_refiner import (
                    load_trades_from_matrix, enrich_trades
                )
                # WHY (Phase A.48 fix): Pass entry_tf so the function
                #      can find the right per-TF trades file.
                _matched_tf = None
                for s in _strategies:
                    if s.get('index') == matched_idx and s.get('source') == 'backtest':
                        _matched_tf = s.get('entry_tf', '')
                        break
                raw = load_trades_from_matrix(matched_idx, entry_tf=_matched_tf)
                if raw:
                    _strat_info_lbl.configure(text="⏳ Loading trades...", fg="#e67e22")
                    filters = saved_rule.get('filters_applied')

                    def _do_load_saved(_raw=raw, _filters=filters, _tok=my_token):
                        global _base_trades, _filtered_trades
                        try:
                            _enriched = enrich_trades(list(_raw))
                            if _load_token != _tok:
                                return  # stale — a newer load was started
                            _base_trades = _enriched
                            _filtered_trades = list(_base_trades)
                            if _filters:
                                try:
                                    from project2_backtesting.strategy_refiner import apply_filters
                                    kept, _ = apply_filters(_base_trades, _filters)
                                    _filtered_trades = list(kept)
                                except Exception:
                                    pass
                            if state.window:
                                state.window.after(0, _update_strat_info)
                                state.window.after(50, _schedule_update)
                        except Exception as _e:
                            import traceback; traceback.print_exc()
                            if state.window:
                                state.window.after(0, lambda: messagebox.showerror("Load Error", str(_e)))

                    import threading
                    threading.Thread(target=_do_load_saved, daemon=True).start()
                    return
                else:
                    # WHY: Matrix match found but backtest_trades file is empty/missing.
                    #      Before showing "No Trades", try the saved rule's embedded trades.
                    # CHANGED: April 2026 — fallback to embedded trades
                    if _try_load_embedded_trades():
                        return
                    messagebox.showwarning("No Trades",
                        f"Matched strategy '{rule_combo}' but it has no trade data.\n\n"
                        "Re-run the backtest to generate trades.")
                    return
            except Exception as e:
                import traceback; traceback.print_exc()
                messagebox.showerror("Load Error", str(e))
                return
        else:
            # WHY: No matrix match — try the saved rule's embedded trades first.
            #      Rules saved after April 2026 carry their full trade list.
            #      Only show "No Trades" if the saved rule itself has no trades.
            # CHANGED: April 2026 — embedded trades before giving up
            if _try_load_embedded_trades():
                print(f"[REFINER] No matrix match for '{rule_combo}' — using embedded trades")
                return

            # No matrix match AND no embedded trades — load standalone
            print(f"[REFINER] No matrix match for '{rule_combo}' — loading standalone (no trades)")
            _base_trades = []
            _filtered_trades = []
            if state.window:
                state.window.after(0, _update_strat_info)
                state.window.after(50, _schedule_update)
            if not silent:
                messagebox.showinfo("Saved Rule — No Trades",
                    f"Loaded rule conditions and settings.\n\n"
                    f"No matching backtest found and no embedded trades.\n"
                    f"Run backtest to see trades, or re-save the rule from View Results.")
            return

    # ── Normal strategy loading (backtest result or optimizer) ────────────
    # WHY: load_trades_from_matrix reads large JSON files — blocks UI for seconds.
    #      Run on background thread; update UI on main thread via after().
    # CHANGED: April 2026 — threaded loading to prevent UI freeze
    _strat_info_lbl.configure(text="⏳ Loading trades...", fg="#e67e22")

    # Capture tf before entering thread
    _sel_tf = None
    for s in _strategies:
        if s.get('index') == idx:
            _sel_tf = s.get('entry_tf', '')
            break

    def _do_load(_tok=my_token):
        global _base_trades, _filtered_trades
        try:
            from project2_backtesting.strategy_refiner import (
                load_trades_from_matrix, enrich_trades
            )
            raw = load_trades_from_matrix(idx, entry_tf=_sel_tf)
            if _load_token != _tok:
                return  # stale — user clicked a different row
            if not raw:
                if state.window:
                    state.window.after(0, lambda: messagebox.showwarning(
                        "No Trades",
                        "This strategy has no trade data.\n\nRe-run the backtest first."
                    ))
                return
            _enriched = enrich_trades(list(raw))
            if _load_token != _tok:
                return  # stale — cancelled while enriching
            _base_trades = _enriched
            _filtered_trades = list(_base_trades)
            if state.window:
                state.window.after(0, _update_strat_info)
                state.window.after(50, _schedule_update)
        except Exception as e:
            import traceback; traceback.print_exc()
            if _load_token == _tok and state.window:
                state.window.after(0, lambda: messagebox.showerror("Load Error", str(e)))

    import threading
    threading.Thread(target=_do_load, daemon=True).start()


def _update_strat_info():
    global _strat_info_lbl, _rule_info_lbl
    if not _strat_info_lbl:
        return

    # WHY: When a saved rule loads standalone (no matrix match), _base_trades
    #      is empty. Still show rule info from _loaded_row instead of returning.
    # CHANGED: April 2026 — handle standalone saved rules
    if not _base_trades:
        # Show saved rule info if available
        _loaded_idx = _get_selected_index()
        _lr = None
        for _s in _strategies:
            if _s.get('index') == _loaded_idx:
                _lr = _s
                break
        if _lr and _lr.get('source') == 'saved':
            _sr = _lr.get('saved_rule', {})
            _sr_wr = _sr.get('win_rate', 0)
            _sr_pf = _sr.get('net_profit_factor', 0)
            _sr_pips = _sr.get('net_total_pips', 0)
            _sr_trades = _sr.get('total_trades', 0)
            _sr_exit = _sr.get('exit_name', _sr.get('exit_class', '?'))
            _sr_conds = _sr.get('conditions', [])
            _sr_dir = _sr.get('direction', '?')
            text = (f"💾 Saved rule: {_sr_dir} ({len(_sr_conds)}c) × {_sr_exit}  |  "
                    f"{_sr_trades} trades  |  WR {_sr_wr:.1f}%  |  PF {_sr_pf:.2f}  |  "
                    f"{_sr_pips:+,.0f} pips  |  ⚠ No backtest match — run backtest for trades")
            _strat_info_lbl.configure(text=text, fg="#e67e22")
        else:
            _strat_info_lbl.configure(text="No trades loaded", fg=MIDGREY)
        # Still auto-fill settings even without trades
    else:
        from project2_backtesting.strategy_refiner import compute_stats_summary
        s = compute_stats_summary(_base_trades)
        text = (f"{s['count']} trades  |  WR {s['win_rate']*100:.1f}%  |  PF {s.get('profit_factor', 0):.2f}  |  "
                f"avg {s['avg_pips']:+.1f} pips  |  {s['trades_per_day']:.1f}/day  |  "
                f"hold {s['avg_hold_minutes']:.0f}m  |  max DD {s['max_dd_pips']:.0f} pips")
        _strat_info_lbl.configure(text=text, fg=MIDGREY)

    # WHY: Auto-fill account/risk/stage from the loaded rule's settings
    #      so the optimizer uses the same values the rule was tested with.
    # CHANGED: April 2026 — auto-fill from rule
    try:
        _loaded_idx = _get_selected_index()
        _loaded_row = None
        for _s in _strategies:
            if _s.get('index') == _loaded_idx:
                _loaded_row = _s
                break

        if _loaded_row:
            # WHY: Rule data lives in different places depending on source:
            #      - Backtest results: run_settings, discovery_settings
            #      - Saved rules: saved_rule dict, top-level fields
            #      Check all sources with priority: saved_rule > run_settings > top-level.
            # CHANGED: April 2026 — check all data sources for auto-fill
            _rs = _loaded_row.get('run_settings', {})
            _ds = _loaded_row.get('discovery_settings', {})
            _sr = _loaded_row.get('saved_rule', {})
            _rsk = _sr.get('risk_settings', {})
            _rules_l = _loaded_row.get('rules', [])
            _r0 = (_rules_l[0] if isinstance(_rules_l, list) and _rules_l
                   and isinstance(_rules_l[0], dict) else {})

            # Account
            _rule_acct = (
                _rs.get('starting_capital', 0) or
                _sr.get('account_size', 0) or
                _r0.get('account_size', 0) or
                _rsk.get('account_size', 0) or
                _loaded_row.get('account_size', 0)
            )
            if _rule_acct and float(_rule_acct) > 0 and _acct_var:
                _acct_var.set(str(int(float(_rule_acct))))

            # Risk — rule first (single source of truth)
            _rule_risk = (
                _loaded_row.get('risk_pct', 0) or
                _sr.get('risk_pct', 0) or
                _r0.get('risk_pct', 0) or
                _rs.get('risk_pct', 0) or
                _rsk.get('risk_pct', 0) or
                _ds.get('prop_firm_risk_pct', 0)
            )
            if _rule_risk and float(_rule_risk) > 0 and _risk_var:
                _risk_var.set(str(float(_rule_risk)))

            # Stage
            _rule_stage = (
                _sr.get('prop_firm_stage', '') or
                _ds.get('prop_firm_stage', '') or
                _rsk.get('stage', '') or
                _loaded_row.get('prop_firm_stage', '')
            )
            if _rule_stage and _stage_var:
                _stage_var.set(_rule_stage)

            # Firm
            _rule_firm = (
                _sr.get('prop_firm_name', '') or
                _ds.get('prop_firm_name', '') or
                _loaded_row.get('prop_firm_name', '') or
                _loaded_row.get('firm_name', '') or
                _rsk.get('firm', '')
            )
            if _rule_firm and _opt_target_var:
                _matched = False
                try:
                    _firm_values = list(_opt_target_var.cget('values')) if hasattr(_opt_target_var, 'cget') else []
                except Exception:
                    _firm_values = []
                for _fv in _firm_values:
                    if str(_fv).strip() == _rule_firm.strip():
                        _opt_target_var.set(str(_fv))
                        _matched = True
                        break
                if not _matched:
                    for _fv in _firm_values:
                        if _rule_firm.lower() in str(_fv).lower():
                            _opt_target_var.set(str(_fv))
                            break

            print(f"[REFINER] Auto-filled from rule: account=${_rule_acct}, "
                  f"risk={_rule_risk}%, stage={_rule_stage}, firm={_rule_firm}")

            # WHY: Update the rule info label with values from the loaded rule.
            # CHANGED: April 2026 — rule info display
            try:
                _info_parts = []
                if _rule_firm:
                    _info_parts.append(f"Firm: {_rule_firm}")
                    for _fv in firm_options:
                        if _rule_firm.lower() in str(_fv).lower():
                            _opt_target_var.set(str(_fv))
                            break
                if _rule_stage:
                    _info_parts.append(f"Stage: {_rule_stage}")
                    _stage_var.set(_rule_stage)
                _r_risk = float(_rule_risk) if _rule_risk else 0
                if _r_risk > 0:
                    _info_parts.append(f"Risk: {_r_risk}%")
                _r_dd_d = (float(_sr.get('dd_daily_pct', 0) or 0) or
                           float(_r0.get('dd_daily_pct', 0) or 0) or
                           float(_rs.get('dd_daily_pct', 0) or 0) or
                           float(_rsk.get('dd_daily_pct', 0) or 0) or
                           float(_loaded_row.get('dd_daily_pct', 0) or 0))
                _r_dd_t = (float(_sr.get('dd_total_pct', 0) or 0) or
                           float(_r0.get('dd_total_pct', 0) or 0) or
                           float(_rs.get('dd_total_pct', 0) or 0) or
                           float(_rsk.get('dd_total_pct', 0) or 0) or
                           float(_loaded_row.get('dd_total_pct', 0) or 0))
                _r_lev = int(float(_sr.get('leverage', 0) or 0) or
                             float(_r0.get('leverage', 0) or 0) or
                             float(_rs.get('leverage', 0) or 0) or
                             float(_loaded_row.get('leverage', 0) or 0))
                if _r_dd_d > 0:
                    _info_parts.append(f"DD: {_r_dd_d}%/{_r_dd_t}%")
                if _r_lev > 0:
                    _info_parts.append(f"Leverage: 1:{_r_lev}")
                _r_acct = float(_rule_acct) if _rule_acct else 0
                if _r_acct > 0:
                    _info_parts.append(f"Account: ${int(_r_acct):,}")
                _r_min_hold = 0
                try:
                    _mh_firm2 = _sr.get('prop_firm_name', '') or _loaded_row.get('prop_firm_name', '')
                    if _mh_firm2:
                        import glob as _mh2_glob
                        _mh2_dir = os.path.join(project_root, 'prop_firms')
                        for _mh2_fp in _mh2_glob.glob(os.path.join(_mh2_dir, '*.json')):
                            import json as _mh2_json
                            with open(_mh2_fp, encoding='utf-8') as _mh2_f:
                                _mh2_fd = _mh2_json.load(_mh2_f)
                            if _mh2_fd.get('firm_name') == _mh_firm2:
                                _mh2_sec = int(_mh2_fd.get('challenges', [{}])[0].get('restrictions', {}).get('min_trade_duration_seconds', 0))
                                if _mh2_sec > 0:
                                    _r_min_hold = max(1, _mh2_sec // 60)
                                break
                except Exception:
                    pass
                if _r_min_hold > 0:
                    _info_parts.append(f"Min hold: {_r_min_hold}min")
                if _rule_info_lbl:
                    if _info_parts:
                        _rule_info_lbl.config(text="  |  ".join(_info_parts), fg="#333")
                    else:
                        _rule_info_lbl.config(text="No firm info in rule", fg="#999")
            except Exception as _ri_e:
                print(f"[REFINER] Rule info label error: {_ri_e}")
    except Exception as _e:
        print(f"[REFINER] Could not auto-fill from rule: {_e}")

    # WHY: Show eval pass rate for the loaded strategy so user knows
    #      how likely it is to pass an evaluation before optimizing.
    # CHANGED: April 2026 — eval simulation on load
    # FIXED: April 2026 — trailing DD, daily DD, calendar days, eval deadline
    global _eval_info_lbl
    if _eval_info_lbl and _base_trades and len(_base_trades) > 10:
        try:
            import pandas as _eval_pd
            from datetime import datetime as _eval_dt

            # ── Load firm/rule parameters ─────────────────────────────────
            _eval_acct         = 10000.0
            _eval_dd_total     = 6.0    # total DD limit (%)
            _eval_dd_daily     = 5.0    # daily DD limit (%)
            _eval_target_pct   = 6.0    # profit target (%)
            _eval_max_cal_days = 60     # max calendar days per eval window (default no-limit = 60)
            _eval_dd_type      = 'static'  # 'static' or 'trailing' / 'trailing_eod'
            _eval_risk         = 1.0
            _eval_pip_val      = 1.0
            _eval_sl           = 150.0
            try:
                _loaded_idx2 = _get_selected_index()
                for _s2 in _strategies:
                    if _s2.get('index') == _loaded_idx2:
                        _sr2 = _s2.get('saved_rule', {})
                        _rs2 = _s2.get('run_settings', {})

                        # ── Risk / account / pip params ────────────────────
                        _eval_acct    = float(_sr2.get('account_size', 0) or _rs2.get('starting_capital', 0) or _s2.get('account_size', 0) or 10000)
                        _eval_risk    = float(_sr2.get('risk_pct', 0) or _rs2.get('risk_pct', 0) or _s2.get('risk_pct', 0) or 1.0)
                        _eval_pip_val = float(_sr2.get('pip_value_per_lot', 0) or _rs2.get('pip_value_per_lot', 0) or 1.0)
                        _ep2 = (_sr2.get('exit_params') or _sr2.get('exit_strategy_params') or
                                _s2.get('exit_params') or _s2.get('exit_strategy_params') or {})
                        _eval_sl = float(
                            _sr2.get('sl_pips', 0) or _rs2.get('sl_pips', 0) or
                            _ep2.get('sl_pips', 0) or 0
                        )

                        # ── Eval scenario params ────────────────────────────
                        # WHY: Try embedded eval_settings first (frozen at save
                        #      time for this specific firm+stage). This ensures
                        #      the eval simulation always uses the correct
                        #      parameters for the firm the rule was designed for,
                        #      regardless of what is currently selected in the UI.
                        #      Fall back to re-reading the firm JSON only for old
                        #      rules saved before eval_settings was introduced.
                        # CHANGED: April 2026 — read frozen eval_settings from rule
                        _es = _sr2.get('eval_settings', {})
                        if _es:
                            _eval_target_pct   = float(_es.get('target_pct',   _eval_target_pct))
                            _eval_dd_total     = float(_es.get('dd_total_pct', _eval_dd_total))
                            _eval_dd_daily     = float(_es.get('dd_daily_pct', _eval_dd_daily))
                            _eval_dd_type      = _es.get('dd_type', _eval_dd_type)
                            _mcd = _es.get('max_calendar_days')  # None = unlimited (Get Leveraged)
                            _eval_max_cal_days = int(_mcd) if _mcd else 9999
                        else:
                            # Legacy fallback: read from firm JSON by name
                            _eval_dd_total = float(_sr2.get('dd_total_pct', 0) or _rs2.get('dd_total_pct', 0) or _s2.get('dd_total_pct', 0) or 6.0)
                            _eval_dd_daily = float(_sr2.get('dd_daily_pct', 0) or _rs2.get('dd_daily_pct', 0) or _s2.get('dd_daily_pct', 0) or 5.0)
                            _eval_firm_name = _sr2.get('prop_firm_name', '') or _rs2.get('firm_name', '')
                            if _eval_firm_name:
                                import glob
                                _prop_dir = os.path.join(project_root, 'prop_firms')
                                for _fp in glob.glob(os.path.join(_prop_dir, '*.json')):
                                    import json as _eval_json
                                    with open(_fp, encoding='utf-8') as _ff:
                                        _fd = _eval_json.load(_ff)
                                    if _fd.get('firm_name') == _eval_firm_name:
                                        _ph = _fd.get('challenges', [{}])[0].get('phases', [{}])[0]
                                        _eval_target_pct   = float(_ph.get('profit_target_pct', _eval_target_pct))
                                        _eval_dd_total     = float(_ph.get('max_total_drawdown_pct', _eval_dd_total))
                                        _eval_dd_daily     = float(_ph.get('max_daily_drawdown_pct', _eval_dd_daily))
                                        _mcd = _ph.get('max_calendar_days')
                                        _eval_max_cal_days = int(_mcd) if _mcd else 9999
                                        _eval_dd_type = _ph.get('drawdown_type', 'static')
                                        break
                        break
            except Exception:
                pass

            # ── Derive effective SL from actual trade data if not set ────
            # WHY: ATR-based exits have SL = sl_atr_mult × ATR (e.g. 300-600 pips),
            #      NOT a fixed 150. Using 150 gives 3×-4× too large a lot size,
            #      making daily P&L 3×-4× too high — min/avg/max appear 3× too fast.
            #      Best estimate: average |net_pips| of losing trades ≈ actual SL
            #      (losers hit their SL in the vast majority of cases).
            # CHANGED: April 2026 — derive SL from trade data
            if _eval_sl <= 0:
                _losers_pips = sorted([abs(t.get('net_pips', 0) or 0)
                                       for t in _base_trades
                                       if (t.get('net_pips', 0) or 0) < 0])
                if len(_losers_pips) >= 5:
                    # WHY: Mean is skewed by gap-open outliers (a trade stopped
                    #      at 3× normal SL inflates the average).
                    #      Median gives the typical SL the strategy actually uses.
                    _eval_sl = _losers_pips[len(_losers_pips) // 2]
                else:
                    _eval_sl = 150.0   # fallback if too few losing trades

            # ── Compute $ limits ─────────────────────────────────────────
            _eval_risk_dollars  = _eval_acct * (_eval_risk / 100)
            _eval_lot           = max(0.01, _eval_risk_dollars / (_eval_sl * _eval_pip_val)) if _eval_sl > 0 else 0.01
            _eval_dpp           = _eval_pip_val * _eval_lot   # $ per pip for calculated lot size
            _eval_target_dollars = _eval_acct * (_eval_target_pct / 100)
            _eval_dd_total_dollars = _eval_acct * (_eval_dd_total / 100)   # e.g. $600 for 6%
            _eval_dd_daily_dollars = _eval_acct * (_eval_dd_daily / 100)   # e.g. $500 for 5%

            # ── Pre-parse trade timestamps for speed ─────────────────────
            _trade_cache = []
            for _t in _base_trades:
                try:
                    _entry_ts = _eval_pd.to_datetime(_t.get('entry_time', ''))
                    _exit_ts  = _eval_pd.to_datetime(_t.get('exit_time') or _t.get('entry_time', ''))
                    _pips     = float(_t.get('net_pips', 0) or 0)
                    _trade_cache.append((_entry_ts, _exit_ts, _pips))
                except Exception:
                    continue

            # Collect all unique exit dates to drive the window loop
            _all_exit_dates = sorted(set(str(_ex.date()) for _, _ex, _ in _trade_cache))

            # ── Simulate rolling eval windows ────────────────────────────
            # Each window = one hypothetical eval attempt starting fresh on
            # a different date (every 7 exit-date slots), covering max_cal_days.
            # Only trades entered ON OR AFTER the window start count.
            _eval_days_to_target = []   # cal-days to pass for each passing window
            _window_results      = []   # True=pass, False=fail, one entry per window
            _total_windows       = 0

            for _si in range(0, len(_all_exit_dates), 7):
                if _si >= len(_all_exit_dates):
                    break
                _start_date_str = _all_exit_dates[_si]
                _start_dt       = _eval_dt.strptime(_start_date_str, '%Y-%m-%d')
                _total_windows += 1
                _win_passed     = False

                _win_daily = {}
                for _entry_ts, _exit_ts, _pips in _trade_cache:
                    if _entry_ts.date() < _start_dt.date():
                        continue
                    _exit_day = str(_exit_ts.date())
                    _win_daily[_exit_day] = _win_daily.get(_exit_day, 0) + _pips * _eval_dpp

                _running = 0.0
                _peak    = 0.0

                for _d in sorted(_win_daily.keys()):
                    _cur_dt   = _eval_dt.strptime(_d, '%Y-%m-%d')
                    _cal_days = (_cur_dt - _start_dt).days + 1
                    if _cal_days > _eval_max_cal_days:
                        break
                    _day_pnl  = _win_daily[_d]
                    _running += _day_pnl
                    _peak     = max(_peak, _running)
                    if _running >= _eval_target_dollars:
                        _eval_days_to_target.append(_cal_days)
                        _win_passed = True
                        break
                    if 'trailing' in _eval_dd_type:
                        if _running <= max(-_eval_dd_total_dollars,
                                          _peak - _eval_dd_total_dollars):
                            break
                    else:
                        if _running <= -_eval_dd_total_dollars:
                            break
                    if _day_pnl <= -_eval_dd_daily_dollars:
                        break

                _window_results.append(_win_passed)

            # ── Overall period stats ──────────────────────────────────────
            _all_ts = ([str(_et.date()) for _et, _, _ in _trade_cache] +
                       [str(_xt.date()) for _, _xt, _ in _trade_cache])
            _period_start = min(_all_ts) if _all_ts else '?'
            _period_end   = max(_all_ts) if _all_ts else '?'
            _trading_days_n = len(set(str(_xt.date()) for _, _xt, _ in _trade_cache))
            if _period_start != '?' and _period_end != '?':
                _period_cal_days = (_eval_dt.strptime(_period_end,   '%Y-%m-%d') -
                                    _eval_dt.strptime(_period_start, '%Y-%m-%d')).days
                _period_years = _period_cal_days / 365.25
            else:
                _period_cal_days = 0
                _period_years    = 0

            # Failing-streak analysis: a streak = run of consecutive failed windows
            # Streaks at the start, between passes, and at the end are all counted.
            _fail_streaks = []
            _cur_fail = 0
            for _wr in _window_results:
                if not _wr:
                    _cur_fail += 1
                else:
                    _fail_streaks.append(_cur_fail)  # includes 0 (back-to-back passes)
                    _cur_fail = 0
            if _cur_fail > 0:
                _fail_streaks.append(_cur_fail)   # trailing failures after last pass

            # ── Build and show display ────────────────────────────────────
            _eval_passes = len(_eval_days_to_target)
            _eval_pr     = _eval_passes / _total_windows * 100 if _total_windows else 0

            # Period line
            _period_str = (
                f"Period: {_period_start} – {_period_end} "
                f"({_period_years:.1f} yrs, {_trading_days_n} trading days) | "
                f"Evals/year: {(_total_windows / _period_years * _eval_pr / 100):.1f}"
                if _period_years > 0 else "Period: insufficient data"
            )

            # Window explanation (always shown)
            _window_def = (
                f"A 'window' = 1 simulated eval attempt starting fresh on a "
                f"different date — {_total_windows} attempts tested across the "
                f"full backtest period, every 7 trading sessions apart"
            )

            if _eval_days_to_target and _total_windows > 0:
                _eval_avg = sum(_eval_days_to_target) / _eval_passes
                _eval_min = min(_eval_days_to_target)
                _eval_max = max(_eval_days_to_target)

                if _eval_passes == 1:
                    _days_line = (
                        f"Days to pass: {_eval_min}  "
                        f"[only 1 window passed — Min/Max/Avg are all the same value; "
                        f"need more trades for meaningful spread]"
                    )
                else:
                    _days_line = (
                        f"Avg: {_eval_avg:.0f} days | "
                        f"Min: {_eval_min} days | Max: {_eval_max} days"
                    )

                # Fail-streak lines
                if _fail_streaks:
                    _fs_max = max(_fail_streaks)
                    _fs_min = min(_fail_streaks)
                    _fs_avg = sum(_fail_streaks) / len(_fail_streaks)
                    _fs_note = ""
                    if _fs_min == 0:
                        _fs_note = " (0 = consecutive passes occurred)"
                    _fail_line = (
                        f"Fail streaks (windows failed in a row before next pass): "
                        f"Max={_fs_max} | Avg={_fs_avg:.1f} | Min={_fs_min}{_fs_note}"
                    )
                else:
                    _fail_line = "Fail streaks: n/a (no failures recorded)"

                _eval_text = (
                    f"Eval: {_eval_pr:.0f}% pass rate "
                    f"({_eval_passes}/{_total_windows} windows) | "
                    f"{_days_line} | "
                    f"Target: {_eval_target_pct}% (${_eval_target_dollars:,.0f})\n"
                    f"{_period_str}\n"
                    f"{_fail_line}\n"
                    f"{_window_def}"
                )
                _eval_info_lbl.configure(text=_eval_text, fg="#e65100")
            else:
                _fail_streak_len = len(_window_results)  # all windows failed
                _eval_text = (
                    f"Eval: 0% pass rate (0/{_total_windows} windows) — "
                    f"never reaches {_eval_target_pct}% (${_eval_target_dollars:,.0f}) "
                    f"within {_eval_max_cal_days} cal-days without DD breach\n"
                    f"{_period_str}\n"
                    f"Fail streaks: all {_total_windows} windows failed — "
                    f"strategy does not reach target under these parameters\n"
                    f"{_window_def}"
                )
                _eval_info_lbl.configure(text=_eval_text, fg="#dc3545")
        except Exception as _eval_e:
            print(f"[REFINER] Eval simulation error: {_eval_e}")
            _eval_info_lbl.configure(text="", fg="#999")


def _get_current_filters():
    """Build the filters dict from current UI values."""
    filters = {}

    try:
        v = float(_min_hold_var.get()) if _min_hold_var else 0
        if v > 0:
            filters['min_hold_minutes'] = v
    except Exception:
        pass

    try:
        v = float(_max_hold_var.get()) if _max_hold_var else 0
        if v > 0:
            filters['max_hold_minutes'] = v
    except Exception:
        pass

    try:
        v = int(_max_per_day_var.get()) if _max_per_day_var else 0
        if v > 0:
            filters['max_trades_per_day'] = v
    except Exception:
        pass


    try:
        v = float(_cooldown_var.get()) if _cooldown_var else 0
        if v > 0:
            filters['cooldown_minutes'] = v
    except Exception:
        pass

    sessions = [s for s, var in _session_vars.items() if var.get()]
    if len(sessions) < 3:
        filters['sessions'] = sessions

    days_all = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    days = [d for d, var in _day_vars.items() if var.get()]
    if len(days) < 5:
        filters['days'] = days

    if _custom_filters:
        filters['custom_filters'] = list(_custom_filters)

    return filters


def _schedule_update(event=None):
    """Debounce: schedule a stats update after a short delay."""
    global _update_pending
    if _update_pending:
        return
    _update_pending = True
    if state.window:
        state.window.after(150, _do_update)


def _do_update():
    global _update_pending, _filtered_trades
    _update_pending = False
    if not _base_trades:
        # WHY: When a saved rule loads standalone (_base_trades=[]), the chart
        #      from the previous backtest load remains visible because we used
        #      to return early here. Now we clear all displays explicitly so
        #      the user doesn't see stale trade data for the wrong strategy.
        # CHANGED: April 2026 — clear stale chart on empty trades
        _filtered_trades = []
        _update_results_card([], [])
        if _monthly_chart_canvas:
            _draw_monthly_chart(_monthly_chart_canvas, _monthly_tooltip, [])
        _update_drawdown_display([])
        _update_breach_display([])
        return
    try:
        from project2_backtesting.strategy_refiner import apply_filters, compute_stats_summary
        filters = _get_current_filters()
        kept, removed = apply_filters(_base_trades, filters)
        _filtered_trades = kept
        _update_results_card(kept, removed)
        # Update monthly chart and drawdown display
        if _monthly_chart_canvas:
            _draw_monthly_chart(_monthly_chart_canvas, _monthly_tooltip, kept)
        _update_drawdown_display(kept)
        _update_breach_display(kept)
    except Exception as e:
        print(f"[refiner_panel] update error: {e}")


def _update_results_card(kept, removed):
    global _results_card
    if _results_card is None:
        return
    try:
        from project2_backtesting.strategy_refiner import compute_stats_summary
        b = compute_stats_summary(_base_trades)
        a = compute_stats_summary(kept)
    except Exception:
        return

    for widget in _results_card.winfo_children():
        widget.destroy()

    def _col(parent, title, stats, color):
        f = tk.Frame(parent, bg=WHITE, padx=12, pady=8)
        f.pack(side=tk.LEFT, fill="both", expand=True)
        tk.Label(f, text=title, font=("Segoe UI", 9, "bold"),
                 bg=WHITE, fg=MIDGREY).pack(anchor="w", pady=(0, 4))
        rows = [
            ("Trades",       str(stats['count'])),
            ("Win Rate",     f"{stats['win_rate']*100:.1f}%"),
            ("Avg Pips",     f"{stats['avg_pips']:+.1f}"),
            ("Trades/Day",   f"{stats['trades_per_day']:.1f}"),
            ("Avg Hold",     f"{stats['avg_hold_minutes']:.0f}m"),
            ("Max DD",       f"{stats['max_dd_pips']:.0f} pips"),
            ("Total Pips",   f"{stats['total_pips']:+.0f}"),
        ]
        for label, val in rows:
            r = tk.Frame(f, bg=WHITE)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label + ":", font=("Segoe UI", 8),
                     bg=WHITE, fg=GREY, width=11, anchor="w").pack(side=tk.LEFT)
            tk.Label(r, text=val, font=("Segoe UI", 9, "bold"),
                     bg=WHITE, fg=color).pack(side=tk.LEFT)

    # Determine if after is better
    after_color = GREEN if a['avg_pips'] >= b['avg_pips'] else RED

    _col(_results_card, "BEFORE filters", b, MIDGREY)
    tk.Frame(_results_card, bg="#e0e0e0", width=1).pack(side=tk.LEFT, fill="y", padx=4)
    _col(_results_card, "AFTER filters", a, after_color)

    removed_n = len(removed)
    removed_net = sum(t.get('net_pips', 0) for t in removed)
    tk.Label(_results_card,
             text=f"Removed {removed_n} trades ({removed_net:+.0f} pips removed)",
             font=("Segoe UI", 8, "italic"), bg=WHITE, fg=GREY).pack(side=tk.LEFT, padx=8)


# ─────────────────────────────────────────────────────────────────────────────
# Trade list display
# ─────────────────────────────────────────────────────────────────────────────

def _display_trade_list(trades, parent):
    for widget in parent.winfo_children():
        widget.destroy()

    if not trades:
        tk.Label(parent, text="No trades after filters.",
                font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=10)
        return

    # Header
    hdr = tk.Frame(parent, bg="#f5f5f5", padx=8, pady=4)
    hdr.pack(fill="x", padx=5)
    cols = [("#",3),("Entry",17),("Exit",17),("Dir",5),("Entry$",7),
            ("Exit$",7),("Gross",7),("Spread",6),("Net",7),("Hold",8),("Reason",14)]
    for t, w in cols:
        tk.Label(hdr, text=t, font=("Segoe UI", 7, "bold"),
                bg="#f5f5f5", fg=GREY, width=w, anchor="w").pack(side=tk.LEFT, padx=1)

    MAX_ROWS = 50
    to_show = trades[:MAX_ROWS]
    total_net = 0.0
    winners = 0

    def _row(i, t):
        nonlocal total_net, winners
        net = t.get('net_pips', 0)
        total_net += net
        if net > 0:
            winners += 1
        row_bg = "#f0fdf4" if net > 0 else "#fef2f2"
        net_c = GREEN if net > 0 else RED
        dir_c = GREEN if t.get('direction') == 'BUY' else RED
        r = tk.Frame(parent, bg=row_bg, padx=8, pady=2)
        r.pack(fill="x", padx=5)
        vals = [
            (str(i),                            3,  GREY,   "Segoe UI",   False),
            (str(t.get('entry_time',''))[:16],  17, DARK,   "Consolas",   False),
            (str(t.get('exit_time', ''))[:16],  17, DARK,   "Consolas",   False),
            (t.get('direction',''),             5,  dir_c,  "Segoe UI",   True),
            (f"{t.get('entry_price',0):.2f}",   7,  DARK,   "Consolas",   False),
            (f"{t.get('exit_price', 0):.2f}",   7,  DARK,   "Consolas",   False),
            (f"{t.get('pnl_pips',0):+.1f}",     7,  MIDGREY,"Consolas",   False),
            (f"{t.get('cost_pips',0):.1f}",     6,  GREY,   "Consolas",   False),
            (f"{net:+.1f}",                     7,  net_c,  "Consolas",   True),
            (t.get('hold_display',''),           8,  GREY,   "Segoe UI",   False),
            (t.get('exit_reason',''),           14, MIDGREY,"Segoe UI",   False),
        ]
        for text, w, c, fn, bold in vals:
            tk.Label(r, text=text, font=(fn, 7, "bold" if bold else "normal"),
                    bg=row_bg, fg=c, width=w, anchor="w").pack(side=tk.LEFT, padx=1)

    for i, t in enumerate(to_show, 1):
        _row(i, t)

    # Count remaining for "show all" button
    remaining = len(trades) - MAX_ROWS

    if remaining > 0:
        def _show_rest(btn):
            btn.destroy()
            for i, t in enumerate(trades[MAX_ROWS:], MAX_ROWS + 1):
                _row(i, t)
            _footer()
        show_btn = tk.Button(parent, text=f"Show {remaining} more trades...",
                             bg="#667eea", fg="white", font=("Segoe UI", 8, "bold"),
                             relief=tk.FLAT, cursor="hand2", padx=10, pady=5)
        show_btn.configure(command=lambda b=show_btn: _show_rest(b))
        show_btn.pack(pady=6)
    else:
        _footer()

    def _footer():
        total = len(trades)
        wr = winners / max(total, 1) * 100
        foot = tk.Frame(parent, bg="#e8f4f8", padx=8, pady=6)
        foot.pack(fill="x", padx=5, pady=(4, 0))
        tk.Label(foot,
                 text=f"Total: {total} trades  |  Winners: {winners}  "
                      f"WR: {wr:.1f}%  |  Net: {total_net:+.1f} pips",
                 font=("Segoe UI", 9, "bold"), bg="#e8f4f8", fg=DARK).pack(anchor="w")


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

def _export_csv(trades=None):
    if trades is None:
        trades = _filtered_trades
    if not trades:
        messagebox.showinfo("No Trades", "No trades to export.")
        return
    fp = filedialog.asksaveasfilename(
        title="Export Trades CSV", defaultextension=".csv",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )
    if not fp:
        return
    fieldnames = ['#','entry_time','exit_time','direction','entry_price','exit_price',
                  'pnl_pips','cost_pips','net_pips','hold_minutes','hold_display',
                  'session','day_of_week','exit_reason','rule_id']
    try:
        with open(fp, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            w.writeheader()
            for i, t in enumerate(trades, 1):
                row = {'#': i, **t}
                w.writerow(row)
        messagebox.showinfo("Exported", f"Saved {len(trades)} trades to:\n{fp}")
    except Exception as e:
        messagebox.showerror("Export Error", str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Deep optimizer
# ─────────────────────────────────────────────────────────────────────────────

_opt_target_var    = None
_stage_var         = None
_opt_mode_var      = None
_acct_var          = None
_risk_var          = None


def _update_status(msg, error=False):
    """Thread-safe status label update."""
    color = RED if error else "#28a745"
    try:
        if state.window and state.window.winfo_exists():
            state.window.after(0, lambda: _opt_status_lbl.configure(text=msg, fg=color) if _opt_status_lbl else None)
    except Exception:
        pass


def _start_optimization():
    global _opt_start_btn, _opt_stop_btn, _opt_status_lbl, _opt_worker_running

    # Guard: reject if a worker is already running (e.g. user clicked Stop but
    # the old thread hasn't exited yet and then immediately clicked Start again).
    if _opt_worker_running:
        return

    # Disable button FIRST — before any checks that might fail
    try:
        if _opt_start_btn:
            _opt_start_btn.configure(state="disabled")
        if _opt_stop_btn:
            _opt_stop_btn.configure(state="normal")
    except Exception:
        pass

    _opt_worker_running = True

    if not _base_trades:
        messagebox.showerror("No Data", "Load a strategy first.")
        # Re-enable button since we're not starting
        if _opt_start_btn:
            _opt_start_btn.configure(state="normal")
        if _opt_stop_btn:
            _opt_stop_btn.configure(state="disabled")
        return

    if _opt_status_lbl:
        _opt_status_lbl.configure(text="Running...", fg=GREY)

    target_firm = _opt_target_var.get() if _opt_target_var else None
    if target_firm == "None — maximize pips":
        target_firm = None

    # Clear previous results
    if _opt_results_frame:
        for w in _opt_results_frame.winfo_children():
            w.destroy()

    def _cb(step, total, message, current_best=None, elapsed_str="",
            candidates_tested=0, improvements_found=0):
        """Update optimizer UI — called from background thread."""
        pct = int(step / max(total, 1) * 100)

        def _update():
            try:
                # Update status label
                if _opt_status_lbl:
                    try:
                        if _opt_status_lbl.winfo_exists():
                            _opt_status_lbl.configure(text=message, fg="#28a745")
                    except Exception:
                        pass

                # Update live labels — check each one individually
                if isinstance(_opt_live_labels, dict):
                    # Message/status
                    msg_lbl = _opt_live_labels.get('msg')
                    if msg_lbl:
                        try:
                            if msg_lbl.winfo_exists():
                                msg_lbl.configure(text=message)
                        except Exception:
                            pass

                    # Progress
                    progress_lbl = _opt_live_labels.get('progress')
                    if progress_lbl:
                        try:
                            if progress_lbl.winfo_exists():
                                progress_lbl.configure(text=f"Step {step}/{total}  ({pct}%)")
                        except Exception:
                            pass

                    # Best name
                    best_name_lbl = _opt_live_labels.get('best_name')
                    if best_name_lbl and current_best:
                        try:
                            if best_name_lbl.winfo_exists():
                                best_name_lbl.configure(text=current_best.get('name', '—'))
                        except Exception:
                            pass

                    # Best stats
                    best_stats_lbl = _opt_live_labels.get('best_stats')
                    if best_stats_lbl and current_best:
                        try:
                            if best_stats_lbl.winfo_exists():
                                best_stats_lbl.configure(
                                    text=f"{current_best.get('trades',0)} trades  |  "
                                         f"WR {current_best.get('win_rate',0)*100:.1f}%  |  "
                                         f"avg {current_best.get('avg_pips',0):+.1f} pips  |  "
                                         f"{current_best.get('trades_per_day',0):.1f}/day")
                        except Exception:
                            pass

                    # Counters/elapsed time
                    counters_lbl = _opt_live_labels.get('counters')
                    if counters_lbl:
                        try:
                            if counters_lbl.winfo_exists():
                                counters_lbl.configure(
                                    text=f"Tested: {candidates_tested}  |  "
                                         f"Improvements: {improvements_found}  |  "
                                         f"Elapsed: {elapsed_str}")
                        except Exception:
                            pass

            except Exception as e:
                print(f"[OPTIMIZER UI] Update error: {e}")

        # Schedule on main thread
        try:
            if state.window and state.window.winfo_exists():
                state.window.after(0, _update)
            else:
                print(f"[OPTIMIZER UI] Window not available")
        except Exception as e:
            print(f"[OPTIMIZER UI] after() error: {e}")

    def _worker():
        try:
            print("[OPTIMIZER] Worker thread started")
            current_trades = list(_base_trades)
            current_filters = _get_current_filters()
            print(f"[OPTIMIZER] Base trades: {len(current_trades)}, filters: {current_filters}")

            # WHY: Read spread from config. Old hardcoded 2.5 was wrong for XAUUSD.
            # CHANGED: April 2026 — config-driven spread default
            spread_pips = 25.0
            commission_pips = 0.0
            try:
                from project2_backtesting.panels.configuration import load_config as _opt_lc
                _opt_cfg = _opt_lc()
                spread_pips = float(_opt_cfg.get('spread', 25.0))
                commission_pips = float(_opt_cfg.get('commission', 0.0))
            except Exception:
                pass
            idx = _get_selected_index()
            selected_strategy_row = None
            if idx is not None:
                for s in _strategies:
                    if s['index'] == idx:
                        spread_pips = s.get('spread_pips', 25.0)
                        commission_pips = s.get('commission_pips', 0.0)
                        selected_strategy_row = s
                        break

            # WHY: Per-firm cost/exit parity with Run Backtest.
            # CHANGED: April 2026 — per-firm parity in optimizer
            from shared.firm_settings_resolver import resolve_firm_settings
            _opt_firm_name = ''
            _opt_symbol = 'XAUUSD'
            try:
                if selected_strategy_row:
                    _rs = selected_strategy_row.get('run_settings', {}) or {}
                    _opt_firm_name = (_rs.get('firm_name', '') or
                                      selected_strategy_row.get('prop_firm_name', '') or '')
                    _opt_symbol = (_rs.get('symbol', '') or
                                   selected_strategy_row.get('symbol', '') or 'XAUUSD')
            except Exception:
                pass
            _opt_firm = resolve_firm_settings(_opt_firm_name, _opt_symbol, use_config=True)
            if _opt_firm['firm_resolved'] and _opt_firm['spread_pips'] != spread_pips:
                spread_pips = _opt_firm['spread_pips']
            if _opt_firm['firm_resolved']:
                print(f"[OPTIMIZER] Per-firm settings: {_opt_firm_name} / {_opt_symbol} | "
                      f"spread={_opt_firm['spread_pips']:.1f} | "
                      f"max_spread={_opt_firm['max_spread_pips']:.1f} | "
                      f"hard_close={_opt_firm['hard_close_hour']}h | "
                      f"min_hold={_opt_firm['min_hold_minutes']}m | "
                      f"variable_spread={_opt_firm['variable_spread']}")

            all_candidates = []

            # Get stage and account size
            stage = _stage_var.get().lower() if _stage_var else "funded"
            account_size = float(_acct_var.get()) if _acct_var else 100000
            # WHY: Risk comes from the RULE (single source of truth).
            #      Rule carries margin-capped risk_pct from P1 config.
            #      UI is only a fallback for old rules without risk_pct.
            # CHANGED: April 2026 — risk from rule, not UI
            risk_pct = 0
            _risk_dd_source = "default"
            _cfg_dd_daily = 5.0
            _cfg_dd_total = 10.0
            if selected_strategy_row:
                _sr = selected_strategy_row.get('saved_rule', {})
                _r0 = {}
                _rules_list = selected_strategy_row.get('rules', [])
                if _rules_list and isinstance(_rules_list, list) and len(_rules_list) > 0:
                    _r0 = _rules_list[0] if isinstance(_rules_list[0], dict) else {}
                _rset = selected_strategy_row.get('run_settings', {})
                risk_pct = (
                    float(_sr.get('risk_pct', 0) or 0) or
                    float(_r0.get('risk_pct', 0) or 0) or
                    float(_rset.get('risk_pct', 0) or 0) or
                    float(selected_strategy_row.get('risk_pct', 0) or 0)
                )
                _cfg_dd_daily = (
                    float(_sr.get('dd_daily_pct', 0) or 0) or
                    float(_r0.get('dd_daily_pct', 0) or 0) or
                    float(_rset.get('dd_daily_pct', 0) or 0) or
                    float(selected_strategy_row.get('dd_daily_pct', 0) or 0)
                )
                _cfg_dd_total = (
                    float(_sr.get('dd_total_pct', 0) or 0) or
                    float(_r0.get('dd_total_pct', 0) or 0) or
                    float(_rset.get('dd_total_pct', 0) or 0) or
                    float(selected_strategy_row.get('dd_total_pct', 0) or 0)
                )
                if risk_pct > 0:
                    _risk_dd_source = "from rule"
            if risk_pct <= 0:
                try:
                    import importlib.util as _opt_rsk_ilu
                    _opt_rsk_path = os.path.join(project_root,
                        'project1_reverse_engineering', 'config_loader.py')
                    _opt_rsk_spec = _opt_rsk_ilu.spec_from_file_location('_opt_rsk', _opt_rsk_path)
                    _opt_rsk_mod = _opt_rsk_ilu.module_from_spec(_opt_rsk_spec)
                    _opt_rsk_spec.loader.exec_module(_opt_rsk_mod)
                    _opt_p1 = _opt_rsk_mod.load()
                    risk_pct = float(_opt_p1.get('risk_pct', 0))
                    if risk_pct > 0:
                        _risk_dd_source = "from P1 config"
                    if _cfg_dd_daily <= 0:
                        _cfg_dd_daily = float(_opt_p1.get('dd_daily_pct', 0))
                    if _cfg_dd_total <= 0:
                        _cfg_dd_total = float(_opt_p1.get('dd_total_pct', 0))
                except Exception:
                    pass
            if risk_pct <= 0:
                risk_pct = float(_risk_var.get()) if _risk_var else 1.0
                _risk_dd_source = "from UI (rule has no risk)"
            if _cfg_dd_daily <= 0:
                _cfg_dd_daily = 5.0
            if _cfg_dd_total <= 0:
                _cfg_dd_total = 10.0

            # Pass stage to presets for scoring
            from project2_backtesting.strategy_refiner import get_prop_firm_presets
            if target_firm and isinstance(target_firm, str):
                presets = get_prop_firm_presets()
                target_data = presets.get(target_firm, {})
                target_data['stage'] = stage
            elif target_firm and isinstance(target_firm, dict):
                target_data = target_firm
                target_data['stage'] = stage
            else:
                target_data = {'stage': stage}

            opt_mode = _opt_mode_var.get() if _opt_mode_var else "quick"
            print(f"[OPTIMIZER] Mode: {opt_mode}")

            # ── Extract rules from selected strategy ──────────────────────────────
            # WHY: The optimizer needs the base rules that generated the trades
            #      it's optimizing. Without them, _validator_optimized.json gets
            #      written with empty rules, and the validator can't run walk-forward.
            # CHANGED: April 2026 — extract rules from selected strategy
            base_strategy_rules = []
            if selected_strategy_row:
                # Try direct 'rules' field first (if saved in matrix)
                base_strategy_rules = selected_strategy_row.get('rules', [])
                if not base_strategy_rules or not any(r.get('conditions') for r in base_strategy_rules):
                    # Fallback: load from analysis_report.json using rule_combo
                    try:
                        report_path = os.path.join(project_root,
                            'project1_reverse_engineering', 'outputs', 'analysis_report.json')
                        with open(report_path, 'r', encoding='utf-8') as f:
                            report = json.load(f)
                        all_rules = [r for r in report.get('rules', []) if r.get('prediction') == 'WIN']

                        # Check for saved rule_indices
                        rule_indices = selected_strategy_row.get('rule_indices')
                        if rule_indices is not None:
                            base_strategy_rules = [all_rules[i] for i in rule_indices if i < len(all_rules)]
                        else:
                            # Parse rule_combo name
                            combo_name = selected_strategy_row.get('rule_combo', '')
                            if combo_name == 'All rules combined':
                                base_strategy_rules = all_rules
                            else:
                                import re
                                m = re.match(r'^Rule\s+(\d+)', combo_name)
                                if m:
                                    idx_r = int(m.group(1)) - 1
                                    if 0 <= idx_r < len(all_rules):
                                        base_strategy_rules = [all_rules[idx_r]]
                                else:
                                    m = re.match(r'^Top\s+(\d+)\s+rules', combo_name)
                                    if m:
                                        n = int(m.group(1))
                                        base_strategy_rules = all_rules[:n]
                    except Exception as e:
                        print(f"[OPTIMIZER] Could not load rules from analysis_report: {e}")

            if base_strategy_rules:
                print(f"[OPTIMIZER] Loaded {len(base_strategy_rules)} base rules from selected strategy")
            else:
                print(f"[OPTIMIZER] WARNING: No base rules found — optimizer results won't be validatable")

            # ── Leverage / contract size for margin-aware optimization ──
            # WHY: Read leverage from the rule first (it was tested with this).
            #      Fall back to firm dropdown lookup if not in rule.
            # CHANGED: April 2026 — rule leverage takes priority
            _opt_leverage = 0
            _opt_contract = 100.0
            if selected_strategy_row:
                _opt_leverage = selected_strategy_row.get('leverage', 0)
                if not _opt_leverage:
                    _opt_leverage = selected_strategy_row.get('run_settings', {}).get('leverage', 0)
                _opt_contract = selected_strategy_row.get('contract_size', 100.0)
                if not _opt_contract or _opt_contract == 100.0:
                    _opt_contract = selected_strategy_row.get('run_settings', {}).get('contract_size', 100.0)

            if _opt_leverage == 0:
                try:
                    from shared.prop_firm_engine import get_leverage_for_symbol, get_instrument_type
                    _opt_sym = selected_strategy_row.get('symbol', 'XAUUSD') if selected_strategy_row else 'XAUUSD'
                    if not _opt_sym:
                        try:
                            from project2_backtesting.panels.configuration import load_config as _opt_cfg_load
                            _opt_sym = _opt_cfg_load().get('symbol', 'XAUUSD')
                        except Exception:
                            _opt_sym = 'XAUUSD'
                    if target_data:
                        _opt_leverage = get_leverage_for_symbol(target_data, _opt_sym)
                    _inst_type = get_instrument_type(_opt_sym)
                    if _inst_type == 'forex':
                        _opt_contract = 100000.0
                    elif _inst_type == 'indices':
                        _opt_contract = 1.0
                except Exception:
                    pass

            print(f"[OPTIMIZER] === DIAGNOSTIC ===")
            print(f"[OPTIMIZER] Risk: {risk_pct}% | DD: {_cfg_dd_daily}%/{_cfg_dd_total}%")
            print(f"[OPTIMIZER] Account: ${account_size:,.0f} | Leverage: 1:{_opt_leverage}, contract: {_opt_contract}")
            print(f"[OPTIMIZER] Source: {_risk_dd_source}")
            print(f"[OPTIMIZER] ==================")

            # ── Quick optimize (filter existing trades) ──
            if opt_mode == "quick":
                print("[OPTIMIZER] Running Quick Optimize mode...")
                _update_status("Quick Optimize: testing filter combinations...")

                # WHY (Hotfix): Extract exit info from selected strategy so
                #      quick optimize candidates carry it through to the
                #      Validate button and _validator_optimized.json.
                # CHANGED: April 2026 — Hotfix
                _sel_exit_class = ''
                _sel_exit_params = {}
                _sel_exit_name = ''
                _sel_exit_desc = ''
                if selected_strategy_row:
                    _sel_exit_class = selected_strategy_row.get('exit_class', '')
                    _sel_exit_params = selected_strategy_row.get('exit_params', {})
                    _sel_exit_name = selected_strategy_row.get('exit_name', '')
                    _sel_exit_desc = selected_strategy_row.get('exit_strategy', '')
                print(f"[OPTIMIZER] Selected exit: class={_sel_exit_class!r}, name={_sel_exit_name!r}")

                from project2_backtesting.strategy_refiner import deep_optimize
                quick_results = deep_optimize(
                    trades=current_trades,
                    candles_df=None,
                    indicators_df=None,
                    base_rules=base_strategy_rules,
                    exit_strategies=[],
                    target_firm=target_data,
                    account_size=account_size,
                    progress_callback=_cb,
                    lock_entry=_lock_entry_var.get() if _lock_entry_var else False,
                    lock_exit=_lock_exit_var.get() if _lock_exit_var else False,
                    lock_sltp=_lock_sltp_var.get() if _lock_sltp_var else False,
                    lock_filters=_lock_filters_var.get() if _lock_filters_var else False,
                    # WHY (Hotfix): Pass exit info so candidates carry it.
                    # CHANGED: April 2026 — Hotfix
                    exit_class=_sel_exit_class,
                    exit_params=_sel_exit_params,
                    exit_name=_sel_exit_name,
                    exit_strategy_desc=_sel_exit_desc,
                    leverage=_opt_leverage,
                    contract_size=_opt_contract,
                    risk_per_trade_pct=risk_pct,
                    dd_daily_limit=_cfg_dd_daily,
                    dd_total_limit=_cfg_dd_total,
                )
                all_candidates.extend(quick_results)
                print(f"[OPTIMIZER] Quick mode found {len(quick_results)} candidates")

            # ── Deep Explore (modify rules, find new entries) ──
            elif opt_mode == "deep":
                print("[OPTIMIZER] Running Deep Explore mode...")
                _update_status("Deep Explore: loading indicators and modifying rules...")

                import json as _json
                from project2_backtesting.strategy_refiner import deep_optimize_generate

                rules_path = os.path.join(
                    project_root, 'project1_reverse_engineering', 'outputs', 'analysis_report.json'
                )
                if not os.path.exists(rules_path):
                    print(f"[OPTIMIZER] ERROR: analysis_report.json not found at {rules_path}")
                    _update_status("Error: analysis_report.json not found.", error=True)
                    return

                with open(rules_path) as f:
                    report = _json.load(f)
                base_rules = [r for r in report.get('rules', []) if r.get('prediction') == 'WIN']
                print(f"[OPTIMIZER] Loaded {len(base_rules)} WIN rules from analysis_report.json")

                # Find candle path — use per-strategy entry_tf first (multi-TF backtest)
                from project2_backtesting.panels.configuration import load_config
                cfg = load_config()
                symbol = cfg.get('symbol', 'XAUUSD').lower()

                # WHY: When multi-TF backtest is used, each strategy row carries its
                #      own entry_tf. Use that first, fall back to analysis_report,
                #      then fall back to global config.
                # CHANGED: April 2026 — multi-TF support
                entry_tf = (
                    (selected_strategy_row or {}).get('entry_tf') or
                    (selected_strategy_row or {}).get('entry_timeframe') or
                    # WHY: Same as view_results.py fix — stats are flattened to top level
                    # CHANGED: April 2026 — read flattened stats from row top level
                    ((selected_strategy_row or {}).get('stats') or (selected_strategy_row or {})).get('entry_tf') or
                    None
                )

                _known_tfs = {'M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN'}

                def _resolve_tf(raw_tf):
                    """Extract a valid single TF from a raw value.
                    Handles composite labels like 'H1_M15' by taking the first segment.
                    """
                    if not raw_tf:
                        return None
                    for part in str(raw_tf).split('_'):
                        candidate = part.upper()
                        if candidate in _known_tfs:
                            return candidate
                    return None

                if not entry_tf:
                    entry_tf = cfg.get('winning_scenario', 'H1')
                    try:
                        if os.path.exists(rules_path):
                            saved_tf = report.get('entry_timeframe')
                            resolved = _resolve_tf(saved_tf)
                            if resolved and resolved != entry_tf:
                                print(f"[OPTIMIZER] Rules were discovered on {saved_tf} → using {resolved}, "
                                      f"config says {entry_tf}.")
                                entry_tf = resolved
                    except Exception:
                        pass

                # Normalise in case entry_tf itself is a composite like 'H1_M15'
                entry_tf = _resolve_tf(entry_tf) or entry_tf

                print(f"[OPTIMIZER] Using entry timeframe: {entry_tf}")

                # WHY: data_source_id from the strategy tells us which data to optimize against
                # CHANGED: April 2026 — data_source support in optimizer
                candles_path = None
                _ds_id = (selected_strategy_row or {}).get('data_source_id', '')
                _opt_ds_dir = None
                if _ds_id:
                    try:
                        from shared.data_sources import get_source_path
                        _ds_path = get_source_path(_ds_id)
                        if _ds_path and os.path.isdir(_ds_path):
                            _opt_ds_dir = _ds_path
                            print(f"[OPTIMIZER] Using data source dir: {_ds_id} → {_opt_ds_dir}")
                    except Exception as e:
                        print(f"[OPTIMIZER] Warning: data_source lookup failed: {e}")

                # Probe candidate paths if data_source not found; also try plain H1 as last resort
                if not candles_path:
                    # WHY: Use data source dir from rule, then resolve, then fallback.
                    # CHANGED: April 2026 — data source in optimizer
                    if _opt_ds_dir:
                        _opt_dir = _opt_ds_dir
                    else:
                        try:
                            from shared.data_sources import resolve_data_dir
                            _opt_dir = resolve_data_dir(selected_strategy_row)
                        except Exception:
                            _opt_dir = os.path.join(project_root, 'data')
                    for p in [
                        os.path.join(_opt_dir, f'{symbol}_{entry_tf}.csv'),
                        os.path.join(_opt_dir, f'{symbol.upper()}_{entry_tf}.csv'),
                        os.path.join(_opt_dir, f'{symbol.lower()}_{entry_tf}.csv'),
                        os.path.join(_opt_dir, f'{symbol}_H1.csv'),
                        os.path.join(project_root, 'data', f'{symbol}_{entry_tf}.csv'),
                        os.path.join(project_root, 'data', f'xauusd_{entry_tf}.csv'),
                    ]:
                        if os.path.exists(p):
                            candles_path = p
                            break

                if not candles_path:
                    print(f"[OPTIMIZER] ERROR: No candle CSV found for {symbol}_{entry_tf}")
                    _update_status(f"Error: candle CSV not found for {entry_tf}.", error=True)
                    return

                print(f"[OPTIMIZER] Using candles: {candles_path}")
                feature_matrix_path = os.path.join(
                    project_root, 'project1_reverse_engineering', 'outputs', 'feature_matrix.csv'
                )

                # WHY: Old code let direction default to 'BUY' in
                #      deep_optimize_generate(). For SELL strategies the
                #      optimizer would generate BUY-trade variants of the
                #      strategy's entry conditions and score those —
                #      candidates had no relationship to the actual
                #      strategy. Derive direction from the strategy's
                #      existing trades (majority vote, BUY on tie) and
                #      pass it through explicitly.
                # CHANGED: April 2026 — Phase 28 Fix 1 — derive and pass
                #          strategy direction (audit Part C crit #1)
                _buy_count  = sum(1 for _t in current_trades if _t.get('direction') == 'BUY')
                _sell_count = sum(1 for _t in current_trades if _t.get('direction') == 'SELL')
                _strategy_direction = 'SELL' if _sell_count > _buy_count else 'BUY'
                print(f"[OPTIMIZER] Strategy direction: {_strategy_direction} "
                      f"(BUY={_buy_count}, SELL={_sell_count})")

                generate_results = deep_optimize_generate(
                    trades=current_trades,
                    base_rules=base_rules,
                    candles_path=candles_path,
                    timeframe=entry_tf,
                    spread_pips=spread_pips,
                    commission_pips=commission_pips,
                    target_firm=target_data,
                    account_size=account_size,
                    filters=current_filters if current_filters else None,
                    progress_callback=_cb,
                    feature_matrix_path=feature_matrix_path,
                    direction=_strategy_direction,
                    leverage=_opt_leverage,
                    contract_size=_opt_contract,
                    risk_per_trade_pct=risk_pct,
                    dd_daily_limit=_cfg_dd_daily,
                    dd_total_limit=_cfg_dd_total,
                    # WHY: Per-firm parity from _opt_firm resolved above.
                    # CHANGED: April 2026 — per-firm parity in optimizer
                    max_spread_pips=_opt_firm['max_spread_pips'],
                    hard_close_hour=_opt_firm['hard_close_hour'],
                    variable_spread=_opt_firm['variable_spread'],
                    session_spread_multipliers=_opt_firm['session_spread_multipliers'],
                    min_hold_minutes=_opt_firm['min_hold_minutes'],
                    cooldown_candles=_opt_firm['cooldown_candles'],
                    slippage_pips=_opt_firm['slippage_pips'],
                )
                all_candidates.extend(generate_results)
                print(f"[OPTIMIZER] Deep Explore found {len(generate_results)} candidates")

            # Sort all candidates by score and return ALL (no [:20] cap)
            all_candidates.sort(key=lambda c: c.get('score', 0), reverse=True)
            print(f"[OPTIMIZER] Total candidates: {len(all_candidates)}")

            state.window.after(0, lambda: _show_opt_results(all_candidates))
            mode_name = "⚡ Quick Optimize" if opt_mode == "quick" else "🧬 Deep Explore"
            _update_status(f"Complete — {len(all_candidates)} candidates from {mode_name}")
        except Exception as e:
            import traceback
            print(f"[OPTIMIZER] ERROR: {e}")
            traceback.print_exc()
            _update_status(f"Error: {e}", error=True)
        finally:
            global _opt_worker_running
            _opt_worker_running = False
            try:
                state.window.after(0, lambda: _opt_start_btn.configure(state="normal") if _opt_start_btn else None)
                state.window.after(0, lambda: _opt_stop_btn.configure(state="disabled") if _opt_stop_btn else None)
                print("[OPTIMIZER] Worker thread finished")
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()


def _stop_optimization():
    global _opt_worker_running
    from project2_backtesting.strategy_refiner import stop_optimization
    stop_optimization()
    # WHY: Re-enable Start immediately — the worker's current fast_backtest call
    #      may run for 30+ seconds after the flag is set. Without this the button
    #      stays disabled until the inner backtest finishes, making it look broken.
    # CHANGED: April 2026 — immediate Start re-enable on stop
    _opt_worker_running = False
    if _opt_start_btn:
        _opt_start_btn.configure(state="normal")
    if _opt_stop_btn:
        _opt_stop_btn.configure(state="disabled")
    if _opt_status_lbl:
        _opt_status_lbl.configure(text="Stopped — click Start to run again", fg=AMBER)


def _render_opt_card(parent, rank, cand, stats, dollar_per_pip, acct,
                      challenge_fee, profit_split, risk=1.0, firm_data=None):
    """Render a single optimizer result card with all buttons."""
    score = cand.get('score', 0) or 0
    rules = cand.get('rules', [])
    filters = cand.get('filters_applied', {})
    changes = cand.get('changes_from_base', '')

    card_bg = "#f0fff0" if (score or 0) > 0 else "#fff8f8"
    border = "#28a745" if (score or 0) > 0 else "#dc3545"

    card = tk.Frame(parent, bg=card_bg, highlightbackground=border,
                     highlightthickness=2, padx=12, pady=8)
    card.pack(fill="x", padx=5, pady=4)

    strategy_name = cand.get('name', '?')
    tk.Label(card, text=f"#{rank}: {strategy_name}  (score: {score:.1f})",
             font=("Segoe UI", 10, "bold"), bg=card_bg, fg=DARK).pack(anchor="w")

    # Stats
    wr = stats.get('win_rate', 0) or 0
    wr_str = f"{wr*100:.1f}%" if (wr or 0) <= 1 else f"{wr:.1f}%"
    wr_color = GREEN if ((wr or 0) if (wr or 0) <= 1 else (wr or 0)/100) >= 0.60 else AMBER

    # Add risk % info if present
    _risk_str = f"  |  Risk: {cand.get('risk_pct', '?')}%" if cand.get('risk_pct') else ""
    stats_text = (f"Trades: {stats.get('count', 0)}  |  WR: {wr_str}  |  "
                  f"Avg: {stats.get('avg_pips', 0):+.1f} pips  |  "
                  f"Total: {stats.get('total_pips', 0):+,.0f} pips  |  "
                  f"PF: {stats.get('profit_factor', 0):.2f}  |  "
                  f"{stats.get('trades_per_day', 0):.1f}/day{_risk_str}")
    tk.Label(card, text=stats_text, font=("Segoe UI", 9), bg=card_bg,
             fg=wr_color).pack(anchor="w", pady=(2, 0))

    # Dollar amounts
    total_pips = stats.get('total_pips', 0) or 0
    total_dollars = (total_pips or 0) * (dollar_per_pip or 0)
    total_pct = (total_dollars / max(acct or 1, 1)) * 100
    try:
        trade_list = cand.get('trades', [])
        if trade_list:
            import pandas as pd
            first = pd.to_datetime(trade_list[0].get('entry_time', ''))
            last = pd.to_datetime(trade_list[-1].get('entry_time', ''))
            months = max((last - first).days / 30, 1)
            monthly_dollars = total_dollars / months
        else:
            monthly_dollars = 0
    except Exception:
        monthly_dollars = 0

    your_monthly = (monthly_dollars or 0) * ((profit_split or 80) / 100)

    dollar_row = tk.Frame(card, bg=card_bg)
    dollar_row.pack(fill="x", pady=(2, 0))
    for label, value, color in [
        ("Total", f"${total_dollars:+,.0f} ({total_pct:+.1f}%)",
         "#28a745" if (total_dollars or 0) > 0 else "#dc3545"),
        ("Monthly", f"${monthly_dollars:+,.0f}/mo",
         "#28a745" if (monthly_dollars or 0) > 0 else "#dc3545"),
        ("Your share", f"${your_monthly:+,.0f}/mo ({profit_split or 0}%)", "#667eea"),
    ]:
        tk.Label(dollar_row, text=f"{label}: ", bg=card_bg, fg="#888",
                 font=("Arial", 8)).pack(side=tk.LEFT)
        tk.Label(dollar_row, text=value, bg=card_bg, fg=color,
                 font=("Arial", 8, "bold")).pack(side=tk.LEFT, padx=(0, 10))

    # ROI
    if (challenge_fee or 0) > 0 and (your_monthly or 0) > 0:
        roi = tk.Frame(card, bg="#e8f5e9", padx=6, pady=3)
        roi.pack(fill="x", pady=(3, 0))
        months_roi = (challenge_fee or 0) / max((your_monthly or 0), 1)
        tk.Label(roi, text=f"Fee: ${challenge_fee or 0} | ROI: {months_roi:.1f}mo | "
                           f"Year 1: ${((your_monthly or 0) * 12 - (challenge_fee or 0)):+,.0f}",
                 bg="#e8f5e9", fg="#2e7d32", font=("Arial", 8, "bold")).pack(anchor="w")

    # DD Breach Count
    try:
        from project2_backtesting.strategy_refiner import count_dd_breaches

        # Extract DD limits from firm_data
        daily_limit = 5.0
        total_limit = 10.0
        if firm_data:
            try:
                # Try evaluation phase first (most common for optimizer)
                phase_data = firm_data['challenges'][0]['phases'][0]
                daily_limit = phase_data.get('max_daily_drawdown_pct', 5.0)
                total_limit = phase_data.get('max_total_drawdown_pct', 10.0)
            except (KeyError, IndexError, TypeError):
                # WHY: Old code caught KeyError/IndexError but not TypeError. If
                #      firm_data or firm_data['challenges'] was a string instead of
                #      a dict/list, subscripting it raised TypeError which wasn't
                #      caught, crashing the entire optimizer for some firms.
                # CHANGED: April 2026 — catch TypeError (Get Leveraged bug fix)
                # Fallback to funded phase
                try:
                    funded = firm_data['challenges'][0]['funded']
                    daily_limit = funded.get('max_daily_drawdown_pct', 5.0)
                    total_limit = funded.get('max_total_drawdown_pct', 10.0)
                except (KeyError, IndexError, TypeError):
                    pass

        trades = cand.get('trades', [])
        if trades:
            breach_data = count_dd_breaches(
                trades,
                account_size=acct,
                risk_pct=risk,
                pip_value=float(cand.get('pip_value_per_lot', _srp_pip_value)),
                daily_dd_limit_pct=daily_limit,
                total_dd_limit_pct=total_limit,
                funded_protect=False,
            )

            blown = breach_data.get('blown_count', 0)
            daily_br = breach_data.get('daily_breaches', 0)
            total_br = breach_data.get('total_breaches', 0)
            worst_daily = breach_data.get('worst_daily_pct', 0)
            worst_total = breach_data.get('worst_total_pct', 0)
            survival = breach_data.get('survival_rate_per_month', 0)

            # Color coding: green if 0 blows, red if blown, orange if close calls
            if blown == 0:
                dd_bg = "#e8f5e9"
                dd_fg = "#2e7d32"
            elif blown >= 3:
                dd_bg = "#ffebee"
                dd_fg = "#c62828"
            else:
                dd_bg = "#fff3e0"
                dd_fg = "#e65100"

            dd_frame = tk.Frame(card, bg=dd_bg, padx=6, pady=3)
            dd_frame.pack(fill="x", pady=(3, 0))

            # Main DD breach text
            dd_text = f"🚨 Blown: {blown}x  |  DD Breaches: {daily_br} daily, {total_br} total  |  "
            dd_text += f"Worst: {worst_daily:.1f}% daily, {worst_total:.1f}% total  |  "
            dd_text += f"Survival: {survival:.1f}%"

            dd_label = tk.Label(dd_frame, text=dd_text, bg=dd_bg, fg=dd_fg,
                               font=("Arial", 8, "bold"))
            dd_label.pack(anchor="w")

            # Tooltip with detailed breakdown
            daily_dates = breach_data.get('daily_breach_dates', [])
            total_dates = breach_data.get('total_breach_dates', [])

            tooltip_text = f"DD Limits: {daily_limit}% daily / {total_limit}% total\n"
            tooltip_text += f"Account blown {blown} time(s)\n\n"

            if daily_dates:
                tooltip_text += f"Daily DD breaches ({len(daily_dates)}):\n"
                for dt in daily_dates[:10]:  # Show first 10
                    tooltip_text += f"  • {dt}\n"
                if len(daily_dates) > 10:
                    tooltip_text += f"  ... and {len(daily_dates) - 10} more\n"
                tooltip_text += "\n"

            if total_dates:
                tooltip_text += f"Total DD breaches ({len(total_dates)}):\n"
                for dt in total_dates[:10]:  # Show first 10
                    tooltip_text += f"  • {dt}\n"
                if len(total_dates) > 10:
                    tooltip_text += f"  ... and {len(total_dates) - 10} more\n"

            if not daily_dates and not total_dates:
                tooltip_text += "✓ No DD breaches - clean run!"

            # Create tooltip
            def _show_tooltip(event):
                tooltip = tk.Toplevel()
                tooltip.wm_overrideredirect(True)
                tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
                label = tk.Label(tooltip, text=tooltip_text, justify=tk.LEFT,
                               background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                               font=("Courier", 8), padx=8, pady=6)
                label.pack()
                dd_label._tooltip = tooltip

            def _hide_tooltip(event):
                if hasattr(dd_label, '_tooltip'):
                    dd_label._tooltip.destroy()
                    del dd_label._tooltip

            dd_label.bind("<Enter>", _show_tooltip)
            dd_label.bind("<Leave>", _hide_tooltip)
    except Exception as e:
        # Silently skip if breach calculation fails
        pass

    # Stage-specific estimation (Payout for Funded, Target for Evaluation)
    try:
        from shared.tooltip import add_tooltip
        global _stage_var

        trade_list = cand.get('trades', [])
        if trade_list and len(trade_list) > 20:
            import pandas as pd

            # Group trades by day
            daily_pnls = {}
            for t in trade_list:
                try:
                    day = str(pd.to_datetime(t.get('entry_time', '')).date())
                    pnl_dollars = t.get('net_pips', 0) * dollar_per_pip
                    daily_pnls[day] = daily_pnls.get(day, 0) + pnl_dollars
                except:
                    continue

            if daily_pnls:
                days_sorted = sorted(daily_pnls.keys())
                stage = _stage_var.get().lower() if _stage_var else "funded"

                if stage == "funded":
                    # FUNDED: Payout estimation with consistency rules
                    windows_total = 0
                    windows_pass = 0
                    window_profits = []

                    # WHY (Phase 69 Fix 17): Old code re-read consistency_limit
                    #      and min_profit_days from firm_data on every window
                    #      iteration — O(N×rules) per simulation. Hoist once.
                    # CHANGED: April 2026 — Phase 69 Fix 17 — hoist firm rule lookup
                    #          (audit Part E MEDIUM #17)
                    consistency_limit = 20  # default
                    min_profit_days   = 3   # default
                    if firm_data:
                        for _fr in firm_data.get('trading_rules', []):
                            if _fr.get('type') == 'consistency':
                                consistency_limit = _fr.get('parameters', {}).get('max_day_pct', 20)
                            elif _fr.get('type') == 'min_profitable_days':
                                min_profit_days = _fr.get('parameters', {}).get('min_days', 3)

                    for start_i in range(0, len(days_sorted) - 5, 7):  # step by 7 days
                        # Get 14-day window
                        start_day = pd.to_datetime(days_sorted[start_i])
                        window_pnls = {}
                        for d in days_sorted[start_i:]:
                            dt = pd.to_datetime(d)
                            if (dt - start_day).days >= 14:
                                break
                            window_pnls[d] = daily_pnls[d]

                        if not window_pnls:
                            continue

                        total_profit = sum(v for v in window_pnls.values() if v > 0)
                        if total_profit <= 0:
                            windows_total += 1
                            continue

                        # Check consistency: best day < 20% of total
                        best_day = max(window_pnls.values())
                        best_day_pct = (best_day / total_profit * 100) if total_profit > 0 else 100

                        # Check min profitable days (3 days >= 0.5% of account)
                        min_threshold = (acct or 100000) * 0.005
                        profitable_days = sum(1 for v in window_pnls.values() if v >= min_threshold)

                        # Phase 69 Fix 17: consistency_limit and min_profit_days
                        # now hoisted outside the loop (before 'for start_i')

                        windows_total += 1
                        net_window = sum(window_pnls.values())

                        consistency_ok = best_day_pct <= consistency_limit
                        min_days_ok = profitable_days >= min_profit_days

                        if consistency_ok and min_days_ok and net_window > 0:
                            windows_pass += 1
                            payout = net_window * ((profit_split or 80) / 100)
                            window_profits.append(payout)

                    if windows_total > 0:
                        pass_rate = windows_pass / windows_total * 100
                        avg_payout = sum(window_profits) / len(window_profits) if window_profits else 0
                        min_payout = min(window_profits) if window_profits else 0
                        max_payout = max(window_profits) if window_profits else 0
                        annual_est = avg_payout * (365 / 14)  # ~26 periods per year

                        payout_frame = tk.Frame(card, bg="#f0f0ff", padx=8, pady=5)
                        payout_frame.pack(fill="x", pady=(3, 0))

                        if pass_rate > 0:
                            payout_label = tk.Label(payout_frame,
                                     text=f"💰 Payout: {pass_rate:.0f}% of periods pass | "
                                          f"Avg: ${avg_payout:,.0f} | "
                                          f"Min: ${min_payout:,.0f} | Max: ${max_payout:,.0f} | "
                                          f"Annual est: ${annual_est:,.0f}",
                                     bg="#f0f0ff", fg="#4a148c", font=("Segoe UI", 8, "bold"))
                        else:
                            payout_label = tk.Label(payout_frame,
                                     text=f"💰 Payout: 0% of periods pass consistency — "
                                          f"this strategy won't generate payouts",
                                     bg="#f0f0ff", fg="#dc3545", font=("Segoe UI", 8, "bold"))

                        payout_label.pack(anchor="w")

                        add_tooltip(payout_label,
                            f"Payout Estimation (14-day windows)\n\n"
                            f"Windows tested: {windows_total}\n"
                            f"Windows that pass all rules: {windows_pass} ({pass_rate:.0f}%)\n\n"
                            f"Rules checked per window:\n"
                            f"  • Consistency: best day < {consistency_limit}% of total\n"
                            f"  • Min profitable days: {min_profit_days} days >= 0.5%\n"
                            f"  • Net profit > 0\n\n"
                            f"Payout amounts (your {profit_split}% share):\n"
                            f"  Minimum: ${min_payout:,.0f}\n"
                            f"  Average: ${avg_payout:,.0f}\n"
                            f"  Maximum: ${max_payout:,.0f}\n\n"
                            f"Annual estimate: ${annual_est:,.0f} "
                            f"(~26 periods × ${avg_payout:,.0f})",
                            wraplength=400)

                elif stage == "evaluation":
                    # EVALUATION: Days to reach profit target
                    # Read profit target from firm
                    profit_target_pct = 6.0  # default
                    try:
                        if firm_data:
                            phases = firm_data['challenges'][0].get('phases', [])
                            if phases:
                                profit_target_pct = phases[0].get('profit_target_pct', 6.0)
                    except Exception:
                        pass

                    target_dollars = acct * (profit_target_pct / 100)

                    # Get DD limit for blown check
                    total_limit = 10.0
                    try:
                        if firm_data:
                            phases = firm_data['challenges'][0].get('phases', [])
                            if phases:
                                total_limit = phases[0].get('max_total_drawdown_pct', 10.0)
                    except Exception:
                        pass

                    # Simulate: how many trading days to reach target?
                    days_to_target = []
                    days_list = sorted(daily_pnls.keys())

                    for start_i in range(0, len(days_list) - 5, 7):
                        running = 0
                        day_count = 0
                        reached = False
                        for d in days_list[start_i:]:
                            running += daily_pnls[d]
                            day_count += 1
                            if running >= target_dollars:
                                days_to_target.append(day_count)
                                reached = True
                                break
                            # Check if blown before reaching target
                            if running < -(acct * (total_limit / 100)):
                                break

                    eval_frame = tk.Frame(card, bg="#fff8e1", padx=8, pady=5)
                    eval_frame.pack(fill="x", pady=(3, 0))

                    if days_to_target:
                        avg_days = sum(days_to_target) / len(days_to_target)
                        min_days = min(days_to_target)
                        max_days = max(days_to_target)
                        total_windows = max(len(list(range(0, len(days_list) - 5, 7))), 1)
                        pass_rate = len(days_to_target) / total_windows * 100

                        eval_lbl = tk.Label(eval_frame,
                            text=f"🎯 Eval: {pass_rate:.0f}% pass rate | "
                                 f"Avg: {avg_days:.0f} days | "
                                 f"Min: {min_days} days | Max: {max_days} days | "
                                 f"Target: {profit_target_pct}% (${target_dollars:,.0f})",
                            bg="#fff8e1", fg="#e65100",
                            font=("Segoe UI", 8, "bold"))
                    else:
                        eval_lbl = tk.Label(eval_frame,
                            text=f"🎯 Eval: 0% pass rate — never reaches {profit_target_pct}% target",
                            bg="#fff8e1", fg="#dc3545",
                            font=("Segoe UI", 8, "bold"))

                    eval_lbl.pack(anchor="w")

                    add_tooltip(eval_lbl,
                        f"Evaluation Target Estimation\n\n"
                        f"Target: {profit_target_pct}% = ${target_dollars:,.0f}\n"
                        f"Windows tested: {max(len(list(range(0, len(days_list) - 5, 7))), 1)}\n"
                        f"Windows reaching target: {len(days_to_target)}\n\n"
                        f"Days to reach target:\n"
                        f"  Fastest: {min(days_to_target) if days_to_target else '—'}\n"
                        f"  Average: {sum(days_to_target)//max(len(days_to_target),1) if days_to_target else '—'}\n"
                        f"  Slowest: {max(days_to_target) if days_to_target else '—'}",
                        wraplength=400)
    except Exception as e:
        # Silently skip if calculation fails
        pass

    # What changed
    display_filters = {}
    if isinstance(filters, dict):
        for fk, fv in filters.items():
            if fk not in ('description', 'firm_data', 'stage', 'firm_name'):
                display_filters[fk] = fv

    if display_filters:
        cf = tk.Frame(card, bg="#e8f4fd", padx=8, pady=4)
        cf.pack(fill="x", pady=(4, 0))
        tk.Label(cf, text="Changed:", bg="#e8f4fd", fg="#1565c0",
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        explanations = {
            'max_trades_per_day': lambda v: f"max {v}/day",
            'min_hold_minutes': lambda v: f"hold ≥{v}m",
            'cooldown_minutes': lambda v: f"cooldown {v}m",
            'sessions': lambda v: f"sessions: {', '.join(v) if isinstance(v, list) else str(v)}",
        }
        parts = []
        for fk, fv in display_filters.items():
            fn = explanations.get(fk)
            parts.append(fn(fv) if fn else f"{fk}={fv}")
        tk.Label(cf, text="  " + " | ".join(parts),
                 bg="#e8f4fd", fg="#333", font=("Segoe UI", 8)).pack(side=tk.LEFT)
    elif changes:
        cf = tk.Frame(card, bg="#e8f4fd", padx=8, pady=3)
        cf.pack(fill="x", pady=(3, 0))
        tk.Label(cf, text=f"Changed: {changes}", bg="#e8f4fd", fg="#333",
                 font=("Segoe UI", 8)).pack(anchor="w")

    # ── Buttons ──
    btn = tk.Frame(card, bg=card_bg)
    btn.pack(fill="x", pady=(5, 0))

    trades_snap = list(cand.get('trades', []))
    rules_snap = list(cand.get('rules', []))
    filters_snap = {k: v for k, v in (filters or {}).items()
                    if k not in ('firm_data', 'description', 'stage')} if isinstance(filters, dict) else {}
    stats_snap = dict(stats)

    # WHY (Validator Fix): Capture exit info from the candidate so the
    #      Validate button doesn't depend on the backtest matrix lookup
    #      (which fails for optimizer results with string indices).
    # CHANGED: April 2026 — Validator Fix
    _exit_info_snap = {
        'exit_class': cand.get('exit_class', ''),
        'exit_params': cand.get('exit_params', {}),
        'exit_name': cand.get('exit_name', ''),
        'exit_strategy': cand.get('exit_strategy', ''),
    }

    # WHY (Hotfix): If candidate has no exit info (quick optimize pre-fix),
    #      read it from the currently selected strategy in the dropdown.
    # CHANGED: April 2026 — Hotfix
    if not _exit_info_snap.get('exit_class'):
        try:
            _sel_idx = _get_selected_index()
            if _sel_idx is not None:
                for _s in _strategies:
                    if _s.get('index') == _sel_idx:
                        _exit_info_snap['exit_class'] = _s.get('exit_class', '')
                        _exit_info_snap['exit_params'] = _s.get('exit_params', {})
                        _exit_info_snap['exit_name'] = _s.get('exit_name', '')
                        _exit_info_snap['exit_strategy'] = _s.get('exit_strategy', '')
                        break
        except Exception:
            pass

    tk.Button(btn, text="📊 Trades",
              command=lambda t=trades_snap: _show_candidate_trades(t),
              bg="#667eea", fg="white", font=("Segoe UI", 8, "bold"),
              relief=tk.FLAT, padx=6, pady=2).pack(side=tk.LEFT, padx=(0, 3))

    def _save(r=rules_snap, f=filters_snap, n=strategy_name, s=stats_snap, c=cand, t=trades_snap):
        try:
            from shared.saved_rules import save_rule

            # WHY: A saved strategy needs EVERYTHING to reproduce it:
            #      rules (what triggers a trade), exit strategy (how it exits),
            #      filters (what gets filtered out), and entry TF (candle frequency).
            #      Without exit strategy, the validator can't reconstruct the trade logic.
            #      Without entry TF, the backtester loads the wrong candle file.
            # CHANGED: April 2026 — save complete strategy
            # WHY: Old code read exit info from backtest_matrix[idx], but idx
            #      doesn't always match (optimizer changes selection). Also:
            #      rules_snap may be empty for filter-only optimizations.
            #      Now: read the BASE strategy from the matrix, use its rules
            #      if rules_snap is empty, and always capture exit info.
            # CHANGED: April 2026 — robust optimizer save
            idx = _get_selected_index()
            exit_class = ''
            exit_params = {}
            exit_name = ''
            _base_rules = []
            _base_direction = ''
            _trades_to_save = list(t) if t else []

            # WHY: trades_snap can be empty if the optimizer hasn't generated
            #      new candidates yet, or if this is a filter-only optimization.
            #      Load trades from persisted backtest_trades_{TF}.json files
            #      so the saved rule has full trade history for validation.
            # CHANGED: April 2026 — load trades from persisted files if missing
            if not _trades_to_save and idx is not None:
                try:
                    from project2_backtesting.strategy_refiner import load_trades_for_strategy
                    loaded_trades = load_trades_for_strategy(idx)
                    if loaded_trades:
                        _trades_to_save = list(loaded_trades)
                        print(f"[REFINER SAVE] Loaded {len(_trades_to_save)} trades from backtest_trades file")
                except Exception as _te:
                    print(f"[REFINER SAVE] Could not load trades from file: {_te}")

            if idx is not None:
                try:
                    matrix_path = os.path.join(project_root, 'project2_backtesting',
                                               'outputs', 'backtest_matrix.json')
                    if os.path.exists(matrix_path):
                        import json as _json
                        with open(matrix_path) as _mf:
                            _matrix = _json.load(_mf)
                        _results = _matrix.get('results', []) or _matrix.get('matrix', [])
                        if isinstance(idx, int) and idx < len(_results):
                            _strat = _results[idx]
                        elif isinstance(idx, str) and idx.isdigit() and int(idx) < len(_results):
                            _strat = _results[int(idx)]
                        else:
                            _strat = {}
                        if _strat:
                            exit_class = _strat.get('exit_class', _strat.get('exit_strategy', ''))
                            exit_params = _strat.get('exit_params', _strat.get('exit_strategy_params', {}))
                            exit_name = _strat.get('exit_name', '')
                            _base_rules = _strat.get('rules', [])
                            # Infer direction from rule_combo name
                            _combo = _strat.get('rule_combo', '')
                            if '(BUY)' in _combo:
                                _base_direction = 'BUY'
                            elif '(SELL)' in _combo:
                                _base_direction = 'SELL'
                except Exception:
                    pass

            # WHY: Read entry_tf from the actual backtest strategy row, not config.
            #      In multi-TF backtests, each strategy has its own entry_tf
            #      (e.g., strategy #10 is M5, strategy #11 is H1). Reading from
            #      config would save the wrong timeframe.
            #
            #      Priority order:
            #        1. _strat['entry_tf']         — the row from backtest_matrix.json
            #        2. _strat['entry_timeframe']   — alternative key name
            #        3. _strat['stats']['entry_tf'] — nested in stats dict
            #        4. config['winning_scenario']  — last resort (no row data)
            #
            #      ONLY fall back to config when the strategy row has NO entry_tf
            #      at all.  Do NOT override a valid entry_tf (even 'H1') with
            #      the config value — the row value is the ground truth of what
            #      timeframe was actually backtested.
            # CHANGED: April 2026 — read entry_tf from strategy row
            entry_tf = None
            if _strat:
                entry_tf = (
                    _strat.get('entry_tf') or
                    _strat.get('entry_timeframe') or
                    ((_strat.get('stats') or {}).get('entry_tf')) or
                    None
                )

            # Fallback to config ONLY if strategy row had no entry_tf at all
            if not entry_tf:
                try:
                    from project2_backtesting.panels.configuration import load_config
                    _cfg = load_config()
                    entry_tf = _cfg.get('winning_scenario', 'H1')
                except Exception:
                    entry_tf = 'H1'

            # WHY: rule_combo + trades_snap were missing from the saved data.
            #      Without rule_combo, downstream lookup-by-name fails.
            #      Without trades, the validator gets [] and reports 0 trades
            #      validated even though the optimizer found 300+. Now we
            #      embed the actual trades list (typically <500 KB even for
            #      large strategies) so the validator can use them directly
            #      without re-running a backtest.
            # CHANGED: April 2026 — include rule_combo + trades in saved data
            # WHY: Use base strategy rules if optimizer rules are empty.
            #      Filter-only optimizations (min_hold, sessions) don't
            #      change rules — they just filter the trade list. The
            #      rules come from the base strategy in the backtest matrix.
            # CHANGED: April 2026 — include base rules + direction + fix WR format
            _save_rules = list(r) if r else list(_base_rules)

            # Normalize WR to percentage (backtest uses %, optimizer uses decimal)
            _wr = s.get('win_rate', 0)
            if isinstance(_wr, (int, float)) and 0 < _wr < 1.0:
                _wr = _wr * 100.0  # Convert decimal to percentage

            # WHY: Preserve original rule IDs and backtest metadata for traceability.
            #      When a rule is saved from the optimizer, we need to know:
            #      - Which backtest strategy it came from (index in matrix)
            #      - What the original rule_id/rule_combo were
            #      - What the base (unoptimized) stats were
            #      This allows tracking the optimization lineage and comparing
            #      before/after performance.
            # CHANGED: April 2026 — preserve backtest lineage metadata
            _original_rule_id = _strat.get('rule_id', '') if _strat else ''
            _original_rule_combo = _strat.get('rule_combo', '') if _strat else ''
            _backtest_index = idx if idx is not None else -1

            data = {
                'rule_combo': n,
                'trades': _trades_to_save,
                'conditions': [],
                'prediction': 'WIN',
                'win_rate': round(_wr, 2),
                'avg_pips': s.get('avg_pips', 0),
                'total_pips': s.get('total_pips', 0),
                'net_total_pips': s.get('total_pips', 0),
                'total_trades': s.get('count', 0),
                'max_dd_pips': s.get('max_dd_pips', 0),
                'net_profit_factor': s.get('profit_factor', 0),
                'optimized_rules': _save_rules,
                'rules': _save_rules,
                'filters_applied': f,
                'exit_class': exit_class,
                'exit_params': exit_params,
                'exit_name': exit_name,
                'entry_timeframe': entry_tf,
                'direction': _base_direction,
                # Lineage tracking: where did this optimized strategy come from?
                'original_rule_id': _original_rule_id,
                'original_rule_combo': _original_rule_combo,
                'backtest_strategy_index': _backtest_index,
                'optimization_applied': True if (f or len(r) != len(_base_rules)) else False,
                # Regime filter conditions (if active during backtest)
                'regime_filter_conditions': [],
                # WHY: The refiner's risk/stage/firm/account are set by the user
                #      based on the prop firm they're targeting. These values were
                #      used during optimization but never saved — so the EA generator
                #      had no way to know what risk the strategy was optimized for.
                # CHANGED: April 2026 — save risk management settings
                # WHY: Risk optimization step finds the optimal risk_pct. Save it
                #      from the candidate if present, else from UI.
                # CHANGED: April 2026 — risk optimization
                # WHY: c.get('risk_pct') can return None (key present, value None).
                #      float(None) crashes. Use 'or' to fall through to default.
                # CHANGED: April 2026 — safe float conversion
                'risk_settings': {
                    'risk_pct': float(c.get('risk_pct') or (_risk_var.get() if _risk_var else 1.0) or 1.0),
                    'account_size': int(float(_acct_var.get() or 100000)) if _acct_var else 100000,
                    'firm': _opt_target_var.get() if _opt_target_var else '',
                    'stage': _stage_var.get() if _stage_var else 'Funded',
                },
                # WHY: Embed the selected firm's eval parameters at save time.
                #      Without this, the eval simulation re-reads the firm JSON at
                #      display time, so changing the firm dropdown later would show
                #      wrong eval stats for an old rule. Freezing at save time means
                #      the rule is self-contained: "this rule was designed for FTMO
                #      Evaluation with 10% total DD, 5% daily DD, 30-day deadline."
                #      max_calendar_days=None means unlimited (e.g. Get Leveraged).
                # CHANGED: April 2026 — freeze eval params into saved rule
                'eval_settings': _build_eval_settings(
                    _opt_target_var.get() if _opt_target_var else '',
                    _stage_var.get() if _stage_var else 'Evaluation',
                    project_root
                ),
            }
            for rule in _save_rules:
                if rule.get('prediction') == 'WIN':
                    data['conditions'].extend(rule.get('conditions', []))

            # Embed regime conditions from config into saved data + rules
            try:
                import sys as _sys
                _p1_dir = os.path.join(project_root, 'project1_reverse_engineering')
                if _p1_dir not in _sys.path:
                    _sys.path.insert(0, _p1_dir)
                import config_loader as _rf_cl
                _rf_cfg = _rf_cl.load()
                if str(_rf_cfg.get('regime_filter_enabled', 'false')).lower() == 'true':
                    _rf_disc_str = _rf_cfg.get('regime_filter_discovered', '') or ''
                    if _rf_disc_str:
                        _rf_disc = json.loads(_rf_disc_str)
                        if _rf_disc.get('status') == 'ok':
                            _rf_conds = _rf_disc.get('subset') or _rf_disc.get('subset_chosen') or []
                            data['regime_filter_conditions'] = _rf_conds
                            # Embed per-rule (Phase A.43)
                            for _rule in data.get('optimized_rules', []):
                                _rule['regime_filter'] = _rf_conds
                            for _rule in data.get('rules', []):
                                _rule['regime_filter'] = _rf_conds
                            print(f"[OPTIMIZER SAVE] Embedded {len(_rf_conds)} regime conditions into saved rule")
            except Exception as _rfe:
                print(f"[OPTIMIZER SAVE] Could not embed regime conditions: {_rfe}")

            # Inject broker specs into saved data (fields not already present win)
            try:
                import sys as _bs_sys
                _bs_p1_dir = os.path.join(project_root, 'project1_reverse_engineering')
                if _bs_p1_dir not in _bs_sys.path:
                    _bs_sys.path.insert(0, _bs_p1_dir)
                import config_loader as _bs_cl
                _bs_cfg = _bs_cl.load()
                for _bs_key in ('pip_value_per_lot', 'spread', 'commission_per_lot',
                                'contract_size', 'pip_size',
                                'data_source_id', 'data_source_path',
                                'prop_firm_name', 'prop_firm_id',
                                'prop_firm_leverage'):
                    _bs_val = _bs_cfg.get(_bs_key)
                    if _bs_val is not None and _bs_key not in data:
                        try:
                            data[_bs_key] = float(_bs_val)
                        except (TypeError, ValueError):
                            data[_bs_key] = str(_bs_val)
            except Exception as _bse:
                print(f"[OPTIMIZER SAVE] Could not embed broker specs: {_bse}")

            # Log what's being saved so user can verify
            _n_trades_saving = len(data.get('trades', []))
            _n_rules_saving = len(data.get('rules', []))
            _n_conds_saving = len(data.get('conditions', []))
            print(f"[REFINER SAVE] Saving rule:")
            print(f"  Name:           {n}")
            print(f"  Entry TF:       {entry_tf}  (source: {'strategy row' if _strat and (_strat.get('entry_tf') or _strat.get('entry_timeframe')) else 'config fallback'})")
            print(f"  Direction:      {data.get('direction', '?')}")
            print(f"  Rules:          {_n_rules_saving}")
            print(f"  Conditions:     {_n_conds_saving}")
            print(f"  Trades:         {_n_trades_saving}")
            print(f"  Exit:           {exit_name or exit_class or '?'}")
            print(f"  Win Rate:       {data.get('win_rate', '?')}%")
            print(f"  Profit Factor:  {data.get('net_profit_factor', '?')}")
            print(f"  Original ID:    {data.get('original_rule_id', 'N/A')}")

            rid = save_rule(data, source=f"Optimizer: {n}", notes=str(f))

            # Show confirmation with key details so user can verify
            messagebox.showinfo("Saved",
                f"Saved as #{rid}!\n\n"
                f"Entry TF:    {entry_tf}\n"
                f"Direction:   {data.get('direction', '?')}\n"
                f"Trades:      {_n_trades_saving}\n"
                f"Win Rate:    {data.get('win_rate', '?')}%\n"
                f"PF:          {data.get('net_profit_factor', '?')}"
            )
            # WHY: Refresh the panel to show the newly saved rule in the dropdown
            # CHANGED: April 2026 — auto-refresh after save
            try:
                refresh()
            except Exception:
                pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            messagebox.showerror("Error", str(e))

    tk.Button(btn, text="💾 Save", command=_save,
              bg="#28a745", fg="white", font=("Segoe UI", 8, "bold"),
              relief=tk.FLAT, padx=6, pady=2).pack(side=tk.LEFT, padx=(0, 3))

    def _playground(r=rules_snap):
        try:
            import json
            p = os.path.join(project_root, 'project2_backtesting', 'outputs', '_playground_rules.json')
            with open(p, 'w') as fp:
                json.dump({'rules': r, 'source': 'optimizer'}, fp, indent=2, default=str)
            messagebox.showinfo("Ready", "Go to 🎮 Strategy Playground")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    tk.Button(btn, text="🎮 Play", command=_playground,
              bg="#17a2b8", fg="white", font=("Segoe UI", 8, "bold"),
              relief=tk.FLAT, padx=6, pady=2).pack(side=tk.LEFT, padx=(0, 3))

    def _validate(t=trades_snap, r=rules_snap, n=strategy_name, f=filters_snap,
                  _ei=_exit_info_snap):
        try:
            import json
            # WHY: Validator needs rules + exit + filters to reproduce the exact strategy.
            # CHANGED: April 2026 — pass complete strategy to validator
            idx = _get_selected_index()
            exit_info = {}
            if idx is not None:
                try:
                    matrix_path = os.path.join(project_root, 'project2_backtesting',
                                               'outputs', 'backtest_matrix.json')
                    if os.path.exists(matrix_path):
                        with open(matrix_path) as _mf:
                            _matrix = json.load(_mf)
                        if idx < len(_matrix.get('results', [])):
                            _strat = _matrix['results'][idx]
                            exit_info = {
                                'exit_class': _strat.get('exit_class', ''),
                                'exit_params': _strat.get('exit_params', {}),
                                'exit_name': _strat.get('exit_name', ''),
                            }
                except Exception:
                    pass

            # WHY (Validator Fix): If matrix lookup failed (optimizer result
            #      with string idx), use the captured candidate exit info.
            # CHANGED: April 2026 — Validator Fix
            if not exit_info.get('exit_class'):
                exit_info = dict(_ei)
            # Final fallback: parse from exit description
            if not exit_info.get('exit_class') and exit_info.get('exit_name'):
                _name = exit_info['exit_name'].lower().strip()
                _class_map = {
                    'fixed sl/tp': 'FixedSLTP',
                    'trailing stop': 'TrailingStop',
                    'atr-based': 'ATRBased',
                    'time-based': 'TimeBased',
                    'indicator exit': 'IndicatorExit',
                    'hybrid': 'HybridExit',
                }
                exit_info['exit_class'] = _class_map.get(_name, 'FixedSLTP')

            p = os.path.join(project_root, 'project2_backtesting', 'outputs', '_validator_optimized.json')
            with open(p, 'w') as fp:
                json.dump({
                    'rules': r,
                    'trades': t,
                    'name': n,
                    'source': 'optimizer',
                    'filters': f,
                    **exit_info,
                }, fp, indent=2, default=str)
            messagebox.showinfo("Ready", f"Go to ✅ Strategy Validator")
        except Exception as e:
            import traceback; traceback.print_exc()
            messagebox.showerror("Error", str(e))

    tk.Button(btn, text="✅ Validate", command=_validate,
              bg="#e67e22", fg="white", font=("Segoe UI", 8, "bold"),
              relief=tk.FLAT, padx=6, pady=2).pack(side=tk.LEFT, padx=(0, 3))

    def _csv(t=trades_snap, n=strategy_name):
        p = filedialog.asksaveasfilename(defaultextension=".csv",
            initialfile=f"opt_{n.replace(' ', '_')}.csv", filetypes=[("CSV", "*.csv")])
        if p:
            import pandas as pd
            pd.DataFrame(t).to_csv(p, index=False)
            messagebox.showinfo("Exported", f"{len(t)} trades saved")

    tk.Button(btn, text="📁 CSV", command=_csv,
              bg="#6c757d", fg="white", font=("Segoe UI", 8, "bold"),
              relief=tk.FLAT, padx=6, pady=2).pack(side=tk.LEFT)



def _show_opt_results(candidates):
    """Show optimizer results filtered by minimum WR, with working save buttons."""
    if _opt_results_frame is None:
        return
    for w in _opt_results_frame.winfo_children():
        w.destroy()

    if not candidates:
        tk.Label(_opt_results_frame, text="No candidates found.",
                font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=10)
        return

    from project2_backtesting.strategy_refiner import compute_stats_summary

    # ── Real-time filters ──
    filter_frame = tk.LabelFrame(_opt_results_frame, text="Filter Results",
                                  font=("Segoe UI", 9, "bold"), bg=BG, fg=DARK,
                                  padx=8, pady=5)
    filter_frame.pack(fill="x", padx=5, pady=(4, 6))

    filter_row1 = tk.Frame(filter_frame, bg=BG)
    filter_row1.pack(fill="x")
    filter_row2 = tk.Frame(filter_frame, bg=BG)
    filter_row2.pack(fill="x", pady=(3, 0))

    # Row 1: WR + Trades + PF
    tk.Label(filter_row1, text="Min WR:", font=("Segoe UI", 8), bg=BG, fg="#555").pack(side=tk.LEFT)
    wr_var = tk.StringVar(value="0")
    tk.Entry(filter_row1, textvariable=wr_var, width=4, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 8))
    tk.Label(filter_row1, text="%", font=("Segoe UI", 8), bg=BG, fg="#555").pack(side=tk.LEFT, padx=(0, 10))

    tk.Label(filter_row1, text="Min Trades:", font=("Segoe UI", 8), bg=BG, fg="#555").pack(side=tk.LEFT)
    trades_var = tk.StringVar(value="10")
    tk.Entry(filter_row1, textvariable=trades_var, width=5, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 10))

    tk.Label(filter_row1, text="Min PF:", font=("Segoe UI", 8), bg=BG, fg="#555").pack(side=tk.LEFT)
    pf_var = tk.StringVar(value="0")
    tk.Entry(filter_row1, textvariable=pf_var, width=4, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 10))

    # Row 2: Max trades/day + sort
    tk.Label(filter_row2, text="Max trades/day:", font=("Segoe UI", 8), bg=BG, fg="#555").pack(side=tk.LEFT)
    tpd_var = tk.StringVar(value="99")
    tk.Entry(filter_row2, textvariable=tpd_var, width=3, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 10))

    tk.Label(filter_row2, text="Sort by:", font=("Segoe UI", 8), bg=BG, fg="#555").pack(side=tk.LEFT)
    sort_var = tk.StringVar(value="score")
    ttk.Combobox(filter_row2, textvariable=sort_var,
                  values=["score", "win_rate", "total_pips", "count", "avg_pips", "trades_per_day"],
                  width=12, state="readonly").pack(side=tk.LEFT, padx=(2, 10))

    # Results container (separate from filters so we can clear just the cards)
    cards_frame = tk.Frame(_opt_results_frame, bg=BG)
    cards_frame.pack(fill="both", expand=True)

    _all_candidates = list(candidates)

    # Cache presets once — calling get_prop_firm_presets() inside _apply_filters
    # (which fires on every keystroke) reads JSON files from disk each time.
    try:
        from project2_backtesting.strategy_refiner import get_prop_firm_presets as _gpfp
        _cached_presets = _gpfp()
    except Exception:
        _cached_presets = {}

    def _apply_filters(*_):
        """Re-filter and re-render cards in real time."""
        # Parse filter values safely
        try: min_wr = float(wr_var.get()) / 100.0
        except ValueError: min_wr = 0
        try: min_trades = int(trades_var.get())
        except ValueError: min_trades = 0
        try: min_pf = float(pf_var.get())
        except ValueError: min_pf = 0
        try: max_tpd = float(tpd_var.get())
        except ValueError: max_tpd = 999
        sort_key = sort_var.get()

        # Clear old cards
        for w in cards_frame.winfo_children():
            w.destroy()

        # Filter
        filtered = []
        for c in _all_candidates:
            s = c.get('stats') or compute_stats_summary(c.get('trades', []))
            wr = s.get('win_rate', 0) or 0
            if (wr or 0) > 1:
                wr = (wr or 0) / 100
            count = s.get('count', 0) or 0
            pf = s.get('profit_factor', 0) or 0
            tpd = s.get('trades_per_day', 0) or 0

            if (wr or 0) >= min_wr and (count or 0) >= min_trades and (pf or 0) >= min_pf and (tpd or 0) <= max_tpd:
                filtered.append((c, s))

        # Sort
        def _sort_key(x):
            c, s = x
            if sort_key == 'score':
                return c.get('score', 0) or 0
            elif sort_key == 'win_rate':
                return s.get('win_rate', 0) or 0
            elif sort_key == 'total_pips':
                return s.get('total_pips', 0) or 0
            elif sort_key == 'count':
                return s.get('count', 0) or 0
            elif sort_key == 'avg_pips':
                return s.get('avg_pips', 0) or 0
            elif sort_key == 'trades_per_day':
                return s.get('trades_per_day', 0) or 0
            return c.get('score', 0) or 0

        filtered.sort(key=_sort_key, reverse=True)

        # Count label
        tk.Label(cards_frame,
                 text=f"Showing {len(filtered)} of {len(_all_candidates)} strategies",
                 font=("Segoe UI", 10, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(4, 4))

        if not filtered:
            tk.Label(cards_frame, text="No strategies match the filters. Try loosening them.",
                     font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=10)
            _update_scroll()
            return

        # Save All button
        def _save_all():
            from shared.saved_rules import save_rule
            saved = 0
            for c, s in filtered:
                try:
                    # WHY: Same as _save fix — need rule_combo + trades for validator
                    # CHANGED: April 2026 — include rule_combo + trades in batch save
                    save_data = {
                        'rule_combo': c.get('name', '?'),                    # NEW
                        'trades': list(c.get('trades', [])),                 # NEW
                        'conditions': [],
                        'prediction': 'WIN',
                        'win_rate': s.get('win_rate', 0),
                        'avg_pips': s.get('avg_pips', 0),
                        'total_pips': s.get('total_pips', 0),
                        'net_total_pips': s.get('total_pips', 0),
                        'total_trades': s.get('count', 0),
                        'filters_applied': {k: v for k, v in (c.get('filters_applied') or {}).items()
                                           if k not in ('firm_data', 'description', 'stage')},
                        # Also preserve exit strategy info if available
                        'exit_class': c.get('exit_class', ''),
                        'exit_params': c.get('exit_params', {}),
                        'exit_name': c.get('exit_name', ''),
                        'entry_timeframe': c.get('entry_timeframe', 'H1'),
                        'risk_settings': {
                            'risk_pct': float(c.get('risk_pct') or (_risk_var.get() if _risk_var else 1.0) or 1.0),
                            'account_size': int(float(_acct_var.get() or 100000)) if _acct_var else 100000,
                            'firm': _opt_target_var.get() if _opt_target_var else '',
                            'stage': _stage_var.get() if _stage_var else 'Funded',
                        },
                        'eval_settings': _build_eval_settings(
                            _opt_target_var.get() if _opt_target_var else '',
                            _stage_var.get() if _stage_var else 'Evaluation',
                            project_root
                        ),
                    }
                    for rule in c.get('rules', []):
                        if rule.get('prediction') == 'WIN':
                            save_data['conditions'].extend(rule.get('conditions', []))
                    # Inject broker specs (fields not already present)
                    try:
                        import sys as _bs2_sys
                        _bs2_p1 = os.path.join(project_root, 'project1_reverse_engineering')
                        if _bs2_p1 not in _bs2_sys.path:
                            _bs2_sys.path.insert(0, _bs2_p1)
                        import config_loader as _bs2_cl
                        _bs2_cfg = _bs2_cl.load()
                        for _bs2_k in ('pip_value_per_lot', 'spread', 'commission_per_lot',
                                       'contract_size', 'pip_size'):
                            _bs2_v = _bs2_cfg.get(_bs2_k)
                            if _bs2_v is not None and _bs2_k not in save_data:
                                try:
                                    save_data[_bs2_k] = float(_bs2_v)
                                except (TypeError, ValueError):
                                    pass
                    except Exception:
                        pass
                    save_rule(save_data, source=f"Optimizer: {c.get('name', '?')}")
                    saved += 1
                except Exception:
                    pass
            messagebox.showinfo("Saved", f"Saved {saved} strategies to 💾 Saved Rules!")
            # WHY: Refresh the panel to show newly saved rules in the dropdown
            # CHANGED: April 2026 — auto-refresh after save all
            try:
                refresh()
            except Exception:
                pass

        tk.Button(cards_frame, text=f"💾 Save All {len(filtered)} Strategies",
                  command=_save_all,
                  bg="#28a745", fg="white", font=("Segoe UI", 9, "bold"),
                  relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(pady=(0, 6))

        # Dollar conversion
        # WHY: Risk must come from the candidate/rule, not UI.
        #      UI shows 0.8% or 1.0% but rule has 0.3% (margin-capped).
        #      Wrong risk inflates DD by 3x.
        # CHANGED: April 2026 — risk from candidate, not UI
        try:
            acct = float(_acct_var.get()) if _acct_var else 100000
            # Read risk from candidates (they carry risk_pct from optimizer)
            _first_cand_risk = 0
            if filtered:
                _first_cand_risk = float(filtered[0][0].get('risk_pct', 0) or 0)
            if _first_cand_risk > 0:
                risk = _first_cand_risk
            else:
                risk = float(_risk_var.get()) if _risk_var else 1.0
        except Exception:
            acct = 100000
            risk = 1.0
        pip_value = _srp_pip_value
        sl_pips = _srp_sl_pips
        lot_size = (acct * risk / 100) / (sl_pips * pip_value)
        dollar_per_pip = pip_value * lot_size

        # Firm info
        challenge_fee = 0
        profit_split = 80
        try:
            firm = _opt_target_var.get() if _opt_target_var else ""
            preset = _cached_presets.get(firm, {})
            firm_data = preset.get('firm_data')
            if firm_data:
                costs = firm_data['challenges'][0].get('costs', {})
                fee_by_size = costs.get('challenge_fee_by_size', {})
                challenge_fee = fee_by_size.get(str(int(acct)), 0)
                profit_split = firm_data['challenges'][0].get('funded', {}).get('profit_split_pct', 80)
        except Exception:
            pass

        # Render cards
        for i, (cand, stats) in enumerate(filtered, 1):
            try:
                _render_opt_card(cards_frame, i, cand, stats, dollar_per_pip,
                                  acct, challenge_fee, profit_split, risk, firm_data)
            except Exception as e:
                import traceback; traceback.print_exc()
                err = tk.Frame(cards_frame, bg="#fff0f0", highlightbackground="#dc3545",
                               highlightthickness=1, padx=12, pady=8)
                err.pack(fill="x", padx=5, pady=4)
                tk.Label(err, text=f"#{i}: {cand.get('name','?')} — render error: {e}",
                         font=("Segoe UI", 9), bg="#fff0f0", fg="#dc3545").pack(anchor="w")

        _update_scroll()

    def _update_scroll():
        """Force scroll region update."""
        try:
            _opt_results_frame.update_idletasks()
            if _scroll_canvas:
                _scroll_canvas.configure(scrollregion=_scroll_canvas.bbox("all"))
        except Exception:
            pass

    # Debounce filter traces — without this every keystroke triggers a full
    # card rebuild (destroy + recreate all widgets in cards_frame).
    _filter_debounce_id = [None]
    def _apply_filters_debounced(*_):
        if _filter_debounce_id[0]:
            cards_frame.after_cancel(_filter_debounce_id[0])
        _filter_debounce_id[0] = cards_frame.after(150, _apply_filters)

    for var in [wr_var, trades_var, pf_var, tpd_var]:
        var.trace_add("write", _apply_filters_debounced)
    sort_var.trace_add("write", _apply_filters_debounced)

    # Initial render
    _apply_filters()



def _show_candidate_trades(trades):
    """Display candidate trades in the trade list section."""
    if _trade_list_frame is None:
        return
    _display_trade_list(trades, _trade_list_frame)
    # Scroll to trade list
    if _scroll_canvas:
        _scroll_canvas.yview_moveto(0.5)


def _draw_monthly_chart(canvas, tooltip, trades):
    """Monthly P&L bar chart — one row per year, 12 bars Jan-Dec, scrollable."""
    import calendar as _cal_mod
    from project2_backtesting.strategy_refiner import compute_monthly_pnl

    try:
        from project2_backtesting.panels.configuration import load_config
        cfg = load_config()
        _acct   = float(cfg.get('starting_capital', '100000'))
        _risk   = float(cfg.get('risk_pct', '1.0'))
        _pip_v  = float(cfg.get('pip_value_per_lot', '1.0'))
    except Exception:
        _acct, _risk, _pip_v = 100000, 1.0, 1.0

    canvas.delete("all")
    tooltip.place_forget()

    monthly = compute_monthly_pnl(trades, account_size=_acct,
                                   risk_pct=_risk, pip_value=_pip_v)
    if not monthly:
        canvas.create_text(200, 80, text="No trade data", font=("Arial", 11), fill="#888")
        canvas.configure(scrollregion=(0, 0, 400, 160))
        return

    # ── Pre-compute per-day pips for each month (best/worst day + calendar) ──
    import pandas as _pd_m
    day_pips_by_month = {}   # {'2024-03': {'2024-03-05': 120.0, ...}, ...}
    day_trades_by_month = {}  # {'2024-03': {'2024-03-05': [trade,...], ...}, ...}
    for _t in trades:
        try:
            _ts = _pd_m.to_datetime(_t.get('entry_time', ''))
            _mkey = _ts.strftime('%Y-%m')
            _dkey = _ts.strftime('%Y-%m-%d')
            _pips = float(_t.get('net_pips', 0) or 0)
            day_pips_by_month.setdefault(_mkey, {})
            day_pips_by_month[_mkey][_dkey] = day_pips_by_month[_mkey].get(_dkey, 0) + _pips
            day_trades_by_month.setdefault(_mkey, {})
            day_trades_by_month[_mkey].setdefault(_dkey, []).append(_t)
        except Exception:
            continue

    # ── Layout constants ──────────────────────────────────────────────────────
    w         = canvas.winfo_width() or 800
    LABEL_W   = 42   # year label on left
    TOTAL_W   = 68   # year total on right
    HDR_H     = 18   # month-name header at top
    ROW_H     = 90   # height per year row
    BAR_PAD   = 0.15 # fraction of slot used as gap between bars
    MONTH_ABR = ['Jan','Feb','Mar','Apr','May','Jun',
                 'Jul','Aug','Sep','Oct','Nov','Dec']

    # Group data by year
    years_data = {}   # {'2023': {1: m_dict, 3: m_dict, ...}, ...}
    for m in monthly:
        yr  = m['month'][:4]
        mon = int(m['month'][5:7])
        years_data.setdefault(yr, {})[mon] = m
    sorted_years = sorted(years_data.keys())
    n_years = len(sorted_years)

    total_canvas_h = HDR_H + n_years * ROW_H + 10
    canvas.configure(scrollregion=(0, 0, w, total_canvas_h))

    # ── Global scale (shared across all years) ────────────────────────────────
    all_pnls = [m['pnl_pips'] for m in monthly]
    g_max = max(max(all_pnls), 1)
    g_min = min(min(all_pnls), -1)
    g_range = g_max - g_min

    chart_w  = w - LABEL_W - TOTAL_W
    slot_w   = chart_w / 12
    bar_w    = max(4, int(slot_w * (1 - BAR_PAD * 2)))

    # ── Draw month-name header (once, at top) ─────────────────────────────────
    for mi in range(12):
        xc = LABEL_W + mi * slot_w + slot_w / 2
        canvas.create_text(xc, HDR_H // 2, text=MONTH_ABR[mi],
                           font=("Arial", 7, "bold"), fill="#555")

    # ── Draw each year row ────────────────────────────────────────────────────
    bar_hit_areas = []  # (x1, y1, x2, y2, m_dict, yr_str)

    for yi, yr in enumerate(sorted_years):
        row_y   = HDR_H + yi * ROW_H
        yr_data = years_data[yr]

        # Row separator
        if yi > 0:
            canvas.create_line(0, row_y, w, row_y, fill="#e8e8e8", width=1)

        # Shaded background alternating for readability
        if yi % 2 == 1:
            canvas.create_rectangle(0, row_y, w, row_y + ROW_H,
                                    fill="#fafafa", outline="")

        # Year label
        canvas.create_text(LABEL_W - 4, row_y + ROW_H // 2,
                           text=yr, font=("Arial", 9, "bold"),
                           fill="#333", anchor="e")

        # Zero line for this row
        zero_y = row_y + int(ROW_H * 0.85 * g_max / g_range)
        canvas.create_line(LABEL_W, zero_y, w - TOTAL_W, zero_y,
                           fill="#c8c8c8", dash=(2, 4))
        canvas.create_text(LABEL_W - 4, zero_y,
                           text="0", font=("Arial", 6), fill="#999", anchor="e")

        # Y-axis reference lines (max / min across ALL data)
        for ref_val, ref_lbl in [(g_max, f"{g_max:+.0f}"), (g_min, f"{g_min:+.0f}")]:
            ry = row_y + int(ROW_H * 0.85 * (g_max - ref_val) / g_range)
            if row_y < ry < row_y + ROW_H:
                canvas.create_line(LABEL_W, ry, LABEL_W + 4, ry, fill="#bbb")

        # Year total
        yr_total = sum(m['pnl_pips'] for m in yr_data.values())
        yr_color = "#28a745" if yr_total >= 0 else "#dc3545"
        canvas.create_text(w - TOTAL_W + 6, row_y + ROW_H // 2,
                           text=f"{yr_total:+,.0f}p",
                           font=("Arial", 8, "bold"), fill=yr_color, anchor="w")

        # 12 month bars
        for mi in range(12):
            mon = mi + 1
            xc  = LABEL_W + mi * slot_w + slot_w / 2
            x1  = int(xc - bar_w / 2)
            x2  = int(xc + bar_w / 2)

            if mon not in yr_data:
                # No trades this month — faint tick
                canvas.create_line(xc, zero_y - 2, xc, zero_y + 2,
                                   fill="#ddd", width=1)
                continue

            m = yr_data[mon]
            pnl = m['pnl_pips']

            bar_span = ROW_H * 0.82
            if pnl >= 0:
                bar_top = zero_y - int(bar_span * pnl / g_range)
                bar_bot = zero_y
                color   = "#28a745"
                hover_c = "#1e7e34"
            else:
                bar_top = zero_y
                bar_bot = zero_y + int(bar_span * abs(pnl) / g_range)
                color   = "#dc3545"
                hover_c = "#b21f2d"

            bar_top = max(row_y + 2, bar_top)
            bar_bot = min(row_y + ROW_H - 2, bar_bot)

            rect = canvas.create_rectangle(x1, bar_top, x2, bar_bot,
                                           fill=color, outline="", tags=("bar",))
            bar_hit_areas.append((x1, bar_top, x2, bar_bot, m, yr, hover_c, color, rect))

    # ── Hover + click handlers ────────────────────────────────────────────────
    _hovered = [None]  # track current hovered rect for unhighlight

    def _hit(cx, cy):
        for entry in bar_hit_areas:
            x1, y1, x2, y2 = entry[0], entry[1], entry[2], entry[3]
            if x1 - 4 <= cx <= x2 + 4 and min(y1, y2) - 4 <= cy <= max(y1, y2) + 4:
                return entry
        return None

    def _on_motion(event):
        cx = canvas.canvasx(event.x)
        cy = canvas.canvasy(event.y)
        hit = _hit(cx, cy)
        if hit:
            x1, y1, x2, y2, m, yr, hc, oc, rect = hit
            # Highlight bar
            if _hovered[0] and _hovered[0] is not rect:
                prev = next((e for e in bar_hit_areas if e[8] == _hovered[0]), None)
                if prev:
                    canvas.itemconfig(_hovered[0], fill=prev[7])
            canvas.itemconfig(rect, fill=hc)
            _hovered[0] = rect

            # Build tooltip text
            mkey = m['month']
            dp   = day_pips_by_month.get(mkey, {})
            best_d  = max(dp.values()) if dp else None
            worst_d = min(dp.values()) if dp else None
            mn_num  = int(mkey[5:7])
            mn_name = _cal_mod.month_name[mn_num]
            wr_pct  = m['wins'] / m['trades'] * 100 if m['trades'] > 0 else 0
            be      = m.get('breakeven', 0)
            hold    = m.get('avg_hold_minutes', 0)

            lines = [
                f"  {mn_name} {yr}",
                f"  P&L : {m['pnl_pips']:+,.0f} pips  "
                f"({m['pnl_pct']:+.1f}%  ${m['pnl_dollars']:+,.0f})",
                f"  Trades : {m['trades']}  "
                f"({m['wins']}W / {m['losses']}L"
                + (f" / {be}BE" if be else "") + f"  WR {wr_pct:.0f}%)",
                f"  Active days : {m.get('trading_days', '?')}  "
                f"| Avg/day : {m.get('avg_trades_per_day', 0)}",
            ]
            if best_d is not None:
                lines.append(f"  Best day : {best_d:+,.0f}p  "
                             f"| Worst day : {worst_d:+,.0f}p")
            if hold and hold > 0:
                lines.append(f"  Avg hold : {hold:.0f}m")
            lines.append("  [Click to open month calendar]")

            tooltip.config(text="\n".join(lines))
            # Position tooltip — keep it inside the canvas widget bounds
            tx = min(event.x + 12, canvas.winfo_width() - 260)
            ty = max(4, event.y - 130)
            tooltip.place(in_=canvas, x=tx, y=ty)
        else:
            if _hovered[0]:
                prev = next((e for e in bar_hit_areas if e[8] == _hovered[0]), None)
                if prev:
                    canvas.itemconfig(_hovered[0], fill=prev[7])
                _hovered[0] = None
            tooltip.place_forget()

    def _on_leave(event):
        if _hovered[0]:
            prev = next((e for e in bar_hit_areas if e[8] == _hovered[0]), None)
            if prev:
                canvas.itemconfig(_hovered[0], fill=prev[7])
            _hovered[0] = None
        tooltip.place_forget()

    def _on_click(event):
        cx = canvas.canvasx(event.x)
        cy = canvas.canvasy(event.y)
        hit = _hit(cx, cy)
        if hit:
            _, _, _, _, m, yr, *_ = hit
            _open_month_calendar(
                m, yr,
                day_pips_by_month.get(m['month'], {}),
                day_trades_by_month.get(m['month'], {}),
                canvas.winfo_toplevel()
            )

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind("<Motion>",     _on_motion)
    canvas.bind("<Leave>",      _on_leave)
    canvas.bind("<Button-1>",   _on_click)
    canvas.bind("<MouseWheel>", _on_mousewheel)


def _open_month_calendar(m_data, yr_str, day_pips, day_trades_map, parent):
    """Popup: full-month calendar with per-day P&L + trade detail panel."""
    import calendar as _cal_mod

    month_num  = int(m_data['month'][5:7])
    year_num   = int(yr_str)
    month_name = _cal_mod.month_name[month_num]
    pnl_total  = m_data.get('pnl_pips', 0)
    n_trades   = m_data.get('trades', 0)
    wins       = m_data.get('wins', 0)
    wr         = wins / n_trades * 100 if n_trades > 0 else 0

    popup = tk.Toplevel(parent)
    popup.title(f"{month_name} {yr_str}")
    popup.configure(bg="#f0f2f5")
    popup.resizable(True, True)
    popup.geometry("520x480")

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(popup, bg="#1a1a2a", padx=14, pady=10)
    hdr.pack(fill="x")
    tk.Label(hdr, text=f"{month_name} {yr_str}",
             font=("Segoe UI", 13, "bold"), bg="#1a1a2a", fg="white").pack(side=tk.LEFT)
    stat_color = "#28a745" if pnl_total >= 0 else "#dc3545"
    tk.Label(hdr,
             text=f"{pnl_total:+,.0f} pips  |  {n_trades} trades  |  WR {wr:.0f}%",
             font=("Segoe UI", 10), bg="#1a1a2a", fg=stat_color).pack(side=tk.RIGHT)

    # ── Calendar grid ─────────────────────────────────────────────────────────
    cal_frame = tk.Frame(popup, bg="white", padx=12, pady=10)
    cal_frame.pack(fill="x")

    DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for col, dn in enumerate(DOW):
        tk.Label(cal_frame, text=dn, font=("Segoe UI", 8, "bold"),
                 bg="white", fg="#555", width=7).grid(row=0, column=col, padx=1, pady=1)

    # Compute the key '2024-03-05' format for each day
    def _dkey(d): return f"{year_num:04d}-{month_num:02d}-{d:02d}"

    cal_weeks = _cal_mod.monthcalendar(year_num, month_num)
    detail_frame_ref = [None]   # mutable reference

    for wrow, week in enumerate(cal_weeks):
        for col, day_n in enumerate(week):
            if day_n == 0:
                tk.Frame(cal_frame, bg="white", width=52, height=48).grid(
                    row=wrow + 1, column=col, padx=1, pady=1)
                continue

            dk = _dkey(day_n)
            is_weekend = col >= 5
            has_trades = dk in day_trades_map

            if has_trades:
                dp = day_pips.get(dk, 0)
                bg_c = "#d4f5db" if dp >= 0 else "#fad7d7"
                fg_c = "#1a5c2a" if dp >= 0 else "#7b1c1c"
                txt  = f"{day_n}\n{dp:+.0f}p"
                cursor = "hand2"
                bd_c = "#28a745" if dp >= 0 else "#dc3545"
            elif is_weekend:
                bg_c, fg_c, txt, cursor, bd_c = "#f4f4f4", "#bbb", str(day_n), "", "#e0e0e0"
            else:
                bg_c, fg_c, txt, cursor, bd_c = "white", "#888", str(day_n), "", "#ddd"

            cell = tk.Label(cal_frame, text=txt,
                            font=("Consolas", 8), bg=bg_c, fg=fg_c,
                            width=7, height=3, cursor=cursor,
                            relief="flat", bd=1,
                            highlightbackground=bd_c, highlightthickness=1)
            cell.grid(row=wrow + 1, column=col, padx=1, pady=1)

            if has_trades:
                day_tl = day_trades_map[dk]
                def _click(e, _dk=dk, _day=day_n, _tl=day_tl):
                    _show_day_trades(detail_frame_ref[0], _day, month_name,
                                     yr_str, _dk, _tl)
                def _enter(e, c=cell, bg=bg_c):
                    c.config(relief="raised", highlightthickness=2)
                def _leave(e, c=cell, bg=bg_c):
                    c.config(relief="flat", highlightthickness=1)
                cell.bind("<Button-1>", _click)
                cell.bind("<Enter>",    _enter)
                cell.bind("<Leave>",    _leave)

    # ── Day detail panel ──────────────────────────────────────────────────────
    sep = tk.Frame(popup, bg="#dee2e6", height=1)
    sep.pack(fill="x", padx=10, pady=4)

    detail_outer = tk.Frame(popup, bg="white", padx=14, pady=8)
    detail_outer.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    detail_frame = tk.Frame(detail_outer, bg="white")
    detail_frame.pack(fill="both", expand=True)
    detail_frame_ref[0] = detail_frame

    tk.Label(detail_frame, text="Click a coloured day to see its trades",
             font=("Segoe UI", 9, "italic"), bg="white", fg="#999").pack()

    # Close
    tk.Button(popup, text="Close", command=popup.destroy,
              font=("Segoe UI", 9), bg="#6c757d", fg="white",
              relief=tk.FLAT, padx=14, pady=4, cursor="hand2").pack(pady=(0, 8))


def _show_day_trades(frame, day_n, month_name, yr_str, day_key, trades_list):
    """Fill the detail panel with trades for one clicked day."""
    import pandas as _pd_dt
    for w in frame.winfo_children():
        w.destroy()

    total_pips = sum(t.get('net_pips', 0) or 0 for t in trades_list)
    tc = "#28a745" if total_pips >= 0 else "#dc3545"
    hdr = tk.Frame(frame, bg="white")
    hdr.pack(fill="x", pady=(0, 4))
    tk.Label(hdr, text=f"{month_name} {day_n}, {yr_str}",
             font=("Segoe UI", 10, "bold"), bg="white", fg="#333").pack(side=tk.LEFT)
    tk.Label(hdr, text=f"{total_pips:+.0f} pips  |  {len(trades_list)} trade(s)",
             font=("Segoe UI", 9, "bold"), bg="white", fg=tc).pack(side=tk.RIGHT)

    # Column headers
    cols_frame = tk.Frame(frame, bg="#f8f9fa")
    cols_frame.pack(fill="x")
    for hd, wd in [("Entry", 8), ("Exit", 8), ("Dir", 4), ("Pips", 7), ("Hold", 6)]:
        tk.Label(cols_frame, text=hd, font=("Consolas", 8, "bold"),
                 bg="#f8f9fa", fg="#555", width=wd, anchor="w").pack(side=tk.LEFT, padx=2)

    # Rows (sorted by entry time)
    for t in sorted(trades_list, key=lambda x: x.get('entry_time', '')):
        pips = t.get('net_pips', 0) or 0
        direction = t.get('direction', '?')
        color = "#28a745" if pips > 0 else ("#dc3545" if pips < 0 else "#888")
        try:
            entry_t = _pd_dt.to_datetime(t.get('entry_time', '')).strftime('%H:%M')
        except Exception:
            entry_t = '?'
        try:
            exit_t = _pd_dt.to_datetime(
                t.get('exit_time') or t.get('entry_time', '')).strftime('%H:%M')
        except Exception:
            exit_t = '?'
        hold_m = t.get('hold_minutes', 0) or 0
        if hold_m == 0:
            # compute from timestamps
            try:
                et = _pd_dt.to_datetime(t.get('entry_time', ''))
                xt = _pd_dt.to_datetime(t.get('exit_time', t.get('entry_time', '')))
                hold_m = (xt - et).total_seconds() / 60
            except Exception:
                hold_m = 0
        hold_str = f"{int(hold_m)}m" if hold_m < 60 else f"{hold_m/60:.1f}h"

        row = tk.Frame(frame, bg="white")
        row.pack(fill="x", pady=1)
        for val, wd in [(entry_t, 8), (exit_t, 8), (direction, 4)]:
            tk.Label(row, text=val, font=("Consolas", 8),
                     bg="white", fg="#555", width=wd, anchor="w").pack(side=tk.LEFT, padx=2)
        tk.Label(row, text=f"{pips:+.0f}p", font=("Consolas", 8, "bold"),
                 bg="white", fg=color, width=7, anchor="w").pack(side=tk.LEFT, padx=2)
        tk.Label(row, text=hold_str, font=("Consolas", 8),
                 bg="white", fg="#777", width=6, anchor="w").pack(side=tk.LEFT, padx=2)


def _update_drawdown_display(trades):
    """Update drawdown analysis display."""
    from project2_backtesting.strategy_refiner import compute_three_drawdowns
    from project2_backtesting.panels.configuration import load_config, INSTRUMENT_SPECS
    global _dd_label

    if _dd_label is None:
        return

    if not trades:
        _dd_label.config(text="No trade data", fg="#888")
        return

    # WHY: Old caller hardcoded account_size=100000 and let every other
    #      param default — pip_value=10.0, pip_size=0.01, risk_pct=1.0,
    #      default_sl_pips=150.0 — all XAUUSD. Users on other instruments
    #      saw DD dollar amounts computed from XAUUSD constants. Pull the
    #      actual values from the saved config and INSTRUMENT_SPECS lookup.
    # CHANGED: April 2026 — Phase 30 Fix 4 — pass real config (audit Part C
    #          HIGH #26 caller half)
    try:
        _cfg = load_config()
        _symbol   = (_cfg.get('symbol') or 'XAUUSD').upper()
        _spec     = INSTRUMENT_SPECS.get(_symbol, INSTRUMENT_SPECS.get('XAUUSD', {}))
        _pip_size = float(_spec.get('pip_size', 0.01))
        _pip_val  = float(_cfg.get('pip_value_per_lot', _spec.get('pip_value', 1.0)))
        _risk_pct = float(_cfg.get('risk_pct', 1.0))
        _acct     = float(_cfg.get('starting_capital', 100000))
    except Exception:
        _pip_size = 0.01
        _pip_val  = 1.0
        _risk_pct = 1.0
        _acct     = 100000

    dd = compute_three_drawdowns(
        trades,
        account_size=_acct,
        risk_pct=_risk_pct,
        pip_value=_pip_val,
        pip_size=_pip_size,
    )

    dd_text = (
        f"┌─────────────────────────────────────────────────────────┐\n"
        f"│ 🔴 End-of-Day DD:    {dd['eod_dd_pips']:>8,.0f} pips  ({dd['eod_dd_pct']:>5.1f}%)  │  ← PROP FIRM MEASURES THIS\n"
        f"│    Worst day:        {dd['daily_dd_worst_pips']:>8,.0f} pips  ({dd['daily_dd_worst_pct']:>5.1f}%)  │  date: {dd['daily_dd_worst_date'] or '?'}\n"
        f"│                                                         │\n"
        f"│ 🟡 Realized DD:      {dd['realized_dd_pips']:>8,.0f} pips  ({dd['realized_dd_pct']:>5.1f}%)  │  after trades close\n"
        f"│                                                         │\n"
        f"│ 🟠 Floating DD:      {dd['floating_dd_pips']:>8,.0f} pips  ({dd['floating_dd_pct']:>5.1f}%)  │  during open trades\n"
        f"└─────────────────────────────────────────────────────────┘\n"
    )

    # Color based on prop firm limits
    if dd['daily_dd_worst_pct'] >= 5.0:
        dd_text += "\n⚠️  Worst single day exceeds FTMO 5% daily DD limit!"
        _dd_label.config(fg="#dc3545")
    elif dd['eod_dd_pct'] >= 10.0:
        dd_text += "\n⚠️  Total EOD drawdown exceeds FTMO 10% limit!"
        _dd_label.config(fg="#dc3545")
    else:
        dd_text += f"\n✅  Within FTMO limits (daily: {dd['daily_dd_worst_pct']:.1f}%/5%, total: {dd['eod_dd_pct']:.1f}%/10%)"
        _dd_label.config(fg="#28a745")

    _dd_label.config(text=dd_text)


def _update_breach_display(trades):
    """Update DD breach counter display."""
    from project2_backtesting.strategy_refiner import count_dd_breaches
    global _breach_label

    if _breach_label is None:
        return

    if not trades:
        _breach_label.config(text="No trade data", fg="#888")
        return

    # WHY: Use firm-specific DD limits from rule/config, not generic 5%/10%.
    # CHANGED: April 2026 — firm DD limits in refiner summary
    _sum_daily_lim = 5.0
    _sum_total_lim = 10.0
    _sum_acct = 100000
    try:
        import sys as _sum_sys
        _sum_p1_dir = os.path.join(project_root, 'project1_reverse_engineering')
        if _sum_p1_dir not in _sum_sys.path:
            _sum_sys.path.insert(0, _sum_p1_dir)
        import config_loader as _sum_cl
        _sum_cfg = _sum_cl.load()
        _sum_daily_lim = float(_sum_cfg.get('dd_daily_pct', 0)) or 5.0
        _sum_total_lim = float(_sum_cfg.get('dd_total_pct', 0)) or 10.0
        _sum_acct = float(_sum_cfg.get('prop_firm_account', 0)) or 100000
    except Exception:
        pass
    breaches = count_dd_breaches(trades, account_size=_sum_acct,
                                  daily_dd_limit_pct=_sum_daily_lim, total_dd_limit_pct=_sum_total_lim,
                                  daily_dd_safety_pct=_sum_daily_lim * 0.9,
                                  total_dd_safety_pct=_sum_total_lim * 0.95,
                                  funded_protect=False)

    blown = breaches['blown_count']
    daily_dd_limit = breaches.get('daily_dd_limit_pct', _sum_daily_lim)
    total_dd_limit = breaches.get('total_dd_limit_pct', _sum_total_lim)

    if blown == 0:
        breach_text = (
            f"  ✅ ZERO BREACHES across {breaches['total_months']} months!\n"
            f"     Never exceeded daily {daily_dd_limit}% or total {total_dd_limit}% DD limit.\n"
            f"     Survival rate: {breaches['survival_rate_per_month']}%"
        )
        _breach_label.config(fg="#28a745")
    else:
        breach_text = (
            f"  💀 BLOWN {blown} times in {breaches['total_months']} months\n"
            f"\n"
            f"     Daily DD breaches (≥{daily_dd_limit}%):  {breaches['daily_breaches']} times\n"
            f"     Total DD breaches (≥{total_dd_limit}%): {breaches['total_breaches']} times\n"
            f"\n"
            f"     Worst daily DD:           {breaches['worst_daily_pct']:.1f}%  (limit: {daily_dd_limit}%)\n"
            f"     Worst total DD:           {breaches['worst_total_pct']:.1f}%  (limit: {total_dd_limit}%)\n"
            f"\n"
            f"     Avg days between blows:   {breaches['avg_days_between_blows']} days\n"
            f"     Monthly survival rate:    {breaches['survival_rate_per_month']}%\n"
            f"     Months with blowup:       {breaches['months_blown']} / {breaches['total_months']}\n"
        )

        # Format blow dates as month/year
        import datetime
        all_blow_dates = sorted(set(
            breaches.get('daily_breach_dates', []) +
            breaches.get('total_breach_dates', [])
        ))

        if all_blow_dates:
            breach_text += f"\n\n     Blow timeline:\n"
            for d in all_blow_dates:
                try:
                    dt = datetime.datetime.strptime(d[:10], '%Y-%m-%d')
                    month_str = dt.strftime('%B %Y')  # "October 2008"
                    # Check if daily or total breach
                    breach_type = "daily" if d in breaches.get('daily_breach_dates', []) else "total"
                    breach_text += f"       • {month_str} ({breach_type} DD breach)\n"
                except Exception:
                    breach_text += f"       • {d} (breach)\n"

        # Add safety stops info
        daily_safety = breaches.get('daily_safety_stops', 0)
        total_safety = breaches.get('total_safety_stops', 0)
        total_safety_stops = daily_safety + total_safety

        if total_safety_stops > 0:
            breach_text += f"\n\n  ⚠️ SAFETY STOPS: {total_safety_stops} times (daily:{daily_safety} total:{total_safety})\n"
            breach_text += f"     Bot paused before firm limits — account survived\n"

            # Format safety dates
            all_safety_dates = sorted(set(
                breaches.get('daily_safety_dates', []) +
                breaches.get('total_safety_dates', [])
            ))

            if all_safety_dates:
                breach_text += f"\n     Safety stop timeline:\n"
                for d in all_safety_dates:
                    try:
                        dt = datetime.datetime.strptime(d[:10], '%Y-%m-%d')
                        month_str = dt.strftime('%B %Y')
                        # Check if daily or total safety stop
                        stop_type = "daily" if d in breaches.get('daily_safety_dates', []) else "total"
                        breach_text += f"       • {month_str} ({stop_type} safety limit)\n"
                    except Exception:
                        breach_text += f"       • {d} (safety stop)\n"

        if blown <= 3:
            breach_text += f"\n     🟡 Occasional blows — might pass with good timing"
            _breach_label.config(fg="#e67e22")
        else:
            breach_text += f"\n     🔴 Too many blows — not prop-firm safe"
            _breach_label.config(fg="#dc3545")

    _breach_label.config(text=breach_text)


# ─────────────────────────────────────────────────────────────────────────────
# Panel builder
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _strategy_var, _strat_info_lbl, _base_stats_frame, _eval_info_lbl, _rule_info_lbl
    global _min_hold_var, _max_hold_var, _max_per_day_var, _cooldown_var
    global _session_vars, _day_vars, _results_card, _trade_list_frame
    global _monthly_chart_canvas, _monthly_tooltip, _dd_label, _breach_label
    global _opt_progress_frame, _opt_results_frame, _opt_live_labels
    global _opt_status_lbl, _opt_start_btn, _opt_stop_btn, _opt_target_var, _stage_var
    global _scroll_canvas, _opt_mode_var, _acct_var, _risk_var

    # WHY (Phase A.49 fix): Loading strategies synchronously freezes the UI
    #      when backtest_matrix.json is large (44MB+). Load asynchronously
    #      in a background thread to keep the UI responsive.
    # CHANGED: April 2026 — Phase A.49 fix — async strategy loading

    panel = tk.Frame(parent, bg=BG)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(panel, bg=WHITE, pady=16)
    hdr.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(hdr, text="🔧 Strategy Refiner",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(hdr, text="Optimize your strategy for prop firm challenges",
             bg=WHITE, fg=GREY, font=("Segoe UI", 11)).pack(pady=(4, 0))

    # ── Strategy selector ─────────────────────────────────────────────────────
    sel_frame = tk.Frame(panel, bg=WHITE, padx=20, pady=12)
    sel_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(sel_frame, text="Strategy", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    sel_row = tk.Frame(sel_frame, bg=WHITE)
    sel_row.pack(fill="x")

    # Show loading message initially
    loading_lbl = tk.Label(sel_row, text="⏳ Loading strategies...",
                           font=("Segoe UI", 10), bg=WHITE, fg=GREY)
    loading_lbl.pack(side=tk.LEFT)

    _strategy_var = tk.StringVar(value="")
    dd_container = [None]  # Use list to allow mutation in nested function

    load_btn = tk.Button(sel_row, text="Load", command=_load_selected_strategy,
                         bg=GREEN, fg="white", font=("Segoe UI", 9, "bold"),
                         relief=tk.FLAT, cursor="hand2", padx=14, pady=4,
                         state=tk.DISABLED)  # Disabled until loading completes
    load_btn.pack(side=tk.RIGHT, padx=(10, 0))

    # ── Star/favorite button (created after loading) ──────────────────────
    star_btn_container = [None]  # Placeholder for star button

    # ── Async strategy loading ────────────────────────────────────────────
    def _on_strategies_loaded():
        """Called on main thread after strategies finish loading."""
        nonlocal dd_container, star_btn_container

        # WHY (per-row-delete v3 fix): Instead of destroying and recreating
        #      the Treeview (which causes "invalid command name" errors when
        #      called from a click handler), reuse the existing Treeview if
        #      it exists. Only destroy widgets if we're switching between
        #      "no strategies" and "has strategies" states.
        # CHANGED: April 2026 — per-row-delete v3 bugfix
        existing_tree = dd_container[0] if dd_container else None
        has_existing_tree = existing_tree and hasattr(existing_tree, 'get_children')

        if not _strategies:
            # No strategies found - destroy everything and show message
            if has_existing_tree:
                for widget in sel_row.winfo_children():
                    widget.destroy()
                dd_container[0] = None
            tk.Label(sel_row, text="No backtest results. Run the backtest first.",
                     font=("Segoe UI", 10, "italic"), bg=WHITE, fg=RED).pack(side=tk.LEFT)
        else:
            # WHY: Treeview shows rule ID, exit strategy, WR, PF, trades,
            #      pips at a glance — much better than a truncated dropdown.
            #      _strategy_var stays synced so _get_selected_index() and
            #      all downstream code work unchanged.
            # CHANGED: April 2026 — Treeview replaces Combobox

            # WHY (per-row-delete v3 fix): Reuse existing Treeview if it
            #      exists to avoid "invalid command name" errors when
            #      refreshing from a click handler.
            # CHANGED: April 2026 — per-row-delete v3 bugfix
            if has_existing_tree:
                # Reuse existing tree - just clear items
                _strat_tree = existing_tree
                for item in _strat_tree.get_children():
                    _strat_tree.delete(item)
            else:
                # Create new tree
                tree_frame = tk.Frame(sel_row, bg=WHITE)
                tree_frame.pack(fill="x", expand=True)

                # WHY (per-row-delete): "del" column hosts a clickable 🗑 for
                #      saved rules. A single delete button per row replaces
                #      the old single-row "🗑 Delete" button in sel_row.
                # WHY: Entry timeframe column shows which TF the strategy was
                #      backtested on (M5, M15, H1, etc.). Critical for verifying
                #      that the EA generator uses the correct timeframe.
                # CHANGED: April 2026 — per-row-delete
                #          April 2026 — add entry TF column for verification
                columns = ("star", "#", "rule", "exit", "tf", "trades", "wr", "pf", "net_pips", "avg_pips", "del")
                _strat_tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                           height=min(len(_strategies), 8),
                                           selectmode="browse")

                _strat_tree.heading("star",     text="⭐")
                _strat_tree.heading("#",        text="#")
                _strat_tree.heading("rule",     text="Rule")
                _strat_tree.heading("exit",     text="Exit Strategy")
                _strat_tree.heading("tf",       text="TF")
                _strat_tree.heading("trades",   text="Trades")
                _strat_tree.heading("wr",       text="Win Rate")
                _strat_tree.heading("pf",       text="PF")
                _strat_tree.heading("net_pips", text="Net Pips")
                _strat_tree.heading("avg_pips", text="Avg Pips")
                _strat_tree.heading("del",      text="🗑")

                _strat_tree.column("star",     width=30,  anchor="center")
                _strat_tree.column("#",        width=70,  anchor="center")
                _strat_tree.column("rule",     width=160, anchor="w")
                _strat_tree.column("exit",     width=120, anchor="w")
                _strat_tree.column("tf",       width=45,  anchor="center")
                _strat_tree.column("trades",   width=60,  anchor="center")
                _strat_tree.column("wr",       width=70,  anchor="center")
                _strat_tree.column("pf",       width=60,  anchor="center")
                _strat_tree.column("net_pips", width=90,  anchor="e")
                _strat_tree.column("avg_pips", width=70,  anchor="e")
                _strat_tree.column("del",      width=40,  anchor="center")

                _strat_tree.tag_configure("profitable", foreground="#28a745")
                _strat_tree.tag_configure("losing",     foreground="#dc3545")
                _strat_tree.tag_configure("saved",      foreground="#9b59b6")
                _strat_tree.tag_configure("starred",    foreground="#f39c12")

                tree_scroll = tk.Scrollbar(tree_frame, orient="vertical",
                                           command=_strat_tree.yview)
                _strat_tree.configure(yscrollcommand=tree_scroll.set)
                tree_scroll.pack(side=tk.RIGHT, fill="y")
                _strat_tree.pack(fill="x", expand=True)

                # WHY (per-row-delete v3 fix): Bind event handlers only once
                #      when creating the tree, not on every refresh.
                # CHANGED: April 2026 — per-row-delete v3 bugfix
                dd_container[0] = _strat_tree

            # Populate tree items (happens on both create and refresh)
            # WHY: Saved rules were at position 202+ (after 201 backtest rows).
            #      With 8 visible rows the user couldn't find them. Now shown first.
            # CHANGED: April 2026 — saved rules on top
            _sr_saved   = [s for s in _strategies if s.get('source') == 'saved']
            _sr_sep     = [s for s in _strategies if s.get('source') == 'separator']
            _sr_others  = [s for s in _strategies if s.get('source') not in ('saved', 'separator')]
            _bt_row_n   = 0
            for s in _sr_saved + _sr_sep + _sr_others:
                idx = str(s.get('index', 0))
                rc       = s.get('rule_combo', '?')
                exit_name = s.get('exit_name', s.get('exit_strategy', '?'))
                trades   = s.get('total_trades', s.get('trades', 0))
                wr       = s.get('win_rate', 0)
                wr_str   = f"{wr:.1f}%" if wr > 1 else f"{wr*100:.1f}%"
                pf       = s.get('net_profit_factor', s.get('profit_factor', 0))
                net      = s.get('net_total_pips', s.get('total_pips', 0))
                avg      = s.get('net_avg_pips', s.get('avg_pips', 0))

                is_starred = s.get('is_starred', False)
                source = s.get('source', 'backtest')
                star_display = "⭐" if is_starred else ""

                if source == 'separator':
                    _strat_tree.insert("", "end", iid=idx, values=(
                        "", "── Backtest Results ──", "", "", "", "", "", "", "", "", ""), tags=("separator",))
                    continue
                elif source == 'saved':
                    numeric_id = s.get('id', '')
                    id_display = f"Saved #{numeric_id}"
                    # Show actual conditions in the rule column, not "Saved #N"
                    _sr_dict = s.get('saved_rule', {})
                    _sr_dir  = _sr_dict.get('direction', _sr_dict.get('action', ''))
                    _sr_conds = [c.get('feature', '') for c in _sr_dict.get('conditions', [])]
                    _sr_exit  = s.get('exit_name', s.get('exit_strategy', ''))
                    if _sr_exit in ('', 'Default', '?'):
                        _sr_exit = _sr_dict.get('exit_class', _sr_dict.get('exit_name', ''))
                    rc = (_sr_dir + ' | ' if _sr_dir else '') + ', '.join(
                        f.split('_', 1)[1] if '_' in f else f for f in _sr_conds
                    )
                    exit_name = _sr_exit if _sr_exit and _sr_exit not in ('Default', '?') else '—'
                    wr_s_saved = str(round(wr*100 if wr <= 1 else wr, 1)) + '%'
                    # WHY: Show entry TF from saved rule data
                    # CHANGED: April 2026 — add entry TF column
                    entry_tf_display = (
                        s.get('entry_tf') or
                        s.get('entry_timeframe') or
                        (s.get('stats', {}) or {}).get('entry_tf') or
                        '—'
                    )
                    tag = "saved" if not is_starred else "starred"
                    _strat_tree.insert("", "end", iid=idx, values=(
                        star_display, id_display, rc, exit_name, entry_tf_display, int(trades), wr_s_saved,
                        f"{pf:.2f}", f"{net:+,.0f}", f"{avg:+.1f}", "🗑"
                    ), tags=(tag,))
                    continue
                elif source == 'optimizer':
                    id_display = f"🔧#{idx}"
                    tag = "profitable" if net > 0 else "losing"
                else:
                    _bt_row_n += 1
                    id_display = f"#{_bt_row_n}"
                    tag = "profitable" if net > 0 else "losing"

                if is_starred and tag not in ("saved", "starred"):
                    tag = "starred"

                del_display = "" if source == 'separator' else "🗑"

                # WHY: Show entry TF from backtest matrix data
                # CHANGED: April 2026 — add entry TF column
                entry_tf_display = (
                    s.get('entry_tf') or
                    s.get('entry_timeframe') or
                    (s.get('stats', {}) or {}).get('entry_tf') or
                    '—'
                )

                _strat_tree.insert("", "end", iid=idx, values=(
                    star_display, id_display, rc, exit_name, entry_tf_display, int(trades), wr_str,
                    f"{pf:.2f}", f"{net:+,.0f}", f"{avg:+.1f}", del_display
                ), tags=(tag,))

            # Select first saved rule by default (most relevant to user)
            _tree_children = _strat_tree.get_children()
            if _tree_children:
                global _selected_strat_iid
                _first_saved = next(
                    (c for c in _tree_children
                     if "saved" in (_strat_tree.item(c, "tags") or ())),
                    _tree_children[0]
                )
                _strat_tree.selection_set(_first_saved)
                _selected_strat_iid = _first_saved
                for _fs in _strategies:
                    if str(_fs.get('index', '')) == _first_saved:
                        _strategy_var.set(_fs['label'])
                        break

            # WHY (per-row-delete v3 fix): Set dd_container only when creating
            #      new tree. Event handlers defined below are bound only on
            #      first creation to avoid duplicate bindings.
            # CHANGED: April 2026 — per-row-delete v3 bugfix
            if not has_existing_tree:
                dd_container[0] = _strat_tree

            def _on_tree_select(event=None):
                global _selected_strat_iid
                sel = _strat_tree.selection()
                if not sel:
                    return
                sel_idx = sel[0]
                for s in _strategies:
                    if str(s.get('index', '')) == sel_idx:
                        if s.get('source') == 'separator':
                            return  # ignore separator clicks
                        _selected_strat_iid = sel_idx
                        _strategy_var.set(s['label'])
                        break

            # WHY (per-row-delete v3): Source-dispatched delete with the
            #      bugs from v1/v2 fixed:
            #      - 'saved'     → delete from saved_rules.json. Rule ID
            #                      is now exposed at strategy['id'] by
            #                      the loader (v3 patch); fall back to
            #                      parsing 'saved_N' from strategy['index']
            #                      for legacy safety.
            #      - 'backtest'  → delete from backtest_matrix.json by
            #                      array index (strategy['index'] is the
            #                      array position set at loader:700).
            #                      v3 delete_matrix_row validates with
            #                      rule_combo/exit/tf sanity checks.
            #      - 'optimizer' → show informational message; optimizer
            #                      results live in _validator_optimized.json,
            #                      not the matrix, and rerunning is the
            #                      right way to change them.
            #      - 'separator' → no-op (del cell is empty anyway).
            # CHANGED: April 2026 — per-row-delete v3
            def _on_tree_click(event):
                # Only cell clicks, not headings or separators
                region = _strat_tree.identify_region(event.x, event.y)
                if region != "cell":
                    return
                col_id = _strat_tree.identify_column(event.x)  # e.g. '#10'
                try:
                    col_index = int(col_id.lstrip('#')) - 1
                except (ValueError, AttributeError):
                    return
                if col_index < 0 or col_index >= len(columns):
                    return
                if columns[col_index] != 'del':
                    return

                item_id = _strat_tree.identify_row(event.y)
                if not item_id:
                    return

                # Resolve the strategy record for this row.
                target_strategy = None
                for _s in _strategies:
                    if str(_s.get('index', '')) == item_id:
                        target_strategy = _s
                        break
                if target_strategy is None:
                    return

                src = target_strategy.get('source', 'backtest')
                rule_label = target_strategy.get('label', str(item_id))

                # ── Separator: no-op ──────────────────────────────────
                if src == 'separator':
                    return

                # ── Saved rule: delete_rule from saved_rules.json ─────
                if src == 'saved':
                    # Prefer the top-level 'id' added by the v3 loader
                    # patch; fall back to parsing the 'saved_N' index
                    # format; last resort is the old saved_rule['id']
                    # lookup (still None, but try anyway).
                    rule_id = (
                        target_strategy.get('id')
                        or target_strategy.get('rule_id')
                    )
                    if not rule_id:
                        _idx_val = str(target_strategy.get('index', ''))
                        if _idx_val.startswith('saved_'):
                            rule_id = _idx_val[len('saved_'):]
                    if not rule_id:
                        rule_id = target_strategy.get('saved_rule', {}).get('id')
                    if not rule_id:
                        messagebox.showerror(
                            "Delete Rule",
                            f"Could not resolve rule ID for this saved rule.\n\n"
                            f"Strategy dict keys: "
                            f"{sorted(target_strategy.keys())[:15]}..."
                        )
                        return
                    if not messagebox.askyesno(
                        "Delete Rule",
                        f"Delete saved rule:\n\n{rule_label}\n\n"
                        f"This rewrites saved_rules.json and cannot be undone."
                    ):
                        return
                    try:
                        from shared.saved_rules import delete_rule as _del_rule
                        _del_rule(rule_id)
                        # WHY (per-row-delete v3 fix): Reload to refresh the list.
                        #      Use longer delay to avoid widget errors.
                        # CHANGED: April 2026 — per-row-delete v3 bugfix
                        def _reload():
                            _load_strategies(force=True)
                            _on_strategies_loaded()
                        sel_row.after(100, _reload)
                    except Exception as _de:
                        import traceback as _tb
                        _tb.print_exc()
                        messagebox.showerror("Delete Error", str(_de))
                    return

                # ── Optimizer row: not deletable from here ────────────
                if src == 'optimizer':
                    messagebox.showinfo(
                        "Optimizer Result",
                        "Optimizer results live in _validator_optimized.json, "
                        "not in backtest_matrix.json. To change or remove "
                        "them, re-run the optimizer (it overwrites this file "
                        "on each run)."
                    )
                    return

                # ── Backtest row: delete_matrix_row by array index ────
                # strategy['index'] for backtest rows is the array
                # position in backtest_matrix.json (set at
                # strategy_refiner.py:700 'index': i).
                array_index = target_strategy.get('index')
                if not isinstance(array_index, int):
                    # Refuse to delete if index is unexpectedly a string.
                    messagebox.showerror(
                        "Delete Backtest Row",
                        f"Cannot resolve array index for this row "
                        f"(got {type(array_index).__name__}={array_index!r})."
                    )
                    return

                exp_rc = target_strategy.get('rule_combo', '')
                exp_ex = (target_strategy.get('exit_strategy', '')
                          or target_strategy.get('exit_name', ''))
                exp_tf = target_strategy.get('entry_tf', '') or ''

                if not messagebox.askyesno(
                    "Delete Backtest Row",
                    f"Delete this row from backtest_matrix.json:\n\n"
                    f"{rule_label}\n\n"
                    f"This will be regenerated if you re-run the backtest. "
                    f"Continue?"
                ):
                    return

                try:
                    from project2_backtesting.strategy_refiner import delete_matrix_row as _del_mx
                    print(f"[DEBUG] Deleting: array_index={array_index}, rc={exp_rc}, exit={exp_ex}, tf={exp_tf}")
                    result = _del_mx(
                        array_index=array_index,
                        expected_rule_combo=exp_rc or None,
                        expected_exit_strategy=exp_ex or None,
                        expected_entry_tf=exp_tf if exp_tf else None,
                    )
                    print(f"[DEBUG] Delete result: {result}")
                    if result.get('removed'):
                        # WHY (per-row-delete v3 fix): Reload strategies to fix
                        #      index mismatches. After deleting row 79, what was
                        #      row 80 becomes row 79, etc. We must reload to get
                        #      correct indices. Use deferred call with longer delay.
                        # CHANGED: April 2026 — per-row-delete v3 bugfix
                        print(f"[DEBUG] Delete succeeded, reloading strategies...")
                        def _reload():
                            _load_strategies(force=True)
                            _on_strategies_loaded()
                        # Use 100ms delay to ensure event completes
                        sel_row.after(100, _reload)
                    else:
                        messagebox.showwarning(
                            "Delete Backtest Row",
                            f"Row was not removed.\n\n"
                            f"Reason: {result.get('reason')}"
                        )
                except ValueError as _ve:
                    # Sanity-check failures or structural errors land here.
                    messagebox.showwarning(
                        "Delete Backtest Row",
                        f"Could not delete row safely:\n\n{_ve}"
                    )
                except Exception as _de:
                    import traceback as _tb
                    _tb.print_exc()
                    messagebox.showerror("Delete Error", str(_de))

            # WHY (per-row-delete v3 fix): Bind event handlers only when
            #      creating a new tree to avoid duplicate bindings.
            # CHANGED: April 2026 — per-row-delete v3 bugfix
            if not has_existing_tree:
                _strat_tree.bind("<<TreeviewSelect>>", _on_tree_select)
                _strat_tree.bind("<Button-1>", _on_tree_click, add="+")

            # Enable load button
            load_btn.configure(state=tk.NORMAL)

            # Star button
            def _toggle_star():
                idx = _get_selected_index()
                if idx is None:
                    return
                for s in _strategies:
                    if s.get('index') == idx:
                        rc = s.get('rule_combo', '')
                        es = s.get('exit_strategy', s.get('exit_name', ''))
                        try:
                            from shared.starred import toggle
                            is_now_starred = toggle(rc, es)
                            star_btn.configure(
                                text="⭐ Starred" if is_now_starred else "☆ Star",
                                bg="#f39c12" if is_now_starred else "#95a5a6",
                            )
                            _load_strategies(force=True)
                            # Re-sync label after star reload
                            cur_label = None
                            for s2 in _strategies:
                                if s2.get('index') == idx:
                                    cur_label = s2.get('label')
                                    break
                            if cur_label:
                                _strategy_var.set(cur_label)
                        except ImportError:
                            pass
                        break

            star_btn = tk.Button(sel_row, text="☆ Star", command=_toggle_star,
                                 bg="#95a5a6", fg="white", font=("Segoe UI", 9, "bold"),
                                 relief=tk.FLAT, cursor="hand2", padx=10, pady=4)
            star_btn.pack(side=tk.LEFT, padx=(6, 0))
            star_btn_container[0] = star_btn

            def _update_star_btn(*args):
                idx = _get_selected_index()
                if idx is None:
                    return
                for s in _strategies:
                    if s.get('index') == idx:
                        is_s = s.get('is_starred', False)
                        star_btn.configure(
                            text="⭐ Starred" if is_s else "☆ Star",
                            bg="#f39c12" if is_s else "#95a5a6",
                        )
                        break

            _strategy_var.trace_add('write', _update_star_btn)
            _update_star_btn()

            # Refresh button to reload strategies and rebuild tree
            def _do_refresh():
                try:
                    _load_strategies(force=True)
                    # Rebuild tree by calling _on_strategies_loaded
                    _on_strategies_loaded()
                    messagebox.showinfo("Refreshed", "Strategy list reloaded from disk.")
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    messagebox.showerror("Refresh Error", str(e))

            refresh_btn = tk.Button(sel_row, text="🔄 Refresh", command=_do_refresh,
                                    bg="#3498db", fg="white", font=("Segoe UI", 9, "bold"),
                                    relief=tk.FLAT, cursor="hand2", padx=10, pady=4)
            refresh_btn.pack(side=tk.LEFT, padx=(6, 0))

            # WHY: Diagnostic button to generate detailed report about "No Trades" lookup failures
            # CHANGED: April 2026 — diagnostic button
            diagnose_btn = tk.Button(sel_row, text="🔍 Diagnose", command=_run_lookup_diagnostic,
                                     bg="#8e44ad", fg="white", font=("Segoe UI", 9, "bold"),
                                     relief=tk.FLAT, cursor="hand2", padx=10, pady=4)
            diagnose_btn.pack(side=tk.LEFT, padx=(6, 0))

    def _load_in_background():
        """Background thread: load strategies, then schedule UI update."""
        _load_strategies()
        # Schedule UI update on main thread
        panel.after(0, _on_strategies_loaded)

    # Start background loading
    threading.Thread(target=_load_in_background, daemon=True).start()

    _strat_info_lbl = tk.Label(sel_frame, text="Click Load to load a strategy.",
                                font=("Segoe UI", 9), bg=WHITE, fg=GREY)
    _strat_info_lbl.pack(anchor="w", pady=(5, 0))
    _eval_info_lbl = tk.Label(sel_frame, text="",
                               font=("Segoe UI", 8, "bold"), bg=WHITE, fg="#e65100")
    _eval_info_lbl.pack(anchor="w", pady=(2, 0))

    # ── Scrollable area ───────────────────────────────────────────────────────
    _scroll_canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    vscroll = tk.Scrollbar(panel, orient="vertical", command=_scroll_canvas.yview)
    scroll_frame = tk.Frame(_scroll_canvas, bg=BG)

    scroll_frame.bind("<Configure>",
                      lambda e: _scroll_canvas.configure(
                          scrollregion=_scroll_canvas.bbox("all")))
    cwin = _scroll_canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    _scroll_canvas.configure(yscrollcommand=vscroll.set)
    _scroll_canvas.pack(side="left", fill="both", expand=True, padx=(20, 0))
    vscroll.pack(side="right", fill="y", padx=(0, 20))

    # Safe mousewheel binding — doesn't break other canvases
    def _on_enter(event):
        _scroll_canvas.bind("<MouseWheel>",
            lambda e: _scroll_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        # Linux
        _scroll_canvas.bind("<Button-4>", lambda e: _scroll_canvas.yview_scroll(-3, "units"))
        _scroll_canvas.bind("<Button-5>", lambda e: _scroll_canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        _scroll_canvas.unbind("<MouseWheel>")
        _scroll_canvas.unbind("<Button-4>")
        _scroll_canvas.unbind("<Button-5>")

    _scroll_canvas.bind("<Enter>", _on_enter)
    _scroll_canvas.bind("<Leave>", _on_leave)
    _scroll_canvas.bind("<Configure>",
                        lambda e: _scroll_canvas.itemconfig(cwin, width=e.width))

    # Everything below goes inside scroll_frame
    sf = scroll_frame

    # ── MODE 1: Filters ───────────────────────────────────────────────────────
    mode1_hdr = tk.Frame(sf, bg=WHITE, padx=20, pady=8)
    mode1_hdr.pack(fill="x", padx=5, pady=(5, 0))
    tk.Label(mode1_hdr, text="⚡ Quick Filters (instant preview)",
             font=("Segoe UI", 12, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")

    filters_frame = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    filters_frame.pack(fill="x", padx=5, pady=(0, 5))

    def _filter_row(parent, label, var, from_, to_, resolution=1, is_float=False):
        """Create one filter row with label, scale, and value display."""
        row = tk.Frame(parent, bg=WHITE)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, font=("Segoe UI", 9), bg=WHITE, fg=DARK,
                 width=22, anchor="w").pack(side=tk.LEFT)
        scale = tk.Scale(row, variable=var, from_=from_, to=to_,
                         resolution=resolution, orient=tk.HORIZONTAL,
                         bg=WHITE, highlightthickness=0, length=220,
                         command=lambda v: _schedule_update())
        scale.pack(side=tk.LEFT)
        val_lbl = tk.Label(row, textvariable=var, font=("Segoe UI", 8),
                           bg=WHITE, fg=MIDGREY, width=6)
        val_lbl.pack(side=tk.LEFT, padx=4)
        return scale

    _min_hold_var = tk.DoubleVar(value=0)
    _filter_row(filters_frame, "Min hold time (min):", _min_hold_var, 0, 120, resolution=1)

    _max_per_day_var = tk.IntVar(value=0)
    _filter_row(filters_frame, "Max trades/day (0=unlimited):", _max_per_day_var, 0, 20, resolution=1)

    _cooldown_var = tk.DoubleVar(value=0)
    _filter_row(filters_frame, "Cooldown between trades (min):", _cooldown_var, 0, 480, resolution=5)

    # WHY: min_pips slider removed April 2026 — look-ahead bias.

    # Sessions
    sess_row = tk.Frame(filters_frame, bg=WHITE)
    sess_row.pack(fill="x", pady=3)
    tk.Label(sess_row, text="Sessions:", font=("Segoe UI", 9), bg=WHITE, fg=DARK,
             width=22, anchor="w").pack(side=tk.LEFT)
    for sess in ["Asian", "London", "New York"]:
        var = tk.BooleanVar(value=True)
        _session_vars[sess] = var
        tk.Checkbutton(sess_row, text=sess, variable=var, bg=WHITE,
                       font=("Segoe UI", 9),
                       command=_schedule_update).pack(side=tk.LEFT, padx=5)

    # Days
    day_row = tk.Frame(filters_frame, bg=WHITE)
    day_row.pack(fill="x", pady=3)
    tk.Label(day_row, text="Days:", font=("Segoe UI", 9), bg=WHITE, fg=DARK,
             width=22, anchor="w").pack(side=tk.LEFT)
    for day in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
        var = tk.BooleanVar(value=True)
        _day_vars[day] = var
        tk.Checkbutton(day_row, text=day, variable=var, bg=WHITE,
                       font=("Segoe UI", 9),
                       command=_schedule_update).pack(side=tk.LEFT, padx=3)

    # ── Prop firm presets ─────────────────────────────────────────────────────
    presets_frame = tk.Frame(sf, bg=WHITE, padx=20, pady=8)
    presets_frame.pack(fill="x", padx=5, pady=(0, 5))

    tk.Label(presets_frame, text="Prop firm presets:",
             font=("Segoe UI", 9, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(0, 10))

    from project2_backtesting.strategy_refiner import get_prop_firm_presets
    presets = get_prop_firm_presets()

    def _apply_preset(vals):
        if _min_hold_var:
            _min_hold_var.set(vals.get('min_hold_minutes', 0))
        if _max_per_day_var:
            _max_per_day_var.set(vals.get('max_trades_per_day', 0))
        if _cooldown_var:
            _cooldown_var.set(vals.get('cooldown_minutes', 0))
        for sess, var in _session_vars.items():
            var.set(True)
        for day, var in _day_vars.items():
            var.set(True)
        _schedule_update()

    def _reset_filters():
        _apply_preset({})

    preset_colors = {
        "FTMO-friendly": "#667eea", "Topstep-friendly": "#764ba2",
        "Apex-friendly": "#2d8a4e",
    }
    for pname, pvals in presets.items():
        if pname == "Custom":
            tk.Button(presets_frame, text="Reset", command=_reset_filters,
                      bg=GREY, fg="white", font=("Segoe UI", 8, "bold"),
                      relief=tk.FLAT, cursor="hand2", padx=10, pady=4).pack(side=tk.LEFT, padx=3)
        else:
            col = preset_colors.get(pname, "#667eea")
            filt = {k: v for k, v in pvals.items() if k != 'description'}
            tk.Button(presets_frame, text=pname,
                      command=lambda f=filt: _apply_preset(f),
                      bg=col, fg="white", font=("Segoe UI", 8, "bold"),
                      relief=tk.FLAT, cursor="hand2", padx=10, pady=4).pack(side=tk.LEFT, padx=3)

    # ── Results comparison card ───────────────────────────────────────────────
    rc_outer = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    rc_outer.pack(fill="x", padx=5, pady=(0, 5))
    tk.Label(rc_outer, text="Live results", font=("Segoe UI", 10, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))
    _results_card = tk.Frame(rc_outer, bg=WHITE)
    _results_card.pack(fill="x")
    tk.Label(_results_card, text="Load a strategy to see comparison.",
             font=("Segoe UI", 9, "italic"), bg=WHITE, fg=GREY).pack(anchor="w")

    # ── Action buttons ────────────────────────────────────────────────────────
    actions = tk.Frame(sf, bg=BG, pady=6)
    actions.pack(fill="x", padx=5)

    tk.Button(actions, text="Apply Filters & View Trades",
              command=lambda: _display_trade_list(_filtered_trades, _trade_list_frame),
              bg="#667eea", fg="white", font=("Segoe UI", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=18, pady=8).pack(side=tk.LEFT, padx=(5, 8))

    tk.Button(actions, text="📥 Export Filtered Trades CSV",
              command=lambda: _export_csv(_filtered_trades),
              bg=GREEN, fg="white", font=("Segoe UI", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=18, pady=8).pack(side=tk.LEFT)

    # ── Monthly P&L Chart ─────────────────────────────────────────────────────
    chart_outer = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    chart_outer.pack(fill="x", padx=5, pady=(10, 5))
    tk.Label(chart_outer, text="📊 Monthly P&L  (click a bar to open month calendar)",
             font=("Segoe UI", 10, "bold"), bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 4))

    # Scrollable chart: canvas + vertical scrollbar side by side
    _chart_scroll_frame = tk.Frame(chart_outer, bg=WHITE)
    _chart_scroll_frame.pack(fill="x", pady=5)

    _chart_vscroll = tk.Scrollbar(_chart_scroll_frame, orient="vertical")
    _chart_vscroll.pack(side=tk.RIGHT, fill="y")

    _monthly_chart_canvas = tk.Canvas(
        _chart_scroll_frame, bg="#ffffff", height=260,
        highlightthickness=1, highlightbackground="#ddd",
        yscrollcommand=_chart_vscroll.set
    )
    _monthly_chart_canvas.pack(side=tk.LEFT, fill="x", expand=True)
    _chart_vscroll.config(command=_monthly_chart_canvas.yview)

    # Mousewheel on the chart scrolls the chart itself, not the outer panel
    def _chart_mousewheel(event):
        _monthly_chart_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    _monthly_chart_canvas.bind("<MouseWheel>", _chart_mousewheel)

    # Multi-line tooltip (hidden until hover)
    _monthly_tooltip = tk.Label(
        chart_outer, text="", font=("Consolas", 8),
        bg="#1a1a2a", fg="white", padx=8, pady=6,
        justify="left", relief="flat"
    )
    # Forward scroll from tooltip to chart
    def _tooltip_wheel(event):
        _monthly_tooltip.place_forget()
        _monthly_chart_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    _monthly_tooltip.bind("<MouseWheel>", _tooltip_wheel)

    # Draw placeholder
    _monthly_chart_canvas.create_text(
        200, 130, text="Load a strategy to see monthly P&L",
        font=("Arial", 11), fill="#888"
    )

    # Debounce chart resize
    _chart_resize_id = [None]
    def _on_chart_resize(event):
        if _chart_resize_id[0]:
            _monthly_chart_canvas.after_cancel(_chart_resize_id[0])
        _chart_resize_id[0] = _monthly_chart_canvas.after(
            200, lambda: _draw_monthly_chart(_monthly_chart_canvas, _monthly_tooltip, _filtered_trades)
        )
    _monthly_chart_canvas.bind("<Configure>", _on_chart_resize)

    # ── Drawdown Analysis ─────────────────────────────────────────────────────
    dd_outer = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    dd_outer.pack(fill="x", padx=5, pady=(5, 5))
    tk.Label(dd_outer, text="📉 Drawdown Analysis", font=("Segoe UI", 10, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    _dd_label = tk.Label(dd_outer, text="Load a strategy to see drawdown analysis",
                          font=("Courier", 9), bg=WHITE, fg="#333",
                          justify=tk.LEFT, anchor="nw")
    _dd_label.pack(fill="x")

    # ── DD Breach Counter ─────────────────────────────────────────────────────
    breach_outer = tk.Frame(sf, bg=WHITE, padx=20, pady=10)
    breach_outer.pack(fill="x", padx=5, pady=(5, 5))
    tk.Label(breach_outer, text="💀 Prop Firm Breach Counter", font=("Segoe UI", 10, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    _breach_label = tk.Label(breach_outer, text="Load a strategy to see breach analysis",
                              font=("Courier", 9), bg=WHITE, fg="#333",
                              justify=tk.LEFT, anchor="nw")
    _breach_label.pack(fill="x")

    from shared.tooltip import add_tooltip
    add_tooltip(_breach_label,
                "💀 Prop Firm Breach Counter\n\n"
                "Simulates your strategy across the full backtest period.\n"
                "Every time drawdown exceeds the prop firm limit,\n"
                "the account is 'blown' and restarted — just like\n"
                "a real failed challenge.\n\n"
                "Daily DD breach: lost too much in ONE day\n"
                "Total DD breach: equity dropped too far from peak\n\n"
                "0 blows = strategy never violated limits\n"
                "1-3 blows = occasional, might pass with timing\n"
                "4+ blows = too risky for prop firms")

    # ── Trade list ────────────────────────────────────────────────────────────
    tl_hdr = tk.Frame(sf, bg=WHITE, padx=20, pady=6)
    tl_hdr.pack(fill="x", padx=5, pady=(5, 0))
    tk.Label(tl_hdr, text="📋 Filtered Trade List",
             font=("Segoe UI", 11, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")

    _trade_list_frame = tk.Frame(sf, bg=BG)
    _trade_list_frame.pack(fill="x", padx=5)
    tk.Label(_trade_list_frame,
             text="Click 'Apply Filters & View Trades' to populate.",
             font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(pady=8)

    # ── Separator ─────────────────────────────────────────────────────────────
    tk.Frame(sf, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=12)

    # ── MODE 2: Deep Optimizer ────────────────────────────────────────────────
    opt_hdr = tk.Frame(sf, bg=WHITE, padx=20, pady=8)
    opt_hdr.pack(fill="x", padx=5, pady=(0, 5))
    tk.Label(opt_hdr, text="🧠 Deep Optimizer (10–30 min)",
             font=("Segoe UI", 12, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")
    tk.Label(opt_hdr,
             text="Tests filter combinations and scores them. Runs in background — UI stays responsive.",
             font=("Segoe UI", 9), bg=WHITE, fg=MIDGREY).pack(anchor="w", pady=(2, 0))

    # ── Optimizer Mode Description ────────────────────────────
    mode_desc_frame = tk.Frame(sf, bg="#fff3cd", padx=12, pady=8)
    mode_desc_frame.pack(fill="x", padx=10, pady=(0, 5))

    tk.Label(mode_desc_frame,
             text="🔬 Deep Optimizer — Work In Progress",
             font=("Segoe UI", 10, "bold"), bg="#fff3cd", fg="#856404").pack(anchor="w")
    tk.Label(mode_desc_frame,
             text="The optimizer tests different filter combinations and rule modifications\n"
                  "to find the best version of your strategy for a specific prop firm.\n"
                  "More optimization modes will be added over time.\n"
                  "Select one or both modes below:",
             font=("Segoe UI", 9), bg="#fff3cd", fg="#856404",
             justify=tk.LEFT).pack(anchor="w", pady=(3, 0))

    # ── Mode radio buttons ────────────────────────────────────
    modes_frame = tk.LabelFrame(sf, text="Optimization Mode",
                                 font=("Segoe UI", 10, "bold"), bg=BG, fg=DARK,
                                 padx=12, pady=8)
    modes_frame.pack(fill="x", padx=10, pady=(0, 5))

    _opt_mode_var = tk.StringVar(value="quick")

    # Radio 1: Quick optimization (filter existing trades)
    quick_rb = tk.Radiobutton(modes_frame,
        text="⚡ Quick Optimize — filter existing trades (seconds)",
        variable=_opt_mode_var,
        value="quick",
        font=("Segoe UI", 9, "bold"), bg=BG, fg="#333",
        selectcolor=BG, activebackground=BG, anchor="w")
    quick_rb.pack(fill="x", pady=(0, 2))

    quick_desc = tk.Label(modes_frame,
        text="Uses only the indicators your current rules need. Tests session filters,\n"
             "max trades/day, cooldown, hold time. Very fast — finishes in seconds.",
        font=("Segoe UI", 8), bg=BG, fg="#888", justify=tk.LEFT)
    quick_desc.pack(fill="x", padx=(24, 0), pady=(0, 8))

    # Radio 2: Generate new trades (modify rules)
    deep_rb = tk.Radiobutton(modes_frame,
        text="🧬 Deep Explore — modify rules, find new entries (minutes)",
        variable=_opt_mode_var,
        value="deep",
        font=("Segoe UI", 9, "bold"), bg=BG, fg="#333",
        selectcolor=BG, activebackground=BG, anchor="w")
    deep_rb.pack(fill="x", pady=(0, 2))

    deep_desc = tk.Label(modes_frame,
        text="Loads the top 30 most important indicators from Project 1 analysis.\n"
             "Shifts thresholds ±10-20%, adds new conditions, removes weak ones.\n"
             "Re-runs backtests with each modification. Slower but finds NEW trade setups.",
        font=("Segoe UI", 8), bg=BG, fg="#888", justify=tk.LEFT)
    deep_desc.pack(fill="x", padx=(24, 0), pady=(0, 5))

    # ── Add hover tooltips with full details ──────────────────
    from shared.tooltip import add_tooltip

    # Build dynamic tooltip text showing actual indicators
    def _build_quick_tooltip():
        """Build tooltip showing which indicators quick mode uses."""
        text = (
            "⚡ QUICK OPTIMIZE MODE\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "What it does:\n"
            "  • Tests prop firm filter presets (FTMO, Topstep, Apex, etc.)\n"
            "  • Sweeps min hold time: 1, 2, 5, 10, 15, 20, 30 min\n"
            "  • Sweeps max trades/day: 1, 2, 3, 5, 8\n"
            "  • Tests session combos: London, NY, London+NY, Asian+London\n"
            "  • Tests combined filters: hold + max/day together\n\n"
            "Does NOT change:\n"
            "  • Entry rules — same conditions, same thresholds\n"
            "  • Exit strategy — same SL/TP\n"
            "  • Indicators used — no new ones added\n\n"
            "Speed: ~2-5 seconds\n"
            "Best for: fine-tuning a strategy that already works\n\n"
        )

        # Show which indicators the current rules use
        try:
            idx = _get_selected_index()
            if idx is not None:
                for s in _strategies:
                    if s['index'] == idx:
                        text += f"Current strategy: {s.get('rule_combo', '?')} × {s.get('exit_name', '?')}\n"
                        break
        except Exception:
            pass

        return text

    def _build_deep_tooltip():
        """Build tooltip showing which indicators deep mode explores."""
        text = (
            "🧬 DEEP EXPLORE MODE\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "What it does:\n"
            "  • Shifts each condition threshold ±10% and ±20%\n"
            "    Example: H4_adx_14 > 18.5 → tries > 16.7, > 14.8, > 20.4, > 22.2\n\n"
            "  • Adds NEW indicator conditions from the top 30 features\n"
            "    Example: adds 'D1_atr_50 > 12.5' if it improves win rate\n\n"
            "  • Removes weak conditions one by one\n"
            "    Example: drops 'M15_volume_change > -0.35' if it doesn't help\n\n"
            "  • Tests 5 exit strategies with each modified rule set:\n"
            "    - Fixed SL/TP: 150/300, 100/200, 200/400\n"
            "    - Trailing Stop: 100 pip trail, 50 pip trail\n\n"
            "Speed: 2-10 minutes (depends on number of rules)\n"
            "Best for: discovering new trading patterns\n\n"
        )

        # Show which indicators will be explored
        try:
            import json as _json
            report_path = os.path.join(project_root, 'project1_reverse_engineering',
                                        'outputs', 'analysis_report.json')
            if os.path.exists(report_path):
                with open(report_path) as f:
                    report = _json.load(f)

                # Current rules' indicators
                from helpers import normalize_conditions
                win_rules = [normalize_conditions(r) for r in report.get('rules', [])
                             if r.get('prediction') == 'WIN']
                current_features = set()
                for r in win_rules:
                    for c in r.get('conditions', []):
                        current_features.add(c['feature'])

                text += f"CURRENT rules use {len(current_features)} indicators:\n"
                for feat in sorted(current_features)[:10]:
                    text += f"  • {feat}\n"
                if len(current_features) > 10:
                    text += f"  ... +{len(current_features) - 10} more\n"

                # Top features from importance ranking
                top_features = report.get('feature_importance', {}).get('top_20', [])
                if top_features:
                    text += f"\nTOP features to explore (from Project 1):\n"
                    for feat, score in top_features[:15]:
                        already = "✓ (in rules)" if feat in current_features else "NEW"
                        text += f"  • {feat}  [{already}]\n"
                    if len(top_features) > 15:
                        text += f"  ... +{len(top_features) - 15} more\n"

                text += f"\nRules that will be modified:\n"
                for i, r in enumerate(win_rules[:5]):
                    wr = r.get('win_rate', 0)
                    wr_str = f"{wr:.0%}" if wr <= 1 else f"{wr:.0f}%"
                    conds = [c['feature'] for c in r.get('conditions', [])]  # already normalized above
                    text += f"  Rule {i+1} (WR {wr_str}): {', '.join(conds[:3])}\n"
                if len(win_rules) > 5:
                    text += f"  ... +{len(win_rules) - 5} more rules\n"
        except Exception:
            text += "  (Load a strategy to see which indicators will be explored)\n"

        return text

    # Apply tooltips
    add_tooltip(quick_rb, _build_quick_tooltip, wraplength=450)
    add_tooltip(quick_desc, _build_quick_tooltip, wraplength=450)
    add_tooltip(deep_rb, _build_deep_tooltip, wraplength=450)
    add_tooltip(deep_desc, _build_deep_tooltip, wraplength=450)

    # ── Lock & protect mode ───────────────────────────────────────────────────
    # WHY: Sometimes you want to optimize ONLY the filters and leave the entry
    #      rule alone. Or test "is the exit fine but I need better SL/TP?"
    #      Lock checkboxes restrict what the optimizer is allowed to change.
    # CHANGED: April 2026 — surgical optimization mode
    lock_frame = tk.LabelFrame(
        sf,
        text="🔒 Lock & Protect (restrict what the optimizer changes)",
        font=("Segoe UI", 9, "bold"),
        bg=WHITE,
        padx=10,
        pady=8,
    )
    lock_frame.pack(fill="x", padx=10, pady=(8, 5))

    global _lock_entry_var, _lock_exit_var, _lock_sltp_var, _lock_filters_var
    _lock_entry_var   = tk.BooleanVar(value=False)
    _lock_exit_var    = tk.BooleanVar(value=False)
    _lock_sltp_var    = tk.BooleanVar(value=False)
    _lock_filters_var = tk.BooleanVar(value=False)

    tk.Checkbutton(lock_frame, text="Lock entry rule (don't modify conditions)",
                   variable=_lock_entry_var, bg=WHITE,
                   font=("Segoe UI", 9)).pack(anchor="w")
    tk.Checkbutton(lock_frame, text="Lock exit type (don't change FixedSL/ATR/Hybrid/etc)",
                   variable=_lock_exit_var, bg=WHITE,
                   font=("Segoe UI", 9)).pack(anchor="w")
    tk.Checkbutton(lock_frame, text="Lock SL/TP values (don't change pip distances)",
                   variable=_lock_sltp_var, bg=WHITE,
                   font=("Segoe UI", 9)).pack(anchor="w")
    tk.Checkbutton(lock_frame, text="Lock filters (don't change cooldown/min_hold/max_trades)",
                   variable=_lock_filters_var, bg=WHITE,
                   font=("Segoe UI", 9)).pack(anchor="w")

    tk.Label(lock_frame,
             text="Tip: Lock 3 of these and the optimizer focuses on the 4th — surgical.",
             font=("Segoe UI", 8), fg="#666", bg=WHITE).pack(anchor="w", pady=(4, 0))

    opt_controls = tk.Frame(sf, bg=WHITE, padx=20, pady=8)
    opt_controls.pack(fill="x", padx=5, pady=(0, 5))

    ctrl_row = tk.Frame(opt_controls, bg=WHITE)
    ctrl_row.pack(fill="x", pady=(0, 8))

    # WHY: Firm, stage, risk, DD all come from the rule now.
    #      No dropdowns needed — show as read-only label.
    #      Keep the StringVars so other code that reads them still works.
    # CHANGED: April 2026 — remove firm/stage dropdowns
    from project2_backtesting.strategy_refiner import get_prop_firm_presets
    presets = get_prop_firm_presets()
    firm_options = ["None — maximize pips"] + [name for name in sorted(presets.keys()) if name != "Custom"]
    _opt_target_var = tk.StringVar(value=firm_options[0])
    _stage_var = tk.StringVar(value="Evaluation")

    _rule_info_lbl = tk.Label(ctrl_row, text="Load a strategy to see rule info",
                               font=("Segoe UI", 9), bg=WHITE, fg="#888")
    _rule_info_lbl.pack(side=tk.LEFT, padx=(0, 15))

    stage_info = tk.Label(ctrl_row, text="", font=("Segoe UI", 8), bg=WHITE, fg="#888")
    stage_info.pack(side=tk.LEFT, padx=(0, 10))

    def _on_stage_change(*_):
        stage = _stage_var.get()
        firm = _opt_target_var.get() if _opt_target_var else ""

        # Load trading_rules for this firm
        presets = get_prop_firm_presets()
        preset = presets.get(firm, {})
        firm_data = preset.get('firm_data')
        trading_rules = firm_data.get('trading_rules', []) if firm_data else []

        if stage == "Evaluation":
            stage_info.config(
                text="🎯 Goal: hit profit target fast. No consistency rule. Higher risk OK.",
                fg="#e67e22")
            # Auto-set risk from eval trading_rules
            for rule in trading_rules:
                if rule.get('stage') == 'evaluation' and rule.get('type') == 'eval_settings':
                    params = rule.get('parameters', {})
                    # WHY (Phase A.17): Phase 67 Fix 15 synthesised the
                    #      midpoint of risk_pct_range as the displayed
                    #      default ((0.8+1.5)/2 = 1.15 for the leveraged
                    #      firm) without user consent. The user wants to
                    #      see the conservative lower bound by default
                    #      and explicitly raise it if they want more
                    #      risk — never have a value silently picked.
                    #      Also accept a single 'risk_pct' field for
                    #      firms that don't use a range.
                    # CHANGED: April 2026 — Phase A.17 — lower bound, not midpoint
                    if 'risk_pct' in params:
                        _eval_risk = float(params['risk_pct'])
                    else:
                        risk_range = params.get('risk_pct_range', [0.8, 1.5])
                        _eval_risk = float(risk_range[0])
                    if _risk_var:
                        # WHY: Don't override rule's margin-capped risk with firm's theoretical.
                        # CHANGED: April 2026 — rule risk takes priority
                        _loaded_risk = 0
                        try:
                            _sel_idx2 = _get_selected_index()
                            if _sel_idx2 is not None:
                                for _si2 in _strategies:
                                    if _si2.get('index') == _sel_idx2:
                                        _lsr2 = _si2.get('saved_rule', {})
                                        _loaded_risk = float(_lsr2.get('risk_pct', 0) or 0)
                                        break
                        except Exception:
                            pass
                        if _loaded_risk > 0:
                            _risk_var.set(str(_loaded_risk))
                        else:
                            _risk_var.set(str(_eval_risk))
                    break
            else:
                # No firm-specific eval rules — conservative default
                if _risk_var:
                    _risk_var.set("0.5")
        else:
            stage_info.config(
                text="🛡️ Goal: survive + payouts consistently. Meet DD and consistency rules.",
                fg="#28a745")
            # Auto-set risk from funded trading_rules
            for rule in trading_rules:
                if rule.get('stage') == 'funded' and rule.get('type') == 'funded_accumulate':
                    params = rule.get('parameters', {})
                    # WHY (Phase A.17): old code did
                    #      params.get('risk_pct_range', [0.3, 0.5]) and
                    #      took [0]. leveraged.json uses 'risk_pct': 0.5
                    #      (single value), not a range, so the lookup
                    #      missed and the hardcoded 0.3 fallback fired
                    #      — displaying a value the user never approved
                    #      and that doesn't appear in any config file.
                    #      Read 'risk_pct' first; fall back to
                    #      risk_pct_range[0] only if a range is defined;
                    #      hardcoded fallback only if neither exists.
                    # CHANGED: April 2026 — Phase A.17 — read risk_pct first
                    if 'risk_pct' in params:
                        _funded_risk = float(params['risk_pct'])
                    elif 'risk_pct_range' in params:
                        _funded_risk = float(params['risk_pct_range'][0])
                    else:
                        _funded_risk = 0.5
                    if _risk_var:
                        # WHY: Don't override rule's margin-capped risk with firm's theoretical.
                        # CHANGED: April 2026 — rule risk takes priority
                        _loaded_risk_f = 0
                        try:
                            _sel_idx3 = _get_selected_index()
                            if _sel_idx3 is not None:
                                for _si3 in _strategies:
                                    if _si3.get('index') == _sel_idx3:
                                        _lsr3 = _si3.get('saved_rule', {})
                                        _loaded_risk_f = float(_lsr3.get('risk_pct', 0) or 0)
                                        break
                        except Exception:
                            pass
                        if _loaded_risk_f > 0:
                            _risk_var.set(str(_loaded_risk_f))
                        else:
                            _risk_var.set(str(_funded_risk))
                    break
            else:
                # No firm-specific funded rules — conservative default
                if _risk_var:
                    _risk_var.set("0.5")

    # Traces removed — firm/stage/risk now come from loaded rule, not dropdowns

    # ── Account size + risk row ──
    acct_row = tk.Frame(sf, bg=WHITE)
    acct_row.pack(fill="x", padx=10, pady=(0, 5))

    tk.Label(acct_row, text="Account:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg="#333").pack(side=tk.LEFT)

    _acct_var = tk.StringVar(value="100000")
    _acct_combo = ttk.Combobox(acct_row, textvariable=_acct_var,
                                values=["10000", "25000", "50000", "100000", "200000"],
                                width=10)
    _acct_combo.pack(side=tk.LEFT, padx=5)

    tk.Label(acct_row, text="Risk:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg="#333").pack(side=tk.LEFT, padx=(15, 0))

    _risk_var = tk.StringVar(value="1.0")
    tk.Entry(acct_row, textvariable=_risk_var, width=5, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=5)
    tk.Label(acct_row, text="%/trade", font=("Segoe UI", 9), bg=WHITE, fg="#555").pack(side=tk.LEFT)

    # Account info label
    _acct_info = tk.Label(acct_row, text="", font=("Segoe UI", 8), bg=WHITE, fg="#888")
    _acct_info.pack(side=tk.LEFT, padx=(15, 0))

    # Auto-update account sizes when firm changes
    def _on_firm_change_acct(*_):
        firm = _opt_target_var.get() if _opt_target_var else ""
        presets = get_prop_firm_presets()
        preset = presets.get(firm, {})
        firm_data = preset.get('firm_data')
        if firm_data:
            # WHY: Direct subscripting of firm_data['challenges'][0] without
            #      try/except caused TypeError crashes when firm_data structure
            #      was unexpected (e.g., if firm_data itself was a string).
            # CHANGED: April 2026 — defensive try/except around firm data access
            try:
                sizes = firm_data['challenges'][0].get('account_sizes', [100000])
                _acct_combo['values'] = [str(s) for s in sizes]
                if sizes and _acct_var.get() not in [str(s) for s in sizes]:
                    _acct_var.set(str(sizes[-1]))

                # WHY: DD limits depend on selected stage (eval vs funded).
                #      Leverage from loaded rule first, then firm data.
                # CHANGED: April 2026 — stage-aware DD + rule leverage
                _cur_stage = _stage_var.get().lower() if _stage_var else 'funded'
                _challenge = firm_data['challenges'][0]
                if _cur_stage in ('evaluation', 'eval'):
                    _phase = _challenge.get('phases', [{}])[0]
                    daily = _phase.get('max_daily_drawdown_pct', 5)
                    total = _phase.get('max_total_drawdown_pct', 10)
                    dd_type = _phase.get('drawdown_type', 'static')
                else:
                    _funded = _challenge.get('funded', {})
                    daily = _funded.get('max_daily_drawdown_pct', 5)
                    total = _funded.get('max_total_drawdown_pct', 10)
                    dd_type = _funded.get('drawdown_type', 'static')

                _rule_lev = 0
                try:
                    _sel_idx = _get_selected_index()
                    if _sel_idx is not None:
                        for _si in _strategies:
                            if _si.get('index') == _sel_idx:
                                _lsr = _si.get('saved_rule', {})
                                _rule_lev = int(_lsr.get('leverage', 0) or 0)
                                break
                except Exception:
                    pass
                try:
                    from shared.prop_firm_engine import get_leverage_for_symbol, get_instrument_type
                    _opt_sym = 'XAUUSD'
                    _opt_lev_val = _rule_lev if _rule_lev > 0 else get_leverage_for_symbol(firm_data, _opt_sym)
                    _opt_inst = get_instrument_type(_opt_sym)
                    lev = f"1:{_opt_lev_val} ({_opt_inst})"
                except Exception:
                    lev = f"1:{_rule_lev}" if _rule_lev > 0 else firm_data.get('leverage', '—')
                _acct_info.config(text=f"DD: {daily}%/{total}% {dd_type} | Leverage: {lev}")
            except (KeyError, IndexError, TypeError):
                # Firm data structure unexpected — use defaults
                _acct_combo['values'] = ['100000']
                _acct_var.set('100000')
                _acct_info.config(text="DD: 5%/10% static | Leverage: —")

        # Also update risk based on stage + firm
        _on_stage_change()

    # Traces removed — values come from loaded rule, not dropdowns

    _opt_start_btn = tk.Button(ctrl_row, text="Start Deep Optimization",
                               command=_start_optimization,
                               bg="#667eea", fg="white", font=("Segoe UI", 10, "bold"),
                               relief=tk.FLAT, cursor="hand2", padx=18, pady=7)
    _opt_start_btn.pack(side=tk.LEFT, padx=(0, 8))

    _opt_stop_btn = tk.Button(ctrl_row, text="Stop",
                              command=_stop_optimization,
                              bg=RED, fg="white", font=("Segoe UI", 10, "bold"),
                              relief=tk.FLAT, cursor="hand2", padx=12, pady=7,
                              state="disabled")
    _opt_stop_btn.pack(side=tk.LEFT)

    _opt_status_lbl = tk.Label(opt_controls, text="Ready",
                               font=("Segoe UI", 9, "italic"), bg=WHITE, fg=GREY)
    _opt_status_lbl.pack(anchor="w")

    # Firm rules reminder
    from shared.firm_rules_reminder import show_reminder_on_firm_change

    _reminder = [None]
    show_reminder_on_firm_change(_opt_target_var, sf, _reminder, _stage_var)

    # Live progress box
    prog_box = tk.Frame(sf, bg="#1a1a2a", padx=16, pady=12)
    prog_box.pack(fill="x", padx=5, pady=(0, 5))

    def _live_lbl(key, text, font_size=9, bold=False, color="white"):
        lbl = tk.Label(prog_box, text=text,
                       font=("Segoe UI", font_size, "bold" if bold else "normal"),
                       bg="#1a1a2a", fg=color, anchor="w")
        lbl.pack(anchor="w", pady=1)
        _opt_live_labels[key] = lbl

    _live_lbl("msg",     "Waiting to start...", 9, False, "#aaaacc")
    _live_lbl("progress","",                    8, False, "#8888aa")
    tk.Frame(prog_box, bg="#333355", height=1).pack(fill="x", pady=4)
    tk.Label(prog_box, text="🏆 Current Best Found:",
             font=("Segoe UI", 9, "bold"), bg="#1a1a2a", fg="#ffd700").pack(anchor="w")
    _live_lbl("best_name",  "—", 10, True,  "white")
    _live_lbl("best_stats", "—", 9,  False, "#88ddaa")
    tk.Frame(prog_box, bg="#333355", height=1).pack(fill="x", pady=4)
    _live_lbl("counters", "Tested: 0  |  Improvements: 0  |  Elapsed: 0m 0s",
              8, False, "#aaaacc")

    # Optimizer results
    _opt_results_frame = tk.Frame(sf, bg=BG)
    _opt_results_frame.pack(fill="x", padx=5, pady=(0, 20))

    return panel


def refresh():
    global _strategies, _strategy_var
    _load_strategies()
    if _strategy_var is not None and _strategies:
        labels = [s['label'] for s in _strategies]
        if _strategy_var.get() not in labels:
            _strategy_var.set(labels[0])

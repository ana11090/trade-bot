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
_base_stats_frame = None
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
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    if '───' in val:
        return None  # separator, not a real selection
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _load_selected_strategy():
    """Load the selected strategy's trades for filtering/optimizing.

    WHY: Different strategy sources need different loading:
      - Backtest results (int index) → load trades from matrix directly
      - Optimizer results ('optimizer_latest') → load from optimizer output
      - Saved rules ('saved_X') → MATCH by rule_combo + exit_strategy against
        the matrix, then load those trades. Saved rules don't store trades,
        but they remember which backtest result they came from.

    CHANGED: April 2026 — saved rules match to matrix by name
    """
    global _base_trades, _filtered_trades
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

        matched_idx = None
        if rule_combo:
            # WHY: Search for a backtest result with the same rule_combo + exit name.
            #      Multiple strategies might have the same rule_combo but different exits.
            for s in _strategies:
                if s.get('source') != 'backtest':
                    continue
                if s.get('rule_combo', '') == rule_combo:
                    # Check exit match
                    if exit_strategy and exit_strategy in s.get('label', ''):
                        matched_idx = s.get('index')
                        break
                    if exit_name and exit_name == s.get('exit_name', ''):
                        matched_idx = s.get('index')
                        break

            # If exact exit match failed, try just rule_combo
            if matched_idx is None:
                for s in _strategies:
                    if s.get('source') != 'backtest':
                        continue
                    if s.get('rule_combo', '') == rule_combo:
                        matched_idx = s.get('index')
                        break

        if matched_idx is not None:
            # WHY: Found the matching backtest result — load its trades
            print(f"[REFINER] Saved rule matched to matrix index {matched_idx}")
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

                    def _do_load_saved(_raw=raw, _filters=filters):
                        global _base_trades, _filtered_trades
                        try:
                            _base_trades = enrich_trades(list(_raw))
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
                    messagebox.showwarning("No Trades",
                        f"Matched strategy '{rule_combo}' but it has no trade data.\n\n"
                        "Re-run the backtest to generate trades.")
                    return
            except Exception as e:
                import traceback; traceback.print_exc()
                messagebox.showerror("Load Error", str(e))
                return
        else:
            # WHY: Can't find a matching strategy in the matrix.
            #      Maybe the backtest was re-run with different rules.
            messagebox.showinfo("Saved Rule — No Match",
                f"Could not find a matching strategy in the backtest matrix.\n\n"
                f"Rule: {rule_combo}\n"
                f"Exit: {exit_name or exit_strategy or 'Unknown'}\n\n"
                f"The backtest may have been re-run with different rules.\n"
                f"Select the matching strategy from the backtest results above instead.")
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

    def _do_load():
        global _base_trades, _filtered_trades
        try:
            from project2_backtesting.strategy_refiner import (
                load_trades_from_matrix, enrich_trades
            )
            raw = load_trades_from_matrix(idx, entry_tf=_sel_tf)
            if not raw:
                if state.window:
                    state.window.after(0, lambda: messagebox.showwarning(
                        "No Trades",
                        "This strategy has no trade data.\n\nRe-run the backtest first."
                    ))
                return
            _base_trades = enrich_trades(list(raw))
            _filtered_trades = list(_base_trades)
            if state.window:
                state.window.after(0, _update_strat_info)
                state.window.after(50, _schedule_update)
        except Exception as e:
            import traceback; traceback.print_exc()
            if state.window:
                state.window.after(0, lambda: messagebox.showerror("Load Error", str(e)))

    import threading
    threading.Thread(target=_do_load, daemon=True).start()


def _update_strat_info():
    global _strat_info_lbl
    if not _strat_info_lbl or not _base_trades:
        return
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

            # Account
            _rule_acct = (
                _rs.get('starting_capital', 0) or
                _sr.get('account_size', 0) or
                _rsk.get('account_size', 0) or
                _loaded_row.get('account_size', 0)
            )
            if _rule_acct and float(_rule_acct) > 0 and _acct_var:
                _acct_var.set(str(int(float(_rule_acct))))

            # Risk — rule first (single source of truth)
            _rule_risk = (
                _loaded_row.get('risk_pct', 0) or
                _sr.get('risk_pct', 0) or
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
    except Exception as _e:
        print(f"[REFINER] Could not auto-fill from rule: {_e}")


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
            try:
                from project2_backtesting.panels.configuration import load_config as _opt_lc
                spread_pips = float(_opt_lc().get('spread', 25.0))
            except Exception:
                pass
            commission_pips = 0.0
            idx = _get_selected_index()
            selected_strategy_row = None
            if idx is not None:
                for s in _strategies:
                    if s['index'] == idx:
                        spread_pips = s.get('spread_pips', 25.0)
                        commission_pips = s.get('commission_pips', 0.0)
                        selected_strategy_row = s
                        break

            all_candidates = []

            # Get stage and account size
            stage = _stage_var.get().lower() if _stage_var else "funded"
            account_size = float(_acct_var.get()) if _acct_var else 100000
            risk_pct = float(_risk_var.get()) if _risk_var else 1.0
            print(f"[OPTIMIZER] Stage: {stage}, Account: ${account_size:,.0f}, Risk: {risk_pct}%")
            print(f"[OPTIMIZER] Spread: {spread_pips} pips, Commission: {commission_pips} pips")

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

            print(f"[OPTIMIZER] Leverage: 1:{_opt_leverage}, contract: {_opt_contract}")

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
                total_dd_limit_pct=total_limit
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

    def _save(r=rules_snap, f=filters_snap, n=strategy_name, s=stats_snap, c=cand):
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

            entry_tf = 'H1'
            try:
                from project2_backtesting.panels.configuration import load_config
                _cfg = load_config()
                entry_tf = _cfg.get('winning_scenario', 'H1')
            except Exception:
                pass

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

            data = {
                'rule_combo': n,
                'trades': list(trades_snap),
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
                'risk_settings': {
                    'risk_pct': float(c.get('risk_pct', _risk_var.get() if _risk_var else 1.0)),
                    'account_size': int(float(_acct_var.get())) if _acct_var else 100000,
                    'firm': _opt_target_var.get() if _opt_target_var else '',
                    'stage': _stage_var.get() if _stage_var else 'Funded',
                },
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

            rid = save_rule(data, source=f"Optimizer: {n}", notes=str(f))
            messagebox.showinfo("Saved", f"Saved as #{rid}!")
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
    wr_var = tk.StringVar(value="50")
    tk.Entry(filter_row1, textvariable=wr_var, width=4, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 8))
    tk.Label(filter_row1, text="%", font=("Segoe UI", 8), bg=BG, fg="#555").pack(side=tk.LEFT, padx=(0, 10))

    tk.Label(filter_row1, text="Min Trades:", font=("Segoe UI", 8), bg=BG, fg="#555").pack(side=tk.LEFT)
    trades_var = tk.StringVar(value="10")
    tk.Entry(filter_row1, textvariable=trades_var, width=5, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(2, 10))

    tk.Label(filter_row1, text="Min PF:", font=("Segoe UI", 8), bg=BG, fg="#555").pack(side=tk.LEFT)
    pf_var = tk.StringVar(value="1.0")
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
                            'risk_pct': float(c.get('risk_pct', _risk_var.get() if _risk_var else 1.0)),
                            'account_size': int(float(_acct_var.get())) if _acct_var else 100000,
                            'firm': _opt_target_var.get() if _opt_target_var else '',
                            'stage': _stage_var.get() if _stage_var else 'Funded',
                        },
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

        tk.Button(cards_frame, text=f"💾 Save All {len(filtered)} Strategies",
                  command=_save_all,
                  bg="#28a745", fg="white", font=("Segoe UI", 9, "bold"),
                  relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(pady=(0, 6))

        # Dollar conversion
        try:
            acct = float(_acct_var.get()) if _acct_var else 100000
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
    """Draw monthly P&L bar chart with hover tooltips."""
    from project2_backtesting.strategy_refiner import compute_monthly_pnl

    # Load account config for profit % calculations
    try:
        from project2_backtesting.panels.configuration import load_config
        cfg = load_config()
        _acct_size = float(cfg.get('starting_capital', '100000'))
        _risk_pct = float(cfg.get('risk_pct', '1.0'))
        _pip_value = float(cfg.get('pip_value_per_lot', '1.0'))
    except Exception:
        _acct_size = 100000
        _risk_pct = 1.0
        _pip_value = 1.0

    canvas.delete("all")
    monthly = compute_monthly_pnl(trades, account_size=_acct_size,
                                   risk_pct=_risk_pct, pip_value=_pip_value)

    if not monthly:
        canvas.create_text(200, 100, text="No trade data", font=("Arial", 11), fill="#888")
        return

    w = canvas.winfo_width() or 800
    h = canvas.winfo_height() or 200

    n = len(monthly)
    if n == 0:
        return

    margin_left = 60
    margin_right = 20
    margin_top = 20
    margin_bottom = 40
    chart_w = w - margin_left - margin_right
    chart_h = h - margin_top - margin_bottom

    bar_width = max(2, min(20, chart_w // n - 2))

    pnls = [m['pnl_pips'] for m in monthly]
    max_pnl = max(max(pnls), 1)
    min_pnl = min(min(pnls), -1)
    pnl_range = max_pnl - min_pnl

    # Zero line position
    zero_y = margin_top + int(chart_h * max_pnl / pnl_range)

    # Draw zero line
    canvas.create_line(margin_left, zero_y, w - margin_right, zero_y,
                        fill="#aaa", dash=(3, 3))
    canvas.create_text(margin_left - 5, zero_y, text="0", anchor="e",
                        font=("Arial", 7), fill="#888")

    # Draw bars
    bar_items = []  # (rect_id, month_data)

    for i, m in enumerate(monthly):
        x = margin_left + int(i * chart_w / n) + 1
        pnl = m['pnl_pips']

        if pnl >= 0:
            bar_top = zero_y - int((pnl / pnl_range) * chart_h)
            bar_bottom = zero_y
            color = "#28a745"
        else:
            bar_top = zero_y
            bar_bottom = zero_y + int((abs(pnl) / pnl_range) * chart_h)
            color = "#dc3545"

        rect = canvas.create_rectangle(x, bar_top, x + bar_width, bar_bottom,
                                        fill=color, outline=color, width=0)
        bar_items.append((rect, m))

        # Month label (every 3rd or 6th month to avoid crowding)
        if n <= 36 or i % 3 == 0:
            label = m['month'][2:]  # '2020-01' → '20-01'
            canvas.create_text(x + bar_width // 2, h - 10, text=label,
                                font=("Arial", 6), fill="#888", angle=45)

    # Y-axis labels
    for val in [max_pnl, max_pnl // 2, min_pnl // 2, min_pnl]:
        y = zero_y - int((val / pnl_range) * chart_h)
        canvas.create_text(margin_left - 5, y, text=f"{val:+.0f}",
                            anchor="e", font=("Arial", 7), fill="#888")

    # Hover tooltips
    def _on_motion(event):
        for rect, m in bar_items:
            coords = canvas.coords(rect)
            if coords and coords[0] <= event.x <= coords[2]:
                pnl = m['pnl_pips']
                pnl_pct = m.get('pnl_pct', 0)
                pnl_dollars = m.get('pnl_dollars', 0)

                text = (f"{m['month']}: {pnl:+,.0f} pips  ({pnl_pct:+.1f}%  ${pnl_dollars:+,.0f})\n"
                        f"{m['trades']} trades ({m['wins']}W / {m['losses']}L)\n"
                        f"Avg: {m.get('avg_trades_per_day', 0)}/day  "
                        f"Min: {m.get('min_trades_per_day', 0)}/day  "
                        f"Max: {m.get('max_trades_per_day', 0)}/day")
                tooltip.config(text=text)
                tooltip.place(x=event.x + 10, y=event.y - 50)
                return
        tooltip.place_forget()

    def _on_leave(event):
        tooltip.place_forget()

    canvas.bind("<Motion>", _on_motion)
    canvas.bind("<Leave>", _on_leave)


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
                                  total_dd_safety_pct=_sum_total_lim * 0.95)

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
    global _strategy_var, _strat_info_lbl, _base_stats_frame
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

        # Remove loading message
        try:
            loading_lbl.destroy()
        except:
            pass

        if not _strategies:
            # No strategies found
            tk.Label(sel_row, text="No backtest results. Run the backtest first.",
                     font=("Segoe UI", 10, "italic"), bg=WHITE, fg=RED).pack(side=tk.LEFT)
        else:
            # WHY: Treeview shows rule ID, exit strategy, WR, PF, trades,
            #      pips at a glance — much better than a truncated dropdown.
            #      _strategy_var stays synced so _get_selected_index() and
            #      all downstream code work unchanged.
            # CHANGED: April 2026 — Treeview replaces Combobox
            tree_frame = tk.Frame(sel_row, bg=WHITE)
            tree_frame.pack(fill="x", expand=True)

            columns = ("#", "rule", "exit", "trades", "wr", "pf", "net_pips", "avg_pips")
            _strat_tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                       height=min(len(_strategies), 8),
                                       selectmode="browse")

            _strat_tree.heading("#",        text="#")
            _strat_tree.heading("rule",     text="Rule")
            _strat_tree.heading("exit",     text="Exit Strategy")
            _strat_tree.heading("trades",   text="Trades")
            _strat_tree.heading("wr",       text="Win Rate")
            _strat_tree.heading("pf",       text="PF")
            _strat_tree.heading("net_pips", text="Net Pips")
            _strat_tree.heading("avg_pips", text="Avg Pips")

            _strat_tree.column("#",        width=30,  anchor="center")
            _strat_tree.column("rule",     width=180, anchor="w")
            _strat_tree.column("exit",     width=130, anchor="w")
            _strat_tree.column("trades",   width=60,  anchor="center")
            _strat_tree.column("wr",       width=70,  anchor="center")
            _strat_tree.column("pf",       width=60,  anchor="center")
            _strat_tree.column("net_pips", width=90,  anchor="e")
            _strat_tree.column("avg_pips", width=70,  anchor="e")

            _strat_tree.tag_configure("profitable", foreground="#28a745")
            _strat_tree.tag_configure("losing",     foreground="#dc3545")

            tree_scroll = tk.Scrollbar(tree_frame, orient="vertical",
                                       command=_strat_tree.yview)
            _strat_tree.configure(yscrollcommand=tree_scroll.set)
            tree_scroll.pack(side=tk.RIGHT, fill="y")
            _strat_tree.pack(fill="x", expand=True)

            for row_n, s in enumerate(_strategies, start=1):
                idx = str(s.get('index', 0))
                rc       = s.get('rule_combo', '?')
                exit_name = s.get('exit_name', s.get('exit_strategy', '?'))
                trades   = s.get('total_trades', s.get('trades', 0))
                wr       = s.get('win_rate', 0)
                wr_str   = f"{wr:.1f}%" if wr > 1 else f"{wr*100:.1f}%"
                pf       = s.get('net_profit_factor', s.get('profit_factor', 0))
                net      = s.get('net_total_pips', s.get('total_pips', 0))
                avg      = s.get('net_avg_pips', s.get('avg_pips', 0))
                tag      = "profitable" if net > 0 else "losing"
                _strat_tree.insert("", "end", iid=idx, values=(
                    row_n, rc, exit_name, int(trades), wr_str,
                    f"{pf:.2f}", f"{net:+,.0f}", f"{avg:+.1f}"
                ), tags=(tag,))

            # Select first row and sync _strategy_var
            _tree_children = _strat_tree.get_children()
            if _tree_children:
                _strat_tree.selection_set(_tree_children[0])
                _strategy_var.set(_strategies[0]['label'])

            dd_container[0] = _strat_tree

            def _on_tree_select(event=None):
                sel = _strat_tree.selection()
                if not sel:
                    return
                sel_idx = sel[0]
                for s in _strategies:
                    if str(s.get('index', '')) == sel_idx:
                        _strategy_var.set(s['label'])
                        break

            _strat_tree.bind("<<TreeviewSelect>>", _on_tree_select)

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
    tk.Label(chart_outer, text="📊 Monthly P&L", font=("Segoe UI", 10, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    _monthly_chart_canvas = tk.Canvas(chart_outer, bg="#ffffff", height=200,
                                       highlightthickness=1, highlightbackground="#ddd")
    _monthly_chart_canvas.pack(fill="x", pady=5)

    # Tooltip label (hidden until hover) — must not block scrolling
    _monthly_tooltip = tk.Label(chart_outer, text="", font=("Arial", 9, "bold"),
                                 bg="#333333", fg="white", padx=8, pady=4)

    # Forward scroll from tooltip to main canvas
    def _tooltip_scroll(event):
        _monthly_tooltip.place_forget()
        _scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    _monthly_tooltip.bind("<MouseWheel>", _tooltip_scroll)

    # Draw placeholder
    _monthly_chart_canvas.create_text(200, 100, text="Load a strategy to see monthly P&L chart",
                                       font=("Arial", 11), fill="#888")

    # Debounce chart resize — fires on every pixel change without this
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

    # Firm selector
    tk.Label(ctrl_row, text="Target firm:", font=("Segoe UI", 9), bg=WHITE, fg=DARK).pack(side=tk.LEFT, padx=(0, 8))

    # WHY: Hardcoded firm list required manual updates when adding new firms.
    #      Now dynamically loaded from prop_firms/*.json files.
    # CHANGED: April 2026 — dynamic firm dropdown population
    from project2_backtesting.strategy_refiner import get_prop_firm_presets
    presets = get_prop_firm_presets()
    firm_options = ["None — maximize pips"] + [name for name in sorted(presets.keys()) if name != "Custom"]
    _opt_target_var = tk.StringVar(value=firm_options[0])
    ttk.Combobox(ctrl_row, textvariable=_opt_target_var,
                 values=firm_options, state="readonly", width=25).pack(side=tk.LEFT, padx=(0, 15))

    # Stage selector
    tk.Label(ctrl_row, text="Stage:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg="#333").pack(side=tk.LEFT, padx=(15, 5))

    _stage_var = tk.StringVar(value="Funded")
    stage_combo = ttk.Combobox(ctrl_row, textvariable=_stage_var,
                                values=["Evaluation", "Funded"], width=12, state="readonly")
    stage_combo.pack(side=tk.LEFT, padx=(0, 10))

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
                        _risk_var.set(str(_eval_risk))
                    break
            else:
                # No firm-specific eval rules — conservative default
                # WHY (Phase A.17): was 1.0 unconditionally; align with
                #      the lower-bound policy above so unconfigured
                #      firms also default conservatively.
                # CHANGED: April 2026 — Phase A.17
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
                        _risk_var.set(str(_funded_risk))
                    break
            else:
                # No firm-specific funded rules — conservative default
                if _risk_var:
                    _risk_var.set("0.5")

    _stage_var.trace_add("write", _on_stage_change)
    _on_stage_change()  # Initial update

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

                funded = firm_data['challenges'][0].get('funded', {})
                daily = funded.get('max_daily_drawdown_pct', 5)
                total = funded.get('max_total_drawdown_pct', 10)
                dd_type = funded.get('drawdown_type', 'static')
                try:
                    from shared.prop_firm_engine import get_leverage_for_symbol, get_instrument_type
                    _opt_sym = selected_strategy_row.get('symbol', 'XAUUSD') if selected_strategy_row else 'XAUUSD'
                    _opt_lev_val = get_leverage_for_symbol(firm_data, _opt_sym)
                    _opt_inst = get_instrument_type(_opt_sym)
                    lev = f"1:{_opt_lev_val} ({_opt_inst})"
                except Exception:
                    lev = firm_data.get('leverage', '—')
                _acct_info.config(text=f"DD: {daily}%/{total}% {dd_type} | Leverage: {lev}")
            except (KeyError, IndexError, TypeError):
                # Firm data structure unexpected — use defaults
                _acct_combo['values'] = ['100000']
                _acct_var.set('100000')
                _acct_info.config(text="DD: 5%/10% static | Leverage: —")

        # Also update risk based on stage + firm
        _on_stage_change()

    _opt_target_var.trace_add("write", _on_firm_change_acct)
    _acct_var.trace_add("write", lambda *_: _on_firm_change_acct())

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

"""
Saved Rules Panel — view, manage, export, and activate bookmarked rules.
"""

import tkinter as tk
from tkinter import messagebox
import os
import json
import shutil
import threading as _threading
# WHY (Phase 68 Fix 42): Two concurrent activate clicks race the JSON write.
# CHANGED: April 2026 — Phase 68 Fix 42 — activate write lock
_activate_lock = _threading.Lock()

BG = "#ffffff"
FG = "#333333"

_content_frame = None


def build_panel(parent):
    global _content_frame

    panel = tk.Frame(parent, bg=BG)

    # Scrollable canvas
    canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    scrollbar = tk.Scrollbar(panel, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side=tk.RIGHT, fill="y")
    canvas.pack(side=tk.LEFT, fill="both", expand=True)

    inner = tk.Frame(canvas, bg=BG)
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_configure(event):
        canvas.configure(scrollregion=canvas.bbox("all"))
    inner.bind("<Configure>", _on_configure)

    def _on_canvas_resize(event):
        canvas.itemconfig(window_id, width=event.width)
    canvas.bind("<Configure>", _on_canvas_resize)

    # Safe mousewheel binding — doesn't break other canvases
    def _on_enter(event):
        canvas.bind("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        # Linux
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        canvas.unbind("<MouseWheel>")
        canvas.unbind("<Button-4>")
        canvas.unbind("<Button-5>")

    canvas.bind("<Enter>", _on_enter)
    canvas.bind("<Leave>", _on_leave)

    # Title
    tk.Label(inner, text="💾 Saved Rules", font=("Arial", 16, "bold"),
             bg=BG, fg=FG).pack(pady=(20, 5))
    tk.Label(inner, text="Rules you've bookmarked from anywhere in the app",
             font=("Arial", 10), bg=BG, fg="#666666").pack(pady=(0, 15))

    # Action buttons
    btn_frame = tk.Frame(inner, bg=BG)
    btn_frame.pack(fill="x", padx=20, pady=5)

    tk.Button(btn_frame, text="🔄 Refresh",
              command=lambda: _refresh_list(inner, canvas, window_id),
              bg="#667eea", fg="white", font=("Arial", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(side=tk.LEFT, padx=(0, 5))

    tk.Button(btn_frame, text="📤 Use Selected in Pipeline",
              command=lambda: _activate_selected(inner, canvas, window_id),
              bg="#28a745", fg="white", font=("Arial", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(side=tk.LEFT, padx=(0, 5))

    tk.Button(btn_frame, text="🗑️ Delete All",
              command=lambda: _delete_all(inner, canvas, window_id),
              bg="#dc3545", fg="white", font=("Arial", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(side=tk.LEFT, padx=(0, 5))

    def _cleanup_stale():
        """Remove saved rules that have no exit strategy and no trade data."""
        from shared.saved_rules import load_all, delete_rule
        from tkinter import messagebox
        all_rules = load_all() or []
        _stale_ids = []
        for entry in all_rules:
            rule = entry.get('rule', {})
            has_exit = bool(rule.get('exit_name') or rule.get('exit_class'))
            has_trades = (rule.get('total_trades', 0) > 0 or
                         rule.get('trade_count', 0) > 0)
            has_pf = (rule.get('net_profit_factor', 0) > 0)
            # Keep if it has exit strategy OR trade data OR PF
            if not has_exit and not has_trades and not has_pf:
                _stale_ids.append(entry.get('id'))

        if not _stale_ids:
            messagebox.showinfo("Clean", "No stale rules found — all rules have complete data.")
            return

        if messagebox.askyesno("Clean Up",
                              f"Found {len(_stale_ids)} stale rules (no exit strategy, no trades).\n\n"
                              f"Delete them?\n\n"
                              f"(Rules with complete backtest data will be kept.)"):
            for rid in _stale_ids:
                try:
                    delete_rule(rid)
                except Exception:
                    pass
            print(f"[SAVED RULES] Cleaned up {len(_stale_ids)} stale rules")
            _refresh_list(inner, canvas, window_id)

    # WHY: Stale rules are bare discovery outputs with no exit strategy,
    #      no trades, no PF. They clutter the list and can't be used for
    #      validation or EA generation. This button removes them.
    # CHANGED: April 2026 — cleanup button
    tk.Button(btn_frame, text="🧹 Clean Up Stale",
              command=_cleanup_stale,
              bg="#e67e22", fg="white", font=("Arial", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(side=tk.LEFT)

    # Content frame for rule cards
    _content_frame = tk.Frame(inner, bg=BG)
    _content_frame.pack(fill="both", expand=True, padx=20, pady=10)

    _refresh_list(inner, canvas, window_id)

    return panel


def _refresh_list(inner, canvas, window_id):
    global _content_frame

    # WHY (Phase 68 Fix 44): Destroying all children while a tooltip or
    #      hover callback is active causes stale-callback errors on the
    #      destroyed widget. Check widget validity before destroying.
    # CHANGED: April 2026 — Phase 68 Fix 44 — guard destroy on hover
    #          (audit Part E HIGH #44)
    for widget in list(_content_frame.winfo_children()):
        try:
            widget.destroy()
        except Exception:
            pass

    from shared.saved_rules import load_all
    rules = load_all()

    if not rules:
        tk.Label(_content_frame, text="No saved rules yet.\n\nLook for the 💾 Save button next to any rule in the app.",
                 font=("Arial", 11), bg=BG, fg="#888888").pack(pady=20)
        return

    tk.Label(_content_frame, text=f"{len(rules)} saved rules",
             font=("Arial", 10, "bold"), bg=BG, fg=FG).pack(anchor="w", pady=(0, 10))

    for entry in rules:
        rule = entry.get("rule", {})
        card = tk.Frame(_content_frame, bg="#f8f9fa", bd=1, relief=tk.SOLID, padx=10, pady=8)
        card.pack(fill="x", pady=3)

        # Header row: ID, source, date, delete button
        header = tk.Frame(card, bg="#f8f9fa")
        header.pack(fill="x")

        tk.Label(header, text=f"#{entry.get('id', '?')}",
                 font=("Arial", 10, "bold"), bg="#f8f9fa", fg="#667eea").pack(side=tk.LEFT)
        # WHY: show date + time (HH:MM:SS) so users can distinguish
        #      multiple discovery runs on the same day. ISO format
        #      "YYYY-MM-DDTHH:MM:SS.ffffff" — slice to 19 chars to drop
        #      microseconds, swap 'T' for a space for readability.
        _saved_at_raw = entry.get('saved_at', '?')
        _saved_at_disp = _saved_at_raw[:19].replace('T', ' ') if _saved_at_raw else '?'
        tk.Label(header, text=f"  from {entry.get('source', '?')}  •  {_saved_at_disp}",
                 font=("Arial", 9), bg="#f8f9fa", fg="#888888").pack(side=tk.LEFT)

        # TF badge — show entry_tf, infer from conditions if missing
        # WHY: Old auto-saved rules have no entry_tf. But conditions contain
        #      TF prefixes (M5_, H4_, etc.). Infer entry TF from conditions
        #      so ALL rules show a TF badge.
        # CHANGED: April 2026 — infer entry_tf from conditions
        rule_tf = rule.get('entry_tf', rule.get('entry_timeframe', ''))
        if not rule_tf:
            _tf_order = ['M1', 'M5', 'M15', 'H1', 'H4', 'D1']
            _found_tfs = set()
            for _c in rule.get('conditions', []):
                _feat = _c.get('feature', '') if isinstance(_c, dict) else str(_c)
                for _tf in _tf_order:
                    if _feat.startswith(_tf + '_'):
                        _found_tfs.add(_tf)
                        break
            if _found_tfs:
                for _tf in _tf_order:
                    if _tf in _found_tfs:
                        rule_tf = _tf
                        break
        if rule_tf:
            _tf_color = '#667eea' if (rule.get('entry_tf') or rule.get('entry_timeframe')) else '#999999'
            tk.Label(header, text=f"[{rule_tf}]", bg=_tf_color, fg="white",
                     font=("Arial", 8, "bold"), padx=4, pady=1).pack(side=tk.LEFT, padx=(6, 0))

        # Discovery context badges
        # WHY: User needs to see at a glance what mode produced this rule.
        # CHANGED: April 2026 — discovery context badges
        _src = entry.get('source', '')
        _ds_badge = rule.get('discovery_settings', {})
        _ctx_badges = []
        _rf = rule.get('regime_filter')
        if _rf and isinstance(_rf, list) and len(_rf) > 0:
            _ctx_badges.append(('REGIME', '#9b59b6'))
        if _ds_badge.get('single_rule_mode_enabled') or 'ModeA' in _src:
            _variant = _ds_badge.get('single_rule_mode_variant', 'A').upper()
            _ctx_badges.append((f'SINGLE {_variant}', '#e67e22'))
        if 'Step3' in _src:
            _ctx_badges.append(('STEP 3', '#3498db'))
        elif 'Step4' in _src:
            _ctx_badges.append(('STEP 4', '#16a085'))
        elif 'Backtest' in _src:
            _ctx_badges.append(('BACKTEST', '#27ae60'))
        elif 'Optimizer' in _src:
            _ctx_badges.append(('OPTIMIZER', '#2c3e50'))
        _dir = rule.get('direction', rule.get('action', ''))
        if _dir:
            _ctx_badges.append((_dir, '#28a745' if _dir == 'BUY' else '#dc3545'))
        for _bt, _bc in _ctx_badges:
            tk.Label(header, text=_bt, bg=_bc, fg="white",
                     font=("Arial", 7, "bold"), padx=3, pady=0
                     ).pack(side=tk.LEFT, padx=(3, 0))

        # Leverage badge — shows margin constraint active during backtest
        # WHY: User needs to see at a glance what leverage constraint was
        #      used when this rule was found, so they know lot sizes are
        #      comparable to their live account settings.
        # CHANGED: April 2026 — leverage badge in saved rules
        _lev = rule.get('leverage', 0)
        _firm = rule.get('firm_name', rule.get('firm_id', ''))
        if _lev > 0:
            _lev_text = f"1:{_lev}"
            if _firm:
                _lev_text += f" ({_firm})"
            tk.Label(header, text=_lev_text, font=("Arial", 7, "bold"),
                     bg="#e0e0e0", fg="#333333", padx=4, pady=1
                     ).pack(side=tk.LEFT, padx=(3, 0))

        rid = entry.get('id')
        tk.Button(header, text="🗑️", font=("Arial", 8),
                  bg="#dc3545", fg="white", relief=tk.FLAT, padx=4,
                  command=lambda r=rid: _delete_one(r, inner, canvas, window_id)).pack(side=tk.RIGHT)

        # WHY (Phase A.40b): One-click "▶ Backtest" button per saved
        #      rule. Sets state.pending_backtest_rule_id to this entry's
        #      id, navigates to the Run Backtest panel (lazy-builds if
        #      first access), then schedules a 200ms callback to the
        #      run_backtest_panel's apply_pending_rule_selection helper
        #      which picks up the pending id, switches the source to
        #      "Saved/Bookmarked Rules", and checks ONLY this rule.
        #      User then clicks "Run Backtest" to start.
        #
        #      200ms delay is enough for lazy-build + first paint on
        #      typical hardware. If a panel-not-ready warning fires in
        #      practice, increase to 500ms. We don't block/poll because
        #      that hangs the UI thread.
        # CHANGED: April 2026 — Phase A.40b
        def _a40b_backtest_this_rule(r=rid):
            try:
                import state as _a40b_state
                import sidebar as _a40b_sidebar
                from project2_backtesting.panels import run_backtest_panel as _a40b_rbp

                _a40b_state.pending_backtest_rule_id[0] = r
                _a40b_state.pending_backtest_auto_run[0] = False

                # Navigate to the backtest panel (lazy-builds on first show).
                _a40b_sidebar.show_panel("p2_run")

                # Schedule apply after the panel has had a chance to paint.
                # Using the tk `after` mechanism so we don't block. The
                # card widget is still alive here so card.after is safe.
                card.after(200, _a40b_rbp.apply_pending_rule_selection)
            except Exception as e:
                try:
                    from tkinter import messagebox as _a40b_mb
                    _a40b_mb.showerror(
                        "Backtest error",
                        f"Could not start backtest: {e}\n\n"
                        f"You can still switch manually: Project 2 → Run "
                        f"Backtest → source = 'Saved/Bookmarked Rules'."
                    )
                except Exception:
                    pass

        tk.Button(header, text="▶ Backtest", font=("Arial", 8, "bold"),
                  bg="#28a745", fg="white", relief=tk.FLAT, padx=8,
                  command=_a40b_backtest_this_rule).pack(side=tk.RIGHT, padx=(0, 4))

        # Conditions
        # WHY (Phase A.40a hotfix): Mode A discovery doesn't compute a
        #      win-rate (it's a coverage/tightness optimisation, not a
        #      WR-maximiser), so its auto-saved entries arrive with
        #      win_rate=None. The old `wr <= 1.0` comparison raises
        #      TypeError on None and crashes _refresh_list, blocking
        #      the whole Saved Rules panel from rendering. Coerce None
        #      to 0 here so the entry still displays (just with WR=0%).
        # CHANGED: April 2026 — Phase A.40a hotfix
        wr = rule.get('win_rate') or 0
        pips = rule.get('avg_pips') or 0
        cov = rule.get('coverage') or 0

        # WHY (Phase 68 Fix 41): `:.0%` multiplies by 100. A rule with
        #      win_rate=65 (already percent) displayed as '6500%'. Guard
        #      for the fraction range first.
        # CHANGED: April 2026 — Phase 68 Fix 41 — fraction-safe WR format
        #          (audit Part E HIGH #41)
        _wr_display = wr * 100 if wr <= 1.0 else wr
        stats = f"WR: {_wr_display:.0f}%  |  Avg pips: {pips:+.0f}  |  Coverage: {cov}"
        tk.Label(card, text=stats, font=("Arial", 9, "bold"), bg="#f8f9fa",
                 fg="#28a745" if wr > 0.6 else "#e67e22").pack(anchor="w")

        for cond in rule.get('conditions', []):
            # WHY (Phase 68 Fix 43): Direct dict access crashes if conditions
            #      are in string format. normalize_condition was added for this
            #      exact case but saved_rules_panel never used it.
            # CHANGED: April 2026 — Phase 68 Fix 43 — normalize before access
            #          (audit Part E HIGH #43)
            try:
                from helpers import normalize_condition as _nc
                _cond = _nc(cond) if not isinstance(cond, dict) else cond
                if isinstance(_cond, dict) and _cond:
                    txt = f"  {_cond.get('feature','?')} {_cond.get('operator','>')} {_cond.get('value',0)}"
                elif isinstance(_cond, list):
                    txt = '  ' + ' AND '.join(
                        f"{c.get('feature','?')} {c.get('operator','>')} {c.get('value',0)}"
                        for c in _cond if isinstance(c, dict)
                    )
                else:
                    txt = f"  {str(cond)}"
            except Exception:
                txt = f"  {str(cond)}"
            tk.Label(card, text=txt, font=("Courier", 9), bg="#f8f9fa", fg=FG).pack(anchor="w")

        # Regime filter conditions
        _rf = rule.get('regime_filter')
        if _rf and isinstance(_rf, list) and len(_rf) > 0:
            tk.Label(card, text="  🔀 Regime filter:",
                     font=("Arial", 8, "bold"), bg="#f8f9fa", fg="#9b59b6"
                     ).pack(anchor="w", pady=(4, 0))
            for _rc in _rf:
                if isinstance(_rc, dict):
                    _feat = _rc.get('feature', '?')
                    _op = _rc.get('direction', _rc.get('operator', '>'))
                    _val = _rc.get('threshold', _rc.get('value', '?'))
                    try: _val = f"{float(_val):.4f}"
                    except Exception: _val = str(_val)
                    tk.Label(card, text=f"    {_feat} {_op} {_val}",
                             font=("Courier", 8), bg="#f8f9fa", fg="#7b2d8e"
                             ).pack(anchor="w")

        # Scenario name
        _scenario = rule.get('scenario', '')
        if _scenario:
            tk.Label(card, text=f"  📁 Scenario: {_scenario}",
                     font=("Arial", 8), bg="#f8f9fa", fg="#888"
                     ).pack(anchor="w", pady=(2, 0))

        # Exit strategy (if saved from backtest/optimizer)
        _exit = rule.get('exit_name', rule.get('exit_class', ''))
        if _exit:
            _ep = rule.get('exit_params', rule.get('exit_strategy_params', {}))
            _exit_text = f"  ⚙️ Exit: {_exit}"
            if _ep:
                _ep_parts = [f"{k}={v}" for k, v in _ep.items() if k != 'pip_size']
                if _ep_parts:
                    _exit_text += f"  ({', '.join(_ep_parts[:4])})"
            tk.Label(card, text=_exit_text,
                     font=("Arial", 8), bg="#f8f9fa", fg="#555"
                     ).pack(anchor="w", pady=(1, 0))

        # Run settings summary (if saved)
        _rs = rule.get('run_settings', {})
        if _rs:
            _rs_parts = []
            if _rs.get('regime_filter_enabled'): _rs_parts.append("Regime ON")
            if _rs.get('multi_tf'): _rs_parts.append("Multi-TF")
            if _rs.get('combine_all_rules'): _rs_parts.append("All combos")
            if _rs.get('use_config'): _rs_parts.append("Config")
            if _rs_parts:
                tk.Label(card, text=f"  🔧 Settings: {', '.join(_rs_parts)}",
                         font=("Arial", 8), bg="#f8f9fa", fg="#888"
                         ).pack(anchor="w", pady=(1, 0))

        # Discovery settings — checkbox/radio state at discovery time
        _ds = rule.get('discovery_settings', {})
        if _ds:
            # Regime settings
            _ds_parts = []
            if _ds.get('regime_filter_enabled'):
                _ds_parts.append("Regime: ON")
                if _ds.get('regime_at_discovery'):
                    _ds_parts.append("At discovery: ✅")
                else:
                    _ds_parts.append("At discovery: ❌")
                if _ds.get('regime_strictness'):
                    _ds_parts.append(f"Strictness: {_ds['regime_strictness'].title()}")
            else:
                _ds_parts.append("Regime: OFF")
            if _ds_parts:
                tk.Label(card, text=f"  🎛️ Regime: {' | '.join(_ds_parts)}",
                         font=("Arial", 8), bg="#f8f9fa", fg="#666"
                         ).pack(anchor="w", pady=(1, 0))

            # Single rule mode settings
            _srm_parts = []
            if _ds.get('single_rule_mode_enabled'):
                _variant = _ds.get('single_rule_mode_variant', 'a').upper()
                _variant_names = {'A': 'Mode A (single feature)',
                                  'B': 'Mode B (crossover)',
                                  'C': 'Mode C (two-feature)',
                                  'D': 'Mode D (regime-gated)'}
                _srm_parts.append(f"Single Rule: ON — {_variant_names.get(_variant, f'Mode {_variant}')}")
                if _variant == 'A':
                    _dedup = "✅" if _ds.get('srm_dedup_correlated') else "❌"
                    _winner = _ds.get('srm_winner_selection', 'tightness').title()
                    _srm_parts.append(f"Dedup: {_dedup}")
                    _srm_parts.append(f"Winner: {_winner}")
            else:
                _srm_parts.append("Single Rule: OFF")
            if _srm_parts:
                tk.Label(card, text=f"  🧪 {' | '.join(_srm_parts)}",
                         font=("Arial", 8), bg="#f8f9fa", fg="#666"
                         ).pack(anchor="w", pady=(1, 0))

        if entry.get('notes'):
            tk.Label(card, text=f"📝 {entry['notes']}", font=("Arial", 8, "italic"),
                     bg="#f8f9fa", fg="#888888").pack(anchor="w", pady=(2, 0))


def _delete_one(rule_id, inner, canvas, window_id):
    from shared.saved_rules import delete_rule
    delete_rule(rule_id)
    _refresh_list(inner, canvas, window_id)


def _delete_all(inner, canvas, window_id):
    # WHY (Phase 69 Fix 45): Old dialog said "Delete all saved rules?" without
    #      telling the user how many. A user with 50 carefully bookmarked rules
    #      might click OK thinking there were only a few. Show the count.
    # CHANGED: April 2026 — Phase 69 Fix 45 — show count in delete dialog
    #          (audit Part E LOW #45)
    try:
        from shared.saved_rules import load_all
        _count = len(load_all())
    except Exception:
        _count = 0
    _noun = "rule" if _count == 1 else "rules"
    if messagebox.askyesno(
        "Delete All",
        f"Delete all {_count} saved {_noun}?\n\nThis cannot be undone."
    ):
        from shared.saved_rules import delete_all
        delete_all()
        _refresh_list(inner, canvas, window_id)


def _activate_selected(inner, canvas, window_id):
    """Copy all saved rules into analysis_report.json for the pipeline."""
    from shared.saved_rules import load_all, export_to_report

    rules = export_to_report()
    if not rules:
        messagebox.showwarning("No Rules", "No saved rules to activate.")
        return

    report_path = os.path.join(os.path.dirname(__file__), '..', '..',
                                'project1_reverse_engineering', 'outputs', 'analysis_report.json')
    report_path = os.path.abspath(report_path)
    # WHY (Phase 68 Fix 40): Old code only created a backup if none existed.
    #      User who activated set A, then set B, lost set A because the backup
    #      was set A's predecessor (the original DT rules), not set A itself.
    #      Use a timestamped backup so every activation is recoverable.
    # CHANGED: April 2026 — Phase 68 Fix 40 — timestamped backup on every activate
    #          (audit Part E HIGH #40)
    from datetime import datetime as _dt
    _ts = _dt.now().strftime('%Y%m%d_%H%M%S')
    backup_path = report_path.replace('.json', f'_backup_{_ts}.json')
    if os.path.exists(report_path):
        shutil.copy2(report_path, backup_path)

    # Phase 68 Fix 42: acquire lock so concurrent activations serialize
    with _activate_lock:
      if os.path.exists(report_path):
          with open(report_path, encoding='utf-8') as f:
              current = json.load(f)
      else:
          current = {}

      current['rules'] = rules
      current['discovery_method'] = 'saved_rules'

      # FIX 3: carry entry_tf from saved rules into the top-level report field.
      # WHY: Downstream tools (Refiner, Validator, EA Generator) read entry_timeframe
      #      from analysis_report.json. If all saved rules share the same TF, set it.
      #      If mixed, set 'multi' so downstream tools know to check per-row entry_tf.
      # CHANGED: April 2026 — multi-TF support
      # WHY (Phase 69 Fix 46): Old code only handled 1 or 2+ TFs. When all saved
      #      rules lack entry_tf (e.g. from an older analysis_report.json), rule_tfs
      #      is empty and current['entry_timeframe'] kept its previous stale value.
      #      Downstream tools then ran the backtest on the wrong timeframe.
      # CHANGED: April 2026 — Phase 69 Fix 46 — handle zero-TF case explicitly
      #          (audit Part E LOW #46)
      rule_tfs = sorted(set(r.get('entry_tf', '') for r in rules if r.get('entry_tf', '')))
      if len(rule_tfs) == 1:
          current['entry_timeframe'] = rule_tfs[0]
      elif len(rule_tfs) > 1:
          current['entry_timeframe'] = 'multi'
          current['tested_timeframes'] = rule_tfs
      else:
          # No entry_tf on any rule — remove stale TF so downstream uses its own default
          current.pop('entry_timeframe', None)
          current.pop('tested_timeframes', None)

      with open(report_path, 'w', encoding='utf-8') as f:
          json.dump(current, f, indent=2, default=str)

    messagebox.showinfo("Activated",
        f"{len(rules)} saved rules activated in pipeline.\n"
        f"Original rules backed up.\n\n"
        f"Go to Run Backtest to test them.")

    _refresh_list(inner, canvas, window_id)


def refresh():
    pass  # Panel refreshes on build

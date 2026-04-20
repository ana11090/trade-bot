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
    all_entries = load_all()

    if not all_entries:
        tk.Label(_content_frame, text="No saved rules yet.\n\nLook for the 💾 Save button next to any rule in the app.",
                 font=("Arial", 11), bg=BG, fg="#888888").pack(pady=20)
        return

    tk.Label(_content_frame, text=f"{len(all_entries)} saved rules",
             font=("Arial", 10, "bold"), bg=BG, fg=FG).pack(anchor="w", pady=(0, 10))

    # WHY: Group rules by firm so user can see all rules for a prop firm together
    # CHANGED: April 2026 — firm grouping
    groups = {}
    for entry in all_entries:
        rule = entry.get('rule', {})
        firm = rule.get('prop_firm_name', '') or rule.get('firm_name', '') or 'No Firm'
        if firm not in groups:
            groups[firm] = []
        groups[firm].append(entry)

    # Sort: firms with rules first, "No Firm" last
    sorted_firms = sorted(groups.keys(), key=lambda f: (f == 'No Firm', -len(groups[f])))

    for firm_name in sorted_firms:
        entries = groups[firm_name]

        # Sort within group: deployed/validated first, then backtested, then discovered
        status_order = {'deployed': 0, 'validated': 1, 'backtested': 2, 'discovered': 3}
        entries.sort(key=lambda e: status_order.get(
            e.get('rule', {}).get('status', 'discovered'), 3))

        # Firm header (collapsible)
        _render_firm_header(_content_frame, firm_name, entries)

        # Rule cards
        for entry in entries:
            _render_clean_card(_content_frame, entry, inner, canvas, window_id)


def _render_firm_header(parent, firm_name, entries):
    """Render a firm group header with summary."""
    frame = tk.Frame(parent, bg='#dfe6e9', padx=10, pady=6)
    frame.pack(fill='x', padx=5, pady=(10, 2))

    # Count by status
    statuses = [e.get('rule', {}).get('status', 'discovered') for e in entries]
    summary_parts = []
    for s in ['deployed', 'validated', 'backtested', 'discovered']:
        count = statuses.count(s)
        if count > 0:
            summary_parts.append(f"{count} {s}")

    # Get leverage/account from first rule that has it
    sample = entries[0].get('rule', {})
    lev = sample.get('leverage', 0)
    acct = sample.get('account_size', 0)
    stage = sample.get('prop_firm_stage', '')

    header_text = firm_name
    if lev:
        header_text += f"  (1:{lev}"
        if acct:
            try:
                header_text += f", ${float(acct):,.0f}"
            except Exception:
                header_text += f", ${acct}"
        if stage: header_text += f", {stage}"
        header_text += ")"

    tk.Label(frame, text=header_text,
             font=("Segoe UI", 11, "bold"), bg='#dfe6e9', fg='#2d3436'
             ).pack(side=tk.LEFT)

    tk.Label(frame, text=f"{len(entries)} rules  •  {', '.join(summary_parts) if summary_parts else 'all discovered'}",
             font=("Segoe UI", 9), bg='#dfe6e9', fg='#636e72'
             ).pack(side=tk.RIGHT)


def _render_clean_card(parent, entry, inner, canvas, window_id):
    """Render a clean, modern rule card with essential info only."""
    rule = entry.get("rule", {})

    card = tk.Frame(parent, bg="#ffffff", bd=1, relief=tk.RIDGE, padx=12, pady=10)
    card.pack(fill="x", pady=4, padx=5)

    # ── LINE 1: Identity ──
    # Format: {rule_id}  {direction}  {timeframe}  {exit_name}({params})  ⬤ {status}
    header = tk.Frame(card, bg="#ffffff")
    header.pack(fill="x", pady=(0, 6))

    # Rule ID (descriptive or fallback to numeric)
    _display_id = entry.get('rule_id', f"#{entry.get('id')}")
    tk.Label(header, text=_display_id,
             font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#667eea"
             ).pack(side=tk.LEFT, padx=(0, 8))

    # Direction
    _dir = rule.get('direction', rule.get('action', ''))
    if _dir:
        _dir_color = '#28a745' if _dir == 'BUY' else '#dc3545'
        tk.Label(header, text=_dir, bg=_dir_color, fg="white",
                 font=("Segoe UI", 8, "bold"), padx=5, pady=2
                 ).pack(side=tk.LEFT, padx=(0, 4))

    # Timeframe
    rule_tf = rule.get('entry_timeframe', rule.get('entry_tf', ''))
    if not rule_tf:
        # Infer from conditions
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
        tk.Label(header, text=rule_tf, bg="#667eea", fg="white",
                 font=("Segoe UI", 8, "bold"), padx=5, pady=2
                 ).pack(side=tk.LEFT, padx=(0, 4))

    # Exit strategy (compact)
    _exit = rule.get('exit_name', rule.get('exit_class', ''))
    if _exit:
        _ep = rule.get('exit_params', rule.get('exit_strategy_params', {}))
        _exit_short = _exit.replace('Based', '').replace('-', '')
        _exit_text = _exit_short
        if _ep:
            _sl = _ep.get('sl_pips', '')
            _tp = _ep.get('tp_pips', '')
            _mc = _ep.get('max_candles', '')
            _params = []
            if _sl: _params.append(f"SL={_sl}")
            if _tp: _params.append(f"TP={_tp}")
            if _mc: _params.append(f"{_mc}c")
            if _params:
                _exit_text += f"({', '.join(_params[:2])})"
        tk.Label(header, text=_exit_text,
                 font=("Segoe UI", 8), bg="#ffffff", fg="#555"
                 ).pack(side=tk.LEFT, padx=(4, 8))

    # Status indicator (colored dot + text)
    status = rule.get('status', 'discovered')
    grade = rule.get('grade', '')
    score = rule.get('score', 0)

    _status_colors = {
        'discovered': ('#3498db', '🔵'),
        'backtested': ('#f39c12', '🟡'),
        'validated': ('#27ae60', '🟢'),
        'deployed': ('#9b59b6', '⚡')
    }
    _color, _dot = _status_colors.get(status, ('#95a5a6', '⚪'))

    _status_text = status.title()
    if status == 'validated' and grade:
        _status_text = f"Grade {grade} ({score})"

    tk.Label(header, text=f"{_dot} {_status_text}",
             font=("Segoe UI", 9, "bold"), bg="#ffffff", fg=_color
             ).pack(side=tk.LEFT, padx=(8, 0))

    # Delete button (far right)
    rid = entry.get('id')
    tk.Button(header, text="🗑️", font=("Arial", 8),
              bg="#dc3545", fg="white", relief=tk.FLAT, padx=4,
              command=lambda r=rid: _delete_one(r, inner, canvas, window_id)
              ).pack(side=tk.RIGHT)

    # ── LINE 2: Stats ──
    # Format: WR: {wr}%  |  PF: {pf}  |  Avg: {pips} pips  |  {n} trades
    stats_frame = tk.Frame(card, bg="#ffffff")
    stats_frame.pack(fill="x", pady=(0, 6))

    wr = rule.get('win_rate') or 0
    pf = rule.get('net_profit_factor', rule.get('profit_factor', 0)) or 0
    pips = rule.get('avg_pips') or 0
    trades = rule.get('total_trades', rule.get('coverage', 0)) or 0

    # WR display (handle both fraction and percentage)
    _wr_display = wr * 100 if wr <= 1.0 else wr

    # Color based on performance
    if _wr_display > 55 and pf > 1.5:
        _stats_color = '#28a745'  # green
    elif _wr_display > 50 or pf > 1.0:
        _stats_color = '#f39c12'  # orange
    else:
        _stats_color = '#dc3545'  # red

    stats_text = f"WR: {_wr_display:.0f}%  |  PF: {pf:.2f}  |  Avg: {pips:+.0f} pips  |  {int(trades)} trades"
    tk.Label(stats_frame, text=stats_text,
             font=("Segoe UI", 9, "bold"), bg="#ffffff", fg=_stats_color
             ).pack(side=tk.LEFT)

    # ── LINES 3+: Conditions (compact, one per line) ──
    for cond in rule.get('conditions', []):
        try:
            from helpers import normalize_condition as _nc
            _cond = _nc(cond) if not isinstance(cond, dict) else cond
            if isinstance(_cond, dict) and _cond:
                _feat = _cond.get('feature', '?')
                _op = _cond.get('operator', '>')
                _val = _cond.get('value', 0)
                # Compact operator display
                _op_display = {'<=': '≤', '>=': '≥', '==': '=', '!=': '≠'}.get(_op, _op)
                # Truncate value to 2 decimals
                try:
                    _val_display = f"{float(_val):.2f}"
                except Exception:
                    _val_display = str(_val)
                txt = f"{_feat} {_op_display} {_val_display}"
            else:
                txt = str(cond)
        except Exception:
            txt = str(cond)

        tk.Label(card, text=txt,
                 font=("Courier New", 9), bg="#ffffff", fg="#2d3436"
                 ).pack(anchor="w", padx=(0, 0))

    # ── BOTTOM: Action Buttons ──
    # Show only relevant next actions based on status
    actions_frame = tk.Frame(card, bg="#ffffff")
    actions_frame.pack(fill="x", pady=(6, 0))

    if status == 'discovered':
        # Show backtest button
        def _backtest_this_rule(r=rid):
            try:
                import state
                import sidebar
                from project2_backtesting.panels import run_backtest_panel
                state.pending_backtest_rule_id[0] = r
                state.pending_backtest_auto_run[0] = False
                sidebar.show_panel("p2_run")
                card.after(200, run_backtest_panel.apply_pending_rule_selection)
            except Exception as e:
                messagebox.showerror("Error", f"Could not start backtest: {e}")

        tk.Button(actions_frame, text="▶ Backtest", font=("Segoe UI", 9, "bold"),
                  bg="#28a745", fg="white", relief=tk.FLAT, padx=10, pady=4,
                  command=_backtest_this_rule
                  ).pack(side=tk.LEFT, padx=(0, 4))

    elif status == 'backtested':
        # Show validate button
        tk.Button(actions_frame, text="✓ Validate", font=("Segoe UI", 9, "bold"),
                  bg="#27ae60", fg="white", relief=tk.FLAT, padx=10, pady=4,
                  command=lambda: messagebox.showinfo("Validate", "Open Strategy Validator panel to validate this rule")
                  ).pack(side=tk.LEFT, padx=(0, 4))

    elif status == 'validated':
        # Show generate EA button
        tk.Button(actions_frame, text="⚡ Generate EA", font=("Segoe UI", 9, "bold"),
                  bg="#9b59b6", fg="white", relief=tk.FLAT, padx=10, pady=4,
                  command=lambda: messagebox.showinfo("Generate EA", "Open EA Generator panel to generate code for this rule")
                  ).pack(side=tk.LEFT, padx=(0, 4))

    # Notes (if any)
    if entry.get('notes'):
        tk.Label(card, text=f"📝 {entry['notes']}",
                 font=("Segoe UI", 8, "italic"), bg="#ffffff", fg="#7f8c8d"
                 ).pack(anchor="w", pady=(4, 0))

    # ── FOOTER: Source + Date ──
    # WHY: User needs to see when and where the rule came from.
    #      This was shown before the redesign — must not be removed.
    # CHANGED: April 2026 — restore date/source display
    _source = entry.get('source', '')
    _saved_at = entry.get('saved_at', '')
    # DEBUG: Print what we got
    print(f"[DEBUG] Entry source={repr(_source)}, saved_at={repr(_saved_at)}, entry keys={list(entry.keys())}")
    _date_str = ''
    if _saved_at:
        try:
            from datetime import datetime as _dt_parse
            _parsed = _dt_parse.fromisoformat(_saved_at.replace('Z', '+00:00'))
            _date_str = _parsed.strftime('%Y-%m-%d %H:%M')
        except Exception as e:
            _date_str = str(_saved_at)[:16]

    _footer_parts = []
    if _source:
        _footer_parts.append(f"from {_source}")
    if _date_str:
        _footer_parts.append(f"• {_date_str}")

    # Always show footer if there's any info
    if _footer_parts:
        _footer = tk.Frame(card, bg="#ffffff")
        _footer.pack(fill="x", pady=(4, 0))
        tk.Label(_footer, text='  '.join(_footer_parts),
                 font=("Segoe UI", 8), bg="#ffffff", fg="#95a5a6"
                 ).pack(side=tk.LEFT)


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

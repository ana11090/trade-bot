"""
My Rules Panel — view and manage manually-saved rules.

Unlike Saved Rules (auto-populated by discovery), this collection only contains
rules explicitly saved by the user via the "★ My Rules" button.
"""

import tkinter as tk
from tkinter import messagebox

BG = "#ffffff"
FG = "#333333"

_content_frame = None
_filter_profitable = None  # BooleanVar for "Show only profitable" filter


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

    # Safe mousewheel binding
    def _on_enter(event):
        canvas.bind("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

    def _on_leave(event):
        canvas.unbind("<MouseWheel>")
        canvas.unbind("<Button-4>")
        canvas.unbind("<Button-5>")

    canvas.bind("<Enter>", _on_enter)
    canvas.bind("<Leave>", _on_leave)

    # Title
    tk.Label(inner, text="★ My Rules (manual only)", font=("Arial", 16, "bold"),
             bg=BG, fg=FG).pack(pady=(20, 5))
    tk.Label(inner, text="Rules you've manually saved via the ★ My Rules button",
             font=("Arial", 10), bg=BG, fg="#666666").pack(pady=(0, 15))

    # Action buttons
    btn_frame = tk.Frame(inner, bg=BG)
    btn_frame.pack(fill="x", padx=20, pady=5)

    tk.Button(btn_frame, text="🔄 Refresh",
              command=lambda: _refresh_list(inner, canvas, window_id),
              bg="#667eea", fg="white", font=("Arial", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(side=tk.LEFT, padx=(0, 5))

    tk.Button(btn_frame, text="🗑️ Delete All",
              command=lambda: _delete_all(inner, canvas, window_id),
              bg="#dc3545", fg="white", font=("Arial", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=12, pady=4).pack(side=tk.LEFT, padx=(0, 5))

    # Profitable filter
    global _filter_profitable
    _filter_profitable = tk.BooleanVar(value=True)

    filter_frame = tk.Frame(inner, bg="#f8f9fa", padx=10, pady=8)
    filter_frame.pack(fill="x", padx=20, pady=(5, 0))

    tk.Label(filter_frame, text="🔍 Filters:", font=("Arial", 9, "bold"),
             bg="#f8f9fa", fg="#495057").pack(side=tk.LEFT, padx=(0, 10))

    tk.Checkbutton(filter_frame, text="Show only profitable rules",
                   variable=_filter_profitable,
                   command=lambda: _refresh_list(inner, canvas, window_id),
                   bg="#f8f9fa", fg="#495057", font=("Arial", 9),
                   activebackground="#f8f9fa", selectcolor="#ffffff",
                   cursor="hand2").pack(side=tk.LEFT)

    # Content frame for rule cards
    _content_frame = tk.Frame(inner, bg=BG)
    _content_frame.pack(fill="both", expand=True, padx=20, pady=10)

    _refresh_list(inner, canvas, window_id)

    return panel


def _refresh_list(inner, canvas, window_id):
    global _content_frame, _filter_profitable

    for widget in list(_content_frame.winfo_children()):
        try:
            widget.destroy()
        except Exception:
            pass

    from shared.my_rules import load_all
    all_entries = load_all()

    if not all_entries:
        tk.Label(_content_frame, text="No manual rules yet.\n\nUse the ★ My Rules button on the Refiner grid or optimizer cards.",
                 font=("Arial", 11), bg=BG, fg="#888888").pack(pady=20)
        return

    # Apply profitable filter
    total_count = len(all_entries)
    if _filter_profitable and _filter_profitable.get():
        filtered_entries = []
        for entry in all_entries:
            rule = entry.get('rule', {})
            total_pips = rule.get('total_pips', 0) or 0
            net_total_pips = rule.get('net_total_pips', 0) or 0
            profit_factor = rule.get('net_profit_factor', 0) or 0

            is_profitable = (
                total_pips > 0 or
                net_total_pips > 0 or
                profit_factor > 1.0
            )

            if is_profitable:
                filtered_entries.append(entry)

        all_entries = filtered_entries

    # Show count
    if _filter_profitable and _filter_profitable.get():
        count_text = f"{len(all_entries)} of {total_count} rules (profitable only)"
    else:
        count_text = f"{len(all_entries)} manual rules"

    tk.Label(_content_frame, text=count_text,
             font=("Arial", 10, "bold"), bg=BG, fg=FG).pack(anchor="w", pady=(0, 10))

    if not all_entries and total_count > 0:
        tk.Label(_content_frame,
                 text=f"No profitable rules found.\n\n"
                      f"Uncheck the filter to see all {total_count} rules.",
                 font=("Arial", 11), bg=BG, fg="#888888").pack(pady=20)
        return

    # Group by firm
    groups = {}
    for entry in all_entries:
        rule = entry.get('rule', {})
        firm = rule.get('prop_firm_name', '') or rule.get('firm_name', '') or 'No Firm'
        if firm not in groups:
            groups[firm] = []
        groups[firm].append(entry)

    sorted_firms = sorted(groups.keys(), key=lambda f: (f == 'No Firm', -len(groups[f])))

    for firm_name in sorted_firms:
        entries = groups[firm_name]

        status_order = {'deployed': 0, 'validated': 1, 'backtested': 2, 'discovered': 3}
        entries.sort(key=lambda e: status_order.get(
            e.get('rule', {}).get('status', 'discovered'), 3))

        _render_firm_header(_content_frame, firm_name, entries)

        for entry in entries:
            _render_clean_card(_content_frame, entry, inner, canvas, window_id)


def _render_firm_header(parent, firm_name, entries):
    """Render a firm group header with summary."""
    frame = tk.Frame(parent, bg='#dfe6e9', padx=10, pady=6)
    frame.pack(fill='x', padx=5, pady=(10, 2))

    statuses = [e.get('rule', {}).get('status', 'discovered') for e in entries]
    summary_parts = []
    for s in ['deployed', 'validated', 'backtested', 'discovered']:
        count = statuses.count(s)
        if count > 0:
            summary_parts.append(f"{count} {s}")

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
    header = tk.Frame(card, bg="#ffffff")
    header.pack(fill="x", pady=(0, 6))

    _display_id = entry.get('rule_id', f"#{entry.get('id')}")
    tk.Label(header, text=_display_id,
             font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#f5c518"
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

    # Status indicator
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

    # Date — right-aligned
    _saved_at_hdr = entry.get('saved_at', '')
    if _saved_at_hdr:
        try:
            from datetime import datetime as _dt_parse
            _parsed = _dt_parse.fromisoformat(_saved_at_hdr.replace('Z', '+00:00'))
            _date_display = _parsed.strftime('%Y-%m-%d %H:%M')
        except Exception:
            _date_display = str(_saved_at_hdr)[:16]
        tk.Label(header, text=_date_display,
                 font=("Segoe UI", 8), bg="#ffffff", fg="#999"
                 ).pack(side=tk.RIGHT, padx=(0, 8))

    # Star button
    try:
        from shared.starred import toggle as _sr_toggle, is_starred as _sr_is_starred
        _sr_rc = rule.get('rule_combo', entry.get('rule_id', ''))
        _sr_exit = rule.get('exit_name', rule.get('exit_class', ''))
        _sr_tf = rule.get('entry_timeframe', rule.get('entry_tf', ''))
        _sr_starred = _sr_is_starred(_sr_rc, _sr_exit, _sr_tf)

        _sr_btn_ref = [None]
        def _sr_make_toggle(rc, es, tf, btn_ref):
            def _toggle():
                new_state = _sr_toggle(rc, es, tf)
                btn_ref[0].configure(
                    text="⭐" if new_state else "☆",
                    bg="#f39c12" if new_state else "#95a5a6",
                )
            return _toggle

        _sr_btn_ref[0] = tk.Button(header, text="⭐" if _sr_starred else "☆",
            command=_sr_make_toggle(_sr_rc, _sr_exit, _sr_tf, _sr_btn_ref),
            bg="#f39c12" if _sr_starred else "#95a5a6", fg="white",
            font=("Segoe UI", 9), bd=0, padx=4, pady=1, cursor="hand2", relief=tk.FLAT)
        _sr_btn_ref[0].pack(side=tk.RIGHT, padx=(0, 3))
    except Exception as _sr_e:
        print(f"[MY RULES] Star button error: {_sr_e}")

    # Delete button
    rid = entry.get('id')
    tk.Button(header, text="🗑️", font=("Arial", 8),
              bg="#dc3545", fg="white", relief=tk.FLAT, padx=4,
              command=lambda r=rid: _delete_one(r, inner, canvas, window_id)
              ).pack(side=tk.RIGHT)

    # ── LINE 2: Stats ──
    stats_frame = tk.Frame(card, bg="#ffffff")
    stats_frame.pack(fill="x", pady=(0, 6))

    wr = rule.get('win_rate') or 0
    pf = rule.get('net_profit_factor', rule.get('profit_factor', 0)) or 0
    pips = rule.get('avg_pips') or 0
    trades = rule.get('total_trades', rule.get('coverage', 0)) or 0

    _wr_display = wr * 100 if wr <= 1.0 else wr

    if _wr_display > 55 and pf > 1.5:
        _stats_color = '#28a745'
    elif _wr_display > 50 or pf > 1.0:
        _stats_color = '#f39c12'
    else:
        _stats_color = '#dc3545'

    _risk_display = float(rule.get('risk_pct', 0) or 0)
    _risk_str = f"  |  Risk: {_risk_display}%" if _risk_display > 0 else ""
    _acct_display = float(rule.get('account_size', 0) or 0)
    _acct_str = f"  |  ${int(_acct_display):,}" if _acct_display > 0 else ""
    stats_text = f"WR: {_wr_display:.0f}%  |  PF: {pf:.2f}  |  Avg: {pips:+.0f} pips  |  {int(trades)} trades{_risk_str}{_acct_str}"
    tk.Label(stats_frame, text=stats_text,
             font=("Segoe UI", 9, "bold"), bg="#ffffff", fg=_stats_color
             ).pack(side=tk.LEFT)

    # ── Conditions ──
    for cond in rule.get('conditions', []):
        try:
            from helpers import normalize_condition as _nc
            _cond = _nc(cond) if not isinstance(cond, dict) else cond
            if isinstance(_cond, dict) and _cond:
                _feat = _cond.get('feature', '?')
                _op = _cond.get('operator', '>')
                _val = _cond.get('value', 0)
                _op_display = {'<=': '≤', '>=': '≥', '==': '=', '!=': '≠'}.get(_op, _op)
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

    # ── Discovery Settings ──
    _ds = rule.get('discovery_settings', {})
    if _ds:
        _settings_frame = tk.Frame(card, bg="#f0f0f5", padx=6, pady=4)
        _settings_frame.pack(fill="x", pady=(4, 0))
        _setting_parts = []
        if _ds.get('regime_filter_enabled'):
            _rf_text = "Regime: ON"
            if _ds.get('regime_at_discovery'):
                _rf_text += " (at discovery)"
            if _ds.get('regime_strictness'):
                _rf_text += f" [{_ds['regime_strictness']}]"
            _setting_parts.append(_rf_text)
        else:
            _setting_parts.append("Regime: OFF")
        if _ds.get('single_rule_mode_enabled'):
            _variant = _ds.get('single_rule_mode_variant', 'a').upper()
            _variant_names = {'A': 'Mode A (tightest)', 'B': 'Mode B (crossover)',
                              'C': 'Mode C (two-feature)', 'D': 'Mode D (regime-gated)'}
            _srm_text = f"Single Rule: {_variant_names.get(_variant, f'Mode {_variant}')}"
            if _variant == 'A':
                _dedup = "dedup" if _ds.get('srm_dedup_correlated') else "no-dedup"
                _winner = _ds.get('srm_winner_selection', 'tightness')
                _srm_text += f" ({_dedup}, {_winner})"
            _setting_parts.append(_srm_text)
        _ds_id = rule.get('data_source_id', '')
        if _ds_id and _ds_id != 'original':
            _setting_parts.append(f"Data: {_ds_id}")
        if _setting_parts:
            tk.Label(_settings_frame, text="  •  ".join(_setting_parts),
                     font=("Segoe UI", 8), bg="#f0f0f5", fg="#555",
                     wraplength=600, justify=tk.LEFT
                     ).pack(anchor="w")

    # Regime filter conditions
    _rf = rule.get('regime_filter')
    if _rf and isinstance(_rf, list) and len(_rf) > 0:
        _rf_frame = tk.Frame(card, bg="#f5f0fa", padx=6, pady=3)
        _rf_frame.pack(fill="x", pady=(2, 0))
        tk.Label(_rf_frame, text=f"🔀 Regime filter ({len(_rf)} conditions):",
                 font=("Segoe UI", 8, "bold"), bg="#f5f0fa", fg="#9b59b6"
                 ).pack(anchor="w")
        for _rc in _rf:
            if isinstance(_rc, dict):
                _feat = _rc.get('feature', '?')
                _op = _rc.get('direction', _rc.get('operator', '>'))
                _val = _rc.get('threshold', _rc.get('value', '?'))
                try: _val = f"{float(_val):.4f}"
                except Exception: _val = str(_val)
                tk.Label(_rf_frame, text=f"  {_feat} {_op} {_val}",
                         font=("Courier New", 8), bg="#f5f0fa", fg="#7b2d8e"
                         ).pack(anchor="w")

    # ── Action Buttons ──
    actions_frame = tk.Frame(card, bg="#ffffff")
    actions_frame.pack(fill="x", pady=(6, 0))

    if status == 'discovered':
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
        tk.Button(actions_frame, text="✓ Validate", font=("Segoe UI", 9, "bold"),
                  bg="#27ae60", fg="white", relief=tk.FLAT, padx=10, pady=4,
                  command=lambda: messagebox.showinfo("Validate", "Open Strategy Validator panel to validate this rule")
                  ).pack(side=tk.LEFT, padx=(0, 4))

    elif status == 'validated':
        tk.Button(actions_frame, text="⚡ Generate EA", font=("Segoe UI", 9, "bold"),
                  bg="#9b59b6", fg="white", relief=tk.FLAT, padx=10, pady=4,
                  command=lambda: messagebox.showinfo("Generate EA", "Open EA Generator panel to generate code for this rule")
                  ).pack(side=tk.LEFT, padx=(0, 4))

    # Notes
    if entry.get('notes'):
        tk.Label(card, text=f"📝 {entry['notes']}",
                 font=("Segoe UI", 8, "italic"), bg="#ffffff", fg="#7f8c8d"
                 ).pack(anchor="w", pady=(4, 0))

    # ── Footer: Source + Date ──
    _source = entry.get('source', '')
    _saved_at = entry.get('saved_at', '')
    _date_str = ''
    if _saved_at:
        try:
            from datetime import datetime as _dt_parse
            _parsed = _dt_parse.fromisoformat(_saved_at.replace('Z', '+00:00'))
            _date_str = _parsed.strftime('%Y-%m-%d %H:%M')
        except Exception:
            _date_str = str(_saved_at)[:16]

    _footer_parts = []
    if _source:
        _footer_parts.append(f"from {_source}")
    if _date_str:
        _footer_parts.append(f"• {_date_str}")

    if _footer_parts:
        _footer = tk.Frame(card, bg="#ffffff")
        _footer.pack(fill="x", pady=(4, 0))
        tk.Label(_footer, text='  '.join(_footer_parts),
                 font=("Segoe UI", 8), bg="#ffffff", fg="#95a5a6"
                 ).pack(side=tk.LEFT)


def _delete_one(rule_id, inner, canvas, window_id):
    from shared.my_rules import delete_rule
    delete_rule(rule_id)
    _refresh_list(inner, canvas, window_id)


def _delete_all(inner, canvas, window_id):
    try:
        from shared.my_rules import load_all
        _count = len(load_all())
    except Exception:
        _count = 0
    _noun = "rule" if _count == 1 else "rules"
    if messagebox.askyesno(
        "Delete All",
        f"Delete all {_count} manual {_noun}?\n\nThis cannot be undone."
    ):
        from shared.my_rules import delete_all
        delete_all()
        _refresh_list(inner, canvas, window_id)


def refresh():
    pass  # Panel refreshes on build

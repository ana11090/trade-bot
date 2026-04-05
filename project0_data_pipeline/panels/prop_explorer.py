"""
Prop Firm Explorer — browse, edit, clone, and delete prop firms.
Fully editable — all changes save to JSON files immediately.
"""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import os
import json
import state

_tree     = None
_all_rows = []
_filter_var = None
_PROP_DIR = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')), 'prop_firms')


# ─────────────────────────────────────────────────────────────────────────────
# CRUD Operations
# ─────────────────────────────────────────────────────────────────────────────

def _load_all_firms():
    """Load all prop firm JSON files."""
    firms = {}
    if not os.path.isdir(_PROP_DIR):
        return firms
    for f in sorted(os.listdir(_PROP_DIR)):
        if f.endswith('.json'):
            try:
                with open(os.path.join(_PROP_DIR, f), 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                firms[f] = data
            except Exception:
                pass
    return firms


def _save_firm(filename, data):
    """Save a prop firm to JSON."""
    path = os.path.join(_PROP_DIR, filename)
    os.makedirs(_PROP_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def _delete_firm(filename):
    """Delete a prop firm JSON file."""
    path = os.path.join(_PROP_DIR, filename)
    if os.path.exists(path):
        os.remove(path)


def _clone_firm(source_filename, new_name):
    """Clone a firm — copy JSON, change the name."""
    source_path = os.path.join(_PROP_DIR, source_filename)
    with open(source_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    data['firm_name'] = new_name
    data['firm_id'] = new_name.lower().replace(' ', '_')

    new_filename = data['firm_id'] + '.json'
    _save_firm(new_filename, data)
    return new_filename


# ─────────────────────────────────────────────────────────────────────────────
# Edit Dialog
# ─────────────────────────────────────────────────────────────────────────────

def _open_edit_dialog(parent, filename, data, on_save_callback):
    """Open a dialog to edit all fields of a prop firm."""
    win = tk.Toplevel(parent)
    win.title(f"Edit — {data.get('firm_name', '?')}")
    win.geometry("700x800")
    win.minsize(600, 600)

    # Scrollable content
    canvas = tk.Canvas(win, bg="#ffffff", highlightthickness=0)
    scrollbar = tk.Scrollbar(win, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side=tk.RIGHT, fill="y")
    canvas.pack(side=tk.LEFT, fill="both", expand=True)

    inner = tk.Frame(canvas, bg="#ffffff", padx=20, pady=15)
    wid = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(wid, width=e.width))

    def _mw(e):
        canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _mw))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    tk.Label(inner, text=f"Editing: {data.get('firm_name', '?')}", font=("Arial", 14, "bold"),
             bg="#ffffff", fg="#333").pack(anchor="w", pady=(0, 10))

    entries = {}  # key → StringVar

    def _add_field(parent_frame, label, key, value, description=""):
        row = tk.Frame(parent_frame, bg="#ffffff")
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, font=("Arial", 9, "bold"), bg="#ffffff",
                 fg="#333", width=25, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar(value=str(value))
        tk.Entry(row, textvariable=var, font=("Arial", 9), width=30).pack(side=tk.LEFT, padx=5)
        entries[key] = var
        if description:
            tk.Label(parent_frame, text=description, font=("Arial", 8),
                     bg="#ffffff", fg="#888").pack(anchor="w", padx=(0, 0), pady=(0, 3))

    # ── Top-level fields ──
    top_lf = tk.LabelFrame(inner, text="🏢 Firm Info", font=("Arial", 10, "bold"),
                            bg="#ffffff", fg="#555", padx=10, pady=8)
    top_lf.pack(fill="x", pady=(0, 10))

    _add_field(top_lf, "Firm Name", "firm_name", data.get('firm_name', ''), "Display name")
    _add_field(top_lf, "Website", "website", data.get('website', ''))
    _add_field(top_lf, "Market Type", "market_type", data.get('market_type', 'forex_cfd'),
               "forex_cfd, futures, crypto")
    _add_field(top_lf, "DD Reset Timezone", "dd_reset_timezone", data.get('dd_reset_timezone', 'UTC'),
               "CET, CT, ET, UTC")
    _add_field(top_lf, "Swap XAUUSD $/lot/night", "typical_swap_xauusd_per_lot_per_night",
               data.get('typical_swap_xauusd_per_lot_per_night', 0),
               "Overnight swap cost. 0 for futures firms.")

    # ── For each challenge ──
    for ci, challenge in enumerate(data.get('challenges', [])):
        ch_lf = tk.LabelFrame(inner, text=f"📋 Challenge: {challenge.get('challenge_name', f'#{ci+1}')}",
                               font=("Arial", 10, "bold"), bg="#ffffff", fg="#8e44ad",
                               padx=10, pady=8)
        ch_lf.pack(fill="x", pady=(0, 10))

        _add_field(ch_lf, "Challenge Name", f"c{ci}_name", challenge.get('challenge_name', ''))
        _add_field(ch_lf, "Account Sizes", f"c{ci}_sizes",
                   ','.join(str(s) for s in challenge.get('account_sizes', [])),
                   "Comma-separated: 10000,25000,50000,100000,200000")

        # Phases
        for pi, phase in enumerate(challenge.get('phases', [])):
            ph_lf = tk.LabelFrame(ch_lf, text=f"Phase {phase.get('phase_number', pi+1)}: {phase.get('phase_name', '')}",
                                   font=("Arial", 9, "bold"), bg="#ffffff", fg="#333",
                                   padx=8, pady=5)
            ph_lf.pack(fill="x", pady=3)

            _add_field(ph_lf, "Profit Target %", f"c{ci}_p{pi}_target", phase.get('profit_target_pct', 10))
            _add_field(ph_lf, "Daily DD %", f"c{ci}_p{pi}_daily_dd", phase.get('max_daily_drawdown_pct', 5))
            _add_field(ph_lf, "Total DD %", f"c{ci}_p{pi}_total_dd", phase.get('max_total_drawdown_pct', 10))
            _add_field(ph_lf, "DD Type", f"c{ci}_p{pi}_dd_type", phase.get('drawdown_type', 'static'),
                       "static, trailing, trailing_eod")
            _add_field(ph_lf, "Min Trading Days", f"c{ci}_p{pi}_min_days", phase.get('min_trading_days', 4))
            _add_field(ph_lf, "Max Calendar Days", f"c{ci}_p{pi}_max_days",
                       phase.get('max_calendar_days', '') or '',
                       "Leave empty for unlimited")
            _add_field(ph_lf, "Consistency Rule %", f"c{ci}_p{pi}_consistency",
                       phase.get('consistency_rule_pct', '') or '',
                       "Leave empty for none. 50 = no single day > 50% of total profit")

        # Funded
        funded = challenge.get('funded', {})
        fund_lf = tk.LabelFrame(ch_lf, text="💰 Funded Account",
                                 font=("Arial", 9, "bold"), bg="#ffffff", fg="#28a745",
                                 padx=8, pady=5)
        fund_lf.pack(fill="x", pady=3)

        _add_field(fund_lf, "Profit Split %", f"c{ci}_f_split", funded.get('profit_split_pct', 80))
        _add_field(fund_lf, "Max Split %", f"c{ci}_f_max_split", funded.get('max_profit_split_pct', 90))
        _add_field(fund_lf, "Daily DD %", f"c{ci}_f_daily_dd", funded.get('max_daily_drawdown_pct', 5))
        _add_field(fund_lf, "Total DD %", f"c{ci}_f_total_dd", funded.get('max_total_drawdown_pct', 10))
        _add_field(fund_lf, "DD Type", f"c{ci}_f_dd_type", funded.get('drawdown_type', 'static'))
        _add_field(fund_lf, "Payout Frequency", f"c{ci}_f_payout", funded.get('payout_frequency', 'biweekly'),
                   "biweekly, monthly, on_demand")

        # Restrictions
        restr = challenge.get('restrictions', {})
        restr_lf = tk.LabelFrame(ch_lf, text="⚠️ Restrictions",
                                  font=("Arial", 9, "bold"), bg="#ffffff", fg="#e67e22",
                                  padx=8, pady=5)
        restr_lf.pack(fill="x", pady=3)

        _add_field(restr_lf, "News Trading", f"c{ci}_r_news", restr.get('news_trading_allowed', True),
                   "True or False")
        _add_field(restr_lf, "News Blackout (min)", f"c{ci}_r_blackout", restr.get('news_blackout_minutes', 0))
        _add_field(restr_lf, "Weekend Holding", f"c{ci}_r_weekend", restr.get('weekend_holding_allowed', True))
        _add_field(restr_lf, "EA Allowed", f"c{ci}_r_ea", restr.get('ea_allowed', True))

        # Costs
        costs = challenge.get('costs', {})
        costs_lf = tk.LabelFrame(ch_lf, text="💸 Costs",
                                  font=("Arial", 9, "bold"), bg="#ffffff", fg="#dc3545",
                                  padx=8, pady=5)
        costs_lf.pack(fill="x", pady=3)

        fee_by_size = costs.get('challenge_fee_by_size', {})
        _add_field(costs_lf, "Fee by size", f"c{ci}_cost_fees",
                   ','.join(f'{k}:{v}' for k, v in fee_by_size.items()),
                   "Format: 10000:155,25000:250,100000:540")
        _add_field(costs_lf, "Fee Refundable", f"c{ci}_cost_refund", costs.get('fee_refundable', True),
                   "True or False")

    # ── Save / Cancel buttons ──
    btn_row = tk.Frame(inner, bg="#ffffff")
    btn_row.pack(fill="x", pady=(15, 0))

    def _on_save():
        # Read all entries back into the data structure
        data['firm_name'] = entries['firm_name'].get()
        data['website'] = entries.get('website', tk.StringVar(value='')).get()
        data['market_type'] = entries.get('market_type', tk.StringVar(value='forex_cfd')).get()
        data['dd_reset_timezone'] = entries.get('dd_reset_timezone', tk.StringVar(value='UTC')).get()

        try:
            data['typical_swap_xauusd_per_lot_per_night'] = float(
                entries.get('typical_swap_xauusd_per_lot_per_night', tk.StringVar(value='0')).get())
        except ValueError:
            data['typical_swap_xauusd_per_lot_per_night'] = 0

        for ci, challenge in enumerate(data.get('challenges', [])):
            challenge['challenge_name'] = entries.get(f'c{ci}_name', tk.StringVar(value='')).get()

            sizes_str = entries.get(f'c{ci}_sizes', tk.StringVar(value='')).get()
            try:
                challenge['account_sizes'] = [int(s.strip()) for s in sizes_str.split(',') if s.strip()]
            except ValueError:
                pass

            for pi, phase in enumerate(challenge.get('phases', [])):
                try:
                    phase['profit_target_pct'] = float(entries[f'c{ci}_p{pi}_target'].get())
                except (KeyError, ValueError): pass
                try:
                    phase['max_daily_drawdown_pct'] = float(entries[f'c{ci}_p{pi}_daily_dd'].get())
                except (KeyError, ValueError): pass
                try:
                    phase['max_total_drawdown_pct'] = float(entries[f'c{ci}_p{pi}_total_dd'].get())
                except (KeyError, ValueError): pass
                try:
                    phase['drawdown_type'] = entries[f'c{ci}_p{pi}_dd_type'].get()
                except KeyError: pass
                try:
                    phase['min_trading_days'] = int(entries[f'c{ci}_p{pi}_min_days'].get())
                except (KeyError, ValueError): pass
                try:
                    val = entries[f'c{ci}_p{pi}_max_days'].get()
                    phase['max_calendar_days'] = int(val) if val.strip() else None
                except (KeyError, ValueError):
                    phase['max_calendar_days'] = None
                try:
                    val = entries[f'c{ci}_p{pi}_consistency'].get()
                    phase['consistency_rule_pct'] = float(val) if val.strip() else None
                except (KeyError, ValueError):
                    phase['consistency_rule_pct'] = None

            funded = challenge.get('funded', {})
            try: funded['profit_split_pct'] = float(entries[f'c{ci}_f_split'].get())
            except (KeyError, ValueError): pass
            try: funded['max_profit_split_pct'] = float(entries[f'c{ci}_f_max_split'].get())
            except (KeyError, ValueError): pass
            try: funded['max_daily_drawdown_pct'] = float(entries[f'c{ci}_f_daily_dd'].get())
            except (KeyError, ValueError): pass
            try: funded['max_total_drawdown_pct'] = float(entries[f'c{ci}_f_total_dd'].get())
            except (KeyError, ValueError): pass
            try: funded['drawdown_type'] = entries[f'c{ci}_f_dd_type'].get()
            except KeyError: pass
            try: funded['payout_frequency'] = entries[f'c{ci}_f_payout'].get()
            except KeyError: pass

            restr = challenge.get('restrictions', {})
            try: restr['news_trading_allowed'] = entries[f'c{ci}_r_news'].get().lower() == 'true'
            except KeyError: pass
            try: restr['news_blackout_minutes'] = int(entries[f'c{ci}_r_blackout'].get())
            except (KeyError, ValueError): pass
            try: restr['weekend_holding_allowed'] = entries[f'c{ci}_r_weekend'].get().lower() == 'true'
            except KeyError: pass
            try: restr['ea_allowed'] = entries[f'c{ci}_r_ea'].get().lower() == 'true'
            except KeyError: pass

            costs = challenge.get('costs', {})
            try:
                fees_str = entries[f'c{ci}_cost_fees'].get()
                fee_dict = {}
                for pair in fees_str.split(','):
                    if ':' in pair:
                        k, v = pair.strip().split(':')
                        fee_dict[k.strip()] = int(v.strip())
                costs['challenge_fee_by_size'] = fee_dict
            except (KeyError, ValueError): pass
            try: costs['fee_refundable'] = entries[f'c{ci}_cost_refund'].get().lower() == 'true'
            except KeyError: pass

        _save_firm(filename, data)
        messagebox.showinfo("Saved", f"Prop firm '{data['firm_name']}' saved to {filename}")
        win.destroy()
        if on_save_callback:
            on_save_callback()

    tk.Button(btn_row, text="💾 Save Changes", font=("Arial", 11, "bold"),
              bg="#28a745", fg="white", relief=tk.FLAT, padx=20, pady=8,
              command=_on_save).pack(side=tk.LEFT, padx=(0, 10))

    tk.Button(btn_row, text="Cancel", font=("Arial", 11),
              bg="#6c757d", fg="white", relief=tk.FLAT, padx=20, pady=8,
              command=win.destroy).pack(side=tk.LEFT)


# ─────────────────────────────────────────────────────────────────────────────
# Panel UI
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(content):
    global _tree, _filter_var

    panel = tk.Frame(content, bg="#f0f2f5")

    tk.Label(panel, text="Prop Firm Explorer", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
    tk.Label(panel, text="Browse, edit, clone, and delete prop firms — all changes save to JSON",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

    # Filter buttons
    filter_frame = tk.Frame(panel, bg="#f0f2f5")
    filter_frame.pack(fill="x", padx=20, pady=(0, 10))
    panel._filter_frame = filter_frame

    _filter_var = tk.StringVar(value="All")

    tk.Button(filter_frame, text="All", font=("Segoe UI", 9, "bold"),
              bg="#e94560", fg="white", bd=0, padx=10, pady=3,
              command=lambda: _apply_filter("All")).pack(side="left", padx=(0, 4))

    # Action buttons
    action_frame = tk.Frame(panel, bg="#f0f2f5")
    action_frame.pack(fill="x", padx=20, pady=(0, 10))

    def _reload_table():
        """Reload all firms from disk and refresh the Treeview."""
        _load_data()
        _apply_filter(_filter_var.get() if _filter_var else "All")
        # Rebuild firm filter buttons
        if _tree is not None:
            try:
                firm_names = sorted({row[0] for row in _all_rows})
                _rebuild_firm_buttons(filter_frame, firm_names)
            except Exception:
                pass

    def _on_edit():
        sel = _tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Click a row in the table first.")
            return
        item = _tree.item(sel[0])
        firm_name = item['values'][0]
        # Find the file
        firms = _load_all_firms()
        for fname, fdata in firms.items():
            if fdata.get('firm_name') == firm_name:
                _open_edit_dialog(panel, fname, fdata, on_save_callback=lambda: _reload_table())
                return
        messagebox.showwarning("Not Found", f"Could not find file for '{firm_name}'")

    def _on_clone():
        sel = _tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Click a row to clone from.")
            return
        item = _tree.item(sel[0])
        source_name = item['values'][0]

        new_name = simpledialog.askstring("Clone Firm",
            f"Creating a copy of '{source_name}'.\n\nEnter name for the new firm:",
            parent=panel)
        if not new_name:
            return

        firms = _load_all_firms()
        for fname, fdata in firms.items():
            if fdata.get('firm_name') == source_name:
                new_file = _clone_firm(fname, new_name)
                messagebox.showinfo("Cloned", f"Created '{new_name}' from '{source_name}'.\n\nFile: {new_file}")
                _reload_table()
                return

    def _on_add_new():
        new_name = simpledialog.askstring("New Prop Firm",
            "Enter the firm name:", parent=panel)
        if not new_name:
            return

        # Create from a template (clone first firm found)
        firms = _load_all_firms()
        template = None
        for fname, fdata in firms.items():
            template = (fname, fdata)
            break  # use first firm as template

        if template:
            new_file = _clone_firm(template[0], new_name)
            # Open editor immediately
            with open(os.path.join(_PROP_DIR, new_file), 'r') as f:
                new_data = json.load(f)
            _reload_table()
            _open_edit_dialog(panel, new_file, new_data, on_save_callback=lambda: _reload_table())
        else:
            messagebox.showwarning("No Template", "No existing firms to use as template.")

    def _on_delete():
        sel = _tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Click a row to delete.")
            return
        item = _tree.item(sel[0])
        firm_name = item['values'][0]

        if not messagebox.askyesno("Delete", f"Delete '{firm_name}' permanently?"):
            return

        firms = _load_all_firms()
        for fname, fdata in firms.items():
            if fdata.get('firm_name') == firm_name:
                _delete_firm(fname)
                messagebox.showinfo("Deleted", f"'{firm_name}' deleted.")
                _reload_table()
                return

    tk.Button(action_frame, text="✏️ Edit Selected", font=("Arial", 9, "bold"),
              bg="#667eea", fg="white", relief=tk.FLAT, padx=12, pady=5,
              command=_on_edit).pack(side=tk.LEFT, padx=(0, 5))

    tk.Button(action_frame, text="📋 Clone Selected", font=("Arial", 9, "bold"),
              bg="#28a745", fg="white", relief=tk.FLAT, padx=12, pady=5,
              command=_on_clone).pack(side=tk.LEFT, padx=(0, 5))

    tk.Button(action_frame, text="➕ Add New", font=("Arial", 9, "bold"),
              bg="#17a2b8", fg="white", relief=tk.FLAT, padx=12, pady=5,
              command=_on_add_new).pack(side=tk.LEFT, padx=(0, 5))

    tk.Button(action_frame, text="🗑️ Delete Selected", font=("Arial", 9, "bold"),
              bg="#dc3545", fg="white", relief=tk.FLAT, padx=12, pady=5,
              command=_on_delete).pack(side=tk.LEFT)

    tk.Label(action_frame, text="Double-click a row to edit",
             font=("Arial", 8), bg="#f0f2f5", fg="#888").pack(side=tk.LEFT, padx=(15, 0))

    # Treeview
    tree_frame = tk.Frame(panel, bg="#f0f2f5")
    tree_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))

    cols = ("firm", "stage", "target", "daily_dd", "max_dd", "min_days",
            "consistency", "profit_days", "payout", "leverage", "sizes")
    _tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=18)

    _tree.heading("firm",         text="Firm")
    _tree.heading("stage",        text="Stage")
    _tree.heading("target",       text="Target")
    _tree.heading("daily_dd",     text="Daily DD")
    _tree.heading("max_dd",       text="Max DD")
    _tree.heading("min_days",     text="Min Days")
    _tree.heading("consistency",  text="Consistency")
    _tree.heading("profit_days",  text="Profit Days")
    _tree.heading("payout",       text="Payout")
    _tree.heading("leverage",     text="Leverage")
    _tree.heading("sizes",        text="Sizes")

    _tree.column("firm",         width=100)
    _tree.column("stage",        width=110)
    _tree.column("target",       width=55,  anchor="center")
    _tree.column("daily_dd",     width=60,  anchor="center")
    _tree.column("max_dd",       width=100, anchor="center")
    _tree.column("min_days",     width=60,  anchor="center")
    _tree.column("consistency",  width=75,  anchor="center")
    _tree.column("profit_days",  width=90,  anchor="center")
    _tree.column("payout",       width=100, anchor="center")
    _tree.column("leverage",     width=60,  anchor="center")
    _tree.column("sizes",        width=130)

    # Color code eval vs funded rows
    _tree.tag_configure("eval", background="#f0f8ff")
    _tree.tag_configure("funded", background="#f0fff0")

    scrollbar_y = ttk.Scrollbar(tree_frame, orient="vertical",   command=_tree.yview)
    scrollbar_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=_tree.xview)
    _tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
    _tree.pack(side="left", fill="both", expand=True)
    scrollbar_y.pack(side="right", fill="y")

    # Double-click to edit
    _tree.bind("<Double-1>", lambda e: _on_edit())

    # Trading rules info panel
    rules_frame = tk.LabelFrame(panel, text="📋 Trading Rules (select a firm above)",
                                 font=("Segoe UI", 10, "bold"), bg="#f0f2f5", fg="#333",
                                 padx=10, pady=8)
    rules_frame.pack(fill="x", padx=20, pady=(0, 10))

    _rules_info_label = tk.Label(rules_frame, text="Click a row to see detailed trading rules",
                                  font=("Segoe UI", 9), bg="#f0f2f5", fg="#666",
                                  justify=tk.LEFT, anchor="nw", wraplength=800)
    _rules_info_label.pack(fill="x")
    panel._rules_info_label = _rules_info_label

    # Bind row selection to show trading rules
    def _on_row_select(event):
        sel = _tree.selection()
        if not sel:
            return
        item = _tree.item(sel[0])
        firm_name = item['values'][0]

        # Find trading rules for this firm
        import glob
        for fp in glob.glob(os.path.join(_PROP_DIR, '*.json')):
            try:
                with open(fp, encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('firm_name') == firm_name and data.get('trading_rules'):
                    rules_text = f"Trading Rules for {firm_name}:\n\n"
                    for rule in data['trading_rules']:
                        rules_text += f"  {rule['name']}\n"
                        rules_text += f"  Stage: {rule['stage']}\n"
                        rules_text += f"  {rule['description']}\n\n"

                    _rules_info_label.config(text=rules_text)
                    return
            except Exception:
                continue

        # No trading rules found
        _rules_info_label.config(text="No special trading rules defined for this firm.")

    _tree.bind("<<TreeviewSelect>>", _on_row_select)

    return panel


def _format_sizes(sizes):
    if not sizes:
        return "—"
    s_min, s_max = min(sizes), max(sizes)
    def _fmt(v):
        return f"${v // 1000}K" if v >= 1000 else f"${v}"
    return _fmt(s_min) if s_min == s_max else f"{_fmt(s_min)}-{_fmt(s_max)}"


def _format_targets(phases):
    if not phases:
        return "Instant"
    targets = [f"{p['profit_target_pct']}%" for p in phases if p.get("profit_target_pct")]
    return " / ".join(targets) if targets else "—"


def _load_data():
    """Load all prop firms and build rows — one per eval phase + one for funded."""
    global _all_rows
    _all_rows = []

    import glob
    firm_names = set()

    for fp in sorted(glob.glob(os.path.join(_PROP_DIR, '*.json'))):
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue

        name = data.get('firm_name', '?')
        firm_names.add(name)
        leverage_map = data.get('leverage_by_size', {})
        default_leverage = list(leverage_map.values())[0] if leverage_map else '—'

        for challenge in data.get('challenges', []):
            ch_name = challenge.get('challenge_name', '?')
            sizes = challenge.get('account_sizes', [])
            sizes_str = ', '.join(f'${s//1000}K' for s in sizes[:4])
            funded = challenge.get('funded', {})

            # EVALUATION rows
            for phase in challenge.get('phases', []):
                target = phase.get('profit_target_pct', '—')
                daily_dd = phase.get('max_daily_drawdown_pct', '—')
                total_dd = phase.get('max_total_drawdown_pct', '—')
                dd_type = phase.get('drawdown_type', 'static')
                min_days = phase.get('min_trading_days', 0)
                consistency = phase.get('consistency_rule_pct')

                _all_rows.append((
                    name,
                    f"📋 {phase.get('phase_name', 'Eval')}",
                    f"{target}%" if target != '—' else '—',
                    f"{daily_dd}%",
                    f"{total_dd}% {dd_type}",
                    str(min_days) if min_days else "—",
                    f"{consistency}%" if consistency else "—",
                    "—",
                    "—",
                    default_leverage,
                    sizes_str,
                    "eval"  # tag for coloring
                ))

            # FUNDED row
            daily_dd_f = funded.get('max_daily_drawdown_pct', '—')
            total_dd_f = funded.get('max_total_drawdown_pct', '—')
            dd_type_f = funded.get('drawdown_type', 'static')
            consistency_f = funded.get('consistency_rule_pct')
            min_profit_days = funded.get('min_profitable_days')
            split = funded.get('profit_split_pct', '—')
            payout_freq = funded.get('payout_frequency', '—')

            profit_days_str = "—"
            if min_profit_days:
                min_pct = funded.get('min_profitable_day_pct', 0.5)
                profit_days_str = f"{min_profit_days}d (>={min_pct}%)"

            _all_rows.append((
                name,
                "💰 Funded",
                "—",
                f"{daily_dd_f}%",
                f"{total_dd_f}% {dd_type_f}",
                "—",
                f"{consistency_f}%" if consistency_f else "—",
                profit_days_str,
                f"{split}% / {payout_freq}",
                default_leverage,
                sizes_str,
                "funded"  # tag for coloring
            ))


def _rebuild_firm_buttons(filter_frame, firm_names):
    """Rebuild per-firm filter buttons (called once after data load)."""
    # Remove existing firm buttons (keep "All" at index 0)
    for w in list(filter_frame.pack_slaves()):
        if getattr(w, "_is_firm_btn", False):
            w.destroy()
    for name in firm_names:
        btn = tk.Button(filter_frame, text=name, font=("Segoe UI", 9),
                        bg="#1e2d4e", fg="white", bd=0, padx=8, pady=3,
                        command=lambda n=name: _apply_filter(n))
        btn._is_firm_btn = True
        btn.pack(side="left", padx=(0, 4))


def _apply_filter(firm_name):
    if _tree is None:
        return
    for item in _tree.get_children():
        _tree.delete(item)
    for row in _all_rows:
        if firm_name == "All" or row[0] == firm_name:
            # Extract tag (last element) and values (all but last)
            tag = row[-1] if len(row) > 11 else ""
            values = row[:-1] if len(row) > 11 else row
            _tree.insert("", "end", values=values, tags=(tag,))


def refresh():
    _load_data()
    _apply_filter("All")
    # Rebuild firm filter buttons from loaded data
    if _tree is not None:
        try:
            filter_frame = _tree.master.master._filter_frame
            firm_names = sorted({row[0] for row in _all_rows})
            _rebuild_firm_buttons(filter_frame, firm_names)
        except Exception:
            pass

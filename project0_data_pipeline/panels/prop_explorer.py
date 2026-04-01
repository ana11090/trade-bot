"""
Prop Firm Explorer — browse and compare all prop firm rules.
No trades needed — this is a reference tool.
"""
import tkinter as tk
from tkinter import ttk
import state

_tree     = None
_all_rows = []
_filter_var = None


def build_panel(content):
    global _tree, _filter_var

    panel = tk.Frame(content, bg="#f0f2f5")

    tk.Label(panel, text="Prop Firm Explorer", bg="#f0f2f5", fg="#1a1a2a",
             font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(24, 2))
    tk.Label(panel, text="Browse and compare rules across all prop firms",
             bg="#f0f2f5", fg="#666666", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 16))

    # Filter buttons
    filter_frame = tk.Frame(panel, bg="#f0f2f5")
    filter_frame.pack(fill="x", padx=20, pady=(0, 10))
    panel._filter_frame = filter_frame

    _filter_var = tk.StringVar(value="All")

    tk.Button(filter_frame, text="All", font=("Segoe UI", 9, "bold"),
              bg="#e94560", fg="white", bd=0, padx=10, pady=3,
              command=lambda: _apply_filter("All")).pack(side="left", padx=(0, 4))

    # Treeview
    tree_frame = tk.Frame(panel, bg="#f0f2f5")
    tree_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    cols = ("firm", "challenge", "phases", "target", "daily_dd", "max_dd",
            "dd_type", "min_days", "consistency", "split", "sizes")
    _tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=22)

    _tree.heading("firm",        text="Firm")
    _tree.heading("challenge",   text="Challenge")
    _tree.heading("phases",      text="Phases")
    _tree.heading("target",      text="Target")
    _tree.heading("daily_dd",    text="Daily DD")
    _tree.heading("max_dd",      text="Max DD")
    _tree.heading("dd_type",     text="DD Type")
    _tree.heading("min_days",    text="Min Days")
    _tree.heading("consistency", text="Consistency")
    _tree.heading("split",       text="Split")
    _tree.heading("sizes",       text="Sizes")

    _tree.column("firm",        width=110, minwidth=90)
    _tree.column("challenge",   width=140, minwidth=110)
    _tree.column("phases",      width=50,  minwidth=40,  anchor="center")
    _tree.column("target",      width=80,  minwidth=60,  anchor="center")
    _tree.column("daily_dd",    width=65,  minwidth=50,  anchor="center")
    _tree.column("max_dd",      width=65,  minwidth=50,  anchor="center")
    _tree.column("dd_type",     width=75,  minwidth=60,  anchor="center")
    _tree.column("min_days",    width=60,  minwidth=45,  anchor="center")
    _tree.column("consistency", width=80,  minwidth=60,  anchor="center")
    _tree.column("split",       width=55,  minwidth=45,  anchor="center")
    _tree.column("sizes",       width=120, minwidth=90)

    scrollbar_y = ttk.Scrollbar(tree_frame, orient="vertical",   command=_tree.yview)
    scrollbar_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=_tree.xview)
    _tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
    _tree.pack(side="left", fill="both", expand=True)
    scrollbar_y.pack(side="right", fill="y")

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
    global _all_rows
    _all_rows = []
    from shared.prop_firm_engine import load_all_firms
    firms = load_all_firms()
    for firm_id, firm in sorted(firms.items(), key=lambda x: x[1].firm_name):
        for ch_info in firm.list_challenges():
            ch = firm.get_challenge(ch_info["challenge_id"])
            if not ch:
                continue
            phases = ch.get("phases", [])
            funded = ch.get("funded", {})
            if phases:
                p0         = phases[0]
                daily_dd   = p0.get("max_daily_drawdown_pct")
                max_dd     = p0.get("max_total_drawdown_pct")
                dd_type    = p0.get("drawdown_type", "—")
                min_days   = p0.get("min_trading_days", 0)
                consistency = p0.get("consistency_rule_pct")
            else:
                daily_dd   = funded.get("max_daily_drawdown_pct")
                max_dd     = funded.get("max_total_drawdown_pct")
                dd_type    = funded.get("drawdown_type", "—")
                min_days   = 0
                consistency = funded.get("consistency_rule_pct")
            _all_rows.append((
                firm.firm_name,
                ch["challenge_name"],
                str(len(phases)),
                _format_targets(phases),
                f"{daily_dd}%" if daily_dd is not None else "None",
                f"{max_dd}%"   if max_dd   is not None else "—",
                dd_type.replace("_", " ").title(),
                str(min_days) if min_days else "None",
                f"{consistency}%" if consistency else "None",
                f"{funded.get('profit_split_pct', '?')}%",
                _format_sizes(ch.get("account_sizes", [])),
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
            _tree.insert("", "end", values=row)


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

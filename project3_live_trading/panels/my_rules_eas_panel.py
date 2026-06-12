"""My Rules & EAs — browse saved rules and view/generate their EA side by side."""
# CHANGED: June 2026 — new panel for browsing my_rules.json with EA generation
import os
import json
import glob
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

BG = "#f0f2f5"
WHITE = "white"
GREEN = "#2d8a4e"
DARK = "#1a1a2a"
MIDGREY = "#555566"
AMBER = "#996600"

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MYRULES = os.path.join(_ROOT, "my_rules.json")
_PROPDIR = os.path.join(_ROOT, "prop_firms")

# CHANGED: June 2026 — safe float coercer for stats fed into generate_ea
def _f(v, default=0.0):
    try:
        if v is None:
            return default
        if isinstance(v, str):
            v = v.strip().replace(',', '').replace('+', '').replace('−', '-')
        return float(v)
    except (TypeError, ValueError):
        return default


_tree = None
_rule_txt = None
_ea_txt = None
_rules_cache = []
_last_code_by_iid = {}


def _load_rules():
    try:
        with open(_MYRULES, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _load_firms():
    firms = {}
    for fp in sorted(glob.glob(os.path.join(_PROPDIR, "*.json"))):
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
            firms[d.get("firm_name", d.get("firm_id", os.path.basename(fp)))] = d
        except Exception:
            pass
    return firms


def _resolve_firm(entry):
    """Best-effort firm match from the rule's notes/source/fields."""
    firms = _load_firms()
    blob = json.dumps(entry).lower()
    for name, data in firms.items():
        if name.lower() in blob or str(data.get("firm_id", "")).lower() in blob:
            return name, data
    # fallback: first firm or None
    if firms:
        first_name = next(iter(firms))
        return first_name, firms[first_name]
    return None, None


def _fmt_rule(entry):
    r = entry.get("rule", {})
    firm_name, firm = _resolve_firm(entry)
    firm = firm or {}
    L = []
    A = L.append
    A("=" * 58)
    A("RULE")
    A("=" * 58)
    A("id            : " + str(entry.get("id")))
    A("rule_id       : " + str(entry.get("rule_id")))
    A("source        : " + str(entry.get("source")))
    A("saved_at      : " + str(entry.get("saved_at")))
    A("combo         : " + str(r.get("rule_combo")))
    A("direction     : " + str(r.get("direction")))
    A("entry_tf      : " + str(r.get("entry_tf") or r.get("entry_timeframe") or "?"))
    A("exit          : " + str(r.get("exit_name")))
    _ep = r.get("exit_params") or r.get("exit_strategy_params") or {}
    if _ep:
        A("exit params   :")
        for k, v in _ep.items():
            A("    " + str(k) + " = " + str(v))
    # CHANGED: June 2026 — surface the entry timing so it's clear whether the rule was
    #   backtested at signal bar (N) or next bar (N+1); the EA must match this.
    _ebo = r.get("entry_bar_offset", 0)
    try:
        _ebo = int(_ebo)
    except Exception:
        _ebo = 0
    _ebo_label = "N (signal bar)" if _ebo == 0 else "N+1 (next bar, EA parity)"
    A("entry timing  : offset=" + str(_ebo) + "  -> " + _ebo_label)
    A("filters       : " + str(r.get("filters_applied") or "(none)"))
    A("WR            : " + str(r.get("win_rate")))
    A("net pips      : " + str(r.get("net_total_pips") or r.get("total_pips")))
    A("PF            : " + str(r.get("net_profit_factor")))
    A("trades        : " + str(r.get("total_trades")))
    A("")
    A("-" * 58)
    A("BRANCHES")
    A("-" * 58)
    _branches = r.get("rules") or []
    _win = [b for b in _branches if b.get("prediction") == "WIN"] or _branches
    for i, b in enumerate(_win, 1):
        A("Branch " + str(i) + " (pred=" + str(b.get("prediction")) + ")")
        for c in b.get("conditions", []):
            A("    " + str(c.get("feature")) + " " +
              str(c.get("operator", c.get("op"))) + " " + str(c.get("value")))
    A("")
    A("=" * 58)
    A("PROP FIRM")
    A("=" * 58)
    A("firm          : " + str(firm_name))
    A("leverage      : " + str(firm.get("leverage")))
    A("force_close hr : " + str(firm.get("force_close_hour_gmt")))
    A("no-trades win  : [" + str(firm.get("no_trades_window_start_hour_gmt")) +
      "," + str(firm.get("no_trades_window_end_hour_gmt")) + ") GMT")
    _is = firm.get("instrument_specs", {})
    if _is:
        A("instrument_specs:")
        A(json.dumps(_is, indent=2, default=str))
    A("")
    A("=" * 58)
    A("RAW")
    A("=" * 58)
    A(json.dumps(entry, indent=2, default=str))
    return "\n".join(L)


def _gen_ea_for(entry):
    """Build strategy+firm and call generate_ea(). Returns code or error string."""
    from project3_live_trading.ea_generator import generate_ea
    # WHY: my_rules entries are wrapped as {"rule": {...}}, but batch entries from
    #   load_strategy_list() are FLAT (fields at top level, no "rule" wrapper). Support both.
    # CHANGED: June 2026 — accept flat load_strategy_list entries
    r = entry.get("rule")
    if not isinstance(r, dict) or not r:
        r = entry   # flat strategy dict from load_strategy_list
    # Normalize condition keys: load_strategy_list rows may store the entry conditions under
    # a different key than generate_ea expects ("rule_combo" or "rules").
    # CHANGED: June 2026 — normalize condition keys across entry shapes
    if not r.get("rule_combo"):
        r = dict(r)  # don't mutate the shared dict
        r["rule_combo"] = (r.get("combo") or r.get("rule_conditions") or
                           r.get("conditions"))
    firm_name, firm = _resolve_firm(entry)
    entry_tf = r.get("entry_tf") or r.get("entry_timeframe") or "H1"
    _fa = r.get("filters_applied") or {}
    hour_filter = None
    if isinstance(_fa.get("hours"), (list, tuple)) and len(_fa["hours"]) == 2:
        try:
            hour_filter = [int(_fa["hours"][0]), int(_fa["hours"][1])]
        except Exception:
            hour_filter = None
    strategy = {
        "rule_combo": r.get("rule_combo"),
        "direction": r.get("direction", "BUY"),
        "rules": r.get("rules", []),
        "exit_name": r.get("exit_name", "FixedSLTP"),
        "exit_strategy_params": r.get("exit_params") or r.get("exit_strategy_params") or {},
        "filters_applied": _fa,
        # WHY: flat load_strategy_list entries carry None for missing stats; ea_generator
        #   formats them with {:.1f}/{:.2f} which crashes on None. Coerce to float (0.0).
        #   Also resolve matrix field aliases (net_total_pips / net_profit_factor).
        # WHY win_rate: ea_generator multiplies by 100, so expects a fraction (0.47).
        #   If the row stores it as a percent (47.0), divide back down.
        # CHANGED: June 2026 — never pass None stats into generate_ea
        "stats": {
            "win_rate":      (_f(r.get("win_rate")) / 100.0
                              if _f(r.get("win_rate")) > 1.0
                              else _f(r.get("win_rate"))),
            "total_pips":    _f(r.get("total_pips") or r.get("net_total_pips")),
            "profit_factor": _f(r.get("profit_factor") or r.get("net_profit_factor")),
        },
        "regime_filter_conditions": r.get("regime_filter_conditions", []),
    }
    try:
        return generate_ea(
            strategy=strategy, platform="mt5", prop_firm=firm,
            stage="evaluation", entry_timeframe=entry_tf, symbol="XAUUSD",
            magic_number=12345,
            risk_per_trade_pct=float(r.get("risk_pct", 0.3) or 0.3),
            hour_filter=hour_filter,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        # CHANGED: June 2026 — non-empty error message so the panel shows the actual cause
        _msg = str(e) or repr(e) or type(e).__name__
        return "// EA generation failed:\n// " + _msg


def _on_select(_evt=None):
    if not _tree:
        return
    sel = _tree.selection()
    if not sel:
        return
    iid = sel[0]
    idx = int(iid)
    entry = _rules_cache[idx]
    _rule_txt.configure(state="normal")
    _rule_txt.delete("1.0", "end")
    _rule_txt.insert("end", _fmt_rule(entry))
    _rule_txt.configure(state="disabled")
    # EA: lazy — show hint until Generate pressed
    _ea_txt.configure(state="normal")
    _ea_txt.delete("1.0", "end")
    cached = _last_code_by_iid.get(iid)
    hint = "// Press 'Generate / Refresh EA' to build the EA for this rule."
    _ea_txt.insert("end", cached if cached else hint)
    _ea_txt.configure(state="disabled")


def _generate_selected():
    sel = _tree.selection() if _tree else None
    if not sel:
        messagebox.showinfo("Select a rule", "Pick a rule from the list first.")
        return
    iid = sel[0]
    code = _gen_ea_for(_rules_cache[int(iid)])
    _last_code_by_iid[iid] = code
    _ea_txt.configure(state="normal")
    _ea_txt.delete("1.0", "end")
    _ea_txt.insert("end", code)
    _ea_txt.configure(state="disabled")


def _copy_ea():
    code = _ea_txt.get("1.0", "end-1c") if _ea_txt else ""
    if code.strip():
        _ea_txt.clipboard_clear()
        _ea_txt.clipboard_append(code)
        messagebox.showinfo("Copied", "EA code copied.")


def _save_ea():
    code = _ea_txt.get("1.0", "end-1c") if _ea_txt else ""
    if not code.strip():
        messagebox.showinfo("Nothing", "Generate the EA first.")
        return
    path = filedialog.asksaveasfilename(
        title="Save EA", defaultextension=".mq5",
        filetypes=[("MQL5 files", "*.mq5"), ("All files", "*.*")])
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        messagebox.showinfo("Saved", "Saved to:" + "\n" + path)


def _populate():
    global _rules_cache
    _rules_cache = _load_rules()
    if not _tree:
        return
    for it in _tree.get_children():
        _tree.delete(it)
    for i, e in enumerate(_rules_cache):
        r = e.get("rule", {})
        _ebo_val = r.get("entry_bar_offset", 0)
        try:
            _ebo_val = int(_ebo_val)
        except Exception:
            _ebo_val = 0
        _tree.insert("", "end", iid=str(i), values=(
            e.get("id"),
            (r.get("rule_combo") or e.get("source") or "")[:40],
            r.get("entry_tf") or r.get("entry_timeframe") or "?",
            r.get("exit_name") or "?",
            "N" if _ebo_val == 0 else "N+1",   # CHANGED: June 2026 — show entry offset
            r.get("win_rate") or "",
            r.get("net_total_pips") or r.get("total_pips") or "",
            e.get("status", ""),
        ))


def build_panel(parent):
    global _tree, _rule_txt, _ea_txt
    panel = tk.Frame(parent, bg=BG)

    hdr = tk.Frame(panel, bg=DARK)
    hdr.pack(fill="x")
    tk.Label(hdr, text="My Rules & EAs", bg=DARK, fg="white",
             font=("Segoe UI", 13, "bold"), padx=12, pady=8).pack(side=tk.LEFT)
    tk.Button(hdr, text="\u21bb Reload", command=_populate, bg=MIDGREY, fg="white",
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3,
              font=("Segoe UI", 9, "bold")).pack(side=tk.RIGHT, padx=10, pady=6)

    body = tk.PanedWindow(panel, orient=tk.HORIZONTAL, bg=BG, sashwidth=6)
    body.pack(fill="both", expand=True, padx=6, pady=6)

    # LEFT: rule list
    left = tk.Frame(body, bg=WHITE)
    # CHANGED: June 2026 — added "Offset" column to show N vs N+1 entry timing
    cols = ("id", "combo", "tf", "exit", "offset", "wr", "net", "status")
    _tree = ttk.Treeview(left, columns=cols, show="headings", height=20)
    for c, t, w in [("id", "ID", 40), ("combo", "Rule", 240), ("tf", "TF", 50),
                    ("exit", "Exit", 110), ("offset", "Offset", 60),
                    ("wr", "WR", 60), ("net", "Net pips", 80),
                    ("status", "Status", 80)]:
        _tree.heading(c, text=t)
        _tree.column(c, width=w, anchor="w")
    _tree.bind("<<TreeviewSelect>>", _on_select)
    _tree.pack(side="left", fill="both", expand=True)
    _tsb = ttk.Scrollbar(left, orient="vertical", command=_tree.yview)
    _tree.configure(yscrollcommand=_tsb.set)
    _tsb.pack(side="right", fill="y")
    body.add(left, minsize=420)

    # RIGHT: rule detail (top) + EA (bottom)
    right = tk.PanedWindow(body, orient=tk.VERTICAL, bg=BG, sashwidth=6)
    rd = tk.Frame(right, bg=WHITE)
    tk.Label(rd, text="Rule detail", bg=WHITE, fg=DARK,
             font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
    _rule_txt = tk.Text(rd, font=("Consolas", 8), bg="#0f1020", fg="#d8e0ff", wrap="none")
    _rule_txt.pack(fill="both", expand=True, padx=8, pady=(0, 6))
    _rule_txt.configure(state="disabled")
    right.add(rd, minsize=200)

    ef = tk.Frame(right, bg=WHITE)
    ebar = tk.Frame(ef, bg=WHITE)
    ebar.pack(fill="x")
    tk.Label(ebar, text="Generated EA", bg=WHITE, fg=DARK,
             font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=8, pady=(6, 2))
    tk.Button(ebar, text="Generate / Refresh EA", command=_generate_selected,
              bg=GREEN, fg="white", relief=tk.FLAT, cursor="hand2", padx=10, pady=3,
              font=("Segoe UI", 9, "bold")).pack(side=tk.RIGHT, padx=4, pady=4)
    tk.Button(ebar, text="Save EA\u2026", command=_save_ea, bg=MIDGREY, fg="white",
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3,
              font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=4, pady=4)
    tk.Button(ebar, text="Copy EA", command=_copy_ea, bg=MIDGREY, fg="white",
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3,
              font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=4, pady=4)
    _ea_txt = tk.Text(ef, font=("Consolas", 8), bg="#1a1a2a", fg="#e0e0e0", wrap="none")
    _ea_txt.pack(fill="both", expand=True, padx=8, pady=(0, 6))
    _ea_txt.configure(state="disabled")
    right.add(ef, minsize=200)

    body.add(right, minsize=500)
    _populate()
    return panel


def refresh():
    _populate()

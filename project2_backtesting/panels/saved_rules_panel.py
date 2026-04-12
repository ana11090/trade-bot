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
        tk.Label(header, text=f"  from {entry.get('source', '?')}  •  {entry.get('saved_at', '?')[:10]}",
                 font=("Arial", 9), bg="#f8f9fa", fg="#888888").pack(side=tk.LEFT)

        # TF badge — show entry_tf if present on the rule
        # WHY: multi-TF backtest saves separate rules per TF; badge makes it visible
        # CHANGED: April 2026 — multi-TF support
        rule_tf = rule.get('entry_tf', '')
        if rule_tf:
            tk.Label(header, text=f"[{rule_tf}]", bg="#667eea", fg="white",
                     font=("Arial", 8, "bold"), padx=4, pady=1).pack(side=tk.LEFT, padx=(6, 0))

        rid = entry.get('id')
        tk.Button(header, text="🗑️", font=("Arial", 8),
                  bg="#dc3545", fg="white", relief=tk.FLAT, padx=4,
                  command=lambda r=rid: _delete_one(r, inner, canvas, window_id)).pack(side=tk.RIGHT)

        # Conditions
        wr = rule.get('win_rate', 0)
        pips = rule.get('avg_pips', 0)
        cov = rule.get('coverage', 0)

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

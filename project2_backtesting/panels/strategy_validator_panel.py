"""
Strategy Validator Panel — walk-forward validation + Monte Carlo robustness testing.

Proves whether a strategy has a real edge or is overfitting.
Results are saved to validation_results.json for use by the Prop Firm Test panel.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys
import threading
import json
import re
import re

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

GRADE_COLORS = {
    'A': "#28a745",
    'B': "#2d8a4e",
    'C': "#996600",
    'D': "#e67e00",
    'F': "#e94560",
}


class _Tooltip:
    """Scrollable hover tooltip for any Tkinter widget.
    Shows on hover, stays open while mouse is over the tooltip itself,
    and can be scrolled with mousewheel."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self._hide_id = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._schedule_hide)

    def _show(self, event=None):
        if self.tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)

        # Calculate text height to decide if scrolling is needed
        lines = self.text.count('\n') + 1
        max_height = 300  # max pixel height before scroll kicks in

        frame = tk.Frame(tw, bg="#333", padx=2, pady=2)
        frame.pack(fill="both", expand=True)

        if lines > 10:
            # Scrollable version for long content
            canvas = tk.Canvas(frame, bg="#333", highlightthickness=0,
                               width=440, height=max_height)
            scrollbar = tk.Scrollbar(frame, orient="vertical", command=canvas.yview)
            scroll_frame = tk.Frame(canvas, bg="#333")

            scroll_frame.bind("<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)

            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            # Mousewheel scrolling
            def _on_mousewheel(e):
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

            def _on_mousewheel_linux_up(e):
                canvas.yview_scroll(-3, "units")

            def _on_mousewheel_linux_down(e):
                canvas.yview_scroll(3, "units")

            canvas.bind("<MouseWheel>", _on_mousewheel)
            canvas.bind("<Button-4>", _on_mousewheel_linux_up)
            canvas.bind("<Button-5>", _on_mousewheel_linux_down)
            scroll_frame.bind("<MouseWheel>", _on_mousewheel)
            scroll_frame.bind("<Button-4>", _on_mousewheel_linux_up)
            scroll_frame.bind("<Button-5>", _on_mousewheel_linux_down)

            label = tk.Label(scroll_frame, text=self.text, justify="left",
                             bg="#333", fg="white", font=("Segoe UI", 9),
                             padx=10, pady=6, wraplength=420)
            label.pack(anchor="nw")
            label.bind("<MouseWheel>", _on_mousewheel)
            label.bind("<Button-4>", _on_mousewheel_linux_up)
            label.bind("<Button-5>", _on_mousewheel_linux_down)
        else:
            # Simple version for short content (no scrollbar needed)
            label = tk.Label(frame, text=self.text, justify="left",
                             bg="#333", fg="white", font=("Segoe UI", 9),
                             padx=10, pady=6, wraplength=420)
            label.pack()

        # Keep tooltip open when mouse moves over it
        tw.bind("<Enter>", self._cancel_hide)
        tw.bind("<Leave>", self._schedule_hide)

        # Position: try to keep on screen
        tw.update_idletasks()
        screen_w = tw.winfo_screenwidth()
        screen_h = tw.winfo_screenheight()
        tip_w = tw.winfo_reqwidth()
        tip_h = tw.winfo_reqheight()

        if x + tip_w > screen_w:
            x = screen_w - tip_w - 10
        if y + tip_h > screen_h:
            y = self.widget.winfo_rooty() - tip_h - 5

        tw.wm_geometry(f"+{x}+{y}")

    def _schedule_hide(self, event=None):
        """Hide after a short delay — gives time to move mouse to the tooltip."""
        if self._hide_id:
            self.widget.after_cancel(self._hide_id)
        self._hide_id = self.widget.after(300, self._hide)

    def _cancel_hide(self, event=None):
        """Cancel the scheduled hide — mouse entered the tooltip."""
        if self._hide_id:
            self.widget.after_cancel(self._hide_id)
            self._hide_id = None

    def _hide(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None
        self._hide_id = None


# ── Module-level state ────────────────────────────────────────────────────────
_batch_mode     = False
_strategy_var   = None
_strategies     = []
_tree           = None
_selected_count = None
_check_vars     = {}  # index -> bool (checkbox state)
_firm_name_to_id = {}  # firm display name -> firm_id (for Monte Carlo)
_current_run_settings = {}  # WHY: Store current strategy's run_settings for _show_estimation
                            # CHANGED: April 2026 — BUG 5 fix (pip_value from rule)


# Settings vars
_train_var          = None
_test_var           = None
_windows_var        = None
_recent_first_var   = None
_custom_windows_var = None
_sims_var           = None
_slip_levels_var    = None  # Phase 69 Fix 27: configurable slippage levels
_mc_firm_var        = None
_stage_var          = None


def _get_slippage_levels():
    """Return slippage levels list from UI entry, or defaults."""
    # WHY: Old code hardcoded [0,1,2,3,5]. Now reads from _slip_levels_var if set.
    # CHANGED: April 2026 — Phase 69 Fix 27
    default = [0, 1, 2, 3, 5]
    if _slip_levels_var is None:
        return default
    try:
        raw = _slip_levels_var.get().strip()
        if not raw:
            return default
        parsed = [int(x.strip()) for x in raw.split(',') if x.strip().isdigit()]
        return sorted(set(parsed)) if parsed else default
    except Exception:
        return default
_account_var        = None
_spread_var         = None
_comm_var           = None
_risk_var           = None
_sl_var             = None
_pipval_var         = None
_pip_size_var       = None

# Filter vars
_filt_wr        = None
_filt_pf        = None
_filt_trades    = None

# Widgets
_strat_info_lbl  = None
_strat_detail_lbl = None
_prev_result_lbl = None
_copy_results_btn = None
_last_validation_result = None  # stores the last completed validation result dict
_start_wf_btn    = None
_start_mc_btn    = None
_start_full_btn  = None
_start_slip_btn  = None
_stop_btn        = None
_status_lbl      = None
_progress_bar    = None
_scroll_canvas   = None
_wf_frame        = None
_mc_frame        = None
_slip_frame      = None
_live_firm_frame = None
_verdict_frame   = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Cache to prevent reloading 43MB file every time panel is shown
_strategies_cache = []
_cache_mtime = 0


def _copy_validation_results():
    """Format validation results as text and copy to clipboard."""
    global _last_validation_result
    if not _last_validation_result:
        import tkinter.messagebox as mb
        mb.showinfo("No Results", "Run a validation first.")
        return

    r = _last_validation_result
    lines = []
    lines.append("=" * 60)
    lines.append("STRATEGY VALIDATION RESULTS")
    lines.append(f"Validated: {r.get('validated_at', '?')[:19]}")
    lines.append(f"Strategy: {r.get('strategy_index', '?')}")
    lines.append("=" * 60)

    # ── Combined verdict ──
    combined = r.get('combined', {})
    if combined:
        lines.append(f"\nGRADE: {combined.get('grade', '?')} ({combined.get('confidence_score', 0)}/100)")
        lines.append(f"Verdict: {combined.get('verdict', '?')}")
        checks = combined.get('checks', {})
        if checks:
            lines.append("\nChecks:")
            for check_name, check_val in checks.items():
                if isinstance(check_val, dict):
                    status = check_val.get('status', '?')
                    detail = check_val.get('detail', '')
                    lines.append(f"  {check_name}: {status} — {detail}")
                else:
                    lines.append(f"  {check_name}: {check_val}")

    # ── Walk-Forward ──
    wf = r.get('walk_forward')
    if wf:
        lines.append("\n" + "-" * 40)
        lines.append("WALK-FORWARD RESULTS")
        summary = wf.get('summary', {})
        lines.append(f"Windows completed: {summary.get('windows_completed', 0)}")
        lines.append(f"Avg IN WR: {summary.get('avg_in_wr', 0)*100:.1f}%")
        lines.append(f"Avg OUT WR: {summary.get('avg_out_wr', 0)*100:.1f}%")
        lines.append(f"Avg IN PF: {summary.get('avg_in_pf', 0):.2f}")
        lines.append(f"Avg OUT PF: {summary.get('avg_out_pf', 0):.2f}")
        lines.append(f"Avg degradation: {summary.get('avg_degradation', 0):.1f}pp")
        lines.append(f"Verdict: {summary.get('verdict', '?')}")

        windows = wf.get('windows', [])
        if windows:
            lines.append(f"\nPer-window details ({len(windows)} windows):")
            for w in windows:
                label = w.get('label', '?')
                in_s = w.get('in_sample', {})
                out_s = w.get('out_sample', {})
                lines.append(f"  {label}")
                lines.append(f"    IN:  {in_s.get('count', 0):>5} trades  "
                             f"WR {in_s.get('win_rate', 0)*100:>5.1f}%  "
                             f"avg {in_s.get('avg_pips', 0):>+7.1f} pips  "
                             f"PF {in_s.get('profit_factor', 0):>5.2f}")
                lines.append(f"    OUT: {out_s.get('count', 0):>5} trades  "
                             f"WR {out_s.get('win_rate', 0)*100:>5.1f}%  "
                             f"avg {out_s.get('avg_pips', 0):>+7.1f} pips  "
                             f"PF {out_s.get('profit_factor', 0):>5.2f}")

    # ── Monte Carlo ──
    mc = r.get('monte_carlo')
    if mc:
        lines.append("\n" + "-" * 40)
        lines.append("MONTE CARLO RESULTS")
        lines.append(f"Simulations: {mc.get('n_simulations', 0)}")
        lines.append(f"Baseline pass rate: {mc.get('baseline_pass_rate', 0)*100:.1f}%")
        lines.append(f"Mean pass rate: {mc.get('mean_pass_rate', 0)*100:.1f}%")
        lines.append(f"P5 pass rate: {mc.get('p5_pass_rate', 0)*100:.1f}%")
        lines.append(f"P95 pass rate: {mc.get('p95_pass_rate', 0)*100:.1f}%")
        lines.append(f"Verdict: {mc.get('verdict', '?')}")

    # ── Slippage ──
    slip = r.get('slippage')
    if slip:
        lines.append("\n" + "-" * 40)
        lines.append("SLIPPAGE STRESS TEST")
        lines.append(f"Max safe slippage: {slip.get('max_safe_slippage', '?')} pips")
        lines.append(f"Breakeven slippage: {slip.get('breakeven_slippage', '?')} pips")
        lines.append(f"Verdict: {slip.get('verdict', '?')}")
        levels = slip.get('levels', [])
        if levels:
            lines.append("Levels:")
            for lv in levels:
                lines.append(f"  {lv.get('slippage_pips', '?')} pips: "
                             f"WR {lv.get('avg_wr', 0)*100:.1f}%  "
                             f"avg {lv.get('avg_pips', 0):+.1f} pips  "
                             f"PF {lv.get('avg_pf', 0):.2f}")

    # ── Live Firm ──
    # WHY: live_firm_results can be a list (normal), dict with _error (error),
    #      or None (not run). Handle all three cases.
    # CHANGED: April 2026 — handle list vs dict formats
    live = r.get('live_firm_results')
    if live:
        lines.append("\n" + "-" * 40)
        lines.append("LIVE FIRM SIMULATION")
        try:
            if isinstance(live, dict):
                if live.get('_error'):
                    lines.append(f"  Error: {live['_error']}")
                else:
                    for firm_id, firm_data in live.items():
                        if isinstance(firm_data, dict) and 'pass_rate' in firm_data:
                            lines.append(f"  {firm_id}: pass {firm_data.get('pass_rate', 0):.0f}%  "
                                         f"avg_days {firm_data.get('avg_days', 0):.0f}")
            elif isinstance(live, list):
                for item in live:
                    if isinstance(item, dict):
                        firm = item.get('firm_id', item.get('firm', '?'))
                        pr = item.get('pass_rate', item.get('eval_pass_rate', 0))
                        days = item.get('avg_days', 0)
                        lines.append(f"  {firm}: pass {float(pr)*100:.0f}%  avg_days {days:.0f}")
        except Exception as _e:
            lines.append(f"  (Could not format: {_e})")

    lines.append("\n" + "=" * 60)

    text = "\n".join(lines)

    # Copy to clipboard
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
    except Exception:
        # Fallback: use the existing window
        try:
            import shared.state as state
            state.window.clipboard_clear()
            state.window.clipboard_append(text)
        except Exception:
            pass

    import tkinter.messagebox as mb
    mb.showinfo("Copied", f"Validation results copied to clipboard!\n\n({len(lines)} lines)")


def _load_strategies():
    global _strategies, _strategies_cache, _cache_mtime
    try:
        backtest_path = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
        saved_path = os.path.join(project_root, 'saved_rules.json')

        # WHY: Old code only checked backtest_matrix.json mtime. If a new
        #      rule was saved, the cache wasn't invalidated — new rules
        #      didn't appear until app restart.
        # CHANGED: April 2026 — check both file mtimes
        _bt_mtime = os.path.getmtime(backtest_path) if os.path.exists(backtest_path) else 0
        _sr_mtime = os.path.getmtime(saved_path) if os.path.exists(saved_path) else 0
        _combined_mtime = _bt_mtime + _sr_mtime

        if _combined_mtime == _cache_mtime and _strategies_cache:
            _strategies = _strategies_cache
            return

        # File changed or no cache — reload
        from project2_backtesting.strategy_refiner import load_strategy_list
        _strategies = load_strategy_list()
        _strategies_cache = _strategies
        _cache_mtime = _combined_mtime
    except Exception as e:
        print(f"[validator_panel] {e}")
        _strategies = []


def _get_selected_index():
    """Get the first selected strategy index."""
    if not _strategies or _strategy_var is None:
        return None
    val = _strategy_var.get()
    if '───' in val:
        return None  # separator, not a real selection
    for s in _strategies:
        if s['label'] == val:
            return s['index']
    return None


def _get_all_selected_indices():
    """Get all checked strategy indices (from checkboxes).

    WHY: Treeview iids can be int (backtest results) or string ('saved_3', 'optimizer_latest').
         Old code forced int(), silently dropping non-int rows.
    CHANGED: April 2026 — preserve original type, exclude separators
    """
    global _check_vars
    if not _check_vars:
        return []

    checked = []
    for idx_key, is_checked in _check_vars.items():
        if is_checked:
            # Skip separator rows (not selectable strategies)
            if isinstance(idx_key, str) and idx_key.startswith('__separator_'):
                continue

            # Preserve original type: int for backtest results, str for saved/optimizer
            try:
                # Try int conversion for numeric strings
                checked.append(int(idx_key))
            except (ValueError, TypeError):
                # Keep as string for 'saved_3', 'optimizer_latest', etc.
                checked.append(idx_key)

    # Sort with int first, then str (for consistent ordering)
    return sorted(checked, key=lambda x: (isinstance(x, str), x))


def _strategy_for_iid(iid):
    """Look up strategy dict from _strategies by iid (type-safe).

    WHY: iid can be int (backtest results) or str ('saved_3', 'optimizer_latest').
         Matching must respect type or '3' != 3 will cause silent misses.
    CHANGED: April 2026 — type-safe lookup for checkbox bug fix
    """
    global _strategies
    for s in _strategies:
        s_idx = s.get('index')
        # Exact type+value match
        if s_idx == iid:
            return s
        # Fallback: if both convert to same int, accept (for robustness)
        try:
            if int(s_idx) == int(iid):
                return s
        except (ValueError, TypeError):
            pass
    return None


def _parse_exit_strategy(exit_name, exit_str):
    """Parse the human-readable exit_strategy string back into class + params.

    Returns (class_name, params_dict).
    """
    if not exit_name or not exit_str:
        return 'FixedSLTP', {'sl_pips': 150, 'tp_pips': 300}

    name = exit_name.lower().strip()
    s = exit_str.strip()

    if name == 'fixed sl/tp' or s.startswith('Fixed SL'):
        # "Fixed SL 150 pips / TP 300 pips"
        m = re.search(r'SL\s+(\d+).*TP\s+(\d+)', s)
        if m:
            return 'FixedSLTP', {'sl_pips': int(m.group(1)), 'tp_pips': int(m.group(2))}
        return 'FixedSLTP', {'sl_pips': 150, 'tp_pips': 300}

    elif name == 'trailing stop' or 'trail after' in s:
        # "SL 150 pips, trail after +100 pips, trail distance 150 pips"
        sl_m = re.search(r'SL\s+(\d+)', s)
        act_m = re.search(r'trail after \+(\d+)', s)
        dist_m = re.search(r'trail distance\s+(\d+)', s)
        return 'TrailingStop', {
            'sl_pips': int(sl_m.group(1)) if sl_m else 150,
            'activation_pips': int(act_m.group(1)) if act_m else 50,
            'trail_distance_pips': int(dist_m.group(1)) if dist_m else 100,
        }

    elif name == 'atr-based' or 'xATR' in s:
        # "SL 1.5xATR, TP 3.0xATR"
        sl_m = re.search(r'SL\s+([\d.]+)xATR', s)
        tp_m = re.search(r'TP\s+([\d.]+)xATR', s)
        return 'ATRBased', {
            'sl_atr_mult': float(sl_m.group(1)) if sl_m else 1.5,
            'tp_atr_mult': float(tp_m.group(1)) if tp_m else 3.0,
        }

    elif name == 'time-based' or 'close after' in s:
        # "SL 150 pips, close after 6 candles"
        sl_m = re.search(r'SL\s+(\d+)', s)
        candles_m = re.search(r'after\s+(\d+)\s+candles', s)
        return 'TimeBased', {
            'sl_pips': int(sl_m.group(1)) if sl_m else 150,
            'max_candles': int(candles_m.group(1)) if candles_m else 6,
        }

    elif name == 'indicator exit' or 'exit when' in s:
        # "SL 150 pips, exit when H1_rsi_14 above 70"
        sl_m = re.search(r'SL\s+(\d+)', s)
        ind_m = re.search(r'when\s+(\S+)\s+(above|below)\s+([\d.]+)', s)
        if ind_m:
            return 'IndicatorExit', {
                'sl_pips': int(sl_m.group(1)) if sl_m else 150,
                'exit_indicator': ind_m.group(1),
                'exit_threshold': float(ind_m.group(3)),
                'exit_direction': ind_m.group(2),
            }
        return 'IndicatorExit', {'sl_pips': 150, 'exit_indicator': 'H1_rsi_14',
                                  'exit_threshold': 70, 'exit_direction': 'above'}

    elif name == 'hybrid' or 'BE at' in s:
        # "SL 150, BE at +50, trail 100, max 12 candles"
        sl_m = re.search(r'SL\s+(\d+)', s)
        be_m = re.search(r'BE at \+(\d+)', s)
        trail_m = re.search(r'trail\s+(\d+)', s)
        candles_m = re.search(r'max\s+(\d+)\s+candles', s)
        return 'HybridExit', {
            'sl_pips': int(sl_m.group(1)) if sl_m else 150,
            'breakeven_activation_pips': int(be_m.group(1)) if be_m else 50,
            'trail_distance_pips': int(trail_m.group(1)) if trail_m else 100,
            'max_candles': int(candles_m.group(1)) if candles_m else 12,
        }

    # Fallback
    return 'FixedSLTP', {'sl_pips': 150, 'tp_pips': 300}


def _load_rules_from_report():
    """Load WIN-prediction rules from Project 1 analysis_report.json."""
    try:
        report_path = os.path.join(project_root,
            'project1_reverse_engineering', 'outputs', 'analysis_report.json')
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
        rules = report.get('rules', [])
        return [r for r in rules if r.get('prediction') == 'WIN']
    except Exception:
        return []


def _resolve_rules(r):
    """Get rules for a strategy result — from saved data or by parsing rule_combo name."""
    # 1) Check if rules are saved directly (future backtests will have this)
    saved_rules = r.get('rules', [])
    if saved_rules and len(saved_rules) > 0:
        # Verify they actually have conditions (not just metadata)
        if any(rule.get('conditions') for rule in saved_rules):
            print(f"[validator] _resolve_rules: found {len(saved_rules)} embedded rules with conditions")
            return saved_rules
        else:
            print(f"[validator] _resolve_rules: {len(saved_rules)} embedded rules but NONE have conditions")

    # 2) Fallback: load from analysis_report.json and use rule_combo name to select
    all_rules = _load_rules_from_report()
    if not all_rules:
        print(f"[validator] _resolve_rules: analysis_report.json has 0 rules!")
        return []
    print(f"[validator] _resolve_rules: analysis_report has {len(all_rules)} rules, "
          f"looking for combo '{r.get('rule_combo', '')}')")

    # Check for saved rule_indices first
    indices = r.get('rule_indices')
    if indices is not None:
        return [all_rules[i] for i in indices if i < len(all_rules)]

    # Parse the rule_combo name to figure out which rules were used
    combo_name = r.get('rule_combo', '')

    if not combo_name:
        print(f"[validator] _resolve_rules: empty combo_name, returning all {len(all_rules)} rules")
        return all_rules

    # WHY: Old code did exact match — 'All rules combined (BUY)' != 'All rules combined'.
    #      Use substring match so direction suffix doesn't break the lookup.
    # CHANGED: April 2026 — substring match for combo names
    if 'All rules combined' in combo_name:
        print(f"[validator] _resolve_rules: matched 'All rules combined' → returning {len(all_rules)} rules")
        return all_rules

    m = re.match(r'^Rule\s+(\d+)', combo_name)
    if m:
        idx = int(m.group(1)) - 1  # "Rule 1" → index 0
        if 0 <= idx < len(all_rules):
            print(f"[validator] _resolve_rules: matched Rule {idx+1} → 1 rule")
            return [all_rules[idx]]
        else:
            print(f"[validator] _resolve_rules: Rule {idx+1} out of range (have {len(all_rules)} rules)")

    m = re.match(r'^Top\s+(\d+)\s+rules', combo_name)
    if m:
        n = int(m.group(1))
        print(f"[validator] _resolve_rules: matched Top {n} rules → returning {min(n, len(all_rules))} rules")
        return all_rules[:n]

    # Handle "Rules 1+2+3 (BUY)" format
    m = re.match(r'^Rules?\s+([\d+]+)', combo_name)
    if m:
        _idx_str = m.group(1)
        _indices = [int(x) - 1 for x in _idx_str.split('+')]
        _picked = [all_rules[j] for j in _indices if 0 <= j < len(all_rules)]
        if _picked:
            print(f"[validator] _resolve_rules: matched Rules {_idx_str} → {len(_picked)} rules")
            return _picked

    # Unknown combo name — try all rules as fallback
    print(f"[validator] _resolve_rules: unknown combo '{combo_name}', returning all {len(all_rules)} rules as fallback")
    return all_rules


def _get_strategy_meta(idx):
    """Return (rules, exit_class, exit_params, trades, spread, commission, filters, direction) for strategy idx.

    Handles three index types:
      - int → row N of backtest_matrix.json's results array
      - 'saved_N' → look up saved rule N from saved_rules.json (which now
        embeds rule_combo + trades + exit_class + exit_params per Edit 1)
      - 'optimizer_latest' → look up the latest optimizer result

    WHY: Old code did data['results'][idx] unconditionally, which raised
         TypeError on string indices. The bare except returned an empty
         trades list silently — so the validator ran on [] and reported
         '0 trades validated' for any row that came from saved rules or
         the optimizer.
    CHANGED: April 2026 — handle saved + optimizer index types
    """
    # Default fallback for total failure
    # WHY: Added direction (8th element) so walk-forward tests the correct side.
    # CHANGED: April 2026 — direction in _get_strategy_meta
    _empty = ([], 'FixedSLTP', {'sl_pips': 150, 'tp_pips': 300}, [], 2.5, 0.0, None, 'BUY')

    try:
        # ── Saved rule branch ──────────────────────────────────────────────
        if isinstance(idx, str) and idx.startswith('saved_'):
            try:
                rule_id = int(idx.split('_', 1)[1])
            except (ValueError, IndexError):
                print(f"[validator] Bad saved rule id: {idx!r}")
                return _empty

            from shared.saved_rules import load_all
            for entry in load_all():
                if entry.get('id') != rule_id:
                    continue
                rule = entry.get('rule', {})
                # Reconstruct (rules, exit_class, exit_params, trades, ...)
                # from the saved fields. Edit 1 ensures rule_combo, trades,
                # exit_class, exit_params, and entry_timeframe are present
                # for newly-saved strategies. Older saves may be missing
                # them — fall back to whatever we can find.
                # WHY: Different save buttons use different keys:
                #   - Optimizer save → 'optimized_rules'
                #   - View Results save → 'rules'
                #   - Step 3/4 save → 'conditions' (flat list)
                #   Check all three in order. For 'conditions', wrap as
                #   a single rule (these are always from single-rule saves).
                # CHANGED: April 2026 — check all rule keys
                _rules = []

                # 1. optimized_rules (from optimizer save)
                _opt = rule.get('optimized_rules', [])
                if _opt and any(r.get('conditions') for r in _opt):
                    _rules = _opt
                    print(f"[validator] Loaded {len(_rules)} rules from 'optimized_rules'")

                # 2. rules (from View Results save / backtest matrix)
                if not _rules:
                    _saved = rule.get('rules', [])
                    if _saved and any(r.get('conditions') for r in _saved):
                        _rules = _saved
                        print(f"[validator] Loaded {len(_rules)} rules from 'rules'")

                # 3. conditions (from Step 3/4 single-rule save)
                if not _rules:
                    conds = rule.get('conditions', [])
                    if conds:
                        _rules = [{'prediction': 'WIN', 'conditions': conds}]
                        print(f"[validator] Wrapped {len(conds)} conditions as 1 rule")

                # 4. Last resort: load from analysis_report by rule_combo name
                if not _rules:
                    _combo = rule.get('rule_combo', '')
                    if _combo:
                        _resolved = _resolve_rules({
                            'rule_combo': _combo,
                            'rule_indices': rule.get('rule_indices'),
                        })
                        if _resolved:
                            _rules = _resolved
                            print(f"[validator] Loaded {len(_rules)} rules from analysis_report via '{_combo}'")

                if not _rules:
                    print(f"[validator] WARNING: saved rule has 0 rules! keys={sorted(rule.keys())}")
                _exit_class  = rule.get('exit_class', 'FixedSLTP')
                _exit_params = rule.get('exit_params', {'sl_pips': 150, 'tp_pips': 300})
                _trades      = rule.get('trades', [])
                _spread      = rule.get('spread_pips', 25.0)
                _comm        = rule.get('commission_pips', 0.0)
                if not _trades:
                    print(f"[validator] WARNING: saved rule {idx} has no trades — "
                          f"this is a stale save from before April 2026. "
                          f"Re-save it from the optimizer to pick up the new format.")
                _filters = rule.get('filters', rule.get('filters_applied', None))
                _direction = rule.get('direction', '')
                if not _direction:
                    # Infer from rule_combo name
                    _combo = rule.get('rule_combo', '')
                    if '(BUY)' in _combo: _direction = 'BUY'
                    elif '(SELL)' in _combo: _direction = 'SELL'
                if not _direction:
                    # Infer from rule action
                    for _r in _rules:
                        if _r.get('action') in ('BUY', 'SELL'):
                            _direction = _r['action']
                            break
                if not _direction:
                    _direction = 'BUY'

                # Embed regime filter from saved rule data
                _rf_from_rule = rule.get('regime_filter')
                _rf_from_settings = rule.get('run_settings', {}).get('regime_filter_conditions', [])
                _rf_enabled = rule.get('run_settings', {}).get('regime_filter_enabled', None)

                if _rf_from_rule is not None:
                    # Per-rule regime_filter already set (Phase A.43) — use as-is
                    pass
                elif _rf_enabled is False:
                    # Backtest had regime OFF → suppress
                    for _rule in _rules:
                        _rule['regime_filter'] = []
                    print(f"[validator] Saved rule: regime was OFF — suppressing global config fallback")
                elif _rf_from_settings:
                    for _rule in _rules:
                        _rule['regime_filter'] = _rf_from_settings
                    print(f"[validator] Saved rule: embedded {len(_rf_from_settings)} regime conditions")

                return _rules, _exit_class, _exit_params, _trades, _spread, _comm, _filters, _direction

            print(f"[validator] Saved rule {idx} not found in saved_rules.json")
            return _empty

        # ── Optimizer-latest branch ────────────────────────────────────────
        # WHY: Reads the file written by the ✅ Validate button on each
        #      optimizer result card in strategy_refiner_panel.py. Filename
        #      is _validator_optimized.json (NOT optimizer_latest.json — the
        #      previous fix used the wrong name). Shape is a single dict
        #      with top-level keys: rules, trades, name, exit_class,
        #      exit_params, exit_name.
        # CHANGED: April 2026 — corrected filename + shape
        if isinstance(idx, str) and idx == 'optimizer_latest':
            try:
                opt_path = os.path.join(project_root, 'project2_backtesting',
                                        'outputs', '_validator_optimized.json')
                if not os.path.exists(opt_path):
                    print(f"[validator] _validator_optimized.json not found at {opt_path}")
                    print(f"[validator] Click ✅ Validate on an optimizer card first.")
                    return _empty
                with open(opt_path, 'r', encoding='utf-8') as f:
                    opt = json.load(f)
                _rules       = opt.get('rules', [])
                _trades      = opt.get('trades', [])
                _exit_class  = opt.get('exit_class', 'FixedSLTP')
                _exit_params = opt.get('exit_params', {'sl_pips': 150, 'tp_pips': 300})
                # Spread / commission are not stored in _validator_optimized.json
                # — fall back to the standard defaults the optimizer used.
                _spread      = opt.get('spread_pips', 25.0)
                _comm        = opt.get('commission_pips', 0.0)
                if not _trades:
                    print(f"[validator] _validator_optimized.json exists but contains no trades. "
                          f"Re-click ✅ Validate on the optimizer card you want to test.")
                # WHY (Validator Fix): Read and return optimizer filters.
                # CHANGED: April 2026 — Validator Fix
                _filters = opt.get('filters', opt.get('filters_applied', None))
                _direction = opt.get('direction', 'BUY')

                # Embed regime filter from optimizer data
                _opt_rf = opt.get('regime_filter_conditions', [])
                _opt_rf_on = opt.get('run_settings', {}).get('regime_filter_enabled', None)
                if _opt_rf_on is False:
                    for _rule in _rules:
                        _rule['regime_filter'] = []
                    print(f"[validator] Optimizer: regime was OFF — suppressing global config fallback")
                elif _opt_rf:
                    for _rule in _rules:
                        _rule['regime_filter'] = _opt_rf
                    print(f"[validator] Optimizer: embedded {len(_opt_rf)} regime conditions")

                return _rules, _exit_class, _exit_params, _trades, _spread, _comm, _filters, _direction
            except Exception as e:
                import traceback; traceback.print_exc()
                print(f"[validator] Failed to load _validator_optimized.json: {e}")
                return _empty

        # ── Skip separator rows ────────────────────────────────────────────
        if isinstance(idx, str) and idx.startswith('__separator'):
            print(f"[validator] Cannot validate separator row: {idx!r}")
            return _empty

        # ── Integer index branch (backtest matrix row) ─────────────────────
        # Coerce digit strings to int (Tk Treeview iids are always strings,
        # so a backtest row's iid is e.g. '5' even though s['index'] is 5).
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
        if not isinstance(idx, int):
            print(f"[validator] Unrecognized index type {type(idx).__name__}: {idx!r}")
            return _empty

        backtest_path = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
        with open(backtest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        results = data.get('results', []) or data.get('matrix', [])
        if not (0 <= idx < len(results)):
            print(f"[validator] Index {idx} out of range (0..{len(results)-1})")
            return _empty
        r = results[idx]
        print(f"[validator] Matrix result[{idx}]: rule_combo={r.get('rule_combo','?')}, "
              f"exit_name={r.get('exit_name','?')}, "
              f"embedded_rules={len(r.get('rules',[]))}, "
              f"trade_count={r.get('trade_count', r.get('total_trades', '?'))}")

        rules = _resolve_rules(r)
        print(f"[validator] _resolve_rules returned {len(rules)} rules")
        if not rules:
            print(f"[validator] ⚠️ _resolve_rules found NOTHING. Attempting fallback...")
            # Fallback: load ALL rules from analysis_report
            _all = _load_rules_from_report()
            if _all:
                rules = _all
                print(f"[validator] Fallback loaded {len(rules)} rules from analysis_report.json")
            else:
                print(f"[validator] ❌ analysis_report.json also has 0 rules!")

        # ── Embed regime filter from run_settings into rules ──────────
        # WHY: run_backtest() checks per-rule 'regime_filter' key (Phase A.43).
        #      If missing, it falls back to GLOBAL config — which may have
        #      different settings than when the backtest ran. The backtest
        #      result carries run_settings.regime_filter_conditions showing
        #      what was ACTUALLY used. Embed it into each rule so the
        #      validator reproduces the exact same behavior.
        #
        #      Three cases:
        #      (a) run_settings says filter was OFF → set regime_filter=[]
        #          (explicitly suppresses global config fallback)
        #      (b) run_settings has conditions → set regime_filter=conditions
        #      (c) no run_settings at all (old result) → leave rules unchanged
        #          (falls back to global config — backward compat)
        # CHANGED: April 2026 — regime from strategy, not config
        _run_settings = r.get('run_settings', {})
        if _run_settings:
            _rf_enabled = _run_settings.get('regime_filter_enabled', False)
            _rf_conditions = _run_settings.get('regime_filter_conditions', [])
            if _rf_enabled and _rf_conditions:
                # Case (b): embed the actual conditions — FORCE override
                # WHY: Rules may already have regime_filter key from add_run_settings_metadata.
                #      Must override to ensure validator uses exact backtest conditions.
                # CHANGED: April 2026 — force override, don't check if key exists
                for _rule in rules:
                    _rule['regime_filter'] = _rf_conditions
                print(f"[validator] Embedded {len(_rf_conditions)} regime conditions from run_settings into {len(rules)} rules")
            elif not _rf_enabled:
                # Case (a): explicitly disable — FORCE override even if key exists.
                # WHY: add_run_settings_metadata may have embedded regime conditions
                #      into the rules during backtest save, even though the filter
                #      passed 0% of signals (effectively OFF). Force [] to suppress.
                # CHANGED: April 2026 — force override, don't check if key exists
                for _rule in rules:
                    _rule['regime_filter'] = []  # empty = OFF
                print(f"[validator] Regime filter was OFF during backtest — suppressing global config fallback")
            # Case (c): no run_settings → don't touch rules → backward compat

        # WHY (Hotfix): exit_class and exit_params can be empty in old
        #      backtest results. ALWAYS parse from exit_name/exit_strategy
        #      as primary source — these human-readable strings are always
        #      saved correctly. Only use stored exit_class/exit_params if
        #      they actually exist.
        # CHANGED: April 2026 — Hotfix
        exit_class = r.get('exit_class', '') or ''
        exit_params = r.get('exit_params') or r.get('exit_strategy_params')

        # Always try parsing the description — it's the most reliable source
        _parsed_class, _parsed_params = _parse_exit_strategy(
            r.get('exit_name', ''), r.get('exit_strategy', ''))

        # Use parsed values if stored ones are missing
        if not exit_class:
            exit_class = _parsed_class
        if not exit_params:
            exit_params = _parsed_params

        # Debug log so we can trace what the validator uses
        print(f"[validator] Strategy idx={idx}: "
              f"exit_class={exit_class!r}, "
              f"exit_name={r.get('exit_name', '')!r}, "
              f"exit_strategy={r.get('exit_strategy', '')!r}, "
              f"exit_params keys={list((exit_params or {}).keys())}")

        # WHY (Validator Fix): A.48 stripped trades from backtest_matrix.json
        #      to prevent OOM crashes. Load from per-TF trade files instead.
        # CHANGED: April 2026 — Validator Fix
        trades = r.get('trades', [])
        if not trades:
            # Try per-TF trade file
            try:
                _entry_tf = r.get('entry_tf', '')
                if _entry_tf:
                    _trades_path = os.path.join(
                        project_root, 'project2_backtesting', 'outputs',
                        f'backtest_trades_{_entry_tf}.json'
                    )
                    if os.path.exists(_trades_path):
                        import json as _tj
                        with open(_trades_path, 'r', encoding='utf-8') as _tf:
                            _all_trades = _tj.load(_tf)

                        # Find matching index: count results with this TF before idx
                        _tf_local_idx = 0
                        for _ri in range(idx):
                            if _ri < len(results) and results[_ri].get('entry_tf', '') == _entry_tf:
                                _tf_local_idx += 1
                        if str(_tf_local_idx) in _all_trades:
                            trades = _all_trades[str(_tf_local_idx)]
                            print(f"[validator] Loaded {len(trades)} trades from {_trades_path} (tf_idx={_tf_local_idx})")
                        else:
                            # Fallback: match by combo name + exit
                            _target_combo = r.get('rule_combo', '')
                            _target_exit = r.get('exit_strategy', r.get('exit_name', ''))
                            _tf_results = [rr for rr in results if rr.get('entry_tf', '') == _entry_tf]
                            for _ti, _tr in enumerate(_tf_results):
                                if (_tr.get('rule_combo', '') == _target_combo and
                                    (_tr.get('exit_strategy', '') == _target_exit or
                                     _tr.get('exit_name', '') == _target_exit)):
                                    if str(_ti) in _all_trades:
                                        trades = _all_trades[str(_ti)]
                                        print(f"[validator] Loaded {len(trades)} trades by name match from {_trades_path}")
                                    break
            except Exception as _te:
                print(f"[validator] Could not load trades from per-TF file: {_te}")
        spread     = r.get('spread_pips', 25.0)
        commission = r.get('commission_pips', 0.0)
        # WHY (Validator Fix): Include filters from the result row.
        #      Optimizer results store filters; backtest results don't.
        # CHANGED: April 2026 — Validator Fix
        filters = r.get('filters', r.get('filters_applied', None))
        _direction = r.get('direction', '')
        if not _direction:
            _combo = r.get('rule_combo', '')
            if '(BUY)' in _combo: _direction = 'BUY'
            elif '(SELL)' in _combo: _direction = 'SELL'
            else: _direction = 'BUY'
        return rules, exit_class, exit_params, trades, spread, commission, filters, _direction

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[validator] _get_strategy_meta({idx!r}) failed: {e}")
        return _empty


def _get_candles_path(entry_tf_hint=None):
    """Get candle CSV path for the entry TF the rules were discovered on.

    Priority:
      0. entry_tf_hint — per-strategy TF from a multi-TF backtest row (highest)
      1. backtest_matrix.json → entry_timeframe (what the backtest ran on)
      2. analysis_report.json → entry_timeframe (what P4 discovery used)
      3. Configuration → winning_scenario (user's current setting)
      4. Fallback → H1

    WHY: Rules found on M15 must be validated on M15 candles.
         With multi-TF backtest each row has its own entry_tf — use it first.
    CHANGED: April 2026 — multi-TF support (entry_tf_hint parameter)
    """
    # 0. Use per-row entry TF if provided (multi-TF backtest)
    entry_tf = entry_tf_hint or None

    # 1. Try backtest_matrix.json (only if no hint)
    if not entry_tf:
        try:
            matrix_path = os.path.join(project_root, 'project2_backtesting',
                                       'outputs', 'backtest_matrix.json')
            if os.path.exists(matrix_path):
                import json as _json
                with open(matrix_path, 'r') as f:
                    meta = _json.load(f)
                entry_tf = meta.get('entry_timeframe') or None
        except Exception:
            pass

    # 2. Try analysis_report.json
    if not entry_tf:
        try:
            report_path = os.path.join(project_root, 'project1_reverse_engineering',
                                       'outputs', 'analysis_report.json')
            if os.path.exists(report_path):
                import json as _json
                with open(report_path, 'r') as f:
                    report = _json.load(f)
                entry_tf = report.get('entry_timeframe') or None
        except Exception:
            pass

    # 3. Try Configuration
    symbol = None
    if not entry_tf:
        try:
            from project2_backtesting.panels.configuration import load_config
            cfg = load_config()
            entry_tf = cfg.get('winning_scenario') or None
            symbol = cfg.get('symbol') or None
        except Exception:
            pass
    # Also load symbol from config even if entry_tf was found
    if not symbol:
        try:
            from project2_backtesting.panels.configuration import load_config
            cfg = load_config()
            symbol = cfg.get('symbol') or None
        except Exception:
            pass

    # 4. Fallback
    if not entry_tf:
        entry_tf = 'H1'
        # WHY (Phase 33 Fix 11): Old code hardcoded 'xauusd'. Non-XAUUSD users
        #      got confusing "candle file not found" errors. Load from config;
        #      warn if falling back to XAUUSD so user knows to check config.
        # CHANGED: April 2026 — Phase 33 Fix 11 — symbol from config + fallback warnings
        #          (Ref: trade_bot_audit_round2_partC.pdf HIGH item #91 pg.31)
        print("[strategy_validator_panel] No entry_tf found — falling back to H1. Check config.")

    if not symbol:
        symbol = 'xauusd'
        print("[strategy_validator_panel] No symbol found in config — falling back to XAUUSD.")
    for p in [
        os.path.join(project_root, 'data', f'{symbol}_{entry_tf}.csv'),
        os.path.join(project_root, 'data', symbol, f'{entry_tf}.csv'),
        os.path.join(project_root, 'data', f'{symbol}_H1.csv'),  # last resort
    ]:
        if os.path.exists(p):
            return p
    return None


def _update_strat_info():
    global _strat_info_lbl, _prev_result_lbl, _check_vars
    if not _strat_info_lbl:
        return

    # Show info for last checked item (or first selected)
    # WHY: Same as _get_all_selected_indices fix — idx can be int or string
    # CHANGED: April 2026 — preserve original index type
    checked_indices = []
    for idx_key, is_checked in _check_vars.items():
        if is_checked:
            # Skip separators
            if isinstance(idx_key, str) and idx_key.startswith('__separator_'):
                continue
            # Preserve type: int for backtest, str for saved/optimizer
            try:
                checked_indices.append(int(idx_key))
            except (ValueError, TypeError):
                checked_indices.append(idx_key)
    idx = checked_indices[-1] if checked_indices else None

    if idx is None:
        _strat_info_lbl.configure(text="")
        return

    # WHY (Hotfix): Show complete strategy details so the user can verify
    #      exit strategy, entry TF, and filters BEFORE running validation.
    # CHANGED: April 2026 — Hotfix
    for s in _strategies:
        if s['index'] == idx:
            rule_name = s.get('rule_combo', '?')
            exit_name = s.get('exit_name', '?')

            # ── Extract detailed info from strategy or saved rule ──
            _exit_class = s.get('exit_class', '')
            _exit_params = s.get('exit_params', {})
            _exit_desc = s.get('exit_strategy', exit_name)
            _entry_tf = s.get('entry_tf', '')
            _filters = {}
            _has_trades = s.get('has_trades', False)
            _trade_count = s.get('total_trades', 0)

            # For saved rules, dig into the saved_rule dict
            _saved = s.get('saved_rule', {})
            if _saved:
                if not _exit_class:
                    _exit_class = _saved.get('exit_class', '')
                if not _exit_params:
                    _exit_params = _saved.get('exit_params', _saved.get('exit_strategy_params', {}))
                if not _exit_desc or _exit_desc == 'Default':
                    _en = _saved.get('exit_name', '')
                    _es = _saved.get('exit_strategy', '')
                    _exit_desc = _es if _es else (_en if _en else 'Default')
                if not _entry_tf:
                    _entry_tf = _saved.get('entry_timeframe', _saved.get('entry_tf', ''))
                _filters = _saved.get('filters_applied', {})
                if not _trade_count:
                    _trade_count = _saved.get('total_trades', 0)
                if _saved.get('trades'):
                    _has_trades = True

            # Build display lines
            line1 = f"{rule_name} × {_exit_desc}"
            if _entry_tf:
                line1 += f"  [{_entry_tf}]"
            line1 += f"  [{_trade_count} trades, WR {s['win_rate']:.1f}%, PF {s['net_profit_factor']:.2f}, {s['net_total_pips']:+,.0f} pips]"

            details = []
            if _exit_class:
                details.append(f"Exit class: {_exit_class}")
            if _exit_params:
                _params_str = ', '.join(f"{k}={v}" for k, v in _exit_params.items())
                details.append(f"Exit params: {_params_str}")
            if _filters:
                _filt_str = ', '.join(f"{k}={v}" for k, v in _filters.items())
                details.append(f"Filters: {_filt_str}")
            if not _has_trades and _trade_count > 0:
                details.append("⚠️ Trades in per-TF file (will load on validate)")
            elif not _has_trades and _trade_count == 0:
                details.append("⚠️ No trade data — re-run backtest")

            line2 = "  |  ".join(details) if details else ""

            _strat_info_lbl.configure(text=line1, fg=MIDGREY)

            # Show details on a second label (create if needed)
            global _strat_detail_lbl
            try:
                if _strat_detail_lbl and _strat_detail_lbl.winfo_exists():
                    if line2:
                        _strat_detail_lbl.configure(text=line2, fg="#888")
                    else:
                        _strat_detail_lbl.configure(text="")
            except Exception:
                pass
            break
    # Previous validation result
    if _prev_result_lbl:
        try:
            from project2_backtesting.strategy_validator import get_validation_for_strategy
            result = get_validation_for_strategy(idx)
            if result is None:
                _prev_result_lbl.configure(text="Not yet validated", fg=GREY)
            else:
                combined = result.get('combined', {})
                grade    = combined.get('grade', '?')
                score    = combined.get('confidence_score', 0)
                validated_at = result.get('validated_at', '')[:10]
                color = GRADE_COLORS.get(grade, GREY)
                _prev_result_lbl.configure(
                    text=f"Previously validated: Grade {grade} ({score}/100) on {validated_at}",
                    fg=color,
                )
        except Exception:
            _prev_result_lbl.configure(text="", fg=GREY)


def _set_buttons(running):
    for btn in (_start_wf_btn, _start_mc_btn, _start_full_btn, _start_slip_btn):
        if btn:
            btn.configure(state="disabled" if running else "normal")
    if _stop_btn:
        _stop_btn.configure(state="normal" if running else "disabled")


def _stop():
    try:
        from project2_backtesting.strategy_validator import stop_validation
        stop_validation()
    except Exception:
        pass
    if _status_lbl:
        _status_lbl.configure(text="Stopped by user", fg=AMBER)
    _set_buttons(False)


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

def _display_slip_results(slip_result):
    if _slip_frame is None:
        return
    for w in _slip_frame.winfo_children():
        w.destroy()

    tk.Label(_slip_frame, text="Slippage Stress Test",
             font=("Segoe UI", 11, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(8, 4))

    tk.Label(_slip_frame,
             text="Tests how your strategy holds up when trade execution is worse than expected.\n"
                  "Slippage = extra pips lost on each trade due to slow fills, requotes, or spread widening.\n"
                  "A robust strategy stays profitable even at 3-5 pips of slippage.",
             font=("Segoe UI", 8, "italic"), bg=BG, fg=GREY,
             wraplength=600, justify="left").pack(anchor="w", padx=5, pady=(0, 6))

    if not slip_result or slip_result.get('verdict') == 'INSUFFICIENT_DATA':
        msg = slip_result.get('error', 'Not run.') if slip_result else 'Not run.'
        tk.Label(_slip_frame, text=msg,
                 font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(anchor="w", padx=5)
        return

    levels   = slip_result.get('levels', [])
    max_safe = slip_result.get('max_safe_slippage', 0)
    be_slip  = slip_result.get('breakeven_slippage', '?')
    verdict  = slip_result.get('verdict', '?')

    verdict_colors = {'ROBUST': GREEN, 'MODERATE': AMBER, 'FRAGILE': RED, 'NO_EDGE': RED}
    verdict_icons  = {'ROBUST': '✅', 'MODERATE': '⚠️', 'FRAGILE': '❌', 'NO_EDGE': '❌'}

    # Table header
    hdr_frame = tk.Frame(_slip_frame, bg=WHITE, padx=12, pady=6)
    hdr_frame.pack(fill="x", padx=5, pady=(0, 2))
    tk.Label(hdr_frame,
             text=f"{'Slippage':>10}  {'Win Rate':>10}  {'Avg Pips':>10}  {'Total Pips':>11}  {'Status':>10}",
             font=("Consolas", 8, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")

    # Level rows
    table_frame = tk.Frame(_slip_frame, bg=WHITE, padx=12, pady=4)
    table_frame.pack(fill="x", padx=5, pady=(0, 4))

    # Get baseline (0 slippage) for comparison
    baseline_tp = levels[0]['total_pips'] if levels else 1

    for lvl in levels:
        sp     = lvl['slippage_pips']
        wr     = lvl['win_rate']
        ap     = lvl['avg_pips']
        tp     = lvl['total_pips']
        ok     = lvl['profitable']
        color  = GREEN if ok else RED
        status = "✅ profit" if ok else "❌ loss"

        # Show % change from baseline
        if baseline_tp != 0 and sp > 0:
            pct_change = (tp - baseline_tp) / abs(baseline_tp) * 100
            change_str = f"{pct_change:+.0f}%"
        else:
            change_str = "baseline"

        line = (f"  {sp:>5.1f} pips  │  WR {wr:>5.1f}%  │  avg {ap:>+7.1f}  │  "
                f"total {tp:>+8.0f}  │  {change_str:>8}  │  {status}")
        tk.Label(table_frame, text=line,
                 font=("Consolas", 8), bg=WHITE, fg=color).pack(anchor="w")

    # Summary card
    sum_card = tk.Frame(_slip_frame, bg="#1a1a2a", padx=14, pady=10)
    sum_card.pack(fill="x", padx=5, pady=(0, 4))
    tk.Label(sum_card,
             text=f"Max safe slippage: {max_safe} pips  |  Estimated breakeven: ~{be_slip} pips",
             font=("Consolas", 8), bg="#1a1a2a", fg="#aaaacc").pack(anchor="w")
    tk.Label(sum_card,
             text=f"Verdict: {verdict}  {verdict_icons.get(verdict, '')}",
             font=("Segoe UI", 9, "bold"),
             bg="#1a1a2a",
             fg=verdict_colors.get(verdict, WHITE)).pack(anchor="w", pady=(6, 0))


def _verdict_explanation(verdict):
    """Plain-language explanation of each live-firm verdict."""
    explanations = {
        'EXCELLENT': (
            "Zero blows AND 3+ payout cycles completed.\n\n"
            "Ideal result. The strategy survived the entire test period without "
            "ever breaching the firm's DD limits, AND completed multiple successful "
            "payout cycles. You should feel confident deploying this live."
        ),
        'GOOD': (
            "Zero blows AND 1-2 payout cycles completed.\n\n"
            "Solid result. Strategy works but the test period may be too short to "
            "show many cycles. Consider running on a longer date range to confirm."
        ),
        'ACCEPTABLE': (
            "1 blow AND at least 1 payout cycle completed.\n\n"
            "Risky but viable. The strategy lost an account once but recovered "
            "and made payouts. Only deploy if you can afford the occasional "
            "account replacement fee."
        ),
        'MARGINAL': (
            "Inconsistent — neither clearly profitable nor dangerous.\n\n"
            "Strategy doesn't show a clear edge on this firm. Either the rules "
            "don't suit this firm's setup, or the strategy needs refinement."
        ),
        'RISKY': (
            "Account blew but never completed a payout cycle.\n\n"
            "Worst kind of result — you lose money before ever making any. "
            "Do not deploy this strategy on this firm."
        ),
        'DANGEROUS': (
            "Account blew 3+ times during the test period.\n\n"
            "Strategy is fundamentally incompatible with this firm's rules. "
            "Either the DD limits are too tight or the strategy is too "
            "aggressive. Do not deploy."
        ),
        'INSUFFICIENT_DATA': (
            "Not enough trades or days to run a meaningful simulation.\n\n"
            "Try running the strategy on a longer historical period."
        ),
        'ERROR': (
            "Simulation crashed for this firm.\n\n"
            "Likely a missing field in the firm JSON. Check the console for details."
        ),
    }
    return explanations.get(verdict, "No explanation available.")


def _display_live_firm_results(live_firm_results):
    """Render the Live Firm Simulation section."""
    if _live_firm_frame is None:
        return
    for w in _live_firm_frame.winfo_children():
        w.destroy()

    tk.Label(_live_firm_frame, text="Live Firm Simulation",
             font=("Segoe UI", 11, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(8, 4))

    header_lbl = tk.Label(
        _live_firm_frame,
        text=(
            "Replays your strategy's trades day-by-day using each prop firm's EXACT rules\n"
            "(closed-balance trailing, lock-after-gain, post-payout lock). Hover for details."
        ),
        font=("Segoe UI", 8, "italic"), bg=BG, fg=GREY,
        wraplength=600, justify="left",
        cursor="question_arrow",
    )
    header_lbl.pack(anchor="w", padx=5, pady=(0, 6))
    _Tooltip(header_lbl,
        "LIVE FIRM SIMULATION — what this tests\n\n"
        "This test replays your strategy's trades through each prop firm's EXACT\n"
        "rules, day by day. Unlike the basic prop firm sim that uses generic\n"
        "percentage-based DD checks, this models:\n\n"
        "  • The firm's DD trailing method (closed-balance vs equity)\n"
        "  • Lock-after-gain rules (e.g. DD locks at +6% on Leveraged)\n"
        "  • Post-payout DD lock (some firms lock floor after first withdrawal)\n"
        "  • Payout cycles (default 14 days) with 80% withdrawal assumed\n\n"
        "FOR EACH FIRM you see:\n"
        "  Blow count — how many times the account would have died\n"
        "  Lock day — when the lock-after-gain fired (if at all)\n"
        "  Payout cycles — how many successful payout windows\n"
        "  Avg/cycle — typical dollars withdrawn per payout\n"
        "  Annual estimate — extrapolated yearly income from this firm\n\n"
        "VERDICTS:\n"
        "  EXCELLENT  — Zero blows, 3+ cycles completed\n"
        "  GOOD       — Zero blows, 1+ cycles\n"
        "  ACCEPTABLE — 1 blow max, 1+ cycles\n"
        "  MARGINAL   — Inconsistent results\n"
        "  RISKY      — Blows but no payouts\n"
        "  DANGEROUS  — 3+ blows in the test period"
    )

    if not live_firm_results:
        tk.Label(_live_firm_frame, text="Not run.",
                 font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(anchor="w", padx=5)
        return

    verdict_colors = {
        'EXCELLENT':        '#27ae60',
        'GOOD':             '#2ecc71',
        'ACCEPTABLE':       '#f39c12',
        'MARGINAL':         '#e67e22',
        'RISKY':            '#e74c3c',
        'DANGEROUS':        '#c0392b',
        'INSUFFICIENT_DATA': '#95a5a6',
        'ERROR':            '#95a5a6',
        'N/A':              '#95a5a6',
    }

    for firm_result in live_firm_results:
        firm_name = firm_result.get('firm_name', 'Unknown')
        verdict   = firm_result.get('verdict', 'N/A')
        color     = verdict_colors.get(verdict, GREY)

        firm_card = tk.Frame(_live_firm_frame, bg=WHITE, padx=12, pady=8,
                             highlightbackground=color, highlightthickness=2)
        firm_card.pack(fill="x", padx=5, pady=3)

        # Top row: firm name + verdict badge
        top_row = tk.Frame(firm_card, bg=WHITE)
        top_row.pack(fill="x")

        tk.Label(top_row, text=firm_name, font=("Segoe UI", 10, "bold"),
                 bg=WHITE, fg=DARK).pack(side="left")

        verdict_lbl = tk.Label(top_row, text=verdict,
                               font=("Segoe UI", 8, "bold"),
                               bg=color, fg="white", padx=8, pady=2,
                               cursor="question_arrow")
        verdict_lbl.pack(side="right")
        _Tooltip(verdict_lbl,
            f"Verdict: {verdict}\n\n" + _verdict_explanation(verdict))

        # Stats row
        blow_count = firm_result.get('blow_count', 0)
        cycles     = firm_result.get('payout_cycles_completed', 0)
        avg_cycle  = firm_result.get('avg_per_cycle', 0)
        annual     = firm_result.get('estimated_annual', 0)
        lock_day   = firm_result.get('lock_day')
        pd_days    = firm_result.get('payout_period_days', 14)

        stats_parts = [
            f"Blows: {blow_count}",
            f"Cycles: {cycles}",
            f"Avg/cycle: ${avg_cycle:,.0f}",
            f"Est. annual: ${annual:,.0f}",
        ]
        if lock_day is not None:
            stats_parts.append(f"DD locked: day {lock_day}")
        stats_text = "   |   ".join(stats_parts)

        stats_lbl = tk.Label(firm_card, text=stats_text,
                             font=("Segoe UI", 9), bg=WHITE, fg="#333",
                             cursor="question_arrow")
        stats_lbl.pack(anchor="w", pady=(4, 0))
        _Tooltip(stats_lbl,
            "BLOWS: How many times the account hit total DD breach.\n"
            "  Goal: 0. Each blow = a lost account + account fee.\n\n"
            f"PAYOUT CYCLES: Full {pd_days}-day periods completed with profit.\n"
            "  More cycles = more income. Aim for regular cycles.\n\n"
            "AVG/CYCLE: Average dollars withdrawn per payout window\n"
            "  (assumes withdrawing 80% of period profit).\n\n"
            "EST. ANNUAL: Extrapolated yearly income from this firm.\n"
            "  Formula: avg_per_cycle × cycles_per_year × (1 − blow_rate).\n\n"
            "DD LOCKED: Day the lock-after-gain rule fired.\n"
            "  Earlier = better. Once locked, DD floor stops trailing up."
        )

        # Warnings
        for warn_text in firm_result.get('warnings', []):
            tk.Label(firm_card, text=f"⚠  {warn_text}",
                     font=("Segoe UI", 8), bg=WHITE, fg=RED).pack(anchor="w", pady=(2, 0))


def _clear_results():
    for frame in (_wf_frame, _mc_frame, _slip_frame, _live_firm_frame, _verdict_frame):
        if frame:
            for w in frame.winfo_children():
                w.destroy()


def _display_wf_results(wf_result):
    if _wf_frame is None:
        return
    for w in _wf_frame.winfo_children():
        w.destroy()

    windows = wf_result.get('windows', [])
    if _recent_first_var and _recent_first_var.get():
        windows = list(reversed(windows))
    summary = wf_result.get('summary', {})

    tk.Label(_wf_frame, text="Walk-Forward Results",
             font=("Segoe UI", 11, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(8, 4))

    if not windows:
        tk.Label(_wf_frame, text="Insufficient data for walk-forward validation.",
                 font=("Segoe UI", 9, "italic"), bg=BG, fg=RED).pack(anchor="w", padx=5)
        return

    for w in windows:
        ins  = w['in_sample']
        outs = w['out_sample']
        deg  = w['degradation']
        in_err  = w.get('in_error')
        out_err = w.get('out_error')

        # 0-trade windows get grey/warning — NOT green
        if ins['count'] == 0 and outs['count'] == 0:
            border_color = "#999999"
            deg_color    = GREY
            check        = "⚪"
            deg_text     = "NO TRADES"
        elif outs['count'] == 0:
            border_color = "#996600"
            deg_color    = AMBER
            check        = "⚠️"
            deg_text     = "0 OOS trades"
        elif deg > -15:
            border_color = "#2d8a4e"
            deg_color    = GREEN
            check        = "✅"
            deg_text     = f"{deg:+.1f}%"
        elif deg < -25:
            border_color = "#e94560"
            deg_color    = RED
            check        = "❌"
            deg_text     = f"{deg:+.1f}%"
        else:
            border_color = "#996600"
            deg_color    = AMBER
            check        = "⚠️"
            deg_text     = f"{deg:+.1f}%"

        # Custom windows get a golden border
        if w.get('is_custom', False):
            border_color = "#DAA520"  # gold

        card = tk.Frame(_wf_frame, bg=WHITE,
                        highlightbackground=border_color, highlightthickness=2,
                        padx=12, pady=8)
        card.pack(fill="x", padx=5, pady=3)

        title_row = tk.Frame(card, bg=WHITE)
        title_row.pack(fill="x")
        tk.Label(title_row,
                 text=f"{w['label']}",
                 font=("Segoe UI", 10, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT)
        tk.Label(title_row,
                 text=f"  {deg_text}  {check}",
                 font=("Segoe UI", 9, "bold"), bg=WHITE, fg=deg_color).pack(side=tk.LEFT)

        tk.Label(card,
                 text=f"  IN:  {ins['count']:3d} trades  WR {ins['win_rate']*100:.1f}%  "
                      f"avg {ins['avg_pips']:+.0f} pips  PF {ins['profit_factor']:.2f}",
                 font=("Consolas", 8), bg=WHITE, fg=MIDGREY).pack(anchor="w")
        tk.Label(card,
                 text=f"  OUT: {outs['count']:3d} trades  WR {outs['win_rate']*100:.1f}%  "
                      f"avg {outs['avg_pips']:+.0f} pips  PF {outs['profit_factor']:.2f}",
                 font=("Consolas", 8), bg=WHITE, fg=DARK if outs['count'] > 0 else GREY).pack(anchor="w")

        # ── Side-by-side comparison table ─────────────────────────────────────
        if ins['count'] > 0 or outs['count'] > 0:
            cmp_frame = tk.Frame(card, bg="#f8f9fa", padx=6, pady=5)
            cmp_frame.pack(fill="x", pady=(4, 0))

            def _cmp_row(parent, icon, label, in_val, out_val,
                         warn_fn=None, bg_color="#f8f9fa"):
                """Add one comparison row: icon  label  IN_value  OUT_value.
                in_val and out_val should be PRE-FORMATTED strings."""
                row = tk.Frame(parent, bg=bg_color)
                row.pack(fill="x")
                tk.Label(row, text=f"  {icon} {label}",
                         font=("Consolas", 8), bg=bg_color, fg="#555",
                         width=24, anchor="w").pack(side=tk.LEFT)
                tk.Label(row, text=str(in_val),
                         font=("Consolas", 8), bg=bg_color, fg="#555",
                         width=16, anchor="e").pack(side=tk.LEFT)
                out_color = "#555"
                if warn_fn and out_val:
                    try:
                        out_color = warn_fn(out_val)
                    except:
                        pass
                tk.Label(row, text=str(out_val),
                         font=("Consolas", 8), bg=bg_color, fg=out_color,
                         width=16, anchor="e").pack(side=tk.LEFT)

            # Header
            hdr = tk.Frame(cmp_frame, bg="#e8ecf0")
            hdr.pack(fill="x")
            tk.Label(hdr, text="",
                     font=("Consolas", 8, "bold"), bg="#e8ecf0",
                     width=24, anchor="w").pack(side=tk.LEFT)
            tk.Label(hdr, text="IN-SAMPLE",
                     font=("Consolas", 8, "bold"), bg="#e8ecf0", fg="#667eea",
                     width=16, anchor="e").pack(side=tk.LEFT)
            tk.Label(hdr, text="OUT-OF-SAMPLE",
                     font=("Consolas", 8, "bold"), bg="#e8ecf0", fg="#2d8a4e",
                     width=16, anchor="e").pack(side=tk.LEFT)

            # ── Section: Drawdown ─────────────────────────────────────────────
            dd_warn = lambda v: "#e94560" if float(v.replace('%','')) >= 8 else (
                "#e67e00" if float(v.replace('%','')) >= 5 else "#2d8a4e")

            _cmp_row(cmp_frame, "📊", "Max daily DD",
                f"{ins.get('daily_dd_max_pct', 0):.1f}%",
                f"{outs.get('daily_dd_max_pct', 0):.1f}%",
                warn_fn=dd_warn)

            _cmp_row(cmp_frame, "📊", "Max total DD",
                f"{ins.get('total_dd_max_pct', 0):.1f}%",
                f"{outs.get('total_dd_max_pct', 0):.1f}%",
                warn_fn=dd_warn)

            _cmp_row(cmp_frame, "🔴", "DD touches (daily/total)",
                f"{ins.get('dd_daily_touches', 0)} / {ins.get('dd_total_touches', 0)}",
                f"{outs.get('dd_daily_touches', 0)} / {outs.get('dd_total_touches', 0)}")

            in_rec = "✅ yes" if ins.get('dd_recovered', True) else "❌ no"
            out_rec = "✅ yes" if outs.get('dd_recovered', True) else "❌ no"
            _cmp_row(cmp_frame, "🔄", "DD recovered", in_rec, out_rec)

            # ── Section: Daily trade frequency ────────────────────────────────
            _cmp_row(cmp_frame, "📅", "Trades/day avg",
                f"{ins.get('trades_per_day_avg', 0):.1f}",
                f"{outs.get('trades_per_day_avg', 0):.1f}")

            _cmp_row(cmp_frame, "📅", "Trades/day min",
                f"{ins.get('trades_per_day_min', 0)}",
                f"{outs.get('trades_per_day_min', 0)}")

            _cmp_row(cmp_frame, "📅", "Trades/day max",
                f"{ins.get('trades_per_day_max', 0)}",
                f"{outs.get('trades_per_day_max', 0)}")

            _cmp_row(cmp_frame, "📅", "Trading days",
                f"{ins.get('trading_days', 0)}",
                f"{outs.get('trading_days', 0)}")

            # ── Section: Monthly trade frequency ──────────────────────────────
            _cmp_row(cmp_frame, "📆", "Trades/month avg",
                f"{ins.get('trades_per_month_avg', 0):.1f}",
                f"{outs.get('trades_per_month_avg', 0):.1f}")

            _cmp_row(cmp_frame, "📆", "Trades/month min",
                f"{ins.get('trades_per_month_min', 0)}",
                f"{outs.get('trades_per_month_min', 0)}")

            _cmp_row(cmp_frame, "📆", "Trades/month max",
                f"{ins.get('trades_per_month_max', 0)}",
                f"{outs.get('trades_per_month_max', 0)}")

            _cmp_row(cmp_frame, "📆", "Trading months",
                f"{ins.get('trading_months', 0)}",
                f"{outs.get('trading_months', 0)}")

            # ── Section: Payout ───────────────────────────────────────────────
            _cmp_row(cmp_frame, "💰", "Payout 14d min",
                f"${ins.get('min_payout_14d', 0):,.0f}",
                f"${outs.get('min_payout_14d', 0):,.0f}")

            _cmp_row(cmp_frame, "💰", "Payout 14d max",
                f"${ins.get('max_payout_14d', 0):,.0f}",
                f"${outs.get('max_payout_14d', 0):,.0f}")

            # ── Section: Monthly profit ───────────────────────────────────────
            _cmp_row(cmp_frame, "📈", "Monthly profit avg",
                f"${ins.get('monthly_avg', 0):,.0f}",
                f"${outs.get('monthly_avg', 0):,.0f}")

            _cmp_row(cmp_frame, "📈", "Monthly profit best",
                f"${ins.get('monthly_best', 0):,.0f}",
                f"${outs.get('monthly_best', 0):,.0f}")

            _cmp_row(cmp_frame, "📈", "Monthly profit worst",
                f"${ins.get('monthly_worst', 0):,.0f}",
                f"${outs.get('monthly_worst', 0):,.0f}")

            _cmp_row(cmp_frame, "📈", "Months green / red",
                f"{ins.get('months_green', 0)} / {ins.get('months_red', 0)}",
                f"{outs.get('months_green', 0)} / {outs.get('months_red', 0)}")

        # ── Errors ────────────────────────────────────────────────────────────
        if in_err:
            tk.Label(card, text=f"  ⚠ IN error: {in_err}",
                     font=("Consolas", 8), bg=WHITE, fg=RED).pack(anchor="w")
        if out_err:
            tk.Label(card, text=f"  ⚠ OUT error: {out_err}",
                     font=("Consolas", 8), bg=WHITE, fg=RED).pack(anchor="w")

    # Summary
    verdict = summary.get('verdict', 'INSUFFICIENT_DATA')
    verdict_colors = {
        'LIKELY_REAL':       GREEN,
        'INCONCLUSIVE':      AMBER,
        'LIKELY_OVERFITTING': RED,
        'INSUFFICIENT_DATA': GREY,
    }
    sum_card = tk.Frame(_wf_frame, bg="#f0f8ff",
                        highlightbackground="#b0c4de", highlightthickness=1,
                        padx=12, pady=8)
    sum_card.pack(fill="x", padx=5, pady=(6, 2))
    tk.Label(sum_card, text="Summary", font=("Segoe UI", 9, "bold"),
             bg="#f0f8ff", fg=DARK).pack(anchor="w")
    tk.Label(sum_card,
             text=f"Avg out-of-sample WR: {summary.get('avg_out_wr',0)*100:.1f}%  |  "
                  f"Avg degradation: {summary.get('avg_degradation',0):+.1f}%  |  "
                  f"Edge held: {summary.get('edge_held_count',0)}/{summary.get('windows_completed',0)} windows",
             font=("Segoe UI", 9), bg="#f0f8ff", fg=MIDGREY).pack(anchor="w", pady=(2, 4))
    tk.Label(sum_card,
             text=f"Verdict: {verdict.replace('_', ' ')}",
             font=("Segoe UI", 10, "bold"),
             bg="#f0f8ff",
             fg=verdict_colors.get(verdict, GREY)).pack(anchor="w")

    # Show date coverage
    if windows:
        first_test = windows[0].get('test_start', '?')
        last_test = windows[-1].get('test_end', '?')
        tk.Label(sum_card,
            text=f"Coverage: {first_test} to {last_test}  |  {len(windows)} windows",
            font=("Segoe UI", 8), bg="#f0f8ff", fg=GREY).pack(anchor="w", pady=(2, 0))

        # Count how many windows had DD issues
        dd_warnings = sum(1 for w in windows
                         if w['out_sample'].get('total_dd_max_pct', 0) >= 8)
        if dd_warnings > 0:
            tk.Label(sum_card,
                text=f"⚠ {dd_warnings}/{len(windows)} windows had total DD ≥ 8%",
                font=("Segoe UI", 8, "bold"), bg="#f0f8ff", fg="#e94560").pack(anchor="w")
        no_recover = sum(1 for w in windows
                         if not w['out_sample'].get('dd_recovered', True)
                         and w['out_sample']['count'] > 0)
        if no_recover > 0:
            tk.Label(sum_card,
                text=f"⚠ {no_recover}/{len(windows)} windows did not recover from drawdown",
                font=("Segoe UI", 8, "bold"), bg="#f0f8ff", fg="#e94560").pack(anchor="w")


def _display_mc_results(mc_result):
    print(f"[validator] _display_mc_results called: "
          f"result={'None' if mc_result is None else mc_result.get('verdict', '?')}, "
          f"_mc_frame={'exists' if _mc_frame else 'None'}")
    if _mc_frame is None:
        return
    for w in _mc_frame.winfo_children():
        w.destroy()

    tk.Label(_mc_frame, text="Monte Carlo Robustness Test",
             font=("Segoe UI", 11, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(8, 4))

    if not mc_result or mc_result.get('verdict') == 'INSUFFICIENT_DATA':
        msg = mc_result.get('error', 'No Monte Carlo data.') if mc_result else 'Not run.'
        tk.Label(_mc_frame, text=msg,
                 font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY).pack(anchor="w", padx=5)
        return

    hist = mc_result.get('histogram', [])
    n_sims = mc_result.get('n_simulations', 0)
    firm_id_raw = mc_result.get('firm_id', '?')
    # Show the display name, not the internal ID
    firm_display = _mc_firm_var.get() if _mc_firm_var else firm_id_raw.upper()
    baseline = mc_result.get('baseline_pass_rate', 0) * 100
    mean_pr  = mc_result.get('mean_pass_rate', 0) * 100
    p5_pr    = mc_result.get('p5_pass_rate', 0) * 100
    p95_pr   = mc_result.get('p95_pass_rate', 0) * 100
    verdict  = mc_result.get('verdict', '?')

    header_card = tk.Frame(_mc_frame, bg=WHITE, padx=12, pady=8)
    header_card.pack(fill="x", padx=5, pady=(0, 4))
    tk.Label(header_card,
             text=f"Pass Rate Distribution ({n_sims} shuffles, {firm_display}):",
             font=("Segoe UI", 9, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")

    # Text histogram
    hist_frame = tk.Frame(_mc_frame, bg="#1a1a2a", padx=14, pady=10)
    hist_frame.pack(fill="x", padx=5, pady=(0, 4))

    if hist:
        max_pct = max(h['pct'] for h in hist) or 1
        for h in hist:
            bar_len = int(h['pct'] / max_pct * 28)
            bar_str = "█" * bar_len
            is_near_mean = abs(h['pct'] - max_pct) < 5
            color = "#88ddaa" if is_near_mean else "#5588aa"
            row = tk.Frame(hist_frame, bg="#1a1a2a")
            row.pack(anchor="w")
            tk.Label(row, text=f"{h['label']:>8}  |{bar_str:<28}  {h['pct']:5.1f}%",
                     font=("Consolas", 8), bg="#1a1a2a", fg=color).pack(anchor="w")

    tk.Label(hist_frame,
             text=f"\nOriginal: {baseline:.0f}%  |  Mean: {mean_pr:.0f}%  |  "
                  f"Worst (5th%): {p5_pr:.0f}%  |  Best (95th%): {p95_pr:.0f}%",
             font=("Consolas", 8), bg="#1a1a2a", fg="#aaaacc").pack(anchor="w", pady=(4, 0))

    verdict_colors = {'ROBUST': GREEN, 'MODERATE': AMBER, 'FRAGILE': RED}
    verdict_icons  = {'ROBUST': '✅', 'MODERATE': '⚠️', 'FRAGILE': '❌'}
    tk.Label(hist_frame,
             text=f"Verdict: {verdict}  {verdict_icons.get(verdict, '')}",
             font=("Segoe UI", 9, "bold"),
             bg="#1a1a2a",
             fg=verdict_colors.get(verdict, WHITE)).pack(anchor="w", pady=(6, 0))


def _show_estimation(trades, parent_frame):
    """Show payout or eval estimation after validation."""
    if not trades or len(trades) < 20:
        return

    try:
        global _stage_var, _mc_firm_var, _account_var, _scroll_canvas

        stage = _stage_var.get().lower() if _stage_var else "funded"
        acct = float(_account_var.get()) if _account_var else 100000
        firm_name = _mc_firm_var.get() if _mc_firm_var else ""
    except:
        stage = "funded"
        acct = 100000
        firm_name = ""

    # Read settings from the panel (not hardcoded)
    try:
        global _current_run_settings
        risk = float(_risk_var.get()) if _risk_var else 1.0
        # WHY: Read pip_value from strategy's run_settings first (single source of truth).
        # CHANGED: April 2026 — rule-driven pip_value (BUG 5 fix)
        _rule_pv = _current_run_settings.get('pip_value_per_lot', 0)
        if _rule_pv and float(_rule_pv) > 0:
            pip_value = float(_rule_pv)
        elif _pipval_var:
            pip_value = float(_pipval_var.get())
        else:
            pip_value = 1.0
        sl_pips = float(_sl_var.get()) if _sl_var else 150.0
    except:
        risk = 1.0
        pip_value = 1.0
        sl_pips = 150.0

    lot_size = (acct * risk / 100) / (sl_pips * pip_value)
    dollar_per_pip = pip_value * lot_size

    # ── Load firm data — find the right challenge by matching account size ─────
    firm_data = None
    profit_split = 80
    challenge_name = "?"
    profit_target_pct = 6.0
    total_dd_limit = 10.0
    daily_dd_limit = 5.0
    phase_name = "Evaluation"

    try:
        import glob
        prop_dir = os.path.join(project_root, 'prop_firms')
        for fp in glob.glob(os.path.join(prop_dir, '*.json')):
            with open(fp, encoding='utf-8') as f:
                fd = json.load(f)
            if fd.get('firm_name') == firm_name:
                firm_data = fd

                # Find the challenge that has this account size
                best_challenge = fd['challenges'][0]  # fallback to first
                for ch in fd.get('challenges', []):
                    sizes = ch.get('account_sizes', [])
                    if int(acct) in sizes:
                        best_challenge = ch
                        break

                challenge_name = best_challenge.get('challenge_name', '?')
                profit_split = best_challenge.get('funded', {}).get('profit_split_pct', 80)

                # Get phase data for evaluation
                phases = best_challenge.get('phases', [])
                if phases:
                    phase = phases[0]
                    phase_name = phase.get('phase_name', 'Evaluation')
                    profit_target_pct = phase.get('profit_target_pct', 6.0)
                    total_dd_limit = phase.get('max_total_drawdown_pct', 10.0)
                    daily_dd_limit = phase.get('max_daily_drawdown_pct', 5.0) or 5.0

                # For funded stage, get DD from funded section
                if stage == "funded":
                    funded = best_challenge.get('funded', {})
                    total_dd_limit = funded.get('max_total_drawdown_pct', total_dd_limit)

                break
    except:
        import traceback; traceback.print_exc()

    # ── Build daily PnL ───────────────────────────────────────────────────────
    import pandas as pd
    daily_pnls = {}
    for t in trades:
        try:
            day = str(pd.to_datetime(t.get('entry_time', '')).date())
            pnl = (t.get('net_pips', 0) or 0) * dollar_per_pip
            daily_pnls[day] = daily_pnls.get(day, 0) + pnl
        except:
            continue

    if not daily_pnls:
        return

    days_sorted = sorted(daily_pnls.keys())

    # ── Frame ─────────────────────────────────────────────────────────────────
    est_frame = tk.LabelFrame(parent_frame,
        text="💰 Payout Estimation" if stage == "funded" else "🎯 Eval Target Estimation",
        font=("Segoe UI", 10, "bold"), bg=WHITE, fg="#4a148c" if stage == "funded" else "#e65100",
        padx=10, pady=8)
    est_frame.pack(fill="x", padx=5, pady=(10, 5))

    # Show settings being used
    settings_lbl = tk.Label(est_frame,
        text=f"Firm: {firm_name or 'default'}  |  Challenge: {challenge_name}  |  "
             f"Account: ${acct:,.0f}  |  Risk: {risk}%  |  Lot: {lot_size:.2f}",
        bg=WHITE, fg="#999", font=("Segoe UI", 8))
    settings_lbl.pack(anchor="w", pady=(0, 2))
    _Tooltip(settings_lbl,
        "These settings determine lot size and dollar-per-pip.\n"
        "Change them in the Settings section above.\n"
        f"Lot size = (${acct:,.0f} × {risk}%) ÷ ({sl_pips:.0f} SL × ${pip_value} pip value) = {lot_size:.3f}")

    # Disclaimer
    tk.Label(est_frame,
        text="⚠ Based on in-sample backtest trades — validate with walk-forward first",
        bg=WHITE, fg=AMBER, font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(0, 4))

    # ══════════════════════════════════════════════════════════════════════════
    if stage == "funded":
        # ── FUNDED: Payout estimation ─────────────────────────────────────────
        consistency_limit = 20
        min_profit_days_req = 3
        min_day_threshold = acct * 0.005

        if firm_data:
            for rule in firm_data.get('trading_rules', []):
                if rule.get('type') == 'consistency':
                    consistency_limit = rule.get('parameters', {}).get('max_day_pct', 20)
                elif rule.get('type') == 'min_profitable_days':
                    min_profit_days_req = rule.get('parameters', {}).get('min_days', 3)

        rules_lbl = tk.Label(est_frame,
            text=f"📋 Funded rules: {profit_split}% profit split  |  "
                 f"consistency: best day ≤{consistency_limit}% of total  |  "
                 f"min {min_profit_days_req} profitable days  |  "
                 f"max DD: {total_dd_limit}%",
            bg=WHITE, fg="#555", font=("Segoe UI", 9))
        rules_lbl.pack(anchor="w", pady=(0, 6))
        _Tooltip(rules_lbl,
            f"Payout rules from {firm_name}:\n"
            f"• You keep {profit_split}% of profits\n"
            f"• No single day can be more than {consistency_limit}% of total profit\n"
            f"• Need at least {min_profit_days_req} profitable days per payout window\n"
            f"• Account blows if total DD reaches {total_dd_limit}%")

        windows_total = 0
        windows_pass = 0
        window_profits = []

        # WHY: Old loop used step 7 with a 14-day window, giving 50%
        #      overlap between consecutive windows. Every trading day
        #      was counted in ~2 windows, so avg/min/max payouts and
        #      the annual estimate (avg_p * 365/14) were systematically
        #      ~2x too high. Using step 14 makes windows disjoint so
        #      each day appears in exactly one window and the 365/14
        #      periods-per-year math becomes correct.
        # CHANGED: April 2026 — Phase 29 Fix 3 — non-overlapping 14-day
        #          windows (audit Part C crit #11)
        for start_i in range(0, len(days_sorted) - 5, 14):
            start_day = pd.to_datetime(days_sorted[start_i])
            window = {}
            for d in days_sorted[start_i:]:
                if (pd.to_datetime(d) - start_day).days >= 14:
                    break
                window[d] = daily_pnls[d]

            if not window:
                continue

            # WHY: Old code computed total_profit as sum of positive days
            #      only (gross), then best_day / total_profit gave the
            #      consistency ratio. Firms enforce consistency against
            #      NET total profit — using gross-positive makes the
            #      denominator larger and the ratio smaller, so the
            #      check was looser than what the firm actually measures.
            #      Same bug family as prop_firm_engine consistency check
            #      fixed in Phase 2. Use `net` (sum of all days) as the
            #      denominator, guarded for non-positive nets.
            # CHANGED: April 2026 — Phase 29 Fix 4 — net total in
            #          consistency denominator (audit Part C HIGH #80)
            net = sum(window.values())
            windows_total += 1

            if net <= 0:
                continue

            best_day  = max(window.values())
            best_pct  = best_day / net * 100   # consistency vs NET profit
            prof_days = sum(1 for v in window.values() if v >= min_day_threshold)

            if best_pct <= consistency_limit and prof_days >= min_profit_days_req and net > 0:
                windows_pass += 1
                window_profits.append(net * profit_split / 100)

        if windows_total > 0 and window_profits:
            pr = windows_pass / windows_total * 100
            avg_p = sum(window_profits) / len(window_profits)
            min_p = min(window_profits)
            max_p = max(window_profits)
            annual = avg_p * (365 / 14)

            pr_lbl = tk.Label(est_frame,
                text=f"✅ Pass rate: {pr:.0f}% of {windows_total} payout periods",
                bg=WHITE, fg="#2d8a4e" if pr >= 60 else "#e67e00",
                font=("Segoe UI", 10, "bold"))
            pr_lbl.pack(anchor="w")
            _Tooltip(pr_lbl,
                f"We simulate {windows_total} consecutive 14-day payout windows.\n"
                f"{windows_pass} of them pass all the rules (consistency, min days, positive P&L).\n"
                f"Pass rate = {windows_pass}/{windows_total} = {pr:.0f}%")

            pay_lbl = tk.Label(est_frame,
                text=f"💰 Payout per period: min ${min_p:,.0f}  |  avg ${avg_p:,.0f}  |  max ${max_p:,.0f}",
                bg=WHITE, fg="#333", font=("Segoe UI", 9))
            pay_lbl.pack(anchor="w", pady=(2, 0))
            _Tooltip(pay_lbl,
                f"From the {len(window_profits)} passing windows:\n"
                f"• Smallest payout: ${min_p:,.0f}\n"
                f"• Average payout: ${avg_p:,.0f}\n"
                f"• Largest payout: ${max_p:,.0f}\n"
                f"Payouts = net profit × {profit_split}% split")

            ann_lbl = tk.Label(est_frame,
                text=f"📅 Annual estimate: ${annual:,.0f}  ({365//14} periods × ${avg_p:,.0f} avg)",
                bg=WHITE, fg="#666", font=("Segoe UI", 9))
            ann_lbl.pack(anchor="w", pady=(2, 0))
            _Tooltip(ann_lbl,
                f"If every 14-day period pays the average (${avg_p:,.0f}):\n"
                f"~{365//14} periods per year × ${avg_p:,.0f} = ${annual:,.0f}/year\n"
                f"This is optimistic — only {pr:.0f}% of periods pass the rules.")
        else:
            tk.Label(est_frame,
                text=f"0% of payout periods pass — strategy won't generate payouts under current rules",
                bg=WHITE, fg="#dc3545", font=("Segoe UI", 10)).pack(anchor="w")

    else:
        # ── EVALUATION: Days to reach profit target ───────────────────────────
        target_dollars = acct * (profit_target_pct / 100)
        total_dd_dollars = acct * (total_dd_limit / 100)
        daily_dd_dollars = acct * (daily_dd_limit / 100)

        rules_lbl = tk.Label(est_frame,
            text=f"📋 {phase_name}: make {profit_target_pct}% (${target_dollars:,.0f}) profit  "
                 f"without losing {daily_dd_limit}% in a day (${daily_dd_dollars:,.0f}) "
                 f"or {total_dd_limit}% total (${total_dd_dollars:,.0f})",
            bg=WHITE, fg="#555", font=("Segoe UI", 9), wraplength=600, justify="left")
        rules_lbl.pack(anchor="w", pady=(0, 6))
        _Tooltip(rules_lbl,
            f"Evaluation rules from {firm_name} — {challenge_name}:\n"
            f"• Profit target: {profit_target_pct}% of ${acct:,.0f} = ${target_dollars:,.0f}\n"
            f"• Max daily drawdown: {daily_dd_limit}% = ${daily_dd_dollars:,.0f} loss in one day\n"
            f"• Max total drawdown: {total_dd_limit}% = ${total_dd_dollars:,.0f} total loss from peak\n"
            f"You pass by reaching the target before hitting either DD limit.")

        # Simulate attempts
        days_to_target = []
        blown_daily = 0
        blown_total = 0
        total_attempts = 0

        for start_i in range(0, len(days_sorted) - 5, 7):
            running = 0
            # WHY: Track HWM so total-DD can be measured from peak equity,
            #      not from the start-of-attempt zero. Old code only
            #      triggered total-DD when the cumulative P&L was negative
            #      by the full limit — a strategy that rose to +$8k then
            #      dropped to +$3k had running=+3000 and was never caught,
            #      even though it lost $5k from peak (a real DD breach on
            #      most firms). Reset peak_running to 0 at each attempt
            #      start so peak tracking is per-attempt.
            # CHANGED: April 2026 — Phase 29 Fix 5 — HWM-based total DD
            #          (audit Part C crit #12)
            peak_running = 0.0
            day_count = 0
            total_attempts += 1

            for d in days_sorted[start_i:]:
                day_pnl = daily_pnls[d]
                running += day_pnl
                day_count += 1

                # Track peak for HWM-based DD
                if running > peak_running:
                    peak_running = running

                if running >= target_dollars:
                    days_to_target.append(day_count)
                    break

                # Daily DD: single day loss exceeds limit
                if day_pnl < 0 and abs(day_pnl) >= daily_dd_dollars:
                    blown_daily += 1
                    break

                # Total DD: drawdown from peak exceeds limit (HWM-based).
                # Old check only fired when running was negative, missing
                # losses that stayed above zero after a winning phase.
                drawdown_from_peak = peak_running - running
                if drawdown_from_peak >= total_dd_dollars:
                    blown_total += 1
                    break

        if total_attempts == 0:
            tk.Label(est_frame,
                text="Not enough data to simulate evaluation attempts",
                bg=WHITE, fg="#dc3545", font=("Segoe UI", 10)).pack(anchor="w")
        elif days_to_target:
            pass_rate = len(days_to_target) / total_attempts * 100
            avg_d = sum(days_to_target) / len(days_to_target)
            total_blown = blown_daily + blown_total
            blow_rate = total_blown / total_attempts * 100

            # Pass rate
            pr_lbl = tk.Label(est_frame,
                text=f"✅ Pass rate: {pass_rate:.0f}% of {total_attempts} simulated attempts",
                bg=WHITE, fg="#2d8a4e" if pass_rate >= 80 else "#e67e00",
                font=("Segoe UI", 10, "bold"))
            pr_lbl.pack(anchor="w")
            _Tooltip(pr_lbl,
                f"We start a simulated evaluation at {total_attempts} different points\n"
                f"in your backtest data (every 7 trading days).\n\n"
                f"Each attempt trades until it either:\n"
                f"  ✅ Reaches ${target_dollars:,.0f} profit ({profit_target_pct}%)\n"
                f"  💥 Hits the daily DD limit (${daily_dd_dollars:,.0f})\n"
                f"  💥 Hits the total DD limit (${total_dd_dollars:,.0f})\n\n"
                f"Result: {len(days_to_target)} passed, {total_blown} blown = {pass_rate:.0f}% pass rate")

            # Days to target
            days_lbl = tk.Label(est_frame,
                text=f"📅 Trading days to reach ${target_dollars:,.0f} target: "
                     f"avg {avg_d:.0f}  |  fastest {min(days_to_target)}  |  slowest {max(days_to_target)}",
                bg=WHITE, fg="#333", font=("Segoe UI", 9))
            days_lbl.pack(anchor="w", pady=(2, 0))
            _Tooltip(days_lbl,
                f"Of the {len(days_to_target)} successful attempts:\n"
                f"• Average: {avg_d:.0f} trading days to make ${target_dollars:,.0f}\n"
                f"• Fastest: reached target in just {min(days_to_target)} trading day(s)\n"
                f"• Slowest: took {max(days_to_target)} trading days\n\n"
                f"These are TRADING days (when the bot trades), not calendar days.")

            # Blow details
            if total_blown > 0:
                blow_lbl = tk.Label(est_frame,
                    text=f"💥 Blow rate: {blow_rate:.0f}%  —  "
                         f"{blown_daily} from daily DD (≥{daily_dd_limit}%)  |  "
                         f"{blown_total} from total DD (≥{total_dd_limit}%)",
                    bg=WHITE, fg="#e94560" if blow_rate > 10 else "#e67e00",
                    font=("Segoe UI", 9))
                blow_lbl.pack(anchor="w", pady=(2, 0))
                _Tooltip(blow_lbl,
                    f"Out of {total_attempts} attempts, {total_blown} failed:\n"
                    f"• {blown_daily} blew the daily DD limit ({daily_dd_limit}% = ${daily_dd_dollars:,.0f})\n"
                    f"  → a single trading day lost more than ${daily_dd_dollars:,.0f}\n"
                    f"• {blown_total} blew the total DD limit ({total_dd_limit}% = ${total_dd_dollars:,.0f})\n"
                    f"  → cumulative losses exceeded ${total_dd_dollars:,.0f}")
            else:
                blow_lbl = tk.Label(est_frame,
                    text=f"💥 Blow rate: 0% — no attempts hit any DD limit",
                    bg=WHITE, fg="#2d8a4e", font=("Segoe UI", 9))
                blow_lbl.pack(anchor="w", pady=(2, 0))
                _Tooltip(blow_lbl,
                    f"None of the {total_attempts} simulated attempts hit the drawdown limits.\n"
                    f"Daily limit: {daily_dd_limit}% (${daily_dd_dollars:,.0f})\n"
                    f"Total limit: {total_dd_limit}% (${total_dd_dollars:,.0f})")

            # Expected attempts
            if pass_rate < 100:
                expected = 100 / max(pass_rate, 1)
                att_lbl = tk.Label(est_frame,
                    text=f"🔄 Expected attempts to pass: {expected:.1f}",
                    bg=WHITE, fg="#666", font=("Segoe UI", 8))
                att_lbl.pack(anchor="w", pady=(2, 0))
                _Tooltip(att_lbl,
                    f"With a {pass_rate:.0f}% pass rate, on average you'd need\n"
                    f"{expected:.1f} attempts before passing.\n\n"
                    f"If you fail (hit a DD limit), you restart the evaluation\n"
                    f"from scratch with a fresh ${acct:,.0f} account.")
        else:
            blow_rate = (blown_daily + blown_total) / max(total_attempts, 1) * 100
            tk.Label(est_frame,
                text=f"❌ 0% pass rate — never reaches {profit_target_pct}% target "
                     f"(${target_dollars:,.0f})",
                bg=WHITE, fg="#dc3545", font=("Segoe UI", 10, "bold")).pack(anchor="w")
            if blown_daily > 0 or blown_total > 0:
                tk.Label(est_frame,
                    text=f"💥 {blown_daily} daily DD blows + {blown_total} total DD blows "
                         f"out of {total_attempts} attempts",
                    bg=WHITE, fg="#e94560", font=("Segoe UI", 9)).pack(anchor="w")

    # Update scroll region
    if _scroll_canvas:
        try:
            parent_frame.update_idletasks()
            _scroll_canvas.configure(scrollregion=_scroll_canvas.bbox("all"))
        except:
            pass


def _display_verdict(combined, trades=None):
    if _verdict_frame is None:
        return
    for w in _verdict_frame.winfo_children():
        w.destroy()

    score = combined.get('confidence_score', 0)
    grade = combined.get('grade', '?')
    rec   = combined.get('recommendation', '')
    warns = combined.get('warnings', [])
    verdicts = combined.get('verdicts', {})
    grade_color = GRADE_COLORS.get(grade, GREY)

    # ── Real-World Expectations Summary ───────────────────────────────────────
    # WHY: Walk-forward + MC + slippage tested separately gives 3 numbers to
    #      interpret. This card combines them into ONE "expected real-world
    #      performance" estimate so the user sees what to actually expect live.
    # CHANGED: April 2026 — combined summary
    if trades and len(trades) >= 20:
        # Compute in-sample stats from trades
        # WHY (Phase 68 Fix 22): Old code preferred gross 'pips', falling back to
        #      'net_pips'. This meant PF was computed on gross pips but all other
        #      metrics were net — inconsistent cost basis. Prefer 'net_pips' so
        #      PF and DD share the same denominator. Fall back to gross 'pips'
        #      only when net is absent.
        # CHANGED: April 2026 — Phase 68 Fix 22 — net_pips preferred
        #          (audit Part E HIGH #22)
        pips_vals  = [float(t.get('net_pips', t.get('pips', 0))) for t in trades]
        wins       = [p for p in pips_vals if p > 0]
        losses     = [p for p in pips_vals if p <= 0]
        in_pips    = int(sum(pips_vals))
        in_wr      = round(len(wins) / max(len(pips_vals), 1) * 100, 1)
        gross_win  = sum(wins)
        # WHY (Phase 68 Fix 23): `abs(sum(losses)) or 1` meant a strategy
        #      with zero losses got gross_loss=1, so in_pf = gross_win pips
        #      (e.g. 85.0). Users saw PF=85.00 and thought it meant a very
        #      good but finite edge; it actually means no losses at all.
        #      Use 99.99 sentinel (consistent with compute_stats convention).
        # CHANGED: April 2026 — Phase 68 Fix 23 — PF=99.99 for no-loss
        #          (audit Part E HIGH #23)
        gross_loss = abs(sum(losses))
        in_pf      = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.99
        # Max drawdown: largest cumulative dip from peak
        equity_curve = []
        running = 0
        for p in pips_vals:
            running += p
            equity_curve.append(running)
        peak = 0
        max_dd_pips = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd_pips:
                max_dd_pips = dd
        in_dd = int(max_dd_pips)

        # WHY (Phase 65 Fix 1+2): OLD code computed realism_factor =
        #      max(0.30, weighted_sum) and projected in-sample stats
        #      through it as if it were a calibrated forecast. It was not:
        #      the 0.40/0.30/0.30 weights were invented; the mc_pass_rate
        #      input ignored the real MC pass rate and used hardcoded
        #      label-to-number constants (ROBUST=0.80, etc.); and the
        #      max(0.30,...) floor meant a strategy with 100% edge collapse
        #      still displayed "Real PF: 0.55" — catastrophically misleading
        #      for funding decisions.
        #
        #      NEW approach: show the ACTUAL, RAW validation numbers the
        #      tests computed. No synthetic "realistic forecast". Explicitly
        #      refuse to show any projected numbers when edge has collapsed.
        # CHANGED: April 2026 — Phase 65 Fix 1+2 — honest degradation display
        #          (audit Part E CRITICAL #18, #19)
        wf_degrad = combined.get('avg_degradation', 0) / 100  # e.g. -0.15
        edge_held = combined.get('edge_held_ratio', 0.5)       # 0.0–1.0
        mc_v      = verdicts.get('monte_carlo', 'N/A')

        # Classify degradation honestly — no floor
        if wf_degrad <= -0.50:
            degrad_label = "EDGE COLLAPSED"
            degrad_color = RED
        elif wf_degrad <= -0.20:
            degrad_label = f"HEAVY DEGRADATION ({wf_degrad*100:+.0f}%)"
            degrad_color = RED
        elif wf_degrad <= -0.05:
            degrad_label = f"MODERATE DEGRADATION ({wf_degrad*100:+.0f}%)"
            degrad_color = AMBER
        elif wf_degrad >= 0:
            degrad_label = f"STABLE / IMPROVING ({wf_degrad*100:+.0f}%)"
            degrad_color = GREEN
        else:
            degrad_label = f"MINOR DEGRADATION ({wf_degrad*100:+.0f}%)"
            degrad_color = AMBER

        summary_card = tk.Frame(_verdict_frame, bg="#e8f4fd", padx=15, pady=12,
                                highlightbackground="#1565C0", highlightthickness=2)
        summary_card.pack(fill="x", padx=5, pady=(8, 4))

        header_lbl = tk.Label(summary_card,
                              text="📊 Walk-Forward Degradation Summary",
                              font=("Segoe UI", 12, "bold"),
                              bg="#e8f4fd", fg=DARK, cursor="question_arrow")
        header_lbl.pack(anchor="w")
        _Tooltip(header_lbl,
            "WHAT THIS SECTION SHOWS\n\n"
            "These are your ACTUAL walk-forward test results — not projections.\n"
            "No synthetic 'realism factor' is applied here.\n\n"
            "Walk-Forward Degradation: how much your win rate / profit factor\n"
            "  dropped between the training window and the unseen test window.\n"
            "  Negative = edge decayed on new data.\n\n"
            "Edge Held Ratio: fraction of walk-forward windows where the\n"
            "  strategy remained profitable on unseen data.\n\n"
            "Monte Carlo Verdict: whether the strategy's profit sequence\n"
            "  is consistent or dependent on lucky trade ordering.\n\n"
            "WHY NO 'REALISTIC FORECAST':\n"
            "Projecting in-sample stats through a formula requires calibrated\n"
            "weights derived from many strategies across many instruments.\n"
            "We do not have that calibration, so we do not pretend to.\n"
            "The raw test results are your most honest signal."
        )

        # Degradation status row
        tk.Label(summary_card,
                 text=f"Walk-Forward Status:  {degrad_label}",
                 font=("Segoe UI", 11, "bold"), bg="#e8f4fd", fg=degrad_color,
                 ).pack(anchor="w", pady=(6, 2))

        tk.Label(summary_card,
                 text=f"Edge Held:  {edge_held*100:.0f}% of walk-forward windows profitable  "
                      f"|  Monte Carlo: {mc_v}",
                 font=("Segoe UI", 9), bg="#e8f4fd", fg="#444").pack(anchor="w", pady=(0, 6))

        # In-sample stats (raw, not projected)
        in_col_frame = tk.Frame(summary_card, bg="#e8f4fd")
        in_col_frame.pack(fill="x")
        tk.Label(in_col_frame, text="IN-SAMPLE STATS (backtest period):",
                 font=("Segoe UI", 9, "bold"), bg="#e8f4fd", fg="#666").pack(anchor="w")
        tk.Label(in_col_frame,
                 text=f"PF {in_pf}  |  WR {in_wr}%  |  Net {in_pips:+,} pips  |  Max DD {in_dd:,} pips",
                 font=("Segoe UI", 9), bg="#e8f4fd", fg="#444").pack(anchor="w", pady=(2, 4))

        # If edge collapsed, show explicit warning instead of any projection
        if wf_degrad <= -0.50:
            tk.Label(summary_card,
                     text="⛔  Edge has collapsed in walk-forward testing.\n"
                          "    Live performance cannot be meaningfully estimated from in-sample stats.\n"
                          "    Do NOT fund a challenge based on these in-sample numbers.",
                     font=("Segoe UI", 9, "bold"), bg="#e8f4fd", fg=RED,
                     wraplength=600, justify="left").pack(anchor="w", pady=(4, 0))
        else:
            tk.Label(summary_card,
                     text=f"ℹ️  In-sample stats shown above are NOT a live trading forecast.\n"
                          f"    Real performance will differ — use the walk-forward degradation\n"
                          f"    ({wf_degrad*100:+.0f}%) as your best signal of expected decay.",
                     font=("Segoe UI", 8, "italic"), bg="#e8f4fd", fg="#555",
                     wraplength=600, justify="left").pack(anchor="w", pady=(4, 0))

    card = tk.Frame(_verdict_frame, bg=WHITE,
                    highlightbackground=grade_color, highlightthickness=2,
                    padx=16, pady=12)
    card.pack(fill="x", padx=5, pady=8)

    # Title row
    title_row = tk.Frame(card, bg=WHITE)
    title_row.pack(fill="x", pady=(0, 4))
    tk.Label(title_row, text=f"CONFIDENCE: {score}/100",
             font=("Segoe UI", 14, "bold"), bg=WHITE, fg=DARK).pack(side=tk.LEFT)
    tk.Label(title_row, text=f"GRADE: {grade}",
             font=("Segoe UI", 14, "bold"), bg=WHITE, fg=grade_color).pack(side=tk.RIGHT)

    # Progress bar (text-based)
    bar_filled = int(score / 100 * 40)
    bar_empty  = 40 - bar_filled
    bar_str = "█" * bar_filled + "░" * bar_empty
    tk.Label(card, text=bar_str, font=("Consolas", 9),
             bg=WHITE, fg=grade_color).pack(anchor="w", pady=(0, 8))

    # Verdicts
    wf_v    = verdicts.get('walk_forward', 'N/A')
    mc_v    = verdicts.get('monte_carlo', 'N/A')
    slip_v  = verdicts.get('slippage', 'N/A')
    firm_v  = verdicts.get('live_firm', 'N/A')
    verdict_icons = {
        'LIKELY_REAL': '✅', 'INCONCLUSIVE': '⚠️',
        'LIKELY_OVERFITTING': '❌', 'INSUFFICIENT_DATA': '⚪',
        'ROBUST': '✅', 'MODERATE': '⚠️', 'FRAGILE': '❌', 'NO_EDGE': '❌',
        'EXCELLENT': '✅', 'ACCEPTABLE': '⚠️', 'NO_VIABLE_FIRM': '❌',
        'N/A': '—',
    }
    verdict_colors = {
        'LIKELY_REAL': GREEN, 'INCONCLUSIVE': AMBER,
        'LIKELY_OVERFITTING': RED, 'INSUFFICIENT_DATA': GREY,
        'ROBUST': GREEN, 'MODERATE': AMBER, 'FRAGILE': RED, 'NO_EDGE': RED,
        'EXCELLENT': GREEN, 'ACCEPTABLE': AMBER, 'NO_VIABLE_FIRM': RED,
        'N/A': GREY,
    }
    tk.Label(card,
             text=f"Walk-Forward:  {wf_v.replace('_', ' ')}  {verdict_icons.get(wf_v, '')}",
             font=("Segoe UI", 10), bg=WHITE,
             fg=verdict_colors.get(wf_v, GREY)).pack(anchor="w", pady=1)
    tk.Label(card,
             text=f"Monte Carlo:   {mc_v.replace('_', ' ')}  {verdict_icons.get(mc_v, '')}",
             font=("Segoe UI", 10), bg=WHITE,
             fg=verdict_colors.get(mc_v, GREY)).pack(anchor="w", pady=1)
    tk.Label(card,
             text=f"Slippage Test: {slip_v.replace('_', ' ')}  {verdict_icons.get(slip_v, '')}",
             font=("Segoe UI", 10), bg=WHITE,
             fg=verdict_colors.get(slip_v, GREY)).pack(anchor="w", pady=1)
    tk.Label(card,
             text=f"Live Firms:    {firm_v.replace('_', ' ')}  {verdict_icons.get(firm_v, '')}",
             font=("Segoe UI", 10), bg=WHITE,
             fg=verdict_colors.get(firm_v, GREY)).pack(anchor="w", pady=(1, 8))

    # Recommendation
    tk.Label(card, text=rec, font=("Segoe UI", 10),
             bg=WHITE, fg=MIDGREY, wraplength=600, justify="left").pack(anchor="w", pady=(0, 8))

    # Warnings
    for warn in warns:
        tk.Label(card, text=f"⚠  {warn}",
                 font=("Segoe UI", 9), bg=WHITE, fg=AMBER).pack(anchor="w", pady=1)

    # Navigation buttons
    nav_row = tk.Frame(card, bg=WHITE)
    nav_row.pack(anchor="w", pady=(12, 0))

    tk.Button(nav_row, text="Proceed to Prop Firm Test →",
              command=lambda: state.all_panels.get('p2_prop_test') and _nav('p2_prop_test'),
              bg="#667eea", fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=14, pady=5).pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(nav_row, text="Back to Refiner",
              command=lambda: _nav('p2_refiner'),
              bg=GREY, fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=14, pady=5).pack(side=tk.LEFT)

    # Show estimation ONLY if walk-forward found real data
    wf_verdict = verdicts.get('walk_forward', 'N/A')
    if trades and wf_verdict in ('LIKELY_REAL', 'INCONCLUSIVE'):
        _show_estimation(trades, _verdict_frame)
    elif trades and wf_verdict in ('LIKELY_OVERFITTING', 'INSUFFICIENT_DATA', 'N/A'):
        reason = ("Walk-forward had no trade data — cannot evaluate."
                  if wf_verdict == 'INSUFFICIENT_DATA'
                  else "Walk-forward indicates overfitting — in-sample results unreliable.")
        warn_frame = tk.LabelFrame(_verdict_frame,
            text="🎯 Estimation Suppressed",
            font=("Segoe UI", 10, "bold"), bg=WHITE, fg=RED,
            padx=10, pady=8)
        warn_frame.pack(fill="x", padx=5, pady=(10, 5))
        tk.Label(warn_frame, text=reason + "\nImprove the strategy before estimating payouts.",
            bg=WHITE, fg="#666", font=("Segoe UI", 9), wraplength=550,
            justify="left").pack(anchor="w")


def _nav(panel_name):
    """Navigate to another panel by reusing show_panel from sidebar."""
    try:
        from sidebar import build_sidebar
        for pframe in state.all_panels.values():
            pframe.pack_forget()
        if panel_name in state.all_panels:
            state.all_panels[panel_name].pack(fill="both", expand=True)
        state.active_panel[0] = panel_name
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Run logic
# ─────────────────────────────────────────────────────────────────────────────

def _make_progress_cb(label_text):
    def _cb(step, total, message):
        if _status_lbl and state.window:
            pct = int(step / max(total, 1) * 100)
            state.window.after(0, lambda: _status_lbl.configure(
                text=f"{label_text} | {message}", fg=GREY))
            if _progress_bar:
                state.window.after(0, lambda: _progress_bar.configure(value=pct))
    return _cb


def _display_batch_summary(batch_results):
    """Show a summary table of all validated strategies after batch run."""
    if _verdict_frame is None:
        return
    for w in _verdict_frame.winfo_children():
        w.destroy()

    if not batch_results:
        return

    # Title
    tk.Label(_verdict_frame, text=f"✅ Batch Validation Summary — {len(batch_results)} strategies",
             font=("Segoe UI", 13, "bold"), bg=BG, fg=DARK).pack(anchor="w", padx=5, pady=(10, 8))

    # Sort by score descending
    batch_results.sort(key=lambda x: x.get('score', 0), reverse=True)

    # Table frame
    table = tk.Frame(_verdict_frame, bg=WHITE, padx=10, pady=8)
    table.pack(fill="x", padx=5, pady=(0, 8))

    # Header
    hdr = tk.Frame(table, bg="#e8ecf0")
    hdr.pack(fill="x")
    cols = [
        ("Strategy", 28), ("Trades", 8), ("WR", 7), ("PF", 7),
        ("WF Verdict", 16), ("MC", 12), ("Slip", 12),
        ("Grade", 7), ("Score", 7),
    ]
    for text, w in cols:
        tk.Label(hdr, text=text, font=("Consolas", 8, "bold"),
                 bg="#e8ecf0", fg="#333", width=w, anchor="w").pack(side=tk.LEFT, padx=1)

    # Rows
    grade_colors = {'A': "#28a745", 'B': "#2d8a4e", 'C': "#996600", 'D': "#e67e00", 'F': "#e94560"}
    verdict_short = {
        'LIKELY_REAL': '✅ Real', 'INCONCLUSIVE': '⚠️ Unclear',
        'LIKELY_OVERFITTING': '❌ Overfit', 'INSUFFICIENT_DATA': '⚪ No data',
        'ROBUST': '✅ Robust', 'MODERATE': '⚠️ Moderate', 'FRAGILE': '❌ Fragile',
        'NO_EDGE': '❌ No edge', 'N/A': '—', 'ERROR': '❌ Error',
    }

    for i, br in enumerate(batch_results):
        grade = br.get('grade', '?')
        score = br.get('score', 0)
        g_color = grade_colors.get(grade, GREY)
        row_bg = "#f8f9fa" if i % 2 == 0 else WHITE

        row = tk.Frame(table, bg=row_bg)
        row.pack(fill="x")

        strategy_name = f"{br.get('rule_combo', '?')} × {br.get('exit_name', '?')}"
        wr = br.get('win_rate', 0)
        wr_str = f"{wr:.1f}%" if wr > 1 else f"{wr*100:.1f}%"

        values = [
            (strategy_name, 28, "#333"),
            (str(br.get('total_trades', 0)), 8, "#333"),
            (wr_str, 7, "#333"),
            (f"{br.get('net_profit_factor', 0):.2f}", 7, "#333"),
            (verdict_short.get(br.get('wf_verdict', 'N/A'), br.get('wf_verdict', '?')), 16, "#333"),
            (verdict_short.get(br.get('mc_verdict', 'N/A'), br.get('mc_verdict', '?')), 12, "#333"),
            (verdict_short.get(br.get('slip_verdict', 'N/A'), br.get('slip_verdict', '?')), 12, "#333"),
            (grade, 7, g_color),
            (str(score), 7, g_color),
        ]

        for text, w, fg in values:
            tk.Label(row, text=text, font=("Consolas", 8),
                     bg=row_bg, fg=fg, width=w, anchor="w").pack(side=tk.LEFT, padx=1)

    # Best strategy highlight
    best = batch_results[0]
    best_name = f"{best.get('rule_combo', '?')} × {best.get('exit_name', '?')}"

    highlight = tk.Frame(_verdict_frame, bg="#e8f5e9", padx=12, pady=8)
    highlight.pack(fill="x", padx=5, pady=(4, 8))
    tk.Label(highlight,
        text=f"🏆 Best: {best_name} — Grade {best.get('grade', '?')} ({best.get('score', 0)}/100)",
        font=("Segoe UI", 10, "bold"), bg="#e8f5e9", fg="#2d8a4e").pack(anchor="w")

    grade_val = best.get('grade')
    if grade_val in ('A', 'B'):
        rec = "Proceed to Prop Firm Test with this strategy."
    elif grade_val == 'C':
        rec = "Some edge found — consider refining before prop firm testing."
    else:
        rec = "No strong edge — go back to Refiner and improve."
    tk.Label(highlight, text=rec,
             font=("Segoe UI", 9), bg="#e8f5e9", fg="#555").pack(anchor="w")

    # Navigation buttons
    nav_row = tk.Frame(_verdict_frame, bg=BG)
    nav_row.pack(anchor="w", padx=5, pady=(0, 10))

    tk.Button(nav_row, text="Proceed to Prop Firm Test →",
              command=lambda: _nav('p2_prop_test'),
              bg="#667eea", fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=14, pady=5).pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(nav_row, text="Back to Refiner",
              command=lambda: _nav('p2_refiner'),
              bg=GREY, fg="white", font=("Segoe UI", 9, "bold"),
              relief=tk.FLAT, cursor="hand2", padx=14, pady=5).pack(side=tk.LEFT)

    # Update scroll region
    if _scroll_canvas:
        try:
            _verdict_frame.update_idletasks()
            _scroll_canvas.configure(scrollregion=_scroll_canvas.bbox("all"))
        except Exception:
            pass


def _run_multi(mode):
    """Run validation on all selected strategies, one at a time."""
    global _batch_mode
    indices = _get_all_selected_indices()
    if not indices:
        messagebox.showwarning("No Selection", "Select at least one strategy from the table.")
        return

    if len(indices) == 1:
        _run(mode)
        return

    if not messagebox.askyesno("Batch Validation",
                               f"Run {mode} validation on {len(indices)} selected strategies?\n\n"
                               f"This may take a while."):
        return

    def _worker():
        global _batch_mode
        _batch_mode = True

        # Clear once at the start
        state.window.after(0, lambda: _set_buttons(True))
        state.window.after(0, _clear_results)

        batch_results = []  # collect (idx, label, combined) for summary

        try:
            for i, idx in enumerate(indices):
                # WHY: Use type-safe lookup (idx can be int or str)
                # CHANGED: April 2026 — use _strategy_for_iid() helper (checkbox bug fix)
                strat = _strategy_for_iid(idx)
                if not strat:
                    continue

                label = strat.get('label', f'Strategy {idx}')
                if _status_lbl:
                    state.window.after(0, lambda lbl=label, i=i, total=len(indices):
                                        _status_lbl.config(text=f"[{i+1}/{total}] Validating: {lbl}..."))

                done = threading.Event()
                _run(mode, override_idx=idx, done_event=done)
                done.wait(timeout=600)

                # Read the saved result
                try:
                    from project2_backtesting.strategy_validator import get_validation_for_strategy
                    result = get_validation_for_strategy(idx)
                    combined = result.get('combined', {}) if result else {}
                    batch_results.append({
                        'idx': idx,
                        'label': label,
                        'rule_combo': strat.get('rule_combo', '?'),
                        'exit_name': strat.get('exit_name', '?'),
                        'total_trades': strat.get('total_trades', 0),
                        'win_rate': strat.get('win_rate', 0),
                        'net_profit_factor': strat.get('net_profit_factor', 0),
                        'grade': combined.get('grade', '?'),
                        'score': combined.get('confidence_score', 0),
                        'wf_verdict': combined.get('verdicts', {}).get('walk_forward', 'N/A'),
                        'mc_verdict': combined.get('verdicts', {}).get('monte_carlo', 'N/A'),
                        'slip_verdict': combined.get('verdicts', {}).get('slippage', 'N/A'),
                    })
                except Exception:
                    batch_results.append({
                        'idx': idx, 'label': label,
                        'rule_combo': strat.get('rule_combo', '?'),
                        'exit_name': strat.get('exit_name', '?'),
                        'total_trades': 0, 'win_rate': 0, 'net_profit_factor': 0,
                        'grade': '?', 'score': 0,
                        'wf_verdict': 'ERROR', 'mc_verdict': 'N/A', 'slip_verdict': 'N/A',
                    })

            # Show batch summary
            state.window.after(0, lambda br=list(batch_results): _display_batch_summary(br))

            if _status_lbl:
                state.window.after(0, lambda: _status_lbl.config(
                    text=f"✅ Done — validated {len(batch_results)} strategies"))

        except Exception as e:
            import traceback; traceback.print_exc()
            if _status_lbl:
                state.window.after(0, lambda: _status_lbl.config(
                    text=f"Batch error: {e}"))
        finally:
            _batch_mode = False
            state.window.after(0, lambda: _set_buttons(False))

    threading.Thread(target=_worker, daemon=True).start()


def _run(mode, override_idx=None, done_event=None):
    """
    mode: 'wf' | 'mc' | 'full' | 'slip'
    override_idx: if provided, use this index instead of reading from checkboxes/dropdown
    done_event: threading.Event — set when validation completes (for batch mode)
    """
    # Use override index if provided (for batch validation)
    if override_idx is not None:
        idx = override_idx
    else:
        # Get from checkboxes first, fallback to dropdown
        indices = _get_all_selected_indices()
        if indices:
            # WHY: indices now preserve original type (int or str)
            # CHANGED: April 2026 — removed int() cast (checkbox bug fix)
            idx = indices[0]  # Use first checked item
        else:
            idx = _get_selected_index()  # Fallback to dropdown

    if idx is None:
        messagebox.showerror("No Strategy", "Select a strategy first.")
        return

    # Read per-strategy entry_tf first (multi-TF backtest support)
    # WHY: Each row from a multi-TF backtest carries its own entry_tf.
    #      Pass it as a hint so the validator uses the correct candle file.
    # CHANGED: April 2026 — multi-TF support
    _row_entry_tf = None
    try:
        _matrix_path = os.path.join(project_root, 'project2_backtesting',
                                    'outputs', 'backtest_matrix.json')
        with open(_matrix_path, 'r', encoding='utf-8') as _mf:
            _mdata = json.load(_mf)
        # WHY: Only backtest matrix rows (int idx) carry per-row entry_tf.
        #      Saved/optimizer rows store entry_timeframe in their own data
        #      structures — _get_strategy_meta handles those branches.
        # CHANGED: April 2026 — safe int check for string indices
        _row = {}
        if isinstance(idx, int):
            _results = _mdata.get('results', []) or _mdata.get('matrix', [])
            if idx < len(_results):
                _row = _results[idx]
        elif isinstance(idx, str) and idx.isdigit():
            _ii = int(idx)
            _results = _mdata.get('results', []) or _mdata.get('matrix', [])
            if _ii < len(_results):
                _row = _results[_ii]
        # WHY: Same as view_results.py fix — stats are flattened to top level
        # CHANGED: April 2026 — read flattened stats from row top level
        _row_entry_tf = (_row.get('entry_tf') or
                         (_row.get('stats') or _row).get('entry_tf'))
        if _row_entry_tf:
            print(f"[VALIDATOR] Using per-strategy entry TF: {_row_entry_tf}")
    except Exception:
        pass

    candles_path = _get_candles_path(entry_tf_hint=_row_entry_tf)
    if not candles_path and mode in ('wf', 'full', 'slip'):
        messagebox.showerror("No Candle Data",
                             "H1 candle CSV not found in data/ folder.\n"
                             "Required for walk-forward validation.")
        if mode == 'full':
            pass  # still run MC-only below
        else:
            return

    rules, exit_class, exit_params, trades, spread_meta, comm_meta, strategy_filters, strategy_direction = _get_strategy_meta(idx)
    print(f"[VALIDATOR] Strategy direction: {strategy_direction}")

    # ── Read run_settings from strategy for leverage auto-detection ──
    global _current_run_settings
    _val_run_settings = {}
    try:
        if isinstance(idx, (int, str)) and not str(idx).startswith('saved_') and str(idx) != 'optimizer_latest':
            _val_bp = os.path.join(project_root, 'project2_backtesting', 'outputs', 'backtest_matrix.json')
            import json as _vj
            with open(_val_bp, 'r', encoding='utf-8') as _vf:
                _val_data = _vj.load(_vf)
            _val_results = _val_data.get('results', []) or _val_data.get('matrix', [])
            _val_ridx = int(idx) if str(idx).isdigit() else idx
            if isinstance(_val_ridx, int) and 0 <= _val_ridx < len(_val_results):
                _val_run_settings = _val_results[_val_ridx].get('run_settings', {})
        elif str(idx).startswith('saved_'):
            _val_sp = os.path.join(project_root, 'project2_backtesting', 'outputs', 'saved_rules.json')
            if os.path.exists(_val_sp):
                import json as _vj2
                with open(_val_sp, 'r', encoding='utf-8') as _vsf:
                    _val_saved = _vj2.load(_vsf)
                _val_ridx2 = int(idx.split('_', 1)[1])
                if 0 <= _val_ridx2 < len(_val_saved):
                    _val_run_settings = _val_saved[_val_ridx2].get('run_settings', {})
    except Exception as _vex:
        print(f"[VALIDATOR] Could not read run_settings for leverage: {_vex}")

    # Store in global so _show_estimation can read pip_value from strategy
    _current_run_settings = _val_run_settings

    # ── Loud diagnostics — print everything the validator will use ──
    # WHY: 0-trade walks are silent. Without diagnostics you can't tell
    #      if rules didn't load, indicators are missing, or candle data
    #      doesn't cover the date range.
    # CHANGED: April 2026 — validator diagnostics
    print(f"[VALIDATOR] ═══ STRATEGY META FOR idx={idx!r} ═══")
    print(f"[VALIDATOR]   Rules: {len(rules)} rules")
    if rules:
        for _ri, _r in enumerate(rules):
            _conds = _r.get('conditions', [])
            print(f"[VALIDATOR]     Rule {_ri+1}: prediction={_r.get('prediction','?')}, "
                  f"{len(_conds)} conditions")
            for _c in _conds[:2]:
                print(f"[VALIDATOR]       {_c.get('feature','?')} {_c.get('operator','?')} {_c.get('value','?')}")
            if len(_conds) > 2:
                print(f"[VALIDATOR]       ... and {len(_conds)-2} more")
    else:
        print(f"[VALIDATOR]   ⚠️ NO RULES — walk-forward will produce 0 trades!")
    print(f"[VALIDATOR]   Exit: {exit_class} {exit_params}")
    print(f"[VALIDATOR]   Trades: {len(trades)}")
    print(f"[VALIDATOR]   Filters: {strategy_filters}")
    print(f"[VALIDATOR] ═══════════════════════════════════════")

    # WHY: Old code silently ran validation on empty trade lists, producing
    #      "validated 0 trades" results that looked like normal output but
    #      were actually a sign that _get_strategy_meta had failed to load
    #      the strategy. Now: fail loudly so the user knows to re-save the
    #      strategy or pick a different one.
    # CHANGED: April 2026 — guard against silent empty-strategy validation
    if not trades and not rules:
        messagebox.showerror(
            "Strategy Has No Data",
            f"Could not load strategy {idx!r} — got 0 trades and 0 rules.\n\n"
            f"Most likely causes:\n"
            f"  • The strategy was saved before April 2026 and is missing\n"
            f"    embedded trades — re-save it from the Refiner Optimizer.\n"
            f"  • The backtest_matrix.json file was deleted or moved.\n"
            f"  • The _validator_optimized.json file is empty or missing.\n\n"
            f"Check the terminal for [validator] messages with more detail."
        )
        if not _batch_mode:
            _set_buttons(False)
        return

    try:
        account_size  = int(_account_var.get())
        spread_pips   = float(_spread_var.get())
        comm_pips     = float(_comm_var.get())
        risk_pct      = float(_risk_var.get())
        sl_pips       = float(_sl_var.get())
        # WHY: Read pip_value from strategy's run_settings first, then UI, then default.
        #      Strategy carries its own broker specs (single source of truth).
        # CHANGED: April 2026 — rule-driven pip_value (BUG 5 fix)
        _mc_rule_pv = _val_run_settings.get('pip_value_per_lot', 0)
        if _mc_rule_pv and float(_mc_rule_pv) > 0:
            pip_val = float(_mc_rule_pv)
            print(f"[VALIDATOR] Using pip value from strategy: ${pip_val}/lot")
        else:
            pip_val = float(_pipval_var.get())
        pip_size      = float(_pip_size_var.get())
        n_windows     = int(_windows_var.get())
        train_years   = int(_train_var.get())
        test_years    = int(_test_var.get())
        n_sims        = int(_sims_var.get())
        # WHY (Phase 68 Fix 25): Old fallback stripped spaces, hyphens, and
        #      underscores but not parentheses or other chars. A firm name
        #      like 'FTMO (90 day)' became 'ftmo(90day)' — no match in the
        #      firm registry → MC test ran against wrong/default firm silently.
        #      Use re.sub to strip ALL non-alphanumeric characters.
        # CHANGED: April 2026 — Phase 68 Fix 25 — strip all special chars
        #          (audit Part E HIGH #25)
        import re as _re25
        _raw_firm = _mc_firm_var.get()
        mc_firm   = _firm_name_to_id.get(
            _raw_firm,
            _re25.sub(r'[^a-z0-9]', '', _raw_firm.lower())
        )
    except ValueError:
        messagebox.showerror("Invalid Settings", "Check that all settings are valid numbers.")
        return

    # ── Leverage detection for margin-aware validation ──
    _val_leverage = _val_run_settings.get('leverage', 0)
    _val_contract = _val_run_settings.get('contract_size', 100.0)
    _val_sym = _val_run_settings.get('symbol', '') or (trades[0].get('symbol', '') if trades else '')
    if not _val_sym:
        try:
            from project2_backtesting.panels.configuration import load_config as _vc
            _val_sym = _vc().get('symbol', 'XAUUSD')
        except Exception:
            _val_sym = 'XAUUSD'
    if _val_leverage == 0:
        try:
            from shared.prop_firm_engine import get_leverage_for_symbol, get_instrument_type, load_all_firms
            _val_firm_id = _val_run_settings.get('firm_id', '')
            if _val_firm_id:
                _val_firms = load_all_firms()
                if _val_firm_id in _val_firms:
                    _val_leverage = get_leverage_for_symbol(_val_firms[_val_firm_id].config, _val_sym)
            if _val_leverage == 0:
                _val_inst = get_instrument_type(_val_sym)
                _val_leverage = {'forex': 30, 'metals': 10, 'indices': 10, 'energies': 5, 'crypto': 1}.get(_val_inst, 30)
            if _val_contract == 100.0:
                _val_inst2 = get_instrument_type(_val_sym)
                if _val_inst2 == 'forex':
                    _val_contract = 100000.0
                elif _val_inst2 == 'indices':
                    _val_contract = 1.0
        except Exception:
            pass
    print(f"[VALIDATOR] Leverage: 1:{_val_leverage}, contract_size={_val_contract}, symbol={_val_sym!r}")

    if not _batch_mode:
        _set_buttons(True)
        _clear_results()
    if _status_lbl:
        _status_lbl.configure(text="Starting...", fg=GREY)
    if _progress_bar:
        _progress_bar.configure(value=0)

    def _worker():
        try:
            from project2_backtesting.strategy_validator import (
                walk_forward_validate, monte_carlo_test, combined_score,
                slippage_stress_test, run_full_validation, _save_validation,
            )

            wf_result   = None
            mc_result   = None
            slip_result = None

            # Parse custom windows if specified
            custom_windows_str = _custom_windows_var.get().strip() if _custom_windows_var else ""
            custom_windows = None
            if custom_windows_str:
                custom_windows = []
                for part in custom_windows_str.split(','):
                    part = part.strip()
                    if '>' in part:
                        train_part, test_part = part.split('>', 1)
                        train_part = train_part.strip()
                        test_part = test_part.strip()
                        if '-' in train_part:
                            t_start, t_end = train_part.split('-', 1)
                            t_start = t_start.strip()
                            t_end   = t_end.strip()
                            # WHY (Phase 69 Fix 26): Old code silently accepted
                            #      inverted windows (train_start > train_end).
                            #      walk_forward_validate then either crashed or
                            #      produced zero windows with no explanation.
                            # CHANGED: April 2026 — Phase 69 Fix 26 — validate bounds
                            #          (audit Part E MEDIUM #26)
                            try:
                                if t_start >= t_end:
                                    messagebox.showwarning(
                                        "Invalid Window",
                                        f"Custom window '{part}': train_start ({t_start}) "
                                        f"must be before train_end ({t_end}). Skipping."
                                    )
                                    continue
                            except Exception:
                                pass
                            custom_windows.append({
                                'train_start': t_start,
                                'train_end':   t_end,
                                'test_year':   test_part.strip(),
                            })
                if custom_windows:
                    print(f"[VALIDATOR] {len(custom_windows)} custom window(s) will be ADDED to auto windows")

            if mode in ('wf', 'full') and candles_path:
                wf_result = walk_forward_validate(
                    rules=rules,
                    candles_path=candles_path,
                    exit_strategy_class=exit_class,
                    exit_strategy_params=exit_params,
                    n_windows=n_windows,
                    train_years=train_years,
                    test_years=test_years,
                    pip_size=pip_size,
                    spread_pips=spread_pips,
                    commission_pips=comm_pips,
                    account_size=account_size,
                    custom_windows=custom_windows,
                    progress_callback=_make_progress_cb("Walk-Forward"),
                    # WHY (Validator Fix): Pass optimizer filters so walk-forward
                    #      validates the ACTUAL optimized strategy, not the raw one.
                    # CHANGED: April 2026 — Validator Fix
                    filters=strategy_filters,
                    direction=strategy_direction,
                    leverage=_val_leverage,
                    contract_size=_val_contract,
                )
                state.window.after(0, lambda r=wf_result: _display_wf_results(r))

            # WHY (Validator Fix): Apply optimizer filters to trades before
            #      Monte Carlo. MC shuffles the trade list — it must be the
            #      filtered list, not the raw one.
            # CHANGED: April 2026 — Validator Fix
            _mc_trades = trades
            if strategy_filters and trades:
                try:
                    from project2_backtesting.strategy_refiner import apply_filters, enrich_trades
                    _enriched = enrich_trades(list(trades))
                    _mc_trades, _ = apply_filters(_enriched, strategy_filters)
                    print(f"[validator] Applied filters to MC trades: {len(trades)} → {len(_mc_trades)}")
                except Exception as _fe:
                    print(f"[validator] Could not apply filters to MC trades: {_fe}")
                    _mc_trades = trades

            if mode in ('mc', 'full'):
                print(f"[validator] Starting MC: {len(_mc_trades)} trades, "
                      f"firm={mc_firm!r}, account={account_size}, sims={n_sims}, "
                      f"risk={risk_pct}%, sl={sl_pips}, pip_val={pip_val}")
                mc_result = monte_carlo_test(
                    trades=_mc_trades,
                    firm_id=mc_firm,
                    account_size=account_size,
                    n_simulations=n_sims,
                    risk_per_trade_pct=risk_pct,
                    default_sl_pips=sl_pips,
                    pip_value_per_lot=pip_val,
                    progress_callback=_make_progress_cb("Monte Carlo"),
                    symbol=_val_sym,
                )
                print(f"[validator] MC result: verdict={mc_result.get('verdict', '?')}, "
                      f"pass_rate={mc_result.get('mean_pass_rate', '?')}, "
                      f"error={mc_result.get('error', 'none')}")
                state.window.after(0, lambda r=mc_result: _display_mc_results(r))

            if mode in ('slip', 'full') and candles_path:
                slip_result = slippage_stress_test(
                    trades=trades,
                    rules=rules,
                    candles_path=candles_path,
                    exit_strategy_class=exit_class,
                    exit_strategy_params=exit_params,
                    # WHY (Phase 69 Fix 27): Old hardcoded [0,1,2,3,5] pip slippage
                    #      levels missed high-slippage instruments (GBPJPY, BTC) where
                    #      10-20 pip slippage is realistic. Read from a configurable
                    #      UI variable; fall back to defaults when not set.
                    # CHANGED: April 2026 — Phase 69 Fix 27 — configurable slippage levels
                    #          (audit Part E MEDIUM #27)
                    slippage_levels=_get_slippage_levels(),
                    pip_size=pip_size,
                    spread_pips=spread_pips,
                    commission_pips=comm_pips,
                    account_size=account_size,
                    n_runs_per_level=3,
                    progress_callback=_make_progress_cb("Slippage Test"),
                    # WHY (Validator Fix): Pass actual optimizer filters.
                    # CHANGED: April 2026 — Validator Fix
                    filters=strategy_filters,
                    leverage=_val_leverage,
                    contract_size=_val_contract,
                )
                state.window.after(0, lambda r=slip_result: _display_slip_results(r))

            # ── Live Firm Simulation ────────────────────────────────────────
            # WHY: Tests against each firm's exact rules — most realistic estimate.
            # CHANGED: April 2026
            live_firm_results = None
            if trades and mode in ('full', 'mc'):
                try:
                    from shared.live_firm_sim import simulate_all_firms
                    if _status_lbl:
                        state.window.after(0, lambda: _status_lbl.configure(
                            text="Running live firm simulation...", fg=GREY))
                    live_firm_results = simulate_all_firms(trades, account_size=account_size)
                    state.window.after(0, lambda r=live_firm_results: _display_live_firm_results(r))
                except Exception as e:
                    # WHY (Phase 68 Fix 24): Old code silently swallowed
                    #      live_firm_sim errors. The verdict panel still
                    #      rendered 4 check-rows but only 3 actually ran.
                    #      Set a sentinel so _display_verdict can show
                    #      "Live Firms: ERROR" instead of a misleading N/A.
                    # CHANGED: April 2026 — Phase 68 Fix 24 — live firm error visible
                    #          (audit Part E HIGH #24)
                    print(f"[validator] live firm sim failed: {e}")
                    import traceback; traceback.print_exc()
                    live_firm_results = {'_error': str(e), 'verdict': 'ERROR'}

            if wf_result or mc_result or slip_result or live_firm_results:
                combined = combined_score(wf_result, mc_result, slip_result, live_firm_results)
                state.window.after(0, lambda c=combined, t=trades: _display_verdict(c, t))

                # Save
                result = {
                    'strategy_index':    idx,
                    'validated_at':      __import__('datetime').datetime.now().isoformat(),
                    'walk_forward':      wf_result,
                    'monte_carlo':       mc_result,
                    'slippage':          slip_result,
                    'live_firm_results': live_firm_results,
                    'combined':          combined,
                }
                _save_validation(idx, result)
                state.window.after(0, _update_strat_info)

                # WHY: Update status of saved rules to 'validated' with grade and score
                # CHANGED: April 2026 — lifecycle status tracking
                try:
                    from shared.saved_rules import update_rule_field
                    _grade = combined.get('grade', '?')
                    _score = combined.get('confidence_score', 0)
                    _updated_count = 0
                    for rule in rules:
                        _entry_id = rule.get('_saved_entry_id')
                        _rule_id = rule.get('_saved_rule_id')
                        if _entry_id or _rule_id:
                            _id_to_update = _rule_id if _rule_id else _entry_id
                            try:
                                update_rule_field(_id_to_update, 'status', 'validated')
                                update_rule_field(_id_to_update, 'grade', _grade)
                                update_rule_field(_id_to_update, 'score', _score)
                                _updated_count += 1
                            except Exception as _ue:
                                print(f"[STATUS] Could not update status for rule {_id_to_update}: {_ue}")
                    if _updated_count > 0:
                        print(f"[STATUS] Updated {_updated_count} saved rules to 'validated' status (grade {_grade}, score {_score})")
                except Exception as _se:
                    print(f"[STATUS] Could not update rule statuses: {_se}")

                # WHY: Store for the Copy Results button
                # CHANGED: April 2026
                global _last_validation_result
                _last_validation_result = result

            state.window.after(0, lambda: _status_lbl.configure(
                text="Validation complete.", fg=GREEN))
            state.window.after(0, lambda: _progress_bar.configure(value=100))

        except Exception as e:
            import traceback; traceback.print_exc()
            # WHY (Validator Fix): Python 3 deletes `e` at end of except
            #      block. The lambda captures by reference — when it runs
            #      in the main thread via after(), `e` is gone → NameError.
            #      Capture as default arg to bind immediately.
            # CHANGED: April 2026 — Validator Fix
            _err_msg = str(e)
            state.window.after(0, lambda _m=_err_msg: _status_lbl.configure(
                text=f"Error: {_m}", fg=RED))
        finally:
            if not _batch_mode:
                state.window.after(0, lambda: _set_buttons(False))
            if done_event:
                done_event.set()

    threading.Thread(target=_worker, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Panel builder
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(parent):
    global _strategy_var, _strat_info_lbl, _prev_result_lbl
    global _tree, _selected_count
    global _train_var, _test_var, _windows_var, _recent_first_var, _custom_windows_var, _sims_var, _mc_firm_var, _stage_var
    global _account_var, _spread_var, _comm_var, _risk_var, _sl_var, _pipval_var, _pip_size_var
    global _start_wf_btn, _start_mc_btn, _start_full_btn, _start_slip_btn, _stop_btn
    global _status_lbl, _progress_bar, _scroll_canvas
    global _wf_frame, _mc_frame, _slip_frame, _live_firm_frame, _verdict_frame
    global _firm_name_to_id

    _load_strategies()

    panel = tk.Frame(parent, bg=BG)

    # ── Scrollable canvas wrapping ALL content ────────────────────────────────
    _scroll_canvas = tk.Canvas(panel, bg=BG, highlightthickness=0)
    vscroll = tk.Scrollbar(panel, orient="vertical", command=_scroll_canvas.yview)
    scroll_frame = tk.Frame(_scroll_canvas, bg=BG)

    scroll_frame.bind("<Configure>",
                      lambda e: _scroll_canvas.configure(
                          scrollregion=_scroll_canvas.bbox("all")))
    cwin = _scroll_canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    _scroll_canvas.configure(yscrollcommand=vscroll.set)
    _scroll_canvas.pack(side="left", fill="both", expand=True)
    vscroll.pack(side="right", fill="y")

    # Safe mousewheel binding
    def _on_enter(event):
        _scroll_canvas.bind("<MouseWheel>",
            lambda e: _scroll_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
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

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(scroll_frame, bg=WHITE, pady=16)
    hdr.pack(fill="x", padx=20, pady=(20, 10))
    tk.Label(hdr, text="✅ Strategy Validator",
             bg=WHITE, fg=DARK, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(hdr, text="Prove your strategy is real — not just overfitting",
             bg=WHITE, fg=GREY, font=("Segoe UI", 11)).pack(pady=(4, 0))

    # ── Strategy selector ─────────────────────────────────────────────────────
    sel_frame = tk.Frame(scroll_frame, bg=WHITE, padx=20, pady=12)
    sel_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(sel_frame, text="Strategy", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 6))

    _strategy_var = tk.StringVar(value="")

    if not _strategies:
        tk.Label(sel_frame,
                 text="No backtest results. Run the backtest first.",
                 font=("Segoe UI", 10, "italic"), bg=WHITE, fg=RED).pack(anchor="w")
    else:
        # Filter row
        global _filt_wr, _filt_pf, _filt_trades

        filter_frame = tk.Frame(sel_frame, bg=WHITE)
        filter_frame.pack(fill="x", pady=(0, 5))

        tk.Label(filter_frame, text="Filters:", font=("Segoe UI", 9, "bold"),
                 bg=WHITE, fg="#333").pack(side=tk.LEFT)

        tk.Label(filter_frame, text="Min WR:", font=("Segoe UI", 8),
                 bg=WHITE, fg="#555").pack(side=tk.LEFT, padx=(10, 2))
        _filt_wr = tk.StringVar(value="0")
        tk.Entry(filter_frame, textvariable=_filt_wr, width=4, font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Label(filter_frame, text="%", font=("Segoe UI", 8), bg=WHITE, fg="#555").pack(side=tk.LEFT)

        tk.Label(filter_frame, text="Min PF:", font=("Segoe UI", 8),
                 bg=WHITE, fg="#555").pack(side=tk.LEFT, padx=(10, 2))
        _filt_pf = tk.StringVar(value="0")
        tk.Entry(filter_frame, textvariable=_filt_pf, width=4, font=("Segoe UI", 8)).pack(side=tk.LEFT)

        tk.Label(filter_frame, text="Min Trades:", font=("Segoe UI", 8),
                 bg=WHITE, fg="#555").pack(side=tk.LEFT, padx=(10, 2))
        _filt_trades = tk.StringVar(value="0")
        tk.Entry(filter_frame, textvariable=_filt_trades, width=5, font=("Segoe UI", 8)).pack(side=tk.LEFT)

        def _apply_filters():
            _rebuild_tree()

        tk.Button(filter_frame, text="Apply", font=("Segoe UI", 8, "bold"),
                  bg="#667eea", fg="white", relief=tk.FLAT, padx=8,
                  command=_apply_filters).pack(side=tk.LEFT, padx=(10, 2))

        tk.Button(filter_frame, text="Reset", font=("Segoe UI", 8),
                  bg="#6c757d", fg="white", relief=tk.FLAT, padx=8,
                  command=lambda: [_filt_wr.set("0"), _filt_pf.set("0"), _filt_trades.set("0"), _rebuild_tree()]).pack(side=tk.LEFT, padx=2)

        # Sort buttons
        sort_frame = tk.Frame(sel_frame, bg=WHITE)
        sort_frame.pack(fill="x", pady=(5, 5))
        tk.Label(sort_frame, text="Sort by:", font=("Segoe UI", 9),
                 bg=WHITE, fg=GREY).pack(side=tk.LEFT)

        _sort_key = [None]  # current sort column

        def _sort_strategies(key, reverse=True):
            _sort_key[0] = key
            _strategies.sort(key=lambda s: s.get(key, 0), reverse=reverse)
            _rebuild_tree()

        for label, key, rev in [
            ("Profit ↓", "net_total_pips", True),
            ("Win Rate ↓", "win_rate", True),
            ("PF ↓", "net_profit_factor", True),
            ("DD ↑ (lowest)", "max_dd_pips", False),
            ("Trades ↓", "total_trades", True),
        ]:
            tk.Button(sort_frame, text=label, font=("Arial", 8),
                      bg="#667eea", fg="white", relief=tk.FLAT, padx=6, pady=2,
                      command=lambda k=key, r=rev: _sort_strategies(k, r)).pack(side=tk.LEFT, padx=2)

        # Strategy table with Treeview
        tree_frame = tk.Frame(sel_frame, bg=WHITE)
        tree_frame.pack(fill="x", pady=5)

        columns = ("select", "rule", "exit", "trades", "wr", "pf", "net_pips", "dd", "avg_pips")
        _tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                             height=min(len(_strategies), 10), selectmode="extended")

        _tree.heading("select",    text="✓")
        _tree.heading("rule",      text="Rule")
        _tree.heading("exit",      text="Exit Strategy")
        _tree.heading("trades",    text="Trades")
        _tree.heading("wr",        text="Win Rate")
        _tree.heading("pf",        text="PF")
        _tree.heading("net_pips",  text="Net Pips")
        _tree.heading("dd",        text="Max DD")
        _tree.heading("avg_pips",  text="Avg Pips")

        _tree.column("select",    width=30,  anchor="center")
        _tree.column("rule",      width=100, anchor="w")
        _tree.column("exit",      width=130, anchor="w")
        _tree.column("trades",    width=60,  anchor="center")
        _tree.column("wr",        width=70,  anchor="center")
        _tree.column("pf",        width=60,  anchor="center")
        _tree.column("net_pips",  width=90,  anchor="e")
        _tree.column("dd",        width=80,  anchor="e")
        _tree.column("avg_pips",  width=70,  anchor="e")

        # Scrollbar
        tree_scroll = tk.Scrollbar(tree_frame, orient="vertical", command=_tree.yview)
        _tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side=tk.RIGHT, fill="y")
        _tree.pack(fill="x")

        # Style rows with colors
        _tree.tag_configure("profitable", foreground="#28a745")
        _tree.tag_configure("losing", foreground="#dc3545")
        _tree.tag_configure("no_trades", foreground="#888888")
        _tree.tag_configure("saved_good", foreground="#2980b9")   # blue — saved rule with good stats
        _tree.tag_configure("separator", foreground="#aaa", background="#f5f5f5")

        def _rebuild_tree():
            global _check_vars, _selected_count
            _tree.delete(*_tree.get_children())

            # Get filter values
            try:
                min_wr = float(_filt_wr.get()) / 100
            except:
                min_wr = 0
            try:
                min_pf = float(_filt_pf.get())
            except:
                min_pf = 0
            try:
                min_trades = int(_filt_trades.get())
            except:
                min_trades = 0

            visible = 0
            for s in _strategies:
                wr = s.get('win_rate', 0)
                wr_val = wr if wr <= 1 else wr / 100
                pf = s.get('net_profit_factor', 0)
                trades = s.get('total_trades', 0)

                # Apply filters
                if wr_val < min_wr:
                    continue
                if pf < min_pf:
                    continue
                if trades < min_trades:
                    continue

                wr_str = f"{wr:.1f}%" if wr > 1 else f"{wr*100:.1f}%"
                net = s.get('net_total_pips', 0)
                dd = s.get('max_dd_pips', 0)

                # WHY: Stale saved rules (missing exit, conditions, etc.) are
                #      useless for validation — skip them entirely. Don't
                #      clutter the list with broken entries.
                # CHANGED: April 2026 — hide stale rules, source-aware coloring
                _source = s.get('source', '')
                if _source == 'separator':
                    tag = "separator"
                elif _source == 'saved':
                    # Skip stale rules entirely — they can't be validated
                    if s.get('is_stale'):
                        continue
                    # Skip rules with no conditions and no stats
                    wr_val = wr / 100 if wr > 1 else wr
                    if wr_val == 0 and pf == 0 and trades == 0:
                        continue
                    if pf > 1.0 or wr_val > 0.5:
                        tag = "saved_good"
                    else:
                        tag = "saved_good"
                elif trades == 0:
                    tag = "no_trades"
                elif net > 0:
                    tag = "profitable"
                else:
                    tag = "losing"

                idx = str(s['index'])
                checked = _check_vars.get(idx, False)
                check_mark = "☑" if checked else "☐"

                _tree.insert("", "end", iid=idx, values=(
                    check_mark,
                    s.get('rule_combo', '?'),
                    s.get('exit_name', '?'),
                    trades,
                    wr_str,
                    f"{pf:.2f}",
                    f"{net:+,.0f}",
                    f"{dd:,.0f}",
                    f"{s.get('net_avg_pips', s.get('avg_pips', 0)):+.1f}",
                ), tags=(tag,))
                visible += 1

            if _selected_count:
                checked_count = sum(1 for v in _check_vars.values() if v)
                _selected_count.config(text=f"{checked_count} selected of {visible} shown ({len(_strategies)} total)")

        _rebuild_tree()

        # Checkbox click handler
        def _on_click(event):
            global _check_vars, _selected_count
            region = _tree.identify_region(event.x, event.y)
            if region == "cell":
                col = _tree.identify_column(event.x)
                item = _tree.identify_row(event.y)
                if col == "#1" and item:  # first column = checkbox
                    current = _check_vars.get(item, False)
                    _check_vars[item] = not current
                    # Update the display
                    values = list(_tree.item(item, "values"))
                    values[0] = "☑" if _check_vars[item] else "☐"
                    _tree.item(item, values=values)
                    # Update count and info
                    checked_count = sum(1 for v in _check_vars.values() if v)
                    visible = len(_tree.get_children())
                    _selected_count.config(text=f"{checked_count} selected of {visible} shown ({len(_strategies)} total)")
                    _update_strat_info()

        _tree.bind("<Button-1>", _on_click)

        # Select All / Deselect All buttons
        btn_frame = tk.Frame(sel_frame, bg=WHITE)
        btn_frame.pack(fill="x", pady=(5, 0))

        def _select_all():
            global _check_vars, _selected_count
            for item in _tree.get_children():
                _check_vars[item] = True
                values = list(_tree.item(item, "values"))
                values[0] = "☑"
                _tree.item(item, values=values)
            checked = sum(1 for v in _check_vars.values() if v)
            visible = len(_tree.get_children())
            _selected_count.config(text=f"{checked} selected of {visible} shown ({len(_strategies)} total)")
            _update_strat_info()

        def _deselect_all():
            global _check_vars, _selected_count
            for item in _tree.get_children():
                _check_vars[item] = False
                values = list(_tree.item(item, "values"))
                values[0] = "☐"
                _tree.item(item, values=values)
            _selected_count.config(text=f"0 selected of {len(_tree.get_children())} shown ({len(_strategies)} total)")
            _update_strat_info()

        tk.Button(btn_frame, text="Select All Visible", font=("Segoe UI", 8),
                  bg="#28a745", fg="white", relief=tk.FLAT, padx=8,
                  command=_select_all).pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(btn_frame, text="Deselect All", font=("Segoe UI", 8),
                  bg="#6c757d", fg="white", relief=tk.FLAT, padx=8,
                  command=_deselect_all).pack(side=tk.LEFT)

        def _refresh_list():
            """Force reload from disk — picks up newly saved rules."""
            global _strategies, _strategies_cache, _cache_mtime
            _cache_mtime = 0  # Invalidate cache
            _strategies_cache = None
            _load_strategies()
            _rebuild_tree()
            print(f"[VALIDATOR] Refreshed — {len(_strategies)} strategies loaded")

        tk.Button(btn_frame, text="🔄 Refresh", font=("Segoe UI", 8),
                  bg="#3498db", fg="white", relief=tk.FLAT, padx=8,
                  command=_refresh_list).pack(side=tk.LEFT, padx=(10, 0))

        # Selection info
        sel_info = tk.Label(sel_frame, text="Click the checkbox (✓) column to select strategies for validation.",
                             font=("Segoe UI", 8), bg=WHITE, fg=GREY)
        sel_info.pack(anchor="w", pady=(5, 0))

        _selected_count = tk.Label(sel_frame, text="0 selected of 0 shown (0 total)",
                                    font=("Segoe UI", 9, "bold"), bg=WHITE, fg="#667eea")
        _selected_count.pack(anchor="w")

    _strat_info_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 9),
                                bg=WHITE, fg=MIDGREY)
    _strat_info_lbl.pack(anchor="w", pady=(4, 0))

    # WHY (Hotfix): Show exit strategy, entry TF, and filters on a
    #      second line so the user can verify before running validation.
    # CHANGED: April 2026 — Hotfix
    global _strat_detail_lbl
    _strat_detail_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 8),
                                  bg=WHITE, fg="#888")
    _strat_detail_lbl.pack(anchor="w", pady=(0, 0))

    _prev_result_lbl = tk.Label(sel_frame, text="", font=("Segoe UI", 9, "italic"),
                                 bg=WHITE, fg=GREY)
    _prev_result_lbl.pack(anchor="w", pady=(2, 0))

    _update_strat_info()

    # ── Settings ──────────────────────────────────────────────────────────────
    settings_frame = tk.Frame(scroll_frame, bg=WHITE, padx=20, pady=12)
    settings_frame.pack(fill="x", padx=20, pady=(0, 5))

    tk.Label(settings_frame, text="Settings", font=("Segoe UI", 11, "bold"),
             bg=WHITE, fg=DARK).pack(anchor="w", pady=(0, 8))

    def _field(parent, label, default, width=7):
        var = tk.StringVar(value=default)
        tk.Label(parent, text=label, font=("Segoe UI", 9), bg=WHITE, fg=DARK
                 ).pack(side=tk.LEFT, padx=(0, 3))
        tk.Entry(parent, textvariable=var, width=width).pack(side=tk.LEFT, padx=(0, 15))
        return var

    # Walk-forward row
    wf_row = tk.Frame(settings_frame, bg=WHITE)
    wf_row.pack(fill="x", pady=2)
    tk.Label(wf_row, text="Walk-Forward:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)
    _train_var   = _field(wf_row, "Train years:", "3", 4)
    _test_var    = _field(wf_row, "Test years:", "1", 4)
    _windows_var = _field(wf_row, "Windows:", "20", 4)
    _recent_first_var = tk.BooleanVar(value=False)
    tk.Checkbutton(wf_row, text="Recent first", variable=_recent_first_var,
                   bg=WHITE, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(10, 0))

    # Custom windows row
    custom_row = tk.Frame(settings_frame, bg=WHITE)
    custom_row.pack(fill="x", pady=2)
    tk.Label(custom_row, text="Custom Windows:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)

    _custom_windows_var = tk.StringVar(value="")
    tk.Entry(custom_row, textvariable=_custom_windows_var, width=60,
             font=("Consolas", 8)).pack(side=tk.LEFT, padx=5)

    tk.Label(custom_row, text="(e.g. 2018-2020>2021, 2022-2024>2025)",
             font=("Segoe UI", 7, "italic"), bg=WHITE, fg=GREY).pack(side=tk.LEFT)

    # Monte Carlo row
    mc_row = tk.Frame(settings_frame, bg=WHITE)
    mc_row.pack(fill="x", pady=2)
    tk.Label(mc_row, text="Monte Carlo:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)
    _sims_var = _field(mc_row, "Simulations:", "500", 6)

    # Phase 69 Fix 27: slippage levels entry
    global _slip_levels_var
    _slip_levels_var = tk.StringVar(value="0,1,2,3,5")
    slip_row = tk.Frame(settings_frame, bg=WHITE)
    slip_row.pack(fill="x", pady=2)
    tk.Label(slip_row, text="Slip levels (pips):",
             font=("Segoe UI", 9), bg=WHITE, fg=DARK,
             width=18, anchor="w").pack(side="left")
    tk.Entry(slip_row, textvariable=_slip_levels_var,
             width=20, font=("Segoe UI", 9)).pack(side="left")
    tk.Label(slip_row, text="comma-sep, e.g. 0,1,2,5,10",
             font=("Segoe UI", 8), bg=WHITE, fg=GREY).pack(side="left", padx=5)

    # Load firm names from JSON files
    import glob
    prop_dir = os.path.join(project_root, 'prop_firms')
    firm_names_list = []
    global _firm_name_to_id
    _firm_name_to_id = {}  # Reset mapping
    for fp in sorted(glob.glob(os.path.join(prop_dir, '*.json'))):
        try:
            with open(fp, encoding='utf-8') as f:
                fd = json.load(f)
            name = fd.get('firm_name', '?')
            fid  = fd.get('firm_id', '')
            firm_names_list.append(name)
            _firm_name_to_id[name] = fid  # Build name -> id mapping
        except:
            pass

    _mc_firm_var = tk.StringVar(value=firm_names_list[0] if firm_names_list else "FTMO")
    tk.Label(mc_row, text="Target firm:", font=("Segoe UI", 9), bg=WHITE, fg=DARK
             ).pack(side=tk.LEFT, padx=(0, 3))
    ttk.Combobox(mc_row, textvariable=_mc_firm_var,
                 values=firm_names_list if firm_names_list else ["FTMO"],
                 state="readonly", width=18).pack(side=tk.LEFT, padx=5)

    # Add Stage dropdown
    tk.Label(mc_row, text="Stage:", font=("Segoe UI", 9), bg=WHITE, fg=DARK
             ).pack(side=tk.LEFT, padx=(15, 2))
    _stage_var = tk.StringVar(value="Funded")
    ttk.Combobox(mc_row, textvariable=_stage_var,
                 values=["Evaluation", "Funded"], width=12,
                 state="readonly").pack(side=tk.LEFT, padx=5)

    # DD info label
    _val_dd_info = tk.Label(mc_row, text="", font=("Segoe UI", 8), bg=WHITE, fg="#888")
    _val_dd_info.pack(side=tk.LEFT, padx=(15, 0))

    # Common row
    com_row = tk.Frame(settings_frame, bg=WHITE)
    com_row.pack(fill="x", pady=2)
    tk.Label(com_row, text="Common:", font=("Segoe UI", 9, "bold"),
             bg=WHITE, fg=DARK, width=16, anchor="w").pack(side=tk.LEFT)
    _account_var = _field(com_row, "Account size ($):", "100000", 9)
    _spread_var  = _field(com_row, "Spread:", "25", 5)
    _comm_var    = _field(com_row, "Commission:", "0.0", 5)

    # Auto-update settings when firm/stage changes
    def _on_val_firm_change(*_):
        firm = _mc_firm_var.get()
        stage = _stage_var.get().lower()  # "evaluation" or "funded"

        for fp in sorted(glob.glob(os.path.join(prop_dir, '*.json'))):
            try:
                with open(fp, encoding='utf-8') as f:
                    fd = json.load(f)
                if fd.get('firm_name') != firm:
                    continue

                ch = fd['challenges'][0]
                sizes = ch.get('account_sizes', [100000])

                # ── Account size ──────────────────────────────────────────────
                # Use largest size by default (same as refiner)
                if sizes and _account_var.get() not in [str(s) for s in sizes]:
                    _account_var.set(str(sizes[-1]))

                # ── DD info label ─────────────────────────────────────────────
                if stage == "funded":
                    funded = ch.get('funded', {})
                    daily_dd = funded.get('max_daily_drawdown_pct', '?')
                    total_dd = funded.get('max_total_drawdown_pct', '?')
                    dd_type  = funded.get('drawdown_type', 'static')
                else:
                    # Evaluation — use phase 1
                    phases = ch.get('phases', [{}])
                    p1 = phases[0] if phases else {}
                    daily_dd = p1.get('max_daily_drawdown_pct', '?')
                    total_dd = p1.get('max_total_drawdown_pct', '?')
                    dd_type  = p1.get('drawdown_type', 'static')

                try:
                    _val_dd_info.config(
                        text=f"Daily DD: {daily_dd}%  |  Total DD: {total_dd}%  ({dd_type})"
                    )
                except Exception:
                    pass

                # ── Risk% from trading_rules ──────────────────────────────────
                trading_rules = fd.get('trading_rules', [])
                risk_set = False

                if stage in ('evaluation', 'eval'):
                    for rule in trading_rules:
                        if rule.get('type') == 'eval_settings' and rule.get('stage') == 'evaluation':
                            rng = rule.get('parameters', {}).get('risk_pct_range') or \
                                  rule.get('parameters', {}).get('risk_per_trade_pct_range')
                            if rng:
                                _risk_var.set(str(rng[0]))  # lower bound (safer)
                                risk_set = True
                            break
                    if not risk_set:
                        _risk_var.set("1.0")

                else:  # funded
                    for rule in trading_rules:
                        if rule.get('type') == 'funded_accumulate' and rule.get('stage') == 'funded':
                            rng = rule.get('parameters', {}).get('risk_pct_range') or \
                                  rule.get('parameters', {}).get('risk_per_trade_pct_range')
                            if rng:
                                _risk_var.set(str(rng[0]))  # lower bound (safest)
                                risk_set = True
                            break
                    if not risk_set:
                        _risk_var.set("0.5")

                break  # found the right firm, done

            except Exception:
                pass

    _mc_firm_var.trace_add("write", _on_val_firm_change)
    _stage_var.trace_add("write", _on_val_firm_change)
    _on_val_firm_change()  # populate values immediately on panel load

    com_row2 = tk.Frame(settings_frame, bg=WHITE)
    com_row2.pack(fill="x", pady=2)
    tk.Label(com_row2, text="", width=16).pack(side=tk.LEFT)
    _risk_var   = _field(com_row2, "Risk %:", "1.0", 5)
    _sl_var     = _field(com_row2, "SL pips:", "150", 5)
    _pipval_var = _field(com_row2, "Pip value/lot:", "10.0", 5)

    com_row3 = tk.Frame(settings_frame, bg=WHITE)
    com_row3.pack(fill="x", pady=2)
    tk.Label(com_row3, text="", width=16).pack(side=tk.LEFT)
    _pip_size_var = _field(com_row3, "Pip size:", "0.01", 7)

    # Show firm rules reminder
    try:
        from shared.firm_rules_reminder import show_reminder_on_firm_change
        _val_reminder = [None]
        show_reminder_on_firm_change(_mc_firm_var, settings_frame, _val_reminder, _stage_var)
    except Exception as e:
        import traceback
        print("Warning: Could not initialize firm rules reminder")
        traceback.print_exc()

    # ── Buttons + progress ────────────────────────────────────────────────────
    btn_frame = tk.Frame(scroll_frame, bg=BG, pady=8)
    btn_frame.pack(fill="x", padx=20)

    _start_wf_btn = tk.Button(btn_frame, text="Run Walk-Forward Only",
                              command=lambda: _run_multi('wf'),
                              bg="#2d8a4e", fg="white", font=("Segoe UI", 9, "bold"),
                              relief=tk.FLAT, cursor="hand2", padx=12, pady=7)
    _start_wf_btn.pack(side=tk.LEFT, padx=(0, 6))

    _start_mc_btn = tk.Button(btn_frame, text="Run Monte Carlo Only",
                              command=lambda: _run_multi('mc'),
                              bg="#764ba2", fg="white", font=("Segoe UI", 9, "bold"),
                              relief=tk.FLAT, cursor="hand2", padx=12, pady=7)
    _start_mc_btn.pack(side=tk.LEFT, padx=(0, 6))

    _start_full_btn = tk.Button(btn_frame, text="Run Full Validation",
                                command=lambda: _run_multi('full'),
                                bg="#667eea", fg="white", font=("Segoe UI", 10, "bold"),
                                relief=tk.FLAT, cursor="hand2", padx=16, pady=7)
    _start_full_btn.pack(side=tk.LEFT, padx=(0, 6))

    _start_slip_btn = tk.Button(btn_frame, text="Slippage Stress Test",
                                command=lambda: _run_multi('slip'),
                                bg="#e67e00", fg="white", font=("Segoe UI", 9, "bold"),
                                relief=tk.FLAT, cursor="hand2", padx=12, pady=7)
    _start_slip_btn.pack(side=tk.LEFT, padx=(0, 6))

    _stop_btn = tk.Button(btn_frame, text="Stop",
                          command=_stop,
                          bg=RED, fg="white", font=("Segoe UI", 9, "bold"),
                          relief=tk.FLAT, cursor="hand2", padx=12, pady=7,
                          state="disabled")
    _stop_btn.pack(side=tk.LEFT)

    _progress_bar = ttk.Progressbar(scroll_frame, mode='determinate', length=400)
    _progress_bar.pack(fill="x", padx=20, pady=(4, 0))

    _status_lbl = tk.Label(scroll_frame, text="Ready",
                            font=("Segoe UI", 9, "italic"), bg=BG, fg=GREY)
    _status_lbl.pack(pady=(2, 5))

    # ── Copy Results button ──
    # WHY: Easy way to export validation results as text for sharing/review.
    # CHANGED: April 2026
    global _copy_results_btn
    _copy_results_btn = tk.Button(
        scroll_frame, text="📋 Copy Results to Clipboard",
        font=("Segoe UI", 9), bg="#17a2b8", fg="white",
        relief=tk.FLAT, padx=10, pady=4,
        command=_copy_validation_results,
    )
    _copy_results_btn.pack(anchor="w", padx=20, pady=(5, 0))

    # ── Results frames ────────────────────────────────────────────────────────
    # Separator
    tk.Frame(scroll_frame, bg="#c0c0c0", height=1).pack(fill="x", padx=20, pady=8)

    _wf_frame      = tk.Frame(scroll_frame, bg=BG)
    _wf_frame.pack(fill="x", padx=5, pady=(5, 0))

    tk.Frame(scroll_frame, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=8)

    _mc_frame      = tk.Frame(scroll_frame, bg=BG)
    _mc_frame.pack(fill="x", padx=5)

    tk.Frame(scroll_frame, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=8)

    _slip_frame    = tk.Frame(scroll_frame, bg=BG)
    _slip_frame.pack(fill="x", padx=5)

    tk.Frame(scroll_frame, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=8)

    _live_firm_frame = tk.Frame(scroll_frame, bg=BG)
    _live_firm_frame.pack(fill="x", padx=5)

    tk.Frame(scroll_frame, bg="#c0c0c0", height=1).pack(fill="x", padx=10, pady=8)

    _verdict_frame = tk.Frame(scroll_frame, bg=BG)
    _verdict_frame.pack(fill="x", padx=5, pady=(0, 20))

    return panel


def refresh():
    global _strategies, _strategy_var
    _load_strategies()
    if _strategy_var is not None and _strategies:
        labels = [s['label'] for s in _strategies]
        if _strategy_var.get() not in labels:
            _strategy_var.set(labels[0])
        _update_strat_info()

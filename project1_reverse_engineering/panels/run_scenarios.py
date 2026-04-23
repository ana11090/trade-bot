"""
Run Scenarios Panel for Project 1 - Reverse Engineering
Execute individual steps or run all scenarios
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import os
import sys
import threading

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

import state
from helpers import make_copyable

# Module-level variable to store data status frame
_data_status_frame = None

# WHY (Phase 56 Fix 3): run_btn.configure(state="disabled") handles most
#      double-clicks, but if run_btn=None (caller didn't pass it) two
#      concurrent background threads could start, interleaving output.
#      Add a module-level flag so the guard works regardless of whether
#      the button reference was passed in.
# CHANGED: April 2026 — Phase 56 Fix 3 — module-level running flag
#          (audit Part D HIGH #90)
import threading as _threading
_running = False
_running_lock = _threading.Lock()
# WHY (Phase 57 Fix 5): sys.stdout redirect is global — daemon threads that
#      print during a step's redirect window lose their output. Serialise
#      the redirect with a second lock.
# CHANGED: April 2026 — Phase 57 Fix 5 — stdout redirect lock (#91)
_stdout_lock = _threading.Lock()

# WHY (Phase 49 Fix 4b): Module-level step1 run cache for persistent
#      run tracking across button clicks. Keyed by output_dir so
#      re-clicks within the same session reuse existing aligned_trades.csv.
# CHANGED: April 2026 — Phase 49 Fix 4b — persistent run flags
#          (audit Part D HIGH #90)
_step1_run_cache = {}

# WHY: run_in_background() is module-level (not a closure over build_run_scenarios_panel).
#      Store live StringVar + map references here so the background thread can read
#      the UI dropdowns' current values and force-save them before discovery begins.
# CHANGED: April 2026 — force-save firm/data-source before run
_ui_firm_var = None        # tk.StringVar — current firm name
_ui_stage_var = None       # tk.StringVar — current stage
_ui_acct_var = None        # tk.StringVar — current account size
_ui_data_source_var = None # tk.StringVar — current data source display name
_ui_firm_map = {}          # firm_name → firm dict
_ui_source_map = {}        # source display name → {id, path}
_ui_margin_cap_var = None  # tk.BooleanVar — margin cap checkbox


def build_panel(parent):
    global _data_status_frame
    """Build the run scenarios panel"""
    panel = tk.Frame(parent, bg="#f0f2f5")

    # Title
    title_frame = tk.Frame(panel, bg="white", pady=20)
    title_frame.pack(fill="x", padx=20, pady=(20, 10))

    tk.Label(title_frame, text="🚀 Run Scenarios",
             bg="white", fg="#16213e",
             font=("Segoe UI", 18, "bold")).pack()

    tk.Label(title_frame, text="Execute reverse engineering pipeline for different timeframes",
             bg="white", fg="#666",
             font=("Segoe UI", 11)).pack(pady=(5, 0))

    # Main content
    content_frame = tk.Frame(panel, bg="#f0f2f5")
    content_frame.pack(fill="both", expand=True, padx=20, pady=10)

    # ── Left column: scrollable container ────────────────────────────────
    # WHY: Left column content (regime filter + SRM + scenarios + steps +
    #      discovery settings) exceeds ~1000px and was getting cut off on
    #      smaller windows. Wrap in a Canvas + Scrollbar so the column
    #      scrolls independently. left_frame is kept as the inner
    #      scrollable Frame so all existing pack() calls against
    #      left_frame continue to work unchanged.
    _left_container = tk.Frame(content_frame, bg="white")
    _left_container.pack(side="left", fill="both", expand=True, padx=(0, 10))

    _left_canvas = tk.Canvas(_left_container, bg="white", highlightthickness=0)
    _left_scrollbar = tk.Scrollbar(
        _left_container, orient="vertical", command=_left_canvas.yview
    )
    _left_canvas.configure(yscrollcommand=_left_scrollbar.set)
    _left_scrollbar.pack(side="right", fill="y")
    _left_canvas.pack(side="left", fill="both", expand=True)

    # The old `left_frame` name now refers to the inner scrollable frame
    # that lives inside the Canvas. All downstream pack() calls on
    # left_frame work exactly the same.
    left_frame = tk.Frame(_left_canvas, bg="white", padx=20, pady=20)
    _left_canvas_window = _left_canvas.create_window(
        (0, 0), window=left_frame, anchor="nw"
    )

    def _left_on_frame_configure(_e):
        # Keep scrollregion synced with inner frame height
        _left_canvas.configure(scrollregion=_left_canvas.bbox("all"))
    left_frame.bind("<Configure>", _left_on_frame_configure)

    def _left_on_canvas_configure(event):
        # Make inner frame width match canvas width so fill="x" packing works
        _left_canvas.itemconfigure(_left_canvas_window, width=event.width)
    _left_canvas.bind("<Configure>", _left_on_canvas_configure)

    # ── Mousewheel support (hover-based) ─────────────────────────────────
    # bind_all catches wheel events on every descendant widget while the
    # pointer is over the left column. Unbind on leave so the right
    # column (or other panels) aren't affected.
    def _left_on_mousewheel(event):
        try:
            _left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass
    def _left_bind_wheel(_e):
        _left_canvas.bind_all("<MouseWheel>", _left_on_mousewheel)
        _left_canvas.bind_all("<Button-4>",
                              lambda e: _left_canvas.yview_scroll(-1, "units"))
        _left_canvas.bind_all("<Button-5>",
                              lambda e: _left_canvas.yview_scroll(1, "units"))
    def _left_unbind_wheel(_e):
        _left_canvas.unbind_all("<MouseWheel>")
        _left_canvas.unbind_all("<Button-4>")
        _left_canvas.unbind_all("<Button-5>")
    _left_container.bind("<Enter>", _left_bind_wheel)
    _left_container.bind("<Leave>", _left_unbind_wheel)

    tk.Label(left_frame, text="📊 Select Scenarios to Run",
             bg="white", fg="#16213e",
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 15))

    # WHY (Phase A.36.1): Regime Filter section was originally placed
    #      between the Steps display and the Discovery Settings card
    #      per the A.36 spec. In practice that y-position fell below the
    #      fold on typical window sizes (content in left_frame exceeds
    #      ~900px, and left_frame has no scrollbar), so the card was
    #      invisible to users. Relocated to the very top of left_frame
    #      — immediately under the "Select Scenarios to Run" title and
    #      above the scenario checkboxes — so it is always on-screen.
    #      The section's internal logic (auto-save, visibility toggling,
    #      config keys) is unchanged.
    # CHANGED: April 2026 — Phase A.36.1 — move to top of left_frame
    # WHY (Phase A.36): Config load moved UP from its original location
    #      inside the Discovery Settings card (below) to here, so that
    #      the Regime Filter section (which comes before Discovery
    #      Settings on screen) can read/write config via _cfg and _cl.
    #      Both sections now share the same module import and loaded
    #      dict. No behavior change in Discovery Settings.
    # CHANGED: April 2026 — Phase A.36
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    import config_loader as _cl
    _cfg = _cl.load()

    # WHY (Phase A.40a): Global auto-save toggle for the rule library.
    #      Sits at the very top of the Run Scenarios panel — above the
    #      regime filter card — because it controls behavior shared by
    #      Step 3, Step 4, and Mode A. When ON (default), every
    #      discovered rule is appended to saved_rules.json (the same
    #      file used by the Saved Rules panel and the backtester);
    #      duplicates are deduped by structural hash so a rerun on
    #      identical data adds nothing new. When OFF, the three
    #      discovery paths still write their own per-run JSON files
    #      but don't touch saved_rules.json.
    # CHANGED: April 2026 — Phase A.40a
    _a40a_frame = tk.Frame(left_frame, bg="#f5f5fa", padx=15, pady=10)
    _a40a_frame.pack(fill="x", pady=(0, 10))

    _a40a_var = tk.BooleanVar(
        value=str(_cfg.get('auto_save_discovered_rules', 'true')).lower() == 'true'
    )

    _a40a_cb = tk.Checkbutton(
        _a40a_frame,
        text="💾 Auto-save discovered rules to library",
        variable=_a40a_var,
        bg="#f5f5fa", fg="#16213e",
        font=("Segoe UI", 10, "bold"),
        activebackground="#f5f5fa",
        anchor="w",
    )
    _a40a_cb.pack(anchor="w")

    def _a40a_on_toggle(*_):
        try:
            _cl.save({
                'auto_save_discovered_rules': 'true' if _a40a_var.get() else 'false'
            })
        except Exception as _e:
            print(f"[A.40a] could not save auto_save_discovered_rules: {_e}")

    _a40a_var.trace_add('write', _a40a_on_toggle)

    # ── Prop Firm Target ────────────────────────────────────────────────────
    # WHY: Firm determines leverage (for lot sizing), risk%, DD limits, and
    #      account size. Saved in discovery_settings so every rule carries
    #      the firm info to P2 backtest and EA generator.
    # CHANGED: April 2026 — prop firm in P1 Run Scenarios
    import json as _pf_json
    import glob as _pf_glob
    _pf_prop_dir = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', 'prop_firms'))
    _pf_firm_names = ["None"]
    _pf_firm_map = {}
    for _pf_fp in sorted(_pf_glob.glob(os.path.join(_pf_prop_dir, '*.json'))):
        try:
            with open(_pf_fp, encoding='utf-8') as _pf_ff:
                _pf_fd = _pf_json.load(_pf_ff)
            _pf_name = _pf_fd.get('firm_name', '?')
            _pf_firm_names.append(_pf_name)
            _pf_firm_map[_pf_name] = _pf_fd
        except Exception:
            pass

    _pf_frame = tk.LabelFrame(left_frame, text="Prop Firm Target",
                              font=("Segoe UI", 11, "bold"), bg="#ffffff", fg="#333",
                              padx=15, pady=10)
    _pf_frame.pack(fill="x", pady=(0, 10))

    _pf_row1 = tk.Frame(_pf_frame, bg="#ffffff")
    _pf_row1.pack(fill="x")
    tk.Label(_pf_row1, text="Firm:", font=("Segoe UI", 10, "bold"),
             bg="#ffffff", fg="#333", width=8, anchor="w").pack(side=tk.LEFT)
    _pf_firm_var = tk.StringVar(value=_cfg.get('prop_firm_name', '') or "None")
    _pf_firm_combo = ttk.Combobox(_pf_row1, textvariable=_pf_firm_var,
                                  values=_pf_firm_names, state="readonly", width=22)
    _pf_firm_combo.pack(side=tk.LEFT, padx=5)
    tk.Label(_pf_row1, text="Stage:", font=("Segoe UI", 10, "bold"),
             bg="#ffffff", fg="#333").pack(side=tk.LEFT, padx=(10, 0))
    _pf_stage_var = tk.StringVar(value=_cfg.get('prop_firm_stage', 'Evaluation'))
    ttk.Combobox(_pf_row1, textvariable=_pf_stage_var,
                 values=["Evaluation", "Funded"], state="readonly", width=12).pack(side=tk.LEFT, padx=5)

    _pf_row2 = tk.Frame(_pf_frame, bg="#ffffff")
    _pf_row2.pack(fill="x", pady=(4, 0))
    tk.Label(_pf_row2, text="Account:", font=("Segoe UI", 10, "bold"),
             bg="#ffffff", fg="#333", width=8, anchor="w").pack(side=tk.LEFT)
    _pf_acct_var = tk.StringVar(value=_cfg.get('prop_firm_account', '10000'))
    _pf_acct_combo = ttk.Combobox(_pf_row2, textvariable=_pf_acct_var,
                                  values=["10000"], state="readonly", width=10)
    _pf_acct_combo.pack(side=tk.LEFT, padx=5)

    # WHY: Firm's theoretical risk (0.8%) may exceed what margin allows.
    #      When checked, auto-calculates the real achievable risk using
    #      latest price from data source, leverage, account, and contract size.
    #      When unchecked, old behavior preserved (firm's theoretical risk).
    # CHANGED: April 2026 — margin-capped risk checkbox
    _margin_cap_var = tk.BooleanVar(value=True)
    _margin_cap_cb = tk.Checkbutton(_pf_row2, text="Margin-Cap Risk",
        variable=_margin_cap_var, font=("Segoe UI", 9, "bold"),
        bg="#ffffff", fg="#333", selectcolor="#ffffff",
        command=lambda: _pf_on_change())
    _margin_cap_cb.pack(side=tk.LEFT, padx=(10, 0))

    _pf_info_lbl = tk.Label(_pf_frame, text="", font=("Segoe UI", 9),
                            bg="#ffffff", fg="#555", justify="left")
    _pf_info_lbl.pack(anchor="w", pady=(6, 0))

    def _pf_on_change(*_):
        fname = _pf_firm_var.get()
        stage = _pf_stage_var.get().lower()
        if fname == "None" or fname not in _pf_firm_map:
            _pf_info_lbl.config(text="No firm \u2014 backtest uses conservative margin defaults", fg="#999")
            try:
                _cl.save({'prop_firm_id': '', 'prop_firm_name': '',
                          'prop_firm_stage': _pf_stage_var.get(),
                          'prop_firm_account': _pf_acct_var.get()})
            except Exception:
                pass
            return
        fd = _pf_firm_map[fname]
        # Update account sizes
        try:
            challenge = fd.get('challenges', [{}])[0]
            sizes = challenge.get('account_sizes', [10000])
            _pf_acct_combo['values'] = [str(s) for s in sizes]
            if _pf_acct_var.get() not in [str(s) for s in sizes]:
                _pf_acct_var.set(str(sizes[0]))
        except Exception:
            pass
        # Get leverage
        _lev, _inst = '?', '?'
        try:
            from shared.prop_firm_engine import get_leverage_for_symbol, get_instrument_type
            _sym = _cfg.get('symbol', 'XAUUSD')
            _lev = get_leverage_for_symbol(fd, _sym)
            _inst = get_instrument_type(_sym)
        except Exception:
            pass
        # WHY: Read pip_value, spread, commission from firm JSON's
        #      instrument_specs. These are broker-confirmed values
        #      from diagnostic_compare.mq5.
        # CHANGED: April 2026 — broker specs from firm JSON
        try:
            _inst_specs = fd.get('instrument_specs', {}).get(_sym, {})
            if _inst_specs:
                _cl.save({
                    'pip_value_per_lot': str(_inst_specs.get('pip_value_per_lot', 1.0)),
                    'spread': str(_inst_specs.get('typical_spread', 25.0)),
                    'commission': str(_inst_specs.get('commission_per_lot', 0.0)),
                    'contract_size': str(_inst_specs.get('contract_size', 100.0)),
                })
        except Exception:
            pass
        # Get DD limits and target
        _dd_daily, _dd_total, _target = '?', '?', '?'
        try:
            challenge = fd.get('challenges', [{}])[0]
            if stage == 'evaluation':
                phase = challenge.get('phases', [{}])[0]
                _dd_daily = phase.get('max_daily_drawdown_pct', '?')
                _dd_total = phase.get('max_total_drawdown_pct', '?')
                _target = phase.get('profit_target_pct', '?')
            else:
                funded = challenge.get('funded', {})
                _dd_daily = funded.get('max_daily_drawdown_pct', '?')
                _dd_total = funded.get('max_total_drawdown_pct', '?')
                _target = None
        except Exception:
            pass
        # Get risk from trading rules
        _risk = 1.0
        for rule in fd.get('trading_rules', []):
            if rule.get('stage') not in (stage, 'both'):
                continue
            params = rule.get('parameters', {})
            if rule.get('type') in ('eval_settings', 'eval_strategy'):
                _risk = params.get('risk_pct_range', [0.8, 1.5])[0]
                break
            elif rule.get('type') == 'funded_accumulate':
                _risk = params.get('risk_pct', params.get('risk_pct_range', [0.3, 0.5])[1])
                break
        # WHY: When margin-cap checkbox is ON, calculate the real
        #      achievable risk from leverage, account, latest price,
        #      contract size, and a reference SL. Save the effective
        #      value so backtest/validator/EA all use the same number.
        #      When OFF, save the firm's theoretical risk (old behavior).
        # CHANGED: April 2026 — margin-capped risk
        _effective_risk = _risk
        _margin_note = ""
        if _margin_cap_var.get():
            try:
                _mc_account = float(_pf_acct_var.get())
                _mc_leverage = float(_lev) if _lev != '?' else 0
                _mc_contract = float(_inst_specs.get('contract_size', 100.0)) if _inst_specs else 100.0
                _mc_pip_value = float(_inst_specs.get('pip_value_per_lot', 1.0)) if _inst_specs else 1.0
                _mc_default_sl = 150.0
                _mc_price = 0.0

                # Read latest price from selected data source
                try:
                    _mc_ds_name = _data_source_var.get() if '_data_source_var' in dir() else ''
                    _mc_ds_path = ''
                    if _mc_ds_name:
                        from shared.data_sources import list_sources
                        for _src in list_sources():
                            if _src['name'] == _mc_ds_name:
                                _mc_ds_path = _src['path']
                                break
                    if not _mc_ds_path:
                        _mc_ds_path = _cl.load().get('data_source_path', '')
                    if _mc_ds_path:
                        import glob, csv
                        _mc_h1_files = glob.glob(os.path.join(_mc_ds_path, '*H1*'))
                        if not _mc_h1_files:
                            _mc_h1_files = glob.glob(os.path.join(_mc_ds_path, '*h1*'))
                        if _mc_h1_files:
                            with open(_mc_h1_files[0], 'r') as _mcf:
                                _reader = csv.reader(_mcf)
                                _header = next(_reader)
                                _close_idx = None
                                for _ci, _ch in enumerate(_header):
                                    if _ch.strip().lower() in ('close', 'close_price'):
                                        _close_idx = _ci
                                        break
                                if _close_idx is not None:
                                    _last_row = None
                                    for _last_row in _reader:
                                        pass
                                    if _last_row:
                                        _mc_price = float(_last_row[_close_idx])
                except Exception as _price_e:
                    print(f"[MARGIN-CAP] Could not read price: {_price_e}")

                if _mc_price <= 0:
                    _mc_price = 2500.0  # Safe fallback for XAUUSD

                if _mc_leverage > 0 and _mc_account > 0:
                    _mc_margin_per_lot = (_mc_price * _mc_contract) / _mc_leverage
                    _mc_max_lots = (_mc_account * 0.95) / _mc_margin_per_lot
                    _mc_max_risk_dollars = _mc_max_lots * _mc_default_sl * _mc_pip_value
                    _mc_max_risk_pct = _mc_max_risk_dollars / _mc_account * 100.0
                    _mc_max_risk_pct = round(max(0.1, _mc_max_risk_pct), 1)

                    if _mc_max_risk_pct < _risk:
                        _effective_risk = _mc_max_risk_pct
                        _margin_note = (f" (margin-capped from {_risk}% → "
                                       f"{_effective_risk}% | price=${_mc_price:,.0f} "
                                       f"SL={int(_mc_default_sl)}pip)")
                        print(f"[MARGIN-CAP] {_risk}% → {_effective_risk}% "
                              f"(price=${_mc_price:,.0f}, margin/lot=${_mc_margin_per_lot:,.0f}, "
                              f"max_lots={_mc_max_lots:.3f})")
                    else:
                        _margin_note = " (margin OK)"
                        print(f"[MARGIN-CAP] {_risk}% fits within margin (max {_mc_max_risk_pct}%)")
            except Exception as _mc_e:
                print(f"[MARGIN-CAP] Error: {_mc_e}")

        parts = [f"Leverage: 1:{_lev} ({_inst})", f"Risk: {_effective_risk}%{_margin_note}"]
        if _target:
            parts.append(f"Target: {_target}%")
        parts.append(f"DD: {_dd_daily}%/{_dd_total}%")
        _pf_info_lbl.config(text="  |  ".join(parts), fg="#333")
        try:
            _save_data = {
                'prop_firm_id': fd.get('firm_id', ''),
                'prop_firm_name': fname,
                'prop_firm_stage': _pf_stage_var.get(),
                'prop_firm_account': _pf_acct_var.get(),
                'prop_firm_leverage': str(_lev) if _lev != '?' else '0',
                # WHY: Save both firm risk and effective risk. All downstream
                #      components (backtest, validator, EA) read risk_pct.
                # CHANGED: April 2026 — save risk to config
                'risk_pct': str(_effective_risk),
                'risk_pct_firm': str(_risk),
                'dd_daily_pct': str(_dd_daily),
                'dd_total_pct': str(_dd_total),
                'margin_cap_enabled': '1' if _margin_cap_var.get() else '0',
            }
            _cl.save(_save_data)
            _verify = _cl.load()
            if _verify.get('prop_firm_name') != fname:
                print(f"[FIRM] WARNING: Save failed! Wanted '{fname}' but config has '{_verify.get('prop_firm_name')}'")
            else:
                print(f"[FIRM] Saved: {fname} (verified on disk)")
        except Exception as _e:
            print(f"[FIRM] ERROR saving firm: {_e}")
        print(f"[FIRM] Selected: {fname} | Stage: {_pf_stage_var.get()} | "
              f"Account: ${_pf_acct_var.get()} | Risk: {_risk}% | Leverage: 1:{_lev}")

    _pf_firm_var.trace_add("write", _pf_on_change)
    _pf_stage_var.trace_add("write", _pf_on_change)
    _pf_acct_var.trace_add("write", _pf_on_change)
    _pf_on_change()

    # ── Data Source Selector ──────────────────────────────────────────
    # WHY: Different brokers have different data. Rules must be trained
    #      on the SAME data they'll trade on.
    # CHANGED: April 2026 — data source selector
    data_frame = tk.LabelFrame(left_frame, text="📊 Historical Data Source",
                                font=("Segoe UI", 11, "bold"),
                                bg="#ffffff", fg="#333", padx=15, pady=10)
    data_frame.pack(fill="x", pady=(0, 10))

    from shared.data_sources import list_sources, import_data_source

    _sources = list_sources()
    _source_names = [s['name'] for s in _sources] or ['No data sources found']
    _source_map = {s['name']: s for s in _sources}

    _data_source_var = tk.StringVar()
    # Set from P1 config
    _p1_data_source = _cfg.get('data_source_id', 'original')
    for s in _sources:
        if s['id'] == _p1_data_source:
            _data_source_var.set(s['name'])
            break
    if not _data_source_var.get() and _source_names:
        _data_source_var.set(_source_names[0])

    _ds_row = tk.Frame(data_frame, bg="#ffffff")
    _ds_row.pack(fill="x")
    tk.Label(_ds_row, text="Source:", font=("Segoe UI", 10, "bold"),
             bg="#ffffff", fg="#333", width=8, anchor="w").pack(side=tk.LEFT)
    _ds_dropdown = tk.OptionMenu(_ds_row, _data_source_var, *_source_names)
    _ds_dropdown.config(font=("Segoe UI", 10), width=30, bg="#ffffff")
    _ds_dropdown.pack(side=tk.LEFT, padx=5)

    _ds_info_label = tk.Label(data_frame, text="", font=("Segoe UI", 9),
                               bg="#ffffff", fg="#666", justify=tk.LEFT)
    _ds_info_label.pack(anchor="w", pady=(5, 0))

    def _on_data_source_changed(*_):
        name = _data_source_var.get()
        src = _source_map.get(name, {})
        if src:
            info = f"  Symbol: {src['symbol']}  |  TFs: {', '.join(src['timeframes'])}"
            info += f"\n  Range: {src['date_range']}  |  Candles: {src['candle_count']:,}"
            if src.get('broker'):
                info += f"\n  Broker: {src['broker']}"
            if src.get('timezone_offset'):
                info += f"  |  Timezone: {src['timezone_offset']}"
            _ds_info_label.config(text=info)
            # Save to P1 config
            try:
                _cfg['data_source_id'] = src['id']
                _cfg['data_source_path'] = src['path']
                # WHY: Must NOT save entire _cfg — it has stale firm name
                #      from build time. Only save the keys that changed.
                # CHANGED: April 2026 — fix firm overwrite bug
                _cl.save({'data_source_id': src['id'],
                          'data_source_path': src['path']})

                # WHY: Old indicator cache was built from different data.
                #      Must delete so step2 recomputes from new source.
                # CHANGED: April 2026 — clear cache on source change
                import glob
                _cache_dir = src.get('path', '')
                if _cache_dir:
                    for _cache in glob.glob(os.path.join(_cache_dir, '.cache_*')):
                        try:
                            os.remove(_cache)
                        except Exception:
                            pass
                # Also clear cache in legacy data/ folder
                _legacy_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'data')
                for _cache in glob.glob(os.path.join(_legacy_dir, '.cache_*')):
                    try:
                        os.remove(_cache)
                    except Exception:
                        pass
            except Exception:
                pass
        # WHY: Data source change affects latest price → margin cap recalc.
        # CHANGED: April 2026 — recalc margin on data source change
        try:
            _pf_on_change()
        except Exception:
            pass

    _data_source_var.trace_add("write", _on_data_source_changed)
    _on_data_source_changed()  # show info for initial selection

    # WHY: Expose live StringVars at module level so run_in_background()
    #      (module-level, not a closure here) can read & force-save them.
    # CHANGED: April 2026 — force-save firm/data-source before run
    global _ui_firm_var, _ui_stage_var, _ui_acct_var, _ui_data_source_var
    global _ui_firm_map, _ui_source_map, _ui_margin_cap_var
    _ui_firm_var = _pf_firm_var
    _ui_stage_var = _pf_stage_var
    _ui_acct_var = _pf_acct_var
    _ui_data_source_var = _data_source_var
    _ui_firm_map = _pf_firm_map
    _ui_source_map = _source_map
    _ui_margin_cap_var = _margin_cap_var

    # Import button
    def _import_new_source():
        from tkinter import filedialog, simpledialog, messagebox
        folder = filedialog.askdirectory(title="Select folder with CSV candle files")
        if not folder:
            return
        name = simpledialog.askstring("Data Source Name",
            "Name for this data source (e.g. 'Get Leveraged'):",
            parent=panel)
        if not name:
            return
        source_id = name.lower().replace(' ', '_').replace('-', '_')
        broker = simpledialog.askstring("Broker Name",
            "Which broker is this data from? (optional):",
            parent=panel) or ''

        result = import_data_source(folder, source_id, display_name=name, broker=broker)
        messagebox.showinfo("Imported",
            f"Imported {result['files_copied']} CSV files as '{name}'.\n\n"
            f"Saved to: {result['path']}")

        # Refresh dropdown
        _sources.clear()
        _sources.extend(list_sources())
        _source_map.clear()
        for s in _sources:
            _source_map[s['name']] = s
        menu = _ds_dropdown['menu']
        menu.delete(0, 'end')
        for s in _sources:
            menu.add_command(label=s['name'],
                           command=lambda v=s['name']: _data_source_var.set(v))
        _data_source_var.set(name)

    # WHY: User needs the MT5 export script to get candle data from
    #      any broker. Put download next to import so workflow is clear:
    #      download script → run on MT5 → import the CSVs
    # CHANGED: April 2026 — MT5 script download button
    def _download_mt5_export():
        import shutil
        _script_src = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'project3_live_trading', 'templates', 'export_candles.mq5')
        if not os.path.exists(_script_src):
            messagebox.showerror("Not Found",
                f"export_candles.mq5 not found at:\n{_script_src}")
            return
        from tkinter import filedialog
        _dst = filedialog.asksaveasfilename(
            title="Save MT5 Export Script",
            defaultextension=".mq5",
            filetypes=[("MQL5 Script", "*.mq5"), ("All files", "*.*")],
            initialfile="export_candles.mq5",
        )
        if _dst:
            shutil.copy2(_script_src, _dst)
            messagebox.showinfo("Saved",
                f"Script saved to:\n{_dst}\n\n"
                "How to use:\n"
                "1. Copy to MT5 → MQL5/Scripts/\n"
                "2. Compile in MetaEditor (F7)\n"
                "3. Open chart for your symbol (e.g. XAUUSD)\n"
                "4. Drag script onto the chart\n"
                "5. CSVs saved in MT5 → MQL5/Files/\n"
                "6. Come back here → Import Data Source")

    _btn_row = tk.Frame(data_frame, bg="#ffffff")
    _btn_row.pack(anchor="w", pady=(8, 0))

    tk.Button(_btn_row, text="📥 Import Data Source",
              command=_import_new_source,
              bg="#17a2b8", fg="white", font=("Segoe UI", 9),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3
              ).pack(side=tk.LEFT)

    tk.Button(_btn_row, text="📤 MT5 Historical Data Export Script",
              command=_download_mt5_export,
              bg="#6c757d", fg="white", font=("Segoe UI", 9),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3
              ).pack(side=tk.LEFT, padx=(8, 0))

    def _compare_sources():
        """Compare two data sources side by side."""
        from shared.data_sources import list_sources
        import pandas as pd
        from tkinter import Toplevel, scrolledtext

        sources = list_sources()
        if len(sources) < 2:
            messagebox.showerror("Need 2 Sources",
                "Import at least 2 data sources to compare.")
            return

        source_names = [s['name'] for s in sources]
        source_map = {s['name']: s for s in sources}

        # WHY: Dropdowns instead of text boxes — user can't mistype.
        # CHANGED: April 2026 — dropdown selection dialog
        _pick_win = Toplevel()
        _pick_win.title("Compare Data Sources")
        _pick_win.geometry("400x180")
        _pick_win.resizable(False, False)
        _pick_win.grab_set()

        tk.Label(_pick_win, text="Select two data sources to compare:",
                 font=("Segoe UI", 11, "bold"), pady=10).pack()

        _row1 = tk.Frame(_pick_win)
        _row1.pack(fill="x", padx=20, pady=5)
        tk.Label(_row1, text="Source A:", font=("Segoe UI", 10), width=10, anchor="w").pack(side=tk.LEFT)
        _src1_var = tk.StringVar(value=source_names[0])
        ttk.Combobox(_row1, textvariable=_src1_var, values=source_names,
                     state="readonly", width=30).pack(side=tk.LEFT, padx=5)

        _row2 = tk.Frame(_pick_win)
        _row2.pack(fill="x", padx=20, pady=5)
        tk.Label(_row2, text="Source B:", font=("Segoe UI", 10), width=10, anchor="w").pack(side=tk.LEFT)
        _src2_var = tk.StringVar(value=source_names[1] if len(source_names) > 1 else source_names[0])
        ttk.Combobox(_row2, textvariable=_src2_var, values=source_names,
                     state="readonly", width=30).pack(side=tk.LEFT, padx=5)

        _result = [None, None]

        def _ok():
            _result[0] = _src1_var.get()
            _result[1] = _src2_var.get()
            _pick_win.destroy()

        def _cancel():
            _pick_win.destroy()

        _btn_row = tk.Frame(_pick_win)
        _btn_row.pack(pady=15)
        tk.Button(_btn_row, text="Compare", command=_ok,
                  bg="#6f42c1", fg="white", font=("Segoe UI", 10, "bold"),
                  padx=20, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(_btn_row, text="Cancel", command=_cancel,
                  font=("Segoe UI", 10), padx=20, pady=5).pack(side=tk.LEFT, padx=5)

        _pick_win.wait_window()

        src1_name = _result[0]
        src2_name = _result[1]
        if not src1_name or not src2_name or src1_name not in source_map or src2_name not in source_map:
            return
        if src1_name == src2_name:
            messagebox.showwarning("Same Source", "Please select two different data sources.")
            return

        src1 = source_map[src1_name]
        src2 = source_map[src2_name]

        win = Toplevel()
        win.title(f"Compare: {src1_name} vs {src2_name}")
        win.geometry("900x700")
        txt = scrolledtext.ScrolledText(win, font=("Courier", 10), wrap="word")
        txt.pack(fill="both", expand=True)

        def log(s):
            txt.insert("end", s + "\n")
            txt.see("end")
            win.update()

        log(f"{'='*70}")
        log(f"DATA SOURCE COMPARISON")
        log(f"  Source A: {src1_name} ({src1['path']})")
        log(f"  Source B: {src2_name} ({src2['path']})")
        log(f"{'='*70}\n")

        symbol = _cfg.get('symbol', 'XAUUSD').upper()

        for tf in ['M5', 'M15', 'H1', 'H4', 'D1']:
            file_a = None
            file_b = None
            for fname in [f'{symbol}_{tf}.csv', f'{symbol.lower()}_{tf}.csv']:
                pa = os.path.join(src1['path'], fname)
                pb = os.path.join(src2['path'], fname)
                if os.path.exists(pa): file_a = pa
                if os.path.exists(pb): file_b = pb

            if not file_a:
                log(f"── {tf}: Source A has no {symbol} file — SKIPPED")
                continue
            if not file_b:
                log(f"── {tf}: Source B has no {symbol} file — SKIPPED")
                continue

            log(f"── {tf} {'─'*55}")

            try:
                df_a = pd.read_csv(file_a, parse_dates=['timestamp'])
                df_b = pd.read_csv(file_b, parse_dates=['timestamp'])
            except Exception as e:
                log(f"  ERROR loading: {e}")
                continue

            log(f"  Source A: {len(df_a):,} candles  ({df_a['timestamp'].min()} to {df_a['timestamp'].max()})")
            log(f"  Source B: {len(df_b):,} candles  ({df_b['timestamp'].min()} to {df_b['timestamp'].max()})")

            start = max(df_a['timestamp'].min(), df_b['timestamp'].min())
            end   = min(df_a['timestamp'].max(), df_b['timestamp'].max())
            log(f"  Overlap:  {start} to {end}")

            df_a_overlap = df_a[(df_a['timestamp'] >= start) & (df_a['timestamp'] <= end)].copy()
            df_b_overlap = df_b[(df_b['timestamp'] >= start) & (df_b['timestamp'] <= end)].copy()
            log(f"  A in overlap: {len(df_a_overlap):,} candles")
            log(f"  B in overlap: {len(df_b_overlap):,} candles")

            if len(df_a_overlap) == 0 or len(df_b_overlap) == 0:
                log(f"  No overlapping data — SKIPPED\n")
                continue

            merged = pd.merge(df_a_overlap, df_b_overlap, on='timestamp',
                              suffixes=('_A', '_B'), how='inner')
            log(f"  Matched timestamps: {len(merged):,}")

            if len(merged) == 0:
                log(f"  ⚠ Zero matches — checking timezone offset...")
                for offset_h in range(-5, 6):
                    df_b_shifted = df_b_overlap.copy()
                    df_b_shifted['timestamp'] = df_b_shifted['timestamp'] + pd.Timedelta(hours=offset_h)
                    m = pd.merge(df_a_overlap, df_b_shifted, on='timestamp', how='inner')
                    if len(m) > 10:
                        log(f"    Offset {offset_h:+d}h: {len(m):,} matches")
                        if len(m) > len(merged):
                            merged = m
                if len(merged) == 0:
                    log(f"  No matches even with offset — SKIPPED\n")
                    continue

            for col in ['open', 'high', 'low', 'close']:
                col_a = f'{col}_A'
                col_b = f'{col}_B'
                if col_a in merged.columns and col_b in merged.columns:
                    diff = (merged[col_a] - merged[col_b]).abs()
                    log(f"  {col.upper():>5} diff: mean={diff.mean():.4f}, "
                        f"max={diff.max():.4f}, exact_match={int((diff < 0.001).sum()):,}/{len(merged):,} "
                        f"({(diff < 0.001).mean()*100:.1f}%)")

            if 'volume_A' in merged.columns and 'volume_B' in merged.columns:
                vol_a = merged['volume_A'].astype(float)
                vol_b = merged['volume_B'].astype(float)
                vol_corr = vol_a.corr(vol_b)
                vol_ratio = (vol_a / vol_b.replace(0, float('nan'))).median()
                log(f"  VOLUME: correlation={vol_corr:.4f}, median_ratio={vol_ratio:.2f}")

            if len(df_a_overlap) != len(df_b_overlap):
                log(f"  ⚠ Different candle counts in overlap — possible timezone mismatch")
                a_only = set(df_a_overlap['timestamp']) - set(df_b_overlap['timestamp'])
                b_only = set(df_b_overlap['timestamp']) - set(df_a_overlap['timestamp'])
                if a_only:
                    sample = sorted(a_only)[:3]
                    log(f"    A has {len(a_only)} extra candles, e.g.: {[str(s) for s in sample]}")
                if b_only:
                    sample = sorted(b_only)[:3]
                    log(f"    B has {len(b_only)} extra candles, e.g.: {[str(s) for s in sample]}")

            try:
                close_a = merged['close_A']
                close_b = merged['close_B']
                sma_a = close_a.rolling(20).mean()
                sma_b = close_b.rolling(20).mean()
                sma_diff = (sma_a - sma_b).dropna().abs()
                log(f"  SMA20 diff: mean={sma_diff.mean():.4f}, max={sma_diff.max():.4f}")
                std_a = close_a.rolling(50).std()
                std_b = close_b.rolling(50).std()
                std_diff = (std_a - std_b).dropna().abs()
                log(f"  STD50 diff: mean={std_diff.mean():.4f}, max={std_diff.max():.4f}")
            except Exception:
                pass

            log("")

        log(f"{'='*70}")
        log(f"SUMMARY")
        log(f"  If prices match closely → rules transfer well between sources")
        log(f"  If volume differs a lot → volume_ratio rules won't transfer")
        log(f"  If timezone differs → all indicator thresholds shift")
        log(f"{'='*70}")

    tk.Button(_btn_row, text="🔍 Compare Data Sources",
              command=_compare_sources,
              bg="#6f42c1", fg="white", font=("Segoe UI", 9),
              relief=tk.FLAT, cursor="hand2", padx=10, pady=3
              ).pack(side=tk.LEFT, padx=(8, 0))

    try:
        from shared.tooltip import add_tooltip as _a40a_tooltip
        _a40a_tooltip(
            _a40a_cb,
            "When ON (default), every rule discovered by Step 3 (decision-tree "
            "extraction), Step 4 (bot-entry discovery), and Mode A (single-rule "
            "discovery) is appended to saved_rules.json — the same library the "
            "Saved Rules panel and the backtester read from.\n\n"
            "Duplicates are detected by a structural hash over the rule's "
            "conditions and prediction (not over source/notes/win-rate), so "
            "rerunning on the same data adds nothing new and the same rule "
            "rediscovered with different settings still collapses to one "
            "library entry.\n\n"
            "When OFF, the three discovery paths still write their own per-run "
            "JSON files (analysis_report.json, bot_entry_rules.json, "
            "single_rule_mode.json) but the shared library is left untouched. "
            "Use OFF when experimenting with discovery hyperparameters and you "
            "don't want the library cluttered."
        )
    except Exception:
        pass

    # WHY (Phase A.36): Regime Filter UI scaffolding. Lives between the
    #      steps display and the Discovery Settings card so the user
    #      sees it before configuring rule discovery — the filter (when
    #      enabled in A.38) acts as a pre-filter on rule discovery and
    #      backtest signals. A.36 only adds the UI controls and the
    #      auto-save plumbing; no filter logic runs yet. The master
    #      checkbox is unchecked by default → the pipeline behaves
    #      exactly as before A.36 until the user opts in.
    #
    #      Architecture decisions (locked in for A.36 / A.37 / A.38):
    #        Decision 1 (which features become filters):  hybrid scan —
    #          analyze_market_regimes() output + top RF features,
    #          deduplicated by correlation                 [A.37]
    #        Decision 2 (threshold selection):  per-feature grid search
    #          with hard floors                            [A.37]
    #        Decision 3 (subset selection):  test all 2^N subsets,
    #          pick by score                               [A.37]
    #        Decision 4 (overfitting controls):  ALL — min trades,
    #          min WR delta, min expectancy delta, train/test split,
    #          WFE warning                                 [A.37]
    #        Decision 5 (where applied):  Step 3 + Backtest (skip
    #          Step 4 — bot_entry_discovery's question is different)
    #                                                      [A.38]
    #        Decision 6 (per-direction):  EMA-like filters check
    #          price>EMA200 for action='BUY' rules, price<EMA200 for
    #          action='SELL' rules                         [A.38]
    #        Decision 7 (rollout):  three phases — A.36 UI, A.37
    #          discovery, A.38 application
    # CHANGED: April 2026 — Phase A.36 — UI scaffolding
    regime_frame = tk.Frame(left_frame, bg="#f0fff4", padx=15, pady=15)
    regime_frame.pack(fill="x", pady=(15, 0))

    tk.Label(regime_frame, text="🎯 Regime Filter (experimental)",
             bg="#f0fff4", fg="#16213e",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

    # ── Master enable checkbox ────────────────────────────────────────────
    # WHY (Phase A.36): tk.BooleanVar wired to trace_add('write', ...) so
    #      every toggle (mouse, keyboard, programmatic) persists to
    #      p1_config.json. Same pattern as A.29.1's StringVar handlers
    #      for the Discovery Settings spinboxes. Initial value comes
    #      from config (default 'false' → unchecked).
    # CHANGED: April 2026 — Phase A.36
    _a36_enabled_var = tk.BooleanVar(
        value=str(_cfg.get('regime_filter_enabled', 'false')).lower() == 'true'
    )

    _a36_enable_cb = tk.Checkbutton(
        regime_frame,
        text="Enable regime filter",
        variable=_a36_enabled_var,
        bg="#f0fff4", fg="#16213e",
        font=("Segoe UI", 10),
        activebackground="#f0fff4",
        anchor="w",
    )
    _a36_enable_cb.pack(anchor="w", pady=(0, 4))

    # ── Container that hides/shows everything below the checkbox ─────────
    # WHY (Phase A.36): Use a single inner Frame so we can pack/pack_forget
    #      it as one unit when the checkbox toggles. Avoids tracking
    #      individual widget visibility.
    # CHANGED: April 2026 — Phase A.36
    _a36_inner = tk.Frame(regime_frame, bg="#f0fff4")
    # NOTE: deliberately not pack()'d yet — _a36_apply_visibility() below
    # will pack it when the checkbox is checked.

    # ── Mode radio buttons (Automatic / Manual) ──────────────────────────
    tk.Label(_a36_inner, text="Filter discovery mode:",
             bg="#f0fff4", fg="#444",
             font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 2))

    _a36_mode_var = tk.StringVar(
        value=str(_cfg.get('regime_filter_mode', 'automatic')).lower()
    )
    # WHY (Phase A.36): Sanity check — if config has anything other than
    #      the two allowed strings, snap to 'automatic'.
    # CHANGED: April 2026 — Phase A.36
    if _a36_mode_var.get() not in ('automatic', 'manual'):
        _a36_mode_var.set('automatic')

    _a36_radio_frame = tk.Frame(_a36_inner, bg="#f0fff4")
    _a36_radio_frame.pack(anchor="w", pady=(0, 4))

    tk.Radiobutton(
        _a36_radio_frame,
        text="Automatic — discover from data",
        variable=_a36_mode_var,
        value='automatic',
        bg="#f0fff4", fg="#16213e",
        font=("Segoe UI", 9),
        activebackground="#f0fff4",
        anchor="w",
    ).pack(anchor="w")

    tk.Radiobutton(
        _a36_radio_frame,
        text="Manual — set thresholds yourself",
        variable=_a36_mode_var,
        value='manual',
        bg="#f0fff4", fg="#16213e",
        font=("Segoe UI", 9),
        activebackground="#f0fff4",
        anchor="w",
    ).pack(anchor="w")

    # ── Strictness preset (Phase A.37.2) ─────────────────────────────────
    # WHY (Phase A.37.2): Three preset modes that control A.37's four
    #      overfitting floors. Conservative keeps trade count high
    #      (~30% survival, single-filter typical). Strict maximises
    #      WR (~10% survival, 3+ filter combinations typical). The
    #      preset is read by analyze.py at scenario run time and
    #      forwarded to discover_regime_filter. Visible only when the
    #      master checkbox is on AND mode is automatic — the inner
    #      frame's pack/forget logic in _a36_apply_visibility shows
    #      this whole block conditionally.
    # CHANGED: April 2026 — Phase A.37.2
    _a372_strictness_frame = tk.Frame(_a36_inner, bg="#f0fff4")
    # NOTE: not pack()'d yet — _a36_apply_visibility() handles it.

    tk.Label(_a372_strictness_frame, text="Filter strictness:",
             bg="#f0fff4", fg="#444",
             font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 2))

    _a372_strictness_var = tk.StringVar(
        value=str(_cfg.get('regime_filter_strictness', 'conservative')).lower()
    )
    if _a372_strictness_var.get() not in ('conservative', 'balanced', 'strict'):
        _a372_strictness_var.set('conservative')

    _a372_radio_frame = tk.Frame(_a372_strictness_frame, bg="#f0fff4")
    _a372_radio_frame.pack(anchor="w", pady=(0, 4))

    tk.Radiobutton(
        _a372_radio_frame,
        text="Conservative — survival ≥30%, typical 1-2 filters",
        variable=_a372_strictness_var,
        value='conservative',
        bg="#f0fff4", fg="#16213e",
        font=("Segoe UI", 9),
        activebackground="#f0fff4",
        anchor="w",
    ).pack(anchor="w")

    tk.Radiobutton(
        _a372_radio_frame,
        text="Balanced — survival ≥20%, typical 2-3 filters",
        variable=_a372_strictness_var,
        value='balanced',
        bg="#f0fff4", fg="#16213e",
        font=("Segoe UI", 9),
        activebackground="#f0fff4",
        anchor="w",
    ).pack(anchor="w")

    tk.Radiobutton(
        _a372_radio_frame,
        text="Strict — survival ≥10%, typical 3-5 filters, higher WR",
        variable=_a372_strictness_var,
        value='strict',
        bg="#f0fff4", fg="#16213e",
        font=("Segoe UI", 9),
        activebackground="#f0fff4",
        anchor="w",
    ).pack(anchor="w")

    # WHY (Phase A.37.2): Auto-save handler for the strictness preset.
    #      Uses default-arg binding to dodge the closure trap (same
    #      pattern as A.29.1 / A.36's other handlers).
    # CHANGED: April 2026 — Phase A.37.2
    def _a372_save_strictness(*_args, _v=_a372_strictness_var):
        try:
            _cl.save({'regime_filter_strictness': str(_v.get())})
        except Exception as _e:
            print(f"[A.37.2] could not save regime_filter_strictness: {_e}")

    _a372_strictness_var.trace_add('write', _a372_save_strictness)

    # WHY (Phase A.38b): Separate checkbox for applying the regime filter
    #      DURING Step 3's rule discovery (not just at backtest time).
    #      Default OFF so Step 3 sees all trades — identical to pre-A.38b.
    #      Grayed out when the main regime filter checkbox is OFF (no
    #      conditions to filter with).
    # CHANGED: April 2026 — Phase A.38b
    try:
        from shared.tooltip import add_tooltip as _a38b_tooltip
    except Exception:
        def _a38b_tooltip(*_args, **_kwargs):
            return None

    _a38b_disc_var = tk.BooleanVar(
        value=str(_cfg.get('regime_filter_at_discovery', 'false')).lower() == 'true'
    )
    _a38b_disc_cb = tk.Checkbutton(
        _a36_inner,
        text="Also apply regime filter during Step 3 rule discovery",
        variable=_a38b_disc_var,
        bg="#f0fff4", fg="#333",
        font=("Segoe UI", 9),
        anchor="w",
        activebackground="#f0fff4",
    )
    _a38b_disc_cb.pack(anchor="w", pady=(8, 0))

    _a38b_tooltip(_a38b_disc_cb, (
        "WHAT IT DOES:\n"
        "When checked, Step 3 (Analyze & Extract Rules) drops all trades "
        "that FAIL the discovered regime filter conditions BEFORE extracting "
        "decision-tree rules. So instead of training on all trades "
        "(mixed good and bad regimes), Step 3 trains only on the trades "
        "that pass the regime filter.\n\n"
        "WHY YOU MIGHT WANT IT:\n"
        "The rules Step 3 discovers from regime-filtered trades will be "
        "DIFFERENT from the ones discovered from all trades. Without this "
        "checkbox, Step 3's rules include conditions that distinguish "
        "good-regime-winners from bad-regime-losers. With this checkbox ON, "
        "the rules focus purely on what separates winners from losers "
        "WITHIN the good regime.\n\n"
        "WHEN TO USE:\n"
        "You've already run with the main regime filter and are happy with "
        "the discovered conditions, and want Step 3 to find rules optimized "
        "FOR that regime.\n\n"
        "WHEN TO LEAVE OFF (default):\n"
        "First run, or when you want Step 3's rules to work universally "
        "with the regime filter as an optional add-on at backtest time.\n\n"
        "INTERACTION:\n"
        "Main regime filter OFF → this checkbox is ignored (grayed out).\n"
        "Main ON + this OFF → Step 3 sees all trades (current behavior).\n"
        "Main ON + this ON → Step 3 sees ONLY regime-passing trades."
    ), wraplength=430)

    def _a38b_save_disc(*_a):
        try:
            _cl.save({
                'regime_filter_at_discovery':
                    'true' if _a38b_disc_var.get() else 'false'
            })
        except Exception as _e:
            print(f"[A.38b] Could not save regime_filter_at_discovery: {_e}")
    _a38b_disc_var.trace_add('write', _a38b_save_disc)

    def _a38b_update_disc_state(*_a):
        try:
            _a38b_disc_cb.config(
                state='normal' if _a36_enabled_var.get() else 'disabled'
            )
        except Exception:
            pass
    _a36_enabled_var.trace_add('write', _a38b_update_disc_state)
    _a38b_update_disc_state()

    # ── Automatic-mode display area — dynamic, rebuilt from config ──────
    # WHY (Phase A.37): A.36 shipped a static placeholder here. A.37 now
    #      has real discovery output in p1_config.json['regime_filter_
    #      discovered']. Render from that on every show so re-runs are
    #      reflected without restarting the app. Rebuild-on-show keeps
    #      the code dumb — no cross-call state tracking needed.
    # CHANGED: April 2026 — Phase A.37 — dynamic auto-frame contents
    _a36_auto_frame = tk.Frame(_a36_inner, bg="#e8f5e9", padx=10, pady=8)
    # NOTE: deliberately not pack()'d yet — _a36_apply_visibility() does it.

    def _a37_render_auto_frame():
        """Clear and rebuild _a36_auto_frame from current config."""
        for _w in list(_a36_auto_frame.winfo_children()):
            try:
                _w.destroy()
            except Exception:
                pass

        # Reload config each call — config_loader.load() is cheap
        try:
            _rf_raw = _cl.load().get('regime_filter_discovered', '') or ''
        except Exception:
            _rf_raw = ''

        _rf = None
        if _rf_raw:
            try:
                import json as _json_a37
                _rf = _json_a37.loads(_rf_raw)
            except Exception:
                _rf = None

        if not _rf:
            tk.Label(
                _a36_auto_frame,
                text="No filter discovered yet.",
                bg="#e8f5e9", fg="#16213e",
                font=("Segoe UI", 9, "bold"),
                wraplength=320, justify="left",
            ).pack(anchor="w")
            tk.Label(
                _a36_auto_frame,
                text="Run the pipeline ('Run Selected Scenarios') with "
                     "this checkbox enabled to discover filters from your "
                     "data.",
                bg="#e8f5e9", fg="#555",
                font=("Segoe UI", 9),
                wraplength=320, justify="left",
            ).pack(anchor="w", pady=(4, 0))
            return

        _status = str(_rf.get('status', '')).lower()
        if _status != 'ok':
            tk.Label(
                _a36_auto_frame,
                text="No filter recommended.",
                bg="#e8f5e9", fg="#16213e",
                font=("Segoe UI", 9, "bold"),
                wraplength=320, justify="left",
            ).pack(anchor="w")
            _msg = _rf.get('message') or (
                'Discovery ran but nothing passed the overfitting floors.'
            )
            tk.Label(
                _a36_auto_frame,
                text=_msg,
                bg="#e8f5e9", fg="#555",
                font=("Segoe UI", 9),
                wraplength=320, justify="left",
            ).pack(anchor="w", pady=(4, 0))
            return

        # status == 'ok' → render the filter set
        _subset = _rf.get('subset') or []
        _metrics = _rf.get('metrics') or {}
        _baseline = _rf.get('baseline') or {}

        # WHY (Phase A.37.2): Show which strictness preset was active
        #      for this discovery so the user can tell at a glance
        #      whether to expect a wide subset or a single filter.
        # CHANGED: April 2026 — Phase A.37.2
        _a372_used = _rf.get('strictness') or 'conservative'
        tk.Label(
            _a36_auto_frame,
            text=f"Discovered filter set ({len(_subset)} rule(s), "
                 f"strictness={_a372_used}):",
            bg="#e8f5e9", fg="#16213e",
            font=("Segoe UI", 9, "bold"),
            wraplength=320, justify="left",
        ).pack(anchor="w")

        for _f in _subset:
            _feat = _f.get('feature', '?')
            _dir  = _f.get('direction', '?')
            _thr  = _f.get('threshold')
            try:
                _thr_s = f"{float(_thr):.4g}"
            except Exception:
                _thr_s = str(_thr)
            tk.Label(
                _a36_auto_frame,
                text=f"  • {_feat} {_dir} {_thr_s}",
                bg="#e8f5e9", fg="#16213e",
                font=("Consolas", 9),
                wraplength=320, justify="left",
            ).pack(anchor="w", pady=(2, 0))

        def _pct(x):
            try:
                return f"{float(x)*100:.1f}%"
            except Exception:
                return "—"

        def _num(x, fmt="{:.2f}"):
            try:
                return fmt.format(float(x))
            except Exception:
                return "—"

        _b_wr  = _pct(_baseline.get('win_rate'))
        _b_exp = _num(_baseline.get('expectancy'), "{:+.2f}")
        _b_n   = _baseline.get('count', '—')
        _m_wr  = _pct(_metrics.get('win_rate'))
        _m_exp = _num(_metrics.get('expectancy'), "{:+.2f}")
        _m_surv= _pct(_metrics.get('survival'))
        _m_n   = _metrics.get('count', '—')

        tk.Label(
            _a36_auto_frame,
            text=(f"Baseline: {_b_n} trades, WR {_b_wr}, "
                  f"expectancy {_b_exp} pips"),
            bg="#e8f5e9", fg="#555",
            font=("Segoe UI", 9),
            wraplength=320, justify="left",
        ).pack(anchor="w", pady=(6, 0))

        tk.Label(
            _a36_auto_frame,
            text=(f"After filter: {_m_n} trades ({_m_surv} survival), "
                  f"WR {_m_wr}, expectancy {_m_exp} pips"),
            bg="#e8f5e9", fg="#16213e",
            font=("Segoe UI", 9, "bold"),
            wraplength=320, justify="left",
        ).pack(anchor="w", pady=(2, 0))

        tk.Label(
            _a36_auto_frame,
            text=("Filter is discovered but not yet applied — application "
                  "comes in Phase A.38."),
            bg="#e8f5e9", fg="#888",
            font=("Segoe UI", 8, "italic"),
            wraplength=320, justify="left",
        ).pack(anchor="w", pady=(6, 0))

    # Render once now so the frame has content before first pack().
    _a37_render_auto_frame()

    # ── Manual-mode display area (placeholder for now) ───────────────────
    _a36_manual_frame = tk.Frame(_a36_inner, bg="#fff3e0", padx=10, pady=8)
    # NOTE: deliberately not pack()'d yet — _a36_apply_visibility() does it.

    tk.Label(
        _a36_manual_frame,
        text="Manual mode: edit filter thresholds directly.",
        bg="#fff3e0", fg="#16213e",
        font=("Segoe UI", 9, "bold"),
        wraplength=320,
        justify="left",
    ).pack(anchor="w")

    tk.Label(
        _a36_manual_frame,
        text="Run Automatic mode at least once to populate sensible "
             "defaults, then switch back here to tune them. Editable "
             "spinboxes will appear in Phase A.38.",
        bg="#fff3e0", fg="#555",
        font=("Segoe UI", 9),
        wraplength=320,
        justify="left",
    ).pack(anchor="w", pady=(4, 0))

    # ── Footer — explicit "no filter applied yet" disclaimer ─────────────
    _a36_footer = tk.Label(
        _a36_inner,
        text="ⓘ UI placeholder only — no filter is applied yet. "
             "Discovery and application come in A.37 and A.38.",
        bg="#f0fff4", fg="#888",
        font=("Segoe UI", 8, "italic"),
        wraplength=320,
        justify="left",
    )
    # NOTE: packed inside _a36_apply_visibility() so it follows the radios.

    # ── Visibility logic ─────────────────────────────────────────────────
    # WHY (Phase A.36): One function controls all show/hide transitions
    #      so the state machine is single-source-of-truth and easy to
    #      reason about. Called on every checkbox toggle and on every
    #      radio change.
    # CHANGED: April 2026 — Phase A.36
    def _a36_apply_visibility(*_args):
        # First, unpack everything inside the inner frame
        # WHY (Phase A.37.2): also forget the strictness frame so it
        #      only appears in Automatic mode.
        # CHANGED: April 2026 — Phase A.37.2
        for w in (_a36_auto_frame, _a36_manual_frame, _a36_footer,
                  _a372_strictness_frame):
            try:
                w.pack_forget()
            except Exception:
                pass

        if not _a36_enabled_var.get():
            # Filter disabled → hide the whole inner block
            try:
                _a36_inner.pack_forget()
            except Exception:
                pass
            return

        # Filter enabled → show inner block + the appropriate mode area
        if not _a36_inner.winfo_ismapped():
            _a36_inner.pack(fill="x", pady=(4, 0))

        _a36_mode = _a36_mode_var.get()
        if _a36_mode == 'manual':
            _a36_manual_frame.pack(fill="x", pady=(4, 4))
        else:
            # WHY (Phase A.37.2): Strictness preset only applies in
            #      Automatic mode — packed before the discovered-filter
            #      readout so the user sees the control they're tuning
            #      above the result.
            # CHANGED: April 2026 — Phase A.37.2
            _a372_strictness_frame.pack(fill="x", pady=(4, 4))
            # WHY (Phase A.37): rebuild auto-frame contents from current
            #      config just before it becomes visible so re-runs of
            #      the pipeline show their new discovery without
            #      restarting the panel.
            # CHANGED: April 2026 — Phase A.37
            try:
                _a37_render_auto_frame()
            except Exception as _e:
                print(f"[A.37] Could not render auto-frame: {_e}")
            _a36_auto_frame.pack(fill="x", pady=(4, 4))

        _a36_footer.pack(anchor="w", pady=(8, 0))

    # ── Auto-save handlers ───────────────────────────────────────────────
    # WHY (Phase A.36): Every change to the checkbox or the radio
    #      persists immediately to p1_config.json. Same pattern as
    #      A.29.1 used for the Discovery Settings spinboxes
    #      (StringVar + trace_add('write', ...) with default-arg
    #      binding to avoid the closure trap).
    # CHANGED: April 2026 — Phase A.36
    def _a36_save_enabled(*_args, _v=_a36_enabled_var):
        try:
            _val = 'true' if _v.get() else 'false'
            _cl.save({'regime_filter_enabled': _val})
            print(f"[A.36] regime_filter_enabled saved as: {_val}")
        except Exception as _e:
            print(f"[A.36] Could not save regime_filter_enabled: {_e}")
        _a36_apply_visibility()

    def _a36_save_mode(*_args, _v=_a36_mode_var):
        try:
            _cl.save({'regime_filter_mode': str(_v.get())})
        except Exception as _e:
            print(f"[A.36] Could not save regime_filter_mode: {_e}")
        _a36_apply_visibility()

    _a36_enabled_var.trace_add('write', _a36_save_enabled)
    _a36_mode_var.trace_add('write', _a36_save_mode)

    # ── Initial render ───────────────────────────────────────────────────
    # WHY (Phase A.36): Apply visibility ONCE after construction so the
    #      panel renders correctly on first paint based on saved state.
    # CHANGED: April 2026 — Phase A.36
    _a36_apply_visibility()

    # ════════════════════════════════════════════════════════════════════════
    # PHASE A.39a — Single Rule Mode UI scaffolding
    # ════════════════════════════════════════════════════════════════════════
    # ── Cross-card links for disabling Discovery Settings when SRM is on ──
    # WHY: Discovery Settings only tune the Step 3 DecisionTreeClassifier
    #      — they have no effect on Single Rule Mode (which uses its own
    #      hardcoded algorithm). Disable the spinboxes when SRM is on so
    #      the user isn't misled into thinking the knobs do anything.
    #      The spinbox list is populated later (after _make_spinbox() is
    #      defined + called); the updater is safe to call before then —
    #      it iterates an empty list until spinboxes are added.
    _a39b_discovery_spinboxes = []
    _a39b_discovery_hint_ref  = {'label': None}

    def _a39b_update_discovery_state():
        srm_on = bool(_a39a_enabled_var.get())
        new_state = 'disabled' if srm_on else 'normal'
        for _sb in _a39b_discovery_spinboxes:
            try:
                _sb.configure(state=new_state)
            except Exception:
                pass
        _lbl = _a39b_discovery_hint_ref.get('label')
        if _lbl is not None:
            try:
                if srm_on:
                    _lbl.configure(
                        text="⚠ Disabled — Single Rule Mode is active "
                             "(it uses its own algorithm, these knobs do nothing)",
                        fg="#b8860b",
                    )
                else:
                    _lbl.configure(
                        text="💡 Changes save automatically",
                        fg="#666",
                    )
            except Exception:
                pass
    # WHY (Phase A.39a): Adds a parallel discovery-mode card beneath the
    #      Regime Filter card. Master checkbox + 4 variant radios (A/B/C/D)
    #      + yellow "not yet wired" disclaimer. Mutually exclusive with the
    #      Regime Filter checkbox — the save handlers share a mutex so we
    #      never end up with both on at once. No pipeline code reads these
    #      keys yet; A.39b onwards wire each variant individually.
    # CHANGED: April 2026 — Phase A.39a
    _a39a_frame = tk.Frame(left_frame, bg="#fffbea", padx=15, pady=15)
    _a39a_frame.pack(fill="x", pady=(15, 0))

    tk.Label(_a39a_frame, text="🎲 Single Rule Mode (experimental)",
             bg="#fffbea", fg="#16213e",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

    # ── Master enable checkbox ──────────────────────────────────────────────
    _a39a_enabled_var = tk.BooleanVar(
        value=str(_cfg.get('single_rule_mode_enabled', 'false')).lower() == 'true'
    )

    _a39a_enable_cb = tk.Checkbutton(
        _a39a_frame,
        text="Enable single rule mode",
        variable=_a39a_enabled_var,
        bg="#fffbea", fg="#16213e",
        font=("Segoe UI", 10),
        activebackground="#fffbea",
        anchor="w",
    )
    _a39a_enable_cb.pack(anchor="w", pady=(0, 4))

    # ── Container for mode radios + disclaimer ──────────────────────────────
    _a39a_inner = tk.Frame(_a39a_frame, bg="#fffbea")
    # NOTE: pack()'d by _a39a_apply_visibility() below.

    # ── Mode variant radios (A/B/C/D) ───────────────────────────────────────
    tk.Label(_a39a_inner, text="Rule variant:",
             bg="#fffbea", fg="#444",
             font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 2))

    _a39a_variant_var = tk.StringVar(
        value=str(_cfg.get('single_rule_mode_variant', 'a')).lower()
    )
    if _a39a_variant_var.get() not in ('a', 'b', 'c', 'd'):
        _a39a_variant_var.set('a')

    _a39a_radio_frame = tk.Frame(_a39a_inner, bg="#fffbea")
    _a39a_radio_frame.pack(anchor="w", pady=(0, 4))

    for _val, _lbl in (
        ('a', "Mode A — single feature + threshold (e.g. RSI < 30)"),
        ('b', "Mode B — single crossover (e.g. EMA9 crosses above EMA20)"),
        ('c', "Mode C — two-feature conjunction (e.g. RSI<30 AND ADX>25)"),
        ('d', "Mode D — regime-gated single rule"),
    ):
        tk.Radiobutton(
            _a39a_radio_frame,
            text=_lbl,
            variable=_a39a_variant_var,
            value=_val,
            bg="#fffbea", fg="#16213e",
            font=("Segoe UI", 9),
            activebackground="#fffbea",
            anchor="w",
        ).pack(anchor="w")

    # ── Status / disclaimer area (yellow "not wired") ──────────────────────
    # WHY (Phase A.39a): Shown when the selected variant has no discovery
    #      algorithm yet (B/C/D today) OR when Mode A is selected but no
    #      discovery has run yet. A.39b replaces this with a green frame
    #      (_a39b_discovered_frame below) for valid Mode A results.
    # CHANGED: April 2026 — Phase A.39b (simplified: yellow only)
    _a39a_status_frame = tk.Frame(_a39a_inner, bg="#fff8dc", padx=10, pady=8)
    # NOTE: packed inside _a39a_apply_visibility().

    def _a39a_render_status():
        """Render the yellow 'not yet wired' message. Content varies
        slightly based on selected variant."""
        for _w in list(_a39a_status_frame.winfo_children()):
            try:
                _w.destroy()
            except Exception:
                pass

        _v = (_a39a_variant_var.get() or 'a').lower()
        _phase_map = {'a': 'A.39b', 'b': 'A.39c', 'c': 'A.39d', 'd': 'A.39e'}
        _phase = _phase_map.get(_v, 'a follow-up phase')

        tk.Label(
            _a39a_status_frame,
            text="⚠ Not yet wired — UI scaffolding only." if _v != 'a'
                 else "ⓘ Mode A has not produced a discovery yet.",
            bg="#fff8dc", fg="#b8860b",
            font=("Segoe UI", 9, "bold"),
            wraplength=320, justify="left",
        ).pack(anchor="w")
        tk.Label(
            _a39a_status_frame,
            text=(f"Mode {_v.upper()}'s discovery algorithm is implemented in "
                  f"phase {_phase}. Toggling the checkbox today only persists "
                  f"your preference — it does not alter rule extraction or "
                  f"backtest behavior." if _v != 'a'
                  else "Run a scenario with Single Rule Mode + Mode A enabled "
                       "to populate the discovered conjunction here."),
            bg="#fff8dc", fg="#555",
            font=("Segoe UI", 9),
            wraplength=320, justify="left",
        ).pack(anchor="w", pady=(4, 0))

    _a39a_render_status()

    # ── Phase A.39b: Mode A discovery parameters (user-tunable) ─────────────
    # WHY (Phase A.39b): The 8 knobs that control Mode A's
    #      tightest-conjunction search used to be hardcoded in
    #      single_rule_mode_discovery.py. Expose them here so the user can
    #      tune them from the Run Scenarios panel without editing Python.
    #      Defaults (from config_loader.DEFAULTS) match the original
    #      hardcoded values so behavior is unchanged until the user
    #      touches them. Panel is visible only when variant == 'a' since
    #      these settings only apply to Mode A.
    # CHANGED: April 2026 — Phase A.39b
    _a39b_params_frame = tk.LabelFrame(
        _a39a_inner,
        text="Mode A Discovery Settings (editable)",
        bg="#fffbea", fg="#16213e",
        font=("Segoe UI", 9, "bold"),
        padx=8, pady=6,
    )
    # NOTE: packed conditionally by _a39a_apply_visibility().

    tk.Label(
        _a39b_params_frame,
        text=("These parameters tune Mode A's tightest-conjunction search. "
              "Defaults work for most cases; tweak if discovery returns "
              "no conjunction or you want to widen/tighten the result."),
        bg="#fffbea", fg="#555",
        font=("Segoe UI", 8, "italic"),
        wraplength=320, justify="left",
    ).pack(anchor="w", pady=(0, 6))

    # WHY (Phase A.39b.5): Two new strategy controls ABOVE the numeric
    #      spinboxes. User decides HOW Mode A searches first, then tunes
    #      the numeric parameters.
    #
    #      Control 1: Dedup checkbox — removes features >0.7 Pearson
    #      correlated with a higher-ranked pool member BEFORE conjunction
    #      enumeration. Fixes the "3 ATR features chosen together"
    #      problem from A.39b.4.
    #
    #      Control 2: Winner-selection radio — tightness (default, picks
    #      the most specific conjunction above target) vs coverage
    #      (picks the highest-coverage conjunction above target).
    #
    #      Both tooltips are multi-paragraph plain-language explanations
    #      so the user knows exactly what each option does AND when to
    #      use it WITHOUT reading code.
    # CHANGED: April 2026 — Phase A.39b.5

    # WHY (Phase A.39b.5.1 hotfix): The original A.39b.5 tooltip calls
    #      referenced `_a291_add_tooltip` which is defined ~670 lines
    #      LATER in this same function. At widget-construction time the
    #      name didn't exist yet, so every tooltip attach raised
    #      NameError, silently swallowed by try/except, leaving the
    #      checkbox and radios with no hover help.
    #
    #      Fix: import the tooltip helper LOCALLY at the top of this
    #      block under a distinct name (`_a39b5_tooltip`) and use that
    #      for A.39b.5's three attachments. The later A.29.1 block
    #      continues to work unchanged.
    # CHANGED: April 2026 — Phase A.39b.5.1
    try:
        from shared.tooltip import add_tooltip as _a39b5_tooltip
    except Exception as _a39b5_tt_import_err:
        # Fall back to no-op so downstream code still runs even if the
        # tooltip module is missing. Print once so the reason is visible
        # during development — no-op in production logs.
        print(f"[A.39b.5.1] shared.tooltip unavailable: {_a39b5_tt_import_err} — "
              f"Mode A controls will have no hover text.")
        def _a39b5_tooltip(*_args, **_kwargs):
            return None

    # ---- Control 1: Dedup correlated features checkbox ----
    _a39b5_dedup_frame = tk.Frame(_a39b_params_frame, bg="#fffbea")
    _a39b5_dedup_frame.pack(fill="x", pady=(0, 4))

    _a39b5_dedup_var = tk.BooleanVar(
        value=str(_cfg.get('srm_a_dedup_correlated', 'false')).lower() == 'true'
    )
    _a39b5_dedup_cb = tk.Checkbutton(
        _a39b5_dedup_frame,
        text="Dedup correlated features before search",
        variable=_a39b5_dedup_var,
        bg="#fffbea", fg="#333",
        font=("Segoe UI", 9),
        anchor="w",
        activebackground="#fffbea",
    )
    _a39b5_dedup_cb.pack(side="left", anchor="w")

    _a39b5_dedup_tooltip = (
        "WHAT IT DOES:\n"
        "Before searching for conjunctions, removes any candidate feature "
        "that is too similar (>70% Pearson correlation) to a higher-ranked "
        "feature already in the pool.\n\n"
        "WHY YOU MIGHT WANT IT:\n"
        "Without this, Mode A can pick 3 highly-correlated features (e.g. "
        "H4_atr_14, H1_atr_28, M5_atr_100 — which all say \"low "
        "volatility\") as 3 of its 5 conditions. The result looks like a "
        "5-condition rule but is really the same condition measured 3 "
        "ways. Turning this ON forces diverse, informationally-distinct "
        "features into each conjunction slot.\n\n"
        "WHEN TO USE:\n"
        "• Your discovered rule has obvious duplicates (\"ATR < X\" on "
        "three timeframes, or \"EMA_20 > Y\" and \"EMA_50 > Y\").\n"
        "• You want a cleaner, more interpretable rule.\n\n"
        "WHEN TO LEAVE OFF:\n"
        "• Default. Preserves original A.39b.4 behavior.\n"
        "• You want the absolute tightest conjunction regardless of "
        "structural redundancy.\n\n"
        "TRADE-OFF:\n"
        "ON reduces the candidate pool size (some candidates are "
        "dropped), which may make a 95% joint-coverage target harder "
        "to hit. If you enable dedup and no conjunction is found, try "
        "lowering \"Target coverage\" below."
    )
    # WHY (Phase A.39b.5.1 hotfix): use the locally-imported tooltip
    #      helper; the original _a291_add_tooltip reference was not yet
    #      defined at this point in the function body.
    # CHANGED: April 2026 — Phase A.39b.5.1
    _a39b5_tooltip(_a39b5_dedup_cb, _a39b5_dedup_tooltip, wraplength=420)

    def _a39b5_on_dedup_change(*_a):
        try:
            _cl.save({'srm_a_dedup_correlated':
                      'true' if _a39b5_dedup_var.get() else 'false'})
            try:
                _a291_flash_saved('srm_a_dedup_correlated')
            except Exception:
                pass
        except Exception as e:
            print(f"[A.39b.5] Could not save srm_a_dedup_correlated: {e}")
    _a39b5_dedup_var.trace_add('write', _a39b5_on_dedup_change)

    # ---- Control 2: Winner-selection radio group ----
    _a39b5_winner_outer = tk.Frame(_a39b_params_frame, bg="#fffbea")
    _a39b5_winner_outer.pack(fill="x", pady=(0, 6))

    _a39b5_winner_header = tk.Label(
        _a39b5_winner_outer,
        text="Winner selection:",
        bg="#fffbea", fg="#333",
        font=("Segoe UI", 9, "bold"),
        anchor="w",
    )
    _a39b5_winner_header.pack(anchor="w")

    _a39b5_winner_header_tooltip = (
        "When multiple candidate conjunctions meet the coverage target, "
        "Mode A needs to pick ONE as the final answer. This radio chooses "
        "the selection strategy.\n\n"
        "Both options respect \"Target coverage\" — conjunctions below "
        "the target are excluded regardless of strategy."
    )
    # WHY (Phase A.39b.5.1 hotfix): use the locally-imported tooltip
    #      helper; the original _a291_add_tooltip reference was not yet
    #      defined at this point in the function body.
    # CHANGED: April 2026 — Phase A.39b.5.1
    _a39b5_tooltip(_a39b5_winner_header, _a39b5_winner_header_tooltip,
                   wraplength=420)

    _a39b5_winner_var = tk.StringVar(
        value=str(_cfg.get('srm_a_winner_selection', 'tightness')).lower()
    )
    if _a39b5_winner_var.get() not in ('tightness', 'coverage'):
        _a39b5_winner_var.set('tightness')

    _a39b5_radio_frame = tk.Frame(_a39b5_winner_outer, bg="#fffbea")
    _a39b5_radio_frame.pack(anchor="w", padx=(12, 0))

    _a39b5_tightness_rb = tk.Radiobutton(
        _a39b5_radio_frame,
        text="Tightness (default, original)",
        variable=_a39b5_winner_var, value='tightness',
        bg="#fffbea", fg="#333",
        font=("Segoe UI", 9),
        activebackground="#fffbea",
        anchor="w",
    )
    _a39b5_tightness_rb.pack(anchor="w")

    _a39b5_tightness_tooltip = (
        "WHAT IT DOES:\n"
        "Among all conjunctions that meet the Target coverage, picks the "
        "one with the LOWEST tightness product — the most specific rule. "
        "Tightness is 1/(1+|threshold-median|/IQR), so low tightness = "
        "thresholds are far from the feature's median (at the fat tail "
        "of the distribution).\n\n"
        "EXAMPLE:\n"
        "Target = 86%. Say the search finds:\n"
        "  Conjunction A: 2 conditions, covers 95%, tightness product 0.08\n"
        "  Conjunction B: 5 conditions, covers 86%, tightness product 0.00004\n"
        "Under TIGHTNESS, B wins (smallest product, most specific).\n\n"
        "WHY YOU MIGHT WANT IT:\n"
        "• Reverse-engineering: if the bot uses MANY tight conditions, "
        "this finds them even if their joint coverage is near the floor.\n"
        "• You trust your Target coverage setting and want maximum "
        "specificity above it.\n\n"
        "WHEN TO USE:\n"
        "• Default. Preserves original A.39b.4 behavior.\n"
        "• You've set Target coverage to a level you're sure the bot "
        "actually meets (e.g. 86% or higher), and want the tightest rule "
        "that reaches that floor."
    )
    # WHY (Phase A.39b.5.1 hotfix): use the locally-imported tooltip helper.
    # CHANGED: April 2026 — Phase A.39b.5.1
    _a39b5_tooltip(_a39b5_tightness_rb, _a39b5_tightness_tooltip,
                   wraplength=420)

    _a39b5_coverage_rb = tk.Radiobutton(
        _a39b5_radio_frame,
        text="Coverage (highest coverage wins)",
        variable=_a39b5_winner_var, value='coverage',
        bg="#fffbea", fg="#333",
        font=("Segoe UI", 9),
        activebackground="#fffbea",
        anchor="w",
    )
    _a39b5_coverage_rb.pack(anchor="w")

    _a39b5_coverage_tooltip = (
        "WHAT IT DOES:\n"
        "Among all conjunctions that meet the Target coverage, picks the "
        "one covering the HIGHEST fraction of trades. Tightness is used "
        "only as a tie-breaker when multiple conjunctions have very "
        "similar coverage.\n\n"
        "EXAMPLE:\n"
        "Target = 86%. Say the search finds:\n"
        "  Conjunction A: 2 conditions, covers 95%, tightness product 0.08\n"
        "  Conjunction B: 5 conditions, covers 86%, tightness product 0.00004\n"
        "Under COVERAGE, A wins (highest coverage).\n\n"
        "WHY YOU MIGHT WANT IT:\n"
        "• Reverse-engineering: you want the rule that BEST DESCRIBES "
        "the majority of the bot's trades, even if it's less tight.\n"
        "• Your Target coverage is set low (e.g. 80%) to just rule out "
        "obvious outliers, and you want the highest-coverage rule above "
        "that floor rather than the tightest.\n\n"
        "WHEN TO USE:\n"
        "• You set Target coverage to a conservative floor (80-90%) and "
        "want Mode A to tell you the BIGGEST rule it can find above "
        "that floor.\n"
        "• The current Tightness setting gave you a rule at exactly the "
        "floor coverage, and you want to know if a wider rule exists."
    )
    # WHY (Phase A.39b.5.1 hotfix): use the locally-imported tooltip helper.
    # CHANGED: April 2026 — Phase A.39b.5.1
    _a39b5_tooltip(_a39b5_coverage_rb, _a39b5_coverage_tooltip,
                   wraplength=420)

    def _a39b5_on_winner_change(*_a):
        try:
            _v = _a39b5_winner_var.get()
            if _v not in ('tightness', 'coverage'):
                _v = 'tightness'
            _cl.save({'srm_a_winner_selection': _v})
            try:
                _a291_flash_saved('srm_a_winner_selection')
            except Exception:
                pass
        except Exception as e:
            print(f"[A.39b.5] Could not save srm_a_winner_selection: {e}")
    _a39b5_winner_var.trace_add('write', _a39b5_on_winner_change)

    # ---- Thin separator before the numeric spinboxes ----
    _a39b5_sep = tk.Frame(_a39b_params_frame, bg="#e8d982", height=1)
    _a39b5_sep.pack(fill="x", pady=(4, 6))

    def _a39b_make_param_spinbox(parent, label_text, config_key,
                                 from_, to, increment, tooltip_text,
                                 is_float=False):
        """Parallel to _make_spinbox but styled for the SRM yellow panel
        and supports float increments. Defined inline (not reusing
        _make_spinbox) because _make_spinbox is declared later in the
        function — moving it would reorder unrelated code, and the
        styling diverges anyway (SRM yellow vs Discovery beige)."""
        row = tk.Frame(parent, bg="#fffbea")
        row.pack(fill="x", pady=2)

        label = tk.Label(
            row, text=label_text, bg="#fffbea", fg="#333",
            font=("Segoe UI", 9), width=26, anchor="w",
        )
        label.pack(side="left")
        try:
            _a291_add_tooltip(label, tooltip_text, wraplength=380)
        except Exception:
            pass

        var = tk.StringVar(value=str(_cfg.get(config_key, str(from_))))

        spinbox = tk.Spinbox(
            row, from_=from_, to=to, increment=increment,
            textvariable=var,
            font=("Segoe UI", 9), width=10,
            bg="white", relief="solid", borderwidth=1,
            format=("%.2f" if is_float else "%.0f"),
        )
        spinbox.pack(side="right")
        try:
            _a291_add_tooltip(spinbox, tooltip_text, wraplength=380)
        except Exception:
            pass

        def _on_var_change(*_a, _var=var, _key=config_key):
            try:
                _cl.save({_key: _var.get()})
                try:
                    _a291_flash_saved(_key)
                except Exception:
                    pass
            except Exception as e:
                print(f"[A.39b] Could not save {_key}: {e}")

        var.trace_add('write', _on_var_change)
        return spinbox

    _a39b_make_param_spinbox(
        _a39b_params_frame, "Target coverage:", "srm_a_target_coverage",
        0.01, 1.00, 0.01,
        "Minimum fraction of trades the joint AND-conjunction must cover. "
        "Default 0.95 — the bot's trigger must be present in >=95% of "
        "historical trades. Lower for noisier datasets; higher for "
        "strictly deterministic bots.",
        is_float=True,
    )
    _a39b_make_param_spinbox(
        _a39b_params_frame, "Per-condition coverage:", "srm_a_per_condition_coverage",
        0.01, 1.00, 0.01,
        "Minimum fraction of trades each single-sided candidate condition "
        "must cover on its own. Default 0.95 matches the 5th/95th "
        "percentile construction. Lower to let tighter-but-rarer "
        "conditions into the pool.",
        is_float=True,
    )
    _a39b_make_param_spinbox(
        _a39b_params_frame, "Min non-NaN fraction:", "srm_a_min_non_nan_frac",
        0.01, 1.00, 0.01,
        "A feature is usable only if >= this fraction of trade rows "
        "have a non-NaN value for it. Default 0.95 excludes features "
        "that fail to evaluate on many candles.",
        is_float=True,
    )
    _a39b_make_param_spinbox(
        _a39b_params_frame, "Pool size:", "srm_a_pool_size",
        1, 500, 1,
        "Keep the top-N tightest single-sided candidate conditions for "
        "conjunction enumeration. Default 40. Larger pool = more "
        "conjunctions explored but slower.",
        is_float=False,
    )
    _a39b_make_param_spinbox(
        _a39b_params_frame, "Min cardinality:", "srm_a_min_cardinality",
        1, 10, 1,
        "Minimum number of conditions in the discovered conjunction. "
        "Default 2 — a single-feature rule is trivial.",
        is_float=False,
    )
    _a39b_make_param_spinbox(
        _a39b_params_frame, "Max cardinality:", "srm_a_max_cardinality",
        1, 10, 1,
        "Maximum number of conditions. Default 5. Higher allows more "
        "complex conjunctions but grows combinatorially.",
        is_float=False,
    )
    _a39b_make_param_spinbox(
        _a39b_params_frame, "Max enumerations/level:", "srm_a_max_enumerations_per_level",
        1, 1000000, 1000,
        "Cap per-cardinality combinations. Default 5000. When exceeded, "
        "a seeded random sample is drawn. Increase for thoroughness, "
        "decrease for speed.",
        is_float=False,
    )
    _a39b_make_param_spinbox(
        _a39b_params_frame, "Tie-break within pct:", "srm_a_tie_break_within_pct",
        0.0, 1.0, 0.01,
        "When multiple conjunctions score within this fraction of the "
        "best, prefer fewer conditions. Default 0.10 (10%). 0 disables "
        "the tie-break.",
        is_float=True,
    )

    # ── Phase A.39b: Discovered-rule display for Mode A ─────────────────────
    # WHY (Phase A.39b): When Mode A has run successfully, show the
    #      discovered conjunction here instead of the yellow warning.
    #      Rebuilt every time the user switches into variant 'a' via
    #      _a39b_render_discovered. Reads from single_rule_mode_discovered
    #      config key (written by analyze.py at the end of Step 3 when
    #      Mode A ran).
    # CHANGED: April 2026 — Phase A.39b
    _a39b_discovered_frame = tk.Frame(_a39a_inner, bg="#e8f5e9", padx=10, pady=8)
    # NOTE: _a39a_apply_visibility() packs exactly ONE of (status_frame,
    #       discovered_frame) at a time.

    def _a39b_render_discovered():
        """Rebuild the green discovered-rule display from the latest
        single_rule_mode_discovered config value. Returns True if a valid
        Mode A discovery is present and rendered; False otherwise (caller
        falls back to showing the yellow status_frame)."""
        for _w in list(_a39b_discovered_frame.winfo_children()):
            try:
                _w.destroy()
            except Exception:
                pass

        try:
            _raw = _cl.load().get('single_rule_mode_discovered', '') or ''
        except Exception:
            _raw = ''
        if not _raw:
            return False

        try:
            import json as _a39b_json
            _disc = _a39b_json.loads(_raw)
        except Exception:
            return False

        if _disc.get('status') != 'ok' or _disc.get('variant') != 'a':
            return False

        _chosen = _disc.get('chosen') or []
        if not _chosen:
            return False

        _stats  = _disc.get('chosen_stats') or {}
        _tc     = _disc.get('trade_count', 0)
        _target = _disc.get('target_coverage', 0.95)

        tk.Label(
            _a39b_discovered_frame,
            text=f"✓ Discovered conjunction ({_stats.get('cardinality', len(_chosen))} "
                 f"condition(s)):",
            bg="#e8f5e9", fg="#16213e",
            font=("Segoe UI", 9, "bold"),
            wraplength=320, justify="left",
        ).pack(anchor="w")

        for _cond in _chosen:
            tk.Label(
                _a39b_discovered_frame,
                text=f"  {_cond.get('feature')} {_cond.get('operator')} "
                     f"{_cond.get('threshold')}",
                bg="#e8f5e9", fg="#16213e",
                font=("Consolas", 9),
                anchor="w",
            ).pack(anchor="w", pady=(2, 0))

        try:
            _joint = float(_stats.get('joint_coverage', 0)) * 100
            _tp    = _stats.get('tightness_product', None)
            _summary = (
                f"Joint coverage: {_joint:.1f}% of {_tc} trades "
                f"(target >={_target*100:.0f}%)"
            )
            if _tp is not None:
                _summary += f"\nTightness product: {_tp:.4f} "
                _summary += "(lower = tighter / more specific)"
            tk.Label(
                _a39b_discovered_frame,
                text=_summary,
                bg="#e8f5e9", fg="#16213e",
                font=("Segoe UI", 9),
                wraplength=320, justify="left",
            ).pack(anchor="w", pady=(6, 0))
        except Exception:
            pass

        tk.Label(
            _a39b_discovered_frame,
            text="ⓘ Discovery only — rule is NOT applied to the pipeline. "
                 "Full details in outputs/single_rule_mode.json.",
            bg="#e8f5e9", fg="#888",
            font=("Segoe UI", 8, "italic"),
            wraplength=320, justify="left",
        ).pack(anchor="w", pady=(6, 0))

        # WHY (Phase A.40a): Manual Save button on the green Mode A
        #      discovered-rule display. The global auto-save checkbox
        #      already saves Mode A rules at discovery time, but the
        #      user may have run the discovery with auto-save OFF, or
        #      they may want to save the SAME rule again with a fresh
        #      note. The button posts the currently-displayed Mode A
        #      conjunction to saved_rules.json with the standard
        #      "Mode A (manual)" source tag.
        # CHANGED: April 2026 — Phase A.40a
        try:
            from shared.saved_rules import build_save_button as _a40a_build_btn
            _a40a_manual_rule = {
                'conditions': [
                    {
                        'feature':  _c.get('feature'),
                        'operator': _c.get('operator'),
                        'value':    _c.get('value', _c.get('threshold')),
                    }
                    for _c in _chosen
                ],
                'prediction':       'BUY',
                # WHY (Phase A.40a hotfix): Mode A has no WR; use 0.0
                #      not None so the Saved Rules panel can render
                #      the entry without TypeError.
                # CHANGED: April 2026 — Phase A.40a hotfix
                'win_rate':         0.0,
                'avg_pips':         0.0,
                'coverage':         int(round(float(_stats.get('joint_coverage', 0.0)) * _tc)),
                'confidence':       float(_stats.get('joint_coverage', 0.0)),
                'tightness_product': _stats.get('tightness_product'),
            }
            _a40a_btn = _a40a_build_btn(
                _a39b_discovered_frame,
                _a40a_manual_rule,
                source="Mode A (manual)",
                bg="#e8f5e9",
            )
            _a40a_btn.pack(anchor="w", pady=(8, 0))
        except Exception as _a40a_e:
            print(f"[A.40a] manual save button skipped: {_a40a_e}")

        return True

    # ── Visibility ──────────────────────────────────────────────────────────
    # WHY (Phase A.39b): Switches between showing the green discovered_frame
    #      (Mode A result available) vs the yellow status_frame (no result
    #      yet, or a non-A variant selected).
    # CHANGED: April 2026 — Phase A.39b
    def _a39a_apply_visibility(*_args):
        for _w in (_a39a_status_frame, _a39b_discovered_frame, _a39b_params_frame):
            try:
                _w.pack_forget()
            except Exception:
                pass

        if not _a39a_enabled_var.get():
            try:
                _a39a_inner.pack_forget()
            except Exception:
                pass
            return

        if not _a39a_inner.winfo_ismapped():
            _a39a_inner.pack(fill="x", pady=(4, 0))

        _variant_is_a = (_a39a_variant_var.get() or 'a').lower() == 'a'

        # Mode A params panel — only shown for variant 'a'.
        if _variant_is_a:
            _a39b_params_frame.pack(fill="x", pady=(8, 0))

        _rendered_discovered = False
        if _variant_is_a:
            try:
                _rendered_discovered = _a39b_render_discovered()
            except Exception as _re:
                print(f"[A.39b] render failed: {_re}")
                _rendered_discovered = False

        if _rendered_discovered:
            _a39b_discovered_frame.pack(fill="x", pady=(8, 0))
        else:
            try:
                _a39a_render_status()
            except Exception as _e:
                print(f"[A.39a] Could not render status: {_e}")
            _a39a_status_frame.pack(fill="x", pady=(8, 0))

        # Sync the Discovery Settings enable/disable state.
        try:
            _a39b_update_discovery_state()
        except Exception:
            pass

    # ── Mutual-exclusivity mutex ────────────────────────────────────────────
    # WHY (Phase A.39a): Both the Regime Filter checkbox and the Single
    #      Rule Mode checkbox have trace_add save handlers. When we flip
    #      one programmatically from within the other's handler, the
    #      second handler fires recursively. A small mutex ('busy' flag
    #      in a mutable dict so closures share state) short-circuits the
    #      inner invocation so each user-initiated toggle causes exactly
    #      one save per checkbox.
    # CHANGED: April 2026 — Phase A.39a
    _a39a_mutex_lock = {'busy': False}

    def _a39a_save_enabled(*_args, _v=_a39a_enabled_var,
                            _other=_a36_enabled_var, _lock=_a39a_mutex_lock):
        if _lock['busy']:
            return
        _lock['busy'] = True
        try:
            _cl.save({
                'single_rule_mode_enabled': 'true' if _v.get() else 'false'
            })
            # If this turned ON and the regime filter is also ON, force
            # regime filter OFF so they're mutually exclusive.
            if _v.get() and _other.get():
                _other.set(False)
                try:
                    _cl.save({'regime_filter_enabled': 'false'})
                except Exception as _e:
                    print(f"[A.39a] Could not disable regime filter: {_e}")
        except Exception as _e:
            print(f"[A.39a] Could not save single_rule_mode_enabled: {_e}")
        finally:
            _lock['busy'] = False
        _a39a_apply_visibility()

    def _a39a_save_variant(*_args, _v=_a39a_variant_var):
        try:
            _cl.save({'single_rule_mode_variant': str(_v.get())})
        except Exception as _e:
            print(f"[A.39a] Could not save single_rule_mode_variant: {_e}")
        # Re-render card so green/yellow frame swaps when variant changes.
        try:
            _a39a_apply_visibility()
        except Exception:
            pass

    _a39a_enabled_var.trace_add('write', _a39a_save_enabled)
    _a39a_variant_var.trace_add('write', _a39a_save_variant)

    # Mirror handler on the A.36 side: when Regime Filter turns on and
    # Single Rule Mode is on, force Single Rule Mode off.
    def _a39a_mirror_from_a36(*_args, _v=_a36_enabled_var,
                               _other=_a39a_enabled_var,
                               _lock=_a39a_mutex_lock):
        if _lock['busy']:
            return
        if _v.get() and _other.get():
            _lock['busy'] = True
            try:
                _other.set(False)
                try:
                    _cl.save({'single_rule_mode_enabled': 'false'})
                except Exception as _e:
                    print(f"[A.39a] Could not disable single rule mode: {_e}")
            finally:
                _lock['busy'] = False
            # SRM was just forced off — refresh UI (SRM card + Discovery
            # Settings state) since _a39a_save_enabled is short-circuited
            # by the mutex and won't do it.
            try:
                _a39a_apply_visibility()
            except Exception:
                pass

    _a36_enabled_var.trace_add('write', _a39a_mirror_from_a36)

    # Startup hygiene: if config somehow has both on, prefer the regime
    # filter (since A.37 already writes real discovery output) and turn
    # single rule mode off.
    if _a36_enabled_var.get() and _a39a_enabled_var.get():
        _a39a_mutex_lock['busy'] = True
        try:
            _a39a_enabled_var.set(False)
            try:
                _cl.save({'single_rule_mode_enabled': 'false'})
            except Exception as _e:
                print(f"[A.39a] Startup hygiene save failed: {_e}")
        finally:
            _a39a_mutex_lock['busy'] = False

    _a39a_apply_visibility()

    # WHY (Phase 56 Fix 1): Old scenarios dict was hardcoded. A user
    #      whose config sets align_timeframes=M5,H1,D1 saw the wrong
    #      five options. Now: read align_timeframes from config_loader,
    #      union with any existing outputs/scenario_*/ dirs, and fall
    #      back to the hardcoded list only when config cannot be read.
    # CHANGED: April 2026 — Phase 56 Fix 1 — dynamic scenarios from config
    #          (audit Part D HIGH #86)
    _FALLBACK_SCENARIOS = {
        'M5':    ('M5 - 5 Minute',    'Fastest timeframe, best for scalping bots'),
        'M15':   ('M15 - 15 Minute',  'Medium-fast timeframe'),
        'H1':    ('H1 - 1 Hour',      'Most common timeframe for day trading'),
        'H4':    ('H4 - 4 Hour',      'Swing trading timeframe'),
        'H1_M15':('H1+M15 Combined',  'Multi-timeframe analysis'),
    }
    _TF_LABELS = {
        'M1': 'M1 - 1 Minute', 'M5': 'M5 - 5 Minute', 'M15': 'M15 - 15 Minute',
        'M30': 'M30 - 30 Minute', 'H1': 'H1 - 1 Hour', 'H4': 'H4 - 4 Hour',
        'H8': 'H8 - 8 Hour', 'D1': 'D1 - Daily', 'W1': 'W1 - Weekly',
    }
    def _build_scenarios():
        keys = []
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
            import config_loader as _cl
            _cfg = _cl.load()
            _tfs = [t.strip() for t in _cfg.get('align_timeframes', '').split(',') if t.strip()]
            keys.extend(_tfs)
        except Exception:
            pass
        # Also include any already-run scenario dirs
        try:
            import glob as _g
            _out = os.path.join(os.path.dirname(__file__), '..', 'outputs')
            for _d in sorted(_g.glob(os.path.join(_out, 'scenario_*'))):
                _k = os.path.basename(_d).replace('scenario_', '')
                if _k and _k not in keys:
                    keys.append(_k)
        except Exception:
            pass
        if not keys:
            return dict(_FALLBACK_SCENARIOS)
        result = {}
        for k in keys:
            label = _TF_LABELS.get(k, f'{k} - {k}')
            result[k] = (label, f'{k} timeframe scenario')
        return result

    scenarios = _build_scenarios()

    scenario_vars = {}

    for scenario_key, (label, desc) in scenarios.items():
        frame = tk.Frame(left_frame, bg="white", pady=5)
        frame.pack(fill="x")

        var = tk.BooleanVar(value=True)
        scenario_vars[scenario_key] = var

        cb = tk.Checkbutton(frame, text=label,
                          variable=var,
                          bg="white", fg="#333",
                          font=("Segoe UI", 11, "bold"),
                          activebackground="white")
        cb.pack(anchor="w")

        tk.Label(frame, text=f"   {desc}",
                bg="white", fg="#666",
                font=("Segoe UI", 9)).pack(anchor="w")

    # Select/Deselect all
    btn_frame = tk.Frame(left_frame, bg="white", pady=15)
    btn_frame.pack(fill="x")

    def select_all():
        for var in scenario_vars.values():
            var.set(True)

    def deselect_all():
        for var in scenario_vars.values():
            var.set(False)

    tk.Button(btn_frame, text="Select All",
             bg="#3498db", fg="white",
             font=("Segoe UI", 9), bd=0, pady=5, padx=15,
             cursor="hand2",
             command=select_all).pack(side="left", padx=(0, 5))

    tk.Button(btn_frame, text="Deselect All",
             bg="#95a5a6", fg="white",
             font=("Segoe UI", 9), bd=0, pady=5, padx=15,
             cursor="hand2",
             command=deselect_all).pack(side="left")

    # Steps info
    steps_frame = tk.Frame(left_frame, bg="#e8f4f8", padx=15, pady=15)
    steps_frame.pack(fill="x", pady=(15, 0))

    # WHY (Phase A.31.1): The current pipeline has 4 real steps, not 7.
    #      The 7-step list was historical (the old pipeline through
    #      step1..step7). The modern pipeline runs only step1, step2,
    #      analyze.run_analysis, and bot_entry_discovery. Update the
    #      label so it matches what actually runs.
    # CHANGED: April 2026 — Phase A.31.1
    tk.Label(steps_frame, text="4 Steps per Scenario:",
             bg="#e8f4f8", fg="#16213e",
             font=("Segoe UI", 10, "bold")).pack(anchor="w")

    steps = [
        "1. Align price data (step1)",
        "2. Compute indicators (step2)",
        "3. Extract win-condition rules (analyze.py)",
        "4. Discover bot entry rules (bot_entry_discovery)",
    ]

    for step in steps:
        tk.Label(steps_frame, text=f"  {step}",
                bg="#e8f4f8", fg="#333",
                font=("Segoe UI", 9)).pack(anchor="w", pady=1)


    # WHY (Phase A.29): Old code hardcoded six tunables in analyze.py.
    #      User cannot adjust them without editing code. Add a Discovery
    #      Settings card with spinboxes for all six, hover tooltips, and
    #      auto-save on focus-out so changes persist immediately.
    # CHANGED: April 2026 — Phase A.29 — Discovery Settings panel
    discovery_frame = tk.Frame(left_frame, bg="#fff9e6", padx=15, pady=15)
    discovery_frame.pack(fill="x", pady=(15, 0))

    tk.Label(discovery_frame, text="🔍 Discovery Settings:",
             bg="#fff9e6", fg="#16213e",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 10))

    # WHY (Phase A.36): _cfg and _cl are now loaded above (before the
    #      Regime Filter section). The original load that used to live
    #      here has been relocated. This block previously re-imported
    #      config_loader and re-loaded the config — kept as a no-op
    #      comment to show where the old load was.
    # CHANGED: April 2026 — Phase A.36

    # WHY (Phase A.29.1): The A.29 helper had four real bugs.
    #      (1) Save was bound to <FocusOut> and <Return>. Spinbox up/
    #          down arrow clicks fire the spinbox's `command=`
    #          callback — NOT FocusOut, NOT Return. Result: every arrow
    #          click changed the display but saved nothing. The user
    #          tuned values, ran scenarios, got the same defaults, and
    #          got the same restrictive 10-rule output as before A.29.
    #      (2) Closure bug — the inner _on_change captured `spinbox`
    #          and `config_key` by name from the enclosing function.
    #          Python closes over variable names not values, so all
    #          six handlers shared the LAST iteration's bindings.
    #          A FocusOut on Tree Max Depth could end up saving the
    #          Min Avg Pips value under the rule_min_avg_pips key.
    #      (3) No visible save confirmation — the user had no way to
    #          tell whether a value persisted without opening JSON.
    #      (4) Tooltip only on the label, not the spinbox. Plus the
    #          inline tooltip code reinvented shared/tooltip.py with
    #          no delay, no screen-edge handling, no parent ownership.
    #      Fix: use a tk.StringVar with a trace_add('write', ...) per
    #      spinbox. Trace fires on every value change regardless of
    #      source (typing, paste, arrow click, programmatic .set()).
    #      Each trace closure captures its OWN var and key via default
    #      argument binding, killing the closure bug. Add a "Saved ✓"
    #      indicator label that flashes green on every successful
    #      save. Use shared.tooltip.add_tooltip on both label AND
    #      spinbox so hovering either works.
    # CHANGED: April 2026 — Phase A.29.1
    try:
        from shared.tooltip import add_tooltip as _a291_add_tooltip
    except Exception:
        def _a291_add_tooltip(*_args, **_kwargs):
            pass

    # WHY: Module-level reference to the indicator label so any spinbox
    #      callback can flash it. Created here, populated below.
    # CHANGED: April 2026 — Phase A.29.1
    _a291_save_indicator = {'label': None, 'after_id': None}

    def _a291_flash_saved(key):
        lbl = _a291_save_indicator['label']
        if lbl is None:
            return
        # Cancel any pending revert
        prev_id = _a291_save_indicator['after_id']
        if prev_id is not None:
            try:
                lbl.after_cancel(prev_id)
            except Exception:
                pass
        try:
            lbl.config(text=f"✓ Saved: {key}", fg="#27ae60")
            _a291_save_indicator['after_id'] = lbl.after(
                2500,
                lambda: lbl.config(text="💡 Changes save automatically", fg="#666"),
            )
        except Exception:
            pass

    def _make_spinbox(parent, label_text, config_key, from_, to, increment, tooltip_text):
        row = tk.Frame(parent, bg="#fff9e6")
        row.pack(fill="x", pady=3)

        label = tk.Label(
            row, text=label_text, bg="#fff9e6", fg="#333",
            font=("Segoe UI", 9), width=22, anchor="w",
        )
        label.pack(side="left")

        # WHY (Phase A.29.1): Use the shared tooltip helper instead of
        #      the inline custom version. shared.tooltip.add_tooltip
        #      attaches with a delay, handles screen edges, and uses
        #      a proper Toplevel parent. Attach to BOTH the label and
        #      the spinbox so hovering either shows the explanation.
        # CHANGED: April 2026 — Phase A.29.1
        _a291_add_tooltip(label, tooltip_text, wraplength=380)

        # WHY (Phase A.29.1): tk.StringVar with trace_add('write', ...)
        #      catches EVERY change to the spinbox value: typing,
        #      paste, up/down arrow clicks, and programmatic .set().
        #      The old <FocusOut>/<Return> binding missed arrow
        #      clicks entirely.
        # CHANGED: April 2026 — Phase A.29.1
        var = tk.StringVar(value=str(_cfg.get(config_key, str(from_))))

        spinbox = tk.Spinbox(
            row, from_=from_, to=to, increment=increment,
            textvariable=var,
            font=("Segoe UI", 9), width=10,
            bg="white", relief="solid", borderwidth=1,
        )
        spinbox.pack(side="right")

        _a291_add_tooltip(spinbox, tooltip_text, wraplength=380)

        # WHY (T3b-addendum): Expose label on the spinbox so the
        #      radio-change handler can rewrite hint text when target
        #      mode changes. No existing caller reads this attribute.
        # CHANGED: April 2026 — T3b-addendum
        spinbox._t3b_label = label
        spinbox._t3b_base_text = label_text

        # WHY (Phase A.29.1): Closure-safe via default-arg binding —
        #      `_var=var` and `_key=config_key` are evaluated at
        #      function-definition time, not at call time, so each
        #      handler keeps its own pair instead of sharing the loop
        #      variable. Without this the Python late-binding closure
        #      bug would make every handler save under the LAST
        #      key created in the loop.
        # CHANGED: April 2026 — Phase A.29.1
        def _on_var_change(*_a, _var=var, _key=config_key):
            try:
                _cl.save({_key: _var.get()})
                _a291_flash_saved(_key)
            except Exception as e:
                print(f"[run_scenarios] Could not save {_key}: {e}")

        var.trace_add('write', _on_var_change)

        return spinbox

    # WHY (T3b): Four-way Tree Target radio. Replaces the T3a 2-radio block.
    #      'regression_weighted' (default) trains a regressor on raw pips
    #      with per-trade sample weights that equalize month influence, so
    #      the tree can't over-fit to a high-trade-density period (e.g. the
    #      2025-2026 gold bull run). 'regression_raw' = T3a current (no
    #      weights). 'binary' = T3a-fix classifier. 'legacy' = frozen pre-T3a
    #      classifier with old gate ordering (for A/B comparison only).
    #      Auto-saves on every click via trace_add('write', ...).
    # CHANGED: April 2026 — T3b
    _t3a_target_frame = tk.Frame(discovery_frame, bg="#fff9e6")
    _t3a_target_frame.pack(fill="x", pady=(0, 10))

    tk.Label(
        _t3a_target_frame,
        text="Tree target:",
        bg="#fff9e6", fg="#333",
        font=("Segoe UI", 9, "bold"),
    ).pack(anchor="w")

    _t3b_valid_modes = ('regression_weighted', 'regression_raw', 'binary', 'legacy')
    _raw_mode = str(_cfg.get('rule_target_mode', 'regression_weighted')).lower()
    # Migrate old 'regression' value from T3a config
    if _raw_mode == 'regression':
        _raw_mode = 'regression_weighted'
    if _raw_mode not in _t3b_valid_modes:
        _raw_mode = 'regression_weighted'
    _t3a_target_var = tk.StringVar(value=_raw_mode)

    _t3a_radio_row = tk.Frame(_t3a_target_frame, bg="#fff9e6")
    _t3a_radio_row.pack(anchor="w", pady=(2, 0))

    _t3b_options = [
        (
            'regression_weighted',
            "Regression weighted  (recommended)",
            "Trains a regression tree on raw pips. Each trade is weighted\n"
            "by 1 / trades_in_its_month, so all calendar months contribute\n"
            "equally regardless of how many trades fell in that period.\n"
            "Suppresses rules locked to a single high-activity regime\n"
            "(e.g. price-level conditions valid only during a bull run).",
        ),
        (
            'regression_raw',
            "Regression raw  (T3a, no weights)",
            "Same regressor as above but all trades weighted equally.\n"
            "Months with more trades dominate splits. Picks high mean-pip\n"
            "leaves but may over-fit to the most active period in the data.",
        ),
        (
            'binary',
            "Binary  (T3a-fix classifier)",
            "Trains a classifier on is_winner = pips > 0.\n"
            "Picks leaves with high win rate. Gate ordering matches T3a-fix:\n"
            "matching_pips are computed before the confidence gate runs.\n"
            "Use this to compare win-rate-based vs magnitude-based rules.",
        ),
        (
            'legacy',
            "Legacy  (pre-T3a, frozen)",
            "Frozen pre-T3a classifier with the original gate ordering\n"
            "(confidence placeholder 0.0 evaluated before matching_pips).\n"
            "Included only for A/B comparison. In practice produces fewer\n"
            "rules than Binary because regression leaves fail the gate.\n"
            "Do not use for production rule discovery.",
        ),
    ]

    _t3b_radiobuttons = []
    for _t3b_val, _t3b_label, _t3b_tip in _t3b_options:
        _rb = tk.Radiobutton(
            _t3a_radio_row,
            text=_t3b_label,
            variable=_t3a_target_var,
            value=_t3b_val,
            bg="#fff9e6", fg="#16213e",
            font=("Segoe UI", 9),
            activebackground="#fff9e6",
            anchor="w",
        )
        _rb.pack(anchor="w")
        try:
            from shared.tooltip import ToolTip
            ToolTip(_rb, _t3b_tip)
        except Exception:
            pass
        _t3b_radiobuttons.append(_rb)

    def _t3a_save_target(*_args, _v=_t3a_target_var):
        try:
            _cl.save({'rule_target_mode': str(_v.get())})
            _a291_flash_saved('rule_target_mode')
        except Exception as _e:
            print(f"[T3b] Could not save rule_target_mode: {_e}")
    _t3a_target_var.trace_add('write', _t3a_save_target)

    # WHY (T3b-addendum): When the radio changes, rewrite the two
    #      mode-sensitive spinbox labels so the user sees what
    #      'confidence' and 'avg_pips' mean in the current mode.
    #      Classifier modes: confidence = tree leaf purity.
    #      Regression modes: confidence = fraction-profitable from matching_pips.
    # CHANGED: April 2026 — T3b-addendum
    def _t3b_update_mode_hints(*_args, _v=_t3a_target_var):
        _mode = (_v.get() or '').lower()
        _is_regression = _mode in ('regression_raw', 'regression_weighted')
        try:
            if _is_regression:
                _t3b_min_conf_sb._t3b_label.configure(
                    text="Min Confidence:   (fraction of matching trades profitable)"
                )
            else:
                _t3b_min_conf_sb._t3b_label.configure(
                    text="Min Confidence:   (leaf purity: max(wins,losses)/total)"
                )
        except Exception:
            pass
        try:
            if _is_regression:
                _t3b_min_avgpips_sb._t3b_label.configure(
                    text="Min Avg Pips:     (gate: leaves below this rejected)"
                )
            else:
                _t3b_min_avgpips_sb._t3b_label.configure(
                    text="Min Avg Pips:     (post-filter after classifier gate)"
                )
        except Exception:
            pass

    _t3a_target_var.trace_add('write', _t3b_update_mode_hints)
    try:
        _t3b_update_mode_hints()
    except Exception as _e:
        print(f"[T3b-addendum] initial hint update failed: {_e}")

    # Add all four radiobuttons to the disable-on-SRM list
    for _t3b_rb in _t3b_radiobuttons:
        _a39b_discovery_spinboxes.append(_t3b_rb)

    # Six tunables with tooltips
    _a39b_discovery_spinboxes.append(_make_spinbox(discovery_frame, "Tree Max Depth:", "rule_tree_max_depth",
                 1, 20, 1,
                 "Maximum depth of the decision tree. Each leaf becomes one "
                 "rule, and depth = number of conditions stacked in that rule. "
                 "Higher = more specific rules with more conditions per rule "
                 "(each catching fewer trades). Lower = simpler rules covering "
                 "more candles. Safe range 3-10. Default: 5. Try 7 for more variety."))

    _a39b_discovery_spinboxes.append(_make_spinbox(discovery_frame, "Tree Min Samples Leaf:", "rule_tree_min_samples_leaf",
                 1, 100, 1,
                 "Minimum number of training trades required at each leaf. "
                 "The tree won't create a leaf with fewer trades than this. "
                 "Lower = more leaves = more rules, each matching fewer trades. "
                 "Higher = fewer, broader rules. Safe range 5-50. Default: 20. "
                 "Drop to 5 if you want many rules."))

    _a39b_discovery_spinboxes.append(_make_spinbox(discovery_frame, "Tree Min Samples Split:", "rule_tree_min_samples_split",
                 2, 200, 1,
                 "Minimum number of trades a node needs before the tree is "
                 "allowed to split it further. Should be ~2x Min Samples Leaf. "
                 "Higher = shallower trees with fewer rules. Lower = deeper "
                 "trees with more rules. Safe range 10-100. Default: 40."))

    _a39b_discovery_spinboxes.append(_make_spinbox(discovery_frame, "Min Leaf Samples Filter:", "rule_min_leaf_samples",
                 1, 100, 1,
                 "Final post-tree filter — after the tree is built, only "
                 "leaves with at least this many training trades become rules. "
                 "Different from Min Samples Leaf above (which controls tree "
                 "construction). This is a second sanity check. Lower = more "
                 "rules survive. Safe range 5-50. Default: 15."))

    # WHY (T3b-addendum): Capture the two mode-sensitive spinboxes so the
    #      radio-change handler can update their label text. Other four
    #      spinboxes have mode-independent meaning and need no update.
    # CHANGED: April 2026 — T3b-addendum
    _t3b_min_conf_sb = _make_spinbox(discovery_frame, "Min Confidence:", "rule_min_confidence",
                 0.0, 1.0, 0.05,
                 "Minimum win rate (0.0-1.0) a rule must have on training "
                 "trades to be kept. 0.65 = at least 65% wins. 0.55 = at "
                 "least 55%. Set to 0 to disable this filter entirely (then "
                 "use Min Avg Pips below to keep only profitable rules "
                 "regardless of win rate). Default: 0.65. Drop to 0.55 for "
                 "many more rules.")
    _a39b_discovery_spinboxes.append(_t3b_min_conf_sb)

    _t3b_min_avgpips_sb = _make_spinbox(discovery_frame, "Min Avg Pips:", "rule_min_avg_pips",
                 -1000, 1000, 1,
                 "Minimum average pips per trade a rule must earn on training "
                 "trades to be kept. Lets you accept rules with mixed wins/"
                 "losses as long as they're profitable on average. Set to 0 "
                 "to require any positive expectancy. Set to a positive number "
                 "to demand minimum profit per trade. Set to -1000 to disable. "
                 "Default: 0.")
    _a39b_discovery_spinboxes.append(_t3b_min_avgpips_sb)

    # WHY (Phase A.29.1): Replaces the static "Changes save automatically"
    #      label with a live indicator that flashes green for ~2.5s
    #      every time a save lands, then reverts to the help text.
    #      Visible confirmation that the save happened — the user
    #      should never need to look at p1_config.json.
    # CHANGED: April 2026 — Phase A.29.1
    _a291_indicator = tk.Label(
        discovery_frame,
        text="💡 Changes save automatically",
        bg="#fff9e6", fg="#666",
        font=("Segoe UI", 8, "italic"),
    )
    _a291_indicator.pack(anchor="w", pady=(8, 0))
    _a291_save_indicator['label'] = _a291_indicator

    # Hook the hint label into the SRM updater so it shows the
    # "disabled by Single Rule Mode" message when appropriate, then
    # run the updater once so initial state matches config.
    _a39b_discovery_hint_ref['label'] = _a291_indicator
    try:
        _a39b_update_discovery_state()
    except Exception as _e:
        print(f"[A.39b] initial discovery-state sync failed: {_e}")

    # Right column - scrollable execution controls + console
    _right_outer = tk.Frame(content_frame, bg="white")
    _right_outer.pack(side="left", fill="both", expand=True, padx=(10, 0))

    _right_canvas = tk.Canvas(_right_outer, bg="white", highlightthickness=0)
    _right_scroll = tk.Scrollbar(_right_outer, orient="vertical", command=_right_canvas.yview)
    _right_canvas.configure(yscrollcommand=_right_scroll.set)
    _right_scroll.pack(side="right", fill="y")
    _right_canvas.pack(side="left", fill="both", expand=True)

    right_frame = tk.Frame(_right_canvas, bg="white", padx=20, pady=20)
    _right_window = _right_canvas.create_window((0, 0), window=right_frame, anchor="nw")

    def _right_on_configure(event):
        _right_canvas.configure(scrollregion=_right_canvas.bbox("all"))
    def _right_on_canvas_resize(event):
        _right_canvas.itemconfig(_right_window, width=event.width)

    right_frame.bind("<Configure>", _right_on_configure)
    _right_canvas.bind("<Configure>", _right_on_canvas_resize)

    def _right_mousewheel(event):
        _right_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    def _bind_mousewheel(widget):
        widget.bind("<MouseWheel>", _right_mousewheel)
        for child in widget.winfo_children():
            _bind_mousewheel(child)

    right_frame.bind("<Map>", lambda e: _bind_mousewheel(right_frame))

    tk.Label(right_frame, text="▶️ Execute",
             bg="white", fg="#16213e",
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 15))

    # Trade data status indicator
    _data_status_frame = tk.Frame(right_frame, bg="#e8f4f8", padx=10, pady=10)
    _data_status_frame.pack(fill="x", pady=(0, 15))

    update_data_status_display()

    # Run button
    run_btn = tk.Button(right_frame, text="🚀 Run Selected Scenarios",
                       bg="#27ae60", fg="white",
                       font=("Segoe UI", 12, "bold"),
                       bd=0, pady=15, cursor="hand2",
                       command=lambda: run_scenarios(scenario_vars, output_text,
                                                     progress_label, progress_bar, pct_label, run_btn))
    run_btn.pack(fill="x", pady=(0, 10))

    # ── Bot Entry Discovery card ──────────────────────────────────────────────
    # WHY (Phase A.31): bot_entry_discovery.py trains on candle-level
    #      "did the bot enter here" labels — fundamentally different
    #      from the legacy 7-step pipeline which trains on trade-level
    #      "did the bot win". This is the right model for reproducing
    #      the bot's actual trade frequency. Add a button + tunables
    #      so the user can run it from the panel without touching code.
    #      Output goes to outputs/bot_entry_rules.json which Run
    #      Backtest already lists as a source (added in Phase A.25).
    # CHANGED: April 2026 — Phase A.31
    bot_entry_frame = tk.LabelFrame(
        right_frame,
        text=" 🤖 Bot Entry Discovery (alternative to 7-step pipeline) ",
        bg="white", fg="#16213e",
        font=("Segoe UI", 9, "bold"),
        padx=10, pady=8,
    )
    bot_entry_frame.pack(fill="x", pady=(0, 10))

    tk.Label(
        bot_entry_frame,
        text=(
            "Trains on candle-level 'did the bot enter' labels.\n"
            "Discovers actual entry rules across all timeframes.\n"
            "Output: outputs/bot_entry_rules.json (loadable in Run Backtest)."
        ),
        bg="white", fg="#666",
        font=("Segoe UI", 8),
        justify="left",
    ).pack(anchor="w", pady=(0, 6))

    # Four bot-entry-specific spinboxes
    _make_spinbox(
        bot_entry_frame, "Max rules:", "bot_entry_max_rules",
        5, 100, 1,
        ("Maximum number of rules to keep across all timeframes after "
         "deduplication. Higher = more rule variety, longer runtime. "
         "Lower = only top rules. Safe range 10-50. Default: 25."),
    )
    _make_spinbox(
        bot_entry_frame, "Tree max depth:", "bot_entry_max_depth",
        2, 8, 1,
        ("Maximum depth of the decision tree extracted per timeframe. "
         "Higher = more conditions per rule (more specific). Lower = "
         "simpler rules covering more candles. Safe range 3-6. Default: 4."),
    )
    _make_spinbox(
        bot_entry_frame, "Min coverage:", "bot_entry_min_coverage",
        5, 200, 1,
        ("Minimum number of candles a rule must match in the training "
         "set to be kept. Higher = fewer, broader rules. Lower = more "
         "specific rules. Safe range 10-50. Default: 20."),
    )
    _make_spinbox(
        bot_entry_frame, "Min recall:", "bot_entry_min_win_rate",
        0.0, 1.0, 0.05,
        ("Minimum 'recall' — fraction of candles in a leaf where the "
         "bot actually entered. NOTE: this is NOT a profit win rate. "
         "It measures how reliably this leaf identifies bot entries. "
         "0.55 = at least 55% of candles in the leaf are real bot "
         "entries. Default: 0.55."),
    )

    tk.Label(
        bot_entry_frame,
        text="↑ Tunables. Discovery runs as Step 4 of '🚀 Run Selected Scenarios'.",
        bg="white", fg="#888",
        font=("Segoe UI", 8, "italic"),
        wraplength=320, justify="left",
    ).pack(anchor="w", pady=(8, 0))

    # Progress indicator
    progress_label = tk.Label(right_frame, text="Ready to run",
                            bg="white", fg="#666",
                            font=("Segoe UI", 10))
    progress_label.pack(anchor="w", pady=(0, 5))
    make_copyable(progress_label)

    # Progress bar
    style = ttk.Style()
    style.theme_use("default")
    style.configure("scenarios.Horizontal.TProgressbar",
                    troughcolor="#e0e0e0", background="#27ae60", thickness=16)
    style.configure("scenarios.error.Horizontal.TProgressbar",
                    troughcolor="#e0e0e0", background="#e74c3c", thickness=16)

    progress_bar = ttk.Progressbar(right_frame, orient="horizontal",
                                   mode="determinate", length=300,
                                   style="scenarios.Horizontal.TProgressbar")
    progress_bar.pack(fill="x", pady=(0, 4))

    pct_label = tk.Label(right_frame, text="",
                         bg="white", fg="#888", font=("Segoe UI", 8))
    pct_label.pack(anchor="e", pady=(0, 10))

    # Output console
    tk.Label(right_frame, text="Console Output:",
             bg="white", fg="#333",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 5))

    output_text = scrolledtext.ScrolledText(right_frame,
                                           height=50,
                                           font=("Consolas", 10),
                                           bg="#2c3e50", fg="#ecf0f1",
                                           insertbackground="white")
    output_text.pack(fill="both", expand=True)

    output_text.insert(tk.END, "Ready to run scenarios.\n")
    output_text.insert(tk.END, "Select scenarios from the left and click Run.\n\n")

    return panel


def run_scenarios(scenario_vars, output_text, progress_label, progress_bar, pct_label, run_btn=None):
    """Run selected scenarios"""
    # Check if trade data is loaded from Project 0
    if state.loaded_data is None:
        messagebox.showerror(
            "No Trade Data",
            "No trade data loaded!\n\n"
            "Please go to Project 0 → Data Pipeline and load your trade data first.\n\n"
            "Steps:\n"
            "1. Click '0 - Data Pipeline' in sidebar\n"
            "2. Select your trade file\n"
            "3. Click 'Run' to load the data\n"
            "4. Return to Project 1 and try again"
        )
        return

    selected = [key for key, var in scenario_vars.items() if var.get()]

    if not selected:
        messagebox.showwarning("No Selection", "Please select at least one scenario to run.")
        return

    # Phase 56 Fix 3: atomic guard — refuse second concurrent run
    global _running
    with _running_lock:
        if _running:
            messagebox.showwarning("Already Running",
                                   "A scenario run is already in progress.\n"
                                   "Please wait for it to complete.")
            return
        _running = True

    if run_btn:
        run_btn.configure(state="disabled", text="⏳ Running...", bg="#95a5a6")

    output_text.delete('1.0', tk.END)
    output_text.insert(tk.END, f"Starting execution of {len(selected)} scenario(s)...\n")
    output_text.insert(tk.END, f"Selected: {', '.join(selected)}\n")
    output_text.insert(tk.END, "=" * 60 + "\n\n")

    # Reset progress bar
    progress_bar.after(0, lambda: progress_bar.config(
        value=0, style="scenarios.Horizontal.TProgressbar"))
    pct_label.after(0, lambda: pct_label.config(text="0%"))

    # WHY (Phase 55 Fix 7a): STEPS_PER_SCENARIO was hardcoded to 7
    #      above the steps list definition. If a step is ever added or
    #      removed, the progress bar percentages are wrong. Compute
    #      total_steps after building the steps list instead.
    # CHANGED: April 2026 — Phase 55 Fix 7a — dynamic step count
    #          (audit Part D HIGH #89)
    # total_steps computed after steps list is built — see below
    completed_steps = [0]   # mutable counter accessible in closure
    # Phase 49 Fix 5: track failures to choose the right completion dialog
    _scenario_failures = []

    # WHY (Phase A.37.1): Old _update_bar referenced `total_steps` as a
    #      free name. `total_steps` is defined inside run_in_background()
    #      (the nested function), not in run_scenarios()'s scope where
    #      _update_bar lives. Tkinter's `after(0, lambda: _update_bar())`
    #      schedules the lambda to fire on the main thread later — and if
    #      run_in_background() has already returned, the closure can no
    #      longer resolve `total_steps` and raises NameError.
    #
    #      The bug was latent — earlier runs happened to fire callbacks
    #      while run_in_background()'s frame was still live, so Python
    #      found total_steps via outer-scope lookup. After A.37 made
    #      Step 3 ~2× faster, the timing shifted and the callbacks now
    #      fire after the function has returned.
    #
    #      Fix: take total_steps as a parameter. Both call sites inside
    #      run_in_background() already have it in scope and pass it
    #      explicitly via the lambda. Default value 1 prevents division
    #      by zero if the function is somehow called without a value
    #      (defensive — should not happen in practice).
    # CHANGED: April 2026 — Phase A.37.1
    def _update_bar(extra_label="", total_steps=1):
        try:
            _denom = max(int(total_steps), 1)
            pct = int(completed_steps[0] / _denom * 100)
            progress_bar.config(value=pct)
            pct_label.config(text=f"{pct}%  {extra_label}".strip())
        except Exception as _ub_e:
            # WHY (Phase A.37.1): Defensive — never let a progress bar
            #      update crash the UI. The pipeline is what matters,
            #      not the bar.
            print(f"[A.37.1] _update_bar swallowed exception: {_ub_e}")

    def run_in_background():
        # WHY (Phase 56 Fix 3): module-level guard checked again inside
        #      the thread to handle the None-run_btn edge case.
        global _running
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

            # WHY: Force-save firm + data source from UI dropdowns before any
            #      discovery step reads the config. _ui_* vars are module-level
            #      references set by build_run_scenarios_panel so they survive
            #      the scope boundary between build-time and run-time.
            # CHANGED: April 2026 — force-save firm/data-source before run
            try:
                import config_loader as _pre_cl
                _pre_save = {}

                if _ui_firm_var is not None:
                    _fname = _ui_firm_var.get()
                    if _fname and _fname != "None" and _fname in _ui_firm_map:
                        _fd = _ui_firm_map[_fname]
                        _lev_pre = 0
                        try:
                            from shared.prop_firm_engine import get_leverage_for_symbol
                            _lev_pre = get_leverage_for_symbol(_fd, 'XAUUSD')
                        except Exception:
                            pass
                        _stage_pre = _ui_stage_var.get() if _ui_stage_var else 'Evaluation'
                        _acct_pre  = _ui_acct_var.get()  if _ui_acct_var  else '10000'
                        # Get risk from firm trading rules
                        _risk_pre = 1.0
                        _dd_daily_pre = 5.0
                        _dd_total_pre = 10.0
                        try:
                            for _tr in _fd.get('trading_rules', []):
                                if _tr.get('stage') not in (_stage_pre.lower(), 'both'):
                                    continue
                                _tp = _tr.get('parameters', {})
                                if _tr.get('type') in ('eval_settings', 'eval_strategy'):
                                    _risk_pre = _tp.get('risk_pct_range', [0.8, 1.5])[0]
                                    break
                                elif _tr.get('type') == 'funded_accumulate':
                                    _risk_pre = _tp.get('risk_pct', _tp.get('risk_pct_range', [0.3, 0.5])[1])
                                    break
                            # DD limits
                            _ch = _fd.get('challenges', [{}])[0]
                            if _stage_pre.lower() == 'evaluation':
                                _ph = _ch.get('phases', [{}])[0]
                                _dd_daily_pre = float(_ph.get('max_daily_drawdown_pct', 5.0))
                                _dd_total_pre = float(_ph.get('max_total_drawdown_pct', 10.0))
                            else:
                                _funded = _ch.get('funded', {})
                                _dd_daily_pre = float(_funded.get('max_daily_drawdown_pct', 5.0))
                                _dd_total_pre = float(_funded.get('max_total_drawdown_pct', 10.0))
                        except Exception:
                            pass

                        # WHY: Margin-cap — same math as _pf_on_change.
                        # CHANGED: April 2026 — force-save risk before discovery
                        _effective_pre = _risk_pre
                        _do_cap = True
                        try:
                            _do_cap = _ui_margin_cap_var.get() if _ui_margin_cap_var else True
                        except Exception:
                            pass
                        _mc_diag = ""
                        if _do_cap and _lev_pre and float(_acct_pre) > 0:
                            try:
                                _inst_pre = _fd.get('instrument_specs', {}).get('XAUUSD', {})
                                _contract_pre = float(_inst_pre.get('contract_size', 100.0))
                                _pv_pre = float(_inst_pre.get('pip_value_per_lot', 1.0))
                                _sl_pre = 150.0
                                _price_pre = 2500.0
                                try:
                                    _ds_pre = _ui_source_map.get(
                                        _ui_data_source_var.get() if _ui_data_source_var else '', {})
                                    _ds_path_pre = _ds_pre.get('path', '')
                                    if _ds_path_pre:
                                        import glob, csv
                                        _h1_files = glob.glob(os.path.join(_ds_path_pre, '*H1*'))
                                        if not _h1_files:
                                            _h1_files = glob.glob(os.path.join(_ds_path_pre, '*h1*'))
                                        if _h1_files:
                                            with open(_h1_files[0], 'r') as _pf:
                                                _rdr = csv.reader(_pf)
                                                _hdr = next(_rdr)
                                                _ci = None
                                                for _i, _h in enumerate(_hdr):
                                                    if _h.strip().lower() in ('close', 'close_price'):
                                                        _ci = _i
                                                        break
                                                if _ci is not None:
                                                    _last = None
                                                    for _last in _rdr:
                                                        pass
                                                    if _last:
                                                        _price_pre = float(_last[_ci])
                                except Exception:
                                    pass
                                _margin_lot = (_price_pre * _contract_pre) / float(_lev_pre)
                                _max_lots = (float(_acct_pre) * 0.95) / _margin_lot
                                _max_risk_d = _max_lots * _sl_pre * _pv_pre
                                _max_risk_p = round(max(0.1, _max_risk_d / float(_acct_pre) * 100), 1)
                                _mc_diag = (
                                    f"  [MARGIN-CAP] price=${_price_pre:,.0f} | "
                                    f"contract={_contract_pre} | leverage=1:{_lev_pre}\n"
                                    f"  [MARGIN-CAP] margin/lot=${_margin_lot:,.0f} | "
                                    f"max_lots={_max_lots:.3f} | SL={int(_sl_pre)}pip\n"
                                    f"  [MARGIN-CAP] max_risk=${_max_risk_d:.2f} = {_max_risk_p}% | "
                                    f"firm_risk={_risk_pre}%"
                                )
                                if _max_risk_p < _risk_pre:
                                    _effective_pre = _max_risk_p
                                    _mc_diag += f" → CAPPED to {_effective_pre}%"
                                else:
                                    _mc_diag += f" → OK (margin allows {_max_risk_p}%)"
                            except Exception as _mc_e:
                                _mc_diag = f"  [MARGIN-CAP] Error: {_mc_e}"

                        _pre_save.update({
                            'prop_firm_id':      _fd.get('firm_id', ''),
                            'prop_firm_name':    _fname,
                            'prop_firm_stage':   _stage_pre,
                            'prop_firm_account': _acct_pre,
                            'prop_firm_leverage': str(_lev_pre) if _lev_pre else '0',
                            'risk_pct':          str(_effective_pre),
                            'risk_pct_firm':     str(_risk_pre),
                            'dd_daily_pct':      str(_dd_daily_pre),
                            'dd_total_pct':      str(_dd_total_pre),
                            'margin_cap_enabled': '1' if _do_cap else '0',
                        })

                if _ui_data_source_var is not None:
                    _ds_name_pre = _ui_data_source_var.get()
                    _ds_src_pre  = _ui_source_map.get(_ds_name_pre, {})
                    if _ds_src_pre:
                        _pre_save['data_source_id']   = _ds_src_pre['id']
                        _pre_save['data_source_path'] = _ds_src_pre['path']

                if _pre_save:
                    _pre_cl.save(_pre_save)
                    _verify_pre = _pre_cl.load()
                    # WHY: Show full diagnostic in run log so user sees
                    #      exactly what risk/DD/margin values were calculated.
                    # CHANGED: April 2026 — diagnostic output
                    _diag_text = (
                        f"  [P1] Firm: {_verify_pre.get('prop_firm_name','?')} | "
                        f"Stage: {_verify_pre.get('prop_firm_stage','?')}\n"
                        f"  [P1] Account: ${_verify_pre.get('prop_firm_account','?')} | "
                        f"Leverage: 1:{_verify_pre.get('prop_firm_leverage','?')}\n"
                        f"  [P1] Risk: {_verify_pre.get('risk_pct','?')}% "
                        f"(firm says: {_verify_pre.get('risk_pct_firm','?')}%) | "
                        f"Margin-cap: {'ON' if _verify_pre.get('margin_cap_enabled') == '1' else 'OFF'}\n"
                        f"  [P1] DD limits: {_verify_pre.get('dd_daily_pct','?')}% daily / "
                        f"{_verify_pre.get('dd_total_pct','?')}% total\n"
                        f"  [P1] Data source: {_verify_pre.get('data_source_id','?')}"
                    )
                    if _mc_diag:
                        _diag_text += f"\n{_mc_diag}"
                    print(_diag_text)
                    output_text.after(0, lambda m=_diag_text: output_text.insert(tk.END, m + "\n\n"))
            except Exception as _pre_e:
                print(f"[RUN] Force-save warning: {_pre_e}")

            # WHY (Phase 56 Fix 2): Old code imported and ran legacy steps
            #      3-7 (label_trades, train_model, shap, extract_rules,
            #      validate). These write rules_report.txt — a format the
            #      rest of the app has moved past. The results panel now
            #      reads analysis_report.json produced by analyze.run_analysis.
            #      Replace the 7-step pipeline with the modern 3-step path
            #      so Run Scenarios → View Results actually works.
            # CHANGED: April 2026 — Phase 56 Fix 2 — modern 3-step pipeline
            #          (audit Part D HIGH #88)
            import step1_align_price
            import step2_compute_indicators

            # WHY: PRICE_DATA_FOLDER is cached at module import time.
            #      Force-update from config every time steps run.
            #      Import config_loader inline — _cl/_cfg are not in scope here
            #      (run_scenarios is a module-level function, not a closure).
            # CHANGED: April 2026 — fix stale data source path
            try:
                import config_loader as _run_cl
                _ds_cfg = _run_cl.load()
                _ds_path = _ds_cfg.get('data_source_path', '')
                if _ds_path and os.path.isdir(_ds_path):
                    step1_align_price.PRICE_DATA_FOLDER = _ds_path
                    step2_compute_indicators.PRICE_DATA_FOLDER = _ds_path
                    print(f"[P1] Data source updated: {_ds_path}")
                else:
                    print(f"[P1] Using default data source: {step1_align_price.PRICE_DATA_FOLDER}")
            except Exception as _ds_e:
                print(f"[P1] Could not update data source: {_ds_e}")

            # WHY: align_all_timeframes runs once for ALL TFs together —
            #      it doesn't need to run per scenario. Only run it on the
            #      first iteration.
            # WHY (Phase 49 Fix 4b): Old code used a closure-local
            #      `step1_already_run = [False]` flag that reset on
            #      every Run click. Clicking Run twice re-ran step1
            #      from scratch even though the first run's output
            #      was on disk. Use a module-level dict keyed on the
            #      output_dir so re-clicks within the same session
            #      reuse the existing aligned_trades.csv if present.
            # CHANGED: April 2026 — fix step1 function name + run-once logic
            # CHANGED: April 2026 — Phase 49 Fix 4b — persistent run flags
            #          (audit Part D HIGH #90)
            global _step1_run_cache
            # Clear cache so step1 reruns if data source changed
            if getattr(step1_align_price, 'PRICE_DATA_FOLDER', '') != getattr(step1_align_price, '_last_folder', ''):
                _step1_run_cache.clear()
                step1_align_price._last_folder = getattr(step1_align_price, 'PRICE_DATA_FOLDER', '')
            _outputs_dir = os.path.normpath(
                os.path.join(os.path.dirname(__file__), '..', 'outputs')
            )
            _cache_key = _outputs_dir
            step1_already_run = [_cache_key in _step1_run_cache]

            def _step1_wrapper(scenario):
                if step1_already_run[0]:
                    print(f"  (Step 1 already run for previous scenario — skipping)")
                    return True
                # WHY: Print which data source is being used so user can verify.
                # CHANGED: April 2026 — data source confirmation
                print(f"[P1] Using data source: {step1_align_price.PRICE_DATA_FOLDER}")
                result = step1_align_price.align_all_timeframes()
                step1_already_run[0] = (result is not None)
                if step1_already_run[0]:
                    _step1_run_cache[_cache_key] = True
                return step1_already_run[0]

            # WHY: compute_features() processes ALL timeframes at once —
            #      same pattern as step1. Only run it on the first iteration.
            # CHANGED: April 2026 — fix step2 function name + run-once logic
            step2_already_run = [False]

            def _step2_wrapper(scenario):
                # WHY: step2 saves feature_matrix.csv to outputs/, but step3+ look
                #      for it inside outputs/scenario_{name}/. Copy it to every
                #      selected scenario folder so the per-scenario steps find it.
                # CHANGED: April 2026 — copy feature matrix to scenario folders
                import shutil

                if not step2_already_run[0]:
                    result = step2_compute_indicators.compute_features()
                    step2_already_run[0] = (result is not None)
                    if not step2_already_run[0]:
                        return False

                # Always copy to the current scenario folder (even if step2 was already run)
                outputs_dir = os.path.normpath(
                    os.path.join(os.path.dirname(__file__), '..', 'outputs')
                )
                master_file = os.path.join(outputs_dir, 'feature_matrix.csv')

                if not os.path.exists(master_file):
                    print(f"  ERROR: master feature_matrix.csv not found at {master_file}")
                    return False

                scenario_dir = os.path.join(outputs_dir, f'scenario_{scenario}')
                os.makedirs(scenario_dir, exist_ok=True)

                target_file = os.path.join(scenario_dir, 'feature_matrix.csv')
                try:
                    shutil.copy2(master_file, target_file)
                    print(f"  Copied feature_matrix.csv -> scenario_{scenario}/")
                except Exception as e:
                    print(f"  ERROR copying to scenario_{scenario}: {e}")
                    return False

                # Also copy aligned_trades.csv if step3 needs it
                master_aligned = os.path.join(outputs_dir, 'aligned_trades.csv')
                if os.path.exists(master_aligned):
                    try:
                        shutil.copy2(master_aligned, os.path.join(scenario_dir, 'aligned_trades.csv'))
                    except Exception:
                        pass

                return True

            # _analyze_wrapper: run analyze.run_analysis once (all scenarios
            # share the same feature matrix), then copy analysis_report.json
            # into each scenario's outputs/ subdirectory so the results panel
            # can retrieve it by scenario name.
            analyze_already_run = [False]

            def _analyze_wrapper(scenario):
                import shutil
                import analyze as _analyze_mod
                _out = os.path.normpath(
                    os.path.join(os.path.dirname(__file__), '..', 'outputs')
                )
                if not analyze_already_run[0]:
                    _fm = os.path.join(_out, 'feature_matrix.csv')
                    if os.path.exists(_fm):
                        _analyze_mod.run_analysis(feature_matrix_path=_fm)
                    else:
                        _analyze_mod.run_analysis()
                    analyze_already_run[0] = True

                # Copy analysis_report.json into the scenario subfolder
                _src = os.path.join(_out, 'analysis_report.json')
                _scenario_dir = os.path.join(_out, f'scenario_{scenario}')
                os.makedirs(_scenario_dir, exist_ok=True)
                if os.path.exists(_src):
                    try:
                        shutil.copy2(_src, os.path.join(_scenario_dir, 'analysis_report.json'))
                    except Exception as _ce:
                        print(f"  WARNING: could not copy analysis_report.json to "
                              f"scenario_{scenario}: {_ce}")
                return True

            # WHY (Phase A.31.1): bot_entry_discovery is now Step 4 of
            #      the pipeline. Both rule producers run from one click.
            #      Like analyze, it runs once total (not per scenario)
            #      because it processes all timeframes internally and
            #      writes a single bot_entry_rules.json. The
            #      `bot_entry_already_run` latch enforces this.
            #      Tunables come from p1_config.json via the four
            #      spinboxes in the Bot Entry Discovery card built in
            #      build_panel above.
            # CHANGED: April 2026 — Phase A.31.1
            bot_entry_already_run = [False]

            def _bot_entry_wrapper(scenario):
                if bot_entry_already_run[0]:
                    print("  (Bot entry discovery already run — skipping)")
                    return True
                try:
                    import config_loader as _bcl
                    _bcfg = _bcl.load()
                    _be_max_rules    = int(  _bcfg.get('bot_entry_max_rules',    '25'))
                    _be_max_depth    = int(  _bcfg.get('bot_entry_max_depth',    '4'))
                    _be_min_coverage = int(  _bcfg.get('bot_entry_min_coverage', '20'))
                    _be_min_wr       = float(_bcfg.get('bot_entry_min_win_rate', '0.55'))
                except Exception:
                    _be_max_rules, _be_max_depth, _be_min_coverage, _be_min_wr = (
                        25, 4, 20, 0.55,
                    )

                print(
                    f"  Bot Entry Discovery params: "
                    f"max_rules={_be_max_rules} max_depth={_be_max_depth} "
                    f"min_coverage={_be_min_coverage} min_win_rate={_be_min_wr}"
                )
                try:
                    from project1_reverse_engineering.bot_entry_discovery import (
                        discover_bot_entry_rules,
                    )
                except ImportError as _ie:
                    print(f"  ERROR: bot_entry_discovery not importable: {_ie}")
                    return False
                except Exception as _e:
                    print(f"  ERROR loading bot_entry_discovery: {_e}")
                    return False

                try:
                    result = discover_bot_entry_rules(
                        max_rules=_be_max_rules,
                        max_depth=_be_max_depth,
                        min_coverage=_be_min_coverage,
                        min_win_rate=_be_min_wr,
                        progress_callback=lambda m: print(m),
                    )
                except FileNotFoundError as _fe:
                    print(f"  ERROR: {_fe}")
                    return False
                except Exception as _e:
                    import traceback as _tb
                    print(f"  ERROR running bot_entry_discovery: "
                          f"{type(_e).__name__}: {_e}")
                    print(_tb.format_exc())
                    return False

                _rules = result.get('rules', [])
                _action_dist = {}
                for _r in _rules:
                    _a = _r.get('action', 'MISSING')
                    _action_dist[_a] = _action_dist.get(_a, 0) + 1
                print(
                    f"  Bot entry rules written: {len(_rules)}  "
                    f"action distribution: {_action_dist}"
                )
                bot_entry_already_run[0] = True
                return True

            steps = [
                ("Step 1: Align Price",              _step1_wrapper),
                ("Step 2: Compute Indicators",       _step2_wrapper),
                ("Step 3: Analyze & Extract Rules",  _analyze_wrapper),
                ("Step 4: Bot Entry Discovery",      _bot_entry_wrapper),
            ]
            # total_steps derived from actual list (Phase 55 Fix 7a)
            total_steps = len(selected) * len(steps)

            results = {}

            for scenario in selected:
                def log(msg):
                    output_text.after(0, lambda m=msg: output_text.insert(tk.END, m + "\n"))
                    output_text.after(0, lambda: output_text.see(tk.END))

                def update_progress(msg):
                    progress_label.after(0, lambda m=msg: progress_label.config(text=m))

                log(f"\n{'#' * 60}")
                log(f"# SCENARIO: {scenario}")
                log(f"{'#' * 60}\n")
                update_progress(f"Running {scenario}...")

                scenario_success = True

                for step_name, step_func in steps:
                    log(f">>> {step_name} — {scenario}")
                    update_progress(f"{scenario}: {step_name}")
                    extra = f"({scenario} — {step_name})"
                    # WHY (Phase A.37.1): pass total_steps explicitly so
                    #      the lambda doesn't need to resolve it from a
                    #      vanished closure frame.
                    # CHANGED: April 2026 — Phase A.37.1
                    progress_bar.after(0, lambda e=extra, t=total_steps: _update_bar(e, total_steps=t))

                    try:
                        import io
                        # WHY (Phase 54 Fix 6): Old code used StringIO
                        #      which (a) grows unbounded and (b) doesn't
                        #      restore stdout if the step raises mid-run,
                        #      so subsequent runs lose all output to the
                        #      orphaned StringIO. Use a bounded buffer
                        #      (same as Phase 53 Fix 5 in p1 config
                        #      panel) and a try/finally guard around
                        #      the redirect block.
                        # CHANGED: April 2026 — Phase 54 Fix 6 — safe stdout redirect
                        #          (audit Part D MED #91)
                        class _BoundedBuf:
                            def __init__(self, max_lines=2000):
                                self._lines = []
                                self._max = max_lines
                            def write(self, s):
                                if not s:
                                    return
                                for line in str(s).splitlines():
                                    self._lines.append(line)
                                    if len(self._lines) > self._max:
                                        self._lines.pop(0)
                            def flush(self):
                                pass
                            def getvalue(self):
                                return '\n'.join(self._lines)
                        # Phase 57 Fix 5: serialise stdout redirect
                        with _stdout_lock:
                            old_stdout = sys.stdout
                            sys.stdout = buffer = _BoundedBuf(max_lines=2000)
                            try:
                                success = step_func(scenario)
                            finally:
                                sys.stdout = old_stdout
                                captured = buffer.getvalue()

                        if captured:
                            for line in captured.split('\n'):
                                if line.strip():
                                    log(f"  {line}")

                        completed_steps[0] += 1
                        # WHY (Phase A.37.1): pass total_steps explicitly.
                        # CHANGED: April 2026 — Phase A.37.1
                        progress_bar.after(0, lambda t=total_steps: _update_bar(total_steps=t))

                        if not success:
                            log(f"✗ FAILED: {step_name}")
                            scenario_success = False
                            break

                        log(f"✓ COMPLETED: {step_name}\n")

                    except Exception as e:
                        completed_steps[0] += 1
                        # WHY (Phase A.37.1): pass total_steps explicitly.
                        # CHANGED: April 2026 — Phase A.37.1
                        progress_bar.after(0, lambda t=total_steps: _update_bar(total_steps=t))
                        log(f"✗ ERROR: {str(e)}")
                        import traceback
                        log(traceback.format_exc())
                        scenario_success = False
                        break

                results[scenario] = scenario_success

                if scenario_success:
                    log(f"\n✓ SCENARIO {scenario} COMPLETED SUCCESSFULLY\n")
                else:
                    log(f"\n✗ SCENARIO {scenario} FAILED\n")
                    _scenario_failures.append(f"{scenario}: pipeline failed")
                    progress_bar.after(0, lambda: progress_bar.config(
                        style="scenarios.error.Horizontal.TProgressbar"))

            # Summary
            log("\n" + "=" * 60)
            log("EXECUTION SUMMARY")
            log("=" * 60 + "\n")

            for scenario, success in results.items():
                status = "✓ SUCCESS" if success else "✗ FAILED"
                log(f"  {scenario:10s} {status}")

            successful = sum(1 for s in results.values() if s)
            log(f"\nCompleted: {successful}/{len(selected)} scenarios successful")

            update_progress(f"Done: {successful}/{len(selected)} successful")
            progress_bar.after(0, lambda: pct_label.config(
                text=f"100%  — {successful}/{len(selected)} scenarios OK"))
            progress_bar.after(0, lambda: progress_bar.config(value=100))

            # WHY (Phase 55 Fix 7b): Title said "Execution Complete"
            #      regardless of outcome. A user who saw 0/3 successful
            #      still got a green-sounding "Complete". Now the title
            #      and icon reflect the true outcome.
            # CHANGED: April 2026 — Phase 55 Fix 7b — outcome-aware title
            #          (audit Part D HIGH #92)
            _all_ok  = (successful == len(selected))
            _none_ok = (successful == 0)
            _title   = ("All Scenarios Complete" if _all_ok
                        else "Scenarios Failed" if _none_ok
                        else "Partial Success")
            _show    = messagebox.showinfo if not _none_ok else messagebox.showwarning
            if _scenario_failures:
                _fail_msg = f"Completed {len(selected)} scenario(s).\n" \
                            f"{successful} successful, {len(selected)-successful} failed.\n\n" \
                            f"Failures:\n" + "\n".join(f"  • {f}" for f in _scenario_failures)
                output_text.after(0, lambda: _show(
                    _title, _fail_msg))
            else:
                output_text.after(0, lambda: _show(
                    _title,
                    f"Completed {len(selected)} scenario(s).\n"
                    f"{successful} successful, {len(selected)-successful} failed.\n\n"
                    f"Check the console output for details."))

        except Exception as e:
            def show_error():
                output_text.insert(tk.END, f"\n\nFATAL ERROR: {str(e)}\n")
                import traceback
                output_text.insert(tk.END, traceback.format_exc())
                messagebox.showerror("Error", f"Execution failed:\n{str(e)}")
            output_text.after(0, show_error)

        finally:
            global _running
            _running = False
            if run_btn:
                run_btn.after(0, lambda: run_btn.configure(
                    state="normal", text="🚀 Run Selected Scenarios", bg="#27ae60"))

    # Run in background thread
    thread = threading.Thread(target=run_in_background, daemon=True)
    thread.start()


def update_data_status_display():
    """Update the data status indicator"""
    global _data_status_frame

    if _data_status_frame is None:
        return

    # Clear existing widgets
    for widget in _data_status_frame.winfo_children():
        widget.destroy()

    # Update with current status
    if state.loaded_data is not None:
        num_trades = len(state.loaded_data)
        status_text = f"✓ {num_trades} trades loaded from Project 0"
        status_color = "#27ae60"
    else:
        status_text = "⚠️ No trade data loaded - Load data in Project 0 first"
        status_color = "#e74c3c"

    tk.Label(_data_status_frame, text=status_text,
            bg="#e8f4f8", fg=status_color,
            font=("Segoe UI", 9, "bold")).pack()


def refresh():
    """Refresh the panel - update data status when panel is shown"""
    update_data_status_display()

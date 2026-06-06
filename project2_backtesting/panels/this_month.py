"""
This Month Panel — rank backtested rules by performance in months whose
market regime matches the current month.

Regime dimensions:
  trend  — Trend / Range       (ADX vs threshold)
  vol    — High / Low          (ATR vs rolling median)
  dir    — Bull / Bear         (close vs EMA 200)

All indicator math is inline (no shared helper); avoids the trade-feature
DataFrame that analyze.py's analyze_market_regimes uses.
"""

import os
import json
import threading
import tkinter as tk
from tkinter import ttk
import numpy as np

_HERE  = os.path.dirname(os.path.abspath(__file__))
_P2DIR = os.path.dirname(_HERE)

# ─── module-level state ──────────────────────────────────────────────────────
_worker_running    = False
_after_sr          = [None]   # debounce handle for scroll-region recompute
# CHANGED: June 2026 — capture the SPECIFIC candle-load failure so the panel can
#          show a meaningful message instead of the generic "check P2 config"
_LAST_CANDLE_ERROR  = None
# CHANGED: June 2026 — record which source was actually used (for status display)
_CANDLE_SOURCE_LABEL = None

# ─── Diagnostics ─────────────────────────────────────────────────────────────
_DIAG_LINES  = []           # accumulated log lines (kept forever, reset on each refresh)
_DIAG_WINDOW = [None]       # Toplevel reference so we reuse an existing window
_DIAG_TEXT   = [None]       # scrolledtext widget inside the diagnostics window


def _diag(msg):
    """Append a timestamped line to the diagnostics buffer and echo to open window."""
    import time as _t
    line = f"[{_t.strftime('%H:%M:%S')}] {msg}"
    _DIAG_LINES.append(line)
    if _DIAG_TEXT[0] is not None:
        try:
            _DIAG_TEXT[0].configure(state='normal')
            _DIAG_TEXT[0].insert('end', line + '\n')
            _DIAG_TEXT[0].see('end')
            _DIAG_TEXT[0].configure(state='disabled')
        except Exception:
            pass


def _open_diagnostics():
    """Open (or raise) the diagnostics window."""
    from tkinter.scrolledtext import ScrolledText as _ST
    import tkinter as _tk
    if _DIAG_WINDOW[0] is not None:
        try:
            _DIAG_WINDOW[0].lift()
            _DIAG_WINDOW[0].focus_force()
            return
        except Exception:
            _DIAG_WINDOW[0] = None

    win = _tk.Toplevel()
    win.title('This Month — Diagnostics')
    win.geometry('780x440')
    _DIAG_WINDOW[0] = win

    st = _ST(win, wrap='none', font=('Consolas', 9), state='disabled')
    st.pack(fill='both', expand=True, padx=6, pady=6)
    _DIAG_TEXT[0] = st

    # Populate with lines already collected
    st.configure(state='normal')
    for line in _DIAG_LINES:
        st.insert('end', line + '\n')
    st.see('end')
    st.configure(state='disabled')

    def _on_close():
        _DIAG_WINDOW[0] = None
        _DIAG_TEXT[0]   = None
        win.destroy()

    win.protocol('WM_DELETE_WINDOW', _on_close)

    _tk.Button(win, text='Clear', command=lambda: (
        _DIAG_LINES.clear(),
        st.configure(state='normal'),
        st.delete('1.0', 'end'),
        st.configure(state='disabled'),
    )).pack(side='right', padx=6, pady=4)


# ═════════════════════════════════════════════════════════════════════════════
# Indicator helpers (Wilder smoothing, no external deps beyond numpy)
# ═════════════════════════════════════════════════════════════════════════════

def _wilder_smooth(arr, period):
    """Wilder smoothed moving average (alpha = 1/period)."""
    out = np.full(len(arr), np.nan)
    if len(arr) < period:
        return out
    out[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        out[i] = (out[i - 1] * (period - 1) + arr[i]) / period
    return out


def _ema(arr, period):
    """Standard EMA (alpha = 2/(period+1))."""
    alpha = 2.0 / (period + 1)
    out   = np.full(len(arr), np.nan)
    # first valid EMA seed = SMA of first `period` values
    if len(arr) < period:
        return out
    out[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        out[i] = arr[i] * alpha + out[i - 1] * (1.0 - alpha)
    return out


def _adx(high, low, close, period=14):
    """Returns ADX array (same length as inputs, NaN before warmup)."""
    n = len(close)
    tr  = np.zeros(n)
    pdm = np.zeros(n)
    ndm = np.zeros(n)

    for i in range(1, n):
        hl   = high[i]  - low[i]
        hpc  = abs(high[i]  - close[i - 1])
        lpc  = abs(low[i]   - close[i - 1])
        tr[i] = max(hl, hpc, lpc)

        up   = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        pdm[i] = up   if up > down and up > 0   else 0.0
        ndm[i] = down if down > up and down > 0 else 0.0

    s_tr  = _wilder_smooth(tr,  period)
    s_pdm = _wilder_smooth(pdm, period)
    s_ndm = _wilder_smooth(ndm, period)

    pdi = np.where(s_tr > 0, 100.0 * s_pdm / s_tr, 0.0)
    ndi = np.where(s_tr > 0, 100.0 * s_ndm / s_tr, 0.0)
    denom = pdi + ndi
    dx    = np.where(denom > 0, 100.0 * np.abs(pdi - ndi) / denom, 0.0)

    adx_arr = _wilder_smooth(dx, period)
    return adx_arr


def _atr(high, low, close, period=14):
    """Returns ATR array (same length as inputs)."""
    n  = len(close)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i]  - close[i - 1]),
                    abs(low[i]   - close[i - 1]))
    return _wilder_smooth(tr, period)


# ═════════════════════════════════════════════════════════════════════════════
# Candle loading + monthly regime labelling
# ═════════════════════════════════════════════════════════════════════════════

def _resolve_candle_path(tf):
    """Return (path, symbol) for the requested TF using P2 config, then P1 fallback."""
    import importlib.util as _ilu

    # ── P2 config ────────────────────────────────────────────────────────────
    symbol      = 'XAUUSD'
    data_src_id = ''
    data_path   = ''
    try:
        from project2_backtesting.panels.configuration import load_config as _p2cfg
        cfg = _p2cfg()
        symbol      = cfg.get('symbol', 'XAUUSD') or 'XAUUSD'
        data_src_id = cfg.get('data_source_id', '') or ''
        data_path   = cfg.get('data_source_path', '') or ''
        if data_path and not os.path.isdir(data_path):
            data_path = ''
        if not data_path and data_src_id:
            try:
                from shared.data_sources import get_source_path
                data_path = get_source_path(data_src_id) or ''
            except Exception:
                pass
    except Exception:
        pass

    # ── P1 config fallback ───────────────────────────────────────────────────
    if not data_path:
        try:
            _p1_path = os.path.join(
                os.path.dirname(os.path.dirname(_HERE)),
                'project1_reverse_engineering', 'config_loader.py')
            _spec = _ilu.spec_from_file_location('_p1cl', _p1_path)
            _mod  = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            p1 = _mod.load()
            data_path = p1.get('data_source_path', '') or ''
            if not data_path:
                data_src_id = data_src_id or p1.get('data_source_id', '') or ''
                if data_src_id:
                    try:
                        from shared.data_sources import get_source_path
                        data_path = get_source_path(data_src_id) or ''
                    except Exception:
                        pass
        except Exception:
            pass

    if not data_path:
        data_path = os.path.join(os.path.dirname(os.path.dirname(_HERE)), 'data')

    # ── candidate paths ───────────────────────────────────────────────────────
    sym_up  = symbol.upper()
    sym_lo  = symbol.lower()
    for p in [
        os.path.join(data_path, f'{sym_up}_{tf}.csv'),
        os.path.join(data_path, f'{sym_lo}_{tf}.csv'),
        os.path.join(data_path, f'{symbol}_{tf}.csv'),
        os.path.join(data_path, symbol, f'{tf}.csv'),
    ]:
        if os.path.isfile(p):
            return p, symbol

    # ── auto-discover a source under data/sources/* ───────────────────────────
    # WHY: P2 config often has data_source_id=None, so the bare data/ fallback
    #      finds nothing even though candles exist under data/sources/<id>/.
    #      Scan known sources (richest first) rather than giving up.
    # CHANGED: June 2026 — auto-discover when config is empty
    global _CANDLE_SOURCE_LABEL
    try:
        from shared.data_sources import list_sources
        for src in sorted(list_sources(),
                          key=lambda s: s.get('candle_count', 0), reverse=True):
            spath = src.get('path')
            if not spath or not os.path.isdir(spath):
                continue
            for _name in (f'{sym_up}_{tf}.csv', f'{sym_lo}_{tf}.csv',
                          f'{symbol}_{tf}.csv'):
                cand = os.path.join(spath, _name)
                if os.path.isfile(cand):
                    _CANDLE_SOURCE_LABEL = src.get('name') or os.path.basename(spath)
                    return cand, symbol
    except Exception:
        pass

    # ── last resort: glob under data/sources ─────────────────────────────────
    try:
        import glob
        base = os.path.join(os.path.dirname(os.path.dirname(_HERE)), 'data', 'sources')
        for _name in (f'{sym_up}_{tf}.csv', f'{sym_lo}_{tf}.csv'):
            hits = glob.glob(os.path.join(base, '*', _name))
            if hits:
                _CANDLE_SOURCE_LABEL = os.path.basename(os.path.dirname(hits[0]))
                return hits[0], symbol
    except Exception:
        pass

    return None, symbol


def _load_candles(tf):
    """Load OHLC candles for TF. Returns DataFrame or None.
    On a git-LFS pointer stub, auto-pull via the shared helper and retry; if
    the file still isn't real data, stores the reason in _LAST_CANDLE_ERROR and
    returns None so the panel can show WHY instead of the generic message.
    CHANGED: June 2026 — auto-handle LFS stubs (reuse assert_not_lfs_stub)
    """
    global _LAST_CANDLE_ERROR
    _LAST_CANDLE_ERROR = None
    try:
        import pandas as pd
        path, _ = _resolve_candle_path(tf)
        if path is None:
            return None
        # Detect + auto-pull LFS stubs (same helper the backtest uses)
        try:
            from shared.data_sources import assert_not_lfs_stub
            assert_not_lfs_stub(path)   # auto-runs git lfs pull; raises if still a stub
        except Exception as _lfs_err:
            _LAST_CANDLE_ERROR = str(_lfs_err)
            return None
        df = pd.read_csv(path, encoding='utf-8-sig')
        # Normalise timestamp column
        ts_col = next(
            (c for c in df.columns
             if c.lower().strip() in ('timestamp', 'time', 'date', 'datetime',
                                      'open_time', 'opentime')),
            df.columns[0]
        )
        df = df.rename(columns={ts_col: 'timestamp'})
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce', utc=False)
        df = df.dropna(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
        # Ensure OHLC columns present (case-insensitive)
        col_map = {c.lower(): c for c in df.columns}
        for need in ('open', 'high', 'low', 'close'):
            if need not in df.columns and need in col_map:
                df = df.rename(columns={col_map[need]: need})
        for need in ('high', 'low', 'close'):
            if need not in df.columns:
                _LAST_CANDLE_ERROR = (
                    f"{os.path.basename(path)} has no OHLC columns "
                    f"(found: {list(df.columns)[:6]}). File may be malformed.")
                return None
        return df
    except Exception as _e:
        _LAST_CANDLE_ERROR = f"{type(_e).__name__}: {_e}"
        return None


def label_months(df, adx_threshold=25):
    """
    Assign regime labels to each calendar month in `df`.

    Returns dict:  {'YYYY-MM': {'trend': 'Trend'/'Range',
                                'vol':   'High'/'Low',
                                'dir':   'Bull'/'Bear'}, ...}

    Regime per month = the most common label across candles in that month.
    ATR high/low threshold = median ATR of the entire series.
    """
    import pandas as pd

    n = len(df)
    if n < 250:
        return {}

    high  = df['high'].to_numpy(dtype=float)
    low   = df['low'].to_numpy(dtype=float)
    close = df['close'].to_numpy(dtype=float)

    adx_arr  = _adx(high, low, close, period=14)
    atr_arr  = _atr(high, low, close, period=14)
    ema200   = _ema(close, period=200)

    atr_median = float(np.nanmedian(atr_arr))

    df = df.copy()
    df['_adx']    = adx_arr
    df['_atr']    = atr_arr
    df['_ema200'] = ema200
    df['_month']  = df['timestamp'].dt.to_period('M').astype(str)

    result = {}
    for month, grp in df.groupby('_month'):
        valid = grp.dropna(subset=['_adx', '_atr', '_ema200'])
        if len(valid) < 5:
            continue

        trend_votes = (valid['_adx'] >= adx_threshold).sum()
        trend_lbl   = 'Trend' if trend_votes > len(valid) / 2 else 'Range'

        vol_votes   = (valid['_atr'] >= atr_median).sum()
        vol_lbl     = 'High' if vol_votes > len(valid) / 2 else 'Low'

        dir_votes   = (valid['close'] >= valid['_ema200']).sum()
        dir_lbl     = 'Bull' if dir_votes > len(valid) / 2 else 'Bear'

        result[month] = {'trend': trend_lbl, 'vol': vol_lbl, 'dir': dir_lbl}

    return result


def _current_regime(month_labels):
    """Return regime dict for the most recent complete month, or empty dict."""
    if not month_labels:
        return {}
    latest = sorted(month_labels.keys())[-1]
    return month_labels[latest]


def _adx_threshold():
    """Read adx_trend_threshold from P1 config; default 25."""
    try:
        import importlib.util as _ilu
        _p1_path = os.path.join(
            os.path.dirname(os.path.dirname(_HERE)),
            'project1_reverse_engineering', 'config_loader.py')
        _spec = _ilu.spec_from_file_location('_p1cl2', _p1_path)
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return float(_mod.load().get('adx_trend_threshold', '25') or '25')
    except Exception:
        return 25.0


# ═════════════════════════════════════════════════════════════════════════════
# Rule scoring
# ═════════════════════════════════════════════════════════════════════════════

def _score_rule(monthly_rows, matching_months, rank_by):
    """
    Given a list of compute_monthly_pnl rows for ONLY the matching months,
    compute a composite score for display.

    rank_by: 'wr' | 'pips' | 'pass' | 'score'
    Returns dict with wr, pips, pass_pct, variance, score.
    """
    # CHANGED: June 2026 — split walrus out (illegal directly after `not`)
    matching_rows = [r for r in monthly_rows if r['month'] in matching_months]
    if not matching_rows:
        return None

    trades_total = sum(r['trades'] for r in matching_rows)
    wins_total   = sum(r['wins']   for r in matching_rows)
    wr           = round(wins_total / trades_total * 100, 1) if trades_total > 0 else 0.0
    pips_total   = round(sum(r['pnl_pips'] for r in matching_rows), 1)
    pass_pct     = round(sum(1 for r in matching_rows if r['pnl_pips'] > 0)
                         / len(matching_rows) * 100, 1)
    monthly_pips = [r['pnl_pips'] for r in matching_rows]
    variance     = round(float(np.std(monthly_pips)), 1) if len(monthly_pips) > 1 else 0.0

    # Composite: WR 35% + pass% 35% + pips normalised 20% - variance penalty 10%
    # Normalisation constants are loose — just for relative ranking.
    _pips_norm = max(-1.0, min(1.0, pips_total / max(1, trades_total * 10)))
    score = round(
        wr       * 0.35
        + pass_pct * 0.35
        + _pips_norm * 20.0
        - min(variance / 20.0, 10.0),
        2,
    )

    return {
        'wr':           wr,
        'pips':         pips_total,
        'pass_pct':     pass_pct,
        'variance':     variance,
        'score':        score,
        'n_match':      len(matching_rows),
        'match_months': sorted(r['month'] for r in matching_rows),  # CHANGED: June 2026 — which months
        'trades':       trades_total,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Panel
# ═════════════════════════════════════════════════════════════════════════════

def build_panel(parent):
    import pandas as pd

    BG        = '#f0f2f5'
    CARD_BG   = '#ffffff'
    HDR_BG    = '#2c3e50'
    HDR_FG    = '#ffffff'
    AMBER     = '#e67e22'
    GREEN     = '#27ae60'
    RED       = '#c0392b'
    GREY_ROW  = '#bbbbbb'

    # ── outer frame ──────────────────────────────────────────────────────────
    outer = tk.Frame(parent, bg=BG)
    outer.pack(fill='both', expand=True)

    # ── header ───────────────────────────────────────────────────────────────
    hdr = tk.Frame(outer, bg=HDR_BG, pady=8)
    hdr.pack(fill='x')
    tk.Label(hdr, text='This Month — Regime-Matched Rule Ranking',
             bg=HDR_BG, fg=HDR_FG,
             font=('Segoe UI', 13, 'bold')).pack(side='left', padx=12)

    # ── regime banner ────────────────────────────────────────────────────────
    regime_bar = tk.Frame(outer, bg='#dce8f5', pady=4)
    regime_bar.pack(fill='x')
    _regime_lbl = tk.Label(regime_bar, text='Current regime: (not loaded)',
                           bg='#dce8f5', fg='#1a252f',
                           font=('Segoe UI', 10, 'italic'))
    _regime_lbl.pack(side='left', padx=10)

    _warn_lbl = tk.Label(regime_bar, text='', bg='#dce8f5',
                         fg=RED, font=('Segoe UI', 9))
    _warn_lbl.pack(side='left', padx=6)

    # ── controls ─────────────────────────────────────────────────────────────
    ctrl = tk.Frame(outer, bg=BG, pady=6)
    ctrl.pack(fill='x', padx=10)

    # Regime dimension checkboxes
    _use_trend = tk.BooleanVar(value=True)
    _use_vol   = tk.BooleanVar(value=True)
    _use_dir   = tk.BooleanVar(value=False)

    tk.Label(ctrl, text='Match on:', bg=BG,
             font=('Segoe UI', 9)).grid(row=0, column=0, sticky='w', padx=(0, 4))
    tk.Checkbutton(ctrl, text='Trend', variable=_use_trend, bg=BG,
                   font=('Segoe UI', 9)).grid(row=0, column=1, padx=4)
    tk.Checkbutton(ctrl, text='Volatility', variable=_use_vol, bg=BG,
                   font=('Segoe UI', 9)).grid(row=0, column=2, padx=4)
    tk.Checkbutton(ctrl, text='Direction', variable=_use_dir, bg=BG,
                   font=('Segoe UI', 9)).grid(row=0, column=3, padx=4)

    _match_lbl = tk.Label(ctrl, text='Matching past months: —',
                          bg=BG, fg='#555', font=('Segoe UI', 9, 'italic'))
    _match_lbl.grid(row=0, column=4, padx=12, sticky='w')

    # Second row: Rank by / Min months / Hide thin / Refresh
    tk.Label(ctrl, text='Rank by:', bg=BG,
             font=('Segoe UI', 9)).grid(row=1, column=0, sticky='w', pady=(4, 0))

    _rank_var = tk.StringVar(value='Score')
    rank_combo = ttk.Combobox(ctrl, textvariable=_rank_var, width=14, state='readonly',
                              values=['Score', 'Win rate', 'Net pips', 'Pass rate'])
    rank_combo.grid(row=1, column=1, columnspan=2, padx=4, pady=(4, 0), sticky='w')

    tk.Label(ctrl, text='Min months:', bg=BG,
             font=('Segoe UI', 9)).grid(row=1, column=3, sticky='w', pady=(4, 0), padx=(8, 0))
    _min_months_var = tk.StringVar(value='4')
    _min_months_entry = tk.Entry(ctrl, textvariable=_min_months_var, width=4,
                                 font=('Segoe UI', 9))
    _min_months_entry.grid(row=1, column=4, pady=(4, 0), sticky='w')

    _hide_thin = tk.BooleanVar(value=False)
    tk.Checkbutton(ctrl, text='Hide < min months', variable=_hide_thin, bg=BG,
                   font=('Segoe UI', 9)).grid(row=1, column=5, padx=8, pady=(4, 0))

    _refresh_btn = tk.Button(ctrl, text='↻ Refresh', bg='#3498db', fg='white',
                             font=('Segoe UI', 9, 'bold'), relief='flat',
                             padx=10, pady=3)
    _refresh_btn.grid(row=1, column=6, padx=4, pady=(4, 0))

    _diag_btn = tk.Button(ctrl, text='🔍 Diagnostics', bg='#7f8c8d', fg='white',
                          font=('Segoe UI', 9), relief='flat',
                          padx=8, pady=3, command=_open_diagnostics)
    _diag_btn.grid(row=1, column=7, padx=4, pady=(4, 0))

    _status_lbl = tk.Label(ctrl, text='', bg=BG, fg='#555',
                           font=('Segoe UI', 9, 'italic'))
    _status_lbl.grid(row=2, column=0, columnspan=7, sticky='w', pady=(2, 0))

    # ── scrollable treeview area ──────────────────────────────────────────────
    tree_frame = tk.Frame(outer, bg=BG)
    tree_frame.pack(fill='both', expand=True, padx=10, pady=(4, 10))

    tv_scroll_y = ttk.Scrollbar(tree_frame, orient='vertical')
    tv_scroll_x = ttk.Scrollbar(tree_frame, orient='horizontal')
    tv_scroll_y.pack(side='right', fill='y')
    tv_scroll_x.pack(side='bottom', fill='x')

    _cols = ('#', 'Rule', 'TF', 'Dir', 'Months', 'WR%', 'Pips', 'Pass%', 'Variance', 'Score')
    tree = ttk.Treeview(tree_frame, columns=_cols, show='headings',
                        yscrollcommand=tv_scroll_y.set,
                        xscrollcommand=tv_scroll_x.set)
    tv_scroll_y.config(command=tree.yview)
    tv_scroll_x.config(command=tree.xview)
    tree.pack(fill='both', expand=True)

    # CHANGED: June 2026 — detail label: shows which months matched for the selected rule
    _months_detail = tk.Label(outer, text='Select a rule to see its matching months',
                              font=('Segoe UI', 9), fg='#555', anchor='w', justify='left',
                              wraplength=900, bg=BG)
    _months_detail.pack(fill='x', padx=10, pady=(4, 2))

    # CHANGED: June 2026 — sim detail box: eval + funded breakdown on rule click
    from tkinter.scrolledtext import ScrolledText as _ScrolledText
    _detail_box = _ScrolledText(
        outer, height=8, state='disabled',
        font=('Consolas', 8), wrap='none',
        relief='flat', borderwidth=1, bg='#fafafa',
    )
    _detail_box.pack(fill='x', padx=10, pady=(0, 8))

    # Column widths
    _col_w = {
        '#':        40,
        'Rule':    260,
        'TF':       50,
        'Dir':      50,
        'Months':   65,
        'WR%':      55,
        'Pips':     70,
        'Pass%':    55,
        'Variance': 70,
        'Score':    60,
    }
    for col in _cols:
        tree.heading(col, text=col)
        tree.column(col, width=_col_w.get(col, 80), anchor='center', stretch=(col == 'Rule'))

    tree.column('Rule', anchor='w')

    # Tag for greyed-out (below min months) rows
    tree.tag_configure('thin',   foreground=GREY_ROW)
    tree.tag_configure('normal', foreground='#1a252f')
    tree.tag_configure('warn',   foreground=AMBER)

    # ─────────────────────────────────────────────────────────────────────────
    # Computation
    # ─────────────────────────────────────────────────────────────────────────

    _state = {
        'month_labels':  {},
        'curr_regime':   {},
        'rows':          [],    # computed rows for current settings
        'rendered_rows': [],    # CHANGED: June 2026 — aligned with tree insert order for selection
    }

    def _rank_key(row):
        rv = _rank_var.get()
        if rv == 'Win rate':   return row.get('wr', 0)
        if rv == 'Net pips':   return row.get('pips', 0)
        if rv == 'Pass rate':  return row.get('pass_pct', 0)
        return row.get('score', 0)

    def _matching_months(month_labels, curr, use_t, use_v, use_d):
        """Return set of month keys whose regime matches curr on the chosen dims."""
        if not curr:
            return set()
        matching = set()
        for m, reg in month_labels.items():
            ok = True
            if use_t and reg.get('trend') != curr.get('trend'):
                ok = False
            if use_v and reg.get('vol')   != curr.get('vol'):
                ok = False
            if use_d and reg.get('dir')   != curr.get('dir'):
                ok = False
            if ok:
                matching.add(m)
        return matching

    def _insert_row_into_tree(row, rank, tag):
        """Insert one row into the tree. Separated so _append_row can reuse it."""
        rule_lbl = row.get('label', '?')
        if len(rule_lbl) > 55:
            rule_lbl = rule_lbl[:52] + '…'
        nm = row.get('n_match', 0)
        tree.insert('', 'end', values=(
            rank,
            rule_lbl,
            row.get('entry_tf', ''),
            row.get('direction', ''),
            nm,
            f"{row.get('wr', 0):.1f}",
            f"{row.get('pips', 0):.0f}",
            f"{row.get('pass_pct', 0):.0f}",
            f"{row.get('variance', 0):.1f}",
            f"{row.get('score', 0):.1f}",
        ), tags=(tag,))

    def _append_row(row, min_months, hide_thin):
        """Stream one scored row into the tree during the compute run.

        Appended at the tail (unsorted); _render_rows at the end re-sorts.
        Called via window.after(0, ...) from the background thread.
        """
        nm = row.get('n_match', 0)
        is_thin = nm < min_months
        if hide_thin and is_thin:
            return
        rank = len(_state['rendered_rows']) + 1
        tag = 'thin' if is_thin else 'normal'
        _insert_row_into_tree(row, rank, tag)
        _state['rendered_rows'].append(row)

    def _render_rows(rows, min_months, hide_thin):
        tree.delete(*tree.get_children())
        sorted_rows = sorted(rows, key=_rank_key, reverse=True)
        _state['rendered_rows'] = []  # CHANGED: June 2026 — reset aligned list
        rank = 0
        for row in sorted_rows:
            nm = row.get('n_match', 0)
            is_thin = nm < min_months
            if hide_thin and is_thin:
                continue
            rank += 1
            tag = 'thin' if is_thin else 'normal'
            _insert_row_into_tree(row, rank, tag)
            _state['rendered_rows'].append(row)  # CHANGED: June 2026 — aligned with insert

    def _set_detail_text(txt, fg='#1a252f'):
        try:
            _detail_box.configure(state='normal')
            _detail_box.delete('1.0', 'end')
            _detail_box.insert('1.0', txt)
            _detail_box.configure(state='disabled', fg=fg)
        except Exception:
            pass

    def _on_select_rule(_e=None):
        # CHANGED: June 2026 — show matching months + run eval/funded sim for selected rule
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        rendered = _state.get('rendered_rows', [])
        if 0 <= idx < len(rendered):
            r = rendered[idx]
            months = r.get('match_months', [])
            if months:
                _months_detail.config(
                    text=(f"{r.get('label', '?')} traded in {len(months)} matching "
                          f"months: " + ", ".join(months)))
            else:
                _months_detail.config(
                    text=f"{r.get('label', '?')} — no matching months traded.")

            # Kick off sim detail in background thread
            _strat = r.get('_strat')
            _trades = r.get('_trades')
            if _strat is None or not _trades:
                _set_detail_text('⚠ No trade data available for this rule.', fg='#c0392b')
                return

            _set_detail_text('⏳ Computing eval & funded simulation…', fg='#888888')

            def _run_sim(strat=_strat, trades=_trades, row=r):
                try:
                    from shared.sim_detail import resolve_firm_challenge, build_sim_detail_text
                    from project2_backtesting.panels.strategy_refiner_panel import (
                        _resolve_firm_challenge as _rfcp)
                except ImportError:
                    try:
                        from shared.sim_detail import resolve_firm_challenge, build_sim_detail_text
                        _rfcp = None
                    except Exception as _ie:
                        import state as _sm
                        _sm.window.after(0, lambda e=str(_ie): _set_detail_text(
                            f'⚠ Import failed: {e}', fg='#c0392b'))
                        return

                rule0 = ((strat.get('rules') or [{}])[0]
                         if strat.get('rules')
                         else (strat.get('saved_rule') or {}))
                _rs0 = strat.get('run_settings') or {}
                try:
                    acct = float(rule0.get('account_size')
                                 or _rs0.get('starting_capital')
                                 or strat.get('account_size')
                                 or 10000)
                    risk = float(rule0.get('risk_pct')
                                 or _rs0.get('risk_pct')
                                 or strat.get('risk_pct')
                                 or 1.0)
                    sl   = float((rule0.get('exit_params') or {}).get('sl_pips', 150) or 150)
                    pipv = float(rule0.get('pip_value_per_lot')
                                 or _rs0.get('pip_value_per_lot') or 1.0)
                except Exception:
                    acct, risk, sl, pipv = 10000.0, 1.0, 150.0, 1.0

                _top_firm    = (strat.get('prop_firm_name')
                                or strat.get('firm_name')
                                or _rs0.get('prop_firm_name')
                                or _rs0.get('firm_name'))
                _top_firm_id = (strat.get('firm_id') or _rs0.get('firm_id'))

                firm_id, ch_id = resolve_firm_challenge(
                    rule0, int(acct),
                    fallback_firm_name=_top_firm,
                    fallback_firm_id=_top_firm_id)

                txt, colour = build_sim_detail_text(
                    strategy_dict=strat, trades=trades,
                    firm_id=firm_id, challenge_id=ch_id,
                    account_size=int(acct), risk_per_trade_pct=risk,
                    default_sl_pips=sl, pip_value_per_lot=pipv,
                    symbol='XAUUSD',
                    firm_label=(_top_firm or rule0.get('prop_firm_name') or '?'),
                )
                fg = '#c0392b' if colour == 'err' else '#1a252f'
                import state as _sm
                _sm.window.after(0, lambda t=txt, f=fg: _set_detail_text(t, fg=f))

            threading.Thread(target=_run_sim, daemon=True).start()

    tree.bind('<<TreeviewSelect>>', _on_select_rule)

    def _apply_and_render():
        """Re-render tree from cached rows using current UI settings."""
        month_labels = _state['month_labels']
        curr         = _state['curr_regime']
        use_t = _use_trend.get()
        use_v = _use_vol.get()
        use_d = _use_dir.get()
        matching = _matching_months(month_labels, curr, use_t, use_v, use_d)

        # Exclude the current/latest month — it's incomplete
        last_month = sorted(month_labels.keys())[-1] if month_labels else ''
        past_matching = {m for m in matching if m < last_month}

        # CHANGED: June 2026 — show which months, not only the count
        if past_matching:
            _match_lbl.config(
                text=f'Matching past months: {len(past_matching)} — '
                     + ', '.join(sorted(past_matching)))
        else:
            _match_lbl.config(text='Matching past months: 0')

        if len(past_matching) < 6:
            _warn_lbl.config(text=f'Low confidence — only {len(past_matching)} matching months')
        else:
            _warn_lbl.config(text='')

        try:
            min_m = int(_min_months_var.get())
        except ValueError:
            min_m = 4

        # Re-score rows against current matching set
        reranked = []
        for row in _state['rows']:
            s = _score_rule(row.get('_monthly_rows', []),
                            past_matching,
                            _rank_var.get())
            if s is None:
                s = {'wr': 0, 'pips': 0, 'pass_pct': 0, 'variance': 0, 'score': 0, 'n_match': 0}
            reranked.append({**row, **s})

        _render_rows(reranked, min_m, _hide_thin.get())

    def _do_compute():
        """Background thread: load candles → label months → score rules."""
        global _worker_running
        _worker_running = True

        import time as _time
        _t0 = _time.monotonic()

        # Reset diagnostics for this run
        _DIAG_LINES.clear()
        _diag('Refresh started')

        try:
            import state as _state_mod

            # Clear tree and cache for this run
            _state_mod.window.after(0, lambda: tree.delete(*tree.get_children()))
            _state_mod.window.after(0, lambda: _state.update({'rendered_rows': []}))
            _state_mod.window.after(0, lambda: _status_lbl.config(
                text='Loading candles…', fg='#555'))

            from project2_backtesting.strategy_refiner import (
                load_strategy_list, load_trades_from_matrix, compute_monthly_pnl,
                clear_trades_cache, get_trades_cache_stats)
            clear_trades_cache()

            adx_thr = _adx_threshold()

            # Try H4 first, then H1
            df = None
            used_tf = 'H4'
            for tf_try in ('H4', 'H1'):
                df = _load_candles(tf_try)
                if df is not None and len(df) >= 250:
                    used_tf = tf_try
                    break

            if df is None or len(df) < 250:
                # CHANGED: June 2026 — show the SPECIFIC failure reason (LFS stub,
                #          bad path, malformed file) instead of generic message
                _reason = (_LAST_CANDLE_ERROR
                           or 'no H4/H1 candle file found — check P2 config data source')
                _msg = f'Cannot load candles — {_reason}'
                _diag(f'CANDLE LOAD FAILED: {_reason}')
                _state_mod.window.after(0, lambda m=_msg: _status_lbl.config(
                    text=m, fg=RED))
                return

            _src_note = f', {_CANDLE_SOURCE_LABEL}' if _CANDLE_SOURCE_LABEL else ''
            _diag(f'Candles loaded: {used_tf}, {len(df):,} rows{_src_note}')
            # CHANGED: June 2026 — name the source when auto-discovered
            _state_mod.window.after(0, lambda _n=_src_note: _status_lbl.config(
                text=f'Computing regimes from {used_tf} candles ({len(df):,} rows{_n})…',
                fg='#555'))

            month_labels = label_months(df, adx_threshold=adx_thr)
            if not month_labels:
                _diag('REGIME FAILED: not enough data')
                _state_mod.window.after(0, lambda: _status_lbl.config(
                    text='Not enough data to compute monthly regimes.', fg=RED))
                return

            curr = _current_regime(month_labels)
            _state['month_labels'] = month_labels
            _state['curr_regime']  = curr
            _diag(f'Regime: {curr}  |  total months: {len(month_labels)}')

            # Update regime banner
            def _update_banner(c=curr, tf=used_tf):
                if c:
                    txt = (f'Current regime ({tf}): '
                           f"Trend={c.get('trend','?')}  "
                           f"Vol={c.get('vol','?')}  "
                           f"Dir={c.get('dir','?')}")
                else:
                    txt = 'Current regime: unknown'
                _regime_lbl.config(text=txt)
            _state_mod.window.after(0, _update_banner)

            # ── Score rules ──────────────────────────────────────────────────
            _state_mod.window.after(0, lambda: _status_lbl.config(
                text='Scoring rules…', fg='#555'))

            strategies = load_strategy_list()
            if not strategies:
                _diag('No strategies found in backtest_matrix.json')
                _state_mod.window.after(0, lambda: _status_lbl.config(
                    text='No backtested strategies found.', fg=AMBER))
                return

            _diag(f'Strategies loaded: {len(strategies)}')

            rows = []
            _skipped_no_trades = 0
            _skipped_no_match  = 0
            use_t = _use_trend.get()
            use_v = _use_vol.get()
            use_d = _use_dir.get()

            # Matching months for initial render (excludes the current/latest month)
            last_month = sorted(month_labels.keys())[-1]
            matching = {m for m in _matching_months(month_labels, curr, use_t, use_v, use_d)
                        if m < last_month}
            _diag(f'Matching months ({len(matching)}): ' + ', '.join(sorted(matching)))

            try:
                min_m = int(_min_months_var.get())
            except ValueError:
                min_m = 4
            _hide = _hide_thin.get()

            total = len(strategies)
            for idx, strat in enumerate(strategies):
                if not _worker_running:
                    _diag('Scoring aborted by user')
                    break
                if idx % 20 == 0:
                    pct = idx * 100 // total
                    _state_mod.window.after(0, lambda p=pct: _status_lbl.config(
                        text=f'Scoring rules… {p}%', fg='#555'))

                sidx = strat.get('index')
                etf  = strat.get('entry_tf', '')
                trades = None
                try:
                    trades = load_trades_from_matrix(sidx, entry_tf=etf)
                except Exception as _te:
                    _diag(f'  SKIP idx={sidx} ({etf}): trade load error — {_te}')

                if not trades:
                    _skipped_no_trades += 1
                    continue

                monthly_rows = compute_monthly_pnl(trades)
                scored = _score_rule(monthly_rows, matching, _rank_var.get())
                if scored is None:
                    _skipped_no_match += 1
                    continue

                row = {
                    'label':         strat.get('label', '?'),
                    'entry_tf':      etf,
                    'direction':     strat.get('direction', ''),
                    '_monthly_rows': monthly_rows,
                    '_strat':        strat,
                    '_trades':       trades,
                    **scored,
                }
                rows.append(row)
                # Stream the row into the tree immediately (unsorted, re-sorted at end)
                _state_mod.window.after(0, lambda r=row, m=min_m, h=_hide: _append_row(r, m, h))

            _state['rows'] = rows

            h, ms = get_trades_cache_stats()
            _elapsed = _time.monotonic() - _t0
            _diag(
                f'Scoring done: {len(rows)} scored, '
                f'{_skipped_no_trades} skipped (no trades), '
                f'{_skipped_no_match} skipped (no matching months) | '
                f'cache hits={h} misses={ms} | '
                f'elapsed={_elapsed:.1f}s'
            )

            def _finish():
                try:
                    min_m2 = int(_min_months_var.get())
                except ValueError:
                    min_m2 = 4
                _apply_and_render()
                _status_lbl.config(
                    text=f'Done — {len(rows)} rules scored against {len(matching)} matching months.',
                    fg=GREEN)

            _state_mod.window.after(0, _finish)

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            _diag(f'ERROR: {exc}')
            try:
                import state as _sm
                _sm.window.after(0, lambda e=str(exc): _status_lbl.config(
                    text=f'Error: {e}', fg=RED))
            except Exception:
                pass
            print(f'[this_month] error: {tb}')
        finally:
            _worker_running = False
            try:
                import state as _sm
                _sm.window.after(0, lambda: _refresh_btn.config(
                    text='↻ Refresh', state='normal'))
            except Exception:
                pass

    def _start_refresh():
        global _worker_running
        if _worker_running:
            return
        _refresh_btn.config(text='⏹ Running…', state='disabled')
        t = threading.Thread(target=_do_compute, daemon=True)
        t.start()

    _refresh_btn.config(command=_start_refresh)

    # Re-render immediately when UI controls change (no re-compute needed)
    for var in (_use_trend, _use_vol, _use_dir, _rank_var, _hide_thin, _min_months_var):
        var.trace_add('write', lambda *_: _apply_and_render())

    return outer


def refresh():
    pass

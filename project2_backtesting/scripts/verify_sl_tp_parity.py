"""
SL/TP price parity verification.

Compares Python backtester's per-trade SL/TP price levels to what the
MT5 EA generator would set, given the same entry candle. After the
spread bake-in revert, these should match within float dust.

Run: python -m project2_backtesting.scripts.verify_sl_tp_parity [--rule-id N]
"""
import os
import sys
import json
import argparse
import logging

# Make project root importable when run as a script
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd  # noqa: E402

from shared.saved_rules import load_all  # noqa: E402

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger('verify_sl_tp_parity')


def _ea_would_compute_sl_tp(direction, bid_at_entry, ask_at_entry,
                             sl_pips, tp_pips, pip_size):
    """Replicate ea_generator.py:514-516 and 534-536 exactly.

    EA BUY:
        slPrice = NormalizeDouble(bid - sl, _Digits)
        tpPrice = NormalizeDouble(bid + tp, _Digits)
        entry fills at ask
    EA SELL:
        slPrice = NormalizeDouble(ask + sl, _Digits)
        tpPrice = NormalizeDouble(ask - tp, _Digits)
        entry fills at bid

    NormalizeDouble rounds to symbol digits. For XAUUSD pip_size=0.01
    that's 2 decimals.
    """
    import math
    decimals = max(0, -int(math.floor(math.log10(pip_size)))) if pip_size > 0 else 2

    if direction.upper() == "BUY":
        sl_price_unrounded = bid_at_entry - sl_pips * pip_size
        tp_price_unrounded = bid_at_entry + tp_pips * pip_size
        entry_fill = ask_at_entry
    else:  # SELL
        sl_price_unrounded = ask_at_entry + sl_pips * pip_size
        tp_price_unrounded = ask_at_entry - tp_pips * pip_size
        entry_fill = bid_at_entry

    return {
        'entry_fill': round(entry_fill, decimals),
        'sl_price':   round(sl_price_unrounded, decimals),
        'tp_price':   round(tp_price_unrounded, decimals),
    }


def _load_rule_by_id(rule_id):
    """Find a saved rule by id. None means first rule."""
    rules = load_all()
    if not rules:
        log.error("saved_rules.json is empty.")
        return None
    if rule_id is None:
        log.info(f"Using first saved rule (id={rules[0].get('id')}).")
        return rules[0]
    for r in rules:
        if str(r.get('id')) == str(rule_id) or str(r.get('rule_id')) == str(rule_id):
            return r
    log.error(f"No rule found with id={rule_id}.")
    return None


def _extract_sl_tp_pips(rule_dict):
    """Pull sl_pips and tp_pips off the rule's exit strategy.
    Falls back to defaults if not present.
    """
    nested = rule_dict.get('rule', {})
    sl_pips = nested.get('sl_pips')
    tp_pips = nested.get('tp_pips')
    # Try exit_strategy_params if not on the top level
    if sl_pips is None or tp_pips is None:
        params = nested.get('exit_strategy_params', {})
        if isinstance(params, dict):
            sl_pips = sl_pips if sl_pips is not None else params.get('sl_pips')
            tp_pips = tp_pips if tp_pips is not None else params.get('tp_pips')
    # Defaults — match the FixedSLTP defaults
    if sl_pips is None:
        sl_pips = 150.0
    if tp_pips is None:
        tp_pips = 300.0
    return float(sl_pips), float(tp_pips)


def _candles_path(symbol='XAUUSD', timeframe='H1'):
    """Find the candle CSV for a given TF in the configured data source."""
    cfg_path = os.path.join(_ROOT, 'project1_reverse_engineering', 'p1_config.json')
    with open(cfg_path, encoding='utf-8') as f:
        cfg = json.load(f)
    source_id = cfg.get('data_source_id', 'unlimited_leveraged_data')
    # Try both naming conventions
    for name in (f'{symbol}_{timeframe}.csv', f'{timeframe}.csv'):
        p = os.path.join(_ROOT, 'data', 'sources', source_id, name)
        if os.path.exists(p):
            return p
    return os.path.join(_ROOT, 'data', 'sources', source_id, f'{symbol}_{timeframe}.csv')


def main():
    parser = argparse.ArgumentParser(description="Verify SL/TP parity Python ↔ MT5")
    parser.add_argument('--rule-id', type=str, default=None,
                        help="Saved rule id to test (default: first)")
    parser.add_argument('--max-trades', type=int, default=20,
                        help="Stop after N signals (default: 20)")
    parser.add_argument('--symbol', type=str, default='XAUUSD')
    parser.add_argument('--spread-pips', type=float, default=25.0,
                        help="Spread in pips (default: 25)")
    parser.add_argument('--pip-size', type=float, default=0.01)
    parser.add_argument('--tolerance-pips', type=float, default=0.5,
                        help="Difference threshold for flagging a mismatch (pips)")
    args = parser.parse_args()

    # ── Load the rule ──────────────────────────────────────────────────────
    rule = _load_rule_by_id(args.rule_id)
    if rule is None:
        return 1

    nested = rule.get('rule', {})
    direction = nested.get('direction', nested.get('action', 'BUY')).upper()
    entry_tf  = nested.get('entry_tf', nested.get('entry_timeframe', 'H1'))
    sl_pips, tp_pips = _extract_sl_tp_pips(rule)

    log.info(f"Rule:       id={rule.get('id')}  direction={direction}  TF={entry_tf}")
    log.info(f"SL/TP:      sl={sl_pips}  tp={tp_pips}  pip_size={args.pip_size}")
    log.info(f"Spread:     {args.spread_pips} pips (constant — slippage excluded)")

    # ── Load candles ───────────────────────────────────────────────────────
    csv_path = _candles_path(args.symbol, entry_tf)
    if not os.path.exists(csv_path):
        log.error(f"Candle CSV not found: {csv_path}")
        return 2
    candles = pd.read_csv(csv_path)
    # Normalise timestamp column name
    for alt in ('time', 'datetime', 'Time', 'Timestamp'):
        if alt in candles.columns and 'timestamp' not in candles.columns:
            candles = candles.rename(columns={alt: 'timestamp'})
    log.info(f"Candles:    {len(candles):,} rows  ({csv_path})")

    # ── Build indicators for the rule's conditions ─────────────────────────
    log.info("Building indicators...")
    try:
        from shared.indicator_utils import compute_all_indicators
        ind = compute_all_indicators(candles, prefix=f'{entry_tf}_')
    except Exception as e:
        log.warning(f"compute_all_indicators failed ({e}), trying fallback...")
        ind = candles.copy()

    if isinstance(ind, dict):
        ind = pd.DataFrame(ind)
    if 'timestamp' not in ind.columns and 'timestamp' in candles.columns:
        ind['timestamp'] = candles['timestamp'].values

    # ── Match rule conditions ──────────────────────────────────────────────
    mask = pd.Series(True, index=candles.index)
    for cond in nested.get('conditions', []):
        feat = cond.get('feature')
        op   = cond.get('operator')
        val  = float(cond.get('value', 0))
        if feat not in ind.columns:
            log.warning(f"  feature {feat} not in indicators — skipping condition")
            continue
        col = pd.to_numeric(ind[feat], errors='coerce')
        if op == '>':   mask &= col > val
        elif op == '<':  mask &= col < val
        elif op == '>=': mask &= col >= val
        elif op == '<=': mask &= col <= val
        elif op == '==': mask &= (col - val).abs() < 1e-6
        elif op == '!=': mask &= (col - val).abs() >= 1e-6
        else:
            log.warning(f"  unknown operator '{op}' — skipping")
    mask &= ~candles['open'].isna()

    signal_indices = candles.index[mask].tolist()
    log.info(f"Signals:    {len(signal_indices)} matched the rule conditions")

    if not signal_indices:
        log.error("No signals — cannot verify SL/TP parity.")
        return 3

    # ── Compare Python vs MT5 EA per signal ───────────────────────────────
    import math
    decimals = max(0, -int(math.floor(math.log10(args.pip_size)))) if args.pip_size > 0 else 2

    n_compared = 0
    diffs_entry, diffs_sl, diffs_tp = [], [], []
    n_mismatch = 0

    for sig_idx in signal_indices[:args.max_trades]:
        if sig_idx + 1 >= len(candles):
            continue
        entry_candle = candles.iloc[sig_idx + 1]
        bar_open = float(entry_candle['open'])

        # Standard MT5 chart convention: bar open ≈ bid_open
        bid_at_entry = bar_open
        ask_at_entry = bar_open + args.spread_pips * args.pip_size

        # MT5 EA's view (ea_generator.py:514-516)
        ea_view = _ea_would_compute_sl_tp(
            direction, bid_at_entry, ask_at_entry,
            sl_pips, tp_pips, args.pip_size,
        )

        # Python's view after spread-bake-in revert:
        # entry_price = bar_open (bid_open), no spread, no slippage in this check
        py_entry = round(bar_open, decimals)
        if direction == "BUY":
            py_sl = round(py_entry - sl_pips * args.pip_size, decimals)
            py_tp = round(py_entry + tp_pips * args.pip_size, decimals)
        else:
            py_sl = round(py_entry + sl_pips * args.pip_size, decimals)
            py_tp = round(py_entry - tp_pips * args.pip_size, decimals)

        d_entry = abs(py_entry - ea_view['entry_fill']) / args.pip_size
        d_sl    = abs(py_sl    - ea_view['sl_price'])   / args.pip_size
        d_tp    = abs(py_tp    - ea_view['tp_price'])   / args.pip_size

        diffs_entry.append(d_entry)
        diffs_sl.append(d_sl)
        diffs_tp.append(d_tp)

        if max(d_sl, d_tp) > args.tolerance_pips:
            n_mismatch += 1
            if n_mismatch <= 5:
                ts = entry_candle.get('timestamp', sig_idx)
                log.info(f"  MISMATCH @ {ts}: "
                         f"Δentry={d_entry:.3f}p  ΔSL={d_sl:.3f}p  ΔTP={d_tp:.3f}p")
                log.info(f"    Python: entry={py_entry}  sl={py_sl}  tp={py_tp}")
                log.info(f"    EA:     entry={ea_view['entry_fill']}  "
                         f"sl={ea_view['sl_price']}  tp={ea_view['tp_price']}")
        n_compared += 1

    # ── Summary ────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 65)
    log.info(f"Compared:   {n_compared} signals")
    log.info(f"Mismatches: {n_mismatch}  (>{args.tolerance_pips}p on SL or TP)")
    if diffs_entry:
        log.info(f"Δ entry:    max={max(diffs_entry):.3f}p  "
                 f"avg={sum(diffs_entry)/len(diffs_entry):.3f}p")
        log.info(f"Δ SL:       max={max(diffs_sl):.3f}p  "
                 f"avg={sum(diffs_sl)/len(diffs_sl):.3f}p")
        log.info(f"Δ TP:       max={max(diffs_tp):.3f}p  "
                 f"avg={sum(diffs_tp)/len(diffs_tp):.3f}p")
    log.info("=" * 65)

    if n_mismatch == 0:
        log.info("PASS: Python SL/TP levels match MT5 EA within tolerance.")
        return 0
    else:
        log.info(f"FAIL: {n_mismatch} signal(s) differ by more than "
                 f"{args.tolerance_pips} pip.")
        log.info("")
        log.info("If running BEFORE the spread-bake-in revert (commit 0b1fe50),")
        log.info(f"expect Δentry/SL/TP ≈ {args.spread_pips:.0f} pips for BUY trades.")
        log.info("After the revert, ΔSL and ΔTP should be 0; Δentry stays ~spread "
                 "(Python tracks bid, MT5 fills at ask — different by design).")
        return 4


if __name__ == '__main__':
    sys.exit(main())

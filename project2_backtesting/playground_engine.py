"""
Playground Engine — instant backtest for interactive strategy building.
Takes a set of conditions and pre-loaded indicator data, returns trades in <2 seconds.
"""

import numpy as np


def quick_backtest(indicators_df, candles_df, conditions, direction="BUY",
                   sl_pips=150, tp_pips=300, pip_size=0.01, spread_pips=2.5,
                   max_hold_candles=50, max_trades=500):
    """
    Run a fast backtest with the given conditions.

    Args:
        indicators_df: DataFrame with all indicator columns (pre-loaded)
        candles_df: DataFrame with timestamp, open, high, low, close
        conditions: list of dicts: [{"feature": "H1_rsi_14", "operator": ">", "value": 60}, ...]
        direction: "BUY" or "SELL"
        sl_pips, tp_pips: stop loss and take profit in pips
        pip_size: pip size (0.01 for gold)
        spread_pips: spread cost in pips
        max_hold_candles: max candles before forced exit
        max_trades: limit trades for speed

    Returns dict:
        trades: list of trade dicts
        total_trades, win_rate, net_pips, profit_factor, max_drawdown_pips,
        avg_pips, best_trade, worst_trade, avg_hold_candles
    """
    if not conditions:
        return _empty_result()

    n = len(indicators_df)

    # Build signal mask
    mask = np.ones(n, dtype=bool)
    for cond in conditions:
        feat = cond['feature']
        if feat not in indicators_df.columns:
            return _empty_result(missing_feature=feat)

        col = indicators_df[feat].values
        val = float(cond['value'])
        op = cond['operator']

        if op == '>':    mask &= col > val
        elif op == '>=': mask &= col >= val
        elif op == '<':  mask &= col < val
        elif op == '<=': mask &= col <= val
        else:            mask &= col > val

    signal_indices = np.where(mask)[0]

    if len(signal_indices) == 0:
        return _empty_result()

    # Simulate trades
    opens = candles_df['open'].values.astype(float)
    highs = candles_df['high'].values.astype(float)
    lows = candles_df['low'].values.astype(float)
    closes = candles_df['close'].values.astype(float)
    timestamps = candles_df['timestamp'].values

    trades = []
    last_exit_idx = -1

    for sig_idx in signal_indices:
        if sig_idx <= last_exit_idx:
            continue  # still in a trade
        if sig_idx + 1 >= n:
            continue
        if len(trades) >= max_trades:
            break

        # Entry at next candle open
        entry_idx = sig_idx + 1
        entry_price = opens[entry_idx]

        if direction == "BUY":
            entry_price += spread_pips * pip_size
            sl_price = entry_price - sl_pips * pip_size
            tp_price = entry_price + tp_pips * pip_size
        else:
            entry_price -= spread_pips * pip_size
            sl_price = entry_price + sl_pips * pip_size
            tp_price = entry_price - tp_pips * pip_size

        # Look forward for exit
        exit_price = None
        exit_reason = "MAX_HOLD"
        hold = 0

        # WHY: Old code started the exit scan at entry_idx + 1, skipping
        #      the entry candle. Entry happens at opens[entry_idx] at the
        #      OPEN of that candle, so its intrabar high/low (after the
        #      open) must be checked against SL/TP. A same-bar SL hit was
        #      silently missed and the trade continued trading from a
        #      position that should have closed. Same bug as run_backtest
        #      non-vectorized path (fixed in Phase 28 Fix 2). Start at
        #      entry_idx so the entry candle is scanned — hold=0 on that
        #      first iteration.
        # CHANGED: April 2026 — Phase 32 Fix 1 — include entry candle
        #          (audit Part C HIGH #98)
        for j in range(entry_idx, min(entry_idx + max_hold_candles + 1, n)):
            hold = j - entry_idx

            # WHY: Detect same-bar SL+TP ambiguity. Label the exit as
            #      SL_AMBIGUOUS so downstream stats can distinguish clean
            #      SL hits from ambiguous same-bar resolutions. Resolution
            #      stays conservative (always SL on tie), matching Fix 1
            #      in exit_strategies._resolve_sl_tp_priority.
            # CHANGED: April 2026 — ambiguity labeling (audit MED)
            if direction == "BUY":
                sl_hit = lows[j]  <= sl_price
                tp_hit = highs[j] >= tp_price
                if sl_hit and tp_hit:
                    exit_price = min(opens[j], sl_price)
                    exit_reason = "SL_AMBIGUOUS"
                    break
                if sl_hit:
                    exit_price = min(opens[j], sl_price)  # gap fill
                    exit_reason = "SL"
                    break
                if tp_hit:
                    exit_price = max(opens[j], tp_price)
                    exit_reason = "TP"
                    break
            else:
                sl_hit = highs[j] >= sl_price
                tp_hit = lows[j]  <= tp_price
                if sl_hit and tp_hit:
                    exit_price = max(opens[j], sl_price)
                    exit_reason = "SL_AMBIGUOUS"
                    break
                if sl_hit:
                    exit_price = max(opens[j], sl_price)
                    exit_reason = "SL"
                    break
                if tp_hit:
                    exit_price = min(opens[j], tp_price)
                    exit_reason = "TP"
                    break

        if exit_price is None:
            exit_idx = min(entry_idx + max_hold_candles, n - 1)
            exit_price = closes[exit_idx]
            hold = exit_idx - entry_idx
        else:
            exit_idx = entry_idx + hold

        last_exit_idx = exit_idx

        # WHY: Entry spread was applied at entry_price above, but exit
        #      spread was missing. Real round-trip = entry spread + exit
        #      spread. Subtract one more spread_pips to match real broker
        #      cost. Same fix as Phase 12 candle_labeler round-trip.
        # CHANGED: April 2026 — round-trip spread (audit HIGH — Family #4)
        if direction == "BUY":
            pnl_pips = (exit_price - entry_price) / pip_size - spread_pips
        else:
            pnl_pips = (entry_price - exit_price) / pip_size - spread_pips

        trades.append({
            'entry_time': str(timestamps[entry_idx]),
            'exit_time': str(timestamps[exit_idx]),
            'direction': direction,
            'entry_price': round(entry_price, 2),
            'exit_price': round(exit_price, 2),
            'pnl_pips': round(pnl_pips, 1),
            'hold_candles': hold,
            'exit_reason': exit_reason,
        })

    if not trades:
        return _empty_result()

    # Compute stats
    pnls = [t['pnl_pips'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # Drawdown
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    drawdown = peak - cumulative
    max_dd = float(drawdown.max()) if len(drawdown) > 0 else 0

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0

    return {
        'trades': trades,
        'total_trades': len(trades),
        'win_rate': len(wins) / len(trades) if trades else 0,
        'net_pips': round(sum(pnls), 1),
        'avg_pips': round(sum(pnls) / len(pnls), 1),
        'profit_factor': round(gross_profit / max(gross_loss, 0.01), 2),
        'max_drawdown_pips': round(max_dd, 1),
        'best_trade': round(max(pnls), 1) if pnls else 0,
        'worst_trade': round(min(pnls), 1) if pnls else 0,
        'avg_hold': round(sum(t['hold_candles'] for t in trades) / len(trades), 1),
        'total_wins': len(wins),
        'total_losses': len(losses),
    }


def simulate_prop_firm(trades, account_size=100000, risk_pct=1.0,
                        # WHY: Old default was 10.0 (forex major). XAUUSD
                        #      correct value is 1.0. Change default to
                        #      match the project's primary instrument.
                        #      Forex callers must explicitly override.
                        # CHANGED: April 2026 — XAUUSD-correct default (audit MED — Family #1)
                        daily_dd_pct=5.0, total_dd_pct=10.0, pip_value=1.0,
                        # WHY (Phase 32 Fix 2): Old code hardcoded sl_pips=150
                        #      inside the function for lot-size calc. Users
                        #      running the playground with SL=50 got dollar
                        #      PnL computed from SL=150 — numbers were
                        #      completely divorced from the real trades.
                        #      Accept sl_pips as a parameter; callers pass
                        #      the same sl_pips they used in quick_backtest.
                        # CHANGED: April 2026 — Phase 32 Fix 2 — sl_pips
                        #          parameter (audit Part C HIGH #96)
                        sl_pips=150.0):
    """
    Quick prop firm simulation on the trade list.
    Returns pass/fail + stats.
    """
    if not trades:
        return {'passed': False, 'reason': 'NO_TRADES', 'worst_daily_dd': 0, 'worst_total_dd': 0}

    balance = account_size
    high_water = account_size
    worst_daily = 0
    worst_total = 0
    daily_pnl = 0
    prev_day = None
    # WHY (Phase 32 Fix 3): Track today's starting equity as a separate
    #      reference for daily DD. Old code used account_size (the
    #      account's open-date starting balance), so a $5k daily loss
    #      on a grown $120k account was treated as 5%/100k = 5% even
    #      though the firm rule measures 5k/120k = 4.17% against today's
    #      opening equity. Reset day_start_balance on each day change.
    # CHANGED: April 2026 — Phase 32 Fix 3 — daily DD from day_start_balance
    #          (audit Part C HIGH #97)
    day_start_balance = account_size
    blown = False
    blow_reason = None

    for t in trades:
        # Rough daily reset
        trade_day = t.get('entry_time', '')[:10]
        if trade_day != prev_day:
            daily_pnl = 0
            day_start_balance = balance   # capture start of this trading day
            prev_day = trade_day

        # Calculate $ PnL based on risk
        # WHY: Old code re-assigned sl_pips = 150 here, clobbering the
        #      parameter. Use the parameter directly.
        # CHANGED: April 2026 — Phase 32 Fix 2b — use sl_pips parameter
        risk_dollars = balance * (risk_pct / 100)
        _sl_for_sizing = float(sl_pips) if sl_pips and sl_pips > 0 else 150.0
        lots = risk_dollars / (_sl_for_sizing * pip_value) if _sl_for_sizing * pip_value > 0 else 0.01
        trade_pnl = t['pnl_pips'] * pip_value * lots

        balance += trade_pnl
        daily_pnl += trade_pnl
        high_water = max(high_water, balance)

        # WHY: Daily DD now measured against today's starting equity
        #      (day_start_balance), matching most challenge firms.
        #      Total DD continues to measure against account-open
        #      starting balance (account_size), which is the
        #      fixed-dollar rule the audit flagged — leaving the
        #      total DD denominator unchanged because that IS what
        #      most firms use for the total rule. Only the daily
        #      reference was wrong.
        # CHANGED: April 2026 — Phase 32 Fix 3 — day_start_balance denom
        _day_denom = day_start_balance if day_start_balance > 0 else account_size
        cur_daily_dd = abs(min(0, daily_pnl)) / _day_denom * 100
        cur_total_dd = (high_water - balance) / account_size * 100

        worst_daily = max(worst_daily, cur_daily_dd)
        worst_total = max(worst_total, cur_total_dd)

        if cur_daily_dd >= daily_dd_pct:
            blown = True
            blow_reason = f"Daily DD {cur_daily_dd:.1f}% >= {daily_dd_pct}%"
            break
        if cur_total_dd >= total_dd_pct:
            blown = True
            blow_reason = f"Total DD {cur_total_dd:.1f}% >= {total_dd_pct}%"
            break

    final_profit = balance - account_size
    final_pct = (final_profit / account_size) * 100

    return {
        'passed': not blown,
        'reason': blow_reason or 'SURVIVED',
        'worst_daily_dd': round(worst_daily, 2),
        'worst_total_dd': round(worst_total, 2),
        'final_balance': round(balance, 2),
        'final_profit': round(final_profit, 2),
        'final_pct': round(final_pct, 2),
    }


def _empty_result(missing_feature=None):
    r = {
        'trades': [], 'total_trades': 0, 'win_rate': 0, 'net_pips': 0,
        'avg_pips': 0, 'profit_factor': 0, 'max_drawdown_pips': 0,
        'best_trade': 0, 'worst_trade': 0, 'avg_hold': 0,
        'total_wins': 0, 'total_losses': 0,
    }
    if missing_feature:
        r['error'] = f"Feature '{missing_feature}' not found in indicators"
    return r

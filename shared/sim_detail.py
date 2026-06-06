"""
sim_detail.py — shared eval+funded simulation formatter.

Called by both strategy_refiner_panel (click on a grid row) and
this_month (click on a scored rule).  Keeps all sim logic in one
place so both panels show identical output.
"""

import os
import sys

_HERE        = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)


def _ensure_project_root():
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)


def resolve_firm_challenge(rule_dict, account_size,
                           fallback_firm_name=None,
                           fallback_firm_id=None):
    """Find (firm_id, challenge_id) for a rule dict.

    Mirrors strategy_refiner_panel._resolve_firm_challenge but uses
    _PROJECT_ROOT instead of panel-local project_root so it works from
    any import context.

    Returns ('', '') when no match is found.
    """
    _ensure_project_root()
    firm_name = (rule_dict.get('prop_firm_name')
                 or (rule_dict.get('discovery_settings') or {}).get('prop_firm_name')
                 or fallback_firm_name
                 or '')
    firm_id_hint = (rule_dict.get('firm_id')
                    or (rule_dict.get('discovery_settings') or {}).get('firm_id')
                    or fallback_firm_id
                    or '')

    if not firm_name and not firm_id_hint:
        try:
            import importlib.util as _ilu
            _p1_path = os.path.join(_PROJECT_ROOT,
                'project1_reverse_engineering', 'config_loader.py')
            if os.path.exists(_p1_path):
                _spec = _ilu.spec_from_file_location('_sdc_cl', _p1_path)
                _mod  = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                _cfg  = _mod.load()
                firm_name    = _cfg.get('prop_firm_name', '') or ''
                firm_id_hint = (_cfg.get('prop_firm_id', '')
                                or _cfg.get('firm_id', '') or '')
        except Exception:
            pass
        if not firm_name and not firm_id_hint:
            return ('', '')

    try:
        import json as _j, glob as _g
        matched = None
        if firm_name:
            for fp in _g.glob(os.path.join(_PROJECT_ROOT, 'prop_firms', '*.json')):
                with open(fp, encoding='utf-8') as _f:
                    fd = _j.load(_f)
                if fd.get('firm_name') == firm_name:
                    matched = fd
                    break
        if matched is None and firm_id_hint:
            for fp in _g.glob(os.path.join(_PROJECT_ROOT, 'prop_firms', '*.json')):
                with open(fp, encoding='utf-8') as _f:
                    fd = _j.load(_f)
                if fd.get('firm_id') == firm_id_hint:
                    matched = fd
                    break
        if matched is None:
            return ('', '')
        firm_id = matched.get('firm_id', '')
        best = (matched.get('challenges') or [{}])[0]
        for ch in matched.get('challenges', []):
            if int(account_size) in (ch.get('account_sizes') or []):
                best = ch
                break
        return (firm_id, best.get('challenge_id', ''))
    except Exception:
        pass
    return ('', '')


def build_sim_detail_text(strategy_dict, trades,
                          firm_id=None, challenge_id=None,
                          account_size=10000, risk_per_trade_pct=1.0,
                          default_sl_pips=150.0, pip_value_per_lot=1.0,
                          symbol='XAUUSD', firm_label=None):
    """Run eval+funded simulation and return formatted multi-line text.

    Args:
        strategy_dict   : rule dict (for header line, money calc)
        trades          : list of trade dicts (already loaded by caller)
        firm_id         : resolved firm id slug
        challenge_id    : resolved challenge id
        account_size    : int account size in $
        risk_per_trade_pct : float (e.g. 1.0 = 1%)
        default_sl_pips : SL in pips for lot-size calculation
        pip_value_per_lot : pip value per 0.01 lot
        symbol          : instrument (informational)
        firm_label      : display string for firm name in header

    Returns:
        (str, str) — (formatted text, colour hint 'ok'|'warn'|'err')
    """
    _ensure_project_root()
    if not trades:
        return ("⚠ No trade data for this rule — re-run the backtest.", "err")

    try:
        from project2_backtesting.strategy_validator import _trades_to_df
        from shared.prop_firm_simulator import simulate_challenge
    except Exception as ie:
        return (f"⚠ Simulator import failed: {ie}", "err")

    acct = int(account_size)
    risk = float(risk_per_trade_pct)
    sl   = float(default_sl_pips)
    pipv = float(pip_value_per_lot)

    if not firm_id or not challenge_id:
        return (
            f"⚠ Cannot resolve firm/challenge "
            f"(firm_id={firm_id!r}, challenge_id={challenge_id!r}, acct=${acct:,}).",
            "err"
        )

    try:
        df = _trades_to_df(trades, risk, sl, pipv, acct)
    except Exception as e:
        return (f"⚠ Trade conversion failed: {e}", "err")

    sim_e = sim_f = None
    try:
        sim_e = simulate_challenge(
            trades_df=df, firm_id=firm_id, challenge_id=challenge_id,
            account_size=acct, mode='sliding_window',
            simulate_funded=False, risk_per_trade_pct=risk,
            default_sl_pips=sl, pip_value_per_lot=pipv, symbol=symbol,
        )
        sim_f = simulate_challenge(
            trades_df=df, firm_id=firm_id, challenge_id=challenge_id,
            account_size=acct, mode='sliding_window',
            simulate_funded=True, risk_per_trade_pct=risk,
            default_sl_pips=sl, pip_value_per_lot=pipv, symbol=symbol,
        )
    except Exception as se:
        return (f"⚠ Simulator error: {se}", "err")

    # ── Build text ────────────────────────────────────────────────────────────
    lines = []
    _display_firm = (firm_label
                     or (strategy_dict.get('prop_firm_name') if strategy_dict else None)
                     or firm_id or '?')
    lines.append(
        f"Firm: {_display_firm}  |  Account: ${acct:,}  "
        f"|  Risk: {risk}%  |  Trades: {len(trades)}"
    )

    # Money summary
    try:
        _net_total = sum(float(_t.get('net_pips', 0) or 0) for _t in trades)
        _net_avg   = _net_total / len(trades) if trades else 0.0
        _rs = (strategy_dict.get('run_settings') or {}) if strategy_dict else {}
        _broker_step = 0.01
        _raw_lot = (acct * (risk / 100.0)) / (sl * pipv) if sl * pipv > 0 else 0
        lot = max(0.01, int(_raw_lot / _broker_step) * _broker_step)
        _n_dol = _net_total * pipv * lot
        _a_dol = _net_avg  * pipv * lot
        _n_pct = _n_dol / acct * 100.0
        _a_pct = _a_dol / acct * 100.0
        lines.append(
            f"   💵 Net: ${_n_dol:+,.0f} ({_n_pct:+.1f}%)  |  "
            f"Avg/trade: ${_a_dol:+,.2f} ({_a_pct:+.2f}%)"
        )
    except Exception:
        pass

    # EVAL block
    if sim_e is not None:
        cnt     = sim_e.eval_pass_count
        flc     = sim_e.eval_fail_count
        inc     = sim_e.eval_incomplete_count
        decided = cnt + flc
        starts  = decided + inc
        ep      = sim_e.eval_pass_rate * 100
        if inc > 0:
            lines.append(
                f"🎯 EVAL  {cnt} passed  |  {flc} failed  |  {inc} incomplete  "
                f"(of {starts} historical starts)   pass rate: {ep:.0f}%"
            )
        else:
            lines.append(
                f"🎯 EVAL  {cnt} passed  |  {flc} failed  "
                f"(of {starts} historical starts)   pass rate: {ep:.0f}%"
            )
        if cnt > 0:
            lines.append(
                f"   📅 Days to PASS:  avg {sim_e.eval_avg_days_to_pass:.0f}  |  "
                f"min {sim_e.eval_min_days_to_pass:.0f}  |  "
                f"max {sim_e.eval_max_days_to_pass:.0f}"
            )
        if flc > 0:
            lines.append(
                f"   📅 Days to FAIL:  avg {sim_e.eval_avg_days_to_fail:.0f}  |  "
                f"min {sim_e.eval_min_days_to_fail:.0f}  |  "
                f"max {sim_e.eval_max_days_to_fail:.0f}"
            )
        lines.append(f"   📊 Avg max DD per attempt: {sim_e.eval_avg_max_dd_pct:.1f}%")
        if sim_e.eval_fail_reasons:
            reasons = "  |  ".join(
                f"{k.replace('FAIL_','').replace('_',' ').title()}: {v}"
                for k, v in sorted(sim_e.eval_fail_reasons.items(),
                                   key=lambda x: -x[1])
            )
            lines.append(f"   💥 Fail reasons: {reasons}")
    else:
        lines.append("🎯 EVAL  simulator returned no data")

    # FUNDED block
    if sim_f is not None:
        if sim_f.funded_avg_survival_days is not None:
            lines.append(
                f"💰 FUNDED survival: "
                f"avg {sim_f.funded_avg_survival_days:.0f} days  |  "
                f"median {sim_f.funded_median_survival_days:.0f} days"
            )
        if sim_f.funded_avg_monthly_payout is not None:
            lines.append(
                f"   📈 Avg monthly payout: ${sim_f.funded_avg_monthly_payout:,.0f}  "
                f"|  total: ${(sim_f.funded_avg_total_payouts or 0):,.0f}"
            )
        if (sim_f.funded_survival_rate_3mo is not None
                or sim_f.funded_survival_rate_6mo is not None):
            s3 = (f"{sim_f.funded_survival_rate_3mo*100:.0f}%"
                  if sim_f.funded_survival_rate_3mo is not None else "—")
            s6 = (f"{sim_f.funded_survival_rate_6mo*100:.0f}%"
                  if sim_f.funded_survival_rate_6mo is not None else "—")
            lines.append(f"   📉 Survival rate: 3-month {s3}  |  6-month {s6}")
    else:
        lines.append("💰 FUNDED  simulator returned no data")

    # Per-window detail
    if sim_e is not None and sim_e.individual_results:
        _windows = sim_e.individual_results
        lines.append("")
        lines.append(f"📋 Per-window detail ({len(_windows)} windows):")
        for _w in _windows:
            _o = _w.eval_outcome or "?"
            _o_short = {
                'PASS':                  '✓ PASS',
                'FAIL_DD':               '✗ Total-DD breach',
                'FAIL_DAILY_DD':         '✗ Daily-DD breach',
                'FAIL_TIMEOUT':          '✗ Timeout',
                'FAIL_CONSISTENCY':      '✗ Consistency rule',
                'INSUFFICIENT_TRADES':   '… Incomplete (out of data)',
            }.get(_o, _o)
            _sd   = str(_w.start_date or '?')[:10]
            _days = _w.eval_days or 0
            _prof = _w.eval_profit_pct or 0.0
            _ddp  = _w.eval_max_dd_pct or 0.0
            if _o == 'INSUFFICIENT_TRADES':
                lines.append(
                    f"   {_sd} → {_o_short:<28}  "
                    f"{_days:>3}d  |  (window ran out of forward data)"
                )
            else:
                lines.append(
                    f"   {_sd} → {_o_short:<28}  "
                    f"{_days:>3}d  |  profit {_prof:+5.1f}%  |  totalDD {_ddp:4.1f}%"
                )

    colour = "ok"
    if sim_e is None or sim_e.eval_pass_count == 0:
        colour = "warn"

    return ("\n".join(lines), colour)

"""Resolve the broker IANA timezone for DST-correct hour normalization.

Priority: firm 'broker_timezone' -> default 'Europe/Athens' (EET/EEST).
Returns an IANA zone string. (We deliberately avoid fixed integer offsets
because the broker observes DST — a fixed offset silently corrupts ~5 months
of the year by 1 hour.)

WHY: Candle data lives in broker server local time (e.g. EET/EEST for Get
Leveraged), firm rule windows are labeled GMT, and the live EA reads
TimeGMT(). Discovery, the backtest, and the EA must agree on one clock.
The DST-aware IANA-zone localization is the only correct mechanism — see
shared/data_utils.py::convert_to_utc which documents the EET-vs-EEST bug.
"""
# CHANGED: June 2026 — IANA-zone broker tz resolver (DST-correct)


def resolve_broker_tz(firm_data=None, default='Europe/Athens'):
    """Return the IANA timezone string for the broker's server clock.

    Args:
        firm_data: parsed prop_firms/<firm>.json dict, or None.
        default: returned when the firm doesn't specify a zone. The EET/EEST
                 default matches the standard MT5 "EET server" calendar used
                 by most European brokers (Get Leveraged confirmed).
    """
    if firm_data and firm_data.get('broker_timezone'):
        return str(firm_data['broker_timezone'])
    return default

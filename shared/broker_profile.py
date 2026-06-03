"""
broker_profile.py — calibrate a prop firm + the backtester to a real broker.

Parses the JSON block emitted by broker_profile.mq5 and writes broker-specific
fields into:
  - prop_firms/<firm>.json  -> instrument_specs[SYMBOL], leverage,
                               no_trades_window_start/end (derived from the
                               broker's trade-session gap around midnight),
                               gmt_offset_hours (informational)
  - project2_backtesting/backtest_config.json -> pip_value_per_lot, pip_size,
                               typical_spread, contract_size

WHY this exists: the firm's published rules don't tell you what the broker will
actually FILL. The 00:00 "market closed" gap, real spread per session, pip value,
contract size, min-lot and GMT offset are all broker-specific and only knowable
by querying the terminal. This bakes those into the backtest + EA so a backtest
is "precise for this prop firm."
"""
# CHANGED: June 2026 — broker profile applier (refactored from CLI script)

import json
import os
import re
import glob

# WHY: shared/ is one level below repo root; climb up once.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROP_DIR = os.path.join(REPO, 'prop_firms')
BT_CFG = os.path.join(REPO, 'project2_backtesting', 'backtest_config.json')


def extract_json(blob: str) -> dict:
    """Reassemble the JSON emitted in ~200-char chunks between the markers.
    MT5 prefixes each log line with 'timestamp\\tsource\\t'; strip that, skip the
    [BUILD] marker, and concatenate the fragments."""
    # CHANGED: June 2026 — chunked Print reassembly (broker_profile.mq5 v2)
    lines = blob.splitlines()
    begin = end = None
    for i, ln in enumerate(lines):
        if 'BROKER_PROFILE_BEGIN' in ln and begin is None:
            begin = i
        elif 'BROKER_PROFILE_END' in ln and begin is not None:
            end = i
            break
    if begin is None or end is None or end <= begin:
        m = re.search(r'(\{[^{}]*"schema"\s*:\s*"broker_profile_v1".*?\})', blob, re.S)
        if not m:
            raise ValueError("Could not find a broker_profile JSON block "
                             "(BEGIN/END markers not found).")
        return json.loads(re.sub(r'\s+', '', m.group(1)))
    parts = []
    for ln in lines[begin + 1:end]:
        content = ln.split('\t')[-1] if '\t' in ln else ln
        content = content.strip()
        if not content or content.startswith('[BUILD]') or content.startswith('//'):
            continue
        parts.append(content)
    raw = ''.join(parts)
    s, e = raw.find('{'), raw.rfind('}')
    if s == -1 or e == -1:
        raise ValueError("Markers found but no JSON braces inside.")
    return json.loads(raw[s:e + 1])


def find_firm_file(firm_name: str) -> str:
    """Locate the prop-firm JSON file by name or id."""
    for fp in sorted(glob.glob(os.path.join(PROP_DIR, '*.json'))):
        try:
            with open(fp, encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        if firm_name.lower() in (str(d.get('firm_name', '')).lower(),
                                 str(d.get('firm_id', '')).lower()):
            return fp
    available = [os.path.basename(f) for f in glob.glob(os.path.join(PROP_DIR, '*.json'))]
    raise ValueError(f"No prop-firm JSON found for '{firm_name}'. Available: {available}")


def derive_no_trades_window(trade_sessions: dict):
    """From the broker's per-day TRADE sessions (minutes from midnight), find the
    daily closed gap that covers/touches midnight and express it as
    [start_hour, end_hour) in SERVER time.

    Returns (start_hour, end_hour) or (None, None) if the market trades through
    midnight every day (no gap to model).
    """
    # Look at a representative weekday (Wednesday=3) to avoid weekend edges.
    sessions = None
    for probe_day in ('3', '2', '4', '1'):
        secs = trade_sessions.get(probe_day)
        if secs:
            sessions = secs
            break

    if not sessions:
        return (None, None)

    # sessions = [[from_min,to_min],...] sorted by from. The gap that touches
    # midnight is: from the last session's `to` (previous day) to the first
    # session's `from` (this day).
    sessions = sorted(sessions, key=lambda s: s[0])
    first_from_min = sessions[0][0]
    last_to_min = sessions[-1][1]

    # Closed window touching midnight (server time):
    #   [last_to (prev day), 24:00) U [00:00, first_from)
    start_h_srv = last_to_min // 60
    end_h_srv = (first_from_min + 59) // 60  # round up so a 00:05 open still blocks hour 0

    # If the day is essentially 24h (first_from==0 and last_to>=1440) -> no gap
    if first_from_min == 0 and last_to_min >= 1439:
        return (None, None)

    return (start_h_srv, end_h_srv)


def to_gmt_hour(h, gmt_offset_hours):
    """Convert a server-time hour to GMT."""
    if h is None:
        return None
    return int((h - round(gmt_offset_hours)) % 24)


def apply_profile(blob: str, firm_name: str, dry_run: bool = False) -> dict:
    """Parse pasted broker_profile output and write firm + backtest config.
    Returns a summary dict for the UI. Raises on parse/lookup failure."""
    prof = extract_json(blob)
    sym = prof['symbol']
    gmt_off = float(prof.get('gmt_offset_hours', 0.0))

    firm_fp = find_firm_file(firm_name)
    with open(firm_fp, encoding='utf-8') as f:
        firm = json.load(f)

    specs = firm.setdefault('instrument_specs', {}).setdefault(sym, {})
    specs['pip_value_per_lot'] = round(prof['pip_value_per_lot'], 4)
    specs['pip_size']      = prof['pip_size']
    specs['contract_size'] = prof['contract_size']
    specs['min_lot']       = prof['min_lot']
    specs['lot_step']      = prof['lot_step']
    specs['swap_long_pips_per_night']  = prof.get('swap_long_pips_per_night', 0.0)
    specs['swap_short_pips_per_night'] = prof.get('swap_short_pips_per_night', 0.0)
    specs['swap_rollover3days_weekday'] = prof.get('swap_rollover3days_weekday')
    specs['stops_level_pips']  = prof.get('stops_level_pips', 0.0)
    specs['freeze_level_pips'] = prof.get('freeze_level_pips', 0.0)

    smed = prof.get('spread_median_pips', {})
    overall = smed.get('overall') or prof.get('spread_snapshot_pips')
    specs['typical_spread'] = int(round(overall)) if overall else specs.get('typical_spread', 25)
    if any(prof.get('spread_samples', {}).values()):
        specs['spread_session_multipliers'] = prof.get('spread_session_multipliers')
    specs['spread_source'] = (f"broker_profile.mq5 {prof.get('broker_company', '?')}/"
                              f"{prof.get('broker_server', '?')} sampled "
                              f"{prof.get('spread_sampled_seconds', 0)}s")
    specs.setdefault('max_spread_pips_filter', int(round((overall or 25) * 1.6)))

    firm['leverage'] = prof.get('account_leverage', firm.get('leverage'))
    firm['gmt_offset_hours_observed'] = gmt_off
    firm['broker_company_observed']   = prof.get('broker_company')

    sh_srv, eh_srv = derive_no_trades_window(prof.get('trade_sessions_minutes', {}))
    sh_gmt = to_gmt_hour(sh_srv, gmt_off)
    eh_gmt = to_gmt_hour(eh_srv, gmt_off)
    if sh_gmt is not None and eh_gmt is not None:
        firm['no_trades_window_start_hour_gmt'] = sh_gmt
        firm['no_trades_window_end_hour_gmt']   = eh_gmt
        firm['no_trades_window_source'] = (
            f"Auto-derived from broker session gap (server {sh_srv}:00-{eh_srv}:00, "
            f"GMT {gmt_off:+.1f}h).")

    # backtest_config.json
    bt = {}
    if os.path.exists(BT_CFG):
        try:
            with open(BT_CFG, encoding='utf-8') as f:
                bt = json.load(f)
        except Exception:
            bt = {}
    bt_changes = {
        'pip_value_per_lot': round(prof['pip_value_per_lot'], 4),
        'pip_size':          prof['pip_size'],
        'typical_spread':    specs['typical_spread'],
        'contract_size':     prof['contract_size'],
        'swap_long_pips_per_night':  prof.get('swap_long_pips_per_night', 0.0),
        'swap_short_pips_per_night': prof.get('swap_short_pips_per_night', 0.0),
    }

    summary = {
        'symbol': sym, 'broker': prof.get('broker_company'),
        'gmt_offset': gmt_off, 'firm_file': firm_fp,
        'no_trades_window_gmt': [sh_gmt, eh_gmt],
        'pip_value_per_lot': specs['pip_value_per_lot'],
        'typical_spread': specs['typical_spread'],
        'leverage': firm['leverage'], 'bt_changes': bt_changes,
    }

    if not dry_run:
        with open(firm_fp, 'w', encoding='utf-8') as f:
            json.dump(firm, f, indent=2)
        bt.update(bt_changes)
        os.makedirs(os.path.dirname(BT_CFG), exist_ok=True)
        with open(BT_CFG, 'w', encoding='utf-8') as f:
            json.dump(bt, f, indent=2)

    return summary

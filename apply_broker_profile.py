#!/usr/bin/env python3
"""
apply_broker_profile.py — calibrate a prop firm + the backtester to a real broker.

Takes the JSON block emitted by broker_profile.mq5 (paste it via stdin or a file)
plus a prop-firm name, and writes the broker-specific fields into:
  - prop_firms/<firm>.json  -> instrument_specs[SYMBOL], leverage,
                               no_trades_window_start/end (derived from the
                               broker's trade-session gap around midnight),
                               gmt_offset_hours (informational)
  - project2_backtesting/backtest_config.json -> pip_value_per_lot, pip_size,
                               typical_spread, contract_size (for the selected symbol)

WHY this exists: the firm's published rules don't tell you what the broker will
actually FILL. The 00:00 "market closed" gap, real spread per session, pip value,
contract size, min-lot and GMT offset are all broker-specific and only knowable
by querying the terminal. This bakes those into the backtest + EA so a backtest
is "precise for this prop firm."

USAGE:
  python apply_broker_profile.py --firm "Get Leveraged" --paste            # paste, then Ctrl-D
  python apply_broker_profile.py --firm "Get Leveraged" --file profile.txt
  python apply_broker_profile.py --firm "Get Leveraged" --file profile.txt --dry-run
"""
import argparse, json, os, re, sys, glob

REPO = os.path.dirname(os.path.abspath(__file__))
PROP_DIR = os.path.join(REPO, 'prop_firms')
BT_CFG = os.path.join(REPO, 'project2_backtesting', 'backtest_config.json')


def extract_json(blob: str) -> dict:
    """Pull the JSON emitted between the BEGIN/END markers.

    broker_profile.mq5 prints the JSON in ~200-char CHUNKS (MT5 Print truncates a
    single line at ~512 chars), and MT5 prefixes every log line with a timestamp +
    source tag. So we: take all lines between the markers, strip each line's MT5
    prefix (everything up to and including the last tab), then concatenate the
    fragments into one JSON string.
    """
    lines = blob.splitlines()
    begin = end = None
    for i, ln in enumerate(lines):
        if 'BROKER_PROFILE_BEGIN' in ln and begin is None:
            begin = i
        elif 'BROKER_PROFILE_END' in ln and begin is not None:
            end = i
            break
    if begin is None or end is None or end <= begin:
        # fallback: maybe a single un-chunked object is present
        m = re.search(r'(\{[^{}]*"schema"\s*:\s*"broker_profile_v1".*?\})', blob, re.S)
        if not m:
            raise SystemExit("Could not find a broker_profile JSON block "
                             "(BEGIN/END markers not found).")
        frag = m.group(1)
        return json.loads(re.sub(r'\s+', '', frag))

    parts = []
    for ln in lines[begin + 1:end]:
        # strip MT5 log prefix: "...timestamp...\tsource (SYM,TF)\t<content>"
        content = ln.split('\t')[-1] if '\t' in ln else ln
        content = content.strip()
        if content:
            parts.append(content)
    raw = ''.join(parts)
    # guard: drop anything before the first { and after the last }
    s, e = raw.find('{'), raw.rfind('}')
    if s == -1 or e == -1:
        raise SystemExit("Broker profile markers found but no JSON braces inside.")
    raw = raw[s:e + 1]
    return json.loads(raw)


def find_firm_file(firm_name: str) -> str:
    for fp in sorted(glob.glob(os.path.join(PROP_DIR, '*.json'))):
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        if firm_name.lower() in (str(d.get('firm_name', '')).lower(),
                                 str(d.get('firm_id', '')).lower()):
            return fp
    raise SystemExit(f"No prop-firm JSON found for '{firm_name}'. "
                     f"Available: {[os.path.basename(f) for f in glob.glob(os.path.join(PROP_DIR,'*.json'))]}")


def derive_no_trades_window(trade_sessions: dict):
    """From the broker's per-day TRADE sessions (minutes from midnight), find the
    daily closed gap that covers/touches midnight and express it as
    [start_hour, end_hour) GMT for the EA/backtester no-trades window.

    Returns (start_hour, end_hour) or (None, None) if the market trades through
    midnight every day (no gap to model).

    NOTE: sessions are in SERVER time. If the broker's server != GMT, the caller
    should also have gmt_offset_hours; we convert to GMT here so the window
    matches the GMT-based gating used by Python and the EA.
    """
    # Look at a representative weekday (Wednesday=3) to avoid weekend edges.
    for probe_day in ('3', '2', '4', '1'):
        secs = trade_sessions.get(probe_day)
        if secs:
            sessions = secs
            break
    else:
        return (None, None)

    if not sessions:
        return (None, None)

    # sessions = [[from_min,to_min],...] sorted by from. The gap that touches
    # midnight is: from the last session's `to` (previous day) to the first
    # session's `from` (this day). If first 'from' > 0, the market is CLOSED
    # from midnight (00:00) until that 'from' — that's the start-of-day gap.
    sessions = sorted(sessions, key=lambda s: s[0])
    first_from_min = sessions[0][0]
    last_to_min = sessions[-1][1]

    # Closed window touching midnight (server time):
    #   [last_to (prev day), 24:00) ∪ [00:00, first_from)
    # Express as wrapping window start=last_to_hour, end=first_from_hour.
    start_h_srv = last_to_min // 60
    end_h_srv = (first_from_min + 59) // 60  # round up so a 00:05 open still blocks hour 0

    # If the day is essentially 24h (first_from==0 and last_to>=1440) → no gap
    if first_from_min == 0 and last_to_min >= 1439:
        return (None, None)

    return (start_h_srv, end_h_srv)


def to_gmt_hour(h, gmt_offset_hours):
    if h is None:
        return None
    return int((h - round(gmt_offset_hours)) % 24)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--firm', required=True, help="Prop firm name or id (e.g. 'Get Leveraged')")
    ap.add_argument('--file', help="File containing the pasted Experts-tab output")
    ap.add_argument('--paste', action='store_true', help="Read pasted text from stdin")
    ap.add_argument('--dry-run', action='store_true', help="Print what would change, don't write")
    args = ap.parse_args()

    if args.file:
        blob = open(args.file, encoding='utf-8', errors='ignore').read()
    elif args.paste:
        print("Paste the Experts-tab output, then press Ctrl-D (Ctrl-Z Enter on Windows):",
              file=sys.stderr)
        blob = sys.stdin.read()
    else:
        raise SystemExit("Provide --file PATH or --paste")

    prof = extract_json(blob)
    sym = prof['symbol']
    gmt_off = float(prof.get('gmt_offset_hours', 0.0))

    # ── prop firm JSON ───────────────────────────────────────────────
    firm_fp = find_firm_file(args.firm)
    firm = json.load(open(firm_fp))

    specs = firm.setdefault('instrument_specs', {}).setdefault(sym, {})
    specs['pip_value_per_lot'] = round(prof['pip_value_per_lot'], 4)
    specs['pip_size'] = prof['pip_size']
    specs['contract_size'] = prof['contract_size']
    specs['min_lot'] = prof['min_lot']
    specs['lot_step'] = prof['lot_step']
    # overnight swap (pips/night per lot) — used by backtest swap model
    specs['swap_long_pips_per_night'] = prof.get('swap_long_pips_per_night', 0.0)
    specs['swap_short_pips_per_night'] = prof.get('swap_short_pips_per_night', 0.0)
    specs['swap_rollover3days_weekday'] = prof.get('swap_rollover3days_weekday')
    # execution constraints (min SL/TP distance the broker enforces)
    specs['stops_level_pips'] = prof.get('stops_level_pips', 0.0)
    specs['freeze_level_pips'] = prof.get('freeze_level_pips', 0.0)
    # spread: prefer sampled medians if any session was sampled
    smed = prof.get('spread_median_pips', {})
    overall = smed.get('overall') or prof.get('spread_snapshot_pips')
    specs['typical_spread'] = int(round(overall)) if overall else specs.get('typical_spread', 25)
    if any(prof.get('spread_samples', {}).values()):
        specs['spread_session_multipliers'] = prof.get('spread_session_multipliers')
    specs['spread_source'] = (f"broker_profile.mq5 from {prof.get('broker_company','?')} / "
                              f"{prof.get('broker_server','?')}; sampled "
                              f"{prof.get('spread_sampled_seconds',0)}s")
    # max spread filter: snapshot + margin, only set if not already calibrated
    if 'max_spread_pips_filter' not in specs:
        specs['max_spread_pips_filter'] = int(round((overall or 25) * 1.6))

    firm['leverage'] = prof.get('account_leverage', firm.get('leverage'))
    firm['gmt_offset_hours_observed'] = gmt_off
    firm['broker_company_observed'] = prof.get('broker_company')

    # no-trades window from the broker session gap (converted server→GMT)
    sh_srv, eh_srv = derive_no_trades_window(prof.get('trade_sessions_minutes', {}))
    sh_gmt = to_gmt_hour(sh_srv, gmt_off)
    eh_gmt = to_gmt_hour(eh_srv, gmt_off)
    if sh_gmt is not None and eh_gmt is not None:
        firm['no_trades_window_start_hour_gmt'] = sh_gmt
        firm['no_trades_window_end_hour_gmt'] = eh_gmt
        firm['no_trades_window_source'] = (
            f"Derived from broker trade-session gap (server {sh_srv}:00-{eh_srv}:00, "
            f"GMT offset {gmt_off:+.1f}h). Blocks entries the broker would reject "
            f"as 'market closed'.")
    else:
        firm['no_trades_window_source'] = (
            "Broker trades through midnight — no closed-gap window detected.")

    # ── backtest_config.json ─────────────────────────────────────────
    bt = {}
    if os.path.exists(BT_CFG):
        try:
            bt = json.load(open(BT_CFG))
        except Exception:
            bt = {}
    bt_changes = {
        'pip_value_per_lot': round(prof['pip_value_per_lot'], 4),
        'pip_size': prof['pip_size'],
        'typical_spread': specs['typical_spread'],
        'contract_size': prof['contract_size'],
        'swap_long_pips_per_night': prof.get('swap_long_pips_per_night', 0.0),
        'swap_short_pips_per_night': prof.get('swap_short_pips_per_night', 0.0),
    }

    # ── report ───────────────────────────────────────────────────────
    print("\n=== BROKER PROFILE PARSED ===")
    print(f"  symbol            : {sym}")
    print(f"  broker            : {prof.get('broker_company')} / {prof.get('broker_server')}")
    print(f"  gmt_offset_hours  : {gmt_off:+.2f}")
    print(f"  pip_value_per_lot : {prof['pip_value_per_lot']}")
    print(f"  pip_size          : {prof['pip_size']}")
    print(f"  contract_size     : {prof['contract_size']}")
    print(f"  min_lot/step      : {prof['min_lot']} / {prof['lot_step']}")
    print(f"  leverage          : 1:{prof.get('account_leverage')}")
    print(f"  spread (overall)  : {overall} pips  (samples L/NY/A = "
          f"{prof.get('spread_samples')})")
    print(f"  no_trades_window  : server [{sh_srv},{eh_srv}) -> GMT [{sh_gmt},{eh_gmt})")
    print(f"\n  -> firm file      : {firm_fp}")
    print(f"  -> backtest config: {BT_CFG}  {bt_changes}")

    if args.dry_run:
        print("\n[DRY RUN] No files written.")
        return

    json.dump(firm, open(firm_fp, 'w'), indent=2)
    bt.update(bt_changes)
    os.makedirs(os.path.dirname(BT_CFG), exist_ok=True)
    json.dump(bt, open(BT_CFG, 'w'), indent=2)
    print("\nWritten. Re-run the backtest and regenerate the EA for this firm —")
    print("both are now calibrated to this broker.")


if __name__ == '__main__':
    main()

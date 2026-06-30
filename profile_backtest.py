"""
profile_backtest.py — find the REAL hotspots in a backtest run (any timeframe).

Run locally from the repo root:

    python profile_backtest.py --tf M5 --rules 5
    python profile_backtest.py --tf H4 --rules 10

Wraps ONE entry-timeframe run of run_comparison_matrix() in cProfile and prints the
top time-consuming functions by SELF time and CUMULATIVE time. That is the ground-truth
ranking of what to optimize next to make the full 5-TF sweep faster — without guessing.

Two perf fixes are already applied (searchsorted M1 slice + numpy M1 exit loop). Whatever
this profiler shows on top is the next target. It does NOT change results — only measures.
"""
import argparse, cProfile, pstats, io, os, sys, time
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))


def _resolve_data_dir():
    try:
        from shared.data_sources import resolve_data_dir
        return resolve_data_dir()
    except Exception:
        base = os.path.join("data", "sources")
        subs = [os.path.join(base, d) for d in os.listdir(base)
                if os.path.isdir(os.path.join(base, d))]
        return max(subs, key=os.path.getmtime)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf",    default="M5",  help="entry timeframe: M5 M15 H1 H4 D1")
    ap.add_argument("--rules", type=int, default=5,  help="how many rule indices to profile")
    ap.add_argument("--top",   type=int, default=30, help="lines in each ranked table")
    ap.add_argument("--out",   default="profile_backtest.prof")
    ap.add_argument("--m1",    action="store_true",  help="enable exit_intrabar_m1 (tests M1 loop)")
    args = ap.parse_args()

    from project2_backtesting.panels.configuration import load_config
    cfg = load_config()

    data_dir = _resolve_data_dir()
    symbol   = cfg.get("symbol", "XAUUSD") or "XAUUSD"
    candles_path = os.path.join(data_dir, f"{symbol}_M5.csv")
    if not os.path.exists(candles_path):
        candles_path = os.path.join(data_dir, f"{symbol.lower()}_M5.csv")

    print(f"[PROFILE] data_dir:  {data_dir}")
    print(f"[PROFILE] candles:   {candles_path}")
    print(f"[PROFILE] tf={args.tf}  rules={args.rules}  m1={args.m1}")
    print()

    rule_indices = list(range(args.rules))

    spread     = float(cfg.get("spread",           "25") or "25")
    commission = float(cfg.get("commission",        "4.0") or "4.0")
    capital    = float(cfg.get("starting_capital",  "100000") or "100000")
    risk_pct   = float(cfg.get("risk_pct",          "1.0") or "1.0")
    start_date = cfg.get("backtest_start") or "2022-01-01"
    end_date   = cfg.get("backtest_end") or None

    from project2_backtesting import strategy_backtester as sb

    def _call():
        return sb.run_comparison_matrix(
            candles_path,
            timeframe=args.tf,
            rule_indices=rule_indices,
            spread_pips=spread,
            commission_pips=commission,
            pip_size=0.01,
            account_size=capital,
            risk_per_trade_pct=risk_pct,
            start_date=start_date,
            end_date=end_date,
            exit_intrabar_m1=args.m1,
        )

    pr = cProfile.Profile()
    t0 = time.time()
    pr.enable()
    try:
        _call()
    finally:
        pr.disable()
    elapsed = time.time() - t0
    print(f"\n[PROFILE] run took {elapsed:.1f}s")

    pr.dump_stats(args.out)

    for sort_key, title in (("tottime", "SELF TIME (tottime)"),
                            ("cumulative", "CUMULATIVE TIME")):
        s = io.StringIO()
        pstats.Stats(pr, stream=s).sort_stats(sort_key).print_stats(args.top)
        print(f"\n{'='*16} TOP BY {title} {'='*16}")
        print(s.getvalue())

    print(f"[PROFILE] raw saved to {args.out}")
    print(f"[PROFILE] to explore: python -m pstats {args.out}")


if __name__ == "__main__":
    main()

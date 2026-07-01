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
        from shared.data_sources import get_active_source_dir
        return get_active_source_dir()
    except Exception:
        base = os.path.join("data", "sources")
        subs = [os.path.join(base, d) for d in os.listdir(base)
                if os.path.isdir(os.path.join(base, d))]
        return max(subs, key=os.path.getmtime)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="M5")
    ap.add_argument("--rules", type=int, default=5, help="how many rule indices to test")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", default="profile_backtest.prof")
    args = ap.parse_args()

    from project2_backtesting import strategy_backtester as sb
    try:
        from project2_backtesting.panels.configuration import load as load_cfg
        cfg = load_cfg()
    except Exception:
        cfg = {}

    data_dir = _resolve_data_dir()
    candles_path = os.path.join(data_dir, "xauusd_M5.csv")
    if not os.path.exists(candles_path):
        candles_path = os.path.join(data_dir, "XAUUSD_M5.csv")
    print(f"[PROFILE] candles: {candles_path}")
    print(f"[PROFILE] tf={args.tf}  rules={args.rules}")

    # Profile a small but representative slice: first N rule indices, all exits (default).
    rule_indices = list(range(args.rules))

    def _call():
        return sb.run_comparison_matrix(
            candles_path,
            timeframe=args.tf,
            rule_indices=rule_indices,
            spread_pips=float(cfg.get("spread", 37.0) or 37.0),
            commission_pips=float(cfg.get("commission", 4.0) or 4.0),
            pip_size=0.01,
            account_size=float(cfg.get("starting_capital", 10000) or 10000),
            risk_per_trade_pct=float(cfg.get("risk_pct", 1.0) or 1.0),
            start_date=cfg.get("backtest_start") or "2026-01-01",
            end_date=cfg.get("backtest_end") or None,
        )

    pr = cProfile.Profile()
    t0 = time.time()
    pr.enable()
    try:
        _call()
    finally:
        pr.disable()
    print(f"[PROFILE] run took {time.time()-t0:.1f}s")

    pr.dump_stats(args.out)
    for sort_key, title in (("tottime", "SELF TIME (tottime)"),
                            ("cumulative", "CUMULATIVE TIME")):
        s = io.StringIO()
        pstats.Stats(pr, stream=s).sort_stats(sort_key).print_stats(args.top)
        print(f"\n================ TOP BY {title} ================")
        print(s.getvalue())
    print(f"[PROFILE] raw saved to {args.out} — inspect: python -m pstats {args.out}")

if __name__ == "__main__":
    main()

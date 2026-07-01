"""
sample_profile.py — find the hot function even when the backtest never finishes.

The built-in BT_PROFILE only writes its report when run_comparison_matrix RETURNS.
If the run hangs, you get nothing. This sampling profiler dumps the hottest stack
frames after a fixed time budget, so it captures the bottleneck on a hung run.

Usage (from repo root):
    python sample_profile.py --tf M5 --seconds 60

It runs ONE M5 comparison-matrix pass in a background thread, samples the main
thread's stack every 10ms for --seconds, then prints the functions where time is
actually spent (by sampled frame count). No code changes, measures the real run.
"""
import argparse, os, sys, threading, time, collections, traceback, signal
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
    ap.add_argument("--rules", type=int, default=5)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--interval", type=float, default=0.01)
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

    print(f"[SAMPLE] tf={args.tf} rules={args.rules} budget={args.seconds}s")
    print(f"[SAMPLE] candles={candles_path}")

    worker_exc = {}
    def _run():
        try:
            sb.run_comparison_matrix(
                candles_path, timeframe=args.tf,
                rule_indices=list(range(args.rules)),
                spread_pips=float(cfg.get("spread", 37.0) or 37.0),
                commission_pips=float(cfg.get("commission", 4.0) or 4.0),
                pip_size=0.01,
                account_size=float(cfg.get("starting_capital", 10000) or 10000),
                risk_per_trade_pct=float(cfg.get("risk_pct", 1.0) or 1.0),
                start_date=cfg.get("backtest_start") or "2026-01-01",
                end_date=cfg.get("backtest_end") or None,
            )
        except BaseException as e:
            worker_exc["e"] = e

    t = threading.Thread(target=_run, daemon=True)
    main_tid = t.ident  # set after start
    t.start()
    main_tid = t.ident

    # Sample the WORKER thread's stack.
    counts = collections.Counter()
    line_counts = collections.Counter()
    t0 = time.time()
    n = 0
    while time.time() - t0 < args.seconds and t.is_alive():
        frames = sys._current_frames()
        fr = frames.get(t.ident)
        if fr is not None:
            st = traceback.extract_stack(fr)
            if st:
                top = st[-1]
                counts[(os.path.basename(top.filename), top.name)] += 1
                line_counts[(os.path.basename(top.filename), top.lineno, top.name)] += 1
            # also credit the deepest strategy_backtester frame (where OUR code is)
            for f in reversed(st):
                if "strategy_backtester" in f.filename or "exit_strateg" in f.filename \
                   or "prop_firm" in f.filename or "indicator" in f.filename:
                    line_counts[(os.path.basename(f.filename), f.lineno, f.name)] += 1
                    break
        n += 1
        time.sleep(args.interval)

    print(f"\n[SAMPLE] took {n} samples over {time.time()-t0:.0f}s "
          f"(worker {'still running' if t.is_alive() else 'finished'})")
    if worker_exc:
        print(f"[SAMPLE] worker raised: {worker_exc['e']!r}")

    print("\n===== HOT FUNCTIONS (by sampled top-of-stack) =====")
    for (fn, name), c in counts.most_common(20):
        print(f"  {100*c/max(n,1):5.1f}%  {fn}:{name}")

    print("\n===== HOT LINES (deepest OUR-code frame) =====")
    for (fn, lineno, name), c in line_counts.most_common(25):
        print(f"  {100*c/max(n,1):5.1f}%  {fn}:{lineno}  {name}")

if __name__ == "__main__":
    main()

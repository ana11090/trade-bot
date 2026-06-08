"""
Alignment diagnostic — run on YOUR machine (real candles needed).

WHAT IT DOES
  For a sample of Gold Reaper trades, finds the candle whose time window contains
  the entry timestamp (under several candidate clocks), and reports whether the
  entry PRICE falls inside that candle's high-low range. This distinguishes:
    (a) sub-hour TIME skew     -> price lands in an ADJACENT candle, small minute gap
    (b) candle stamp convention -> open-stamped vs close-stamped bars (consistent shift)
    (c) PRICE-FEED mismatch    -> price not in ANY nearby candle (different data vendor)

USAGE (from the repo root, in a normal terminal):
    python diag_alignment.py

Edit TRADES_FILE / CANDLES_FILE below if your paths differ.
Outputs a table to the console AND writes diag_alignment_out.txt.
"""

import os, csv
import pandas as pd
from datetime import datetime, timedelta

# ---- EDIT THESE IF NEEDED -------------------------------------------------
TRADES_FILE  = r"data/sources/levereged_2026.05.03/Gold Reaper New V2 2.txt"
CANDLES_FILE = r"data/sources/levereged_2026.05.03/XAUUSD_H1.csv"
TF_MINUTES   = 60          # H1 candles
PIP_SIZE     = 0.01
TOL_PRICE    = 1.50        # 150 pips * 0.01 (same as alignment_tolerance)
N_SAMPLE     = 25          # how many trades to inspect
# Candidate clocks to test: hours to ADD to the trade time to reach candle (UTC) time
CANDIDATE_OFFSETS = [0, -1, -2, -3, +1, +2, +3]
# --------------------------------------------------------------------------


def load_trades(path):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        r = csv.reader(f, delimiter=";")
        next(r)  # header
        for row in r:
            if len(row) >= 5 and row[0].strip():
                try:
                    t = datetime.strptime(row[0].strip(), "%Y.%m.%d %H:%M:%S")
                    px = float(row[4])
                    rows.append((t, row[1].strip(), px))
                except Exception:
                    pass
    return rows


def load_candles(path):
    # try common layouts; candles need timestamp/open/high/low/close
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    # find a time column
    tcol = next((cols[c] for c in ("timestamp", "time", "date", "datetime") if c in cols), df.columns[0])
    df = df.rename(columns={tcol: "timestamp"})
    for want in ("open", "high", "low", "close"):
        if want not in df.columns:
            for c in df.columns:
                if c.lower() == want:
                    df = df.rename(columns={c: want})
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def candle_for(candles, t):
    """Return the candle whose [open_time, open_time+TF) window contains t,
    assuming candles are OPEN-stamped."""
    # candles stamped at bar open; bar covers [ts, ts+TF)
    win = pd.Timedelta(minutes=TF_MINUTES)
    lo = candles["timestamp"]
    mask = (lo <= t) & (t < lo + win)
    idx = candles.index[mask]
    if len(idx):
        return candles.loc[idx[0]]
    return None


def main():
    if not os.path.exists(CANDLES_FILE):
        print(f"!! candle file not found or is an LFS stub: {CANDLES_FILE}")
        print("   Run `git lfs pull` first so the real data is present.")
        return
    # detect LFS stub
    with open(CANDLES_FILE, "rb") as fh:
        head = fh.read(64)
    if head.startswith(b"version https://git-lfs"):
        print("!! candle file is a git-LFS POINTER, not real data. Run `git lfs pull`.")
        return

    trades = load_trades(TRADES_FILE)
    candles = load_candles(CANDLES_FILE)
    print(f"loaded {len(trades)} trades, {len(candles)} candles "
          f"({candles['timestamp'].min()} → {candles['timestamp'].max()})")

    out = []
    # 1) For each candidate offset, count how many of the sample land IN-range
    sample = trades[:N_SAMPLE]
    print("\n=== offset scan (price-in-range of the containing OPEN-stamped candle) ===")
    best = None
    for off in CANDIDATE_OFFSETS:
        hits = 0
        for (t, typ, px) in trades:        # full set for the count
            c = candle_for(candles, t + timedelta(hours=off))
            if c is not None and (c["low"] - TOL_PRICE) <= px <= (c["high"] + TOL_PRICE):
                hits += 1
        pct = 100.0 * hits / max(len(trades), 1)
        marker = ""
        if best is None or hits > best[1]:
            best = (off, hits); marker = "  <- best so far"
        print(f"  offset {off:+d}h : {hits}/{len(trades)} in-range ({pct:.1f}%){marker}")
    print(f"\nbest offset: {best[0]:+d}h ({100.0*best[1]/len(trades):.1f}%)")

    # 2) Detailed per-trade view at the best offset — show WHY misses miss
    off = best[0]
    print(f"\n=== per-trade detail at offset {off:+d}h (first {N_SAMPLE}) ===")
    hdr = f"{'entry_time':19} {'px':>9} {'candle_open':19} {'c_low':>9} {'c_high':>9} {'in?':>4} {'nearest_in_bar_gap':>18}"
    print(hdr); out.append(hdr)
    for (t, typ, px) in sample:
        tt = t + timedelta(hours=off)
        c = candle_for(candles, tt)
        if c is None:
            line = f"{str(t):19} {px:9.2f} {'<no candle>':19}"
            print(line); out.append(line); continue
        inb = (c["low"] - TOL_PRICE) <= px <= (c["high"] + TOL_PRICE)
        # if miss, find the nearest candle (in time) whose range DOES contain px
        gap = ""
        if not inb:
            cand = candles[(candles["low"] - TOL_PRICE <= px) & (px <= candles["high"] + TOL_PRICE)]
            if len(cand):
                deltas = (cand["timestamp"] - tt).abs()
                nearest = cand.loc[deltas.idxmin()]
                mins = (nearest["timestamp"] - c["timestamp"]).total_seconds() / 60.0
                gap = f"{mins:+.0f}min"
            else:
                gap = "price in NO bar"
        line = (f"{str(t):19} {px:9.2f} {str(c['timestamp']):19} "
                f"{c['low']:9.2f} {c['high']:9.2f} {('YES' if inb else 'no'):>4} {gap:>18}")
        print(line); out.append(line)

    # 3) interpretation hint
    print("\n=== how to read this ===")
    print(" - If most misses show a small +/-60min gap to the nearest in-range bar:")
    print("     => sub-hour / bar-stamp convention skew (fixable by shifting candle stamps).")
    print(" - If many misses say 'price in NO bar':")
    print("     => PRICE-FEED mismatch (candles are a different vendor than the broker fills).")
    print(" - If best offset is clearly one value with high %, that's your trade-file clock.")

    with open("diag_alignment_out.txt", "w") as fh:
        fh.write("\n".join(out))
    print("\nwrote diag_alignment_out.txt")


if __name__ == "__main__":
    main()

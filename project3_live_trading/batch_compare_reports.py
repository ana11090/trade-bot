"""
batch_compare_reports.py — read a folder of MT5 Strategy-Tester reports (.xlsx) and
line each one up against the matching Python backtest trades, producing one summary
table: per EA, MT5 entries vs Python entries, exact-bar matches, and one-bar shifts.

WHY: doing this by hand (open each report, count, eyeball against Python) doesn't
scale. This consumes the reports the batch runner produced and tells you which EAs
actually match Python.

Matching MT5 report -> Python trades:
  - MT5 report file name is the EA name (from the batch manifest's 'name').
  - Python trades for that rule come from a per-rule trades CSV/JSON you point at,
    keyed by the same name or rule_combo. Adjust _python_trades_for() to your export.

CHANGED: June 2026 — batch report comparison
Python 3.9+; needs openpyxl (already used elsewhere in the project).
"""

import os
import sys
import csv
import glob
import json
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


# CHANGED: June 2026 — pull Python trades from stored outputs/rules/*.json files
# WHY: trades already exist on disk from backtesting; no manual export needed.
#   Each rule_*_{combo}_{exit}_{hash}_{TF}.json has a trades list with full detail.
import re


def _py_rules_dir():
    """Return the project2_backtesting/outputs/rules directory."""
    return os.path.join(_ROOT, "project2_backtesting", "outputs", "rules")


def _norm(s):
    """Normalize string for matching: alphanumeric lowercase only."""
    return "".join(ch.lower() for ch in str(s) if ch.isalnum())


def _load_py_trades_for(combo, exit_name, entry_tf):
    """Find the stored rule JSON whose combo + exit + tf match.

    Returns: (trades_list, meta_dict) or (None, None)
    meta_dict includes: {"file": filename, "py_tf": stored_tf, "tf_match": bool}

    WHY: Must match combo + exit + TF. Comparing M5 EA vs H4 Python is meaningless.
    """
    d = _py_rules_dir()
    if not os.path.isdir(d):
        return None, None

    # Extract 8-hex ID from combo for robust matching
    m = re.search(r"[0-9a-f]{8}", str(combo).lower())
    hexid = m.group(0) if m else None
    want_exit = _norm(exit_name)
    want_tf = _norm(entry_tf)

    best = None
    for f in glob.glob(os.path.join(d, "rule_*.json")):
        base = os.path.basename(f).lower()
        # Quick filter: file must contain the hex ID
        if hexid and hexid not in base:
            continue

        try:
            with open(f, encoding="utf-8") as fh:
                rd = json.load(fh)
        except Exception:
            continue

        if not rd.get("trades"):
            continue

        r_exit = _norm(rd.get("exit_name") or rd.get("exit_strategy") or "")
        r_tf = _norm(rd.get("entry_tf") or "")

        # Score the match: exit match + tf match
        score = 0
        if want_exit and want_exit in r_exit:
            score += 2
        if want_tf and want_tf == r_tf:
            score += 2
        elif want_tf and want_tf in r_tf:
            score += 1

        if best is None or score > best[0]:
            best = (score, rd, f, r_tf)

    if not best:
        return None, None

    _, rd, f, r_tf = best
    meta = {
        "file": os.path.basename(f),
        "py_tf": rd.get("entry_tf"),
        "tf_match": (_norm(rd.get("entry_tf") or "") == want_tf)
    }
    return rd["trades"], meta


def _parse_mt5_html_stats(path):
    """Parse summary stats from an MT5 HTML report (UTF-16). Returns dict or None."""
    # WHY: MT5 /config tester writes HTML, not xlsx. Stats live in <td> pairs.
    # CHANGED: June 2026 — HTML report parser replaces xlsx reader for summary stats
    # CHANGED: June 2026 — added data-config fields (symbol, period, bars, deposit, broker)
    import re
    for enc in ("utf-16", "utf-16-le", "utf-8"):
        try:
            with open(path, encoding=enc, errors="ignore") as f:
                html = f.read()
            break
        except Exception:
            html = ""
    if not html:
        return None

    def _grab(label):
        m = re.search(re.escape(label) + r'\s*:?\s*</td>\s*<td[^>]*>\s*([-\d., ]+)',
                      html, re.IGNORECASE)
        if not m:
            return None
        return m.group(1).replace(' ', '').replace(',', '')

    def _grab_text(label):
        """Tolerant grab for header fields — matches 'Label: value' or '<td>Label</td><td>value'."""
        # WHY: header fields aren't always strict <td> pairs; looser pattern for text values.
        m = re.search(re.escape(label) + r'\s*:?\s*(?:</td>\s*<td[^>]*>)?\s*([^<\n\r]+)',
                      html, re.IGNORECASE)
        return m.group(1).strip() if m else None

    return {
        "net_profit":    _grab("Total Net Profit") or _grab("Net Profit"),
        "profit_factor": _grab("Profit Factor"),
        "trades":        _grab("Total Trades"),
        # CHANGED: June 2026 — data-config fields (symbol, period, bars, deposit, broker)
        # WHY: confirms both sides tested same instrument/window/costs.
        "symbol":        _grab_text("Symbol"),
        "period":        _grab_text("Period"),
        "bars":          _grab_text("Bars"),
        "initial_deposit": _grab_text("Initial Deposit") or _grab_text("Initial deposit"),
        "broker":        _grab_text("Broker") or _grab_text("Company") or _grab_text("Server"),
    }


def _read_mt5_entries_html(path):
    """Return sorted list of MT5 entry datetimes from an HTML report (deals 'in')."""
    # WHY: MT5 /config writes HTML; deal rows look like <td>2026.01.05 08:00</td>...<td>in</td>
    # CHANGED: June 2026 — parse HTML trade table instead of xlsx
    import re
    for enc in ("utf-16", "utf-16-le", "utf-8"):
        try:
            with open(path, encoding=enc, errors="ignore") as f:
                html = f.read()
            break
        except Exception:
            html = ""
    if not html:
        return []
    # Each row: sequence of <td> cells; we want rows where one cell matches 'in' (entry direction)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
    out = []
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if not cells:
            continue
        if any(c.lower() == 'in' for c in cells):
            # first cell that looks like a datetime
            for c in cells:
                try:
                    out.append(datetime.strptime(c[:16], '%Y.%m.%d %H:%M'))
                    break
                except Exception:
                    pass
    return sorted(out)


def _read_mt5_entries(path):
    """Dispatch to HTML or xlsx reader based on extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.htm', '.html'):
        return _read_mt5_entries_html(path)
    # Legacy xlsx path (kept for back-compat)
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
        hdr_i = None
        for i, row in enumerate(rows):
            if row and row[0] == 'Time' and 'Deal' in [str(c) for c in row]:
                hdr_i = i
                break
        if hdr_i is None:
            return []
        out = []
        for row in rows[hdr_i + 1:]:
            if not row or row[0] is None:
                continue
            if 'Balance:' in str(row[0]):
                break
            if len(row) > 4 and str(row[4]).lower() == 'in':
                try:
                    out.append(datetime.strptime(str(row[0])[:16], '%Y.%m.%d %H:%M'))
                except Exception:
                    pass
        return sorted(out)
    except Exception:
        return []


def _parse_mt5_html(path):
    """Return (trades, stats) from an MT5 HTML report. Full per-trade deals + summary stats.

    The detailed HTML report (635KB+) contains:
    - Summary stats table: Symbol, Period, Initial Deposit, Total Net Profit, Profit Factor, etc.
    - Deals table with columns: Time | Deal | Symbol | Type | Direction | Volume | Price | Order | Commission | Swap | Profit | Balance | Comment

    This parser extracts both stats and pairs in/out deals into complete trades.
    """
    import re

    # Read HTML with multiple encoding attempts
    html = ""
    for enc in ("utf-16", "utf-16-le", "utf-8"):
        try:
            with open(path, encoding=enc, errors="ignore") as f:
                html = f.read()
            if html:
                break
        except Exception:
            html = ""

    # Parse all table rows and cells first
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.I)

    def _cells(row):
        """Extract cell contents from a table row, stripping HTML tags."""
        cs = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.I)
        return [re.sub(r'<[^>]+>', '', c).strip() for c in cs]

    # CHANGED: June 2026 — extract summary stats via row-cell scan (robust to label colon placement)
    # WHY: MT5 HTML has <td>Symbol:</td><td>XAUUSD</td> (colon inside label cell).
    #   Regex approach missed because it expected Label</td><td> without colon.
    #   Row-cell scan: find cell matching label (ignoring trailing colon), take next non-empty cell.
    all_cells = []
    for row in rows:
        all_cells.extend(_cells(row))

    def _stat(label):
        """Find label cell, return next non-empty cell value."""
        lab = label.lower()
        for i, c in enumerate(all_cells):
            if c.strip().rstrip(":").lower() == lab:
                # Look ahead up to 3 cells for the value
                for j in range(i + 1, min(i + 4, len(all_cells))):
                    v = all_cells[j].strip()
                    if v and v != ":":
                        # Strip thousand separators (space or non-breaking space)
                        return v.replace(" ", "").replace("\xa0", "")
        return None

    stats = {
        "symbol":          _stat("Symbol"),
        "period":          _stat("Period"),
        "bars":            _stat("Bars"),
        "initial_deposit": _stat("Initial Deposit"),
        "net_profit":      _stat("Total Net Profit"),
        "profit_factor":   _stat("Profit Factor"),
        "total_deals":     _stat("Total Deals"),
    }

    # Find the deals header row: contains 'Time' and 'Deal' and 'Direction'
    col = None
    start = 0
    for i, row in enumerate(rows):
        cs = _cells(row)
        if cs and "Time" in cs and "Deal" in cs and ("Direction" in cs or "Profit" in cs):
            # Map column names to indices
            col = {name: k for k, name in enumerate(cs)}
            start = i + 1
            break

    trades = []
    if col is not None:
        def g(cs, name):
            """Get cell value by column name."""
            k = col.get(name)
            return cs[k] if (k is not None and k < len(cs)) else None

        def _num(x):
            """Parse numeric value, handling spaces and non-breaking spaces."""
            try:
                return float(str(x).replace(" ", "").replace("\xa0", ""))
            except Exception:
                return None

        # Pair in/out deals
        pend = None
        for row in rows[start:]:
            cs = _cells(row)
            if not cs:
                continue

            dirn = (g(cs, "Direction") or "").lower()
            typ = (g(cs, "Type") or "").lower()

            # Skip balance adjustments
            if typ == "balance":
                continue

            if dirn == "in":
                # Entry deal - save for pairing
                pend = cs
            elif dirn == "out" and pend is not None:
                # Exit deal - pair with pending entry
                trades.append({
                    "entry_time":  g(pend, "Time"),
                    "exit_time":   g(cs, "Time"),
                    "entry_price": _num(g(pend, "Price")),
                    "exit_price":  _num(g(cs, "Price")),
                    "direction":   g(pend, "Type"),
                    "volume":      g(pend, "Volume"),
                    "commission":  (_num(g(pend, "Commission")) or 0) + (_num(g(cs, "Commission")) or 0),
                    "swap":        _num(g(cs, "Swap")) or 0,
                    "profit":      _num(g(cs, "Profit")) or 0,
                    "comment":     g(pend, "Comment") or "",  # capture for future indicator logging
                })
                pend = None

    return trades, stats


# CHANGED: June 2026 — full MT5 xlsx parser (trades + stats)
# WHY: MT5 tester writes ReportTester xlsx with full deal pairs (in/out) and summary stats.
#   The HTML report lacks per-trade details; xlsx has entry/exit times, prices, profit, commission.
def _parse_mt5_xlsx(path):
    """Return (trades, stats) from an MT5 ReportTester xlsx.

    trades: list of dicts with entry_time, exit_time, entry_price, exit_price,
            direction, volume, profit, commission, swap.
    stats:  dict with symbol, period, initial_deposit, net_profit, profit_factor, total_deals.
    """
    import openpyxl
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    # CHANGED: June 2026 — search all worksheets for stats (labels may be on different sheet)
    # WHY: some MT5 builds put summary on a separate sheet; iterate all sheets for robustness.
    rows = []
    for ws in wb.worksheets:
        rows += list(ws.iter_rows(values_only=True))

    def _cellval(label):
        """Find a label cell and return the next non-empty cell's value."""
        for r in rows:
            cells = list(r)
            for i, c in enumerate(cells):
                if c is not None and str(c).strip().rstrip(":").lower() == label.lower():
                    # Value is the next non-None cell
                    for j in range(i + 1, len(cells)):
                        if cells[j] is not None and str(cells[j]).strip():
                            return str(cells[j]).strip()
        return None

    stats = {
        "symbol":          _cellval("Symbol"),
        "period":          _cellval("Period"),
        "initial_deposit": _cellval("Initial Deposit"),
        "net_profit":      _cellval("Total Net Profit"),
        "profit_factor":   _cellval("Profit Factor"),
        "total_deals":     _cellval("Total Deals"),
    }

    # CHANGED: June 2026 — find the deals HEADER row like the working entries reader
    # WHY: looking for 'Deals' section label didn't match some MT5 layouts; the proven reader
    #   finds the header by 'Time' in first cell + 'Deal' somewhere in the row. This IS the
    #   column header row, so data starts at hdr+1.
    # CHANGED: June 2026 — accept 'Time' anywhere in row (not just first cell) for robustness
    # WHY: some MT5 layouts may have a different column order or leading columns.
    hdr_idx = None
    for i, r in enumerate(rows):
        if not r:
            continue
        cells = [str(c).strip() for c in r if c is not None]
        # Match if row contains both 'Time' AND 'Deal' (column headers)
        if "Time" in cells and "Deal" in cells:
            hdr_idx = i
            break

    trades = []
    if hdr_idx is None:
        return trades, stats  # No deals header found, return empty trades

    header = [str(c).strip() if c is not None else "" for c in rows[hdr_idx]]
    col = {name: k for k, name in enumerate(header)}

    def g(row, name):
        """Get column value by name."""
        k = col.get(name)
        return row[k] if (k is not None and k < len(row)) else None

    pending_in = None
    for r in rows[hdr_idx + 1:]:
        if not r or r[0] is None:
            continue
        # Stop at Balance: summary row (marks end of deals in some layouts)
        if "Balance:" in str(r[0]):
            break
        typ = str(g(r, "Type") or "").lower()
        direction = str(g(r, "Direction") or "").lower()
        # Skip balance rows
        if typ == "balance":
            continue
        # Pair in/out deals into trades
        if direction == "in":
            pending_in = r
        elif direction == "out" and pending_in is not None:
            trades.append({
                "entry_time":  g(pending_in, "Time"),
                "exit_time":   g(r, "Time"),
                "entry_price": g(pending_in, "Price"),
                "exit_price":  g(r, "Price"),
                "direction":   str(g(pending_in, "Type") or ""),
                "volume":      g(pending_in, "Volume"),
                "commission":  (float(g(pending_in, "Commission") or 0) +
                               float(g(r, "Commission") or 0)),
                "swap":        float(g(r, "Swap") or 0),
                "profit":      float(g(r, "Profit") or 0),
            })
            pending_in = None

    return trades, stats


# CHANGED: June 2026 — find MT5 report with xlsx preference
# WHY: xlsx has full deal data; htm is summary-only fallback.
# CHANGED: June 2026 — sort by newest (mtime) to get the right report when multiple exist.
def _find_report(reports_dir, ea_name):
    """Find the MT5 report for an EA, preferring xlsx over htm/html, newest first."""
    import glob as _glob
    # Try exact name match first (EA name + extension), newest first
    for pat in (ea_name + ".xlsx",
                "*" + ea_name + "*.xlsx",      # partial match (e.g. ReportTester-<n>.xlsx)
                "ReportTester*.xlsx",           # MT5 default name if EA-named path wasn't honored
                ea_name + ".htm",
                ea_name + ".html"):
        hits = sorted(_glob.glob(os.path.join(reports_dir, pat)),
                      key=lambda p: os.path.getmtime(p), reverse=True)
        if hits:
            return hits[0]  # Return newest match
    return None


def _read_python_entries(csv_path):
    """Return sorted list of Python entry datetimes (minute-truncated)."""
    out = []
    with open(csv_path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                out.append(datetime.strptime(r['entry_time'], '%Y-%m-%d %H:%M:%S')
                           .replace(second=0))
            except Exception:
                pass
    return sorted(out)


# CHANGED: June 2026 — extract entry times from stored trade list (not CSV)
# WHY: _load_py_trades_for returns a list of trade dicts; we need datetimes for comparison.
def _extract_py_entry_times(trades):
    """Return sorted list of Python entry datetimes from a trade list."""
    out = []
    if not trades:
        return out
    for t in trades:
        if not isinstance(t, dict):
            continue
        et = t.get('entry_time')
        if not et:
            continue
        try:
            # handle both string and datetime formats
            if isinstance(et, str):
                dt = datetime.strptime(et, '%Y-%m-%d %H:%M:%S').replace(second=0)
            elif isinstance(et, datetime):
                dt = et.replace(second=0)
            else:
                continue
            out.append(dt)
        except Exception:
            pass
    return sorted(out)


def _clean_combo(combo):
    # WHY: mirror view_results.py:992 exactly so names line up.
    # CHANGED: June 2026
    return str(combo).replace(' ', '_').replace('/', '_')[:30]


def _clean_exit(ex):
    # WHY: mirror view_results.py:993 exactly.
    # CHANGED: June 2026
    return str(ex).replace(' ', '_').replace('/', '_')[:20]


def _python_trades_for(name, python_dir, rec=None):
    """Locate the Python trades CSV for an EA.

    Preferred path (rec given from manifest): reconstruct the backtester's export
    name  trades_{clean_combo}_{clean_exit}{_tf}_{stamp}.csv  and glob the stamp,
    returning the NEWEST match. Falls back to the old name-based candidates.
    """
    # WHY: manifest-driven match against the real exporter naming scheme.
    # CHANGED: June 2026 — view_results exports trades_{combo}_{exit}{_tf}_{stamp}.csv
    if rec:
        combo = rec.get('rule_combo') or name
        ex = rec.get('exit_name') or ''
        tf = rec.get('entry_tf') or ''
        tf_tag = ('_' + tf) if tf else ''
        pat = 'trades_%s_%s%s_*.csv' % (_clean_combo(combo), _clean_exit(ex), tf_tag)
        hits = glob.glob(os.path.join(python_dir, pat))
        if hits:
            # newest run wins (the stamp guard means many runs may coexist)
            hits.sort(key=os.path.getmtime, reverse=True)
            return hits[0]

    # CHANGED: June 2026 — tolerant match: extract combo (8-hex) + exit keyword from EA name
    # WHY: EA names now carry exit+tf suffixes (e.g. BUY_M5_4c_0608_198587b7_M5_ATR_Only),
    #   while CSVs are trades_{combo}_{exit}{tf}_{stamp}.csv. Match by combo + exit token.
    # CHANGED: June 2026 — guard against empty python_dir (when no CSVs exported yet)
    if not python_dir:
        return None
    toks = name.replace("BUY_", "").replace("SELL_", "").split("_")
    # find the 8-hex combo token (e.g. 198587b7)
    combo = next((t for t in toks if len(t) == 8 and all(c in "0123456789abcdef" for c in t.lower())), None)
    cand = glob.glob(os.path.join(python_dir, "trades_*.csv"))
    if combo:
        cand = [c for c in cand if combo in os.path.basename(c)]
    # further narrow by an exit keyword if present in the EA name
    for kw in ("ATR_Only", "ATR_Fixed", "Fixed_SL", "Time_Based", "Indicator", "Hybrid",
               "Trailing", "PSAR", "Breakeven"):
        if kw.lower() in name.lower():
            hit = [c for c in cand if kw.lower() in os.path.basename(c).lower()]
            if hit:
                cand = hit
                break
    # newest match wins
    if cand:
        cand.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return cand[0]

    # Fallback: legacy/simple layouts.
    cands = [
        os.path.join(python_dir, name + '.csv'),
        os.path.join(python_dir, name + '_trades.csv'),
        os.path.join(python_dir, 'trades_' + name + '.csv'),
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    shared = os.path.join(python_dir, 'trades.csv')
    return shared if os.path.exists(shared) else None


def _compare(mt5, py, bar_minutes=5):
    """Return dict of comparison metrics between two sorted datetime lists."""
    mset, pset = set(mt5), set(py)
    if not mt5:
        return dict(mt5=0, py=len(py), py_in_range=0, exact=0,
                    one_bar_early=0, one_bar_late=0, py_after_end=0)
    cutoff = max(mt5)
    py_in = set(p for p in pset if p <= cutoff)
    delta = timedelta(minutes=bar_minutes)
    early = sum(1 for p in pset if (p + delta) in mset)   # python earlier than MT5
    late = sum(1 for p in pset if (p - delta) in mset)    # python later than MT5
    return dict(
        mt5=len(mset), py=len(pset), py_in_range=len(py_in),
        exact=len(mset & pset),
        one_bar_early=early, one_bar_late=late,
        py_after_end=len(pset - py_in),
    )


def compare_folder(reports_dir, python_dir, manifest_path=None, bar_minutes=5):
    """Compare every MT5 .xlsx report in reports_dir to its Python trades.

    Prints a table and returns the list of row dicts.
    """
    # WHY: keep full manifest record per name so we can rebuild the export filename,
    #      not just a display label.
    # CHANGED: June 2026
    label = {}
    rec_by_name = {}
    if manifest_path and os.path.exists(manifest_path):
        try:
            for rec in json.load(open(manifest_path, encoding='utf-8')):
                rec_by_name[rec['name']] = rec
                label[rec['name']] = rec.get('rule_combo', rec['name'])
        except Exception:
            pass

    # CHANGED: June 2026 — MT5 /config writes HTML reports; accept .htm and .html (+ legacy .xlsx)
    reports = sorted(glob.glob(os.path.join(reports_dir, '*.htm')) +
                     glob.glob(os.path.join(reports_dir, '*.html')) +
                     glob.glob(os.path.join(reports_dir, '*.xlsx')))
    if not reports:
        print('[CMP] no reports (.htm/.html/.xlsx) in %s' % reports_dir)
        return ["No MT5 reports found — check the tester actually wrote them."]

    rows = []
    print('\n%-32s %5s %5s %8s %6s %7s %6s %6s  %s' %
          ('EA', 'MT5', 'PY', 'net_pip', 'exact', 'early', 'late', 'after', 'note'))
    print('-' * 100)
    for rp in reports:
        name = os.path.splitext(os.path.basename(rp))[0]

        # CHANGED: June 2026 — use full parsers for stats (populate net_profit, profit_factor)
        # WHY: _parse_mt5_html and _parse_mt5_xlsx return complete stats; _parse_mt5_html_stats
        #   was a partial fallback. Full parsers extract summary stats from detailed reports.
        stats = {}
        try:
            if rp.lower().endswith('.xlsx'):
                _, stats = _parse_mt5_xlsx(rp)
            elif rp.lower().endswith(('.htm', '.html')):
                _, stats = _parse_mt5_html(rp)
        except Exception:
            stats = {}

        mt5 = _read_mt5_entries(rp)

        # CHANGED: June 2026 — load Python trades from stored outputs/rules/*.json
        # WHY: trades already exist on disk; no manual export/CSV needed. Fully automatic.
        #   Must match combo + exit + TF (comparing M5 EA vs H4 Python is meaningless).
        rec = rec_by_name.get(name, {})
        combo = str(rec.get('rule_combo') or '')
        exit_name = rec.get('exit_name') or ''
        tf = rec.get('entry_tf') or ''
        py_trades, meta = _load_py_trades_for(combo, exit_name, tf) if combo else (None, None)

        if not py_trades:
            # No stored trades for this rule (never backtested, or different combo)
            print('%-32s %5d   no stored Python run for this rule (combo=%s)' %
                  (name[:32], len(mt5), combo[:8] or '?'))
            rows.append(dict(name=name, mt5=len(mt5), error='no_python',
                            note='no stored Python run'))
            continue

        # Check TF match — if stored Python run is different TF than EA, comparison is meaningless
        if meta and not meta.get('tf_match'):
            py_tf = meta.get('py_tf') or '?'
            note = f"PY run TF={py_tf} ≠ EA TF={tf} (counts not comparable)"
            print('%-32s %5d %5d %8s  %s' %
                  (name[:32], len(mt5), len(py_trades),
                   (stats.get('net_profit') or '?')[:8], note))
            rows.append(dict(name=name, mt5=len(mt5), py=len(py_trades),
                            net_profit=stats.get('net_profit', ''),
                            profit_factor=stats.get('profit_factor', ''),
                            note=note))
            continue

        # TF matches → do real entry-time comparison
        py = _extract_py_entry_times(py_trades)
        m = _compare(mt5, py, bar_minutes=bar_minutes)
        m['name'] = name
        m['net_profit'] = stats.get('net_profit', '')
        m['profit_factor'] = stats.get('profit_factor', '')
        m['note'] = f"matched (file: {meta.get('file', '?')[:20]})"
        rows.append(m)
        print('%-32s %5d %5d %8s %6d %7d %6d %6d  %s' %
              (name[:32], m['mt5'], m['py'],
               (stats.get('net_profit') or '?')[:8],
               m['exact'], m['one_bar_early'], m['one_bar_late'], m['py_after_end'],
               m['note'][:30]))

    # write CSV summary
    out_csv = os.path.join(reports_dir, 'comparison_summary.csv')
    keys = ['name', 'net_profit', 'profit_factor', 'mt5', 'py', 'py_in_range', 'exact',
            'one_bar_early', 'one_bar_late', 'py_after_end', 'note']
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print('\n[CMP] summary -> %s' % out_csv)
    print('Columns: exact=same-bar entries; early/late=Python off by one bar;')
    print('         after=Python trades past MT5 end (expected if Python ran longer).')
    print('         note=TF mismatch warning or match confirmation.')
    return rows


def compare_reports(reports_dir, python_dir, manifest_path=''):
    # WHY: panel wants printable lines, not dicts; capture compare_folder's stdout.
    # CHANGED: June 2026
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        compare_folder(reports_dir, python_dir,
                       manifest_path=manifest_path or None)
    return buf.getvalue().splitlines()


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='Compare MT5 reports folder to Python trades')
    p.add_argument('--reports', required=True, help='folder of MT5 .xlsx reports')
    p.add_argument('--python', required=True,
                   help='folder of Python trade CSVs (per-rule or a shared trades.csv)')
    p.add_argument('--manifest', default='', help='batch_manifest.json (optional labels)')
    p.add_argument('--bar-minutes', type=int, default=5, help='bar size for shift check')
    args = p.parse_args()
    compare_folder(args.reports, args.python,
                   manifest_path=args.manifest or None,
                   bar_minutes=args.bar_minutes)

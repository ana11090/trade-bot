"""Append-only history of Project-1 discovery runs.
Records the criteria that influence the algorithm so runs are comparable later.
Stored at project1_reverse_engineering/outputs/run_history.json (a JSON list)."""
import os, json, datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HIST = os.path.join(_ROOT, "project1_reverse_engineering", "outputs", "run_history.json")

# Only these config keys influence the discovery output (plus identity/context).
_INFLUENCING_KEYS = [
    "data_source_id", "data_source_path", "align_timeframes", "broker_timezone",
    "prop_firm_name", "prop_firm_id", "prop_firm_stage", "risk_pct",
    "feature_scope_mode", "lookback_candles",
    "rule_tree_max_depth", "rule_tree_min_samples_leaf", "rule_tree_min_samples_split",
    "rule_min_coverage", "rule_min_confidence", "rule_min_avg_pips", "rule_target_mode",
    "bot_entry_max_depth", "bot_entry_max_rules", "bot_entry_min_coverage",
    "bot_entry_min_win_rate", "match_rate_threshold", "adx_trend_threshold",
    "alignment_tolerance_pips", "regime_filter_mode", "regime_filter_strictness",
    "regime_filter_enabled", "rf_trees", "rf_random_state",
    "min_samples_leaf", "max_tree_depth",
    # tz alignment diagnostic — set by step1_align_price into a marker file
    # which run_scenarios folds into cfg before calling record_run.
    # tz_value supersedes tz_zone/tz_offset_hours (kept for backwards-compat
    # with older marker files); verification rates show why the chosen clock
    # was chosen — both candidates were measured with the same metric.
    "tz_mode", "tz_value", "tz_zone", "tz_offset_hours",
    "tz_verify_offset", "tz_verify_dst", "tz_verify_total",
]

def _load():
    try:
        with open(_HIST, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []

def record_run(cfg, scenarios_run, results=None):
    """Append one run record. cfg = config dict; scenarios_run = list of scenario
    keys; results = optional {scenario: {trade_count, feature_count, rule_count}}."""
    hist = _load()
    run_number = (max((r.get("run_number", 0) for r in hist), default=0) + 1)
    criteria = {k: cfg.get(k) for k in _INFLUENCING_KEYS if k in cfg}
    rec = {
        "run_number": run_number,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "dataset": cfg.get("data_source_id"),
        "timeframes": cfg.get("align_timeframes"),
        "scenarios": scenarios_run,
        "symbol_or_firm": cfg.get("prop_firm_name"),
        "broker_timezone": cfg.get("broker_timezone"),
        "criteria": criteria,
        "results": results or {},
    }
    hist.append(rec)
    os.makedirs(os.path.dirname(_HIST), exist_ok=True)
    tmp = _HIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, indent=2, default=str)
    os.replace(tmp, _HIST)
    return rec

def load_runs():
    """Newest first."""
    return sorted(_load(), key=lambda r: r.get("run_number", 0), reverse=True)

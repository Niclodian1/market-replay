"""Consistency checks for the generated ATR-entry experiment artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent


def main() -> None:
    config = json.loads((HERE / "experiment_config.json").read_text(encoding="utf-8"))
    recommendation = json.loads((HERE / "results" / "recommendation.json").read_text(encoding="utf-8"))
    manifest = pd.read_csv(HERE / "data" / "manifest.csv")
    metrics = pd.read_csv(HERE / "results" / "all_metrics.csv")
    dev_scores = pd.read_csv(HERE / "results" / "development_scores.csv")
    hold_scores = pd.read_csv(HERE / "results" / "holdout_scores.csv")

    n_filters = 1 + len(config["atr_thresholds"])
    expected_rows = len(manifest) * len(config["ma_periods"]) * n_filters * 2 * 2
    assert len(metrics) == expected_rows, (len(metrics), expected_rows)
    assert manifest["panel_id"].nunique() == len(manifest)
    assert set(metrics["split"]) == {"development", "holdout"}
    assert set(metrics["execution"]) == {"single", "legacy_parallel"}

    selected = float(recommendation["selected_body_atr_k"])
    dev_best = float(dev_scores.loc[dev_scores["neighborhood_score"].idxmax(), "atr_threshold"])
    assert selected == dev_best
    ordered_holdout = hold_scores.sort_values("combined_score", ascending=False).reset_index(drop=True)
    hold_rank = ordered_holdout.index[ordered_holdout["atr_threshold"].eq(selected)].tolist()
    assert len(hold_rank) == 1
    assert hold_rank[0] + 1 == recommendation["holdout_rank_among_atr_candidates"]

    selected_id = f"atr_{selected:.2f}"
    selected_rows = metrics[metrics["filter_id"] == selected_id]
    assert len(selected_rows) == len(manifest) * len(config["ma_periods"]) * 2 * 2
    assert selected_rows["n_trades"].sum() > 0
    assert selected_rows[["expectancy_pct", "performance_per_year_pct"]].notna().any().all()

    crypto = manifest[manifest["asset_class"] == "crypto"]
    durations = {"1h": 3600, "4h": 4 * 3600, "1day": 24 * 3600}
    now_ts = int(pd.Timestamp.now(tz="UTC").timestamp())
    for row in crypto.to_dict("records"):
        end_ts = int(pd.Timestamp(row["end"]).timestamp())
        assert end_ts + durations[row["timeframe"]] <= now_ts

    assert (HERE / "RESULTS.md").exists()
    assert (HERE / "results" / "parameter_comparison.png").stat().st_size > 10_000
    print(
        f"verification passed: {len(manifest)} panels, {len(metrics)} metric rows, "
        f"selected={selected:.2f} ATR, holdout rank="
        f"{recommendation['holdout_rank_among_atr_candidates']}/{len(config['atr_thresholds'])}"
    )


if __name__ == "__main__":
    main()

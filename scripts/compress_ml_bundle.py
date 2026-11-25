"""Compress existing scikit-learn joblib bundles and precompute dataset_stats.json.

Reduces Serverless bundle footprint & eliminates CSV reading on Vercel cold-starts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.data_pipeline import build_medallion_training_dataset  # noqa: E402
from backend.ml_engine import (  # noqa: E402
    DATASET_STATS_PATH,
    MODEL_ARTIFACT_PATH,
    MODELS_BUNDLE_PATH,
)


def main() -> None:
    """Compute dataset_stats.json from Gold CSV and compress joblib model bundles."""
    df = build_medallion_training_dataset(force_rebuild=False)
    stats = {
        "total_records": int(len(df)),
        "hex_cells_sampled": int(df["hex_id"].nunique()),
        "time_steps": int(df["step"].nunique()),
        "max_depth_observed_cm": round(float(df["target_depth_now_cm"].max()), 1),
        "flooded_ratio_pct": round(100.0 * float((df["target_depth_now_cm"] >= 10.0).mean()), 1),
    }
    DATASET_STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {DATASET_STATS_PATH.name}: {stats}")

    for path in (MODEL_ARTIFACT_PATH, MODELS_BUNDLE_PATH):
        if path.exists():
            before_kb = path.stat().st_size / 1024.0
            obj = joblib.load(path)
            joblib.dump(obj, path, compress=("zlib", 3))
            after_kb = path.stat().st_size / 1024.0
            print(f"Compressed {path.name}: {before_kb:,.1f} KB -> {after_kb:,.1f} KB")


if __name__ == "__main__":
    main()

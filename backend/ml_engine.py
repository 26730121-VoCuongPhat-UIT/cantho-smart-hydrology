"""Spatio-Temporal Machine Learning Engine for CanThoFloodGuard.

Trains and benchmarks 3 model families (Random Forest, HistGradientBoosting/LightGBM-equivalent,
and Multi-Layer Perceptron Neural Net) across +1h, +3h, and +6h forecast horizons using
chronological train/test splitting, plus regional feature attribution (Riverside vs Urban Basin).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import f1_score, mean_absolute_error, mean_squared_error, r2_score
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from backend.cantho_geo import (
    CANTHO_HEX_GRID,
    NODES_BY_ID,
    ROAD_SEGMENTS,
    distance_to_nearest_water_m,
)
from backend.data_pipeline import DATA_DIR, build_medallion_training_dataset

DANGEROUS_FLOOD_THRESHOLD_CM = 18.0  # Scooter stall threshold

NOWCAST_FEATURES: List[str] = [
    "elevation_m",
    "dist_water_m",
    "twi",
    "drainage_capacity",
    "is_riverside",
    "is_urban_basin",
    "tide_level_m",
    "tide_delta_m",
    "rain_mm_h",
    "rain_3h_cum",
]

FORECAST_BASE_FEATURES: List[str] = NOWCAST_FEATURES + [
    "hour_sin",
    "hour_cos",
    "temporal_lag_1h_cm",
    "spatial_neighbor_lag_1h_cm",
]

HORIZON_SPECS = [
    ("+1h", "target_depth_1h_cm", "tide_t1_m", "rain_nwp_1h_mm"),
    ("+3h", "target_depth_3h_cm", "tide_t3_m", "rain_nwp_3h_mm"),
    ("+6h", "target_depth_6h_cm", "tide_t6_m", "rain_nwp_6h_mm"),
]

MODEL_ARTIFACT_PATH = DATA_DIR / "nowcast_hgbr.joblib"
MODELS_BUNDLE_PATH = DATA_DIR / "nowcast_models_bundle.joblib"
BENCHMARK_ARTIFACT_PATH = DATA_DIR / "model_benchmark.json"
ATTRIBUTION_ARTIFACT_PATH = DATA_DIR / "feature_attribution.json"
DATASET_STATS_PATH = DATA_DIR / "dataset_stats.json"


def _flood_severity_label(depth_cm: float) -> Tuple[str, str]:
    """Map predicted flood depth (cm) to severity code and Vietnamese label."""
    if depth_cm < 5.0:
        return "dry", "Khô ráo"
    if depth_cm < 18.0:
        return "low", "Ngập nhẹ (< 18cm)"
    if depth_cm < 30.0:
        return "moderate", "Nguy hiểm xe tay ga (18–30cm)"
    return "severe", "Ngập sâu (> 30cm)"


def _build_model_factories() -> Dict[str, callable]:
    return {
        "RandomForest (Baseline)": lambda: RandomForestRegressor(
            n_estimators=60, max_depth=12, random_state=42, n_jobs=-1
        ),
        "HistGradientBoosting (LightGBM-style)": lambda: HistGradientBoostingRegressor(
            max_iter=150, max_depth=8, learning_rate=0.07, random_state=42
        ),
        "MLP Neural Net (Deep Baseline)": lambda: Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "mlp",
                    MLPRegressor(
                        hidden_layer_sizes=(64, 32),
                        max_iter=350,
                        early_stopping=True,
                        random_state=42,
                    ),
                ),
            ]
        ),
    }


def _evaluate_regression_and_alert(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    y_pred_clipped = np.clip(y_pred, 0.0, None)
    mae = float(mean_absolute_error(y_true, y_pred_clipped))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred_clipped)))
    r2 = float(r2_score(y_true, y_pred_clipped))

    y_true_bin = (y_true >= DANGEROUS_FLOOD_THRESHOLD_CM).astype(int)
    y_pred_bin = (y_pred_clipped >= DANGEROUS_FLOOD_THRESHOLD_CM).astype(int)
    f1 = float(f1_score(y_true_bin, y_pred_bin, zero_division=0))

    return {
        "mae_cm": round(mae, 2),
        "rmse_cm": round(rmse, 2),
        "r2": round(r2, 4),
        "f1_alert": round(f1, 4),
    }


def _group_feature_importance(importances: np.ndarray, feature_names: List[str]) -> Dict[str, float]:
    """Aggregate permutation importance into domain-meaningful Can Tho hydrology groups."""
    groups = {
        "Triều cường Sông Hậu": ["tide_level_m", "tide_delta_m", "tide_t1_m", "tide_t3_m", "tide_t6_m", "hour_sin", "hour_cos"],
        "Mưa & Dự báo NWP": ["rain_mm_h", "rain_3h_cum", "rain_nwp_1h_mm", "rain_nwp_3h_mm", "rain_nwp_6h_mm"],
        "Địa hình & Thoát nước (DEM/TWI)": ["elevation_m", "dist_water_m", "twi", "drainage_capacity", "is_riverside", "is_urban_basin"],
        "Độ trễ Không gian - Thời gian": ["temporal_lag_1h_cm", "spatial_neighbor_lag_1h_cm"],
    }
    raw_scores: Dict[str, float] = {}
    name_to_idx = {name: i for i, name in enumerate(feature_names)}
    for group_name, cols in groups.items():
        val = sum(max(0.0, float(importances[name_to_idx[c]])) for c in cols if c in name_to_idx)
        raw_scores[group_name] = val

    total = sum(raw_scores.values()) or 1.0
    return {k: round(100.0 * v / total, 1) for k, v in raw_scores.items()}


class CanThoFloodMLEngine:
    """Manages training, benchmarking, and real-time inference for Can Tho flood forecasting."""

    def __init__(self, force_retrain: bool = False) -> None:
        self.nowcast_model: HistGradientBoostingRegressor | None = None
        self.models: Dict[str, object] = {}
        self.benchmark_results: List[Dict] = []
        self.feature_attribution: Dict[str, Dict[str, float]] = {}
        self.dataset_stats: Dict = {}
        self._initialize(force_retrain=force_retrain)

    def _train_and_save_nowcast_bundle(self, df: pd.DataFrame) -> None:
        """Train and persist all 3 model families (HGBR, MLP, RF) for real-time inference."""
        X_now = df[NOWCAST_FEATURES]
        y_now = df["target_depth_now_cm"]

        hgbr = HistGradientBoostingRegressor(
            max_iter=180, max_depth=8, learning_rate=0.07, random_state=42
        )
        hgbr.fit(X_now, y_now)

        rf = RandomForestRegressor(
            n_estimators=60, max_depth=12, random_state=42, n_jobs=-1
        )
        rf.fit(X_now, y_now)

        mlp = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "mlp",
                    MLPRegressor(
                        hidden_layer_sizes=(64, 32),
                        max_iter=350,
                        early_stopping=True,
                        random_state=42,
                    ),
                ),
            ]
        )
        mlp.fit(X_now, y_now)

        self.nowcast_model = hgbr
        self.models = {
            "hgbr": hgbr,
            "mlp": mlp,
            "rf": rf,
        }
        joblib.dump(hgbr, MODEL_ARTIFACT_PATH, compress=("zlib", 3))
        joblib.dump(self.models, MODELS_BUNDLE_PATH, compress=("zlib", 3))

    def _initialize(self, force_retrain: bool = False) -> None:
        # Fast cold-start path for Serverless/Production: avoid reading 3.67 MB CSV when artifacts exist
        if (
            not force_retrain
            and MODELS_BUNDLE_PATH.exists()
            and BENCHMARK_ARTIFACT_PATH.exists()
            and ATTRIBUTION_ARTIFACT_PATH.exists()
            and DATASET_STATS_PATH.exists()
        ):
            self.dataset_stats = json.loads(DATASET_STATS_PATH.read_text(encoding="utf-8"))
            self.models = joblib.load(MODELS_BUNDLE_PATH)
            self.nowcast_model = self.models.get("hgbr")
            if self.nowcast_model is None and MODEL_ARTIFACT_PATH.exists():
                self.nowcast_model = joblib.load(MODEL_ARTIFACT_PATH)
            self.benchmark_results = json.loads(BENCHMARK_ARTIFACT_PATH.read_text(encoding="utf-8"))
            self.feature_attribution = json.loads(ATTRIBUTION_ARTIFACT_PATH.read_text(encoding="utf-8"))
            return

        df = build_medallion_training_dataset(force_rebuild=force_retrain)
        self.dataset_stats = {
            "total_records": int(len(df)),
            "hex_cells_sampled": int(df["hex_id"].nunique()),
            "time_steps": int(df["step"].nunique()),
            "max_depth_observed_cm": round(float(df["target_depth_now_cm"].max()), 1),
            "flooded_ratio_pct": round(100.0 * float((df["target_depth_now_cm"] >= 10.0).mean()), 1),
        }
        try:
            DATASET_STATS_PATH.write_text(
                json.dumps(self.dataset_stats, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

        if (
            not force_retrain
            and MODELS_BUNDLE_PATH.exists()
            and MODEL_ARTIFACT_PATH.exists()
            and BENCHMARK_ARTIFACT_PATH.exists()
            and ATTRIBUTION_ARTIFACT_PATH.exists()
        ):
            self.models = joblib.load(MODELS_BUNDLE_PATH)
            self.nowcast_model = self.models.get("hgbr") or joblib.load(MODEL_ARTIFACT_PATH)
            self.benchmark_results = json.loads(BENCHMARK_ARTIFACT_PATH.read_text(encoding="utf-8"))
            self.feature_attribution = json.loads(ATTRIBUTION_ARTIFACT_PATH.read_text(encoding="utf-8"))
            return

        # 1. Train real-time scenario predictors across all 3 model families (HGBR, MLP, RF)
        self._train_and_save_nowcast_bundle(df)

        if not force_retrain and BENCHMARK_ARTIFACT_PATH.exists() and ATTRIBUTION_ARTIFACT_PATH.exists():
            self.benchmark_results = json.loads(BENCHMARK_ARTIFACT_PATH.read_text(encoding="utf-8"))
            self.feature_attribution = json.loads(ATTRIBUTION_ARTIFACT_PATH.read_text(encoding="utf-8"))
            return

        # 2. Chronological train/test split (first 72% time steps train, last 28% test)
        max_step = int(df["step"].max())
        split_step = int(max_step * 0.72)
        train_df = df[df["step"] <= split_step].reset_index(drop=True)
        test_df = df[df["step"] > split_step].reset_index(drop=True)

        factories = _build_model_factories()
        benchmarks: List[Dict] = []

        primary_1h_model = None
        primary_1h_features: List[str] = []
        for horizon_label, target_col, tide_target_col, rain_target_col in HORIZON_SPECS:
            feat_cols = FORECAST_BASE_FEATURES + [tide_target_col, rain_target_col]
            X_train, y_train = train_df[feat_cols], train_df[target_col].to_numpy()
            X_test, y_test = test_df[feat_cols], test_df[target_col].to_numpy()

            for model_name, make_model in factories.items():
                model = make_model()
                t0 = time.perf_counter()
                model.fit(X_train, y_train)
                train_ms = (time.perf_counter() - t0) * 1000.0

                t1 = time.perf_counter()
                preds = model.predict(X_test)
                infer_ms = (time.perf_counter() - t1) * 1000.0

                metrics = _evaluate_regression_and_alert(y_test, preds)
                benchmarks.append(
                    {
                        "horizon": horizon_label,
                        "model": model_name,
                        **metrics,
                        "train_time_ms": round(train_ms, 1),
                        "infer_time_ms": round(infer_ms, 2),
                    }
                )
                if horizon_label == "+1h" and model_name.startswith("HistGradientBoosting"):
                    primary_1h_model = model
                    primary_1h_features = feat_cols

        self.benchmark_results = benchmarks
        BENCHMARK_ARTIFACT_PATH.write_text(json.dumps(benchmarks, ensure_ascii=False, indent=2), encoding="utf-8")

        # 3. Regional Feature Attribution (Riverside vs Urban Basin) on +1h primary model
        attribution: Dict[str, Dict[str, float]] = {}
        if primary_1h_model is not None:
            for zone_key, zone_label in [
                ("riverside", "Vùng ven sông (Bến Ninh Kiều, Cái Khế, Huỳnh Cương)"),
                ("urban_basin", "Vùng trũng nội ô (Mậu Thân, Hồ Bún Xáng, Trần Văn Hoài)"),
            ]:
                sub_df = test_df[test_df["zone_type"] == zone_key]
                if len(sub_df) >= 20:
                    perm = permutation_importance(
                        primary_1h_model,
                        sub_df[primary_1h_features],
                        sub_df["target_depth_1h_cm"],
                        n_repeats=5,
                        random_state=42,
                    )
                    attribution[zone_label] = _group_feature_importance(perm.importances_mean, primary_1h_features)

        self.feature_attribution = attribution
        ATTRIBUTION_ARTIFACT_PATH.write_text(json.dumps(attribution, ensure_ascii=False, indent=2), encoding="utf-8")

    def predict_city_flood_map(
        self,
        tide_level_m: float,
        rain_mm_h: float,
        rain_3h_cum: float,
        tide_delta_m: float = 0.12,
        horizon: str = "+1h",
        model_key: str = "hgbr",
    ) -> Dict:
        """Predict flood depth (cm) across all Can Tho hex cells and road segments.

        Supports forecast horizons ('now', '+1h', '+3h', '+6h') and real ML model selection
        ('hgbr' = HistGradientBoosting, 'mlp' = MLP Neural Net, 'rf' = RandomForest).
        """
        assert self.nowcast_model is not None

        eff_tide = float(tide_level_m)
        eff_rain = float(rain_mm_h)
        eff_rain3h = float(rain_3h_cum)
        eff_delta = float(tide_delta_m)

        if horizon == "+3h":
            # Project tidal harmonic & cumulative basin runoff 3 hours ahead
            eff_tide = max(0.8, min(2.48, eff_tide + eff_delta * 1.15))
            eff_rain3h = max(0.0, min(180.0, eff_rain3h * 0.85 + eff_rain * 1.35))
            eff_rain = max(0.0, eff_rain * 0.78)
            eff_delta = round(-eff_delta * 0.35, 3)
        elif horizon == "+6h":
            # Semi-diurnal half cycle (~6.1h): high tide ebbs toward low water, while low tide rises toward high water
            half_cycle_shift = max(0.24, abs(eff_delta) * 1.65)
            if eff_tide >= 1.55:
                eff_tide = max(0.8, min(2.45, eff_tide - half_cycle_shift))
                eff_delta = round(-abs(eff_delta) * 0.75, 3)
            else:
                eff_tide = max(0.8, min(2.45, eff_tide + half_cycle_shift))
                eff_delta = round(abs(eff_delta) * 0.75, 3)
            eff_rain3h = max(0.0, eff_rain3h * 0.52)
            eff_rain = max(0.0, eff_rain * 0.35)

        model_labels = {
            "hgbr": "HistGradientBoosting (LightGBM-style)",
            "mlp": "MLP Neural Net (Deep Baseline)",
            "rf": "RandomForest (Ensemble Baseline)",
        }
        norm_model_key = model_key if model_key in model_labels else "hgbr"
        active_model_label = model_labels[norm_model_key]
        active_estimator = self.models.get(norm_model_key, self.nowcast_model)

        # Vectorized feature construction for all hex cells
        hex_rows = [
            {
                "elevation_m": c.elevation_m,
                "dist_water_m": c.dist_water_m,
                "twi": c.twi,
                "drainage_capacity": c.drainage_capacity,
                "is_riverside": 1 if c.zone_type == "riverside" else 0,
                "is_urban_basin": 1 if c.zone_type == "urban_basin" else 0,
                "tide_level_m": eff_tide,
                "tide_delta_m": eff_delta,
                "rain_mm_h": eff_rain,
                "rain_3h_cum": eff_rain3h,
            }
            for c in CANTHO_HEX_GRID
        ]
        hex_df = pd.DataFrame(hex_rows)
        hex_preds = np.clip(active_estimator.predict(hex_df[NOWCAST_FEATURES]), 0.0, None)

        hex_results: List[Dict] = []
        for cell, depth in zip(CANTHO_HEX_GRID, hex_preds):
            d_val = round(float(depth), 1)
            sev_code, sev_label = _flood_severity_label(d_val)
            hex_results.append(
                {
                    "hex_id": cell.hex_id,
                    "center_lat": cell.center_lat,
                    "center_lon": cell.center_lon,
                    "vertices": cell.vertices,
                    "elevation_m": cell.elevation_m,
                    "nearest_landmark": cell.nearest_landmark,
                    "zone_type": cell.zone_type,
                    "depth_cm": d_val,
                    "severity": sev_code,
                    "severity_label": sev_label,
                }
            )

        # Vectorized feature construction for all road segments (midpoint water distance & TWI)
        road_rows = []
        for seg in ROAD_SEGMENTS:
            nu, nv = NODES_BY_ID[seg.u], NODES_BY_ID[seg.v]
            mid_lat = 0.5 * (nu.lat + nv.lat)
            mid_lon = 0.5 * (nu.lon + nv.lon)
            dist_w = round(distance_to_nearest_water_m(mid_lat, mid_lon), 1)
            twi = round(
                max(2.0, min(14.0, 9.5 - 2.8 * (seg.road_elevation_m - 1.0) + 2.2 * np.exp(-dist_w / 550.0))),
                2,
            )
            zone = "urban_basin" if seg.is_flood_hotspot and seg.road_elevation_m < 1.35 else (
                "riverside" if dist_w < 320.0 and seg.road_elevation_m < 1.6 else "elevated_arterial"
            )
            road_rows.append(
                {
                    "elevation_m": seg.road_elevation_m,
                    "dist_water_m": dist_w,
                    "twi": twi,
                    "drainage_capacity": seg.drainage_factor,
                    "is_riverside": 1 if zone == "riverside" else 0,
                    "is_urban_basin": 1 if zone == "urban_basin" else 0,
                    "tide_level_m": eff_tide,
                    "tide_delta_m": eff_delta,
                    "rain_mm_h": eff_rain,
                    "rain_3h_cum": eff_rain3h,
                }
            )
        road_df = pd.DataFrame(road_rows)
        road_preds = np.clip(active_estimator.predict(road_df[NOWCAST_FEATURES]), 0.0, None)

        road_depths: Dict[str, float] = {}
        road_results: List[Dict] = []
        for seg, depth in zip(ROAD_SEGMENTS, road_preds):
            d_val = round(float(depth), 1)
            sev_code, sev_label = _flood_severity_label(d_val)
            road_depths[seg.id] = d_val
            nu, nv = NODES_BY_ID[seg.u], NODES_BY_ID[seg.v]
            seg_coords = [[p[0], p[1]] for p in seg.coords] if seg.coords else [[nu.lat, nu.lon], [nv.lat, nv.lon]]
            road_results.append(
                {
                    "id": seg.id,
                    "name": seg.name,
                    "u": seg.u,
                    "v": seg.v,
                    "coords": seg_coords,
                    "length_m": seg.length_m,
                    "road_elevation_m": seg.road_elevation_m,
                    "depth_cm": d_val,
                    "severity": sev_code,
                    "severity_label": sev_label,
                }
            )

        flooded_hexes = [h for h in hex_results if h["depth_cm"] >= 10.0]
        dangerous_roads = sorted([r for r in road_results if r["depth_cm"] >= 18.0], key=lambda x: -x["depth_cm"])

        summary = {
            "active_horizon": horizon,
            "active_model_key": norm_model_key,
            "active_model_label": active_model_label,
            "effective_tide_m": round(eff_tide, 2),
            "effective_rain_mm_h": round(eff_rain, 1),
            "total_hex_cells": len(hex_results),
            "flooded_hex_count": len(flooded_hexes),
            "dangerous_hex_count": sum(1 for h in hex_results if h["depth_cm"] >= 18.0),
            "max_depth_cm": round(max((h["depth_cm"] for h in hex_results), default=0.0), 1),
            "avg_flooded_depth_cm": round(
                float(np.mean([h["depth_cm"] for h in flooded_hexes])) if flooded_hexes else 0.0, 1
            ),
            "dangerous_road_count": len(dangerous_roads),
            "top_flooded_roads": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "depth_cm": r["depth_cm"],
                    "severity": r["severity"],
                    "mid_coord": r["coords"][len(r["coords"]) // 2],
                }
                for r in dangerous_roads[:6]
            ],
        }

        return {
            "hex_cells": hex_results,
            "roads": road_results,
            "road_depths": road_depths,
            "summary": summary,
        }

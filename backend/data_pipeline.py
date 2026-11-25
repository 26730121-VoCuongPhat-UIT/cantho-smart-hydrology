"""Medallion Data Pipeline (Bronze -> Silver -> Gold) for CanThoFloodGuard.

Integrates:
1. Live/fallback hourly meteorology for Can Tho from Open-Meteo API (Bronze).
2. Hau River (Can Tho hydrological station) asymmetric semi-diurnal tidal simulation.
3. Topographic & spatial feature engineering on the Can Tho H3-style hex grid (Silver).
4. Physics-guided spatio-temporal flood depth dataset generation for ML training (Gold).
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import requests

from backend.cantho_geo import (
    CANTHO_DISTRICTS,
    CANTHO_HEX_GRID,
    DISTRICTS_BY_ID,
    DistrictProfile,
    HexCell,
)

VN_TZ = timezone(timedelta(hours=7))

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass


def _resolve_runtime_cache_dir() -> Path:
    """Resolve writable directory for runtime API/DB caches (Vercel serverless uses /tmp)."""
    explicit = os.environ.get("RUNTIME_CACHE_DIR")
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    elif os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        candidates.append(Path("/tmp/cantho_cache"))
    else:
        candidates.append(DATA_DIR)
        candidates.append(Path("/tmp/cantho_cache"))

    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
            if os.access(cand, os.W_OK):
                return cand
        except OSError:
            continue
    return Path("/tmp")


RUNTIME_CACHE_DIR = _resolve_runtime_cache_dir()


def _safe_write_json(path: Path, payload: Any) -> None:
    """Best-effort JSON cache write that never crashes when filesystem is read-only."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

CANTHO_LAT = 10.0333
CANTHO_LON = 105.7833

# Preset scenarios tailored to Can Tho hydrology
SCENARIOS: Dict[str, Dict] = {
    "spring_tide_rain": {
        "id": "spring_tide_rain",
        "name": "Đỉnh triều Rằm tháng 9 ÂL + Mưa lớn chiều (17h30)",
        "description": "Mực nước sông Hậu vượt Báo động III (2.22m) kết hợp mưa dông chiều 45 mm/h khiến cống nội ô bị khóa.",
        "tide_level_m": 2.22,
        "rain_mm_h": 45.0,
        "rain_3h_cum": 82.0,
        "tide_delta_m": 0.18,
        "hour": 17,
        "is_spring_tide": True,
    },
    "high_tide_dry": {
        "id": "high_tide_dry",
        "name": "Triều cường Rằm (Trời tạnh ráo)",
        "description": "Triều cường đạt 2.12m (trên Báo động III) nhưng không mưa; ngập chủ yếu ở các tuyến ven sông và rốn trũng.",
        "tide_level_m": 2.12,
        "rain_mm_h": 0.0,
        "rain_3h_cum": 2.0,
        "tide_delta_m": 0.14,
        "hour": 6,
        "is_spring_tide": True,
    },
    "heavy_storm_low_tide": {
        "id": "heavy_storm_low_tide",
        "name": "Mưa lớn cục bộ khi triều rút",
        "description": "Mưa to 60 mm/h nhưng mực nước sông Hậu thấp (1.42m), cống thoát nước hoạt động tốt.",
        "tide_level_m": 1.42,
        "rain_mm_h": 60.0,
        "rain_3h_cum": 95.0,
        "tide_delta_m": -0.10,
        "hour": 14,
        "is_spring_tide": False,
    },
    "normal_dry": {
        "id": "normal_dry",
        "name": "Ngày bình thường (Nắng ráo, triều thấp)",
        "description": "Mực nước sông Hậu ở mức an toàn (1.35m), không mưa; toàn thành phố lưu thông thông thoáng.",
        "tide_level_m": 1.35,
        "rain_mm_h": 0.0,
        "rain_3h_cum": 0.0,
        "tide_delta_m": -0.05,
        "hour": 10,
        "is_spring_tide": False,
    },
}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, x))))


def simulate_hau_river_tide(
    hour_of_day: float,
    is_spring_tide: bool = True,
    peak_override_m: float | None = None,
) -> Tuple[float, float]:
    """Simulate Hau River level (m) and hourly rise rate (m/h) at Can Tho station.

    Can Tho experiences asymmetric semi-diurnal tides (2 highs/day, ~05:30 and ~17:30).
    Alarm levels: I = 1.80m, II = 1.90m, III = 2.00m.
    """
    base_mean = 1.42 if is_spring_tide else 1.18
    primary_amp = 0.72 if is_spring_tide else 0.42
    secondary_amp = 0.14 if is_spring_tide else 0.08

    # Asymmetric semi-diurnal harmonic (two peaks ~6:00 and ~17:30)
    phase = 2.0 * math.pi * (hour_of_day - 5.5) / 12.2
    diurnal_ineq = 2.0 * math.pi * (hour_of_day - 17.5) / 24.0
    level = base_mean + primary_amp * math.cos(phase) + secondary_amp * math.cos(diurnal_ineq)

    # Analytical derivative (m/h)
    delta = (
        -primary_amp * (2.0 * math.pi / 12.2) * math.sin(phase)
        - secondary_amp * (2.0 * math.pi / 24.0) * math.sin(diurnal_ineq)
    )

    if peak_override_m is not None:
        max_natural = base_mean + primary_amp + secondary_amp
        level = level + (peak_override_m - max_natural) * max(0.0, math.cos(phase))

    return round(max(0.45, min(2.55, level)), 3), round(delta, 3)


def generate_24h_hydrograph(
    is_spring_tide: bool = True,
    peak_tide_m: float = 2.22,
    rain_mm_h: float = 45.0,
) -> List[Dict]:
    """Generate a 24-hour hydrograph + rainfall profile for dashboard charts."""
    profile: List[Dict] = []
    for h in range(24):
        tide_m, delta_m = simulate_hau_river_tide(float(h), is_spring_tide=is_spring_tide, peak_override_m=peak_tide_m)
        # Typical Mekong Delta convective afternoon/evening rain peak around 16h-19h
        rain_factor = math.exp(-((h - 17.0) ** 2) / 6.5) + 0.15 * math.exp(-((h - 5.0) ** 2) / 4.0)
        hourly_rain = round(rain_mm_h * min(1.0, rain_factor), 1) if rain_mm_h > 0 else 0.0
        profile.append(
            {
                "hour": h,
                "label": f"{h:02d}:00",
                "tide_level_m": tide_m,
                "tide_delta_m": delta_m,
                "rain_mm_h": hourly_rain,
                "alarm_iii_m": 2.00,
            }
        )
    return profile


def fetch_cantho_weather_bronze(timeout_sec: float = 2.5) -> Dict:
    """Bronze layer: Fetch live/forecast hourly weather for Can Tho from Open-Meteo with offline fallback."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={CANTHO_LAT}&longitude={CANTHO_LON}"
        f"&hourly=precipitation,relative_humidity_2m,surface_pressure&forecast_days=2&timezone=Asia%2FBangkok"
    )
    runtime_bronze_path = RUNTIME_CACHE_DIR / "bronze_openmeteo_cantho.json"
    bundled_bronze_path = DATA_DIR / "bronze_openmeteo_cantho.json"

    try:
        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
        payload = resp.json()
        payload["_source"] = "open-meteo-live"
        payload["_fetched_at"] = datetime.now().isoformat(timespec="seconds")
        _safe_write_json(runtime_bronze_path, payload)
        return payload
    except Exception:
        for candidate in (runtime_bronze_path, bundled_bronze_path):
            if candidate.exists():
                try:
                    cached = json.loads(candidate.read_text(encoding="utf-8"))
                    cached["_source"] = "open-meteo-cached"
                    return cached
                except Exception:
                    continue

        # Deterministic Mekong Delta rainy-season fallback profile
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        times = [(now + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(48)]
        precip = [
            round(max(0.0, 22.0 * math.exp(-(((i % 24) - 17) ** 2) / 5.0) - (1.5 if i % 5 == 0 else 0.0)), 1)
            for i in range(48)
        ]
        humidity = [int(round(78 + 14 * math.sin(i / 4.0))) for i in range(48)]
        fallback = {
            "latitude": CANTHO_LAT,
            "longitude": CANTHO_LON,
            "hourly": {
                "time": times,
                "precipitation": precip,
                "relative_humidity_2m": humidity,
                "surface_pressure": [1008.5] * 48,
            },
            "_source": "cantho-historical-fallback",
            "_fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
        _safe_write_json(runtime_bronze_path, fallback)
        return fallback


def compute_physical_flood_depth_cm(
    elevation_m: float,
    dist_water_m: float,
    twi: float,
    drainage_capacity: float,
    zone_type: str,
    tide_level_m: float,
    tide_delta_m: float,
    rain_mm_h: float,
    rain_3h_cum: float,
    prev_depth_cm: float = 0.0,
) -> float:
    """Physics-guided hydraulic-topographic flood depth model (cm) for Can Tho compound flooding.

    Models two coupled mechanisms:
    1. Tidal backflow through Hau/Can Tho river & storm drains when tide exceeds critical local sill.
    2. Rainfall runoff accumulation amplified when high tide submerges urban drainage outlets.
    """
    # 1. Tidal backflow component (stronger near rivers/canals and in low-elevation corridors)
    critical_sill_m = 0.65 * elevation_m + 0.72
    tide_excess_m = max(0.0, tide_level_m - critical_sill_m)
    river_coupling = 0.58 + 0.55 * math.exp(-dist_water_m / 420.0)
    if zone_type == "riverside":
        river_coupling *= 1.12
    backflow_resistance = 1.18 - 0.58 * drainage_capacity
    tidal_depth_cm = tide_excess_m * 100.0 * 0.54 * river_coupling * backflow_resistance

    # Rising tide pushes slightly higher surge than ebbing tide at same height
    if tide_delta_m > 0:
        tidal_depth_cm *= 1.0 + min(0.12, tide_delta_m * 0.35)

    # 2. Rainfall + Tide-locked drainage component
    # Storm drains in Ninh Kieu start back-pressuring once river level exceeds ~1.82m
    tide_lock_factor = _sigmoid((tide_level_m - 1.82) * 4.5)
    effective_drainage = drainage_capacity * (1.0 - 0.64 * tide_lock_factor)

    runoff_intensity = 0.42 * rain_mm_h + 0.30 * rain_3h_cum
    basin_factor = (twi / 7.5) * (1.28 - 0.36 * min(2.4, elevation_m))
    drainage_deficit = max(0.15, 1.05 - effective_drainage)
    rain_depth_cm = max(0.0, runoff_intensity * 0.48 * basin_factor * drainage_deficit - 6.5 * effective_drainage)

    # Elevated arterials shed water much faster via camber and box culverts
    if zone_type == "elevated_arterial" and elevation_m >= 1.75:
        tidal_depth_cm *= 0.42
        rain_depth_cm *= 0.45

    raw_depth_cm = tidal_depth_cm + rain_depth_cm

    # Hydrological inertia from previous hour's standing water
    blended_depth_cm = 0.84 * raw_depth_cm + 0.16 * prev_depth_cm
    return round(max(0.0, min(95.0, blended_depth_cm)), 2)


def build_medallion_training_dataset(force_rebuild: bool = False) -> pd.DataFrame:
    """Silver -> Gold pipeline: Create spatio-temporal dataset across Can Tho hex cells and tidal/rain episodes."""
    gold_path = DATA_DIR / "gold_spatiotemporal_training.csv"
    silver_path = DATA_DIR / "silver_hex_features.csv"

    # Always ensure Bronze weather is materialized
    _ = fetch_cantho_weather_bronze()

    if gold_path.exists() and not force_rebuild:
        return pd.read_csv(gold_path)

    # Silver layer: spatial grid features
    silver_df = pd.DataFrame(
        [
            {
                "hex_id": c.hex_id,
                "center_lat": c.center_lat,
                "center_lon": c.center_lon,
                "elevation_m": c.elevation_m,
                "dist_water_m": c.dist_water_m,
                "twi": c.twi,
                "drainage_capacity": c.drainage_capacity,
                "zone_type": c.zone_type,
                "is_riverside": 1 if c.zone_type == "riverside" else 0,
                "is_urban_basin": 1 if c.zone_type == "urban_basin" else 0,
            }
            for c in CANTHO_HEX_GRID
        ]
    )
    silver_df.to_csv(silver_path, index=False)

    # Sample diverse spatial cells across Can Tho and simulate a full 14-day (336h) lunar tidal cycle
    rng = np.random.default_rng(42)
    sampled_cells: List[HexCell] = CANTHO_HEX_GRID[::3]

    # Build 336-hour meteorological + tidal sequence covering neap -> spring -> neap cycles
    total_hours = 336
    hours_seq = []
    rain_history: List[float] = [0.0, 0.0, 0.0]
    # Spring tide modulation across 14 days (two spring peaks around day 2-4 and day 10-12)
    for step in range(total_hours):
        day = step // 24
        hour = step % 24
        lunar_wave = 0.5 + 0.5 * math.cos(2.0 * math.pi * (day - 2.5) / 8.5)
        is_spring = lunar_wave >= 0.45
        peak_override = round(1.52 + 0.78 * lunar_wave, 3)
        tide_m, tide_delta = simulate_hau_river_tide(float(hour), is_spring_tide=is_spring, peak_override_m=peak_override)

        # Realistic Mekong Delta rainfall episodes (mostly afternoon/evening + occasional early morning showers)
        rain = 0.0
        if day % 2 == 1 and 15 <= hour <= 19:
            rain = float(rng.uniform(20.0, 62.0))
        elif day in (2, 6, 10, 12) and 16 <= hour <= 20:
            rain = float(rng.uniform(30.0, 72.0))
        elif day in (4, 8, 11, 13) and 5 <= hour <= 8:
            rain = float(rng.uniform(14.0, 38.0))
        elif rng.random() < 0.12:
            rain = float(rng.uniform(3.0, 14.0))

        rain_history.append(rain)
        rain_3h = round(sum(rain_history[-3:]), 2)
        hours_seq.append(
            {
                "step": step,
                "hour": hour,
                "tide_level_m": tide_m,
                "tide_delta_m": tide_delta,
                "rain_mm_h": round(rain, 2),
                "rain_3h_cum": rain_3h,
            }
        )

    # Simulate cell depths over time so lag features (t-1h, spatial neighbor lag) and future targets (+1h, +3h, +6h) exist
    depth_matrix = np.zeros((len(hours_seq), len(sampled_cells)), dtype=float)
    for t, h_info in enumerate(hours_seq):
        for c_idx, cell in enumerate(sampled_cells):
            prev_d = depth_matrix[t - 1, c_idx] if t > 0 else 0.0
            d = compute_physical_flood_depth_cm(
                elevation_m=cell.elevation_m,
                dist_water_m=cell.dist_water_m,
                twi=cell.twi,
                drainage_capacity=cell.drainage_capacity,
                zone_type=cell.zone_type,
                tide_level_m=h_info["tide_level_m"],
                tide_delta_m=h_info["tide_delta_m"],
                rain_mm_h=h_info["rain_mm_h"],
                rain_3h_cum=h_info["rain_3h_cum"],
                prev_depth_cm=prev_d,
            )
            # Realistic sensor measurement perturbation (+-0.9cm when wet)
            noise = float(rng.normal(0.0, 0.9)) if d > 1.0 else 0.0
            depth_matrix[t, c_idx] = round(max(0.0, d + noise), 2)

    rows: List[Dict] = []
    # Precompute 3 nearest neighbor indices for spatial lag computation
    cell_coords = np.array([[c.center_lat, c.center_lon] for c in sampled_cells])
    neighbor_indices: List[np.ndarray] = []
    for idx in range(len(sampled_cells)):
        dists = np.linalg.norm(cell_coords - cell_coords[idx], axis=1)
        order = np.argsort(dists)[1:4]
        neighbor_indices.append(order)

    # Build Gold feature table using valid horizon [1 .. T-7]
    for t in range(1, len(hours_seq) - 6):
        h_now = hours_seq[t]
        h_1h = hours_seq[t + 1]
        h_3h = hours_seq[t + 3]
        h_6h = hours_seq[t + 6]
        # NWP rainfall forecast proxies with realistic lead-time uncertainty
        rain_nwp_1h = round(max(0.0, h_1h["rain_mm_h"] + float(rng.normal(0.0, 2.0))), 2)
        rain_nwp_3h = round(max(0.0, h_3h["rain_mm_h"] + float(rng.normal(0.0, 5.5))), 2)
        rain_nwp_6h = round(max(0.0, h_6h["rain_mm_h"] + float(rng.normal(0.0, 9.5))), 2)

        for c_idx, cell in enumerate(sampled_cells):
            neighbor_lag = float(np.mean(depth_matrix[t - 1, neighbor_indices[c_idx]]))
            rows.append(
                {
                    "step": t,
                    "hex_id": cell.hex_id,
                    "zone_type": cell.zone_type,
                    "elevation_m": cell.elevation_m,
                    "dist_water_m": cell.dist_water_m,
                    "twi": cell.twi,
                    "drainage_capacity": cell.drainage_capacity,
                    "is_riverside": 1 if cell.zone_type == "riverside" else 0,
                    "is_urban_basin": 1 if cell.zone_type == "urban_basin" else 0,
                    "tide_level_m": h_now["tide_level_m"],
                    "tide_delta_m": h_now["tide_delta_m"],
                    "hour_sin": round(math.sin(2.0 * math.pi * h_now["hour"] / 24.0), 4),
                    "hour_cos": round(math.cos(2.0 * math.pi * h_now["hour"] / 24.0), 4),
                    "rain_mm_h": h_now["rain_mm_h"],
                    "rain_3h_cum": h_now["rain_3h_cum"],
                    "temporal_lag_1h_cm": round(float(depth_matrix[t - 1, c_idx]), 2),
                    "spatial_neighbor_lag_1h_cm": round(neighbor_lag, 2),
                    # Astronomical tide & NWP rain forecast inputs for +1h, +3h, +6h
                    "tide_t1_m": h_1h["tide_level_m"],
                    "tide_t3_m": h_3h["tide_level_m"],
                    "tide_t6_m": h_6h["tide_level_m"],
                    "rain_nwp_1h_mm": rain_nwp_1h,
                    "rain_nwp_3h_mm": rain_nwp_3h,
                    "rain_nwp_6h_mm": rain_nwp_6h,
                    "target_depth_now_cm": round(float(depth_matrix[t, c_idx]), 2),
                    "target_depth_1h_cm": round(float(depth_matrix[t + 1, c_idx]), 2),
                    "target_depth_3h_cm": round(float(depth_matrix[t + 3, c_idx]), 2),
                    "target_depth_6h_cm": round(float(depth_matrix[t + 6, c_idx]), 2),
                }
            )

    gold_df = pd.DataFrame(rows)
    gold_df.to_csv(gold_path, index=False)
    return gold_df


# ==============================================================================
# 24/7 REAL-TIME WEATHER, AIR QUALITY, LUNAR/TIDAL & SMART ALERTS ENGINE
# ==============================================================================

_LIVE_HUB_CACHE: Dict[str, Any] = {
    "fetched_epoch": 0.0,
    "raw_bundle": None,
}

_GLOFAS_CACHE: Dict[str, Any] = {
    "fetched_epoch": 0.0,
    "data": None,
}


def fetch_glofas_hau_river_discharge(timeout_sec: float = 2.5) -> Dict[str, Any]:
    """Fetch real-time Hau River discharge (m^3/s) from Open-Meteo GloFAS Flood API with 6h cache."""
    now_epoch = time.time()
    if _GLOFAS_CACHE["data"] is not None and (now_epoch - float(_GLOFAS_CACHE["fetched_epoch"])) < 21600.0:
        return _GLOFAS_CACHE["data"]

    cache_path = RUNTIME_CACHE_DIR / "bronze_glofas_hau_river.json"
    if cache_path.exists():
        try:
            age_sec = now_epoch - cache_path.stat().st_mtime
            if age_sec < 21600.0:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached["discharge_source"] = "glofas-cached"
                _GLOFAS_CACHE["fetched_epoch"] = now_epoch
                _GLOFAS_CACHE["data"] = cached
                return cached
        except Exception:
            pass

    url = (
        f"https://flood-api.open-meteo.com/v1/flood"
        f"?latitude={CANTHO_LAT}&longitude={CANTHO_LON}"
        f"&daily=river_discharge,river_discharge_mean,river_discharge_max"
        f"&past_days=1&forecast_days=7"
    )
    now_vn = datetime.now(VN_TZ)
    today_iso = now_vn.date().isoformat()

    try:
        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
        payload = resp.json()
        daily = payload.get("daily", {})
        dates: List[str] = daily.get("time", [])
        discharges = daily.get("river_discharge", [])
        discharges_max = daily.get("river_discharge_max", [])

        idx = dates.index(today_iso) if today_iso in dates else min(1, max(0, len(discharges) - 1))
        today_q = float(discharges[idx]) if idx < len(discharges) and discharges[idx] is not None else 7500.0
        valid_max = [float(v) for v in discharges_max if v is not None]
        max_7d = max(valid_max) if valid_max else today_q

        result = {
            "river_discharge_m3s": round(today_q, 1),
            "river_discharge_max_7d_m3s": round(max_7d, 1),
            "discharge_source": "open-meteo-glofas",
            "fetched_at": now_vn.isoformat(timespec="seconds"),
        }
        _GLOFAS_CACHE["fetched_epoch"] = now_epoch
        _GLOFAS_CACHE["data"] = result
        _safe_write_json(cache_path, result)
        return result
    except Exception:
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached["discharge_source"] = "glofas-cached"
                _GLOFAS_CACHE["fetched_epoch"] = now_epoch
                _GLOFAS_CACHE["data"] = cached
                return cached
            except Exception:
                pass

    # Seasonal Mekong Basin discharge fallback (wet flood peak Sep-Nov vs dry Apr-May)
    month = now_vn.month
    seasonal_q = {
        1: 4200.0, 2: 3100.0, 3: 2500.0, 4: 2350.0, 5: 2950.0, 6: 5400.0,
        7: 8800.0, 8: 12100.0, 9: 15200.0, 10: 15800.0, 11: 8200.0, 12: 5600.0,
    }.get(month, 6500.0)
    fallback = {
        "river_discharge_m3s": seasonal_q,
        "river_discharge_max_7d_m3s": round(seasonal_q * 1.12, 1),
        "discharge_source": "mekong-seasonal-estimate",
        "fetched_at": now_vn.isoformat(timespec="seconds"),
    }
    _GLOFAS_CACHE["fetched_epoch"] = now_epoch
    _GLOFAS_CACHE["data"] = fallback
    return fallback

WEEKDAY_VN = [
    "Thứ Hai",
    "Thứ Ba",
    "Thứ Tư",
    "Thứ Năm",
    "Thứ Sáu",
    "Thứ Bảy",
    "Chủ Nhật",
]


def decode_wmo_weather_code(code: int, is_day: int = 1) -> Dict[str, str]:
    """Decode standard WMO weather code from Open-Meteo into Vietnamese label, category, and SVG icon key.

    Source: https://open-meteo.com/en/docs (WMO Weather interpretation codes WW)
    """
    c = int(code or 0)
    day_flag = bool(is_day)
    if c == 0:
        return {
            "code": c,
            "label": "Trời quang, nắng ấm" if day_flag else "Trời quang, đêm mát",
            "category": "clear",
            "icon_key": "sun" if day_flag else "moon",
            "severity": "normal",
        }
    if c in (1, 2):
        return {
            "code": c,
            "label": "Ít mây, nắng dịu" if day_flag else "Ít mây, gió mát",
            "category": "partly_cloudy",
            "icon_key": "cloud-sun" if day_flag else "cloud-moon",
            "severity": "normal",
        }
    if c == 3:
        return {
            "code": c,
            "label": "Nhiều mây âm u",
            "category": "cloudy",
            "icon_key": "cloud",
            "severity": "normal",
        }
    if c in (45, 48):
        return {
            "code": c,
            "label": "Sương mù nhẹ ven sông",
            "category": "cloudy",
            "icon_key": "cloud",
            "severity": "caution",
        }
    if c in (51, 53, 55, 56, 57):
        return {
            "code": c,
            "label": "Mưa phùn / Mưa nhỏ rải rác",
            "category": "rain",
            "icon_key": "rain-light",
            "severity": "caution",
        }
    if c in (61, 63, 80, 81):
        return {
            "code": c,
            "label": "Mưa rào nhiệt đới",
            "category": "rain",
            "icon_key": "rain-light" if c in (61, 80) else "rain-heavy",
            "severity": "warning",
        }
    if c in (65, 66, 67, 82):
        return {
            "code": c,
            "label": "Mưa to diện rộng",
            "category": "rain",
            "icon_key": "rain-heavy",
            "severity": "danger",
        }
    if c >= 95:
        return {
            "code": c,
            "label": "Mưa dông nhiệt đới & Sấm sét",
            "category": "storm",
            "icon_key": "storm",
            "severity": "danger",
        }
    return {
        "code": c,
        "label": "Mây thay đổi",
        "category": "partly_cloudy",
        "icon_key": "cloud-sun" if day_flag else "cloud-moon",
        "severity": "normal",
    }


def wind_deg_to_vietnamese(deg: float) -> Tuple[str, str]:
    """Convert meteorological wind direction (0-360 deg) into Vietnamese compass code and label."""
    directions = [
        ("B", "Gió Bắc"),
        ("ĐB", "Gió Đông Bắc"),
        ("Đ", "Gió Đông"),
        ("ĐN", "Gió Đông Nam"),
        ("N", "Gió Nam"),
        ("TN", "Gió Tây Nam"),
        ("T", "Gió Tây"),
        ("TB", "Gió Tây Bắc"),
    ]
    idx = int(round((float(deg) % 360.0) / 45.0)) % 8
    return directions[idx]


def evaluate_uv_index(uv: float) -> Dict[str, Any]:
    """Evaluate UV Index according to WHO scale and provide Mekong Delta skin protection guidance."""
    val = round(max(0.0, float(uv)), 1)
    if val < 3.0:
        return {
            "uv_index": val,
            "level": "Thấp",
            "badge_class": "badge-safe",
            "color": "#10b981",
            "recommendation": "An toàn khi di chuyển ngoài trời tại Cần Thơ; không cần biện pháp chống nắng đặc biệt.",
        }
    if val < 6.0:
        return {
            "uv_index": val,
            "level": "Trung bình",
            "badge_class": "badge-warn",
            "color": "#facc15",
            "recommendation": "Nên đội nón, đeo kính râm và thoa kem chống nắng SPF 30+ khi đi ngoài trời nắng trưa.",
        }
    if val < 8.0:
        return {
            "uv_index": val,
            "level": "Cao",
            "badge_class": "badge-warn",
            "color": "#f97316",
            "recommendation": "Tia UV cao buổi trưa miền Tây: mặc áo khoác chống nắng, kính UV400 và tìm bóng râm từ 11:00–14:00.",
        }
    if val < 11.0:
        return {
            "uv_index": val,
            "level": "Rất cao",
            "badge_class": "badge-danger",
            "color": "#ef4444",
            "recommendation": "Cảnh báo tổn thương da nhanh! Bắt buộc che chắn kỹ, dùng kem SPF 50+ và hạn chế ra đường từ 11:00–14:30.",
        }
    return {
        "uv_index": val,
        "level": "Cực kỳ nguy hiểm",
        "badge_class": "badge-danger",
        "color": "#a855f7",
        "recommendation": "Tia cực tím ở ngưỡng cực đại (≥11)! Tránh tiếp xúc trực tiếp ánh nắng mặt trời buổi trưa.",
    }


def evaluate_air_quality(us_aqi: float, pm2_5: float, pm10: float) -> Dict[str, Any]:
    """Evaluate US EPA AQI and PM2.5/PM10 concentration for Can Tho urban health guidance."""
    aqi_int = int(round(max(1.0, float(us_aqi))))
    pm25_val = round(max(0.0, float(pm2_5)), 1)
    pm10_val = round(max(0.0, float(pm10)), 1)

    if aqi_int <= 50:
        return {
            "us_aqi": aqi_int,
            "pm2_5": pm25_val,
            "pm10": pm10_val,
            "level": "Tốt (Trong lành)",
            "badge_class": "badge-safe",
            "color": "#10b981",
            "recommendation": "Không khí sông nước Cần Thơ rất trong lành, lý tưởng để chạy bộ ở Bến Ninh Kiều hoặc Công viên Lưu Hữu Phước.",
        }
    if aqi_int <= 100:
        return {
            "us_aqi": aqi_int,
            "pm2_5": pm25_val,
            "pm10": pm10_val,
            "level": "Trung bình",
            "badge_class": "badge-warn",
            "color": "#facc15",
            "recommendation": "Chất lượng không khí chấp nhận được. Người nhạy cảm đường hô hấp nên đeo khẩu trang khi qua các nút giao đông xe giờ cao điểm.",
        }
    if aqi_int <= 150:
        return {
            "us_aqi": aqi_int,
            "pm2_5": pm25_val,
            "pm10": pm10_val,
            "level": "Kém cho nhóm nhạy cảm",
            "badge_class": "badge-warn",
            "color": "#f97316",
            "recommendation": "Mật độ bụi mịn PM2.5 tăng tại các trục giao thông chính; người già và trẻ nhỏ nên hạn chế vận động mạnh ngoài trời.",
        }
    return {
        "us_aqi": aqi_int,
        "pm2_5": pm25_val,
        "pm10": pm10_val,
        "level": "Xấu (Ô nhiễm)",
        "badge_class": "badge-danger",
        "color": "#ef4444",
        "recommendation": "Khuyến cáo đeo khẩu trang chống bụi mịn PM2.5 (N95/KF94) khi lưu thông ngoài đường.",
    }


def compute_astronomy_and_tide_schedule(
    dt: datetime | None = None,
    sunrise_str: str = "05:54",
    sunset_str: str = "17:36",
) -> Dict[str, Any]:
    """Compute Lunar phase (Âm lịch), Sunrise/Sunset, and 24h Hau River tide schedule for Can Tho."""
    now_vn = dt or datetime.now(VN_TZ)
    # Reference new moon: 2025-01-29 12:36 UTC (Tet At Ty Mùng 1)
    ref_new_moon = datetime(2025, 1, 29, 19, 36, tzinfo=VN_TZ)
    if now_vn.tzinfo is None:
        now_vn = now_vn.replace(tzinfo=VN_TZ)

    synodic_month = 29.530588853
    days_since = (now_vn - ref_new_moon).total_seconds() / 86400.0
    lunar_age = days_since % synodic_month
    lunar_day = int(math.floor(lunar_age)) + 1
    lunar_day = max(1, min(30, lunar_day))

    # Illumination percentage
    illum_pct = int(round(50.0 * (1.0 - math.cos(2.0 * math.pi * lunar_age / synodic_month))))

    # Spring tide occurs around New Moon (days 29, 30, 1, 2, 3, 4) and Full Moon (days 13, 14, 15, 16, 17, 18)
    is_spring_window = (lunar_day in (29, 30, 1, 2, 3, 4)) or (13 <= lunar_day <= 18)

    if lunar_day in (30, 1, 2):
        phase_name = "Trăng non (Đầu tháng - Kỳ nước rong)"
        phase_icon = "🌑"
    elif 3 <= lunar_day <= 6:
        phase_name = "Trăng lưỡi liềm đầu tháng"
        phase_icon = "🌒"
    elif 7 <= lunar_day <= 9:
        phase_name = "Trăng thượng huyền (Nước kém)"
        phase_icon = "🌓"
    elif 10 <= lunar_day <= 13:
        phase_name = "Trăng khuyết tăng (Nước dâng)"
        phase_icon = "🌔"
    elif 14 <= lunar_day <= 17:
        phase_name = "Trăng tròn (Rằm - Đỉnh triều cường)"
        phase_icon = "🌕"
    elif 18 <= lunar_day <= 21:
        phase_name = "Trăng khuyết giảm"
        phase_icon = "🌖"
    elif 22 <= lunar_day <= 24:
        phase_name = "Trăng hạ huyền (Nước kém)"
        phase_icon = "🌗"
    else:
        phase_name = "Trăng tàn cuối tháng"
        phase_icon = "🌘"

    # Physics-informed Mekong Hydrology: GloFAS river discharge stage + 4 tidal constituents (M2, S2, K1, O1)
    glofas = fetch_glofas_hau_river_discharge()
    river_q = float(glofas.get("river_discharge_m3s", 7500.0))
    river_q_max = float(glofas.get("river_discharge_max_7d_m3s", river_q))
    discharge_stage_offset = max(-0.14, min(0.26, (river_q - 6800.0) / 34000.0))

    # Spring-neap astronomical modulation via M2 (12.4206h) & S2 (12.0000h) + diurnal K1 (23.9345h) & O1 (25.8193h)
    spring_neap_factor = 0.5 + 0.5 * math.cos(4.0 * math.pi * lunar_age / synodic_month)  # ~1 at New/Full moon
    base_stage = 1.24 + 0.16 * spring_neap_factor + discharge_stage_offset

    # Epoch aligned so semi-diurnal highs occur near ~05:30 & ~17:45 local Can Tho phase
    day_hours = (now_vn.replace(hour=0, minute=0, second=0, microsecond=0) - ref_new_moon).total_seconds() / 3600.0
    m2_amp = 0.48 + 0.14 * spring_neap_factor
    s2_amp = 0.16
    k1_amp = 0.12
    o1_amp = 0.07

    w_m2 = 2.0 * math.pi / 12.4206
    w_s2 = 2.0 * math.pi / 12.0000
    w_k1 = 2.0 * math.pi / 23.9345
    w_o1 = 2.0 * math.pi / 25.8193

    def _harmonic_tide(h_local: float) -> Tuple[float, float]:
        # Phase anchor calibrated for Hau River Can Tho station (~05:30 & ~17:30 semi-diurnal highs)
        ph_m2 = w_m2 * (h_local - 5.5) + 0.12 * math.sin(2.0 * math.pi * lunar_age / synodic_month)
        ph_s2 = w_s2 * (h_local - 5.5) + (w_s2 - w_m2) * day_hours * 0.0
        # Modulate S2 relative phase directly by synodic cycle so M2+S2 reinforces at New/Full Moon
        syn_phase = 4.0 * math.pi * lunar_age / synodic_month
        ph_k1 = w_k1 * (h_local - 17.5)
        ph_o1 = w_o1 * (h_local - 16.5) + 0.5 * syn_phase

        lvl = (
            base_stage
            + m2_amp * math.cos(ph_m2)
            + s2_amp * math.cos(ph_s2 + syn_phase * 0.5)
            + k1_amp * math.cos(ph_k1)
            + o1_amp * math.cos(ph_o1)
        )
        d_lvl = (
            -m2_amp * w_m2 * math.sin(ph_m2)
            - s2_amp * w_s2 * math.sin(ph_s2 + syn_phase * 0.5)
            - k1_amp * w_k1 * math.sin(ph_k1)
            - o1_amp * w_o1 * math.sin(ph_o1)
        )
        return round(max(0.45, min(2.45, lvl)), 3), round(d_lvl, 3)

    hourly_tide: List[Dict[str, Any]] = []
    for h in range(24):
        lvl, delta = _harmonic_tide(float(h))
        hourly_tide.append(
            {
                "hour": h,
                "time_label": f"{h:02d}:00",
                "level_m": lvl,
                "delta_m_h": delta,
                "alarm_status": (
                    "Trên BĐ III"
                    if lvl >= 2.00
                    else ("Báo động II" if lvl >= 1.90 else ("Báo động I" if lvl >= 1.80 else "An toàn"))
                ),
            }
        )

    current_hour = now_vn.hour
    current_tide = hourly_tide[current_hour]

    # Identify morning & evening high/low tides
    morning_slice = hourly_tide[2:10]
    noon_slice = hourly_tide[9:15]
    evening_slice = hourly_tide[14:22]
    night_slice = hourly_tide[20:24] + hourly_tide[0:3]

    high_morning = max(morning_slice, key=lambda x: x["level_m"])
    low_noon = min(noon_slice, key=lambda x: x["level_m"])
    high_evening = max(evening_slice, key=lambda x: x["level_m"])
    low_night = min(night_slice, key=lambda x: x["level_m"])

    extremes = [
        {
            "type": "Nước lớn (Đỉnh sáng)",
            "kind": "high",
            "time": f"{high_morning['hour']:02d}:30",
            "level_m": high_morning["level_m"],
            "alarm": high_morning["alarm_status"],
        },
        {
            "type": "Nước ròng (Chân trưa)",
            "kind": "low",
            "time": f"{low_noon['hour']:02d}:15",
            "level_m": low_noon["level_m"],
            "alarm": "An toàn",
        },
        {
            "type": "Nước lớn (Đỉnh chiều)",
            "kind": "high",
            "time": f"{high_evening['hour']:02d}:30",
            "level_m": high_evening["level_m"],
            "alarm": high_evening["alarm_status"],
        },
        {
            "type": "Nước ròng (Chân đêm)",
            "kind": "low",
            "time": f"{low_night['hour']:02d}:00",
            "level_m": low_night["level_m"],
            "alarm": "An toàn",
        },
    ]

    max_daily_tide = max(high_morning["level_m"], high_evening["level_m"])
    if max_daily_tide >= 2.00:
        tourism_note = (
            f"Triều cường Sông Hậu hôm nay đạt đỉnh {max_daily_tide:.2f}m (Vượt Báo động III) lúc {high_evening['hour']:02d}:30. "
            "Du khách đi Chợ nổi Cái Răng buổi sáng (06:00–08:00) lưu ý sóng lớn; khu vực Bến Ninh Kiều & Huỳnh Cương có thể ngập nhẹ chiều tối."
        )
    elif max_daily_tide >= 1.80:
        tourism_note = (
            f"Mực nước đỉnh triều đạt {max_daily_tide:.2f}m (Báo động I–II). "
            "Tàu du lịch Bến Ninh Kiều & thuyền đi Chợ nổi Cái Răng hoạt động thuận lợi; chú ý nước dâng ven bờ kè lúc chiều tối."
        )
    else:
        tourism_note = (
            f"Thủy triều Sông Hậu êm ả (đỉnh {max_daily_tide:.2f}m < Báo động I). "
            "Thời điểm lý tưởng tham quan Chợ nổi Cái Răng (05:30–08:30) và dạo bộ Bến Ninh Kiều buổi tối."
        )

    return {
        "sunrise": sunrise_str,
        "sunset": sunset_str,
        "lunar_day_approx": lunar_day,
        "lunar_label": f"Ngày {lunar_day:02d} Âm lịch",
        "moon_phase_name": phase_name,
        "moon_phase_icon": phase_icon,
        "moon_illumination_pct": illum_pct,
        "is_spring_tide_window": is_spring_window,
        "tidal_regime": "Kỳ triều cường (Nước rong)" if is_spring_window else "Kỳ triều kém (Nước bình thường)",
        "current_tide_m": current_tide["level_m"],
        "current_tide_delta_m_h": current_tide["delta_m_h"],
        "current_alarm_status": current_tide["alarm_status"],
        "max_daily_tide_m": max_daily_tide,
        "river_discharge_m3s": river_q,
        "river_discharge_max_7d_m3s": river_q_max,
        "discharge_source": glofas.get("discharge_source", "open-meteo-glofas"),
        "harmonic_model": "GloFAS + M2/S2/K1/O1",
        "alarm_thresholds": {"alarm_i": 1.80, "alarm_ii": 1.90, "alarm_iii": 2.00},
        "extremes": extremes,
        "hourly_24h": hourly_tide,
        "tourism_and_navigation_note": tourism_note,
    }


def generate_smart_contextual_alerts(
    current: Dict[str, Any],
    hourly_24h: List[Dict[str, Any]],
    tide_schedule: Dict[str, Any],
    districts_live: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Analyze the next 3-6 hours of meteorology, UV, AQI, and Hau River tide to issue Smart Contextual Alerts."""
    alerts: List[Dict[str, Any]] = []
    next_6h = hourly_24h[:7] if hourly_24h else []

    # 1. Rain / Thunderstorm detection in next 3-6h
    rain_hours = [
        h
        for h in next_6h
        if float(h.get("precip_mm", 0.0)) >= 1.2
        or int(h.get("precip_prob_pct", 0)) >= 60
        or int(h.get("weather_code", 0)) >= 61
    ]
    district_name = str(current.get("name", "TP. Cần Thơ"))
    if rain_hours:
        peak_rain_h = max(rain_hours, key=lambda x: (float(x.get("precip_mm", 0.0)), int(x.get("precip_prob_pct", 0))))
        prob = int(peak_rain_h.get("precip_prob_pct", 70))
        mm = float(peak_rain_h.get("precip_mm", 5.0))
        time_lbl = peak_rain_h.get("hour_label", "16:00")
        is_storm = int(peak_rain_h.get("weather_code", 0)) >= 95 or mm >= 15.0
        alerts.append(
            {
                "id": "alert-rain-6h",
                "type": "rain_storm",
                "severity": "danger" if is_storm else "warning",
                "icon": "⛈️" if is_storm else "🌧️",
                "title": (
                    f"⚠️ Cảnh báo mưa dông tại {district_name} lúc {time_lbl}"
                    if is_storm
                    else f"⚠️ Dự báo mưa rào tại {district_name} khoảng {time_lbl} (Xác suất {prob}%)"
                ),
                "detail": (
                    f"Lượng mưa dự kiến ~{mm:.1f} mm/h (xác suất {prob}%). "
                    "Người dân nên mang theo áo mưa và kiểm tra lộ trình né ngập nếu di chuyển qua Mậu Thân, 30/4 hoặc Hồ Bún Xáng."
                ),
                "time_window": f"Trong 3–6 giờ tới ({time_lbl})",
                "action_mode": "flood",
            }
        )
    else:
        alerts.append(
            {
                "id": "alert-weather-clear",
                "type": "weather_status",
                "severity": "info",
                "icon": "☀️",
                "title": f"Thời tiết 6 giờ tới tại {district_name} tạnh ráo, lưu thông thuận lợi",
                "detail": (
                    f"Nhiệt độ hiện tại {current.get('temp_c', 31.0):.1f}°C (Cảm nhận RealFeel {current.get('feels_like_c', 34.5):.1f}°C). "
                    "Xác suất mưa thấp trong 6 giờ tới tại Ninh Kiều, Cái Răng và Bình Thủy."
                ),
                "time_window": "6 giờ tới",
                "action_mode": "weather",
            }
        )

    # 2. UV Index Alert (current or midday peak in next 12h)
    uv_candidates = [float(current.get("uv_index", 0.0))] + [
        float(h.get("uv_index", 0.0)) for h in hourly_24h[:12]
    ]
    max_uv = round(max(uv_candidates) if uv_candidates else 0.0, 1)
    if max_uv >= 6.0:
        uv_eval = evaluate_uv_index(max_uv)
        alerts.append(
            {
                "id": "alert-uv-high",
                "type": "uv_health",
                "severity": "danger" if max_uv >= 8.0 else "warning",
                "icon": "☀️",
                "title": f"☀️ Cảnh báo tia UV {uv_eval['level'].lower()} (UV = {max_uv:.0f}) từ 11:00–14:00",
                "detail": uv_eval["recommendation"],
                "time_window": "11:00 – 14:30",
                "action_mode": "weather",
            }
        )

    # 3. Hau River Tidal Peak Alert
    max_tide = float(tide_schedule.get("max_daily_tide_m", 1.85))
    extremes = tide_schedule.get("extremes", [])
    high_peaks = [e for e in extremes if e.get("kind") == "high"]
    evening_peak = high_peaks[-1] if high_peaks else {"time": "17:30", "level_m": max_tide}

    if max_tide >= 1.80:
        alarm_lbl = "vượt Báo động III (2.00m)" if max_tide >= 2.00 else "chạm ngưỡng Báo động I–II"
        alerts.append(
            {
                "id": "alert-tide-peak",
                "type": "hydrology_tide",
                "severity": "danger" if max_tide >= 2.00 else "warning",
                "icon": "🌊",
                "title": f"🌊 Lưu ý triều cường Sông Hậu đạt đỉnh lúc {evening_peak['time']} ({evening_peak['level_m']:.2f}m)",
                "detail": (
                    f"Đỉnh triều hôm nay {alarm_lbl}. Khuyến nghị bật 'Chế độ Triều cường & Né ngập A*' "
                    "khi di chuyển bằng xe tay ga qua các tuyến ven sông và rốn trũng nội ô."
                ),
                "time_window": f"Đỉnh triều {evening_peak['time']}",
                "action_mode": "flood",
            }
        )
    else:
        alerts.append(
            {
                "id": "alert-tide-normal",
                "type": "hydrology_tide",
                "severity": "info",
                "icon": "🌊",
                "title": f"🌊 Thủy triều Sông Hậu ở ngưỡng an toàn (Đỉnh {evening_peak['level_m']:.2f}m lúc {evening_peak['time']})",
                "detail": tide_schedule.get("tourism_and_navigation_note", ""),
                "time_window": "Trong ngày",
                "action_mode": "weather",
            }
        )

    # 4. Heat Index / AQI contextual notice
    feels_like = float(current.get("feels_like_c", 32.0))
    us_aqi = int(current.get("us_aqi", 55))
    if feels_like >= 36.0 or us_aqi > 80:
        alerts.append(
            {
                "id": "alert-urban-comfort",
                "type": "env_comfort",
                "severity": "warning",
                "icon": "🌡️",
                "title": f"🌡️ Nhiệt độ cảm nhận oi bức ({feels_like:.1f}°C) • AQI = {us_aqi}",
                "detail": (
                    "Độ ẩm kết hợp bức xạ nhiệt làm cảm giác nóng hơn nhiệt độ thực tế. "
                    "Hãy bổ sung đủ nước và đeo khẩu trang khi di chuyển ngoài đường."
                ),
                "time_window": "Hiện tại",
                "action_mode": "weather",
            }
        )

    # Determine whether Auto-Flood Mode should recommend activation
    current_rain = float(current.get("precip_mm", 0.0))
    rain_3h_forecast = sum(float(h.get("precip_mm", 0.0)) for h in next_6h[:3])
    current_tide = float(tide_schedule.get("current_tide_m", 1.5))

    should_activate_flood = (
        current_rain >= 15.0
        or rain_3h_forecast >= 20.0
        or current_tide >= 1.80
        or max_tide >= 1.95
    )

    if current_rain >= 15.0 or current_tide >= 1.85:
        trigger_reason = (
            f"Phát hiện mực nước Sông Hậu cao ({current_tide:.2f}m) / mưa lớn ({current_rain:.1f} mm/h) vượt ngưỡng Báo động I."
        )
    elif max_tide >= 1.95:
        trigger_reason = (
            f"Dự báo đỉnh triều Sông Hậu trong ngày đạt {max_tide:.2f}m (tiệm cận/vượt Báo động III)."
        )
    else:
        trigger_reason = "Thời tiết & thủy văn hiện tại ổn định (Dưới Báo động I). Chế độ Né ngập ở trạng thái Chờ theo yêu cầu (On-Demand)."

    return {
        "alerts": alerts,
        "auto_flood_mode": {
            "should_activate": should_activate_flood,
            "is_currently_critical": bool(current_rain >= 15.0 or current_tide >= 1.80),
            "trigger_reason": trigger_reason,
            "recommended_inputs": {
                "tide_level_m": round(max(current_tide, max_tide if should_activate_flood else current_tide), 2),
                "rain_mm_h": round(max(current_rain, float(rain_hours[0]["precip_mm"]) if rain_hours else 0.0), 1),
                "rain_3h_cum": round(max(rain_3h_forecast, current_rain * 1.8), 1),
            },
        },
    }


def _build_fallback_raw_bundle(now_vn: datetime) -> Dict[str, Any]:
    """Construct a realistic Mekong Delta meteorology & air quality bundle when offline."""
    base_hour = now_vn.replace(minute=0, second=0, microsecond=0)
    hourly_times = [(base_hour + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(48)]

    temps, feels, hums, probs, precips, codes, pressures, vis, winds, wdirs, uvs = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    pm25s, pm10s, aqis = [], [], []

    for i in range(48):
        h = (base_hour.hour + i) % 24
        is_day_h = 6 <= h <= 18
        # Diurnal temperature wave: min ~25.0C at 5h, max ~33.2C at 14h
        t = round(28.8 + 4.2 * math.sin(2.0 * math.pi * (h - 8.5) / 24.0), 1)
        rh = int(round(max(55, min(94, 78 - 18 * math.sin(2.0 * math.pi * (h - 8.5) / 24.0)))))
        app_t = round(t + 3.6 + 0.04 * (rh - 65), 1)

        # Afternoon convective shower window 15h-18h
        if 15 <= h <= 18:
            prob = 68 if h in (16, 17) else 45
            pr = 4.2 if h == 16 else (2.4 if h == 17 else 0.4)
            wcode = 80 if pr >= 2.0 else 2
        else:
            prob = 15 if is_day_h else 10
            pr = 0.0
            wcode = 1 if is_day_h else 2

        uv = round(max(0.0, 9.2 * math.sin(math.pi * (h - 6) / 12.0)), 1) if 6 <= h <= 18 else 0.0
        temps.append(t)
        feels.append(app_t)
        hums.append(rh)
        probs.append(prob)
        precips.append(pr)
        codes.append(wcode)
        pressures.append(round(1009.2 + 1.4 * math.cos(2.0 * math.pi * (h - 10) / 24.0), 1))
        vis.append(8500.0 if pr > 1.0 else 10000.0)
        winds.append(round(9.5 + 4.5 * (1.0 if is_day_h else 0.4), 1))
        wdirs.append(115)
        uvs.append(uv)
        pm25s.append(18.5)
        pm10s.append(24.0)
        aqis.append(64)

    daily_dates = [(base_hour.date() + timedelta(days=d)).isoformat() for d in range(7)]
    return {
        "source": "cantho-Fallback-Resilient",
        "fetched_at": now_vn.isoformat(timespec="seconds"),
        "forecast": {
            "current": {
                "time": now_vn.strftime("%Y-%m-%dT%H:%M"),
                "temperature_2m": temps[0],
                "relative_humidity_2m": hums[0],
                "apparent_temperature": feels[0],
                "is_day": 1 if 6 <= now_vn.hour <= 18 else 0,
                "precipitation": precips[0],
                "weather_code": codes[0],
                "cloud_cover": 58,
                "surface_pressure": pressures[0],
                "wind_speed_10m": winds[0],
                "wind_direction_10m": wdirs[0],
                "wind_gusts_10m": round(winds[0] * 1.8, 1),
            },
            "hourly": {
                "time": hourly_times,
                "temperature_2m": temps,
                "apparent_temperature": feels,
                "relative_humidity_2m": hums,
                "precipitation_probability": probs,
                "precipitation": precips,
                "weather_code": codes,
                "surface_pressure": pressures,
                "visibility": vis,
                "wind_speed_10m": winds,
                "wind_direction_10m": wdirs,
                "uv_index": uvs,
            },
            "daily": {
                "time": daily_dates,
                "weather_code": [2, 80, 1, 2, 61, 2, 1],
                "temperature_2m_max": [33.2, 32.8, 33.6, 33.1, 31.9, 32.7, 33.4],
                "temperature_2m_min": [25.1, 25.0, 25.3, 25.2, 24.8, 25.0, 25.1],
                "sunrise": [f"{d}T05:54" for d in daily_dates],
                "sunset": [f"{d}T17:36" for d in daily_dates],
                "uv_index_max": [9.1, 8.6, 9.5, 9.0, 7.8, 8.8, 9.2],
                "precipitation_sum": [1.2, 6.8, 0.5, 2.1, 12.4, 1.8, 0.4],
                "precipitation_probability_max": [35, 70, 20, 40, 80, 35, 20],
                "wind_speed_10m_max": [15.2, 18.4, 14.0, 15.8, 21.0, 16.2, 14.5],
            },
        },
        "aqi": {
            "current": {
                "us_aqi": 64,
                "pm10": 23.4,
                "pm2_5": 18.2,
                "carbon_monoxide": 240.0,
                "nitrogen_dioxide": 4.8,
                "ozone": 112.0,
                "uv_index": uvs[0],
            },
            "hourly": {
                "time": hourly_times,
                "pm2_5": pm25s,
                "pm10": pm10s,
                "us_aqi": aqis,
                "uv_index": uvs,
            },
        },
        "districts_current": None,
        "districts_aqi": None,
    }


def _fetch_raw_openmeteo_bundle(force_refresh: bool = False, timeout_sec: float = 3.8) -> Dict[str, Any]:
    """Fetch or return cached Open-Meteo Forecast + Air Quality bundle for Can Tho."""
    now_epoch = time.time()
    runtime_cache_path = RUNTIME_CACHE_DIR / "bronze_realtime_hub_cantho.json"
    bundled_cache_path = DATA_DIR / "bronze_realtime_hub_cantho.json"

    if (
        not force_refresh
        and _LIVE_HUB_CACHE["raw_bundle"] is not None
        and (now_epoch - float(_LIVE_HUB_CACHE["fetched_epoch"])) < 180.0
    ):
        return _LIVE_HUB_CACHE["raw_bundle"]

    now_vn = datetime.now(VN_TZ)

    lats_csv = ",".join(str(d.lat) for d in CANTHO_DISTRICTS)
    lons_csv = ",".join(str(d.lon) for d in CANTHO_DISTRICTS)

    forecast_url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={CANTHO_LAT}&longitude={CANTHO_LON}"
        f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        f"&hourly=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation_probability,precipitation,weather_code,surface_pressure,visibility,wind_speed_10m,wind_direction_10m,uv_index"
        f"&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max,precipitation_sum,precipitation_probability_max,wind_speed_10m_max"
        f"&forecast_days=7&timezone=Asia%2FHo_Chi_Minh"
    )
    aqi_url = (
        f"https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={CANTHO_LAT}&longitude={CANTHO_LON}"
        f"&current=us_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,ozone,uv_index"
        f"&hourly=pm2_5,pm10,us_aqi,uv_index"
        f"&forecast_days=3&timezone=Asia%2FHo_Chi_Minh"
    )
    batch_districts_url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats_csv}&longitude={lons_csv}"
        f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_direction_10m"
        f"&timezone=Asia%2FHo_Chi_Minh"
    )

    try:
        r_fc = requests.get(forecast_url, timeout=timeout_sec)
        r_fc.raise_for_status()
        fc_json = r_fc.json()

        try:
            r_aqi = requests.get(aqi_url, timeout=timeout_sec)
            r_aqi.raise_for_status()
            aqi_json = r_aqi.json()
        except Exception:
            aqi_json = _build_fallback_raw_bundle(now_vn)["aqi"]

        try:
            r_dist = requests.get(batch_districts_url, timeout=timeout_sec)
            r_dist.raise_for_status()
            dist_json = r_dist.json()
        except Exception:
            dist_json = None

        bundle = {
            "source": "open-meteo-live",
            "fetched_at": now_vn.isoformat(timespec="seconds"),
            "forecast": fc_json,
            "aqi": aqi_json,
            "districts_current": dist_json if isinstance(dist_json, list) else None,
        }
        _LIVE_HUB_CACHE["fetched_epoch"] = now_epoch
        _LIVE_HUB_CACHE["raw_bundle"] = bundle
        _safe_write_json(runtime_cache_path, bundle)
        return bundle
    except Exception:
        for candidate in (runtime_cache_path, bundled_cache_path):
            if candidate.exists():
                try:
                    cached = json.loads(candidate.read_text(encoding="utf-8"))
                    cached["source"] = "open-meteo-cached"
                    _LIVE_HUB_CACHE["fetched_epoch"] = now_epoch
                    _LIVE_HUB_CACHE["raw_bundle"] = cached
                    return cached
                except Exception:
                    pass

        fallback = _build_fallback_raw_bundle(now_vn)
        _LIVE_HUB_CACHE["fetched_epoch"] = now_epoch
        _LIVE_HUB_CACHE["raw_bundle"] = fallback
        return fallback


def fetch_cantho_realtime_hub(
    district_id: str = "ninh_kieu",
    force_refresh: bool = False,
    timeout_sec: float = 3.8,
) -> Dict[str, Any]:
    """Master 24/7 Real-Time Weather, Environment & Urban Intelligence Hub for Can Tho City."""
    now_vn = datetime.now(VN_TZ)
    bundle = _fetch_raw_openmeteo_bundle(force_refresh=force_refresh, timeout_sec=timeout_sec)

    fc = bundle.get("forecast", {})
    aqi_data = bundle.get("aqi", {})
    curr_fc = fc.get("current", {})
    curr_aqi = aqi_data.get("current", {})
    hourly = fc.get("hourly", {})
    daily = fc.get("daily", {})

    selected_profile: DistrictProfile = DISTRICTS_BY_ID.get(district_id, DISTRICTS_BY_ID["ninh_kieu"])

    # Base city-center observations
    base_temp = float(curr_fc.get("temperature_2m", 31.2))
    base_feels = float(curr_fc.get("apparent_temperature", 35.0))
    base_rh = int(curr_fc.get("relative_humidity_2m", 68))
    base_is_day = int(curr_fc.get("is_day", 1 if 6 <= now_vn.hour <= 18 else 0))
    base_precip = float(curr_fc.get("precipitation", 0.0))
    base_wcode = int(curr_fc.get("weather_code", 2))
    base_cloud = int(curr_fc.get("cloud_cover", 65))
    base_press = float(curr_fc.get("surface_pressure", 1009.0))
    base_wind = float(curr_fc.get("wind_speed_10m", 11.0))
    base_wdir = float(curr_fc.get("wind_direction_10m", 110.0))
    base_gust = float(curr_fc.get("wind_gusts_10m", round(base_wind * 1.6, 1)))

    base_us_aqi = float(curr_aqi.get("us_aqi", 68))
    base_pm25 = float(curr_aqi.get("pm2_5", 18.0))
    base_pm10 = float(curr_aqi.get("pm10", 22.0))
    base_co = float(curr_aqi.get("carbon_monoxide", 240.0))
    base_no2 = float(curr_aqi.get("nitrogen_dioxide", 4.5))
    base_o3 = float(curr_aqi.get("ozone", 120.0))
    base_uv = float(curr_aqi.get("uv_index") or 0.0)

    # Align hourly series starting from current Vietnam hour
    h_times: List[str] = hourly.get("time", [])
    current_hour_prefix = now_vn.strftime("%Y-%m-%dT%H:")
    start_idx = 0
    for idx, ts in enumerate(h_times):
        if ts.startswith(current_hour_prefix):
            start_idx = idx
            break

    # If current UV was not in curr_aqi or is 0 during daytime, check hourly UV at start_idx
    h_uvs = hourly.get("uv_index", [])
    if base_uv == 0.0 and start_idx < len(h_uvs) and h_uvs[start_idx] is not None:
        base_uv = float(h_uvs[start_idx])

    h_vis = hourly.get("visibility", [])
    current_vis_m = float(h_vis[start_idx]) if start_idx < len(h_vis) and h_vis[start_idx] is not None else 10000.0
    current_vis_km = round(current_vis_m / 1000.0, 1)

    # Build live weather snapshots for all 6 Can Tho districts
    batch_list = bundle.get("districts_current")
    districts_live: List[Dict[str, Any]] = []
    for idx, d_prof in enumerate(CANTHO_DISTRICTS):
        raw_d = (
            batch_list[idx].get("current", {})
            if isinstance(batch_list, list) and idx < len(batch_list) and isinstance(batch_list[idx], dict)
            else {}
        )
        # Blend real batch coordinate telemetry with local urban/eco micro-climate characteristics
        d_temp = round(float(raw_d.get("temperature_2m", base_temp)) + d_prof.temp_offset_c * 0.5, 1)
        d_feels = round(float(raw_d.get("apparent_temperature", base_feels)) + d_prof.temp_offset_c * 0.7, 1)
        d_rh = max(40, min(99, int(raw_d.get("relative_humidity_2m", base_rh)) + d_prof.humidity_offset_pct))
        d_precip = round(max(0.0, float(raw_d.get("precipitation", base_precip))), 1)
        d_wcode = int(raw_d.get("weather_code", base_wcode))
        d_wind = round(float(raw_d.get("wind_speed_10m", base_wind)), 1)
        d_wdir = float(raw_d.get("wind_direction_10m", base_wdir))
        d_press = round(float(raw_d.get("surface_pressure", base_press)), 1)
        d_cloud = int(raw_d.get("cloud_cover", base_cloud))

        w_meta = decode_wmo_weather_code(d_wcode, is_day=base_is_day)
        w_comp_code, w_comp_label = wind_deg_to_vietnamese(d_wdir)

        d_aqi_val = max(12, int(round(base_us_aqi + d_prof.aqi_offset)))
        d_pm25_val = round(max(3.0, base_pm25 + d_prof.aqi_offset * 0.25), 1)
        d_pm10_val = round(max(5.0, base_pm10 + d_prof.aqi_offset * 0.32), 1)
        aqi_eval = evaluate_air_quality(d_aqi_val, d_pm25_val, d_pm10_val)
        uv_eval = evaluate_uv_index(base_uv)

        districts_live.append(
            {
                **asdict(d_prof),
                "temp_c": d_temp,
                "feels_like_c": d_feels,
                "humidity_pct": d_rh,
                "precip_mm": d_precip,
                "weather_code": d_wcode,
                "weather_label": w_meta["label"],
                "weather_category": w_meta["category"],
                "icon_key": w_meta["icon_key"],
                "cloud_cover_pct": d_cloud,
                "pressure_hpa": d_press,
                "visibility_km": current_vis_km,
                "wind_speed_kmh": d_wind,
                "wind_gusts_kmh": round(max(d_wind * 1.4, base_gust), 1),
                "wind_dir_deg": round(d_wdir, 0),
                "wind_compass": w_comp_code,
                "wind_label": w_comp_label,
                "uv_index": uv_eval["uv_index"],
                "uv_level": uv_eval["level"],
                "us_aqi": aqi_eval["us_aqi"],
                "pm2_5": aqi_eval["pm2_5"],
                "pm10": aqi_eval["pm10"],
                "aqi_level": aqi_eval["level"],
                "aqi_color": aqi_eval["color"],
            }
        )

    selected_district_data = next(
        (d for d in districts_live if d["id"] == selected_profile.id),
        districts_live[0],
    )

    # Build 24-hour forecast slice adjusted for the selected district
    hourly_24h: List[Dict[str, Any]] = []
    h_temps = hourly.get("temperature_2m", [])
    h_feels = hourly.get("apparent_temperature", [])
    h_rhs = hourly.get("relative_humidity_2m", [])
    h_probs = hourly.get("precipitation_probability", [])
    h_precips = hourly.get("precipitation", [])
    h_codes = hourly.get("weather_code", [])
    h_press = hourly.get("surface_pressure", [])
    h_winds = hourly.get("wind_speed_10m", [])
    h_wdirs = hourly.get("wind_direction_10m", [])

    for offset in range(24):
        i = start_idx + offset
        if i >= len(h_times):
            break
        ts_str = h_times[i]
        try:
            dt_h = datetime.fromisoformat(ts_str)
            hr_int = dt_h.hour
            hr_lbl = f"{hr_int:02d}:00"
        except Exception:
            hr_int = (now_vn.hour + offset) % 24
            hr_lbl = f"{hr_int:02d}:00"

        is_day_h = 1 if 6 <= hr_int <= 18 else 0
        wcode_h = int(h_codes[i]) if i < len(h_codes) and h_codes[i] is not None else 2
        w_meta_h = decode_wmo_weather_code(wcode_h, is_day=is_day_h)
        wdir_h = float(h_wdirs[i]) if i < len(h_wdirs) and h_wdirs[i] is not None else 110.0
        w_comp_h, _ = wind_deg_to_vietnamese(wdir_h)

        if offset == 0:
            t_val = selected_district_data["temp_c"]
            f_val = selected_district_data["feels_like_c"]
            rh_val = selected_district_data["humidity_pct"]
            pr_val = selected_district_data["precip_mm"]
            wcode_h = selected_district_data["weather_code"]
            w_meta_h = {
                "label": selected_district_data["weather_label"],
                "category": selected_district_data["weather_category"],
                "icon_key": selected_district_data["icon_key"],
            }
            wind_val = selected_district_data["wind_speed_kmh"]
            w_comp_h = selected_district_data["wind_compass"]
            press_val = selected_district_data["pressure_hpa"]
            uv_val = selected_district_data["uv_index"]
            prob_val = int(h_probs[i]) if i < len(h_probs) and h_probs[i] is not None else (65 if pr_val > 0 else 15)
        else:
            t_val = round(
                (float(h_temps[i]) if i < len(h_temps) and h_temps[i] is not None else base_temp)
                + selected_profile.temp_offset_c * 0.5,
                1,
            )
            f_val = round(
                (float(h_feels[i]) if i < len(h_feels) and h_feels[i] is not None else base_feels)
                + selected_profile.temp_offset_c * 0.6,
                1,
            )
            rh_val = max(
                40,
                min(
                    99,
                    (int(h_rhs[i]) if i < len(h_rhs) and h_rhs[i] is not None else base_rh)
                    + selected_profile.humidity_offset_pct,
                ),
            )
            prob_val = int(h_probs[i]) if i < len(h_probs) and h_probs[i] is not None else 0
            pr_val = round(float(h_precips[i]) if i < len(h_precips) and h_precips[i] is not None else 0.0, 1)
            uv_val = round(float(h_uvs[i]) if i < len(h_uvs) and h_uvs[i] is not None else 0.0, 1)
            wind_val = round(float(h_winds[i]) if i < len(h_winds) and h_winds[i] is not None else base_wind, 1)
            press_val = round(float(h_press[i]) if i < len(h_press) and h_press[i] is not None else base_press, 1)

        hourly_24h.append(
            {
                "time": ts_str,
                "hour": hr_int,
                "hour_label": "Hiện tại" if offset == 0 else hr_lbl,
                "temp_c": t_val,
                "feels_like_c": f_val,
                "humidity_pct": rh_val,
                "precip_prob_pct": prob_val,
                "precip_mm": pr_val,
                "weather_code": wcode_h,
                "weather_label": w_meta_h["label"],
                "weather_category": w_meta_h["category"],
                "icon_key": w_meta_h["icon_key"],
                "uv_index": uv_val,
                "wind_speed_kmh": wind_val,
                "wind_compass": w_comp_h,
                "pressure_hpa": press_val,
            }
        )

    # Build 7-day forecast
    daily_7d: List[Dict[str, Any]] = []
    d_times = daily.get("time", [])
    d_codes = daily.get("weather_code", [])
    d_tmax = daily.get("temperature_2m_max", [])
    d_tmin = daily.get("temperature_2m_min", [])
    d_sunrises = daily.get("sunrise", [])
    d_sunsets = daily.get("sunset", [])
    d_uvmax = daily.get("uv_index_max", [])
    d_psum = daily.get("precipitation_sum", [])
    d_pprob = daily.get("precipitation_probability_max", [])
    d_wmax = daily.get("wind_speed_10m_max", [])

    for d_idx, d_str in enumerate(d_times[:7]):
        try:
            dt_d = datetime.fromisoformat(d_str)
            day_name = "Hôm nay" if d_idx == 0 else ("Ngày mai" if d_idx == 1 else WEEKDAY_VN[dt_d.weekday()])
            date_short = dt_d.strftime("%d/%m")
        except Exception:
            day_name = f"Ngày +{d_idx}"
            date_short = d_str[-5:]

        wc_d = int(d_codes[d_idx]) if d_idx < len(d_codes) and d_codes[d_idx] is not None else 2
        w_meta_d = decode_wmo_weather_code(wc_d, is_day=1)

        sr_raw = str(d_sunrises[d_idx]) if d_idx < len(d_sunrises) and d_sunrises[d_idx] else "05:54"
        ss_raw = str(d_sunsets[d_idx]) if d_idx < len(d_sunsets) and d_sunsets[d_idx] else "17:36"
        sr_time = sr_raw.split("T")[-1][:5] if "T" in sr_raw else sr_raw[:5]
        ss_time = ss_raw.split("T")[-1][:5] if "T" in ss_raw else ss_raw[:5]

        tmax_val = round(
            (float(d_tmax[d_idx]) if d_idx < len(d_tmax) and d_tmax[d_idx] is not None else 33.0)
            + selected_profile.temp_offset_c * 0.4,
            1,
        )
        tmin_val = round(
            (float(d_tmin[d_idx]) if d_idx < len(d_tmin) and d_tmin[d_idx] is not None else 25.0)
            + selected_profile.temp_offset_c * 0.3,
            1,
        )

        daily_7d.append(
            {
                "date": d_str,
                "date_short": date_short,
                "day_name": day_name,
                "weather_code": wc_d,
                "weather_label": w_meta_d["label"],
                "weather_category": w_meta_d["category"],
                "icon_key": w_meta_d["icon_key"],
                "temp_max_c": tmax_val,
                "temp_min_c": tmin_val,
                "precip_prob_max_pct": int(d_pprob[d_idx]) if d_idx < len(d_pprob) and d_pprob[d_idx] is not None else 25,
                "precip_sum_mm": round(float(d_psum[d_idx]) if d_idx < len(d_psum) and d_psum[d_idx] is not None else 0.0, 1),
                "uv_index_max": round(float(d_uvmax[d_idx]) if d_idx < len(d_uvmax) and d_uvmax[d_idx] is not None else 8.5, 1),
                "wind_speed_max_kmh": round(float(d_wmax[d_idx]) if d_idx < len(d_wmax) and d_wmax[d_idx] is not None else 15.0, 1),
                "sunrise": sr_time,
                "sunset": ss_time,
            }
        )

    today_sunrise = daily_7d[0]["sunrise"] if daily_7d else "05:54"
    today_sunset = daily_7d[0]["sunset"] if daily_7d else "17:36"

    astronomy_tide = compute_astronomy_and_tide_schedule(
        dt=now_vn,
        sunrise_str=today_sunrise,
        sunset_str=today_sunset,
    )

    uv_detail = evaluate_uv_index(selected_district_data["uv_index"])
    aqi_detail = evaluate_air_quality(
        selected_district_data["us_aqi"],
        selected_district_data["pm2_5"],
        selected_district_data["pm10"],
    )
    aqi_detail["carbon_monoxide_ugm3"] = round(base_co, 1)
    aqi_detail["nitrogen_dioxide_ugm3"] = round(base_no2, 1)
    aqi_detail["ozone_ugm3"] = round(base_o3, 1)

    smart_alerts_bundle = generate_smart_contextual_alerts(
        current=selected_district_data,
        hourly_24h=hourly_24h,
        tide_schedule=astronomy_tide,
        districts_live=districts_live,
    )

    return {
        "source": bundle.get("source", "open-meteo-live"),
        "fetched_at": bundle.get("fetched_at", now_vn.isoformat(timespec="seconds")),
        "timezone": "Asia/Ho_Chi_Minh",
        "vietnam_now_iso": now_vn.isoformat(timespec="seconds"),
        "selected_district_id": selected_district_data["id"],
        "current": {
            **selected_district_data,
            "temp_max_today_c": daily_7d[0]["temp_max_c"] if daily_7d else round(selected_district_data["temp_c"] + 2.0, 1),
            "temp_min_today_c": daily_7d[0]["temp_min_c"] if daily_7d else round(selected_district_data["temp_c"] - 5.0, 1),
            "is_day": base_is_day,
        },
        "environment": {
            "uv": uv_detail,
            "air_quality": aqi_detail,
        },
        "astronomy_and_tide": astronomy_tide,
        "hourly_24h": hourly_24h,
        "daily_7d": daily_7d,
        "districts": districts_live,
        "smart_alerts": smart_alerts_bundle["alerts"],
        "auto_flood_mode": smart_alerts_bundle["auto_flood_mode"],
    }

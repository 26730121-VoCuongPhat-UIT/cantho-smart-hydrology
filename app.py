"""Starlette + Uvicorn Production & Serverless ASGI Backend for CanThoFloodGuard."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from backend.autonomous_engine import (
    fuse_autonomous_traffic_into_roads,
    get_autonomous_traffic_snapshot,
    get_rainviewer_radar_meta,
    is_tomtom_configured,
)
from backend.camera_engine import get_cameras_payload
from backend.realtime_hub import format_sse, realtime_bus
from backend.cantho_geo import (
    CANTHO_DISTRICTS,
    CANTHO_NODES,
    DISTRICTS_BY_ID,
    ROAD_SEGMENTS,
    get_static_geo_payload,
)
from backend.data_pipeline import (
    SCENARIOS,
    fetch_cantho_realtime_hub,
    fetch_cantho_weather_bronze,
    generate_24h_hydrograph,
)
from backend.incident_engine import (
    VALID_CATEGORIES,
    IncidentStore,
    get_demo_clustered_incidents,
)
from backend.ml_engine import CanThoFloodMLEngine, _flood_severity_label
from backend.osm_graph import get_osm_graph
from backend.poi_search import get_poi_index
from backend.routing_engine import (
    VEHICLE_PROFILES,
    compute_departure_windows,
    find_hybrid_flood_aware_route,
    fuse_community_incidents_into_roads,
)
from backend.security import (
    RateLimitMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    TieredCacheMiddleware,
    parse_allowed_origins,
    sanitize_text,
    validate_photo_data_url,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
PUBLIC_DIR = BASE_DIR / "public"
PUBLIC_ASSETS_DIR = PUBLIC_DIR / "assets"


def _load_local_env_files() -> None:
    """Populate os.environ from .env / .env.local in local development without third-party dotenv."""
    if os.environ.get("VERCEL") == "1":
        return
    for fname in (".env", ".env.local"):
        fpath = BASE_DIR / fname
        if not fpath.is_file():
            continue
        try:
            for raw_line in fpath.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                key = k.strip()
                val = v.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, val)
        except OSError:
            pass


_load_local_env_files()

IS_PROD = (
    os.environ.get("VERCEL") == "1"
    or os.environ.get("APP_ENV", "development").lower() == "production"
    or os.environ.get("USE_MINIFIED_ASSETS") == "1"
)
SERVE_MINIFIED = IS_PROD and (PUBLIC_DIR / "index.html").exists()

app_logger = logging.getLogger("cantho.app")

# Initialize compressed ML Engine, contracted OSM street graph, POI index, and Dual-Mode Incident Store
ml_engine = CanThoFloodMLEngine(force_retrain=False)
osm_graph = get_osm_graph()
poi_index = get_poi_index()
incident_store = IncidentStore.from_env()

_LAST_FORCED_REFRESH_TS = 0.0
_GEOCODE_CACHE: Dict[str, List[Dict]] = {}


def _get_live_hydro_context() -> Tuple[float, float, Dict]:
    """Read cached live rain (mm/h) and Hau River tide (m) for Dynamic TTL & Sensor Fusion."""
    hub = fetch_cantho_realtime_hub(district_id="ninh_kieu", force_refresh=False)
    rain = float(hub.get("current", {}).get("precip_mm", 0.0))
    tide = float(hub.get("astronomy_and_tide", {}).get("current_tide_m", 1.45))
    return rain, tide, hub

GITHUB_RECON_PATTERNS = [
    {
        "repo": "Open-Meteo Weather & Air Quality",
        "url": "https://open-meteo.com/",
        "domain": "Dự báo Thời tiết & Chất lượng Không khí",
        "applied_pattern": "Cung cấp dữ liệu nhiệt độ, mưa, gió, tia UV và US AQI/PM2.5 cho 6 Quận/Huyện tại TP. Cần Thơ.",
    },
    {
        "repo": "Open-Meteo GloFAS Flood API",
        "url": "https://open-meteo.com/en/docs/flood-api",
        "domain": "Thủy văn & Lưu lượng Sông Hậu",
        "applied_pattern": "Cung cấp lưu lượng dòng chảy Sông Hậu kết hợp tính toán thủy triều để ước lượng mức ngập theo giờ.",
    },
    {
        "repo": "OpenStreetMap & Leaflet",
        "url": "https://leafletjs.com/",
        "domain": "Bản đồ Đường phố & Địa điểm Cần Thơ",
        "applied_pattern": "Hiển thị bản đồ trực quan, tìm kiếm địa điểm không dấu và dẫn đường né ngập theo mạng lưới giao thông Cần Thơ.",
    },
]


def _resolve_static_file(filename: str) -> Path:
    """Resolve PWA manifest/sw cleanly from public/ in production or static/ in dev."""
    pub_candidate = PUBLIC_DIR / filename
    if SERVE_MINIFIED and pub_candidate.exists():
        return pub_candidate
    return STATIC_DIR / filename


async def serve_index(request: Request) -> FileResponse:
    """Serve the minified production entry page when built, or dev static HTML locally."""
    entry = (PUBLIC_DIR / "index.html") if SERVE_MINIFIED else (STATIC_DIR / "index.html")
    return FileResponse(entry)


async def serve_manifest(request: Request) -> FileResponse:
    """Serve PWA Web App Manifest."""
    return FileResponse(_resolve_static_file("manifest.json"), media_type="application/manifest+json")


async def serve_sw(request: Request) -> FileResponse:
    """Serve PWA Service Worker with script MIME type."""
    return FileResponse(_resolve_static_file("sw.js"), media_type="application/javascript")


async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint reporting runtime environment and active DB backend."""
    return JSONResponse(
        {
            "status": "ok",
            "service": "CanThoFloodGuard & OmniWeather 24/7 Hub",
            "env": "production" if IS_PROD else "development",
            "storage": incident_store.storage_backend,
            "assets_mode": "minified" if SERVE_MINIFIED else "dev-static",
            "nodes": len(CANTHO_NODES),
            "roads": len(ROAD_SEGMENTS),
            "districts": len(CANTHO_DISTRICTS),
            "osm_nodes": len(osm_graph.nodes) if osm_graph is not None else 0,
            "osm_edges": len(osm_graph.edges) if osm_graph is not None else 0,
            "poi_count": poi_index.count,
            "realtime": {
                "transport": "sse+delta",
                "weather_cache_sec": 30,
                "stream_window_sec": 20,
            },
            "autonomous": {
                "tomtom_enabled": is_tomtom_configured(),
                "vrain_enabled": bool(os.environ.get("VRAIN_API_KEY", "").strip()),
                "radar": "rainviewer",
            },
        }
    )


async def get_live_weather(request: Request) -> JSONResponse:
    """Return 24/7 real-time weather, UV, AQI, lunar/tide schedule, and alerts with refresh cooldown."""
    global _LAST_FORCED_REFRESH_TS
    district_id = sanitize_text(request.query_params.get("district", "ninh_kieu"), 32)
    wants_refresh = request.query_params.get("refresh", "false").lower() in ("1", "true", "yes")
    cooldown_sec = float(os.environ.get("REFRESH_COOLDOWN_SEC", "15"))

    allow_refresh = False
    if wants_refresh:
        now_ts = time.monotonic()
        if (now_ts - _LAST_FORCED_REFRESH_TS) >= cooldown_sec:
            allow_refresh = True
            _LAST_FORCED_REFRESH_TS = now_ts

    hub_data = fetch_cantho_realtime_hub(district_id=district_id, force_refresh=allow_refresh)
    hub_data["refresh_throttled"] = bool(wants_refresh and not allow_refresh)
    return JSONResponse(hub_data)


async def get_init_payload(request: Request) -> JSONResponse:
    """Return initial geospatial metadata, live weather hub, scenarios, vehicles, and ML benchmarks."""
    weather = fetch_cantho_weather_bronze()
    hourly = weather.get("hourly", {})
    weather_preview = [
        {
            "time": t,
            "precip_mm": p,
            "humidity_pct": h,
        }
        for t, p, h in zip(
            hourly.get("time", [])[:12],
            hourly.get("precipitation", [])[:12],
            hourly.get("relative_humidity_2m", [])[:12],
        )
    ]
    live_hub = fetch_cantho_realtime_hub(district_id="ninh_kieu", force_refresh=False)

    return JSONResponse(
        {
            "geo": get_static_geo_payload(),
            "live_weather_hub": live_hub,
            "github_recon_patterns": GITHUB_RECON_PATTERNS,
            "scenarios": list(SCENARIOS.values()),
            "vehicles": [asdict(v) for v in VEHICLE_PROFILES.values()],
            "weather_bronze": {
                "source": weather.get("_source", "unknown"),
                "fetched_at": weather.get("_fetched_at", ""),
                "preview": weather_preview,
            },
            "ml_evaluation": {
                "dataset_stats": ml_engine.dataset_stats,
                "benchmarks": ml_engine.benchmark_results,
                "feature_attribution": ml_engine.feature_attribution,
            },
        }
    )


def _validate_hydro_bounds(tide_m: float, rain_h: float, rain_3h: float, delta_m: float) -> str | None:
    """Validate physical hydro-meteorological bounds without leaking internal exceptions."""
    if not all(math.isfinite(x) for x in (tide_m, rain_h, rain_3h, delta_m)):
        return "Giá trị thủy văn/mưa phải là số thực hữu hạn."
    if not (0.0 <= tide_m <= 3.5 and 0.0 <= rain_h <= 300.0 and 0.0 <= rain_3h <= 600.0 and -1.0 <= delta_m <= 1.0):
        return "Thông số mực nước hoặc lượng mưa vượt ngưỡng vật lý hợp lệ."
    return None


async def simulate_flood(request: Request) -> JSONResponse:
    """Run spatio-temporal flood prediction across Can Tho hex grid and roads."""
    try:
        body = await request.json()
        tide_level_m = float(body.get("tide_level_m", 2.22))
        rain_mm_h = float(body.get("rain_mm_h", 45.0))
        rain_3h_cum = float(body.get("rain_3h_cum", round(rain_mm_h * 1.8, 1)))
        tide_delta_m = float(body.get("tide_delta_m", 0.14))
        is_spring_tide = bool(body.get("is_spring_tide", tide_level_m >= 1.85))
        horizon = sanitize_text(body.get("horizon", "+1h"), 8)
        model_key = sanitize_text(body.get("model_key", "hgbr"), 16)
        include_community = bool(body.get("include_community", False))
        raw_auto = body.get("include_autonomous")
        include_autonomous = bool(include_community and horizon == "now") if raw_auto is None else bool(raw_auto)
    except Exception:
        return JSONResponse({"error": "Tham số mô phỏng JSON không hợp lệ."}, status_code=400)

    err = _validate_hydro_bounds(tide_level_m, rain_mm_h, rain_3h_cum, tide_delta_m)
    if err:
        return JSONResponse({"error": err}, status_code=400)

    prediction = ml_engine.predict_city_flood_map(
        tide_level_m=tide_level_m,
        rain_mm_h=rain_mm_h,
        rain_3h_cum=rain_3h_cum,
        tide_delta_m=tide_delta_m,
        horizon=horizon,
        model_key=model_key,
    )

    autonomous_overrides: Dict[str, Dict] = {}
    autonomous_meta = {"enabled": is_tomtom_configured(), "anomaly_count": 0, "probe_count": 0, "fetched_at": ""}
    if include_autonomous:
        traffic_snap = get_autonomous_traffic_snapshot(
            rain_mm_h=rain_mm_h,
            rain_past_3h_mm=rain_3h_cum,
            tide_level_m=tide_level_m,
            force_refresh=False,
        )
        fused_auto, autonomous_overrides = fuse_autonomous_traffic_into_roads(
            prediction["road_depths"], traffic_snap
        )
        prediction["road_depths"] = fused_auto
        autonomous_meta = {
            "enabled": bool(traffic_snap.get("enabled")),
            "anomaly_count": int(traffic_snap.get("anomaly_count", 0)),
            "probe_count": int(traffic_snap.get("probe_count", 0)),
            "fetched_at": traffic_snap.get("fetched_at", ""),
        }

    community_overrides: Dict[str, Dict] = {}
    if include_community:
        live_rain, live_tide, _ = _get_live_hydro_context()
        inc_data = incident_store.get_clustered_incidents(rain_mm_h=live_rain, tide_level_m=live_tide)
        fused_depths, community_overrides = fuse_community_incidents_into_roads(
            prediction["road_depths"], inc_data.get("master_incidents", [])
        )
        prediction["road_depths"] = fused_depths

    if include_autonomous or include_community:
        for r_obj in prediction["roads"]:
            rid = r_obj["id"]
            if rid in community_overrides:
                r_obj["depth_cm"] = prediction["road_depths"][rid]
                sev_code, sev_label = _flood_severity_label(r_obj["depth_cm"])
                r_obj["severity"] = sev_code
                r_obj["severity_label"] = sev_label
                r_obj["source"] = "community_1022"
            elif rid in autonomous_overrides:
                r_obj["depth_cm"] = prediction["road_depths"][rid]
                sev_code, sev_label = _flood_severity_label(r_obj["depth_cm"])
                r_obj["severity"] = sev_code
                r_obj["severity_label"] = sev_label
                r_obj["source"] = "autonomous_traffic"
            else:
                r_obj["source"] = "ai_model"
        dangerous_roads = sorted([r for r in prediction["roads"] if r["depth_cm"] >= 18.0], key=lambda x: -x["depth_cm"])
        prediction["summary"]["dangerous_road_count"] = len(dangerous_roads)
        prediction["summary"]["top_flooded_roads"] = [
            {
                "id": r["id"],
                "name": r["name"],
                "depth_cm": r["depth_cm"],
                "severity": r["severity"],
                "mid_coord": r["coords"][len(r["coords"]) // 2],
            }
            for r in dangerous_roads[:6]
        ]

    hydrograph = generate_24h_hydrograph(
        is_spring_tide=is_spring_tide,
        peak_tide_m=tide_level_m,
        rain_mm_h=rain_mm_h,
    )

    return JSONResponse(
        {
            "inputs": {
                "tide_level_m": tide_level_m,
                "rain_mm_h": rain_mm_h,
                "rain_3h_cum": rain_3h_cum,
                "tide_delta_m": tide_delta_m,
                "horizon": horizon,
                "model_key": model_key,
                "include_community": include_community,
                "include_autonomous": include_autonomous,
            },
            "prediction": prediction,
            "community_fused_count": len(community_overrides),
            "autonomous_fused_count": len(autonomous_overrides),
            "autonomous_traffic": autonomous_meta,
            "hydrograph_24h": hydrograph,
        }
    )


def _parse_custom_coord(raw: object) -> Optional[Tuple[float, float]]:
    """Parse and bound-check an optional [lat, lon] coordinate inside Can Tho."""
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError("Invalid coordinate format")
    lat = float(raw[0])
    lon = float(raw[1])
    if not (math.isfinite(lat) and math.isfinite(lon) and 9.8 <= lat <= 10.3 and 105.5 <= lon <= 106.0):
        raise ValueError("Coordinate out of Can Tho bounds")
    return (round(lat, 6), round(lon, 6))


async def compute_route(request: Request) -> JSONResponse:
    """Compute shortest vs Flood-Aware A* route with community sensor fusion, hybrid pins & departure windows."""
    try:
        body = await request.json()
        start_id = sanitize_text(body.get("start_id", "dhct_khu2"), 48)
        end_id = sanitize_text(body.get("end_id", "ben_ninh_kieu"), 48)
        vehicle_type = sanitize_text(body.get("vehicle_type", "scooter"), 24)
        tide_level_m = float(body.get("tide_level_m", 2.22))
        rain_mm_h = float(body.get("rain_mm_h", 45.0))
        rain_3h_cum = float(body.get("rain_3h_cum", round(rain_mm_h * 1.8, 1)))
        tide_delta_m = float(body.get("tide_delta_m", 0.14))
        horizon = sanitize_text(body.get("horizon", "+1h"), 8)
        model_key = sanitize_text(body.get("model_key", "hgbr"), 16)
        include_community = body.get("include_community")
        if include_community is None:
            include_community = horizon == "now"
        else:
            include_community = bool(include_community)
        raw_auto = body.get("include_autonomous")
        include_autonomous = bool(include_community and horizon == "now") if raw_auto is None else bool(raw_auto)
        include_departure = bool(body.get("include_departure", horizon == "now"))
        start_label = sanitize_text(body.get("start_label", ""), 90) or None
        end_label = sanitize_text(body.get("end_label", ""), 90) or None
        start_coord = _parse_custom_coord(body.get("start_coord"))
        end_coord = _parse_custom_coord(body.get("end_coord"))
    except ValueError:
        return JSONResponse({"error": "Tọa độ điểm đi hoặc điểm đến không hợp lệ trong phạm vi TP. Cần Thơ."}, status_code=400)
    except Exception:
        return JSONResponse({"error": "Tham số định tuyến JSON không hợp lệ."}, status_code=400)

    err = _validate_hydro_bounds(tide_level_m, rain_mm_h, rain_3h_cum, tide_delta_m)
    if err:
        return JSONResponse({"error": err}, status_code=400)

    prediction = ml_engine.predict_city_flood_map(
        tide_level_m=tide_level_m,
        rain_mm_h=rain_mm_h,
        rain_3h_cum=rain_3h_cum,
        tide_delta_m=tide_delta_m,
        horizon=horizon,
        model_key=model_key,
    )

    live_rain, live_tide, hub = _get_live_hydro_context()
    road_depths = dict(prediction["road_depths"])
    hex_depths = [float(c["depth_cm"]) for c in prediction.get("hex_cells", [])]
    autonomous_overrides: Dict[str, Dict] = {}
    if include_autonomous:
        traffic_snap = get_autonomous_traffic_snapshot(
            rain_mm_h=rain_mm_h,
            rain_past_3h_mm=rain_3h_cum,
            tide_level_m=tide_level_m,
            force_refresh=False,
        )
        road_depths, autonomous_overrides = fuse_autonomous_traffic_into_roads(road_depths, traffic_snap)

    master_incidents: List[Dict] = []
    community_overrides: Dict[str, Dict] = {}
    if include_community:
        inc_data = incident_store.get_clustered_incidents(rain_mm_h=live_rain, tide_level_m=live_tide)
        master_incidents = inc_data.get("master_incidents", [])
        road_depths, community_overrides = fuse_community_incidents_into_roads(
            road_depths, master_incidents
        )

    try:
        route_result = find_hybrid_flood_aware_route(
            start_id=start_id,
            end_id=end_id,
            vehicle_type=vehicle_type,
            road_depths=road_depths,
            start_coord=start_coord,
            end_coord=end_coord,
            start_label=start_label,
            end_label=end_label,
            community_overrides=community_overrides,
            hex_depths=hex_depths,
            master_incidents=master_incidents if include_community else None,
        )
    except ValueError:
        return JSONResponse({"error": "Điểm đi, điểm đến hoặc loại phương tiện không tồn tại trong mạng lưới Cần Thơ."}, status_code=400)

    route_result["autonomous_fused_roads"] = list(autonomous_overrides.values())

    if include_departure:
        route_result["departure_advisor"] = compute_departure_windows(
            ml_engine=ml_engine,
            start_id=start_id,
            end_id=end_id,
            vehicle_type=vehicle_type,
            hourly_weather=hub.get("hourly_24h", []),
            hourly_tide=hub.get("astronomy_and_tide", {}).get("hourly_24h", []),
            base_inputs={
                "tide_level_m": tide_level_m,
                "rain_mm_h": rain_mm_h,
                "rain_3h_cum": rain_3h_cum,
                "tide_delta_m": tide_delta_m,
                "model_key": model_key,
            },
            community_overrides=community_overrides,
            start_coord=start_coord,
            end_coord=end_coord,
            master_incidents=master_incidents if include_community else None,
        )

    return JSONResponse(route_result)


async def get_incidents(request: Request) -> JSONResponse:
    """Return Weather-Aware Dynamic TTL community feed (or RAM-only demo with ?demo=true)."""
    if request.query_params.get("demo", "false").lower() in ("1", "true", "yes"):
        return JSONResponse(get_demo_clustered_incidents())
    rain, tide, _ = _get_live_hydro_context()
    return JSONResponse(incident_store.get_clustered_incidents(rain_mm_h=rain, tide_level_m=tide))


async def create_incident(request: Request) -> JSONResponse:
    """Submit a new citizen community report with compressed photo validation & OWASP hardening."""
    try:
        body = await request.json()
        if str(body.get("website", "")).strip():
            return JSONResponse({"error": "Yêu cầu không hợp lệ."}, status_code=400)

        lat = float(body["lat"])
        lon = float(body["lon"])
        depth = float(body.get("reported_depth_cm", 20.0))
        desc = sanitize_text(body.get("description", "Phản ánh hiện trường thực tế từ người dân"), 280)
        reporter = sanitize_text(body.get("reporter_name", "Người dân Cần Thơ"), 50)
        hint = sanitize_text(body.get("location_hint", "Điểm phản ánh trên bản đồ"), 100)
        category = sanitize_text(body.get("category", "flood_deep"), 24)
        district = sanitize_text(body.get("district", "ninh_kieu"), 24)
        photo_ok, photo_data = validate_photo_data_url(body.get("photo_data", ""))
        if not photo_ok:
            return JSONResponse({"error": "Ảnh đính kèm phải là JPEG/WebP đã nén dưới 90 KB."}, status_code=400)
    except Exception:
        return JSONResponse({"error": "Dữ liệu báo cáo ngập không hợp lệ."}, status_code=400)

    if not all(math.isfinite(x) for x in (lat, lon, depth)):
        return JSONResponse({"error": "Tọa độ và độ sâu ngập phải là số thực hữu hạn."}, status_code=400)
    if not (9.8 <= lat <= 10.3 and 105.5 <= lon <= 106.0):
        return JSONResponse({"error": "Tọa độ nằm ngoài khu vực TP. Cần Thơ."}, status_code=400)
    if not (0.0 <= depth <= 200.0):
        return JSONResponse({"error": "Độ sâu ngập phải nằm trong khoảng 0–200 cm."}, status_code=400)

    norm_cat = category if category in VALID_CATEGORIES else ("flood_deep" if depth >= 18.0 else "flood_light")
    norm_dist = district if district in DISTRICTS_BY_ID else "ninh_kieu"
    rain, tide, _ = _get_live_hydro_context()

    updated = incident_store.add_report(
        lat=lat,
        lon=lon,
        reported_depth_cm=depth,
        description=desc or "Phản ánh hiện trường mới",
        reporter_name=reporter or "Người dân Cần Thơ",
        location_hint=hint or "TP. Cần Thơ",
        category=norm_cat,
        district=norm_dist,
        photo_data=photo_data,
        rain_mm_h=rain,
        tide_level_m=tide,
    )
    realtime_bus.publish(
        "flood_update",
        {
            "reason": "report",
            "district": norm_dist,
            "signature": incident_store.get_state_signature(),
            "server_ts": time.time(),
        },
    )
    return JSONResponse(updated, status_code=201)


async def vote_incident(request: Request) -> JSONResponse:
    """Record 1-tap community confirmation ('confirm') or clearance ('clear') vote."""
    try:
        body = await request.json()
        if str(body.get("website", "")).strip():
            return JSONResponse({"error": "Yêu cầu không hợp lệ."}, status_code=400)

        action = sanitize_text(body.get("action", ""), 16).lower()
        if action not in ("confirm", "clear"):
            return JSONResponse({"error": "Hành động bình chọn phải là 'confirm' hoặc 'clear'."}, status_code=400)

        lat = float(body["lat"])
        lon = float(body["lon"])
        depth = float(body.get("reported_depth_cm", 25.0))
        reporter = sanitize_text(body.get("reporter_name", "Người dân Cần Thơ"), 50)
        hint = sanitize_text(body.get("location_hint", "Điểm phản ánh trên bản đồ"), 100)
        district = sanitize_text(body.get("district", "ninh_kieu"), 24)
        category = sanitize_text(body.get("category", "flood_deep"), 24)
    except Exception:
        return JSONResponse({"error": "Dữ liệu xác nhận không hợp lệ."}, status_code=400)

    if not all(math.isfinite(x) for x in (lat, lon, depth)):
        return JSONResponse({"error": "Tọa độ và độ sâu phải là số hữu hạn."}, status_code=400)
    if not (9.8 <= lat <= 10.3 and 105.5 <= lon <= 106.0):
        return JSONResponse({"error": "Tọa độ nằm ngoài khu vực TP. Cần Thơ."}, status_code=400)
    if not (0.0 <= depth <= 200.0):
        return JSONResponse({"error": "Độ sâu ngập phải nằm trong khoảng 0–200 cm."}, status_code=400)

    rain, tide, _ = _get_live_hydro_context()
    norm_dist = district if district in DISTRICTS_BY_ID else "ninh_kieu"
    updated = incident_store.add_vote(
        action=action,
        lat=lat,
        lon=lon,
        reported_depth_cm=depth,
        location_hint=hint or "TP. Cần Thơ",
        reporter_name=reporter or "Người dân Cần Thơ",
        district=norm_dist,
        category=category if category in VALID_CATEGORIES else "flood_deep",
        rain_mm_h=rain,
        tide_level_m=tide,
    )
    realtime_bus.publish(
        "flood_update",
        {
            "reason": f"vote:{action}",
            "district": norm_dist,
            "signature": incident_store.get_state_signature(),
            "server_ts": time.time(),
        },
    )
    return JSONResponse(updated, status_code=201)


async def comment_incident(request: Request) -> JSONResponse:
    """Attach a community comment to a report."""
    try:
        body = await request.json()
        if str(body.get("website", "")).strip():
            return JSONResponse({"error": "Yêu cầu không hợp lệ."}, status_code=400)
        report_id = sanitize_text(body.get("report_id", ""), 32)
        author = sanitize_text(body.get("author_name", "Người dân Cần Thơ"), 50)
        content = sanitize_text(body.get("content", ""), 240)
    except Exception:
        return JSONResponse({"error": "Dữ liệu bình luận không hợp lệ."}, status_code=400)

    if not report_id or len(content) < 2:
        return JSONResponse({"error": "Nội dung bình luận tối thiểu 2 ký tự."}, status_code=400)

    rain, tide, _ = _get_live_hydro_context()
    updated = incident_store.add_comment(
        report_id=report_id,
        author_name=author or "Người dân Cần Thơ",
        content=content,
        rain_mm_h=rain,
        tide_level_m=tide,
    )
    realtime_bus.publish(
        "flood_update",
        {
            "reason": "comment",
            "report_id": report_id,
            "signature": incident_store.get_state_signature(),
            "server_ts": time.time(),
        },
    )
    return JSONResponse(updated, status_code=201)


async def get_cameras(request: Request) -> JSONResponse:
    """Return 8 Can Tho intersection camera stations enriched with IOC feed state, community photos & AI flood."""
    rain, tide, hub = _get_live_hydro_context()
    try:
        q_tide = request.query_params.get("tide_level_m")
        q_rain = request.query_params.get("rain_mm_h")
        tide_val = float(q_tide) if q_tide is not None else tide
        rain_val = float(q_rain) if q_rain is not None else rain
    except ValueError:
        return JSONResponse({"error": "Tham số thủy văn camera không hợp lệ."}, status_code=400)

    if not (math.isfinite(tide_val) and math.isfinite(rain_val) and 0.0 <= tide_val <= 3.5 and 0.0 <= rain_val <= 300.0):
        return JSONResponse({"error": "Tham số thủy văn ngoài biên."}, status_code=400)

    pred = ml_engine.predict_city_flood_map(
        tide_level_m=tide_val,
        rain_mm_h=rain_val,
        rain_3h_cum=round(rain_val * 1.6, 1),
        tide_delta_m=float(hub.get("astronomy_and_tide", {}).get("current_tide_delta_m_h", 0.0)),
        horizon="now",
        model_key="hgbr",
    )
    inc_data = incident_store.get_clustered_incidents(rain_mm_h=rain, tide_level_m=tide)
    fused_depths, _ = fuse_community_incidents_into_roads(
        pred["road_depths"], inc_data.get("master_incidents", [])
    )
    return JSONResponse(get_cameras_payload(fused_depths, inc_data, hub))


async def geocode_cantho(request: Request) -> JSONResponse:
    """Accent-insensitive Can Tho POI + intersection search (1,600+ local OSM POIs + Photon fallback)."""
    q = sanitize_text(request.query_params.get("q", ""), 120)
    wants_popular = request.query_params.get("popular", "0").lower() in ("1", "true", "yes")
    if wants_popular or not q.strip():
        return JSONResponse({"results": poi_index.get_popular_places(limit=14)})
    if len(q.strip()) < 2:
        return JSONResponse({"results": []})

    cache_key = q.lower().strip()
    if cache_key in _GEOCODE_CACHE:
        return JSONResponse({"results": _GEOCODE_CACHE[cache_key]})

    results = poi_index.search_places(q, limit=8)
    if len(_GEOCODE_CACHE) > 256:
        _GEOCODE_CACHE.clear()
    _GEOCODE_CACHE[cache_key] = results
    return JSONResponse({"results": results})


async def get_radar_meta(request: Request) -> JSONResponse:
    """Return latest RainViewer radar frame URL template & frames for Leaflet overlay."""
    wants_refresh = request.query_params.get("refresh", "false").lower() in ("1", "true", "yes")
    return JSONResponse(get_rainviewer_radar_meta(force_refresh=wants_refresh))


async def get_autonomous_traffic(request: Request) -> JSONResponse:
    """Return live TomTom Traffic Flow speed probes paired with flood anomaly inference."""
    rain, tide, hub = _get_live_hydro_context()
    rain_3h = float(hub.get("current", {}).get("rain_past_3h_mm", 0.0))
    return JSONResponse(
        get_autonomous_traffic_snapshot(
            rain_mm_h=rain,
            rain_past_3h_mm=rain_3h,
            tide_level_m=tide,
            force_refresh=False,
        )
    )


def _build_realtime_pulse(district_id: str) -> Dict:
    rain, tide, hub = _get_live_hydro_context()
    if district_id and district_id != "ninh_kieu":
        hub = fetch_cantho_realtime_hub(district_id=district_id, force_refresh=False)
        rain = float(hub.get("current", {}).get("precip_mm", rain))
    astro = hub.get("astronomy_and_tide", {})
    return {
        "tide_m": round(float(astro.get("current_tide_m", tide)), 2),
        "tide_delta_m_h": round(float(astro.get("current_tide_delta_m_h", 0.0)), 3),
        "rain_mm_h": round(float(rain), 1),
        "weather_fetched_at": hub.get("fetched_at"),
        "server_ts": time.time(),
    }


async def get_realtime_state(request: Request) -> JSONResponse:
    """Return lightweight O(1) signature check or delta incident/hydro payload."""
    district_id = sanitize_text(request.query_params.get("district", "ninh_kieu"), 32) or "ninh_kieu"
    since_sig = sanitize_text(request.query_params.get("since", ""), 96)
    sig = incident_store.get_state_signature()
    pulse = _build_realtime_pulse(district_id)

    if since_sig and since_sig == sig:
        return JSONResponse({"changed": False, "signature": sig, **pulse})

    incidents = incident_store.get_clustered_incidents(
        rain_mm_h=pulse["rain_mm_h"],
        tide_level_m=pulse["tide_m"],
    )
    return JSONResponse({"changed": True, "signature": sig, "incidents": incidents, **pulse})


async def stream_realtime(request: Request) -> StreamingResponse:
    """Push instant community flood updates and live hydro pulses via Server-Sent Events."""
    district_id = sanitize_text(request.query_params.get("district", "ninh_kieu"), 32) or "ninh_kieu"
    try:
        max_seconds = float(request.query_params.get("max_seconds", "20.0"))
    except ValueError:
        max_seconds = 20.0
    max_seconds = max(0.2, min(22.0, max_seconds))

    async def _event_stream():
        q = realtime_bus.subscribe()
        started = time.monotonic()
        last_sig = incident_store.get_state_signature()
        last_pulse_ts = started
        try:
            init_pulse = {**_build_realtime_pulse(district_id), "signature": last_sig}
            yield format_sse("pulse", init_pulse, retry_ms=2000)

            while (time.monotonic() - started) < max_seconds:
                if await request.is_disconnected():
                    break
                remaining = max_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    break
                wait_timeout = min(2.5, remaining)
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=wait_timeout)
                    ev_data = msg.get("data") or {}
                    new_sig = ev_data.get("signature")
                    if new_sig:
                        last_sig = new_sig
                    yield format_sse(msg.get("event", "flood_update"), ev_data)
                    continue
                except asyncio.TimeoutError:
                    pass

                if await request.is_disconnected():
                    break

                cur_sig = incident_store.get_state_signature()
                if cur_sig != last_sig:
                    last_sig = cur_sig
                    yield format_sse(
                        "flood_update",
                        {
                            "reason": "db_sync",
                            "district": district_id,
                            "signature": cur_sig,
                            "server_ts": time.time(),
                        },
                    )

                now_m = time.monotonic()
                if (now_m - last_pulse_ts) >= 10.0:
                    last_pulse_ts = now_m
                    yield format_sse("pulse", {**_build_realtime_pulse(district_id), "signature": last_sig})
        finally:
            realtime_bus.unsubscribe(q)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


routes = [
    Route("/", endpoint=serve_index, methods=["GET"]),
    Route("/manifest.json", endpoint=serve_manifest, methods=["GET"]),
    Route("/sw.js", endpoint=serve_sw, methods=["GET"]),
    Route("/api/health", endpoint=health_check, methods=["GET"]),
    Route("/api/init", endpoint=get_init_payload, methods=["GET"]),
    Route("/api/weather/live", endpoint=get_live_weather, methods=["GET"]),
    Route("/api/simulate", endpoint=simulate_flood, methods=["POST"]),
    Route("/api/route", endpoint=compute_route, methods=["POST"]),
    Route("/api/incidents", endpoint=get_incidents, methods=["GET"]),
    Route("/api/incidents", endpoint=create_incident, methods=["POST"]),
    Route("/api/incidents/vote", endpoint=vote_incident, methods=["POST"]),
    Route("/api/incidents/comment", endpoint=comment_incident, methods=["POST"]),
    Route("/api/cameras", endpoint=get_cameras, methods=["GET"]),
    Route("/api/geocode", endpoint=geocode_cantho, methods=["GET"]),
    Route("/api/radar", endpoint=get_radar_meta, methods=["GET"]),
    Route("/api/autonomous/traffic", endpoint=get_autonomous_traffic, methods=["GET"]),
    Route("/api/realtime/state", endpoint=get_realtime_state, methods=["GET"]),
    Route("/api/realtime/stream", endpoint=stream_realtime, methods=["GET"]),
]

if PUBLIC_ASSETS_DIR.exists():
    routes.append(Mount("/assets", app=StaticFiles(directory=str(PUBLIC_ASSETS_DIR)), name="assets"))

if not IS_PROD and STATIC_DIR.exists():
    routes.append(Mount("/static", app=StaticFiles(directory=str(STATIC_DIR)), name="static"))

allowed_origins = parse_allowed_origins(IS_PROD)

app = Starlette(
    debug=False,
    routes=routes,
    middleware=[
        Middleware(RequestLoggingMiddleware),
        Middleware(SecurityHeadersMiddleware, is_prod=IS_PROD),
        Middleware(RateLimitMiddleware),
        Middleware(TieredCacheMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "X-Request-ID"],
        ),
    ],
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8088"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

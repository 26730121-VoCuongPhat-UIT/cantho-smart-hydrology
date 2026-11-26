"""Autonomous Real-Time Flood Telemetry: TomTom Traffic Flow Probes & RainViewer Radar."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

VN_TZ = timezone(timedelta(hours=7))

TRAFFIC_PROBES: List[Dict[str, Any]] = [
    {
        "id": "probe_mau_than",
        "road_id": "road_01",
        "name": "Mậu Thân (Ngã tư 3/2 → Rạch Ngỗng)",
        "lat": 10.0361,
        "lon": 105.7719,
        "district": "ninh_kieu",
    },
    {
        "id": "probe_32_dhct",
        "road_id": "road_00",
        "name": "Đường 3 Tháng 2 (ĐHCT → Mậu Thân)",
        "lat": 10.0319,
        "lon": 105.7738,
        "district": "ninh_kieu",
    },
    {
        "id": "probe_tran_hung_dao",
        "road_id": "road_02",
        "name": "Trần Hưng Đạo (Mậu Thân → Lý Tự Trọng)",
        "lat": 10.0340,
        "lon": 105.7758,
        "district": "ninh_kieu",
    },
    {
        "id": "probe_tran_van_hoai",
        "road_id": "road_05",
        "name": "Đường 3/2 → Trần Văn Hoài",
        "lat": 10.0287,
        "lon": 105.7743,
        "district": "ninh_kieu",
    },
    {
        "id": "probe_ho_bun_xang",
        "road_id": "road_21",
        "name": "Hẻm 51 → Hồ Bún Xáng",
        "lat": 10.0303,
        "lon": 105.7677,
        "district": "ninh_kieu",
    },
    {
        "id": "probe_huynh_cuong",
        "road_id": "road_27",
        "name": "Hoàng Văn Thụ → Huỳnh Cương",
        "lat": 10.0375,
        "lon": 105.7751,
        "district": "ninh_kieu",
    },
    {
        "id": "probe_cai_khe",
        "road_id": "road_34",
        "name": "Trần Phú → TTTM Cái Khế",
        "lat": 10.0478,
        "lon": 105.7834,
        "district": "ninh_kieu",
    },
    {
        "id": "probe_cmt8_binh_thuy",
        "road_id": "road_38",
        "name": "CMT8 (Đoạn trũng Bình Thủy)",
        "lat": 10.0540,
        "lon": 105.7714,
        "district": "binh_thuy",
    },
]

_TRAFFIC_LOCK = threading.Lock()
_TRAFFIC_RAW_CACHE: Dict[str, Any] = {
    "fetched_epoch": 0.0,
    "fetched_at": "",
    "raw_probes": None,
}

_RADAR_LOCK = threading.Lock()
_RADAR_CACHE: Dict[str, Any] = {
    "fetched_epoch": 0.0,
    "payload": None,
}


def is_tomtom_configured() -> bool:
    """Return True when TOMTOM_API_KEY is configured and live probes are not disabled."""
    if os.environ.get("TOMTOM_DISABLE_LIVE") == "1":
        return False
    return bool(os.environ.get("TOMTOM_API_KEY", "").strip())


def classify_traffic_flood_anomaly(
    current_speed: float,
    free_flow_speed: float,
    confidence: float,
    road_closure: bool,
    rain_mm_h: float,
    rain_past_3h_mm: float,
    tide_level_m: float,
) -> Dict[str, Any]:
    """Infer flood vs dry-jam anomaly deterministically from traffic flow slowdown + hydrology."""
    speed_ratio = max(0.0, min(1.5, float(current_speed) / max(1.0, float(free_flow_speed))))
    slowdown_pct = max(0, min(100, int(round((1.0 - min(1.0, speed_ratio)) * 100))))
    conf = max(0.0, min(1.0, float(confidence)))
    hydro_wet = (float(rain_mm_h) >= 2.0) or (float(rain_past_3h_mm) >= 12.0) or (float(tide_level_m) >= 1.68)

    if road_closure and hydro_wet:
        status = "flood_suspected"
        inferred_depth_cm = 32.0
        label = "Đường bị chặn / ngập sâu nghi ngờ"
    elif hydro_wet and conf >= 0.65 and speed_ratio <= 0.42:
        status = "flood_suspected"
        inferred_depth_cm = 24.0
        label = "Xe di chuyển rất chậm khi mưa/triều cao"
    elif hydro_wet and conf >= 0.65 and speed_ratio <= 0.62:
        status = "flood_suspected"
        inferred_depth_cm = 14.0
        label = "Xe di chuyển chậm khi mưa/triều cao"
    elif (not hydro_wet) and conf >= 0.70 and speed_ratio <= 0.35:
        status = "traffic_jam"
        inferred_depth_cm = 0.0
        label = "Ùn tắc giao thông (trời khô)"
    else:
        status = "clear"
        inferred_depth_cm = 0.0
        label = "Lưu thông bình thường"

    return {
        "status": status,
        "inferred_depth_cm": inferred_depth_cm,
        "slowdown_pct": slowdown_pct,
        "speed_ratio": round(speed_ratio, 3),
        "hydro_wet": hydro_wet,
        "label": label,
    }


def _fetch_single_tomtom_probe(probe: Dict[str, Any], api_key: str, timeout_sec: float = 3.5) -> Optional[Dict[str, Any]]:
    url = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
    params = {"key": api_key, "point": f"{probe['lat']},{probe['lon']}"}
    try:
        resp = requests.get(url, params=params, timeout=timeout_sec)
        resp.raise_for_status()
        seg = resp.json().get("flowSegmentData") or {}
        cur_spd = float(seg.get("currentSpeed", 0.0))
        free_spd = float(seg.get("freeFlowSpeed", 30.0))
        conf = float(seg.get("confidence", 0.0))
        closure = bool(seg.get("roadClosure", False))
        if cur_spd <= 0.0 and not closure:
            return None
        return {
            **probe,
            "current_speed_kmh": round(cur_spd, 1),
            "free_flow_kmh": round(free_spd, 1),
            "confidence": round(conf, 3),
            "road_closure": closure,
        }
    except Exception:
        return None


def get_autonomous_traffic_snapshot(
    rain_mm_h: float = 0.0,
    rain_past_3h_mm: float = 0.0,
    tide_level_m: float = 1.45,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Fetch adaptive-cached Can Tho TomTom Traffic Flow probes and classify flood anomalies."""
    api_key = os.environ.get("TOMTOM_API_KEY", "").strip()
    disabled = os.environ.get("TOMTOM_DISABLE_LIVE") == "1"
    if disabled or not api_key:
        return {
            "enabled": False,
            "source": "disabled",
            "fetched_at": "",
            "probe_count": 0,
            "anomaly_count": 0,
            "jam_count": 0,
            "probes": [],
        }

    hydro_wet = (float(rain_mm_h) >= 2.0) or (float(rain_past_3h_mm) >= 12.0) or (float(tide_level_m) >= 1.68)
    ttl_sec = 180.0 if hydro_wet else 420.0
    now_epoch = time.time()

    with _TRAFFIC_LOCK:
        cached_raw = _TRAFFIC_RAW_CACHE["raw_probes"]
        cached_epoch = float(_TRAFFIC_RAW_CACHE["fetched_epoch"])
        cached_at = str(_TRAFFIC_RAW_CACHE["fetched_at"])

    raw_probes: Optional[List[Dict[str, Any]]] = None
    cache_fresh = cached_raw is not None and (now_epoch - cached_epoch) < ttl_sec
    if not force_refresh and cache_fresh:
        raw_probes = cached_raw
    else:
        with ThreadPoolExecutor(max_workers=len(TRAFFIC_PROBES)) as pool:
            fetched = list(pool.map(lambda p: _fetch_single_tomtom_probe(p, api_key), TRAFFIC_PROBES))
        valid = [item for item in fetched if item is not None]
        if valid:
            now_iso = datetime.now(VN_TZ).isoformat(timespec="seconds")
            with _TRAFFIC_LOCK:
                _TRAFFIC_RAW_CACHE["raw_probes"] = valid
                _TRAFFIC_RAW_CACHE["fetched_epoch"] = now_epoch
                _TRAFFIC_RAW_CACHE["fetched_at"] = now_iso
            raw_probes = valid
            cached_at = now_iso
        elif cached_raw:
            raw_probes = cached_raw
        else:
            return {
                "enabled": True,
                "source": "tomtom-unavailable",
                "fetched_at": "",
                "probe_count": 0,
                "anomaly_count": 0,
                "jam_count": 0,
                "probes": [],
            }

    classified: List[Dict[str, Any]] = []
    anomaly_count = 0
    jam_count = 0
    for item in raw_probes:
        verdict = classify_traffic_flood_anomaly(
            current_speed=item["current_speed_kmh"],
            free_flow_speed=item["free_flow_kmh"],
            confidence=item["confidence"],
            road_closure=item["road_closure"],
            rain_mm_h=rain_mm_h,
            rain_past_3h_mm=rain_past_3h_mm,
            tide_level_m=tide_level_m,
        )
        if verdict["status"] == "flood_suspected":
            anomaly_count += 1
        elif verdict["status"] == "traffic_jam":
            jam_count += 1
        classified.append({**item, **verdict})

    return {
        "enabled": True,
        "source": "tomtom-flow",
        "fetched_at": cached_at,
        "probe_count": len(classified),
        "anomaly_count": anomaly_count,
        "jam_count": jam_count,
        "probes": classified,
    }


def fuse_autonomous_traffic_into_roads(
    road_depths: Dict[str, float],
    traffic_snapshot: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]]]:
    """Elevate road flood depths where live TomTom probes detect wet-weather slowdowns."""
    fused = dict(road_depths)
    overrides: Dict[str, Dict[str, Any]] = {}
    for probe in traffic_snapshot.get("probes") or []:
        if probe.get("status") != "flood_suspected":
            continue
        depth = float(probe.get("inferred_depth_cm", 0.0))
        rid = str(probe.get("road_id", ""))
        if not rid or depth < 8.0:
            continue
        if depth > float(fused.get(rid, 0.0)):
            fused[rid] = round(depth, 1)
        overrides[rid] = {
            "road_id": rid,
            "label": probe.get("label", "Lưu thông chậm khi mưa/triều cao"),
            "current_speed_kmh": probe.get("current_speed_kmh"),
            "free_flow_kmh": probe.get("free_flow_kmh"),
            "slowdown_pct": probe.get("slowdown_pct"),
            "inferred_depth_cm": round(depth, 1),
            "confidence": probe.get("confidence"),
        }
    return fused, overrides


def get_rainviewer_radar_meta(force_refresh: bool = False, timeout_sec: float = 3.5) -> Dict[str, Any]:
    """Return cached RainViewer public weather radar tile template (max_native_zoom=7)."""
    now_epoch = time.time()
    with _RADAR_LOCK:
        cached = _RADAR_CACHE["payload"]
        cached_epoch = float(_RADAR_CACHE["fetched_epoch"])

    if not force_refresh and cached is not None and (now_epoch - cached_epoch) < 120.0:
        return cached

    try:
        resp = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=timeout_sec)
        resp.raise_for_status()
        raw = resp.json()
        host = str(raw.get("host", "https://tilecache.rainviewer.com")).rstrip("/")
        past = (raw.get("radar") or {}).get("past") or []
        if not past:
            raise ValueError("Missing radar frames")
        latest = past[-1]
        latest_path = str(latest.get("path", "")).strip()
        latest_time = int(latest.get("time", 0))
        if not latest_path:
            raise ValueError("Missing latest radar path")
        payload = {
            "available": True,
            "host": host,
            "latest_time": latest_time,
            "latest_path": latest_path,
            "tile_url_template": f"{host}{latest_path}/256/{{z}}/{{x}}/{{y}}/2/1_1.png",
            "max_native_zoom": 7,
            "frames": [
                {"time": int(f.get("time", 0)), "path": str(f.get("path", ""))}
                for f in past[-6:]
                if f.get("path")
            ],
            "fetched_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        }
        with _RADAR_LOCK:
            _RADAR_CACHE["payload"] = payload
            _RADAR_CACHE["fetched_epoch"] = now_epoch
        return payload
    except Exception:
        if cached is not None:
            return cached
        return {
            "available": False,
            "host": "",
            "latest_time": 0,
            "latest_path": "",
            "tile_url_template": "",
            "max_native_zoom": 7,
            "frames": [],
            "fetched_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
        }

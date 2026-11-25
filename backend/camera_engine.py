"""Can Tho Key Intersection Road Camera Hub & Pluggable IOC Stream Adapter.

Design:
- Maps 8 strategic Can Tho junctions to road segments, coordinates, and districts.
- Supports connecting real municipal IOC snapshot/stream feeds via IOC_CAMERA_FEEDS_JSON env var.
- When IOC live API is restricted/unconnected, seamlessly pairs each camera corner with the
  latest crowdsourced photo posted within 350m, live AI road flood depth, and Street View link.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Dict, List

from backend.cantho_geo import NODES_BY_ID, ROAD_SEGMENTS, haversine_m


@dataclass(frozen=True)
class RoadCameraStation:
    """Single traffic/flood monitoring camera station anchored to a Can Tho road junction."""

    id: str
    name: str
    street_label: str
    district: str
    district_name: str
    node_id: str
    road_ids: List[str]
    lat: float
    lon: float
    direction_note: str


CANTHO_CAMERAS: List[RoadCameraStation] = [
    RoadCameraStation(
        id="cam_32_mauthan",
        name="CAM 01 · Ngã tư 3/2 – Mậu Thân",
        street_label="Đường 3 Tháng 2 × Mậu Thân",
        district="ninh_kieu",
        district_name="Ninh Kiều",
        node_id="ngatu_32_mauthan",
        road_ids=["road_00", "road_01", "road_05"],
        lat=NODES_BY_ID["ngatu_32_mauthan"].lat,
        lon=NODES_BY_ID["ngatu_32_mauthan"].lon,
        direction_note="Góc quay hướng về vòng xoay Mậu Thân & Trần Hưng Đạo",
    ),
    RoadCameraStation(
        id="cam_mauthan_thd",
        name="CAM 02 · Cầu Rạch Ngỗng 1 (Đường Mậu Thân)",
        street_label="Đường Mậu Thân × Cầu Rạch Ngỗng 1",
        district="ninh_kieu",
        district_name="Ninh Kiều",
        node_id="mauthan_thd",
        road_ids=["road_01", "road_23", "road_31"],
        lat=NODES_BY_ID["mauthan_thd"].lat,
        lon=NODES_BY_ID["mauthan_thd"].lon,
        direction_note="Điểm trũng cốt +0.96m theo dõi triều dâng Rạch Ngỗng trên trục Mậu Thân",
    ),
    RoadCameraStation(
        id="cam_ho_bun_xang",
        name="CAM 03 · Bờ Hồ Bún Xáng (Nguyễn Văn Cừ)",
        street_label="Đường Hồ Bún Xáng × Nguyễn Văn Cừ",
        district="ninh_kieu",
        district_name="Ninh Kiều",
        node_id="ho_bun_xang",
        road_ids=["road_21", "road_22", "road_24"],
        lat=NODES_BY_ID["ho_bun_xang"].lat,
        lon=NODES_BY_ID["ho_bun_xang"].lon,
        direction_note="Quan trắc tràn bờ hồ Bún Xáng & Hẻm 51",
    ),
    RoadCameraStation(
        id="cam_hung_vuong",
        name="CAM 04 · Vòng xoay Hùng Vương – CMT8",
        street_label="Hùng Vương × Cách Mạng Tháng 8",
        district="ninh_kieu",
        district_name="Ninh Kiều",
        node_id="vong_xoay_hung_vuong",
        road_ids=["road_32", "road_33", "road_34", "road_38"],
        lat=NODES_BY_ID["vong_xoay_hung_vuong"].lat,
        lon=NODES_BY_ID["vong_xoay_hung_vuong"].lon,
        direction_note="Cửa ngõ nối Ninh Kiều – Bình Thủy – TTTM Cái Khế",
    ),
    RoadCameraStation(
        id="cam_hoa_binh",
        name="CAM 05 · Đại lộ Hòa Bình (Công viên Lưu Hữu Phước)",
        street_label="Đại lộ Hòa Bình × Lý Tự Trọng",
        district="ninh_kieu",
        district_name="Ninh Kiều",
        node_id="hoa_binh_lhp",
        road_ids=["road_03", "road_04", "road_08"],
        lat=NODES_BY_ID["hoa_binh_lhp"].lat,
        lon=NODES_BY_ID["hoa_binh_lhp"].lon,
        direction_note="Trục xương sống cốt cao +1.86m trung tâm hành chính",
    ),
    RoadCameraStation(
        id="cam_ben_ninh_kieu",
        name="CAM 06 · Bến Ninh Kiều (Hai Bà Trưng)",
        street_label="Hai Bà Trưng × Cầu đi bộ Ninh Kiều",
        district="ninh_kieu",
        district_name="Ninh Kiều",
        node_id="ben_ninh_kieu",
        road_ids=["road_04", "road_29", "road_35"],
        lat=NODES_BY_ID["ben_ninh_kieu"].lat,
        lon=NODES_BY_ID["ben_ninh_kieu"].lon,
        direction_note="Quan sát trực tiếp mép nước Sông Hậu & Công viên Bến Ninh Kiều",
    ),
    RoadCameraStation(
        id="cam_cau_quang_trung",
        name="CAM 07 · Nút giao Cầu Quang Trung (30/4)",
        street_label="Đường 30 Tháng 4 × Quang Trung",
        district="cai_rang",
        district_name="Ninh Kiều / Cái Răng",
        node_id="cau_quang_trung",
        road_ids=["road_07", "road_08", "road_16", "road_17"],
        lat=NODES_BY_ID["cau_quang_trung"].lat,
        lon=NODES_BY_ID["cau_quang_trung"].lon,
        direction_note="Tuyến vành đai nâng cao vượt Sông Cần Thơ sang Hưng Phú",
    ),
    RoadCameraStation(
        id="cam_cmt8_binh_thuy",
        name="CAM 08 · Cách Mạng Tháng 8 (Bình Thủy)",
        street_label="CMT8 × Lê Hồng Phong (Công viên Bình Thủy)",
        district="binh_thuy",
        district_name="Bình Thủy",
        node_id="cmt8_binh_thuy",
        road_ids=["road_38", "road_39", "road_40"],
        lat=NODES_BY_ID["cmt8_binh_thuy"].lat,
        lon=NODES_BY_ID["cmt8_binh_thuy"].lon,
        direction_note="Theo dõi triều tràn từ Rạch Bình Thủy lên mặt đường CMT8",
    ),
]


def _load_configured_ioc_feeds() -> Dict[str, str]:
    """Load optional external IOC snapshot/stream URLs keyed by camera station ID."""
    raw = os.environ.get("IOC_CAMERA_FEEDS_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {
                str(k): str(v)
                for k, v in parsed.items()
                if isinstance(v, str) and v.startswith(("https://", "http://"))
            }
    except Exception:
        pass
    return {}


def get_cameras_payload(
    road_depths: Dict[str, float],
    incidents_data: Dict,
    weather_hub: Dict,
) -> Dict:
    """Return enriched state for all 8 traffic cameras combining IOC config, community photos & AI hydrology."""
    ioc_feeds = _load_configured_ioc_feeds()
    feed_items = incidents_data.get("social_feed") or incidents_data.get("master_incidents") or []
    roads_by_id = {seg.id: seg for seg in ROAD_SEGMENTS}

    astronomy = weather_hub.get("astronomy_and_tide", {})
    curr = weather_hub.get("current", {})
    tide_m = float(astronomy.get("current_tide_m", 1.45))
    rain_mm = float(curr.get("precip_mm", 0.0))

    stations: List[Dict] = []
    for cam in CANTHO_CAMERAS:
        linked_depths = [float(road_depths.get(rid, 0.0)) for rid in cam.road_ids]
        max_depth = round(max(linked_depths, default=0.0), 1)
        severity = "danger" if max_depth >= 18.0 else ("warn" if max_depth >= 8.0 else "safe")

        # Match newest community post / photo within 350m of this camera junction
        nearby_photo = ""
        nearby_post = None
        for post in feed_items:
            d_m = haversine_m(cam.lat, cam.lon, float(post.get("lat", 0.0)), float(post.get("lon", 0.0)))
            if d_m <= 350.0:
                if post.get("photo_data") and not nearby_photo:
                    nearby_photo = post["photo_data"]
                    nearby_post = post
                    break
                if nearby_post is None:
                    nearby_post = post

        ioc_url = ioc_feeds.get(cam.id, "")
        street_view_url = f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={cam.lat},{cam.lon}"
        linked_road_names = [roads_by_id[rid].name.split("(")[0].strip() for rid in cam.road_ids if rid in roads_by_id]

        stations.append(
            {
                **asdict(cam),
                "ai_max_depth_cm": max_depth,
                "severity": severity,
                "tide_level_m": round(tide_m, 2),
                "rain_mm_h": round(rain_mm, 1),
                "ioc_feed_url": ioc_url,
                "ioc_status": "connected" if ioc_url else "awaiting_ioc_api",
                "latest_community_photo": nearby_photo,
                "latest_community_note": (nearby_post.get("descriptions") or [""])[0] if nearby_post else "",
                "latest_community_time": nearby_post.get("latest_timestamp", "") if nearby_post else "",
                "latest_community_author": nearby_post.get("reporter_name", "") if nearby_post else "",
                "linked_road_names": linked_road_names,
                "google_street_view_url": street_view_url,
            }
        )

    return {
        "total": len(stations),
        "ioc_connected_count": sum(1 for st in stations if st["ioc_status"] == "connected"),
        "ioc_notice": (
            "Đang chờ kết nối luồng camera công khai từ Trung tâm IOC Cần Thơ. "
            "Hiện hiển thị ảnh hiện trường mới nhất do cộng đồng gửi tại đúng ngã tư, thông số ngập AI và Google Street View."
        ),
        "cameras": stations,
    }

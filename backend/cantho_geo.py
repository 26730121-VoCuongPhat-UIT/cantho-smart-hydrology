"""Can Tho geospatial data model: intersections, road network, rivers, and H3-style hex grid."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple

# Earth radius in meters for Haversine calculations
EARTH_RADIUS_M = 6_371_000.0

# Major water bodies in Can Tho (approximate centerlines as polylines of (lat, lon))
WATER_BODIES: Dict[str, List[Tuple[float, float]]] = {
    "Song Hau": [
        (10.0780, 105.7620),
        (10.0620, 105.7780),
        (10.0480, 105.7920),
        (10.0365, 105.7915),
        (10.0220, 105.7980),
        (10.0050, 105.8050),
    ],
    "Song Can Tho": [
        (10.0355, 105.7895),
        (10.0250, 105.7845),
        (10.0155, 105.7760),
        (10.0038, 105.7634),
        (9.9960, 105.7520),
    ],
    "Rach Cai Khe": [
        (10.0490, 105.7890),
        (10.0445, 105.7825),
        (10.0410, 105.7755),
        (10.0385, 105.7690),
    ],
    "Ho Bun Xang": [
        (10.0348, 105.7672),
        (10.0342, 105.7695),
        (10.0332, 105.7685),
    ],
    "Rach Binh Thuy": [
        (10.0650, 105.7680),
        (10.0568, 105.7612),
        (10.0500, 105.7550),
    ],
}


@dataclass(frozen=True)
class DistrictProfile:
    """Represents one of the 6 key administrative districts/counties in Can Tho City."""

    id: str
    name: str
    short_name: str
    lat: float
    lon: float
    elevation_m: float
    area_km2: float
    population: int
    zone_character: str
    flood_risk_level: str
    landmarks: List[str]
    temp_offset_c: float = 0.0
    humidity_offset_pct: int = 0
    aqi_offset: int = 0


CANTHO_DISTRICTS: List[DistrictProfile] = [
    DistrictProfile(
        id="ninh_kieu",
        name="Quận Ninh Kiều",
        short_name="Ninh Kiều",
        lat=10.0333,
        lon=105.7833,
        elevation_m=1.35,
        area_km2=29.2,
        population=284_000,
        zone_character="Trung tâm hành chính, thương mại & giáo dục (Đô thị lõi)",
        flood_risk_level="Cao (Rốn trũng Mậu Thân - Hồ Bún Xáng & ven Bến Ninh Kiều)",
        landmarks=["Bến Ninh Kiều", "ĐH Cần Thơ Khu II", "Hồ Bún Xáng", "Đại lộ Hòa Bình"],
        temp_offset_c=0.2,
        humidity_offset_pct=-1,
        aqi_offset=4,
    ),
    DistrictProfile(
        id="binh_thuy",
        name="Quận Bình Thủy",
        short_name="Bình Thủy",
        lat=10.0625,
        lon=105.7418,
        elevation_m=1.52,
        area_km2=70.6,
        population=146_000,
        zone_character="Cửa ngõ hàng không, di sản văn hóa & ven sông Hậu",
        flood_risk_level="Trung bình - Cao (Trục CMT8 & Rạch Bình Thủy)",
        landmarks=["Cảng HKQT Cần Thơ", "Nhà cổ Bình Thủy", "Đình Bình Thủy", "Đường CMT8"],
        temp_offset_c=0.1,
        humidity_offset_pct=0,
        aqi_offset=2,
    ),
    DistrictProfile(
        id="cai_rang",
        name="Quận Cái Răng",
        short_name="Cái Răng",
        lat=10.0038,
        lon=105.7634,
        elevation_m=1.48,
        area_km2=67.8,
        population=112_000,
        zone_character="Đô thị mới Nam Cần Thơ, cảng sông & du lịch chợ nổi",
        flood_risk_level="Trung bình (Ven sông Cần Thơ & Chợ nổi Cái Răng)",
        landmarks=["Chợ nổi Cái Răng", "KDC Hưng Phú", "Cầu Cần Thơ", "Cầu Quang Trung"],
        temp_offset_c=0.0,
        humidity_offset_pct=2,
        aqi_offset=-1,
    ),
    DistrictProfile(
        id="o_mon",
        name="Quận Ô Môn",
        short_name="Ô Môn",
        lat=10.1147,
        lon=105.6264,
        elevation_m=1.78,
        area_km2=131.9,
        population=132_000,
        zone_character="Trung tâm công nghiệp - năng lượng phía Tây Bắc",
        flood_risk_level="Thấp - Trung bình (Ven sông Hậu & KCN Trà Nóc)",
        landmarks=["Trung tâm Nhiệt điện Ô Môn", "KCN Trà Nóc II", "Chợ Ô Môn"],
        temp_offset_c=0.4,
        humidity_offset_pct=-2,
        aqi_offset=7,
    ),
    DistrictProfile(
        id="thot_not",
        name="Quận Thốt Nốt",
        short_name="Thốt Nốt",
        lat=10.2708,
        lon=105.5342,
        elevation_m=1.85,
        area_km2=121.0,
        population=162_000,
        zone_character="Đô thị thương mại lúa gạo & thủy sản đầu nguồn sông Hậu",
        flood_risk_level="Thấp - Trung bình (Đầu nguồn đón lũ thượng nguồn)",
        landmarks=["Vườn cò Bằng Lăng", "Cầu Vàm Cống", "Cụm CN Thốt Nốt"],
        temp_offset_c=0.5,
        humidity_offset_pct=-2,
        aqi_offset=3,
    ),
    DistrictProfile(
        id="phong_dien",
        name="Huyện Phong Điền",
        short_name="Phong Điền",
        lat=9.9972,
        lon=105.6656,
        elevation_m=1.62,
        area_km2=125.4,
        population=104_000,
        zone_character="Đô thị sinh thái miệt vườn, kênh rạch tự nhiên & du lịch xanh",
        flood_risk_level="Thấp (Mạng lưới sông rạch vành đai tiêu thoát tốt)",
        landmarks=["Làng du lịch Mỹ Khánh", "Thiền viện Trúc Lâm Phương Nam", "Chợ nổi Phong Điền"],
        temp_offset_c=-0.5,
        humidity_offset_pct=4,
        aqi_offset=-12,
    ),
]

DISTRICTS_BY_ID: Dict[str, DistrictProfile] = {d.id: d for d in CANTHO_DISTRICTS}


@dataclass(frozen=True)
class GeoNode:
    """Represents a real intersection or landmark in Can Tho City."""

    id: str
    name: str
    district: str
    lat: float
    lon: float
    elevation_m: float
    drainage_capacity: float  # 0.2 (poor) to 1.0 (modern box culvert)
    zone_type: str  # 'riverside' | 'urban_basin' | 'elevated_arterial'


@dataclass(frozen=True)
class RoadSegment:
    """Represents an undirected road segment connecting two GeoNodes."""

    id: str
    name: str
    u: str
    v: str
    length_m: float
    base_speed_kmh: float
    road_elevation_m: float
    drainage_factor: float  # 0.3 to 1.0
    is_flood_hotspot: bool = False
    coords: Tuple[Tuple[float, float], ...] = ()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance in meters between two WGS84 coordinates."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _point_to_segment_distance_m(
    plat: float, plon: float, alat: float, alon: float, blat: float, blon: float
) -> float:
    """Approximate minimum distance in meters from point P to segment AB using local equirectangular projection."""
    cos_lat = math.cos(math.radians(plat))
    m_per_deg_lat = 111_132.0
    m_per_deg_lon = 111_320.0 * cos_lat

    px, py = plon * m_per_deg_lon, plat * m_per_deg_lat
    ax, ay = alon * m_per_deg_lon, alat * m_per_deg_lat
    bx, by = blon * m_per_deg_lon, blat * m_per_deg_lat

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return math.hypot(px - ax, py - ay)

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def distance_to_nearest_water_m(lat: float, lon: float) -> float:
    """Return shortest distance in meters from a coordinate to any major Can Tho river or canal."""
    min_dist = float("inf")
    for polyline in WATER_BODIES.values():
        for i in range(len(polyline) - 1):
            a_lat, a_lon = polyline[i]
            b_lat, b_lon = polyline[i + 1]
            d = _point_to_segment_distance_m(lat, lon, a_lat, a_lon, b_lat, b_lon)
            if d < min_dist:
                min_dist = d
    return min_dist


# 26 Real Intersections / Landmarks across Ninh Kieu, Binh Thuy, and Cai Rang
# Coordinates verified directly against OpenStreetMap intersections & key POIs
CANTHO_NODES: List[GeoNode] = [
    GeoNode("dhct_khu2", "ĐH Cần Thơ Khu II (Cổng A · Đường 3/2)", "Ninh Kiều", 10.031108, 105.773046, 1.72, 0.72, "elevated_arterial"),
    GeoNode("ngatu_32_mauthan", "Ngã tư 3/2 - Mậu Thân", "Ninh Kiều", 10.032653, 105.774506, 1.06, 0.35, "urban_basin"),
    GeoNode("mauthan_thd", "Mậu Thân - Nguyễn Văn Cừ (Cầu Rạch Ngỗng)", "Ninh Kiều", 10.039613, 105.769250, 0.96, 0.30, "urban_basin"),
    GeoNode("ho_bun_xang", "Khu vực Hồ Bún Xáng (Hẻm 51 · Đường 3/2)", "Ninh Kiều", 10.029424, 105.762356, 0.92, 0.28, "urban_basin"),
    GeoNode("dh_y_duoc", "ĐH Y Dược Cần Thơ (Nguyễn Văn Cừ)", "Ninh Kiều", 10.034105, 105.755409, 1.84, 0.78, "elevated_arterial"),
    GeoNode("nvc_32", "Nguyễn Văn Cừ - Nguyễn Văn Linh", "Ninh Kiều", 10.030629, 105.752089, 1.56, 0.65, "elevated_arterial"),
    GeoNode("cau_dau_sau", "Cầu Đầu Sấu (3/2 - QL91B)", "Ninh Kiều", 10.012699, 105.754943, 1.98, 0.82, "elevated_arterial"),
    GeoNode("tran_van_hoai", "Trần Văn Hoài - Đường 30/4", "Ninh Kiều", 10.024736, 105.774154, 1.02, 0.34, "urban_basin"),
    GeoNode("cau_hung_loi", "Nút giao Cầu Hưng Lợi (NVL - 30/4)", "Ninh Kiều", 10.019668, 105.768567, 2.18, 0.88, "elevated_arterial"),
    GeoNode("nvl_tran_hoang_na", "Nguyễn Văn Linh - Đường 3 Tháng 2", "Ninh Kiều", 10.021440, 105.764712, 2.02, 0.85, "elevated_arterial"),
    GeoNode("nvl_32", "BV Đa khoa TW (Nguyễn Văn Linh)", "Ninh Kiều", 10.027954, 105.755520, 1.92, 0.80, "elevated_arterial"),
    GeoNode("ben_ninh_kieu", "Bến Ninh Kiều (Hai Bà Trưng)", "Ninh Kiều", 10.033663, 105.788349, 1.58, 0.65, "riverside"),
    GeoNode("hoa_binh_lhp", "Đại lộ Hòa Bình (CV Lưu Hữu Phước)", "Ninh Kiều", 10.031646, 105.782084, 1.86, 0.78, "elevated_arterial"),
    GeoNode("ly_tu_trong", "Lý Tự Trọng - Trần Hưng Đạo", "Ninh Kiều", 10.035297, 105.777151, 1.08, 0.36, "urban_basin"),
    GeoNode("huynh_cuong", "Đường Huỳnh Cương (Hồ Xáng Thổi)", "Ninh Kiều", 10.035470, 105.780972, 0.98, 0.32, "riverside"),
    GeoNode("chau_van_liem", "Châu Văn Liêm - Hai Bà Trưng", "Ninh Kiều", 10.030761, 105.787834, 1.12, 0.38, "riverside"),
    GeoNode("vong_xoay_hung_vuong", "Vòng xoay Hùng Vương - CMT8", "Ninh Kiều", 10.045390, 105.779906, 1.24, 0.48, "riverside"),
    GeoNode("cai_khe_tttm", "TTTM Cái Khế (Trần Phú - Lê Lợi)", "Ninh Kiều", 10.050311, 105.787005, 1.04, 0.36, "riverside"),
    GeoNode("cmt8_binh_thuy", "Cách Mạng Tháng 8 (Bình Thủy)", "Bình Thủy", 10.062638, 105.762825, 0.99, 0.32, "riverside"),
    GeoNode("san_bay_can_tho", "Cảng HKQT Cần Thơ (Võ Văn Kiệt)", "Bình Thủy", 10.077499, 105.714172, 2.42, 0.92, "elevated_arterial"),
    GeoNode("vvk_mau_than", "LOTTE Mart Cần Thơ (Đường Mậu Thân)", "Bình Thủy", 10.042184, 105.766645, 1.78, 0.76, "elevated_arterial"),
    GeoNode("vvk_nvc", "Vòng xoay Võ Văn Kiệt - Sân bay", "Bình Thủy", 10.053078, 105.750868, 1.90, 0.82, "elevated_arterial"),
    GeoNode("cau_quang_trung", "Cầu Quang Trung (Quang Trung - 30/4)", "Ninh Kiều", 10.029393, 105.779807, 2.15, 0.86, "elevated_arterial"),
    GeoNode("kdc_hung_phu", "KDC Hưng Phú (Cái Răng)", "Cái Răng", 10.018121, 105.791984, 1.96, 0.82, "elevated_arterial"),
    GeoNode("cho_noi_cai_rang", "Cầu Cái Răng (Chợ nổi Cái Răng)", "Cái Răng", 10.003873, 105.763251, 1.32, 0.52, "riverside"),
    GeoNode("tran_hoang_na_32", "Ngã tư Trần Hoàng Na - Đường 3/2", "Ninh Kiều", 10.016271, 105.760264, 1.88, 0.82, "elevated_arterial"),
]

NODES_BY_ID: Dict[str, GeoNode] = {n.id: n for n in CANTHO_NODES}

# 42 realistic road edges forming a connected network with both flood-prone corridors and elevated bypasses
_RAW_ROADS: List[Tuple[str, str, str, float, float, float, bool]] = [
    # (u, v, street_name, base_speed_kmh, override_elev_m_or_None, drainage_factor, is_flood_hotspot)
    ("dhct_khu2", "ngatu_32_mauthan", "Đường 3 Tháng 2 (Cổng A -> Mậu Thân)", 35.0, 1.15, 0.42, True),
    ("ngatu_32_mauthan", "mauthan_thd", "Đường Mậu Thân (Ngã tư 3/2 -> Cầu Rạch Ngỗng)", 32.0, 0.96, 0.28, True),
    ("ngatu_32_mauthan", "ly_tu_trong", "Đường Trần Hưng Đạo (Mậu Thân -> Lý Tự Trọng)", 30.0, 1.02, 0.32, True),
    ("ly_tu_trong", "hoa_binh_lhp", "Đường Lý Tự Trọng -> Đại lộ Hòa Bình", 30.0, 1.18, 0.45, True),
    ("hoa_binh_lhp", "ben_ninh_kieu", "Đại lộ Hòa Bình -> Bến Ninh Kiều", 32.0, 1.82, 0.78, False),
    ("ngatu_32_mauthan", "tran_van_hoai", "Đường 3/2 -> Trần Văn Hoài", 32.0, 1.04, 0.34, True),
    ("tran_van_hoai", "ly_tu_trong", "Đường 30/4 -> Lý Tự Trọng", 34.0, 1.12, 0.40, True),
    ("tran_van_hoai", "cau_quang_trung", "Đường 30 Tháng 4 (Đoạn cao Quang Trung)", 40.0, 1.82, 0.76, False),
    ("cau_quang_trung", "hoa_binh_lhp", "Đường Quang Trung -> Đại lộ Hòa Bình (Trục nâng cao)", 42.0, 1.94, 0.84, False),
    ("dhct_khu2", "tran_hoang_na_32", "Đường 3 Tháng 2 (Đoạn ĐHCT -> Trần Hoàng Na)", 40.0, 1.86, 0.80, False),
    ("tran_hoang_na_32", "nvc_32", "Đường 3 Tháng 2 (Trần Hoàng Na -> Nguyễn Văn Cừ)", 40.0, 1.75, 0.75, False),
    ("nvc_32", "cau_dau_sau", "Đường 3 Tháng 2 (Nguyễn Văn Cừ -> Cầu Đầu Sấu)", 42.0, 1.88, 0.80, False),
    ("cau_dau_sau", "nvl_32", "Quốc lộ 91B (Cầu Đầu Sấu -> BV Đa khoa TW)", 45.0, 1.95, 0.85, False),
    ("nvl_32", "nvl_tran_hoang_na", "Đường Nguyễn Văn Linh (BV ĐKTW -> Đường 3/2)", 48.0, 2.02, 0.86, False),
    ("tran_hoang_na_32", "nvl_tran_hoang_na", "Đại lộ Trần Hoàng Na / NVL (Trục cao)", 46.0, 1.98, 0.86, False),
    ("nvl_tran_hoang_na", "cau_hung_loi", "Đường Nguyễn Văn Linh -> Cầu Hưng Lợi", 48.0, 2.12, 0.90, False),
    ("cau_hung_loi", "cau_quang_trung", "Đường 30/4 -> Cầu Quang Trung (Vành đai cao)", 44.0, 2.15, 0.88, False),
    ("cau_quang_trung", "kdc_hung_phu", "Cầu Quang Trung -> KDC Hưng Phú", 48.0, 2.05, 0.85, False),
    ("cau_hung_loi", "kdc_hung_phu", "Đường dẫn Cầu Hưng Lợi -> KDC Hưng Phú", 45.0, 2.02, 0.84, False),
    ("cau_hung_loi", "cho_noi_cai_rang", "Quốc lộ 1A -> Cầu Cái Răng", 45.0, 1.58, 0.62, False),
    ("cau_dau_sau", "cho_noi_cai_rang", "Tỉnh lộ 923 -> Chợ nổi Cái Răng", 38.0, 1.42, 0.55, False),
    ("dhct_khu2", "ho_bun_xang", "Hẻm 51 -> Khu vực Hồ Bún Xáng", 26.0, 0.94, 0.26, True),
    ("ho_bun_xang", "nvc_32", "Đường Nguyễn Văn Cừ (Hồ Bún Xáng -> NVL)", 34.0, 1.10, 0.38, True),
    ("ho_bun_xang", "mauthan_thd", "Đường Hồ Bún Xáng -> Cầu Rạch Ngỗng (Mậu Thân)", 28.0, 0.96, 0.30, True),
    ("ho_bun_xang", "dh_y_duoc", "Đường Nguyễn Văn Cừ (Hồ Bún Xáng -> ĐH Y Dược)", 38.0, 1.48, 0.60, False),
    ("nvc_32", "dh_y_duoc", "Đường Nguyễn Văn Cừ -> ĐH Y Dược", 42.0, 1.76, 0.76, False),
    ("dh_y_duoc", "vvk_nvc", "Đường Nguyễn Văn Cừ -> Vòng xoay Võ Văn Kiệt", 46.0, 1.88, 0.82, False),
    ("mauthan_thd", "huynh_cuong", "Đường Hoàng Văn Thụ -> Huỳnh Cương", 28.0, 0.98, 0.30, True),
    ("huynh_cuong", "chau_van_liem", "Đường Huỳnh Cương -> Châu Văn Liêm", 26.0, 1.02, 0.34, True),
    ("chau_van_liem", "ben_ninh_kieu", "Đường Hai Bà Trưng -> Bến Ninh Kiều", 28.0, 1.18, 0.42, True),
    ("hoa_binh_lhp", "chau_van_liem", "Đường Hòa Bình -> Châu Văn Liêm", 28.0, 1.24, 0.48, True),
    ("mauthan_thd", "vvk_mau_than", "Đường Mậu Thân (Cầu Rạch Ngỗng -> Võ Văn Kiệt)", 38.0, 1.45, 0.58, False),
    ("vvk_mau_than", "vong_xoay_hung_vuong", "Đường Nguyễn Văn Cừ / Hùng Vương -> Vòng xoay", 36.0, 1.38, 0.52, True),
    ("huynh_cuong", "vong_xoay_hung_vuong", "Đường Trần Việt Châu -> Vòng xoay Hùng Vương", 32.0, 1.14, 0.40, True),
    ("vong_xoay_hung_vuong", "cai_khe_tttm", "Đường Trần Phú -> TTTM Cái Khế", 34.0, 1.06, 0.35, True),
    ("cai_khe_tttm", "ben_ninh_kieu", "Đường Lê Lợi -> Cầu Ninh Kiều -> Bến Ninh Kiều", 32.0, 1.22, 0.46, True),
    ("vvk_mau_than", "vvk_nvc", "Đại lộ Võ Văn Kiệt (Mậu Thân -> Nguyễn Văn Cừ)", 52.0, 1.92, 0.86, False),
    ("vvk_nvc", "san_bay_can_tho", "Đại lộ Võ Văn Kiệt -> Sân bay Cần Thơ", 55.0, 2.18, 0.92, False),
    ("vong_xoay_hung_vuong", "cmt8_binh_thuy", "Đường Cách Mạng Tháng 8 (Đoạn trũng Bình Thủy)", 36.0, 1.01, 0.32, True),
    ("cmt8_binh_thuy", "vvk_nvc", "Đường Lê Hồng Phong -> Võ Văn Kiệt", 40.0, 1.55, 0.64, False),
    ("cmt8_binh_thuy", "san_bay_can_tho", "Đường Lê Hồng Phong -> Sân bay Cần Thơ", 44.0, 1.85, 0.78, False),
    ("tran_hoang_na_32", "tran_van_hoai", "Đường Tầm Vu / Bờ kè Tham Tướng -> Trần Văn Hoài", 34.0, 1.35, 0.50, True),
]

def _load_cached_road_geometries() -> Dict[str, List[List[float]]]:
    """Load precomputed OSM street-aligned coordinates if present on disk."""
    geom_path = Path(__file__).resolve().parent.parent / "data" / "road_geometries.json"
    if not geom_path.exists():
        return {}
    try:
        raw = json.loads(geom_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def polyline_length_m(coords: Tuple[Tuple[float, float], ...] | List[List[float]]) -> float:
    """Compute true street-aligned distance in meters along a polyline."""
    if len(coords) < 2:
        return 0.0
    total = 0.0
    for i in range(len(coords) - 1):
        total += haversine_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
    return round(total, 1)


_ROAD_GEOMETRY_MAP: Dict[str, List[List[float]]] = _load_cached_road_geometries()

ROAD_SEGMENTS: List[RoadSegment] = []
for idx, (u, v, name, speed, elev, drain, hotspot) in enumerate(_RAW_ROADS):
    seg_id = f"road_{idx:02d}"
    nu, nv = NODES_BY_ID[u], NODES_BY_ID[v]
    raw_pts = _ROAD_GEOMETRY_MAP.get(seg_id, [[nu.lat, nu.lon], [nv.lat, nv.lon]])
    seg_coords = tuple((float(p[0]), float(p[1])) for p in raw_pts)
    length = (
        polyline_length_m(seg_coords)
        if len(seg_coords) >= 3
        else round(haversine_m(nu.lat, nu.lon, nv.lat, nv.lon) * 1.12, 1)
    )
    ROAD_SEGMENTS.append(
        RoadSegment(
            id=seg_id,
            name=name,
            u=u,
            v=v,
            length_m=length,
            base_speed_kmh=speed,
            road_elevation_m=elev,
            drainage_factor=drain,
            is_flood_hotspot=hotspot,
            coords=seg_coords,
        )
    )


@dataclass
class HexCell:
    """Represents a hexagonal spatial cell (Uber H3 Resolution 9 equivalent) over Can Tho."""

    hex_id: str
    center_lat: float
    center_lon: float
    vertices: List[List[float]]  # [[lat, lon], ...]
    elevation_m: float
    dist_water_m: float
    twi: float  # Topographic Wetness Index
    drainage_capacity: float
    zone_type: str
    nearest_landmark: str


def _idw_interpolate_node_attributes(lat: float, lon: float) -> Tuple[float, float, str, str]:
    """Inverse-distance weighting from Can Tho known survey nodes for DEM and drainage."""
    weights = []
    nearest_node = CANTHO_NODES[0]
    nearest_dist = float("inf")

    for node in CANTHO_NODES:
        d = max(40.0, haversine_m(lat, lon, node.lat, node.lon))
        if d < nearest_dist:
            nearest_dist = d
            nearest_node = node
        weights.append((1.0 / (d ** 2.1), node))

    total_w = sum(w for w, _ in weights)
    elev = sum(w * n.elevation_m for w, n in weights) / total_w
    drain = sum(w * n.drainage_capacity for w, n in weights) / total_w
    return round(elev, 3), round(drain, 3), nearest_node.zone_type, nearest_node.name


def idw_elevation_and_drainage(lat: float, lon: float) -> Tuple[float, float, str, str]:
    """Public helper returning IDW-interpolated (elevation_m, drainage_capacity, zone_type, nearest_landmark)."""
    return _idw_interpolate_node_attributes(lat, lon)


def nearest_nodes_for_coord(lat: float, lon: float, k: int = 3) -> List[Tuple[GeoNode, float]]:
    """Return top-k closest Can Tho road-graph intersections sorted by Haversine distance (m)."""
    ranked = [(n, haversine_m(lat, lon, n.lat, n.lon)) for n in CANTHO_NODES]
    ranked.sort(key=lambda item: item[1])
    return ranked[: max(1, k)]


def generate_cantho_hex_grid(
    lat_min: float = 10.002,
    lat_max: float = 10.068,
    lon_min: float = 105.735,
    lon_max: float = 105.798,
    radius_m: float = 280.0,
) -> List[HexCell]:
    """Generate a uniform pointy-top hexagonal spatial tessellation covering central Can Tho."""
    m_per_deg_lat = 111_132.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(10.035))

    d_lat = radius_m / m_per_deg_lat
    d_lon = radius_m / m_per_deg_lon

    horiz_step = math.sqrt(3.0) * d_lon
    vert_step = 1.5 * d_lat

    cells: List[HexCell] = []
    row = 0
    lat = lat_min
    while lat <= lat_max:
        lon_offset = (horiz_step / 2.0) if (row % 2 == 1) else 0.0
        col = 0
        lon = lon_min + lon_offset
        while lon <= lon_max:
            # Filter out cells too far into rural agricultural outskirts or mid-river
            dist_to_city_node = min(haversine_m(lat, lon, n.lat, n.lon) for n in CANTHO_NODES)
            if dist_to_city_node <= 1650.0:
                elev, drain, zone_type, landmark = _idw_interpolate_node_attributes(lat, lon)
                dist_water = round(distance_to_nearest_water_m(lat, lon), 1)
                # Topographic Wetness Index proxy: higher when low elevation & close to drainage basin
                twi = round(
                    max(2.0, min(14.0, 9.5 - 2.8 * (elev - 1.0) + 2.2 * math.exp(-dist_water / 550.0))),
                    2,
                )

                vertices: List[List[float]] = []
                for k in range(6):
                    angle = math.radians(60 * k + 30)  # pointy-top hexagon
                    v_lat = round(lat + d_lat * math.sin(angle), 6)
                    v_lon = round(lon + d_lon * math.cos(angle), 6)
                    vertices.append([v_lat, v_lon])

                cells.append(
                    HexCell(
                        hex_id=f"89658a{row:02d}{col:02d}fffff",
                        center_lat=round(lat, 6),
                        center_lon=round(lon, 6),
                        vertices=vertices,
                        elevation_m=elev,
                        dist_water_m=dist_water,
                        twi=twi,
                        drainage_capacity=drain,
                        zone_type=zone_type,
                        nearest_landmark=landmark,
                    )
                )
            lon += horiz_step
            col += 1
        lat += vert_step
        row += 1

    return cells


CANTHO_HEX_GRID: List[HexCell] = generate_cantho_hex_grid()


def get_static_geo_payload() -> Dict:
    """Serialize static Can Tho nodes, roads, districts, water bodies, and hex grid geometry for API clients."""
    roads_payload = []
    for r in ROAD_SEGMENTS:
        d = asdict(r)
        d["coords"] = [[p[0], p[1]] for p in r.coords]
        roads_payload.append(d)

    return {
        "nodes": [asdict(n) for n in CANTHO_NODES],
        "roads": roads_payload,
        "districts": [asdict(d) for d in CANTHO_DISTRICTS],
        "water_bodies": {name: [[p[0], p[1]] for p in pts] for name, pts in WATER_BODIES.items()},
        "hex_count": len(CANTHO_HEX_GRID),
    }

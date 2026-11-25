"""Build compressed 1,600+ Can Tho real OSM POI search database (data/cantho_pois.json.gz).

Combines curated VIP entrance coordinates for flagship Can Tho landmarks
(exact front-gate points facing the road rather than far-away intersections)
with full OpenStreetMap POIs fetched from Overpass across Ninh Kieu, Binh Thuy,
and Cai Rang (covering DH FPT, DH Nam Can Tho, Ben xe Trung tam, GO!/Big C, etc.).
"""

from __future__ import annotations

import gzip
import json
import sys
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.cantho_geo import CANTHO_NODES, haversine_m

OUTPUT_PATH = PROJECT_ROOT / "data" / "cantho_pois.json.gz"
USER_AGENT = "CanThoSmartCity/3.1 (CanTho Flood Map)"

OVERPASS_MIRRORS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

OVERPASS_POI_QUERY = """[out:json][timeout:35];
(
  nwr["name"]["amenity"~"^(university|college|school|hospital|clinic|marketplace|bus_station|bank|cinema|library|theatre|community_centre|townhall|police|post_office|fuel|cafe|restaurant|fast_food|pharmacy)$"](9.982,105.702,10.088,105.815);
  nwr["name"]["shop"~"^(mall|supermarket|department_store|convenience|electronics)$"](9.982,105.702,10.088,105.815);
  nwr["name"]["tourism"~"^(attraction|hotel|museum|viewpoint)$"](9.982,105.702,10.088,105.815);
  nwr["name"]["leisure"~"^(park|stadium|sports_centre)$"](9.982,105.702,10.088,105.815);
  nwr["name"]["aeroway"~"^(aerodrome|terminal)$"](9.982,105.702,10.088,105.815);
  nwr["name"]["building"~"^(commercial|retail|university|hospital|public|civic)$"](9.982,105.702,10.088,105.815);
);
out center;"""

VIP_CANTHO_POIS: List[Dict] = [
    {
        "n": "LOTTE Mart Cần Thơ",
        "la": 10.042184,
        "lo": 105.766645,
        "c": "mall",
        "a": "84 Mậu Thân, An Hòa, Ninh Kiều",
        "al": ["lotte", "lotte mart", "lotte mart can tho", "sieu thi lotte"],
        "nid": "vvk_mau_than",
        "vip": 1,
    },
    {
        "n": "Vincom Plaza Xuân Khánh",
        "la": 10.024396,
        "lo": 105.774672,
        "c": "mall",
        "a": "209 Đường 30 Tháng 4, Xuân Khánh, Ninh Kiều",
        "al": ["vincom", "vincom xuan khanh", "vincom 30/4"],
        "vip": 1,
    },
    {
        "n": "Vincom Plaza Hùng Vương",
        "la": 10.045593,
        "lo": 105.779352,
        "c": "mall",
        "a": "02 Hùng Vương, Thới Bình, Ninh Kiều",
        "al": ["vincom", "vincom hung vuong"],
        "vip": 1,
    },
    {
        "n": "Sense City Cần Thơ",
        "la": 10.034459,
        "lo": 105.785719,
        "c": "mall",
        "a": "01 Đại lộ Hòa Bình, Tân An, Ninh Kiều",
        "al": ["sense", "sense city", "sense city can tho", "coopmart hoa binh"],
        "vip": 1,
    },
    {
        "n": "GO! Cần Thơ (Big C)",
        "la": 10.014256,
        "lo": 105.784645,
        "c": "mall",
        "a": "Lô 1, KDC Hưng Phú 1, Cái Răng",
        "al": ["go", "go can tho", "big c", "bigc", "sieu thi big c"],
        "vip": 1,
    },
    {
        "n": "MM Mega Market Hưng Lợi",
        "la": 10.022969,
        "lo": 105.760855,
        "c": "supermarket",
        "a": "Quốc lộ 91B, Hưng Lợi, Ninh Kiều",
        "al": ["mega market", "mm mega", "metro can tho"],
        "vip": 1,
    },
    {
        "n": "Đại học Cần Thơ Khu II (Cổng A · Đường 3/2)",
        "la": 10.031108,
        "lo": 105.773046,
        "c": "university",
        "a": "Cổng A, Đường 3 Tháng 2, Xuân Khánh, Ninh Kiều",
        "al": ["dhct", "ctu", "dai hoc can tho", "dh can tho", "cong a", "dhct khu 2"],
        "nid": "dhct_khu2",
        "vip": 1,
    },
    {
        "n": "Đại học Cần Thơ Khu II (Cổng B)",
        "la": 10.029207,
        "lo": 105.771319,
        "c": "university",
        "a": "Cổng B, Đường 3 Tháng 2, Xuân Khánh, Ninh Kiều",
        "al": ["dhct cong b", "cong b dai hoc can tho"],
        "vip": 1,
    },
    {
        "n": "Đại học Cần Thơ Khu II (Cổng C)",
        "la": 10.027989,
        "lo": 105.770234,
        "c": "university",
        "a": "Cổng C, Đường 3 Tháng 2, Xuân Khánh, Ninh Kiều",
        "al": ["dhct cong c", "cong c dai hoc can tho"],
        "vip": 1,
    },
    {
        "n": "Đại học FPT Cần Thơ",
        "la": 10.011934,
        "lo": 105.731558,
        "c": "university",
        "a": "600 Nguyễn Văn Cừ nối dài, An Bình, Ninh Kiều",
        "al": ["dh fpt", "fpt", "fpt can tho", "fptu", "dai hoc fpt"],
        "vip": 1,
    },
    {
        "n": "Đại học Nam Cần Thơ (DNC)",
        "la": 10.005584,
        "lo": 105.722299,
        "c": "university",
        "a": "168 Nguyễn Văn Cừ nối dài, An Bình, Ninh Kiều",
        "al": ["dnc", "dh nam can tho", "nam can tho"],
        "vip": 1,
    },
    {
        "n": "Đại học Y Dược Cần Thơ (CTUMP)",
        "la": 10.034105,
        "lo": 105.755409,
        "c": "university",
        "a": "179 Nguyễn Văn Cừ, An Khánh, Ninh Kiều",
        "al": ["ctump", "dh y duoc", "y duoc can tho", "bv dai hoc y duoc"],
        "nid": "dh_y_duoc",
        "vip": 1,
    },
    {
        "n": "Bệnh viện Đa khoa Trung ương Cần Thơ",
        "la": 10.027954,
        "lo": 105.755520,
        "c": "hospital",
        "a": "315 Nguyễn Văn Linh, An Khánh, Ninh Kiều",
        "al": ["bv dktw", "bv trung uong", "da khoa trung uong", "bvtw"],
        "nid": "nvl_32",
        "vip": 1,
    },
    {
        "n": "Bệnh viện Nhi đồng TP. Cần Thơ",
        "la": 10.016745,
        "lo": 105.739495,
        "c": "hospital",
        "a": "345 Nguyễn Văn Cừ nối dài, An Bình, Ninh Kiều",
        "al": ["bv nhi dong", "nhi dong can tho", "nhi dong"],
        "vip": 1,
    },
    {
        "n": "Bệnh viện Đại học Nam Cần Thơ",
        "la": 10.001152,
        "lo": 105.768643,
        "c": "hospital",
        "a": "KDC Hồng Loan, Hưng Thạnh, Cái Răng",
        "al": ["bv dnc", "bv nam can tho"],
        "vip": 1,
    },
    {
        "n": "Bến xe Trung tâm TP. Cần Thơ",
        "la": 10.005344,
        "lo": 105.771312,
        "c": "bus_station",
        "a": "Quốc lộ 1A, Hưng Thạnh, Cái Răng",
        "al": ["ben xe", "ben xe moi", "ben xe trung tam", "ben xe can tho"],
        "vip": 1,
    },
    {
        "n": "Cảng HKQT Cần Thơ (Sân bay Cần Thơ)",
        "la": 10.077499,
        "lo": 105.714172,
        "c": "aerodrome",
        "a": "179B Lê Hồng Phong / Võ Văn Kiệt, Bình Thủy",
        "al": ["san bay", "san bay can tho", "airport", "cang hkqt"],
        "nid": "san_bay_can_tho",
        "vip": 1,
    },
    {
        "n": "Bến Ninh Kiều (Hai Bà Trưng)",
        "la": 10.033663,
        "lo": 105.788349,
        "c": "attraction",
        "a": "Đường Hai Bà Trưng, Tân An, Ninh Kiều",
        "al": ["ben ninh kieu", "ninh kieu", "cau di bo ninh kieu"],
        "nid": "ben_ninh_kieu",
        "vip": 1,
    },
    {
        "n": "Chợ đêm Bến Ninh Kiều",
        "la": 10.031528,
        "lo": 105.787568,
        "c": "marketplace",
        "a": "Hai Bà Trưng × Nguyễn Thái Học, Ninh Kiều",
        "al": ["cho dem", "cho dem ninh kieu"],
        "vip": 1,
    },
    {
        "n": "Chợ Xuân Khánh",
        "la": 10.026707,
        "lo": 105.776916,
        "c": "marketplace",
        "a": "Đường 30 Tháng 4, Xuân Khánh, Ninh Kiều",
        "al": ["cho xuan khanh", "xuan khanh"],
        "vip": 1,
    },
    {
        "n": "Công viên Lưu Hữu Phước (Đại lộ Hòa Bình)",
        "la": 10.032438,
        "lo": 105.781359,
        "c": "park",
        "a": "Đại lộ Hòa Bình, An Lạc, Ninh Kiều",
        "al": ["luu huu phuoc", "cv luu huu phuoc", "hoa binh"],
        "nid": "hoa_binh_lhp",
        "vip": 1,
    },
    {
        "n": "Chợ nổi Cái Răng (Cầu Cái Răng)",
        "la": 10.003873,
        "lo": 105.763251,
        "c": "attraction",
        "a": "Cầu Cái Răng, Lê Bình, Cái Răng",
        "al": ["cho noi", "cho noi cai rang", "cau cai rang"],
        "nid": "cho_noi_cai_rang",
        "vip": 1,
    },
    {
        "n": "Khu vực Hồ Bún Xáng (Hẻm 51 · Đường 3/2)",
        "la": 10.029424,
        "lo": 105.762356,
        "c": "intersection",
        "a": "Hẻm 51 Đường 3/2, Xuân Khánh, Ninh Kiều",
        "al": ["ho bun xang", "hem 51", "hem 51 3/2"],
        "nid": "ho_bun_xang",
        "vip": 1,
    },
    {
        "n": "Hồ Xáng Thổi (Đường Huỳnh Cương)",
        "la": 10.035470,
        "lo": 105.780972,
        "c": "intersection",
        "a": "Đường Huỳnh Cương, An Cư, Ninh Kiều",
        "al": ["ho xang thoi", "huynh cuong"],
        "nid": "huynh_cuong",
        "vip": 1,
    },
    {
        "n": "TTTM Cái Khế (Trần Phú - Lê Lợi)",
        "la": 10.050311,
        "lo": 105.787005,
        "c": "mall",
        "a": "Trần Phú × Lê Lợi, Cái Khế, Ninh Kiều",
        "al": ["cai khe", "tttm cai khe", "cho cai khe"],
        "nid": "cai_khe_tttm",
        "vip": 1,
    },
    {
        "n": "Ngã tư 3/2 - Mậu Thân",
        "la": 10.032653,
        "lo": 105.774506,
        "c": "intersection",
        "a": "Đường 3 Tháng 2 × Mậu Thân, Ninh Kiều",
        "al": ["nga tu mau than", "3/2 mau than", "cam 01"],
        "nid": "ngatu_32_mauthan",
        "vip": 1,
    },
    {
        "n": "Cầu Rạch Ngỗng 1 (Đường Mậu Thân)",
        "la": 10.039613,
        "lo": 105.769250,
        "c": "intersection",
        "a": "Đường Mậu Thân, An Hòa, Ninh Kiều",
        "al": ["cau rach ngong", "rach ngong", "mau than nguyen van cu", "cam 02"],
        "nid": "mauthan_thd",
        "vip": 1,
    },
    {
        "n": "Ngã tư Võ Văn Kiệt - Mậu Thân",
        "la": 10.043607,
        "lo": 105.765292,
        "c": "intersection",
        "a": "Võ Văn Kiệt × Mậu Thân, An Hòa, Ninh Kiều",
        "al": ["nga tu vo van kiet", "vvk mau than"],
        "vip": 1,
    },
    {
        "n": "Vòng xoay Hùng Vương - CMT8",
        "la": 10.045390,
        "lo": 105.779906,
        "c": "intersection",
        "a": "Hùng Vương × Cách Mạng Tháng 8, Ninh Kiều",
        "al": ["vong xoay hung vuong", "hung vuong cmt8"],
        "nid": "vong_xoay_hung_vuong",
        "vip": 1,
    },
    {
        "n": "Châu Văn Liêm - Hai Bà Trưng",
        "la": 10.030761,
        "lo": 105.787834,
        "c": "intersection",
        "a": "Châu Văn Liêm × Hai Bà Trưng, Tân An, Ninh Kiều",
        "al": ["chau van liem", "hai ba trung"],
        "nid": "chau_van_liem",
        "vip": 1,
    },
    {
        "n": "Lý Tự Trọng - Trần Hưng Đạo",
        "la": 10.035297,
        "lo": 105.777151,
        "c": "intersection",
        "a": "Lý Tự Trọng × Trần Hưng Đạo, An Phú, Ninh Kiều",
        "al": ["ly tu trong", "tran hung dao"],
        "nid": "ly_tu_trong",
        "vip": 1,
    },
    {
        "n": "Cầu Quang Trung (Quang Trung - 30/4)",
        "la": 10.029393,
        "lo": 105.779807,
        "c": "intersection",
        "a": "Quang Trung × Đường 30/4, Ninh Kiều",
        "al": ["cau quang trung", "quang trung 30/4"],
        "nid": "cau_quang_trung",
        "vip": 1,
    },
]


def _norm(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower().strip()


def _fetch_overpass() -> Optional[Dict]:
    for url in OVERPASS_MIRRORS:
        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    data={"data": OVERPASS_POI_QUERY},
                    timeout=32,
                    headers={"User-Agent": USER_AGENT},
                )
                if resp.status_code == 200 and len(resp.content) > 10_000:
                    print(f"Fetched OSM POIs from {url} ({len(resp.content):,} B)", flush=True)
                    return resp.json()
                if resp.status_code == 429:
                    time.sleep(2.0 * (attempt + 1))
            except Exception:
                continue
    return None


def main() -> int:
    t0 = time.perf_counter()
    pois: List[Dict] = []
    seen_entries: List[Dict] = []

    def _is_dup(name_norm: str, lat: float, lon: float, radius_m: float = 85.0) -> bool:
        for existing in seen_entries:
            if abs(existing["la"] - lat) < 0.0015 and abs(existing["lo"] - lon) < 0.0015:
                if existing["_norm"] == name_norm and haversine_m(existing["la"], existing["lo"], lat, lon) <= radius_m:
                    return True
        return False

    # 1. Seed VIP curated entrance POIs first
    for vip in VIP_CANTHO_POIS:
        rec = dict(vip)
        rec["_norm"] = _norm(vip["n"])
        seen_entries.append(rec)
        pois.append(vip)

    # 2. Seed any remaining CANTHO_NODES not already covered by VIP list
    vip_nids = {p.get("nid") for p in VIP_CANTHO_POIS if p.get("nid")}
    for cn in CANTHO_NODES:
        if cn.id in vip_nids:
            continue
        rec = {
            "n": cn.name,
            "la": round(cn.lat, 6),
            "lo": round(cn.lon, 6),
            "c": "intersection",
            "a": f"{cn.district}, TP. Cần Thơ",
            "nid": cn.id,
            "vip": 1,
        }
        rec_chk = {**rec, "_norm": _norm(cn.name)}
        seen_entries.append(rec_chk)
        pois.append(rec)

    # 3. Fetch real OSM POIs from Overpass
    payload = _fetch_overpass()
    if payload:
        for el in payload.get("elements", []):
            tags = el.get("tags", {})
            nm = str(tags.get("name") or "").strip()
            if not nm or len(nm) < 2:
                continue
            lat = el.get("lat") if el.get("type") == "node" else (el.get("center") or {}).get("lat")
            lon = el.get("lon") if el.get("type") == "node" else (el.get("center") or {}).get("lon")
            if lat is None or lon is None:
                continue
            lat_f, lon_f = round(float(lat), 6), round(float(lon), 6)
            nm_norm = _norm(nm)
            if _is_dup(nm_norm, lat_f, lon_f):
                continue

            cat = (
                tags.get("shop")
                or tags.get("amenity")
                or tags.get("tourism")
                or tags.get("leisure")
                or tags.get("aeroway")
                or tags.get("building")
                or "poi"
            )
            street = str(tags.get("addr:street") or "").strip()
            housenum = str(tags.get("addr:housenumber") or "").strip()
            suburb = str(tags.get("addr:suburb") or tags.get("addr:district") or "").strip()
            parts = []
            if housenum and street:
                parts.append(f"{housenum} {street}")
            elif street:
                parts.append(street)
            if suburb:
                parts.append(suburb)
            addr = ", ".join(parts)

            entry: Dict = {"n": nm, "la": lat_f, "lo": lon_f, "c": str(cat)}
            if addr:
                entry["a"] = addr

            seen_entries.append({**entry, "_norm": nm_norm})
            pois.append(entry)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(
        json.dumps(pois, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        compresslevel=9,
    )
    OUTPUT_PATH.write_bytes(compressed)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(
        f"Saved {len(pois):,} POIs to {OUTPUT_PATH} ({len(compressed)/1024:.1f} KB gzip) in {elapsed_ms:.0f} ms",
        flush=True,
    )
    return 0 if len(pois) >= 1200 else 1


if __name__ == "__main__":
    raise SystemExit(main())

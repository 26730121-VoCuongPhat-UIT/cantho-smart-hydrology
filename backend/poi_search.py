"""Fast Accent-Insensitive Can Tho POI & Landmark Search Engine (1,600+ real OSM places + Photon fallback)."""

from __future__ import annotations

import gzip
import json
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

import requests

from backend.cantho_geo import CANTHO_NODES, haversine_m
from backend.security import sanitize_text

POIS_PATH = Path(__file__).resolve().parent.parent / "data" / "cantho_pois.json.gz"

CATEGORY_LABELS: Dict[str, str] = {
    "mall": "TTTM / Siêu thị",
    "supermarket": "Siêu thị",
    "department_store": "TTTM",
    "university": "Đại học",
    "college": "Cao đẳng / Học viện",
    "school": "Trường học",
    "hospital": "Bệnh viện",
    "clinic": "Phòng khám",
    "bus_station": "Bến xe",
    "aerodrome": "Sân bay",
    "terminal": "Nhà ga",
    "marketplace": "Chợ",
    "intersection": "Giao lộ",
    "park": "Công viên",
    "attraction": "Địa danh",
    "hotel": "Khách sạn",
    "bank": "Ngân hàng",
    "cafe": "Cà phê",
    "restaurant": "Nhà hàng",
    "fast_food": "Ăn uống",
    "pharmacy": "Nhà thuốc",
    "fuel": "Trạm xăng",
    "convenience": "Cửa hàng tiện lợi",
    "commercial": "Tòa nhà TM",
}

MAJOR_CATEGORIES = frozenset(
    {"mall", "supermarket", "department_store", "university", "hospital", "bus_station", "aerodrome", "marketplace", "attraction", "park"}
)


def normalize_vn(text: str) -> str:
    """Fold Vietnamese accents and lowercase for fast typo/diacritic-free matching."""
    cleaned = str(text or "").replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFD", cleaned)
    return " ".join(
        "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower().split()
    )


class CanThoPOISearchIndex:
    """In-memory index over 1,600+ curated & OSM Can Tho POIs with Photon/Nominatim fallback."""

    def __init__(self, pois_path: Path = POIS_PATH) -> None:
        self._items: List[Dict] = []
        raw_list: List[Dict] = []
        if pois_path.exists():
            raw_list = json.loads(gzip.decompress(pois_path.read_bytes()).decode("utf-8"))

        # Ensure all preset CANTHO_NODES exist even if poi archive absent
        known_nids = {p.get("nid") for p in raw_list if p.get("nid")}
        for cn in CANTHO_NODES:
            if cn.id not in known_nids:
                raw_list.append(
                    {
                        "n": cn.name,
                        "la": cn.lat,
                        "lo": cn.lon,
                        "c": "intersection",
                        "a": f"{cn.district}, TP. Cần Thơ",
                        "nid": cn.id,
                        "vip": 1,
                    }
                )

        for entry in raw_list:
            name = str(entry.get("n") or "").strip()
            if not name:
                continue
            cat = str(entry.get("c") or "poi")
            addr = str(entry.get("a") or "").strip()
            aliases = [normalize_vn(al) for al in (entry.get("al") or []) if al]
            name_norm = normalize_vn(name)
            addr_norm = normalize_vn(addr)
            blob = " ".join(part for part in (name_norm, " ".join(aliases), addr_norm) if part)
            self._items.append(
                {
                    "label": name,
                    "subtitle": addr or "TP. Cần Thơ",
                    "category": cat,
                    "category_label": CATEGORY_LABELS.get(cat, "Địa điểm"),
                    "lat": round(float(entry["la"]), 6),
                    "lon": round(float(entry["lo"]), 6),
                    "node_id": entry.get("nid") or None,
                    "vip": int(entry.get("vip", 0)),
                    "aliases": aliases,
                    "name_norm": name_norm,
                    "blob": blob,
                    "source": "vip_poi" if entry.get("vip") else "osm_poi",
                }
            )

    @property
    def count(self) -> int:
        return len(self._items)

    def _to_public(self, item: Dict) -> Dict:
        return {
            "label": item["label"],
            "subtitle": item["subtitle"],
            "category_label": item["category_label"],
            "lat": item["lat"],
            "lon": item["lon"],
            "node_id": item["node_id"],
            "source": item["source"],
        }

    def get_popular_places(self, limit: int = 14) -> List[Dict]:
        """Return curated flagship landmarks + core intersections for instant empty-input focus."""
        vips = [it for it in self._items if it["vip"] == 1]
        # Prefer landmarks/malls/universities/hospitals/bus stations before intersections
        vips.sort(key=lambda it: (0 if it["category"] != "intersection" else 1))
        return [self._to_public(it) for it in vips[:limit]]

    def _fetch_remote_fallback(self, query: str, limit: int = 5) -> List[Dict]:
        """Query Photon Komoot first (better Vietnamese street/alley recall), then Nominatim."""
        out: List[Dict] = []
        try:
            resp = requests.get(
                "https://photon.komoot.io/api/",
                params={"q": f"{query}, Cần Thơ", "lat": 10.0333, "lon": 105.7833, "limit": limit},
                headers={"User-Agent": "CanThoSmartCity/3.2 (CanTho Flood Map)"},
                timeout=2.4,
            )
            if resp.status_code == 200:
                for feat in resp.json().get("features", []):
                    coords = feat.get("geometry", {}).get("coordinates") or []
                    if len(coords) != 2:
                        continue
                    lon, lat = float(coords[0]), float(coords[1])
                    if not (9.82 <= lat <= 10.30 and 105.45 <= lon <= 105.95):
                        continue
                    props = feat.get("properties", {})
                    lbl = sanitize_text(props.get("name") or props.get("street") or query, 90)
                    sub_parts = [p for p in (props.get("street"), props.get("district") or props.get("city")) if p and p != lbl]
                    out.append(
                        {
                            "label": lbl,
                            "subtitle": sanitize_text(", ".join(sub_parts) or "TP. Cần Thơ", 90),
                            "category_label": "Địa chỉ OSM",
                            "lat": round(lat, 6),
                            "lon": round(lon, 6),
                            "node_id": None,
                            "source": "osm_photon",
                        }
                    )
        except Exception:
            pass

        if len(out) >= 2:
            return out[:limit]

        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": f"{query}, Cần Thơ",
                    "format": "jsonv2",
                    "limit": limit,
                    "viewbox": "105.45,10.30,105.95,9.82",
                    "bounded": 1,
                },
                headers={"User-Agent": "CanThoSmartCity/3.2 (CanTho Flood Map)"},
                timeout=2.6,
            )
            if resp.status_code == 200:
                for item in resp.json():
                    lat, lon = float(item["lat"]), float(item["lon"])
                    if 9.82 <= lat <= 10.30 and 105.45 <= lon <= 105.95:
                        parts = [p.strip() for p in sanitize_text(item.get("display_name", query), 110).split(",") if p.strip()]
                        out.append(
                            {
                                "label": parts[0] if parts else query,
                                "subtitle": ", ".join(parts[1:3]) if len(parts) > 1 else "TP. Cần Thơ",
                                "category_label": "Địa chỉ OSM",
                                "lat": round(lat, 6),
                                "lon": round(lon, 6),
                                "node_id": None,
                                "source": "osm_nominatim",
                            }
                        )
        except Exception:
            pass

        return out[:limit]

    def search_places(self, query: str, limit: int = 8) -> List[Dict]:
        """Accent-insensitive ranked POI search across 1,600+ Can Tho places + remote fallback."""
        q_norm = normalize_vn(query)
        if not q_norm:
            return self.get_popular_places(limit=max(limit, 12))

        tokens = [t for t in q_norm.split(" ") if t]
        scored: List[tuple] = []

        for item in self._items:
            nm = item["name_norm"]
            aliases = item["aliases"]
            blob = item["blob"]

            score = 0.0
            if q_norm == nm or q_norm in aliases:
                score += 145.0
            elif nm.startswith(q_norm) or any(al.startswith(q_norm) for al in aliases):
                score += 100.0
            elif q_norm in nm or any(q_norm in al for al in aliases):
                score += 72.0
            elif all(tok in blob for tok in tokens):
                score += 48.0
            else:
                continue

            if item["vip"] == 1:
                score += 48.0 if item["category"] != "intersection" else 22.0
            elif item["category"] in MAJOR_CATEGORIES:
                score += 18.0
            else:
                score += 4.0

            # Favor concise flagship names over long descriptive sub-shops on tie
            score -= min(12.0, len(nm) * 0.15)
            scored.append((score, item))

        scored.sort(key=lambda pair: (-pair[0], len(pair[1]["label"])))
        results: List[Dict] = [self._to_public(pair[1]) for pair in scored[:limit]]

        # Fallback to Photon/Nominatim for granular street numbers, alleys ("Hẻm 51") or niche places
        if len(results) < 4 and len(q_norm) >= 3:
            q_tokens = [tok for tok in q_norm.split(" ") if tok and tok not in ("can", "tho", "tp", "duong", "hem")]
            remote = self._fetch_remote_fallback(query, limit=5)
            for r in remote:
                r_norm = normalize_vn(f"{r.get('label', '')} {r.get('subtitle', '')}")
                if q_tokens and not any(tok in r_norm for tok in q_tokens):
                    continue
                dup = any(
                    haversine_m(existing["lat"], existing["lon"], r["lat"], r["lon"]) <= 55.0
                    for existing in results
                )
                if not dup:
                    results.append(r)
                if len(results) >= limit:
                    break

        return results[:limit]


_POI_INDEX_SINGLETON: Optional[CanThoPOISearchIndex] = None


def get_poi_index() -> CanThoPOISearchIndex:
    """Return cached CanThoPOISearchIndex singleton."""
    global _POI_INDEX_SINGLETON
    if _POI_INDEX_SINGLETON is None:
        _POI_INDEX_SINGLETON = CanThoPOISearchIndex()
    return _POI_INDEX_SINGLETON

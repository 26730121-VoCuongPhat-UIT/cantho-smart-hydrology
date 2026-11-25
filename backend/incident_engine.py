"""Spatio-Temporal DBSCAN (ST-DBSCAN) Community Reports & Hydro-Plausibility Engine.

Supports:
- Clean real-world startup (seed_demo=False default; zero fake hardcoded markers)
- Non-destructive ST-DBSCAN Demo preview in RAM
- Weather-Aware Dynamic TTL (180m during rain/high tide, 60m when dry & low tide)
- 1-tap community confirm (+1) & clearance ("Đã rút nước") resolution + photo & comments
- AI Hydro-Plausibility Guard (anti-poisoning score requiring >= 3 confirmations on anomalous claims)
- Dual-Mode Persistence: PostgreSQL (DATABASE_URL / POSTGRES_URL) -> SQLite -> Memory
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from backend.cantho_geo import haversine_m, idw_elevation_and_drainage
from backend.data_pipeline import RUNTIME_CACHE_DIR

VN_TZ = timezone(timedelta(hours=7))
MAX_ACTIVE_REPORTS = 500
VALID_CATEGORIES = ("flood_deep", "flood_light", "traffic", "rain", "dry")
logger = logging.getLogger("cantho.incidents")

try:
    import psycopg  # type: ignore
except Exception:  # pragma: no cover - optional in local dev without psycopg
    psycopg = None


@dataclass
class FloodReport:
    """Single crowdsourced flood or civic traffic report submitted in Can Tho."""

    id: str
    lat: float
    lon: float
    reported_depth_cm: float
    timestamp_iso: str
    description: str
    reporter_name: str
    location_hint: str
    report_kind: str = "flood"  # "flood" | "cleared"
    category: str = "flood_deep"  # "flood_deep" | "flood_light" | "traffic" | "rain" | "dry"
    district: str = "ninh_kieu"
    photo_data: str = ""


@dataclass
class IncidentComment:
    """Community reply attached to a community flood post."""

    id: str
    report_id: str
    author_name: str
    content: str
    timestamp_iso: str


def compute_dynamic_ttl_minutes(rain_mm_h: float, tide_level_m: float) -> Tuple[int, str]:
    """Compute Weather-Aware TTL: 180 min in active rain/high tide, 60 min in dry low tide."""
    if float(rain_mm_h) >= 2.0 or float(tide_level_m) >= 1.75:
        return 180, "wet_or_high_tide"
    return 60, "dry_low_tide"


def compute_hydro_plausibility(
    lat: float,
    lon: float,
    reported_depth_cm: float,
    rain_mm_h: float,
    tide_level_m: float,
    has_photo: bool = False,
    report_count: int = 1,
) -> Dict:
    """Score physical hydro-meteorological plausibility (0-100%) to guard A* against fake reports."""
    elev_m, _drain, _zone, nearest_landmark = idw_elevation_and_drainage(lat, lon)
    rain = max(0.0, float(rain_mm_h))
    tide = max(0.0, float(tide_level_m))
    depth = max(0.0, float(reported_depth_cm))

    score = 64
    if rain >= 10.0:
        score += 16
    elif rain >= 2.0:
        score += 9

    if tide >= 1.80:
        score += 14
    elif tide >= 1.65:
        score += 6

    if elev_m <= 1.25:
        score += 10
    elif elev_m >= 1.65 and depth >= 30.0 and rain < 2.0 and tide < 1.65:
        score -= 40

    if rain < 2.0 and tide < 1.65 and depth >= 35.0 and not has_photo:
        score -= 18

    if has_photo:
        score += 14

    if report_count > 1:
        score += min(22, (report_count - 1) * 10)

    score = max(5, min(99, int(round(score))))
    if score < 50:
        tier = "low_needs_review"
        label = "Cần kiểm chứng chéo"
        min_conf = 3
    elif score < 75:
        tier = "medium"
        label = "Khả tín trung bình"
        min_conf = 2
    else:
        tier = "high"
        label = "Khớp thủy văn"
        min_conf = 2

    return {
        "score": score,
        "tier": tier,
        "label": label,
        "min_confirmations_for_fusion": min_conf,
        "local_elevation_m": round(float(elev_m), 2),
        "nearest_landmark": nearest_landmark,
    }


def _default_seed_reports() -> List[FloodReport]:
    """Generate demo reports anchored to current Vietnam time (used ONLY for demo/unit fixtures)."""
    base_time = datetime.now(VN_TZ).replace(tzinfo=None, microsecond=0)
    return [
        # Cluster 1: Mau Than - Tran Hung Dao (3 simultaneous reports)
        FloodReport(
            id="R-001",
            lat=10.0365,
            lon=105.7760,
            reported_depth_cm=36.0,
            timestamp_iso=(base_time - timedelta(minutes=8)).isoformat(),
            description="Nước ngập qua nửa bánh xe máy ngay ngã tư Mậu Thân - Trần Hưng Đạo.",
            reporter_name="Minh Khang (SV CTU)",
            location_hint="Mậu Thân - Trần Hưng Đạo",
            report_kind="flood",
            category="flood_deep",
            district="ninh_kieu",
        ),
        FloodReport(
            id="R-002",
            lat=10.0361,
            lon=105.7757,
            reported_depth_cm=39.0,
            timestamp_iso=(base_time - timedelta(minutes=4)).isoformat(),
            description="Nhiều xe tay ga chết máy ở đoạn Mậu Thân, nước tràn lên vỉa hè.",
            reporter_name="Ngọc Trâm",
            location_hint="Mậu Thân - Trần Hưng Đạo",
            report_kind="flood",
            category="flood_deep",
            district="ninh_kieu",
        ),
        FloodReport(
            id="R-003",
            lat=10.0368,
            lon=105.7762,
            reported_depth_cm=34.0,
            timestamp_iso=base_time.isoformat(),
            description="Cống thoát nước bị triều đẩy ngược, không nên đi qua Mậu Thân.",
            reporter_name="Anh Tuấn (Grab)",
            location_hint="Mậu Thân - Trần Hưng Đạo",
            report_kind="flood",
            category="flood_deep",
            district="ninh_kieu",
        ),
        # Cluster 2: Ho Bun Xang (2 reports)
        FloodReport(
            id="R-004",
            lat=10.0334,
            lon=105.7663,
            reported_depth_cm=42.0,
            timestamp_iso=(base_time - timedelta(minutes=12)).isoformat(),
            description="Đường bờ hồ Bún Xáng ngập sâu gần 40cm, kẹt xe cục bộ.",
            reporter_name="Hữu Nghĩa",
            location_hint="Khu vực Hồ Bún Xáng",
            report_kind="flood",
            category="flood_deep",
            district="ninh_kieu",
        ),
        FloodReport(
            id="R-005",
            lat=10.0338,
            lon=105.7667,
            reported_depth_cm=38.0,
            timestamp_iso=(base_time - timedelta(minutes=3)).isoformat(),
            description="Nước từ hồ Bún Xáng tràn qua hẻm 51 đường 3/2.",
            reporter_name="Thanh Thảo",
            location_hint="Khu vực Hồ Bún Xáng",
            report_kind="flood",
            category="flood_deep",
            district="ninh_kieu",
        ),
        # Cluster 3: Cach Mang Thang 8 (Binh Thuy) (2 reports)
        FloodReport(
            id="R-006",
            lat=10.0568,
            lon=105.7610,
            reported_depth_cm=35.0,
            timestamp_iso=(base_time - timedelta(minutes=18)).isoformat(),
            description="CMT8 đoạn gần công viên Bình Thủy ngập sâu do triều sông Hậu.",
            reporter_name="Quốc Bảo",
            location_hint="CMT8 (Bình Thủy)",
            report_kind="flood",
            category="flood_deep",
            district="binh_thuy",
        ),
        FloodReport(
            id="R-007",
            lat=10.0572,
            lon=105.7616,
            reported_depth_cm=33.0,
            timestamp_iso=(base_time - timedelta(minutes=5)).isoformat(),
            description="Xe gầm thấp đi chậm ở CMT8, nước ngập lút bô xe.",
            reporter_name="Chị Lan",
            location_hint="CMT8 (Bình Thủy)",
            report_kind="flood",
            category="flood_deep",
            district="binh_thuy",
        ),
        # Noise / Outdated report (95 minutes earlier -> >60m ST-DBSCAN window, <180m wet TTL)
        FloodReport(
            id="R-008",
            lat=10.0462,
            lon=105.7868,
            reported_depth_cm=15.0,
            timestamp_iso=(base_time - timedelta(minutes=95)).isoformat(),
            description="Báo cáo cũ gần TTTM Cái Khế (chờ thêm xác thực hoặc nước đang rút).",
            reporter_name="Hoàng Nam",
            location_hint="TTTM Cái Khế",
            report_kind="flood",
            category="flood_light",
            district="ninh_kieu",
        ),
    ]


def _default_demo_comments() -> List[IncidentComment]:
    base_time = datetime.now(VN_TZ).replace(tzinfo=None, microsecond=0)
    return [
        IncidentComment(
            id="C-001",
            report_id="R-001",
            author_name="Hải Đăng (Ninh Kiều)",
            content="Xe tay ga nên né qua Đại lộ Hòa Bình hoặc Trần Hoàng Na nhé mọi người.",
            timestamp_iso=(base_time - timedelta(minutes=2)).isoformat(),
        ),
        IncidentComment(
            id="C-002",
            report_id="R-004",
            author_name="Phương Vy",
            content="Đoạn bờ hồ vẫn đang dâng nhẹ, mình vừa vòng ra Nguyễn Văn Cừ thì thoáng.",
            timestamp_iso=(base_time - timedelta(minutes=1)).isoformat(),
        ),
    ]


def run_st_dbscan(
    reports: List[FloodReport],
    spatial_eps_m: float = 350.0,
    temporal_eps_min: float = 60.0,
    min_samples: int = 2,
) -> Dict:
    """Cluster flood reports using ST-DBSCAN across (Haversine distance, timestamp delta).

    Returns both Master Incidents (clusters with >= min_samples) and unclustered noise/pending reports.
    """
    n = len(reports)
    params_meta = {
        "spatial_eps_m": spatial_eps_m,
        "temporal_eps_min": temporal_eps_min,
        "min_samples": min_samples,
    }
    if n == 0:
        return {
            "master_incidents": [],
            "noise_reports": [],
            "total_reports": 0,
            "params": params_meta,
        }

    timestamps = [datetime.fromisoformat(r.timestamp_iso).replace(tzinfo=None) for r in reports]

    def region_query(idx: int) -> List[int]:
        ri, ti = reports[idx], timestamps[idx]
        neighbors: List[int] = []
        for j in range(n):
            dt_min = abs((ti - timestamps[j]).total_seconds()) / 60.0
            if dt_min <= temporal_eps_min:
                dist_m = haversine_m(ri.lat, ri.lon, reports[j].lat, reports[j].lon)
                if dist_m <= spatial_eps_m:
                    neighbors.append(j)
        return neighbors

    UNVISITED = -2
    NOISE = -1
    labels = [UNVISITED] * n
    cluster_id = 0

    for i in range(n):
        if labels[i] != UNVISITED:
            continue
        neighbors = region_query(i)
        if len(neighbors) < min_samples:
            labels[i] = NOISE
            continue

        labels[i] = cluster_id
        queue = deque(nb for nb in neighbors if nb != i)
        while queue:
            curr = queue.popleft()
            if labels[curr] == NOISE:
                labels[curr] = cluster_id
            if labels[curr] != UNVISITED:
                continue
            labels[curr] = cluster_id
            curr_neighbors = region_query(curr)
            if len(curr_neighbors) >= min_samples:
                for nb in curr_neighbors:
                    if labels[nb] in (UNVISITED, NOISE):
                        queue.append(nb)
        cluster_id += 1

    clusters: Dict[int, List[FloodReport]] = {}
    noise_reports: List[Dict] = []
    for idx, lbl in enumerate(labels):
        rep_dict = asdict(reports[idx])
        rep_dict["cluster_id"] = lbl
        if lbl == NOISE:
            noise_reports.append(rep_dict)
        else:
            clusters.setdefault(lbl, []).append(reports[idx])

    master_incidents: List[Dict] = []
    for cid, group in sorted(clusters.items()):
        sorted_group = sorted(group, key=lambda x: x.timestamp_iso)
        avg_lat = sum(r.lat for r in sorted_group) / len(sorted_group)
        avg_lon = sum(r.lon for r in sorted_group) / len(sorted_group)
        avg_depth = sum(r.reported_depth_cm for r in sorted_group) / len(sorted_group)
        latest_r = sorted_group[-1]
        latest_photo = next((r.photo_data for r in reversed(sorted_group) if r.photo_data), "")
        confidence = min(0.99, round(0.55 + 0.14 * len(sorted_group), 2))
        master_incidents.append(
            {
                "incident_id": f"INC-{cid + 1:02d}",
                "primary_report_id": sorted_group[0].id,
                "report_ids": [r.id for r in sorted_group],
                "lat": round(avg_lat, 6),
                "lon": round(avg_lon, 6),
                "report_count": len(sorted_group),
                "avg_depth_cm": round(avg_depth, 1),
                "max_depth_cm": round(max(r.reported_depth_cm for r in sorted_group), 1),
                "confidence": confidence,
                "latest_timestamp": latest_r.timestamp_iso,
                "location_label": latest_r.location_hint or sorted_group[0].location_hint,
                "district": latest_r.district,
                "category": latest_r.category,
                "reporter_name": latest_r.reporter_name,
                "photo_data": latest_photo,
                "descriptions": [r.description for r in reversed(sorted_group)],
            }
        )

    master_incidents.sort(key=lambda x: (-x["report_count"], -x["avg_depth_cm"]))
    return {
        "master_incidents": master_incidents,
        "noise_reports": noise_reports,
        "total_reports": n,
        "params": {
            "spatial_eps_m": spatial_eps_m,
            "temporal_eps_min": temporal_eps_min,
            "min_samples": min_samples,
        },
    }


def _parse_naive_ts(ts_iso: str) -> datetime:
    return datetime.fromisoformat(ts_iso).replace(tzinfo=None)


def _build_clustered_social_payload(
    all_reports: List[FloodReport],
    comments: List[IncidentComment],
    rain_mm_h: float,
    tide_level_m: float,
    now_vn: datetime | None,
    storage_backend: str,
    is_demo: bool = False,
) -> Dict:
    """Filter by Weather-Aware Dynamic TTL, run ST-DBSCAN, apply clearance votes & plausibility scoring."""
    ref_now = (now_vn or datetime.now(VN_TZ)).replace(tzinfo=None)
    ttl_minutes, weather_regime = compute_dynamic_ttl_minutes(rain_mm_h, tide_level_m)

    comments_by_report: Dict[str, List[Dict]] = {}
    for c in comments:
        comments_by_report.setdefault(c.report_id, []).append(asdict(c))

    active_reports: List[FloodReport] = []
    expired_count = 0
    for r in all_reports:
        try:
            age_min = max(0.0, (ref_now - _parse_naive_ts(r.timestamp_iso)).total_seconds() / 60.0)
        except Exception:
            age_min = 0.0
        if age_min <= ttl_minutes:
            active_reports.append(r)
        else:
            expired_count += 1

    clear_reports = [r for r in active_reports if r.report_kind == "cleared"]
    civic_posts = [
        r
        for r in active_reports
        if r.report_kind != "cleared" and r.category in ("traffic", "rain", "dry") and r.reported_depth_cm < 8.0
    ]
    flood_reports = [
        r
        for r in active_reports
        if r.report_kind != "cleared" and not (r.category in ("traffic", "rain", "dry") and r.reported_depth_cm < 8.0)
    ]

    dbscan_out = run_st_dbscan(flood_reports)

    def _count_clearances(lat: float, lon: float, since_ts: str) -> int:
        since_dt = _parse_naive_ts(since_ts)
        cnt = 0
        for cr in clear_reports:
            if _parse_naive_ts(cr.timestamp_iso) >= since_dt:
                if haversine_m(lat, lon, cr.lat, cr.lon) <= 350.0:
                    cnt += 1
        return cnt

    cleared_count = 0
    verified_incidents: List[Dict] = []
    for inc in dbscan_out["master_incidents"]:
        c_votes = _count_clearances(inc["lat"], inc["lon"], inc["latest_timestamp"])
        if c_votes >= 2 or c_votes >= inc["report_count"]:
            cleared_count += 1
            continue

        age_min = round(max(0.0, (ref_now - _parse_naive_ts(inc["latest_timestamp"])).total_seconds() / 60.0), 1)
        expires_in = max(0, int(round(ttl_minutes - age_min)))
        plaus = compute_hydro_plausibility(
            lat=inc["lat"],
            lon=inc["lon"],
            reported_depth_cm=inc["avg_depth_cm"],
            rain_mm_h=rain_mm_h,
            tide_level_m=tide_level_m,
            has_photo=bool(inc.get("photo_data")),
            report_count=inc["report_count"],
        )
        merged_comments: List[Dict] = []
        for rid in inc.get("report_ids", [inc["primary_report_id"]]):
            merged_comments.extend(comments_by_report.get(rid, []))

        inc.update(
            {
                "status": "verified",
                "cleared_votes": c_votes,
                "age_minutes": age_min,
                "expires_in_minutes": expires_in,
                "plausibility": plaus,
                "fusion_eligible": inc["report_count"] >= plaus["min_confirmations_for_fusion"],
                "comments": merged_comments,
            }
        )
        verified_incidents.append(inc)

    pending_reports: List[Dict] = []
    surviving_noise: List[Dict] = []
    for rep in dbscan_out["noise_reports"]:
        c_votes = _count_clearances(rep["lat"], rep["lon"], rep["timestamp_iso"])
        if c_votes >= 1:
            cleared_count += 1
            continue

        age_min = round(max(0.0, (ref_now - _parse_naive_ts(rep["timestamp_iso"])).total_seconds() / 60.0), 1)
        expires_in = max(0, int(round(ttl_minutes - age_min)))
        plaus = compute_hydro_plausibility(
            lat=rep["lat"],
            lon=rep["lon"],
            reported_depth_cm=rep["reported_depth_cm"],
            rain_mm_h=rain_mm_h,
            tide_level_m=tide_level_m,
            has_photo=bool(rep.get("photo_data")),
            report_count=1,
        )
        pending_item = {
            "incident_id": f"PENDING-{rep['id']}",
            "primary_report_id": rep["id"],
            "report_ids": [rep["id"]],
            "lat": round(float(rep["lat"]), 6),
            "lon": round(float(rep["lon"]), 6),
            "report_count": 1,
            "avg_depth_cm": round(float(rep["reported_depth_cm"]), 1),
            "max_depth_cm": round(float(rep["reported_depth_cm"]), 1),
            "confidence": 0.50,
            "latest_timestamp": rep["timestamp_iso"],
            "location_label": rep.get("location_hint") or "Điểm phản ánh mới",
            "district": rep.get("district", "ninh_kieu"),
            "category": rep.get("category", "flood_light"),
            "reporter_name": rep.get("reporter_name", "Người dân Cần Thơ"),
            "photo_data": rep.get("photo_data", ""),
            "descriptions": [rep.get("description", "")],
            "status": "pending",
            "cleared_votes": c_votes,
            "age_minutes": age_min,
            "expires_in_minutes": expires_in,
            "plausibility": plaus,
            "fusion_eligible": False,
            "comments": list(comments_by_report.get(rep["id"], [])),
        }
        pending_reports.append(pending_item)
        surviving_noise.append(rep)

    civic_items: List[Dict] = []
    for cr in civic_posts:
        age_min = round(max(0.0, (ref_now - _parse_naive_ts(cr.timestamp_iso)).total_seconds() / 60.0), 1)
        expires_in = max(0, int(round(ttl_minutes - age_min)))
        civic_items.append(
            {
                "incident_id": f"CIVIC-{cr.id}",
                "primary_report_id": cr.id,
                "report_ids": [cr.id],
                "lat": round(float(cr.lat), 6),
                "lon": round(float(cr.lon), 6),
                "report_count": 1,
                "avg_depth_cm": round(float(cr.reported_depth_cm), 1),
                "max_depth_cm": round(float(cr.reported_depth_cm), 1),
                "confidence": 0.70,
                "latest_timestamp": cr.timestamp_iso,
                "location_label": cr.location_hint or "TP. Cần Thơ",
                "district": cr.district,
                "category": cr.category,
                "reporter_name": cr.reporter_name,
                "photo_data": cr.photo_data,
                "descriptions": [cr.description],
                "status": "civic",
                "cleared_votes": 0,
                "age_minutes": age_min,
                "expires_in_minutes": expires_in,
                "plausibility": None,
                "fusion_eligible": False,
                "comments": list(comments_by_report.get(cr.id, [])),
            }
        )

    social_feed = sorted(
        verified_incidents + pending_reports + civic_items,
        key=lambda item: item["latest_timestamp"],
        reverse=True,
    )

    return {
        "master_incidents": verified_incidents,
        "pending_reports": pending_reports,
        "social_feed": social_feed,
        "noise_reports": surviving_noise,
        "raw_reports": [asdict(r) for r in reversed(active_reports)],
        "total_reports": len(active_reports),
        "ttl_minutes": ttl_minutes,
        "weather_regime": weather_regime,
        "expired_count": expired_count,
        "cleared_count": cleared_count,
        "storage_backend": storage_backend,
        "is_demo": is_demo,
        "params": dbscan_out["params"],
    }


def get_demo_clustered_incidents() -> Dict:
    """Return a pure in-memory ST-DBSCAN demonstration dataset without mutating real storage."""
    return _build_clustered_social_payload(
        all_reports=_default_seed_reports(),
        comments=_default_demo_comments(),
        rain_mm_h=38.0,
        tide_level_m=2.12,
        now_vn=datetime.now(VN_TZ),
        storage_backend="demo-memory",
        is_demo=True,
    )


class IncidentStore:
    """Dual-mode store (PostgreSQL via DATABASE_URL/POSTGRES_URL, SQLite fallback, or in-memory)."""

    def __init__(
        self,
        backend: str = "memory",
        database_url: str | None = None,
        sqlite_path: str | Path | None = None,
        seed_demo: bool = False,
    ) -> None:
        self.storage_backend = backend
        self.database_url = database_url or ""
        self.sqlite_path = Path(sqlite_path) if sqlite_path else (RUNTIME_CACHE_DIR / "incidents.db")
        self.seed_demo = seed_demo
        self.reports: List[FloodReport] = []
        self.comments: List[IncidentComment] = []

        if self.storage_backend == "postgres":
            if not self._init_postgres():
                logger.warning("Falling back from postgres to sqlite incident persistence.")
                self.storage_backend = "sqlite"

        if self.storage_backend == "sqlite":
            if not self._init_sqlite():
                logger.warning("Falling back from sqlite to in-memory incident persistence.")
                self.storage_backend = "memory"

        if self.storage_backend == "memory" and self.seed_demo:
            self.reports = _default_seed_reports()
            self.comments = _default_demo_comments()

    @classmethod
    def from_env(cls) -> "IncidentStore":
        """Create environment-appropriate persistent store (Postgres -> SQLite) with clean zero fake startup."""
        for env_key in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
            db_url = os.environ.get(env_key, "").strip()
            if db_url:
                return cls(backend="postgres", database_url=db_url, seed_demo=False)
        sqlite_override = os.environ.get("INCIDENTS_DB_PATH", "").strip()
        db_path = Path(sqlite_override) if sqlite_override else (RUNTIME_CACHE_DIR / "incidents.db")
        return cls(backend="sqlite", sqlite_path=db_path, seed_demo=False)

    def _init_sqlite(self) -> bool:
        try:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.closing(sqlite3.connect(self.sqlite_path)) as conn:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS flood_reports (
                            seq INTEGER PRIMARY KEY AUTOINCREMENT,
                            id TEXT NOT NULL,
                            lat REAL NOT NULL,
                            lon REAL NOT NULL,
                            reported_depth_cm REAL NOT NULL,
                            timestamp_iso TEXT NOT NULL,
                            description TEXT NOT NULL,
                            reporter_name TEXT NOT NULL,
                            location_hint TEXT NOT NULL,
                            report_kind TEXT NOT NULL DEFAULT 'flood',
                            category TEXT NOT NULL DEFAULT 'flood_deep',
                            district TEXT NOT NULL DEFAULT 'ninh_kieu',
                            photo_data TEXT NOT NULL DEFAULT ''
                        )
                        """
                    )
                    existing_cols = {
                        row[1] for row in conn.execute("PRAGMA table_info(flood_reports)").fetchall()
                    }
                    migrations = {
                        "report_kind": "TEXT NOT NULL DEFAULT 'flood'",
                        "category": "TEXT NOT NULL DEFAULT 'flood_deep'",
                        "district": "TEXT NOT NULL DEFAULT 'ninh_kieu'",
                        "photo_data": "TEXT NOT NULL DEFAULT ''",
                    }
                    for col, col_def in migrations.items():
                        if col not in existing_cols:
                            conn.execute(f"ALTER TABLE flood_reports ADD COLUMN {col} {col_def}")

                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS incident_comments (
                            seq INTEGER PRIMARY KEY AUTOINCREMENT,
                            id TEXT NOT NULL,
                            report_id TEXT NOT NULL,
                            author_name TEXT NOT NULL,
                            content TEXT NOT NULL,
                            timestamp_iso TEXT NOT NULL
                        )
                        """
                    )
                    if self.seed_demo:
                        count = conn.execute("SELECT COUNT(*) FROM flood_reports").fetchone()[0]
                        if count == 0:
                            conn.executemany(
                                """
                                INSERT INTO flood_reports
                                (id, lat, lon, reported_depth_cm, timestamp_iso, description, reporter_name, location_hint, report_kind, category, district, photo_data)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                [
                                    (
                                        r.id, r.lat, r.lon, r.reported_depth_cm, r.timestamp_iso,
                                        r.description, r.reporter_name, r.location_hint,
                                        r.report_kind, r.category, r.district, r.photo_data,
                                    )
                                    for r in _default_seed_reports()
                                ],
                            )
            return True
        except Exception as exc:
            logger.warning("SQLite init failed (%s)", exc.__class__.__name__)
            return False

    def _init_postgres(self) -> bool:
        if psycopg is None or not self.database_url:
            return False
        try:
            with psycopg.connect(self.database_url, connect_timeout=4) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS flood_reports (
                            seq BIGSERIAL PRIMARY KEY,
                            id TEXT NOT NULL,
                            lat DOUBLE PRECISION NOT NULL,
                            lon DOUBLE PRECISION NOT NULL,
                            reported_depth_cm DOUBLE PRECISION NOT NULL,
                            timestamp_iso TEXT NOT NULL,
                            description TEXT NOT NULL,
                            reporter_name TEXT NOT NULL,
                            location_hint TEXT NOT NULL,
                            report_kind TEXT NOT NULL DEFAULT 'flood',
                            category TEXT NOT NULL DEFAULT 'flood_deep',
                            district TEXT NOT NULL DEFAULT 'ninh_kieu',
                            photo_data TEXT NOT NULL DEFAULT ''
                        )
                        """
                    )
                    for col, col_def in (
                        ("report_kind", "TEXT NOT NULL DEFAULT 'flood'"),
                        ("category", "TEXT NOT NULL DEFAULT 'flood_deep'"),
                        ("district", "TEXT NOT NULL DEFAULT 'ninh_kieu'"),
                        ("photo_data", "TEXT NOT NULL DEFAULT ''"),
                    ):
                        cur.execute(f"ALTER TABLE flood_reports ADD COLUMN IF NOT EXISTS {col} {col_def}")

                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS incident_comments (
                            seq BIGSERIAL PRIMARY KEY,
                            id TEXT NOT NULL,
                            report_id TEXT NOT NULL,
                            author_name TEXT NOT NULL,
                            content TEXT NOT NULL,
                            timestamp_iso TEXT NOT NULL
                        )
                        """
                    )
                    if self.seed_demo:
                        cur.execute("SELECT COUNT(*) FROM flood_reports")
                        row = cur.fetchone()
                        if row and row[0] == 0:
                            for r in _default_seed_reports():
                                cur.execute(
                                    """
                                    INSERT INTO flood_reports
                                    (id, lat, lon, reported_depth_cm, timestamp_iso, description, reporter_name, location_hint, report_kind, category, district, photo_data)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    """,
                                    (
                                        r.id, r.lat, r.lon, r.reported_depth_cm, r.timestamp_iso,
                                        r.description, r.reporter_name, r.location_hint,
                                        r.report_kind, r.category, r.district, r.photo_data,
                                    ),
                                )
                conn.commit()
            return True
        except Exception as exc:
            logger.warning("Postgres init failed (%s)", exc.__class__.__name__)
            return False

    def _load_recent_reports(self) -> List[FloodReport]:
        if self.storage_backend == "postgres" and psycopg is not None:
            try:
                with psycopg.connect(self.database_url, connect_timeout=4) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT id, lat, lon, reported_depth_cm, timestamp_iso, description,
                                   reporter_name, location_hint, report_kind, category, district, photo_data
                            FROM flood_reports
                            ORDER BY seq DESC
                            LIMIT %s
                            """,
                            (MAX_ACTIVE_REPORTS,),
                        )
                        rows = cur.fetchall()
                return [FloodReport(*row) for row in reversed(rows)]
            except Exception as exc:
                logger.warning("Postgres read failed (%s)", exc.__class__.__name__)

        if self.storage_backend == "sqlite":
            try:
                with contextlib.closing(sqlite3.connect(self.sqlite_path)) as conn:
                    rows = conn.execute(
                        """
                        SELECT id, lat, lon, reported_depth_cm, timestamp_iso, description,
                               reporter_name, location_hint, report_kind, category, district, photo_data
                        FROM flood_reports
                        ORDER BY seq DESC
                        LIMIT ?
                        """,
                        (MAX_ACTIVE_REPORTS,),
                    ).fetchall()
                return [FloodReport(*row) for row in reversed(rows)]
            except Exception as exc:
                logger.warning("SQLite read failed (%s)", exc.__class__.__name__)

        return list(self.reports[-MAX_ACTIVE_REPORTS:])

    def _load_recent_comments(self) -> List[IncidentComment]:
        if self.storage_backend == "postgres" and psycopg is not None:
            try:
                with psycopg.connect(self.database_url, connect_timeout=4) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT id, report_id, author_name, content, timestamp_iso
                            FROM incident_comments
                            ORDER BY seq DESC
                            LIMIT 600
                            """
                        )
                        rows = cur.fetchall()
                return [IncidentComment(*row) for row in reversed(rows)]
            except Exception as exc:
                logger.warning("Postgres comment read failed (%s)", exc.__class__.__name__)

        if self.storage_backend == "sqlite":
            try:
                with contextlib.closing(sqlite3.connect(self.sqlite_path)) as conn:
                    rows = conn.execute(
                        """
                        SELECT id, report_id, author_name, content, timestamp_iso
                        FROM incident_comments
                        ORDER BY seq DESC
                        LIMIT 600
                        """
                    ).fetchall()
                return [IncidentComment(*row) for row in reversed(rows)]
            except Exception as exc:
                logger.warning("SQLite comment read failed (%s)", exc.__class__.__name__)

        return list(self.comments[-600:])

    def get_state_signature(self) -> str:
        """Return an O(1) monotonic version token covering reports and comments across backends."""
        if self.storage_backend == "postgres" and psycopg is not None:
            try:
                with psycopg.connect(self.database_url, connect_timeout=3) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT
                                (SELECT COALESCE(MAX(seq), 0) FROM flood_reports),
                                (SELECT COUNT(*) FROM flood_reports),
                                (SELECT COALESCE(MAX(seq), 0) FROM incident_comments),
                                (SELECT COUNT(*) FROM incident_comments)
                            """
                        )
                        row = cur.fetchone() or (0, 0, 0, 0)
                return f"pg:{int(row[0])}:{int(row[1])}:{int(row[2])}:{int(row[3])}"
            except Exception as exc:
                logger.warning("Postgres signature read failed (%s)", exc.__class__.__name__)

        if self.storage_backend == "sqlite":
            try:
                with contextlib.closing(sqlite3.connect(self.sqlite_path)) as conn:
                    row = conn.execute(
                        """
                        SELECT
                            (SELECT COALESCE(MAX(seq), 0) FROM flood_reports),
                            (SELECT COUNT(*) FROM flood_reports),
                            (SELECT COALESCE(MAX(seq), 0) FROM incident_comments),
                            (SELECT COUNT(*) FROM incident_comments)
                        """
                    ).fetchone() or (0, 0, 0, 0)
                return f"sq:{int(row[0])}:{int(row[1])}:{int(row[2])}:{int(row[3])}"
            except Exception as exc:
                logger.warning("SQLite signature read failed (%s)", exc.__class__.__name__)

        last_rep = self.reports[-1].id if self.reports else "0"
        last_com = self.comments[-1].id if self.comments else "0"
        return f"mem:{len(self.reports)}:{last_rep}:{len(self.comments)}:{last_com}"

    def get_clustered_incidents(
        self,
        rain_mm_h: float = 0.0,
        tide_level_m: float = 1.45,
        now_vn: datetime | None = None,
    ) -> Dict:
        reports = self._load_recent_reports()
        comments = self._load_recent_comments()
        return _build_clustered_social_payload(
            all_reports=reports,
            comments=comments,
            rain_mm_h=rain_mm_h,
            tide_level_m=tide_level_m,
            now_vn=now_vn,
            storage_backend=self.storage_backend,
            is_demo=False,
        )

    def _insert_report(self, report: FloodReport) -> None:
        inserted = False
        if self.storage_backend == "postgres" and psycopg is not None:
            try:
                with psycopg.connect(self.database_url, connect_timeout=4) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO flood_reports
                            (id, lat, lon, reported_depth_cm, timestamp_iso, description, reporter_name, location_hint, report_kind, category, district, photo_data)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                report.id, report.lat, report.lon, report.reported_depth_cm,
                                report.timestamp_iso, report.description, report.reporter_name,
                                report.location_hint, report.report_kind, report.category,
                                report.district, report.photo_data,
                            ),
                        )
                    conn.commit()
                inserted = True
            except Exception as exc:
                logger.warning("Postgres insert failed (%s)", exc.__class__.__name__)

        if not inserted and self.storage_backend == "sqlite":
            try:
                with contextlib.closing(sqlite3.connect(self.sqlite_path)) as conn:
                    with conn:
                        conn.execute(
                            """
                            INSERT INTO flood_reports
                            (id, lat, lon, reported_depth_cm, timestamp_iso, description, reporter_name, location_hint, report_kind, category, district, photo_data)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                report.id, report.lat, report.lon, report.reported_depth_cm,
                                report.timestamp_iso, report.description, report.reporter_name,
                                report.location_hint, report.report_kind, report.category,
                                report.district, report.photo_data,
                            ),
                        )
                inserted = True
            except Exception as exc:
                logger.warning("SQLite insert failed (%s)", exc.__class__.__name__)

        if not inserted:
            self.reports.append(report)
            if len(self.reports) > MAX_ACTIVE_REPORTS:
                self.reports = self.reports[-MAX_ACTIVE_REPORTS:]

    def add_report(
        self,
        lat: float,
        lon: float,
        reported_depth_cm: float,
        description: str,
        reporter_name: str = "Người dân Cần Thơ",
        location_hint: str = "Điểm phản ánh mới",
        timestamp_iso: str | None = None,
        report_kind: str = "flood",
        category: str = "flood_deep",
        district: str = "ninh_kieu",
        photo_data: str = "",
        rain_mm_h: float = 0.0,
        tide_level_m: float = 1.45,
    ) -> Dict:
        ts = timestamp_iso or datetime.now(VN_TZ).replace(tzinfo=None).isoformat(timespec="seconds")
        new_id = f"R-{uuid.uuid4().hex[:6].upper()}"
        norm_kind = "cleared" if report_kind == "cleared" else "flood"
        norm_cat = category if category in VALID_CATEGORIES else ("flood_deep" if reported_depth_cm >= 18 else "flood_light")
        report = FloodReport(
            id=new_id,
            lat=lat,
            lon=lon,
            reported_depth_cm=reported_depth_cm,
            timestamp_iso=ts,
            description=description,
            reporter_name=reporter_name,
            location_hint=location_hint,
            report_kind=norm_kind,
            category=norm_cat,
            district=district or "ninh_kieu",
            photo_data=photo_data or "",
        )
        self._insert_report(report)
        return self.get_clustered_incidents(rain_mm_h=rain_mm_h, tide_level_m=tide_level_m)

    def add_vote(
        self,
        action: str,
        lat: float,
        lon: float,
        reported_depth_cm: float = 25.0,
        location_hint: str = "Điểm phản ánh trên bản đồ",
        reporter_name: str = "Người dân Cần Thơ",
        district: str = "ninh_kieu",
        category: str = "flood_deep",
        rain_mm_h: float = 0.0,
        tide_level_m: float = 1.45,
    ) -> Dict:
        """Record a 1-tap community confirmation ('confirm') or clearance vote ('clear')."""
        if action == "clear":
            return self.add_report(
                lat=lat,
                lon=lon,
                reported_depth_cm=0.0,
                description="Xác nhận thực tế: nước đã rút, xe lưu thông bình thường.",
                reporter_name=reporter_name,
                location_hint=location_hint,
                report_kind="cleared",
                category="dry",
                district=district,
                rain_mm_h=rain_mm_h,
                tide_level_m=tide_level_m,
            )
        return self.add_report(
            lat=lat,
            lon=lon,
            reported_depth_cm=max(8.0, float(reported_depth_cm)),
            description="Xác nhận thực tế: khu vực này vẫn đang ngập.",
            reporter_name=reporter_name,
            location_hint=location_hint,
            report_kind="flood",
            category=category if category in ("flood_deep", "flood_light") else "flood_deep",
            district=district,
            rain_mm_h=rain_mm_h,
            tide_level_m=tide_level_m,
        )

    def add_comment(
        self,
        report_id: str,
        author_name: str,
        content: str,
        rain_mm_h: float = 0.0,
        tide_level_m: float = 1.45,
    ) -> Dict:
        """Attach a community comment to a report."""
        ts = datetime.now(VN_TZ).replace(tzinfo=None).isoformat(timespec="seconds")
        comment = IncidentComment(
            id=f"C-{uuid.uuid4().hex[:6].upper()}",
            report_id=report_id,
            author_name=author_name or "Người dân Cần Thơ",
            content=content,
            timestamp_iso=ts,
        )
        inserted = False
        if self.storage_backend == "postgres" and psycopg is not None:
            try:
                with psycopg.connect(self.database_url, connect_timeout=4) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO incident_comments (id, report_id, author_name, content, timestamp_iso)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (comment.id, comment.report_id, comment.author_name, comment.content, comment.timestamp_iso),
                        )
                    conn.commit()
                inserted = True
            except Exception as exc:
                logger.warning("Postgres comment insert failed (%s)", exc.__class__.__name__)

        if not inserted and self.storage_backend == "sqlite":
            try:
                with contextlib.closing(sqlite3.connect(self.sqlite_path)) as conn:
                    with conn:
                        conn.execute(
                            """
                            INSERT INTO incident_comments (id, report_id, author_name, content, timestamp_iso)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (comment.id, comment.report_id, comment.author_name, comment.content, comment.timestamp_iso),
                        )
                inserted = True
            except Exception as exc:
                logger.warning("SQLite comment insert failed (%s)", exc.__class__.__name__)

        if not inserted:
            self.comments.append(comment)
            if len(self.comments) > 600:
                self.comments = self.comments[-600:]

        return self.get_clustered_incidents(rain_mm_h=rain_mm_h, tide_level_m=tide_level_m)

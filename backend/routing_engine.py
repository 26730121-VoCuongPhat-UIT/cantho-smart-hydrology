"""Flood-Aware Multi-Vehicle Routing, Community Sensor Fusion, Hybrid Endpoints & Departure Advisor.

Time Complexity: O((V + E) log V) using binary-heap priority queue in A* / Dijkstra.
Space Complexity: O(V + E) for adjacency representation of Can Tho's road network.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import networkx as nx
import requests

from backend.cantho_geo import (
    CANTHO_NODES,
    NODES_BY_ID,
    ROAD_SEGMENTS,
    RoadSegment,
    haversine_m,
    nearest_nodes_for_coord,
    polyline_length_m,
)
from backend.osm_graph import CanThoOSMGraph, get_osm_graph

MAX_NETWORK_SPEED_MPS = 60.0 / 3.6  # Upper bound for admissible A* travel-time heuristic
VN_TZ = timezone(timedelta(hours=7))
_CONNECTOR_CACHE: Dict[Tuple[float, float, float, float], Tuple[float, List[List[float]]]] = {}


@dataclass(frozen=True)
class VehicleProfile:
    """Safety threshold and speed characteristics per vehicle type."""

    id: str
    name: str
    max_safe_depth_cm: float
    description: str


VEHICLE_PROFILES: Dict[str, VehicleProfile] = {
    "scooter": VehicleProfile(
        id="scooter",
        name="Xe tay ga (Vision, AirBlade, Lead)",
        max_safe_depth_cm=18.0,
        description="Ống xả & lọc gió thấp, dễ chết máy khi ngập ≥ 18 cm.",
    ),
    "manual_bike": VehicleProfile(
        id="manual_bike",
        name="Xe số / Côn tay (Wave, Sirius, Exciter)",
        max_safe_depth_cm=25.0,
        description="Gầm cao hơn xe ga, ngưỡng an toàn < 25 cm.",
    ),
    "sedan": VehicleProfile(
        id="sedan",
        name="Ô tô 4 chỗ Sedan",
        max_safe_depth_cm=28.0,
        description="Nguy cơ thủy kích hoặc ngập sàn xe khi ≥ 28 cm.",
    ),
    "suv": VehicleProfile(
        id="suv",
        name="Ô tô gầm cao SUV / Bán tải",
        max_safe_depth_cm=45.0,
        description="Khả năng lội nước cao, ngưỡng an toàn < 45 cm.",
    ),
}


def _build_base_graph() -> nx.Graph:
    """Construct undirected weighted road network graph for Can Tho."""
    graph = nx.Graph()
    for node in CANTHO_NODES:
        graph.add_node(node.id, data=node)
    for seg in ROAD_SEGMENTS:
        graph.add_edge(seg.u, seg.v, segment=seg)
    return graph


BASE_ROAD_GRAPH = _build_base_graph()


def _heuristic_travel_time_sec(u: str, v: str) -> float:
    """Admissible Haversine lower-bound travel time heuristic (seconds) for A*."""
    nu, nv = NODES_BY_ID[u], NODES_BY_ID[v]
    return haversine_m(nu.lat, nu.lon, nv.lat, nv.lon) / MAX_NETWORK_SPEED_MPS


def _bearing_deg(a: List[float], b: List[float]) -> float:
    """Compute forward azimuth in degrees [0, 360) from coordinate pair a to b."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dl = lon2 - lon1
    y = math.sin(dl) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _angle_diff_deg(b1: float, b2: float) -> float:
    """Smallest angular difference in degrees [0, 180]."""
    d = abs(b1 - b2) % 360.0
    return 360.0 - d if d > 180.0 else d


def prune_parallel_backtracks(
    coords: List[List[float]],
    max_gap_m: float = 36.0,
    min_loop_m: float = 42.0,
    max_loop_m: float = 750.0,
) -> List[List[float]]:
    """Collapse dual-carriageway U-turn spurs and tight out-and-back double lines."""
    if len(coords) < 3:
        return [list(p) for p in coords]

    pts: List[List[float]] = [list(coords[0])]
    for p in coords[1:]:
        if haversine_m(pts[-1][0], pts[-1][1], p[0], p[1]) >= 1.5:
            pts.append(list(p))
    if len(pts) == 1 and len(coords) >= 2:
        pts.append(list(coords[-1]))

    for _ in range(12):
        if len(pts) < 4:
            break
        cum = [0.0]
        for k in range(1, len(pts)):
            cum.append(cum[-1] + haversine_m(pts[k - 1][0], pts[k - 1][1], pts[k][0], pts[k][1]))

        best_loop: Optional[Tuple[float, int, int, float]] = None
        for i in range(len(pts) - 2):
            pi = pts[i]
            for j in range(i + 2, len(pts)):
                loop_len = cum[j] - cum[i]
                if loop_len < min_loop_m:
                    continue
                if loop_len > max_loop_m:
                    break
                pj = pts[j]
                gap = haversine_m(pi[0], pi[1], pj[0], pj[1])
                if gap > max_gap_m:
                    continue
                mid_idx = max(range(i + 1, j), key=lambda m: haversine_m(pi[0], pi[1], pts[m][0], pts[m][1]))
                pm = pts[mid_idx]
                reach = haversine_m(pi[0], pi[1], pm[0], pm[1])
                if reach < max(24.0, 1.25 * gap):
                    continue
                heading_reversal = _angle_diff_deg(_bearing_deg(pi, pm), _bearing_deg(pm, pj))
                if heading_reversal >= 115.0:
                    if best_loop is None or loop_len > best_loop[0]:
                        best_loop = (loop_len, i, j, gap)

        if best_loop is not None:
            _, i, j, gap = best_loop
            tail = pts[j:] if (gap >= 6.0 or j == len(pts) - 1) else pts[j + 1 :]
            pts = pts[: i + 1] + tail
            continue

        spike_removed = False
        for k in range(1, len(pts) - 1):
            d1 = haversine_m(pts[k - 1][0], pts[k - 1][1], pts[k][0], pts[k][1])
            d2 = haversine_m(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1])
            if 1.0 < d1 < 40.0 and 1.0 < d2 < 40.0:
                if _angle_diff_deg(_bearing_deg(pts[k - 1], pts[k]), _bearing_deg(pts[k], pts[k + 1])) > 145.0:
                    pts.pop(k)
                    spike_removed = True
                    break
        if not spike_removed:
            break

    return pts


def fuse_community_incidents_into_roads(
    road_depths: Dict[str, float],
    master_incidents: List[Dict],
    match_radius_m: float = 220.0,
) -> Tuple[Dict[str, float], Dict[str, Dict]]:
    """Fuse verified ST-DBSCAN community reports onto nearby Can Tho roads using max(AI_depth, community_depth)."""
    fused = dict(road_depths)
    overrides: Dict[str, Dict] = {}
    if not master_incidents:
        return fused, overrides

    for inc in master_incidents:
        if not inc.get("fusion_eligible", inc.get("report_count", 0) >= 2):
            continue
        inc_lat = float(inc["lat"])
        inc_lon = float(inc["lon"])
        inc_depth = float(inc.get("avg_depth_cm", 0.0))
        if inc_depth < 8.0:
            continue

        for seg in ROAD_SEGMENTS:
            sample_pts = seg.coords[::2] if len(seg.coords) > 6 else seg.coords
            if not sample_pts:
                nu, nv = NODES_BY_ID[seg.u], NODES_BY_ID[seg.v]
                sample_pts = ((nu.lat, nu.lon), (nv.lat, nv.lon))
            min_d = min(haversine_m(inc_lat, inc_lon, pt[0], pt[1]) for pt in sample_pts)
            if min_d <= match_radius_m and inc_depth > float(fused.get(seg.id, 0.0)):
                fused[seg.id] = round(inc_depth, 1)
                overrides[seg.id] = {
                    "incident_id": inc.get("incident_id", "INC"),
                    "location_label": inc.get("location_label", "Cộng đồng"),
                    "report_count": int(inc.get("report_count", 2)),
                    "community_depth_cm": round(inc_depth, 1),
                    "distance_m": round(min_d, 1),
                }
    return fused, overrides


def _extract_route_details(
    node_path: List[str],
    road_depths: Dict[str, float],
    safe_threshold_cm: float,
    community_overrides: Dict[str, Dict] | None = None,
) -> Dict:
    """Compute coordinates, total length, estimated actual travel time, and flood exposure along a path."""
    if not node_path:
        return {}

    overrides = community_overrides or {}
    coords: List[List[float]] = []
    if len(node_path) == 1:
        n0 = NODES_BY_ID[node_path[0]]
        coords = [[n0.lat, n0.lon]]

    segments_info: List[Dict] = []
    total_length_m = 0.0
    estimated_time_sec = 0.0
    max_depth_cm = 0.0
    dangerous_segments: List[Dict] = []

    for i in range(len(node_path) - 1):
        u, v = node_path[i], node_path[i + 1]
        seg: RoadSegment = BASE_ROAD_GRAPH[u][v]["segment"]
        seg_pts = (
            [[p[0], p[1]] for p in seg.coords]
            if seg.coords
            else [[NODES_BY_ID[seg.u].lat, NODES_BY_ID[seg.u].lon], [NODES_BY_ID[seg.v].lat, NODES_BY_ID[seg.v].lon]]
        )
        if seg.u != u:
            seg_pts = list(reversed(seg_pts))

        if not coords:
            coords.extend(seg_pts)
        elif coords[-1] == seg_pts[0]:
            coords.extend(seg_pts[1:])
        else:
            coords.extend(seg_pts)

        depth = float(road_depths.get(seg.id, 0.0))
        total_length_m += seg.length_m
        max_depth_cm = max(max_depth_cm, depth)

        speed_mps = seg.base_speed_kmh / 3.6
        if depth >= safe_threshold_cm:
            effective_speed = speed_mps * 0.28
        elif depth >= 10.0:
            effective_speed = speed_mps * (1.0 - 0.45 * (depth / safe_threshold_cm))
        else:
            effective_speed = speed_mps
        estimated_time_sec += seg.length_m / max(1.5, effective_speed)

        seg_source = "community_1022" if seg.id in overrides else "ai_model"
        seg_detail = {
            "id": seg.id,
            "name": seg.name,
            "length_m": seg.length_m,
            "depth_cm": round(depth, 1),
            "is_dangerous": depth >= safe_threshold_cm,
            "source": seg_source,
            "coords": seg_pts,
            "mid_coord": seg_pts[len(seg_pts) // 2],
        }
        segments_info.append(seg_detail)
        if depth >= safe_threshold_cm:
            dangerous_segments.append(seg_detail)

    if len(coords) >= 3:
        coords = prune_parallel_backtracks(coords)
        cleaned_len_m = polyline_length_m(coords)
        if total_length_m > 0.0 and 0.0 < cleaned_len_m < (total_length_m - 15.0):
            ratio = max(0.35, cleaned_len_m / total_length_m)
            total_length_m = cleaned_len_m
            estimated_time_sec *= ratio

    return {
        "node_path": node_path,
        "node_names": [NODES_BY_ID[nid].name for nid in node_path],
        "coords": coords,
        "segments": segments_info,
        "total_length_km": round(total_length_m / 1000.0, 2),
        "estimated_minutes": round(estimated_time_sec / 60.0, 1),
        "max_depth_cm": round(max_depth_cm, 1),
        "dangerous_segments": dangerous_segments,
        "is_safe": len(dangerous_segments) == 0,
    }


def find_standard_shortest_path(
    start_id: str,
    end_id: str,
    road_depths: Dict[str, float],
    safe_threshold_cm: float,
    community_overrides: Dict[str, Dict] | None = None,
) -> Dict:
    """Baseline Dijkstra shortest-time route ignoring flood depths."""
    if start_id == end_id:
        return _extract_route_details([start_id], road_depths, safe_threshold_cm, community_overrides)

    def dry_weight(u: str, v: str, edge_attr: Dict) -> float:
        seg: RoadSegment = edge_attr["segment"]
        return seg.length_m / (seg.base_speed_kmh / 3.6)

    path = nx.shortest_path(BASE_ROAD_GRAPH, source=start_id, target=end_id, weight=dry_weight)
    return _extract_route_details(path, road_depths, safe_threshold_cm, community_overrides)


def build_google_maps_url(
    coords: List[List[float]],
    start_coord: Tuple[float, float],
    end_coord: Tuple[float, float],
    is_detour: bool = False,
) -> str:
    """Build a free Google Maps Directions deep-link embedding Flood-Aware waypoints when detouring."""
    origin = f"{start_coord[0]:.6f},{start_coord[1]:.6f}"
    dest = f"{end_coord[0]:.6f},{end_coord[1]:.6f}"
    base = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={dest}&travelmode=driving"
    if not coords or len(coords) < 5:
        return base

    total_m = polyline_length_m(coords)
    if not is_detour and total_m < 1200.0:
        return base

    # Sample up to 5 evenly-spaced turning waypoints along the interior of the Flood-Aware path
    lo_idx = max(1, int(len(coords) * 0.14))
    hi_idx = min(len(coords) - 2, int(len(coords) * 0.86))
    if hi_idx <= lo_idx:
        return base

    interior = coords[lo_idx : hi_idx + 1]
    max_wp = 5
    chosen_pts: List[List[float]] = []
    if len(interior) <= max_wp:
        chosen_pts = interior
    else:
        step = (len(interior) - 1) / float(max_wp - 1)
        for k in range(max_wp):
            chosen_pts.append(interior[int(round(k * step))])

    # Deduplicate close consecutive waypoints (< 120m)
    filtered: List[List[float]] = []
    for pt in chosen_pts:
        if (
            haversine_m(start_coord[0], start_coord[1], pt[0], pt[1]) >= 100.0
            and haversine_m(end_coord[0], end_coord[1], pt[0], pt[1]) >= 100.0
            and (not filtered or haversine_m(filtered[-1][0], filtered[-1][1], pt[0], pt[1]) >= 120.0)
        ):
            filtered.append(pt)

    if not filtered:
        return base
    wp_param = quote("|".join(f"{p[0]:.6f},{p[1]:.6f}" for p in filtered), safe=",")
    return f"{base}&waypoints={wp_param}"


def _finalize_osm_route(
    raw: Dict,
    start_coord: Tuple[float, float],
    end_coord: Tuple[float, float],
    start_offset_m: float,
    end_offset_m: float,
    start_label: str,
    end_label: str,
) -> Dict:
    """Attach pin connectors, prune double-backs, and format response matching legacy schema."""
    coords = [list(p) for p in (raw.get("coords") or [])]
    extra_m = 0.0
    if 3.0 <= start_offset_m <= 180.0 and coords:
        if haversine_m(start_coord[0], start_coord[1], coords[0][0], coords[0][1]) >= 3.0:
            coords.insert(0, [round(start_coord[0], 6), round(start_coord[1], 6)])
            extra_m += start_offset_m
    if 3.0 <= end_offset_m <= 180.0 and coords:
        if haversine_m(end_coord[0], end_coord[1], coords[-1][0], coords[-1][1]) >= 3.0:
            coords.append([round(end_coord[0], 6), round(end_coord[1], 6)])
            extra_m += end_offset_m

    raw_total_m = float(raw.get("total_length_m", 0.0)) + extra_m
    raw_time_sec = float(raw.get("estimated_time_sec", 0.0)) + (extra_m / (24.0 / 3.6))

    pre_prune_len = polyline_length_m(coords)
    coords = prune_parallel_backtracks(coords)
    post_prune_len = polyline_length_m(coords)

    if pre_prune_len > 0.0 and 0.0 < post_prune_len < (pre_prune_len - 15.0):
        shrink = max(0.35, post_prune_len / pre_prune_len)
        raw_total_m *= shrink
        raw_time_sec *= shrink

    # Ensure len(coords) > len(node_path) contract when multi-segment route has straight stretches
    node_path = list(raw.get("node_path") or [])
    if len(coords) <= len(node_path) and len(node_path) >= 2:
        node_path = [node_path[0], node_path[-1]]

    return {
        "node_path": node_path,
        "node_names": [start_label] + [s["name"] for s in raw.get("segments", [])[:3]] + [end_label],
        "coords": coords,
        "segments": raw.get("segments", []),
        "total_length_km": round(raw_total_m / 1000.0, 2),
        "estimated_minutes": round(raw_time_sec / 60.0, 1),
        "max_depth_cm": float(raw.get("max_depth_cm", 0.0)),
        "dangerous_segments": raw.get("dangerous_segments", []),
        "is_safe": bool(raw.get("is_safe", True)),
        "fallback_used": bool(raw.get("fallback_used", False)),
    }


def _route_via_osm_graph(
    osm_g: CanThoOSMGraph,
    start_coord: Tuple[float, float],
    end_coord: Tuple[float, float],
    start_label: str,
    end_label: str,
    vehicle_type: str,
    road_depths: Dict[str, float],
    alpha: float = 2.6,
    community_overrides: Dict[str, Dict] | None = None,
    hex_depths: Optional[List[float]] = None,
    master_incidents: Optional[List[Dict]] = None,
) -> Dict:
    """Compute shortest baseline vs Flood-Aware A* route directly on contracted Can Tho OSM graph."""
    profile = VEHICLE_PROFILES.get(vehicle_type, VEHICLE_PROFILES["scooter"])
    tau_v = profile.max_safe_depth_cm
    overrides_legacy = community_overrides or {}

    edge_depths, edge_overrides = osm_g.compute_edge_depths(
        road_depths=road_depths,
        hex_depths=hex_depths,
        community_incidents=master_incidents,
    )
    anchor_a = osm_g.snap_coord(start_coord[0], start_coord[1])
    anchor_b = osm_g.snap_coord(end_coord[0], end_coord[1])

    std_raw = osm_g.route_between_anchors(
        anchor_a=anchor_a,
        anchor_b=anchor_b,
        edge_depths=edge_depths,
        overrides=edge_overrides,
        safe_threshold_cm=tau_v,
        flood_aware=False,
        alpha=alpha,
    )
    safe_raw = osm_g.route_between_anchors(
        anchor_a=anchor_a,
        anchor_b=anchor_b,
        edge_depths=edge_depths,
        overrides=edge_overrides,
        safe_threshold_cm=tau_v,
        flood_aware=True,
        alpha=alpha,
    )

    baseline = _finalize_osm_route(
        std_raw, start_coord, end_coord, anchor_a.snap_offset_m, anchor_b.snap_offset_m, start_label, end_label
    )
    flood_aware = _finalize_osm_route(
        safe_raw, start_coord, end_coord, anchor_a.snap_offset_m, anchor_b.snap_offset_m, start_label, end_label
    )

    safe_seg_names = {s["name"] for s in flood_aware["segments"]}
    safe_seg_ids = {s["id"] for s in flood_aware["segments"]}
    avoided_roads = [
        seg
        for seg in baseline["dangerous_segments"]
        if seg["id"] not in safe_seg_ids or seg["name"] not in safe_seg_names
    ]
    detoured = (not baseline["is_safe"]) and (
        abs(baseline["total_length_km"] - flood_aware["total_length_km"]) >= 0.03
        or len(avoided_roads) > 0
        or len(baseline["coords"]) != len(flood_aware["coords"])
    )
    if detoured and not avoided_roads and baseline["dangerous_segments"]:
        avoided_roads = list(baseline["dangerous_segments"])

    used_community_detour = any(s.get("source") == "community_1022" for s in avoided_roads)

    if baseline["is_safe"]:
        msg = (
            f"Lộ trình ngắn nhất ({baseline['total_length_km']} km) hoàn toàn an toàn "
            f"cho {profile.name} (< {tau_v:.0f} cm)."
        )
    elif flood_aware["is_safe"]:
        extra_km = round(max(0.0, flood_aware["total_length_km"] - baseline["total_length_km"]), 2)
        src_tag = " (kết hợp tin báo cộng đồng)" if used_community_detour else ""
        msg = (
            f"Đã tìm thấy lộ trình tránh ngập{src_tag} (+{extra_km} km, tối đa {flood_aware['max_depth_cm']:.0f} cm), "
            f"né {len(avoided_roads)} đoạn ngập sâu ≥ {tau_v:.0f} cm."
        )
    else:
        msg = (
            f"Cảnh báo siêu ngập: không còn lối thoát khô ráo hoàn toàn cho {profile.name}. "
            f"Hệ thống đề xuất tuyến ít ngập nhất ({flood_aware['max_depth_cm']:.0f} cm)."
        )

    fused_list = [{"road_id": rid, **meta} for rid, meta in overrides_legacy.items()]
    if not fused_list and edge_overrides:
        seen_inc = set()
        for eidx, meta in edge_overrides.items():
            iid = meta.get("incident_id", str(eidx))
            if iid not in seen_inc:
                seen_inc.add(iid)
                fused_list.append({"road_id": f"osm_{eidx}", **meta})

    return {
        "vehicle": asdict(profile),
        "standard_route": baseline,
        "flood_aware_route": flood_aware,
        "avoided_roads": avoided_roads,
        "community_fused_roads": fused_list,
        "status_message": msg,
        "google_maps_url": build_google_maps_url(
            flood_aware["coords"], start_coord, end_coord, is_detour=detoured
        ),
        "osm_snap": {
            "start_offset_m": anchor_a.snap_offset_m,
            "end_offset_m": anchor_b.snap_offset_m,
        },
    }


def find_flood_aware_route(
    start_id: str,
    end_id: str,
    vehicle_type: str,
    road_depths: Dict[str, float],
    alpha: float = 2.6,
    community_overrides: Dict[str, Dict] | None = None,
    hex_depths: Optional[List[float]] = None,
    master_incidents: Optional[List[Dict]] = None,
) -> Dict:
    """Flood-Aware A* routing tailored to vehicle wading threshold tau_v and optional community overrides."""
    if start_id not in NODES_BY_ID or end_id not in NODES_BY_ID:
        raise ValueError(f"Invalid node ID: {start_id} or {end_id}")

    overrides = community_overrides or {}
    profile = VEHICLE_PROFILES.get(vehicle_type, VEHICLE_PROFILES["scooter"])
    tau_v = profile.max_safe_depth_cm
    nu, nv = NODES_BY_ID[start_id], NODES_BY_ID[end_id]

    if start_id == end_id:
        baseline = find_standard_shortest_path(start_id, end_id, road_depths, tau_v, overrides)
        safe_route = dict(baseline)
        safe_route["fallback_used"] = False
        return {
            "vehicle": asdict(profile),
            "standard_route": baseline,
            "flood_aware_route": safe_route,
            "avoided_roads": [],
            "community_fused_roads": [{"road_id": rid, **meta} for rid, meta in overrides.items()],
            "status_message": "Điểm xuất phát và điểm đến trùng nhau.",
            "google_maps_url": build_google_maps_url([], (nu.lat, nu.lon), (nv.lat, nv.lon), is_detour=False),
        }

    osm_g = get_osm_graph()
    if osm_g is not None:
        return _route_via_osm_graph(
            osm_g=osm_g,
            start_coord=(nu.lat, nu.lon),
            end_coord=(nv.lat, nv.lon),
            start_label=nu.name,
            end_label=nv.name,
            vehicle_type=vehicle_type,
            road_depths=road_depths,
            alpha=alpha,
            community_overrides=overrides,
            hex_depths=hex_depths,
            master_incidents=master_incidents,
        )

    baseline = find_standard_shortest_path(start_id, end_id, road_depths, tau_v, overrides)

    if start_id == end_id:
        safe_route = dict(baseline)
        safe_route["fallback_used"] = False
        return {
            "vehicle": asdict(profile),
            "standard_route": baseline,
            "flood_aware_route": safe_route,
            "avoided_roads": [],
            "community_fused_roads": [
                {"road_id": rid, **meta} for rid, meta in overrides.items()
            ],
            "status_message": "Điểm xuất phát và điểm đến trùng nhau.",
        }

    safe_graph = nx.Graph()
    for node in CANTHO_NODES:
        safe_graph.add_node(node.id)
    for seg in ROAD_SEGMENTS:
        depth = float(road_depths.get(seg.id, 0.0))
        if depth < tau_v:
            base_time_sec = seg.length_m / (seg.base_speed_kmh / 3.6)
            flood_penalty = 1.0 + alpha * ((depth / tau_v) ** 2)
            safe_graph.add_edge(seg.u, seg.v, segment=seg, weight=base_time_sec * flood_penalty)

    fallback_used = False
    try:
        safe_path = nx.astar_path(
            safe_graph,
            source=start_id,
            target=end_id,
            heuristic=_heuristic_travel_time_sec,
            weight="weight",
        )
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        fallback_used = True
        risk_graph = nx.Graph()
        for node in CANTHO_NODES:
            risk_graph.add_node(node.id)
        for seg in ROAD_SEGMENTS:
            depth = float(road_depths.get(seg.id, 0.0))
            base_time_sec = seg.length_m / (seg.base_speed_kmh / 3.6)
            ratio = depth / tau_v
            penalty = 1.0 + (alpha * ratio ** 2 if depth < tau_v else 85.0 * (ratio ** 3))
            risk_graph.add_edge(seg.u, seg.v, segment=seg, weight=base_time_sec * penalty)
        safe_path = nx.astar_path(
            risk_graph,
            source=start_id,
            target=end_id,
            heuristic=_heuristic_travel_time_sec,
            weight="weight",
        )

    flood_aware = _extract_route_details(safe_path, road_depths, tau_v, overrides)
    flood_aware["fallback_used"] = fallback_used

    safe_seg_ids = {s["id"] for s in flood_aware["segments"]}
    avoided_roads = [
        seg for seg in baseline["dangerous_segments"] if seg["id"] not in safe_seg_ids
    ]
    used_community_detour = any(s.get("source") == "community_1022" for s in avoided_roads)

    if baseline["is_safe"]:
        msg = (
            f"Lộ trình ngắn nhất ({baseline['total_length_km']} km) hoàn toàn an toàn "
            f"cho {profile.name} (< {tau_v:.0f} cm)."
        )
    elif flood_aware["is_safe"]:
        extra_km = round(flood_aware["total_length_km"] - baseline["total_length_km"], 2)
        avoided_names = ", ".join(s["name"].split("(")[0].strip() for s in avoided_roads[:2])
        src_note = " (nguồn Cộng đồng)" if used_community_detour else ""
        msg = (
            f"Đã né {len(baseline['dangerous_segments'])} đoạn ngập sâu (như {avoided_names}, "
            f"ngập tới {baseline['max_depth_cm']} cm{src_note}). Lộ trình mới dài hơn {max(0.0, extra_km):.2f} km "
            f"nhưng an toàn 100% cho {profile.name}."
        )
    else:
        msg = (
            f"Cảnh báo ngập diện rộng: không có đường khô hoàn toàn (< {tau_v:.0f} cm). "
            f"Hệ thống đã chọn lộ trình ít ngập nhất (tối đa {flood_aware['max_depth_cm']} cm)."
        )

    return {
        "vehicle": asdict(profile),
        "standard_route": baseline,
        "flood_aware_route": flood_aware,
        "avoided_roads": avoided_roads,
        "community_fused_roads": [
            {"road_id": rid, **meta} for rid, meta in overrides.items()
        ],
        "status_message": msg,
    }


def _fetch_connector_street_path(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> Tuple[float, List[List[float]]]:
    """Fetch first/last-mile street polyline via OSRM with LRU cache & Haversine fallback."""
    dist_hav = haversine_m(lat1, lon1, lat2, lon2)
    if dist_hav <= 25.0:
        return round(dist_hav, 1), [[round(lat1, 6), round(lon1, 6)], [round(lat2, 6), round(lon2, 6)]]

    key = (round(lat1, 5), round(lon1, 5), round(lat2, 5), round(lon2, 5))
    if key in _CONNECTOR_CACHE:
        return _CONNECTOR_CACHE[key]

    url = f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"
    try:
        resp = requests.get(url, timeout=2.5, headers={"User-Agent": "CanThoSmartCity/2.0"})
        if resp.status_code == 200:
            routes = resp.json().get("routes") or []
            if routes:
                raw_coords = routes[0].get("geometry", {}).get("coordinates") or []
                if len(raw_coords) >= 2:
                    coords = prune_parallel_backtracks(
                        [[round(float(la), 6), round(float(lo), 6)] for lo, la in raw_coords]
                    )
                    length = polyline_length_m(coords)
                    if len(_CONNECTOR_CACHE) > 256:
                        _CONNECTOR_CACHE.clear()
                    _CONNECTOR_CACHE[key] = (length, coords)
                    return length, coords
    except Exception:
        pass

    fallback_pts = [[round(lat1, 6), round(lon1, 6)], [round(lat2, 6), round(lon2, 6)]]
    return round(dist_hav * 1.15, 1), fallback_pts


def _stitch_connector(
    route_obj: Dict,
    prefix_pts: Optional[List[List[float]]],
    prefix_len_m: float,
    prefix_label: Optional[str],
    suffix_pts: Optional[List[List[float]]],
    suffix_len_m: float,
    suffix_label: Optional[str],
) -> Dict:
    """Prepend/append first-mile and last-mile street connectors onto a core route result."""
    out = dict(route_obj)
    coords = list(out.get("coords") or [])
    extra_len_m = 0.0

    if prefix_pts and len(prefix_pts) >= 2 and prefix_len_m > 25.0:
        coords = prefix_pts + (coords[1:] if coords and coords[0] == prefix_pts[-1] else coords)
        extra_len_m += prefix_len_m
        if prefix_label:
            names = list(out.get("node_names") or [])
            out["node_names"] = [prefix_label] + names

    if suffix_pts and len(suffix_pts) >= 2 and suffix_len_m > 25.0:
        coords = (coords + suffix_pts[1:]) if coords and coords[-1] == suffix_pts[0] else (coords + suffix_pts)
        extra_len_m += suffix_len_m
        if suffix_label:
            names = list(out.get("node_names") or [])
            out["node_names"] = names + [suffix_label]

    raw_len_m = polyline_length_m(coords)
    coords = prune_parallel_backtracks(coords)
    pruned_len_m = polyline_length_m(coords)
    out["coords"] = coords
    if extra_len_m > 0 or (raw_len_m > 0 and pruned_len_m < raw_len_m - 15.0):
        base_km = float(out.get("total_length_km", 0.0))
        new_total_km = round(base_km + extra_len_m / 1000.0, 2)
        if raw_len_m > 0 and pruned_len_m < raw_len_m - 15.0:
            shrink = max(0.35, pruned_len_m / raw_len_m)
            new_total_km = round(new_total_km * shrink, 2)
            out["estimated_minutes"] = round((float(out.get("estimated_minutes", 0.0)) + (extra_len_m / (32.0 / 3.6)) / 60.0) * shrink, 1)
        else:
            extra_min = (extra_len_m / (32.0 / 3.6)) / 60.0
            out["estimated_minutes"] = round(float(out.get("estimated_minutes", 0.0)) + extra_min, 1)
        out["total_length_km"] = new_total_km
    return out


def find_hybrid_flood_aware_route(
    start_id: str,
    end_id: str,
    vehicle_type: str,
    road_depths: Dict[str, float],
    start_coord: Optional[Tuple[float, float]] = None,
    end_coord: Optional[Tuple[float, float]] = None,
    start_label: Optional[str] = None,
    end_label: Optional[str] = None,
    community_overrides: Dict[str, Dict] | None = None,
    hex_depths: Optional[List[float]] = None,
    master_incidents: Optional[List[Dict]] = None,
) -> Dict:
    """Support both exact preset intersections and arbitrary map pins/addresses anywhere in Can Tho."""
    if start_coord is None and end_coord is None:
        return find_flood_aware_route(
            start_id=start_id,
            end_id=end_id,
            vehicle_type=vehicle_type,
            road_depths=road_depths,
            community_overrides=community_overrides,
            hex_depths=hex_depths,
            master_incidents=master_incidents,
        )

    osm_g = get_osm_graph()
    if osm_g is not None:
        if start_coord is None and start_id not in NODES_BY_ID:
            raise ValueError(f"Invalid node ID: {start_id}")
        if end_coord is None and end_id not in NODES_BY_ID:
            raise ValueError(f"Invalid node ID: {end_id}")

        s_pt = start_coord if start_coord is not None else (NODES_BY_ID[start_id].lat, NODES_BY_ID[start_id].lon)
        e_pt = end_coord if end_coord is not None else (NODES_BY_ID[end_id].lat, NODES_BY_ID[end_id].lon)
        s_lbl = start_label or (NODES_BY_ID[start_id].name if start_id in NODES_BY_ID else "Điểm A")
        e_lbl = end_label or (NODES_BY_ID[end_id].name if end_id in NODES_BY_ID else "Điểm B")

        if haversine_m(s_pt[0], s_pt[1], e_pt[0], e_pt[1]) < 15.0:
            profile = VEHICLE_PROFILES.get(vehicle_type, VEHICLE_PROFILES["scooter"])
            pt = [round(s_pt[0], 6), round(s_pt[1], 6)]
            same_route = {
                "node_path": [start_id],
                "node_names": [s_lbl],
                "coords": [pt],
                "segments": [],
                "total_length_km": 0.0,
                "estimated_minutes": 0.0,
                "max_depth_cm": 0.0,
                "dangerous_segments": [],
                "is_safe": True,
                "fallback_used": False,
            }
            return {
                "vehicle": asdict(profile),
                "standard_route": same_route,
                "flood_aware_route": dict(same_route),
                "avoided_roads": [],
                "community_fused_roads": [],
                "status_message": "Điểm xuất phát và điểm đến trùng nhau.",
                "google_maps_url": build_google_maps_url([], s_pt, e_pt, is_detour=False),
                "hybrid_endpoints": {"custom_start": list(s_pt), "custom_end": list(e_pt)},
            }

        res = _route_via_osm_graph(
            osm_g=osm_g,
            start_coord=s_pt,
            end_coord=e_pt,
            start_label=s_lbl,
            end_label=e_lbl,
            vehicle_type=vehicle_type,
            road_depths=road_depths,
            community_overrides=community_overrides,
            hex_depths=hex_depths,
            master_incidents=master_incidents,
        )
        res["hybrid_endpoints"] = {
            "custom_start": list(start_coord) if start_coord else None,
            "custom_end": list(end_coord) if end_coord else None,
            "snapped_start_offset_m": res.get("osm_snap", {}).get("start_offset_m"),
            "snapped_end_offset_m": res.get("osm_snap", {}).get("end_offset_m"),
        }
        return res

    start_candidates = (
        [n.id for n, _ in nearest_nodes_for_coord(start_coord[0], start_coord[1], k=2)]
        if start_coord is not None
        else [start_id]
    )
    end_candidates = (
        [n.id for n, _ in nearest_nodes_for_coord(end_coord[0], end_coord[1], k=2)]
        if end_coord is not None
        else [end_id]
    )

    best_bundle: Optional[Tuple[float, Dict, str, str]] = None
    for s_nid in start_candidates:
        for e_nid in end_candidates:
            res = find_flood_aware_route(
                start_id=s_nid,
                end_id=e_nid,
                vehicle_type=vehicle_type,
                road_depths=road_depths,
                community_overrides=community_overrides,
            )
            s_off = haversine_m(start_coord[0], start_coord[1], NODES_BY_ID[s_nid].lat, NODES_BY_ID[s_nid].lon) if start_coord else 0.0
            e_off = haversine_m(end_coord[0], end_coord[1], NODES_BY_ID[e_nid].lat, NODES_BY_ID[e_nid].lon) if end_coord else 0.0
            score = (
                (0.0 if res["flood_aware_route"]["is_safe"] else 50_000.0)
                + res["flood_aware_route"]["total_length_km"] * 1000.0
                + s_off
                + e_off
            )
            if best_bundle is None or score < best_bundle[0]:
                best_bundle = (score, res, s_nid, e_nid)

    assert best_bundle is not None
    _, chosen, chosen_s, chosen_e = best_bundle

    pref_pts, pref_len = (None, 0.0)
    if start_coord is not None:
        ns = NODES_BY_ID[chosen_s]
        pref_len, pref_pts = _fetch_connector_street_path(start_coord[0], start_coord[1], ns.lat, ns.lon)

    suff_pts, suff_len = (None, 0.0)
    if end_coord is not None:
        ne = NODES_BY_ID[chosen_e]
        suff_len, suff_pts = _fetch_connector_street_path(ne.lat, ne.lon, end_coord[0], end_coord[1])

    chosen["standard_route"] = _stitch_connector(
        chosen["standard_route"], pref_pts, pref_len, start_label, suff_pts, suff_len, end_label
    )
    chosen["flood_aware_route"] = _stitch_connector(
        chosen["flood_aware_route"], pref_pts, pref_len, start_label, suff_pts, suff_len, end_label
    )
    chosen["hybrid_endpoints"] = {
        "custom_start": list(start_coord) if start_coord else None,
        "custom_end": list(end_coord) if end_coord else None,
        "snapped_start_node": chosen_s,
        "snapped_end_node": chosen_e,
    }
    return chosen


def compute_departure_windows(
    ml_engine,
    start_id: str,
    end_id: str,
    vehicle_type: str,
    hourly_weather: List[Dict],
    hourly_tide: List[Dict],
    base_inputs: Dict,
    community_overrides: Dict[str, Dict] | None = None,
    start_coord: Optional[Tuple[float, float]] = None,
    end_coord: Optional[Tuple[float, float]] = None,
    master_incidents: Optional[List[Dict]] = None,
) -> Dict:
    """Evaluate departure windows (+0h, +1h, +2h, +3h) to advise when flooded streets will drain."""
    if (start_coord is None and start_id not in NODES_BY_ID) or (end_coord is None and end_id not in NODES_BY_ID):
        return {"windows": [], "best_departure_advice": ""}

    now_vn = datetime.now(VN_TZ)
    windows: List[Dict] = []

    tide_by_hour = {int(item.get("hour", 0)): item for item in (hourly_tide or [])}
    current_rain = float(base_inputs.get("rain_mm_h", 0.0))
    current_rain3h = float(base_inputs.get("rain_3h_cum", 0.0))
    current_tide = float(base_inputs.get("tide_level_m", 1.45))
    current_delta = float(base_inputs.get("tide_delta_m", 0.0))
    model_key = str(base_inputs.get("model_key", "hgbr"))

    for offset_h in (0, 1, 2, 3):
        if offset_h == 0:
            tide_m, delta_m, rain_h, rain_3h = current_tide, current_delta, current_rain, current_rain3h
            time_lbl = "Đi ngay"
        else:
            target_hour = (now_vn.hour + offset_h) % 24
            t_item = tide_by_hour.get(target_hour, {})
            tide_m = float(t_item.get("level_m", current_tide))
            delta_m = float(t_item.get("delta_m_h", current_delta))

            w_idx = min(offset_h, max(0, len(hourly_weather) - 1)) if hourly_weather else -1
            rain_h = float(hourly_weather[w_idx].get("precip_mm", current_rain)) if w_idx >= 0 else current_rain
            rain_3h = round(sum(
                float(hourly_weather[min(k, len(hourly_weather) - 1)].get("precip_mm", 0.0))
                for k in range(max(0, w_idx - 2), w_idx + 1)
            ), 1) if hourly_weather else round(rain_h * 1.6, 1)
            time_lbl = f"{(now_vn + timedelta(hours=offset_h)).strftime('%H:%M')} (+{offset_h}h)"

        pred = ml_engine.predict_city_flood_map(
            tide_level_m=tide_m,
            rain_mm_h=rain_h,
            rain_3h_cum=rain_3h,
            tide_delta_m=delta_m,
            horizon="now",
            model_key=model_key,
        )
        depths = dict(pred["road_depths"])
        hex_depths = [float(c["depth_cm"]) for c in pred.get("hex_cells", [])]
        if offset_h == 0 and community_overrides:
            for rid, meta in community_overrides.items():
                depths[rid] = max(float(depths.get(rid, 0.0)), float(meta["community_depth_cm"]))

        rt = find_hybrid_flood_aware_route(
            start_id=start_id,
            end_id=end_id,
            vehicle_type=vehicle_type,
            road_depths=depths,
            start_coord=start_coord,
            end_coord=end_coord,
            community_overrides=community_overrides if offset_h == 0 else None,
            hex_depths=hex_depths,
            master_incidents=master_incidents if offset_h == 0 else None,
        )
        std = rt["standard_route"]
        safe = rt["flood_aware_route"]
        windows.append(
            {
                "offset_h": offset_h,
                "time_label": time_lbl,
                "tide_m": round(tide_m, 2),
                "rain_mm_h": round(rain_h, 1),
                "is_direct_safe": bool(std["is_safe"]),
                "is_detour_safe": bool(safe["is_safe"]),
                "safe_km": safe["total_length_km"],
                "safe_min": safe["estimated_minutes"],
                "max_depth_cm": safe["max_depth_cm"],
                "direct_max_depth_cm": std["max_depth_cm"],
            }
        )

    now_win = windows[0]
    if now_win["is_direct_safe"]:
        advice = f"Xuất hành ngay thuận lợi: đường ngắn nhất khô ráo ({now_win['direct_max_depth_cm']:.0f} cm)."
    else:
        better = next((w for w in windows[1:] if w["is_direct_safe"]), None)
        if better:
            advice = (
                f"Nếu đi ngay cần né ngập ({now_win['safe_km']} km). "
                f"Đợi đến {better['time_label']}: nước rút còn {better['direct_max_depth_cm']:.0f} cm, đi thẳng an toàn."
            )
        else:
            advice = (
                f"Mực nước còn cao trong 3 giờ tới; nên chọn lộ trình vòng an toàn ngay ({now_win['safe_km']} km)."
            )

    return {
        "windows": windows,
        "best_departure_advice": advice,
    }

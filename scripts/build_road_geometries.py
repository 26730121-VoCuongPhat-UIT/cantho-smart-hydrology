"""Build true 42/42 single-line street-snapped road polylines for Can Tho.

Primary source: undirected OpenStreetMap street graph fetched once from Overpass
(maps.mail.ru / overpass-api.de / kumi) with intersection crossovers across
dual-carriageway medians and `prune_parallel_backtracks()` cleanup so NO segment
produces dual-carriageway U-turn spurs or parallel double lines.

Fallback source: OSRM / OSM.de multi-profile routing (also pruned).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import networkx as nx
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.cantho_geo import CANTHO_NODES, NODES_BY_ID, ROAD_SEGMENTS, RoadSegment, haversine_m
from backend.routing_engine import prune_parallel_backtracks

OUTPUT_PATH = PROJECT_ROOT / "data" / "road_geometries.json"
USER_AGENT = "CanThoSmartCity/2.0 (CanTho Flood Map)"
_THREAD_LOCAL = threading.local()

OVERPASS_MIRRORS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

OVERPASS_QUERY = """[out:json][timeout:30];
way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|unclassified|residential|living_street|service)$"]["service"!~"^(parking_aisle|driveway)$"](9.985,105.705,10.090,105.815);
(._;>;);
out body;"""

HW_MULT: Dict[str, float] = {
    "motorway": 1.0,
    "motorway_link": 1.02,
    "trunk": 1.0,
    "trunk_link": 1.02,
    "primary": 1.0,
    "primary_link": 1.02,
    "secondary": 1.03,
    "secondary_link": 1.05,
    "tertiary": 1.06,
    "tertiary_link": 1.08,
    "unclassified": 1.15,
    "residential": 1.25,
    "living_street": 1.4,
    "service": 1.8,
}


def _get_session() -> requests.Session:
    if not hasattr(_THREAD_LOCAL, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT})
        _THREAD_LOCAL.session = s
    return _THREAD_LOCAL.session


def _fetch_overpass_osm_graph() -> Optional[Tuple[nx.Graph, Dict[int, Tuple[float, float]]]]:
    """Fetch Can Tho drivable OSM ways once and build undirected street graph with intersection crossovers."""
    payload = None
    for url in OVERPASS_MIRRORS:
        try:
            resp = requests.post(url, data={"data": OVERPASS_QUERY}, timeout=25, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200 and len(resp.content) > 50_000:
                payload = resp.json()
                print(f"Loaded Overpass OSM street network from {url}", flush=True)
                break
        except Exception:
            continue

    if not payload or "elements" not in payload:
        return None

    osm_nodes: Dict[int, Tuple[float, float]] = {}
    ways: List[Dict] = []
    for el in payload["elements"]:
        if el.get("type") == "node":
            osm_nodes[int(el["id"])] = (float(el["lat"]), float(el["lon"]))
        elif el.get("type") == "way":
            ways.append(el)

    graph = nx.Graph()
    for w in ways:
        hw = w.get("tags", {}).get("highway", "residential")
        mult = HW_MULT.get(hw, 1.3)
        nds = w.get("nodes", [])
        for a, b in zip(nds, nds[1:]):
            ia, ib = int(a), int(b)
            if ia not in osm_nodes or ib not in osm_nodes:
                continue
            la, loa = osm_nodes[ia]
            lb, lob = osm_nodes[ib]
            dist = haversine_m(la, loa, lb, lob)
            wt = dist * mult
            if graph.has_edge(ia, ib):
                if wt < graph[ia][ib]["weight"]:
                    graph[ia][ib]["weight"] = wt
                    graph[ia][ib]["dist"] = dist
            else:
                graph.add_edge(ia, ib, weight=wt, dist=dist)

    if graph.number_of_nodes() == 0:
        return None

    largest_cc = max(nx.connected_components(graph), key=len)
    graph = graph.subgraph(largest_cc).copy()

    # Add zero-detour dual-carriageway crossover links at intersections (spatial grid bucket ~0.00025 deg)
    deg3_nodes = [nid for nid in graph.nodes if graph.degree(nid) >= 3]
    buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    cell_deg = 0.00025
    for nid in deg3_nodes:
        lat, lon = osm_nodes[nid]
        buckets[(int(lat / cell_deg), int(lon / cell_deg))].append(nid)

    crossovers = 0
    for (by, bx), bucket_nids in buckets.items():
        nearby: List[int] = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nearby.extend(buckets.get((by + dy, bx + dx), ()))
        for a in bucket_nids:
            la, loa = osm_nodes[a]
            dists_from_a: Optional[Dict[int, float]] = None
            for b in nearby:
                if b <= a or graph.has_edge(a, b):
                    continue
                lb, lob = osm_nodes[b]
                euclid = haversine_m(la, loa, lb, lob)
                if euclid <= 22.0:
                    if dists_from_a is None:
                        dists_from_a = nx.single_source_dijkstra_path_length(graph, a, cutoff=80.0, weight="dist")
                    if b not in dists_from_a:
                        graph.add_edge(a, b, weight=euclid, dist=euclid)
                        crossovers += 1

    print(
        f"OSM undirected graph ready: {graph.number_of_nodes()} nodes, "
        f"{graph.number_of_edges()} edges ({crossovers} median crossovers)",
        flush=True,
    )
    return graph, osm_nodes


def _snap_nodes_to_osm(
    graph: nx.Graph, osm_nodes: Dict[int, Tuple[float, float]]
) -> Dict[str, Tuple[int, float, Tuple[float, float]]]:
    """Snap each Can Tho GeoNode to nearest OSM intersection (deg>=3 within 55m, else deg>=2)."""
    snapped: Dict[str, Tuple[int, float, Tuple[float, float]]] = {}
    for cn in CANTHO_NODES:
        best_deg3: Optional[Tuple[float, int]] = None
        best_deg2: Optional[Tuple[float, int]] = None
        for nid in graph.nodes:
            lat, lon = osm_nodes[nid]
            if abs(lat - cn.lat) > 0.0045 or abs(lon - cn.lon) > 0.0045:
                continue
            deg = graph.degree(nid)
            if deg < 2:
                continue
            d = haversine_m(cn.lat, cn.lon, lat, lon)
            if best_deg2 is None or d < best_deg2[0]:
                best_deg2 = (d, nid)
            if deg >= 3 and (best_deg3 is None or d < best_deg3[0]):
                best_deg3 = (d, nid)

        chosen = best_deg3 if (best_deg3 is not None and best_deg3[0] <= 55.0) else best_deg2
        assert chosen is not None, f"Could not snap node {cn.id} to OSM graph"
        dist_m, osm_id = chosen
        snapped[cn.id] = (osm_id, dist_m, osm_nodes[osm_id])
    return snapped


def _fetch_route_candidate(url: str, reverse_coords: bool = False) -> Optional[Tuple[float, List[List[float]]]]:
    """Fallback OSRM/OSM.de route query with exponential backoff retries."""
    delays = (0.0, 0.8, 1.8, 3.5)
    for wait_s in delays:
        if wait_s > 0:
            time.sleep(wait_s)
        try:
            resp = _get_session().get(url, timeout=(5, 10))
            if resp.status_code in (429, 500, 502, 503, 504):
                continue
            if resp.status_code != 200:
                return None
            payload = resp.json()
            routes = payload.get("routes") or []
            if not routes:
                return None
            route = routes[0]
            raw_coords = route.get("geometry", {}).get("coordinates") or []
            if len(raw_coords) < 3:
                return None
            coords = [[round(float(lat), 6), round(float(lon), 6)] for lon, lat in raw_coords]
            if reverse_coords:
                coords.reverse()
            coords = prune_parallel_backtracks(coords)
            return float(route.get("distance", 0.0)), coords
        except Exception:
            continue
    return None


def _snap_segment_fallback_osrm(seg: RoadSegment) -> Tuple[str, List[List[float]], bool]:
    """Fallback single-segment snapping if Overpass is completely unreachable."""
    nu, nv = NODES_BY_ID[seg.u], NODES_BY_ID[seg.v]
    hav = max(1.0, haversine_m(nu.lat, nu.lon, nv.lat, nv.lon))

    urls = [
        (f"https://routing.openstreetmap.de/routed-bike/route/v1/driving/{nu.lon},{nu.lat};{nv.lon},{nv.lat}?overview=full&geometries=geojson", False),
        (f"https://router.project-osrm.org/route/v1/driving/{nu.lon},{nu.lat};{nv.lon},{nv.lat}?overview=full&geometries=geojson", False),
        (f"https://router.project-osrm.org/route/v1/driving/{nv.lon},{nv.lat};{nu.lon},{nu.lat}?overview=full&geometries=geojson", True),
    ]
    valid: List[Tuple[float, List[List[float]]]] = []
    for url, rev in urls:
        res = _fetch_route_candidate(url, reverse_coords=rev)
        if res is not None and 0.90 <= (res[0] / hav) <= 5.5 and len(res[1]) >= 3:
            valid.append(res)

    start_pt = [round(nu.lat, 6), round(nu.lon, 6)]
    end_pt = [round(nv.lat, 6), round(nv.lon, 6)]
    if not valid:
        return seg.id, [start_pt, end_pt], False

    _, best_coords = min(valid, key=lambda item: item[0])
    coords = prune_parallel_backtracks(list(best_coords))
    if haversine_m(coords[0][0], coords[0][1], start_pt[0], start_pt[1]) <= 15.0:
        coords[0] = start_pt
    else:
        coords.insert(0, start_pt)
    if haversine_m(coords[-1][0], coords[-1][1], end_pt[0], end_pt[1]) <= 15.0:
        coords[-1] = end_pt
    else:
        coords.append(end_pt)
    return seg.id, coords, True


def _detect_parallel_overlap(coords: List[List[float]]) -> Optional[Tuple[int, int, float, float]]:
    """Detect any remaining parallel double-line or U-turn spur inside a segment."""
    if len(coords) < 4:
        return None
    cum = [0.0]
    for k in range(1, len(coords)):
        cum.append(cum[-1] + haversine_m(coords[k - 1][0], coords[k - 1][1], coords[k][0], coords[k][1]))
    for i in range(len(coords) - 2):
        for j in range(i + 2, len(coords)):
            arc = cum[j] - cum[i]
            if arc > 70.0:
                gap = haversine_m(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
                if gap < 32.0:
                    return (i, j, round(arc, 1), round(gap, 1))
    return None


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ordered: Dict[str, List[List[float]]] = {}

    osm_bundle = _fetch_overpass_osm_graph()
    if osm_bundle is not None:
        graph, osm_nodes = osm_bundle
        snapped = _snap_nodes_to_osm(graph, osm_nodes)

        far_nodes = [(cid, round(d, 1), pt) for cid, (_, d, pt) in snapped.items() if d > 15.0]
        if far_nodes:
            print(f"Nodes > 15m from snapped OSM node ({len(far_nodes)}):", flush=True)
            for cid, d, pt in far_nodes:
                print(f"  {cid}: offset={d}m -> target=({pt[0]:.6f}, {pt[1]:.6f})", flush=True)

        for seg in ROAD_SEGMENTS:
            nu, nv = NODES_BY_ID[seg.u], NODES_BY_ID[seg.v]
            u_osm = snapped[seg.u][0]
            v_osm = snapped[seg.v][0]
            path = nx.shortest_path(graph, source=u_osm, target=v_osm, weight="weight")
            coords = [[round(osm_nodes[nid][0], 6), round(osm_nodes[nid][1], 6)] for nid in path]

            start_pt = [round(nu.lat, 6), round(nu.lon, 6)]
            end_pt = [round(nv.lat, 6), round(nv.lon, 6)]
            if haversine_m(coords[0][0], coords[0][1], start_pt[0], start_pt[1]) <= 15.0:
                coords[0] = start_pt
            else:
                coords.insert(0, start_pt)
            if haversine_m(coords[-1][0], coords[-1][1], end_pt[0], end_pt[1]) <= 15.0:
                coords[-1] = end_pt
            else:
                coords.append(end_pt)

            coords = prune_parallel_backtracks(coords)
            # Re-anchor exact endpoints after pruning
            if haversine_m(coords[0][0], coords[0][1], start_pt[0], start_pt[1]) <= 15.0:
                coords[0] = start_pt
            if haversine_m(coords[-1][0], coords[-1][1], end_pt[0], end_pt[1]) <= 15.0:
                coords[-1] = end_pt

            # Guarantee >= 3 points even for very short edges
            if len(coords) == 2:
                mid = [round((coords[0][0] + coords[1][0]) / 2, 6), round((coords[0][1] + coords[1][1]) / 2, 6)]
                coords = [coords[0], mid, coords[1]]
            ordered[seg.id] = coords
    else:
        print("Overpass mirrors unavailable; falling back to OSRM/OSM.de...", flush=True)
        tmp: Dict[str, List[List[float]]] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(_snap_segment_fallback_osrm, seg): seg.id for seg in ROAD_SEGMENTS}
            for fut in as_completed(futures):
                seg_id, coords, _ = fut.result()
                tmp[seg_id] = coords
        ordered = {seg.id: tmp[seg.id] for seg in ROAD_SEGMENTS}

    OUTPUT_PATH.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")

    multi_pt = sum(1 for v in ordered.values() if len(v) >= 3)
    overlaps = []
    for seg in ROAD_SEGMENTS:
        ov = _detect_parallel_overlap(ordered[seg.id])
        if ov is not None:
            overlaps.append((seg.id, ov))

    print(
        f"Saved {len(ordered)} segments to {OUTPUT_PATH} | multi_point={multi_pt}/{len(ROAD_SEGMENTS)} | parallel_overlaps={len(overlaps)}/{len(ROAD_SEGMENTS)}",
        flush=True,
    )
    for sid, ov in overlaps:
        print(f"  OVERLAP {sid}: idx=({ov[0]}..{ov[1]}), arc={ov[2]}m, gap={ov[3]}m", flush=True)
    for sid in ("road_08", "road_17", "road_23", "road_31"):
        print(f"  {sid}: {len(ordered.get(sid, []))} pts", flush=True)

    return 0 if (multi_pt == len(ROAD_SEGMENTS) and not overlaps) else 1


if __name__ == "__main__":
    raise SystemExit(main())

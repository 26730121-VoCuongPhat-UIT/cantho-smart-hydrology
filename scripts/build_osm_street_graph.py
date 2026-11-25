"""Build compressed contracted OpenStreetMap street graph for Can Tho City.

Fetches all drivable roads (trunk -> residential) across Ninh Kieu, Binh Thuy,
and Cai Rang once from Overpass, adds zero-detour median crossovers at divided
intersections, contracts degree-2 chains into clean edge polylines, pre-indexes
each edge against the 214-cell H3 grid and 42 monitored road corridors, and writes
data/cantho_osm_graph.json.gz (~300 KB).
"""

from __future__ import annotations

import gzip
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import networkx as nx
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.cantho_geo import (
    CANTHO_HEX_GRID,
    CANTHO_NODES,
    ROAD_SEGMENTS,
    _point_to_segment_distance_m,
    haversine_m,
)

OUTPUT_PATH = PROJECT_ROOT / "data" / "cantho_osm_graph.json.gz"
USER_AGENT = "CanThoSmartCity/3.0 (CanTho Flood Map)"

OVERPASS_MIRRORS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

OVERPASS_QUERY = """[out:json][timeout:35];
way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|unclassified|residential)$"](9.982,105.702,10.088,105.815);
(._;>;);
out body;"""

SPEED_KMH: Dict[str, int] = {
    "motorway": 55,
    "motorway_link": 40,
    "trunk": 50,
    "trunk_link": 38,
    "primary": 42,
    "primary_link": 34,
    "secondary": 38,
    "secondary_link": 32,
    "tertiary": 34,
    "tertiary_link": 28,
    "unclassified": 30,
    "residential": 26,
}


def _point_to_polyline_m(lat: float, lon: float, coords: Tuple[Tuple[float, float], ...]) -> float:
    best = float("inf")
    for k in range(len(coords) - 1):
        d = _point_to_segment_distance_m(lat, lon, coords[k][0], coords[k][1], coords[k + 1][0], coords[k + 1][1])
        if d < best:
            best = d
    return best


def _fetch_overpass() -> Optional[Dict]:
    for url in OVERPASS_MIRRORS:
        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    data={"data": OVERPASS_QUERY},
                    timeout=32,
                    headers={"User-Agent": USER_AGENT},
                )
                if resp.status_code == 200 and len(resp.content) > 100_000:
                    print(f"Fetched Overpass Can Tho ways from {url} ({len(resp.content):,} B)", flush=True)
                    return resp.json()
                if resp.status_code == 429:
                    time.sleep(2.0 * (attempt + 1))
                    continue
            except Exception:
                continue
    return None


def main() -> int:
    t0 = time.perf_counter()
    payload = _fetch_overpass()
    if not payload or "elements" not in payload:
        print("ERROR: All Overpass mirrors failed or returned empty payload.", file=sys.stderr)
        return 1

    osm_nodes: Dict[int, Tuple[float, float]] = {}
    ways: List[Dict] = []
    for el in payload["elements"]:
        if el.get("type") == "node":
            osm_nodes[int(el["id"])] = (float(el["lat"]), float(el["lon"]))
        elif el.get("type") == "way" and el.get("tags", {}).get("highway"):
            ways.append(el)

    raw_g = nx.Graph()
    for w in ways:
        tags = w.get("tags", {})
        hw = tags.get("highway", "residential")
        spd = SPEED_KMH.get(hw, 26)
        name = str(tags.get("name") or tags.get("ref") or "").strip()
        nds = w.get("nodes", [])
        for a, b in zip(nds, nds[1:]):
            ia, ib = int(a), int(b)
            if ia == ib or ia not in osm_nodes or ib not in osm_nodes:
                continue
            la, loa = osm_nodes[ia]
            lb, lob = osm_nodes[ib]
            dist = haversine_m(la, loa, lb, lob)
            if dist <= 0.1:
                continue
            wt = dist / (spd / 3.6)
            if not raw_g.has_edge(ia, ib) or wt < raw_g[ia][ib]["wt"]:
                raw_g.add_edge(ia, ib, dist=dist, wt=wt, speed=spd, hw=hw, name=name)

    if raw_g.number_of_nodes() == 0:
        print("ERROR: Constructed empty OSM graph.", file=sys.stderr)
        return 1

    lcc = max(nx.connected_components(raw_g), key=len)
    raw_g = raw_g.subgraph(lcc).copy()

    # Zero-detour dual-carriageway crossover links at divided intersections
    deg3 = [nid for nid in raw_g.nodes if raw_g.degree(nid) >= 3]
    buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    cell_deg = 0.00025
    for nid in deg3:
        la, lo = osm_nodes[nid]
        buckets[(int(la / cell_deg), int(lo / cell_deg))].append(nid)

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
                if b <= a or raw_g.has_edge(a, b):
                    continue
                lb, lob = osm_nodes[b]
                euclid = haversine_m(la, loa, lb, lob)
                if euclid <= 22.0:
                    if dists_from_a is None:
                        dists_from_a = nx.single_source_dijkstra_path_length(raw_g, a, cutoff=80.0, weight="dist")
                    if b not in dists_from_a:
                        raw_g.add_edge(
                            a,
                            b,
                            dist=euclid,
                            wt=euclid / (38.0 / 3.6),
                            speed=38,
                            hw="primary",
                            name="",
                        )
                        crossovers += 1

    # Contract degree-2 chains into intersection-to-intersection edges
    keep_osm = sorted(nid for nid in raw_g.nodes if raw_g.degree(nid) != 2)
    osm_to_idx: Dict[int, int] = {nid: idx for idx, nid in enumerate(keep_osm)}
    nodes_out: List[List[float]] = [
        [round(osm_nodes[nid][0], 5), round(osm_nodes[nid][1], 5)] for nid in keep_osm
    ]

    # Spatial bbox pre-filter for monitored ROAD_SEGMENTS
    road_boxes = []
    for seg in ROAD_SEGMENTS:
        pts = seg.coords or ((osm_nodes[keep_osm[0]][0], osm_nodes[keep_osm[0]][1]),)
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        road_boxes.append(
            (seg.id, seg.is_flood_hotspot, pts, min(lats) - 0.0012, max(lats) + 0.0012, min(lons) - 0.0012, max(lons) + 0.0012)
        )

    visited_raw_edges = set()
    edges_out: List[Dict] = []
    matched_corridor_count = 0

    for u_osm in keep_osm:
        for v_osm in raw_g.neighbors(u_osm):
            ekey = (min(u_osm, v_osm), max(u_osm, v_osm))
            if ekey in visited_raw_edges:
                continue

            visited_raw_edges.add(ekey)
            first_attr = raw_g[u_osm][v_osm]
            chain = [u_osm, v_osm]
            dist = first_attr["dist"]
            spd = first_attr["speed"]
            hw = first_attr["hw"]
            name = first_attr["name"]

            prev = u_osm
            curr = v_osm
            while curr not in osm_to_idx:
                nbs = [nb for nb in raw_g.neighbors(curr) if nb != prev]
                if not nbs:
                    break
                nxt = nbs[0]
                ek2 = (min(curr, nxt), max(curr, nxt))
                if ek2 in visited_raw_edges:
                    break
                visited_raw_edges.add(ek2)
                attr2 = raw_g[curr][nxt]
                dist += attr2["dist"]
                spd = min(spd, attr2["speed"])
                if not name and attr2["name"]:
                    name = attr2["name"]
                chain.append(nxt)
                prev, curr = curr, nxt

            end_osm = chain[-1]
            if end_osm not in osm_to_idx:
                continue

            u_idx = osm_to_idx[u_osm]
            v_idx = osm_to_idx[end_osm]
            if u_idx == v_idx and dist < 25.0:
                continue

            poly = [[round(osm_nodes[x][0], 5), round(osm_nodes[x][1], 5)] for x in chain]
            mid_pt = poly[len(poly) // 2]
            mid_lat, mid_lon = mid_pt[0], mid_pt[1]

            # Nearest H3 hex index
            best_hx = 0
            best_hx_d = float("inf")
            for hx_i, cell in enumerate(CANTHO_HEX_GRID):
                hd = haversine_m(mid_lat, mid_lon, cell.center_lat, cell.center_lon)
                if hd < best_hx_d:
                    best_hx_d = hd
                    best_hx = hx_i

            # Exact corridor match + nearest fallback (prioritizing continuous elevated arterials over cross-street stubs)
            u_lat, u_lon = osm_nodes[u_osm]
            v_lat, v_lon = osm_nodes[end_osm]
            best_matched_r = ""
            best_score = float("inf")
            nearest_r = ROAD_SEGMENTS[0].id
            nearest_r_d = float("inf")
            is_major_hw = hw in ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link")

            for rid, is_hotspot, rpts, min_la, max_la, min_lo, max_lo in road_boxes:
                d_mid = _point_to_polyline_m(mid_lat, mid_lon, rpts)
                if d_mid < nearest_r_d:
                    nearest_r_d = d_mid
                    nearest_r = rid
                if min_la <= mid_lat <= max_la and min_lo <= mid_lon <= max_lo and d_mid <= 45.0:
                    d_u = _point_to_polyline_m(u_lat, u_lon, rpts)
                    d_v = _point_to_polyline_m(v_lat, v_lon, rpts)
                    both_close = d_u <= 45.0 and d_v <= 45.0
                    mid_straddle = d_mid <= 18.0 and min(d_u, d_v) <= 18.0 and max(d_u, d_v) <= 70.0
                    if both_close or mid_straddle:
                        # Avoid letting side-street flood hotspots bisect elevated main avenues at intersection islands
                        hotspot_pen = 40.0 if (is_hotspot and (is_major_hw or dist <= 55.0)) else 0.0
                        score = d_mid + 0.25 * (d_u + d_v) + hotspot_pen
                        if score < best_score:
                            best_score = score
                            best_matched_r = rid

            if best_matched_r:
                matched_corridor_count += 1

            edges_out.append(
                {
                    "u": u_idx,
                    "v": v_idx,
                    "d": round(dist, 1),
                    "s": int(spd),
                    "h": hw,
                    "n": name,
                    "p": poly,
                    "hx": best_hx,
                    "r": best_matched_r,
                    "nr": nearest_r,
                }
            )

    bundle = {
        "nodes": nodes_out,
        "edges": edges_out,
        "meta": {
            "source": "OpenStreetMap Overpass",
            "crossovers": crossovers,
            "hex_cells": len(CANTHO_HEX_GRID),
            "corridor_edges": matched_corridor_count,
        },
    }
    compressed = gzip.compress(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), compresslevel=9)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(compressed)

    # Verify all 26 CANTHO_NODES snap closely onto the contracted OSM graph
    max_node_snap = 0.0
    far_warnings = []
    for cn in CANTHO_NODES:
        best_d = min(haversine_m(cn.lat, cn.lon, pt[0], pt[1]) for pt in nodes_out)
        # Also measure distance to nearest edge polyline vertex for mid-block POIs
        if best_d > 35.0:
            for e in edges_out:
                if abs(e["p"][0][0] - cn.lat) < 0.005 and abs(e["p"][0][1] - cn.lon) < 0.005:
                    for pt in e["p"]:
                        best_d = min(best_d, haversine_m(cn.lat, cn.lon, pt[0], pt[1]))
        max_node_snap = max(max_node_snap, best_d)
        if best_d > 40.0:
            far_warnings.append((cn.id, round(best_d, 1)))

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(
        f"Saved {OUTPUT_PATH}: {len(nodes_out):,} intersections, {len(edges_out):,} edges, "
        f"{len(compressed)/1024:.1f} KB gzip | corridor_matched={matched_corridor_count} | "
        f"max_preset_snap={max_node_snap:.1f}m | built in {elapsed_ms:.0f} ms",
        flush=True,
    )
    if far_warnings:
        print(f"WARNING preset nodes > 40m from OSM edge: {far_warnings}", flush=True)
    return 0 if len(compressed) < 1_500_000 else 1


if __name__ == "__main__":
    raise SystemExit(main())

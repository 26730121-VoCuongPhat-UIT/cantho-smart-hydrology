"""High-speed Flood-Aware A* Routing Engine on Real Contracted OpenStreetMap Can Tho Graph.

Loads data/cantho_osm_graph.json.gz (~5.4k intersections, ~7.5k street edges, ~277 KB)
and maps H3 AI flood predictions + verified community clusters onto every road edge
in < 1 ms, enabling sub-15ms street-accurate routing from any coordinate in Can Tho.
"""

from __future__ import annotations

import gzip
import heapq
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.cantho_geo import _point_to_segment_distance_m, haversine_m, polyline_length_m

OSM_GRAPH_PATH = Path(__file__).resolve().parent.parent / "data" / "cantho_osm_graph.json.gz"
MAX_OSM_SPEED_MPS = 58.0 / 3.6
CELL_DEG = 0.002  # ~220m spatial grid bucket

# Arterial roads are generally raised/drained slightly better than residential backstreets
HW_FLOOD_FACTOR: Dict[str, float] = {
    "motorway": 0.72,
    "motorway_link": 0.78,
    "trunk": 0.78,
    "trunk_link": 0.82,
    "primary": 0.88,
    "primary_link": 0.90,
    "secondary": 0.95,
    "secondary_link": 0.96,
    "tertiary": 1.00,
    "tertiary_link": 1.00,
    "unclassified": 1.02,
    "residential": 1.05,
}


def _project_point_on_polyline(
    lat: float, lon: float, poly: List[List[float]]
) -> Tuple[float, List[float], int]:
    """Project (lat, lon) onto polyline, returning (distance_m, [proj_lat, proj_lon], seg_idx)."""
    cos_lat = math.cos(math.radians(lat))
    m_lat = 111_132.0
    m_lon = 111_320.0 * cos_lat
    px, py = lon * m_lon, lat * m_lat

    best_d = float("inf")
    best_pt = [poly[0][0], poly[0][1]]
    best_seg = 0

    for i in range(len(poly) - 1):
        a_lat, a_lon = poly[i][0], poly[i][1]
        b_lat, b_lon = poly[i + 1][0], poly[i + 1][1]
        ax, ay = a_lon * m_lon, a_lat * m_lat
        bx, by = b_lon * m_lon, b_lat * m_lat
        dx, dy = bx - ax, by - ay
        seg_sq = dx * dx + dy * dy
        if seg_sq == 0.0:
            t = 0.0
            proj_x, proj_y = ax, ay
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_sq))
            proj_x = ax + t * dx
            proj_y = ay + t * dy
        d = math.hypot(px - proj_x, py - proj_y)
        if d < best_d:
            best_d = d
            best_pt = [round(a_lat + t * (b_lat - a_lat), 6), round(a_lon + t * (b_lon - a_lon), 6)]
            best_seg = i

    return best_d, best_pt, best_seg


@dataclass
class SnapAnchor:
    """Mid-edge or intersection anchor linking an arbitrary GPS point onto the OSM graph."""

    edge_idx: int
    proj_pt: List[float]
    snap_offset_m: float
    seg_idx: int
    u_node: int
    v_node: int
    to_u_coords: List[List[float]]
    to_u_len_m: float
    to_v_coords: List[List[float]]
    to_v_len_m: float


class CanThoOSMGraph:
    """In-memory contracted OSM road graph with spatial indexing and Flood-Aware A*."""

    def __init__(self, graph_path: Path = OSM_GRAPH_PATH) -> None:
        raw = json.loads(gzip.decompress(graph_path.read_bytes()).decode("utf-8"))
        self.nodes: List[Tuple[float, float]] = [(float(p[0]), float(p[1])) for p in raw["nodes"]]
        self.edges: List[Dict] = raw["edges"]
        self.meta: Dict = raw.get("meta", {})

        self.adj: List[List[Tuple[int, int, bool]]] = [[] for _ in range(len(self.nodes))]
        self.edge_buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)

        for idx, edge in enumerate(self.edges):
            u = int(edge["u"])
            v = int(edge["v"])
            self.adj[u].append((v, idx, True))
            self.adj[v].append((u, idx, False))

            poly = edge["p"]
            seen_cells = set()
            for sample in (poly[0], poly[len(poly) // 2], poly[-1]):
                cell = (int(sample[0] / CELL_DEG), int(sample[1] / CELL_DEG))
                if cell not in seen_cells:
                    seen_cells.add(cell)
                    self.edge_buckets[cell].append(idx)

    def _candidate_edges_near(self, lat: float, lon: float, radius_cells: int = 2) -> List[int]:
        cy, cx = int(lat / CELL_DEG), int(lon / CELL_DEG)
        out: List[int] = []
        seen = set()
        for dy in range(-radius_cells, radius_cells + 1):
            for dx in range(-radius_cells, radius_cells + 1):
                for eidx in self.edge_buckets.get((cy + dy, cx + dx), ()):
                    if eidx not in seen:
                        seen.add(eidx)
                        out.append(eidx)
        return out

    def snap_coord(self, lat: float, lon: float) -> SnapAnchor:
        """Snap any coordinate in Can Tho directly onto the closest street edge centerline."""
        candidates = self._candidate_edges_near(lat, lon, radius_cells=2)
        if not candidates:
            candidates = self._candidate_edges_near(lat, lon, radius_cells=5)
        if not candidates:
            candidates = list(range(len(self.edges)))

        best_d = float("inf")
        best_idx = candidates[0]
        best_pt = self.edges[best_idx]["p"][0]
        best_seg = 0

        for eidx in candidates:
            poly = self.edges[eidx]["p"]
            d, pt, s_idx = _project_point_on_polyline(lat, lon, poly)
            if d < best_d:
                best_d = d
                best_idx = eidx
                best_pt = pt
                best_seg = s_idx

        edge = self.edges[best_idx]
        poly = edge["p"]
        # Sub-polyline proj -> u (reversed prefix up to seg_idx)
        to_u = [best_pt] + [list(p) for p in reversed(poly[: best_seg + 1])]
        to_v = [best_pt] + [list(p) for p in poly[best_seg + 1 :]]
        return SnapAnchor(
            edge_idx=best_idx,
            proj_pt=best_pt,
            snap_offset_m=round(best_d, 1),
            seg_idx=best_seg,
            u_node=int(edge["u"]),
            v_node=int(edge["v"]),
            to_u_coords=to_u,
            to_u_len_m=polyline_length_m(to_u),
            to_v_coords=to_v,
            to_v_len_m=polyline_length_m(to_v),
        )

    def compute_edge_depths(
        self,
        road_depths: Dict[str, float],
        hex_depths: Optional[List[float]] = None,
        community_incidents: Optional[List[Dict]] = None,
    ) -> Tuple[List[float], Dict[int, Dict]]:
        """Map H3 AI cells, corridor depths, and verified community clusters onto all OSM edges (< 1 ms)."""
        n_edges = len(self.edges)
        depths = [0.0] * n_edges
        overrides: Dict[int, Dict] = {}

        n_hex = len(hex_depths) if hex_depths else 0
        for idx, e in enumerate(self.edges):
            matched_r = e.get("r", "")
            if matched_r and matched_r in road_depths:
                depths[idx] = float(road_depths[matched_r])
            elif n_hex > 0:
                hx = int(e.get("hx", 0))
                base_d = float(hex_depths[hx]) if 0 <= hx < n_hex else 0.0
                factor = HW_FLOOD_FACTOR.get(e.get("h", "tertiary"), 1.0)
                depths[idx] = round(base_d * factor, 1)
            else:
                nr = e.get("nr", "")
                base_d = float(road_depths.get(nr, 0.0))
                factor = HW_FLOOD_FACTOR.get(e.get("h", "tertiary"), 1.0)
                depths[idx] = round(base_d * factor, 1)

        if community_incidents:
            for inc in community_incidents:
                if not inc.get("fusion_eligible", inc.get("report_count", 0) >= 2):
                    continue
                inc_depth = float(inc.get("avg_depth_cm", 0.0))
                if inc_depth < 8.0:
                    continue
                ilat = float(inc["lat"])
                ilon = float(inc["lon"])
                for eidx in self._candidate_edges_near(ilat, ilon, radius_cells=2):
                    poly = self.edges[eidx]["p"]
                    min_d = float("inf")
                    for k in range(len(poly) - 1):
                        d = _point_to_segment_distance_m(
                            ilat, ilon, poly[k][0], poly[k][1], poly[k + 1][0], poly[k + 1][1]
                        )
                        if d < min_d:
                            min_d = d
                    if min_d <= 220.0 and inc_depth > depths[eidx]:
                        depths[eidx] = round(inc_depth, 1)
                        overrides[eidx] = {
                            "incident_id": inc.get("incident_id", "INC"),
                            "location_label": inc.get("location_label", "Cộng đồng"),
                            "report_count": int(inc.get("report_count", 2)),
                            "community_depth_cm": round(inc_depth, 1),
                            "distance_m": round(min_d, 1),
                        }

        return depths, overrides

    def _same_edge_route(
        self,
        anchor_a: SnapAnchor,
        anchor_b: SnapAnchor,
        edge_depths: List[float],
        overrides: Dict[int, Dict],
        safe_threshold_cm: float,
    ) -> Optional[Dict]:
        """Compute direct slice along a single OSM edge when A and B snap onto the same road."""
        if anchor_a.edge_idx != anchor_b.edge_idx:
            return None
        eidx = anchor_a.edge_idx
        edge = self.edges[eidx]
        poly = edge["p"]

        # Reconstruct points between seg_idx_a and seg_idx_b
        pa_idx, pb_idx = anchor_a.seg_idx, anchor_b.seg_idx
        if pa_idx <= pb_idx:
            if pa_idx == pb_idx:
                da = haversine_m(poly[pa_idx][0], poly[pa_idx][1], anchor_a.proj_pt[0], anchor_a.proj_pt[1])
                db = haversine_m(poly[pa_idx][0], poly[pa_idx][1], anchor_b.proj_pt[0], anchor_b.proj_pt[1])
                coords = [anchor_a.proj_pt, anchor_b.proj_pt] if da <= db else [anchor_a.proj_pt, anchor_b.proj_pt]
            else:
                coords = [anchor_a.proj_pt] + [list(p) for p in poly[pa_idx + 1 : pb_idx + 1]] + [anchor_b.proj_pt]
        else:
            coords = [anchor_a.proj_pt] + [list(p) for p in reversed(poly[pb_idx + 1 : pa_idx + 1])] + [anchor_b.proj_pt]

        length_m = polyline_length_m(coords)
        depth = edge_depths[eidx]
        speed_mps = float(edge["s"]) / 3.6
        if depth >= safe_threshold_cm:
            eff_speed = speed_mps * 0.28
        elif depth >= 10.0:
            eff_speed = speed_mps * (1.0 - 0.45 * (depth / safe_threshold_cm))
        else:
            eff_speed = speed_mps
        time_sec = length_m / max(1.5, eff_speed)
        is_dangerous = depth >= safe_threshold_cm
        seg_info = {
            "id": edge.get("r") or f"osm_{eidx}",
            "name": edge.get("n") or "Đường nội bộ Cần Thơ",
            "length_m": round(length_m, 1),
            "depth_cm": round(depth, 1),
            "is_dangerous": is_dangerous,
            "source": "community_1022" if eidx in overrides else "ai_model",
            "coords": coords,
            "mid_coord": coords[len(coords) // 2],
        }
        return {
            "node_path": [f"osm_{anchor_a.u_node}", f"osm_{anchor_b.v_node}"],
            "coords": coords,
            "segments": [seg_info],
            "total_length_m": length_m,
            "estimated_time_sec": time_sec,
            "max_depth_cm": round(depth, 1),
            "dangerous_segments": [seg_info] if is_dangerous else [],
            "is_safe": not is_dangerous,
            "fallback_used": False,
        }

    def route_between_anchors(
        self,
        anchor_a: SnapAnchor,
        anchor_b: SnapAnchor,
        edge_depths: List[float],
        overrides: Dict[int, Dict],
        safe_threshold_cm: float,
        flood_aware: bool,
        alpha: float = 2.6,
    ) -> Dict:
        """Run fast pure-Python heapq A* between mid-edge anchors (dry baseline or Flood-Aware)."""
        end_edge_idx = anchor_b.edge_idx
        end_depth = edge_depths[end_edge_idx]

        # Same-edge fast path (only skip if flood_aware must avoid a flooded edge via another street)
        same_res = self._same_edge_route(anchor_a, anchor_b, edge_depths, overrides, safe_threshold_cm)
        if same_res is not None and (not flood_aware or not same_res["dangerous_segments"]):
            return same_res

        def _run_astar(strict_safe: bool, risk_mode: bool) -> Optional[Tuple[float, int, Dict[int, Tuple[int, int, bool]]]]:
            b_lat = anchor_b.proj_pt[0]
            b_lon = anchor_b.proj_pt[1]

            def h_sec(node_idx: int) -> float:
                nlat, nlon = self.nodes[node_idx]
                return haversine_m(nlat, nlon, b_lat, b_lon) / MAX_OSM_SPEED_MPS

            def edge_cost(eidx: int, raw_len_m: float) -> Optional[float]:
                edge = self.edges[eidx]
                base_sec = raw_len_m / (float(edge["s"]) / 3.6)
                if not flood_aware:
                    return base_sec
                d = edge_depths[eidx]
                if strict_safe and d >= safe_threshold_cm:
                    return None
                ratio = d / safe_threshold_cm
                if d < safe_threshold_cm:
                    return base_sec * (1.0 + alpha * (ratio ** 2))
                return base_sec * (1.0 + 85.0 * (ratio ** 3))

            # Virtual goal sentinel = -1
            GOAL = -1
            g_score: Dict[int, float] = {}
            parent: Dict[int, Tuple[int, int, bool]] = {}
            heap: List[Tuple[float, int]] = []

            start_d = edge_depths[anchor_a.edge_idx]
            start_options: List[Tuple[int, float, int, bool]] = []
            if anchor_a.to_u_len_m <= 25.0:
                start_options.append((anchor_a.u_node, anchor_a.to_u_len_m / 6.0, -1, False))
            if anchor_a.to_v_len_m <= 25.0:
                start_options.append((anchor_a.v_node, anchor_a.to_v_len_m / 6.0, -1, True))
            if not (strict_safe and start_d >= safe_threshold_cm):
                for target_node, step_len, is_forward in (
                    (anchor_a.u_node, anchor_a.to_u_len_m, False),
                    (anchor_a.v_node, anchor_a.to_v_len_m, True),
                ):
                    c0 = edge_cost(anchor_a.edge_idx, max(1.0, step_len))
                    if c0 is not None:
                        start_options.append((target_node, c0, anchor_a.edge_idx, is_forward))

            for target_node, c0, e_in, is_forward in start_options:
                if target_node not in g_score or c0 < g_score[target_node]:
                    g_score[target_node] = c0
                    parent[target_node] = (-2, e_in, is_forward)  # -2 = START sentinel
                    heapq.heappush(heap, (c0 + h_sec(target_node), target_node))

            end_direct_nodes = set()
            if anchor_b.to_u_len_m <= 25.0:
                end_direct_nodes.add(anchor_b.u_node)
            if anchor_b.to_v_len_m <= 25.0:
                end_direct_nodes.add(anchor_b.v_node)

            end_targets = {
                anchor_b.u_node: (max(1.0, anchor_b.to_u_len_m), True),
                anchor_b.v_node: (max(1.0, anchor_b.to_v_len_m), False),
            }
            closed = set()

            while heap:
                _, cur = heapq.heappop(heap)
                if cur == GOAL:
                    return g_score[GOAL], GOAL, parent
                if cur in closed:
                    continue
                closed.add(cur)

                cur_g = g_score[cur]

                if cur in end_direct_nodes:
                    tail_sec = (
                        anchor_b.to_u_len_m if cur == anchor_b.u_node else anchor_b.to_v_len_m
                    ) / 6.0
                    cand_g = cur_g + tail_sec
                    if GOAL not in g_score or cand_g < g_score[GOAL]:
                        g_score[GOAL] = cand_g
                        parent[GOAL] = (cur, -1, cur == anchor_b.u_node)
                        heapq.heappush(heap, (cand_g, GOAL))

                # Direct exit onto destination block
                if cur in end_targets:
                    tail_len, tail_fwd = end_targets[cur]
                    tail_cost = edge_cost(end_edge_idx, tail_len)
                    if tail_cost is not None:
                        cand_g = cur_g + tail_cost
                        if GOAL not in g_score or cand_g < g_score[GOAL]:
                            g_score[GOAL] = cand_g
                            parent[GOAL] = (cur, end_edge_idx, tail_fwd)
                            heapq.heappush(heap, (cand_g, GOAL))

                for nb, eidx, fwd in self.adj[cur]:
                    if nb in closed:
                        continue
                    c_step = edge_cost(eidx, float(self.edges[eidx]["d"]))
                    if c_step is None:
                        continue
                    cand_g = cur_g + c_step
                    if nb not in g_score or cand_g < g_score[nb]:
                        g_score[nb] = cand_g
                        parent[nb] = (cur, eidx, fwd)
                        heapq.heappush(heap, (cand_g + h_sec(nb), nb))

            return None

        fallback_used = False
        solved = _run_astar(strict_safe=flood_aware, risk_mode=False)
        if solved is None:
            fallback_used = True
            solved = _run_astar(strict_safe=False, risk_mode=True)

        if solved is None:
            # Ultimate isolated fallback
            if same_res is not None:
                same_res["fallback_used"] = True
                return same_res
            raise RuntimeError("No path found on OSM Can Tho graph.")

        _, goal_node, parent = solved
        # Reconstruct edge sequence [(u_or_sentinel, eidx, fwd), ...]
        steps: List[Tuple[int, int, bool]] = []
        cur = goal_node
        while cur != -2:
            prev_node, eidx, fwd = parent[cur]
            steps.append((prev_node, eidx, fwd))
            cur = prev_node
        steps.reverse()

        coords: List[List[float]] = []
        raw_groups: List[Dict] = []
        total_length_m = 0.0
        estimated_time_sec = 0.0
        max_depth_cm = 0.0
        node_path: List[str] = []

        for step_idx, (prev_node, eidx, fwd) in enumerate(steps):
            if eidx == -1:
                # Zero-detour intersection arrival/departure stub
                if step_idx == 0:
                    start_n = anchor_a.v_node if fwd else anchor_a.u_node
                    npt = self.nodes[start_n]
                    seg_pts = [anchor_a.proj_pt, [round(npt[0], 5), round(npt[1], 5)]]
                    if haversine_m(seg_pts[0][0], seg_pts[0][1], seg_pts[1][0], seg_pts[1][1]) >= 1.5:
                        coords.extend(seg_pts)
                    else:
                        coords.append([round(npt[0], 5), round(npt[1], 5)])
                    node_path.append(f"osm_{start_n}")
                elif step_idx == len(steps) - 1:
                    if haversine_m(coords[-1][0], coords[-1][1], anchor_b.proj_pt[0], anchor_b.proj_pt[1]) >= 1.5:
                        coords.append(anchor_b.proj_pt)
                continue

            edge = self.edges[eidx]
            if step_idx == 0:
                seg_pts = [list(p) for p in (anchor_a.to_v_coords if fwd else anchor_a.to_u_coords)]
                start_n = anchor_a.v_node if fwd else anchor_a.u_node
                node_path.append(f"osm_{start_n}")
            elif step_idx == len(steps) - 1:
                # Going FROM prev_node TO proj_pt along edge_b (reverse of to_u / to_v)
                base_tail = anchor_b.to_u_coords if fwd else anchor_b.to_v_coords
                seg_pts = [list(p) for p in reversed(base_tail)]
            else:
                raw_p = edge["p"]
                seg_pts = [list(p) for p in (raw_p if fwd else reversed(raw_p))]
                node_path.append(f"osm_{edge['v'] if fwd else edge['u']}")

            if len(seg_pts) < 2:
                continue

            if not coords:
                coords.extend(seg_pts)
            elif haversine_m(coords[-1][0], coords[-1][1], seg_pts[0][0], seg_pts[0][1]) < 2.0:
                coords.extend(seg_pts[1:])
            else:
                coords.extend(seg_pts)

            seg_len = polyline_length_m(seg_pts)
            depth = edge_depths[eidx]
            total_length_m += seg_len
            max_depth_cm = max(max_depth_cm, depth)

            speed_mps = float(edge["s"]) / 3.6
            if depth >= safe_threshold_cm:
                eff_spd = speed_mps * 0.28
            elif depth >= 10.0:
                eff_spd = speed_mps * (1.0 - 0.45 * (depth / safe_threshold_cm))
            else:
                eff_spd = speed_mps
            estimated_time_sec += seg_len / max(1.5, eff_spd)

            street_name = edge.get("n") or "Đường kết nối nội bộ"
            seg_source = "community_1022" if eidx in overrides else "ai_model"
            is_danger = depth >= safe_threshold_cm
            corridor_id = edge.get("r") or f"osm_{eidx}"

            if (
                raw_groups
                and raw_groups[-1]["name"] == street_name
                and raw_groups[-1]["is_dangerous"] == is_danger
                and raw_groups[-1]["source"] == seg_source
            ):
                grp = raw_groups[-1]
                grp["length_m"] = round(grp["length_m"] + seg_len, 1)
                grp["depth_cm"] = round(max(grp["depth_cm"], depth), 1)
                grp["coords"].extend(seg_pts[1:] if grp["coords"][-1] == seg_pts[0] else seg_pts)
                grp["mid_coord"] = grp["coords"][len(grp["coords"]) // 2]
            else:
                raw_groups.append(
                    {
                        "id": corridor_id,
                        "name": street_name,
                        "length_m": round(seg_len, 1),
                        "depth_cm": round(depth, 1),
                        "is_dangerous": is_danger,
                        "source": seg_source,
                        "coords": list(seg_pts),
                        "mid_coord": seg_pts[len(seg_pts) // 2],
                    }
                )

        if same_res is not None and not same_res["dangerous_segments"]:
            # Never pick a longer wrap-around if same-edge direct slice is safe & shorter
            if total_length_m > same_res["total_length_m"]:
                return same_res

        dangerous_segments = [g for g in raw_groups if g["is_dangerous"]]
        return {
            "node_path": node_path or [f"osm_{anchor_a.u_node}", f"osm_{anchor_b.v_node}"],
            "coords": coords,
            "segments": raw_groups,
            "total_length_m": total_length_m,
            "estimated_time_sec": estimated_time_sec,
            "max_depth_cm": round(max_depth_cm, 1),
            "dangerous_segments": dangerous_segments,
            "is_safe": len(dangerous_segments) == 0,
            "fallback_used": fallback_used,
        }


_OSM_GRAPH_SINGLETON: Optional[CanThoOSMGraph] = None


def get_osm_graph() -> Optional[CanThoOSMGraph]:
    """Return cached singleton CanThoOSMGraph when data/cantho_osm_graph.json.gz exists."""
    global _OSM_GRAPH_SINGLETON
    if _OSM_GRAPH_SINGLETON is None and OSM_GRAPH_PATH.exists():
        _OSM_GRAPH_SINGLETON = CanThoOSMGraph(OSM_GRAPH_PATH)
    return _OSM_GRAPH_SINGLETON

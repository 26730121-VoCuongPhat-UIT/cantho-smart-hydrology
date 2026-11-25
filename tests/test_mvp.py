"""Automated test suite (Normal, Edge, Corner cases) for CanThoFloodGuard MVP."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.cantho_geo import CANTHO_HEX_GRID, CANTHO_NODES, ROAD_SEGMENTS
from backend.data_pipeline import build_medallion_training_dataset, simulate_hau_river_tide
from backend.incident_engine import FloodReport, IncidentStore, run_st_dbscan
from backend.ml_engine import CanThoFloodMLEngine
from backend.routing_engine import find_flood_aware_route


class TestCanThoFloodGuardMVP(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gold_df = build_medallion_training_dataset(force_rebuild=False)
        cls.ml_engine = CanThoFloodMLEngine(force_retrain=False)

    def test_geo_grid_and_graph_integrity(self) -> None:
        """Verify Can Tho intersections, roads, and hexagonal tessellation."""
        self.assertEqual(len(CANTHO_NODES), 26)
        self.assertGreaterEqual(len(ROAD_SEGMENTS), 40)
        self.assertGreaterEqual(len(CANTHO_HEX_GRID), 100)
        for cell in CANTHO_HEX_GRID:
            self.assertEqual(len(cell.vertices), 6)
            self.assertGreater(cell.elevation_m, 0.5)
        for seg in ROAD_SEGMENTS:
            self.assertGreaterEqual(len(seg.coords), 2)

    def test_medallion_dataset_and_ml_benchmarks(self) -> None:
        """Verify Gold dataset generation and 3-model ML evaluation across horizons."""
        self.assertGreater(len(self.gold_df), 2500)
        self.assertGreaterEqual(len(self.ml_engine.benchmark_results), 9)  # 3 models x 3 horizons
        hgbr_1h = next(
            b
            for b in self.ml_engine.benchmark_results
            if b["horizon"] == "+1h" and "HistGradientBoosting" in b["model"]
        )
        self.assertGreater(hgbr_1h["r2"], 0.85)
        self.assertLess(hgbr_1h["mae_cm"], 4.0)
        self.assertGreater(len(self.ml_engine.feature_attribution), 0)

    def test_normal_spring_tide_routing_avoids_mau_than(self) -> None:
        """Normal Test: Peak spring tide + afternoon rain routes scooter safely around flooded Mau Than."""
        pred = self.ml_engine.predict_city_flood_map(
            tide_level_m=2.22,
            rain_mm_h=45.0,
            rain_3h_cum=82.0,
            tide_delta_m=0.18,
        )
        res = find_flood_aware_route(
            start_id="dhct_khu2",
            end_id="ben_ninh_kieu",
            vehicle_type="scooter",
            road_depths=pred["road_depths"],
        )
        std = res["standard_route"]
        safe = res["flood_aware_route"]

        # Standard shortest path goes through flooded Mau Than / Ly Tu Trong (>= 18cm)
        self.assertFalse(std["is_safe"], "Shortest path should hit deep flood during spring tide + rain")
        self.assertGreaterEqual(std["max_depth_cm"], 18.0)

        # Flood-Aware A* finds an elevated safe bypass (< 18cm)
        self.assertTrue(safe["is_safe"], f"Expected safe detour for scooter, got max {safe['max_depth_cm']}cm")
        self.assertLess(safe["max_depth_cm"], 18.0)
        self.assertGreater(len(res["avoided_roads"]), 0)
        self.assertGreater(len(safe["coords"]), len(safe["node_path"]))
        self.assertIn("mid_coord", std["dangerous_segments"][0])

    def test_edge_dry_weather_and_suv_tolerance(self) -> None:
        """Edge Test: Dry weather converges routes; SUV handles deeper water than scooter."""
        dry_pred = self.ml_engine.predict_city_flood_map(
            tide_level_m=1.35, rain_mm_h=0.0, rain_3h_cum=0.0, tide_delta_m=-0.05
        )
        dry_res = find_flood_aware_route("dhct_khu2", "ben_ninh_kieu", "scooter", dry_pred["road_depths"])
        self.assertTrue(dry_res["standard_route"]["is_safe"])
        self.assertTrue(dry_res["flood_aware_route"]["is_safe"])
        self.assertAlmostEqual(
            dry_res["standard_route"]["total_length_km"],
            dry_res["flood_aware_route"]["total_length_km"],
            places=2,
        )

        # In moderate tide + rain, SUV (45cm limit) tolerates direct route whereas scooter (18cm) detours
        flood_pred = self.ml_engine.predict_city_flood_map(
            tide_level_m=2.12, rain_mm_h=30.0, rain_3h_cum=50.0, tide_delta_m=0.12
        )
        suv_res = find_flood_aware_route("dhct_khu2", "ben_ninh_kieu", "suv", flood_pred["road_depths"])
        self.assertTrue(suv_res["standard_route"]["is_safe"])

    def test_corner_same_node_and_extreme_flood_fallback(self) -> None:
        """Corner Test: start == end and extreme historical flood graceful least-risk fallback."""
        extreme_pred = self.ml_engine.predict_city_flood_map(
            tide_level_m=2.45, rain_mm_h=90.0, rain_3h_cum=150.0, tide_delta_m=0.25
        )
        same_node = find_flood_aware_route("dhct_khu2", "dhct_khu2", "scooter", extreme_pred["road_depths"])
        self.assertEqual(same_node["flood_aware_route"]["total_length_km"], 0.0)
        self.assertEqual(same_node["flood_aware_route"]["node_path"], ["dhct_khu2"])

        extreme_route = find_flood_aware_route("dhct_khu2", "ho_bun_xang", "scooter", extreme_pred["road_depths"])
        self.assertGreaterEqual(len(extreme_route["flood_aware_route"]["node_path"]), 2)

    def test_st_dbscan_incident_clustering(self) -> None:
        """Verify ST-DBSCAN clusters nearby concurrent reports and separates old/distant noise."""
        store = IncidentStore(seed_demo=True)
        clustered = store.get_clustered_incidents(rain_mm_h=38.0, tide_level_m=2.12)
        # Seeded demo fixture with 3 spatial-temporal clusters + 1 old noise report (within 180m wet TTL)
        self.assertEqual(len(clustered["master_incidents"]), 3)
        self.assertEqual(len(clustered["noise_reports"]), 1)

        # Adding a report right next to an existing cluster merges it cleanly
        updated = store.add_report(
            lat=10.0365,
            lon=105.7760,
            reported_depth_cm=40.0,
            description="Ngập nặng thêm tại Mậu Thân",
            location_hint="Mậu Thân - Trần Hưng Đạo",
            rain_mm_h=38.0,
            tide_level_m=2.12,
        )
        self.assertEqual(updated["total_reports"], 9)
        top_cluster = updated["master_incidents"][0]
        self.assertEqual(top_cluster["report_count"], 4)

        # Two reports close in space but 3 hours apart must NOT merge
        far_time_reports = [
            FloodReport("A", 10.030, 105.770, 20.0, "2026-11-14T08:00:00", "Sáng", "U1", "3/2"),
            FloodReport("B", 10.0301, 105.7701, 22.0, "2026-11-14T11:30:00", "Trưa", "U2", "3/2"),
        ]
        res_split = run_st_dbscan(far_time_reports)
        self.assertEqual(len(res_split["master_incidents"]), 0)
        self.assertEqual(len(res_split["noise_reports"]), 2)

    def test_hau_river_semi_diurnal_tide_peaks(self) -> None:
        """Verify Hau River tide simulation produces higher levels near 06h & 18h than 12h."""
        low_noon, _ = simulate_hau_river_tide(12.0, is_spring_tide=True)
        high_evening, _ = simulate_hau_river_tide(17.5, is_spring_tide=True)
        self.assertGreater(high_evening, low_noon + 0.8)

    def test_starlette_asgi_endpoints(self) -> None:
        """Verify Starlette app routes return valid HTTP 200/201 responses via ASGI."""
        import json
        from app import app as starlette_app

        async def call_asgi(method: str, path: str, payload: dict | None = None):
            body_bytes = json.dumps(payload).encode("utf-8") if payload else b""
            headers = [(b"content-type", b"application/json")] if payload else []
            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "headers": headers,
            }
            sent = False
            status_code = 500
            chunks = []

            async def receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body_bytes, "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                elif message["type"] == "http.response.body":
                    chunks.append(message.get("body", b""))

            await starlette_app(scope, receive, send)
            return status_code, b"".join(chunks).decode("utf-8")

        st_health, body_health = asyncio.run(call_asgi("GET", "/api/health"))
        self.assertEqual(st_health, 200)
        self.assertIn("CanThoFloodGuard", body_health)

        st_init, body_init = asyncio.run(call_asgi("GET", "/api/init"))
        self.assertEqual(st_init, 200)
        init_json = json.loads(body_init)
        self.assertIn("ml_evaluation", init_json)

        st_route, body_route = asyncio.run(
            call_asgi(
                "POST",
                "/api/route",
                {
                    "start_id": "dhct_khu2",
                    "end_id": "ben_ninh_kieu",
                    "vehicle_type": "scooter",
                    "tide_level_m": 2.22,
                    "rain_mm_h": 45.0,
                    "rain_3h_cum": 82.0,
                },
            )
        )
        self.assertEqual(st_route, 200)
        route_json = json.loads(body_route)
        self.assertTrue(route_json["flood_aware_route"]["is_safe"])

    def test_realtime_weather_hub_and_six_districts(self) -> None:
        """Verify 24/7 Real-Time Weather Hub, 6 Can Tho districts, UV/AQI, Lunar/Tide, and Smart Alerts."""
        from backend.cantho_geo import CANTHO_DISTRICTS
        from backend.data_pipeline import (
            decode_wmo_weather_code,
            evaluate_air_quality,
            evaluate_uv_index,
            fetch_cantho_realtime_hub,
            wind_deg_to_vietnamese,
        )

        # 6 Districts: Ninh Kieu, Binh Thuy, Cai Rang, O Mon, Thot Not, Phong Dien
        self.assertEqual(len(CANTHO_DISTRICTS), 6)
        district_ids = {d.id for d in CANTHO_DISTRICTS}
        self.assertEqual(
            district_ids,
            {"ninh_kieu", "binh_thuy", "cai_rang", "o_mon", "thot_not", "phong_dien"},
        )

        # WMO code decoding & wind compass
        self.assertEqual(decode_wmo_weather_code(0, 1)["category"], "clear")
        self.assertEqual(decode_wmo_weather_code(63, 1)["category"], "rain")
        self.assertEqual(decode_wmo_weather_code(95, 0)["category"], "storm")
        self.assertEqual(wind_deg_to_vietnamese(0.0)[0], "B")
        self.assertEqual(wind_deg_to_vietnamese(135.0)[0], "ĐN")

        # UV & AQI boundary evaluations
        self.assertEqual(evaluate_uv_index(2.0)["level"], "Thấp")
        self.assertEqual(evaluate_uv_index(9.5)["level"], "Rất cao")
        self.assertEqual(evaluate_uv_index(12.0)["level"], "Cực kỳ nguy hiểm")
        self.assertIn("Tốt", evaluate_air_quality(35, 8.0, 12.0)["level"])
        self.assertIn("Trung bình", evaluate_air_quality(74, 19.0, 22.0)["level"])
        self.assertIn("Xấu", evaluate_air_quality(175, 65.0, 90.0)["level"])

        # Full hub payload for Phong Dien & fallback on unknown district_id
        hub_pd = fetch_cantho_realtime_hub(district_id="phong_dien", force_refresh=False)
        self.assertEqual(hub_pd["selected_district_id"], "phong_dien")
        self.assertEqual(len(hub_pd["districts"]), 6)
        self.assertEqual(len(hub_pd["hourly_24h"]), 24)
        self.assertEqual(len(hub_pd["daily_7d"]), 7)
        self.assertEqual(len(hub_pd["astronomy_and_tide"]["extremes"]), 4)
        self.assertGreaterEqual(len(hub_pd["smart_alerts"]), 2)
        self.assertIn("should_activate", hub_pd["auto_flood_mode"])
        # Verify "Hiện tại" (hourly_24h[0]) matches current selected district snapshot
        self.assertEqual(hub_pd["hourly_24h"][0]["hour_label"], "Hiện tại")
        self.assertEqual(hub_pd["hourly_24h"][0]["temp_c"], hub_pd["current"]["temp_c"])
        self.assertEqual(hub_pd["hourly_24h"][0]["feels_like_c"], hub_pd["current"]["feels_like_c"])
        self.assertEqual(hub_pd["hourly_24h"][0]["humidity_pct"], hub_pd["current"]["humidity_pct"])

        hub_fallback = fetch_cantho_realtime_hub(district_id="unknown_district", force_refresh=False)
        self.assertEqual(hub_fallback["selected_district_id"], "ninh_kieu")

    def test_multi_model_and_multi_horizon_h3_prediction(self) -> None:
        """Verify H3-style hex flood prediction across real trained models (hgbr, mlp, rf) and horizons (+1h, +3h, +6h)."""
        from backend.cantho_geo import CANTHO_HEX_GRID

        self.assertEqual(set(self.ml_engine.models.keys()), {"hgbr", "mlp", "rf"})
        preds_by_model = {}
        for model_key in ("hgbr", "mlp", "rf"):
            for horizon in ("+1h", "+3h", "+6h"):
                pred = self.ml_engine.predict_city_flood_map(
                    tide_level_m=2.18,
                    rain_mm_h=40.0,
                    rain_3h_cum=75.0,
                    tide_delta_m=0.15,
                    horizon=horizon,
                    model_key=model_key,
                )
                self.assertEqual(pred["summary"]["total_hex_cells"], len(CANTHO_HEX_GRID))
                self.assertGreaterEqual(pred["summary"]["total_hex_cells"], 200)
                self.assertEqual(pred["summary"]["active_horizon"], horizon)
                self.assertEqual(pred["summary"]["active_model_key"], model_key)
                self.assertGreater(pred["summary"]["max_depth_cm"], 15.0)
                if horizon == "+1h":
                    preds_by_model[model_key] = [c["depth_cm"] for c in pred["hex_cells"]]

        # Verify that hgbr, mlp, and rf produce genuinely distinct model distributions (not uniform scalar multiples)
        self.assertNotEqual(preds_by_model["hgbr"], preds_by_model["mlp"])
        self.assertNotEqual(preds_by_model["hgbr"], preds_by_model["rf"])

        # Verify semi-diurnal +6h horizon rises when starting from low tide (1.20m) and ebbs when starting from high tide (2.20m)
        low_to_high_6h = self.ml_engine.predict_city_flood_map(
            tide_level_m=1.20, rain_mm_h=10.0, rain_3h_cum=20.0, tide_delta_m=0.18, horizon="+6h", model_key="hgbr"
        )
        high_to_low_6h = self.ml_engine.predict_city_flood_map(
            tide_level_m=2.20, rain_mm_h=10.0, rain_3h_cum=20.0, tide_delta_m=0.18, horizon="+6h", model_key="hgbr"
        )
        self.assertGreater(low_to_high_6h["summary"]["effective_tide_m"], 1.20)
        self.assertLess(high_to_low_6h["summary"]["effective_tide_m"], 2.20)

    def test_live_weather_and_validation_endpoints(self) -> None:
        """Verify /api/weather/live and input boundary validation on /api/incidents, /api/simulate, and /api/route."""
        import json
        from app import app as starlette_app

        async def call_asgi(method: str, path: str, query_string: str = "", payload: dict | None = None):
            body_bytes = json.dumps(payload).encode("utf-8") if payload else b""
            headers = [(b"content-type", b"application/json")] if payload else []
            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": query_string.encode("ascii"),
                "headers": headers,
            }
            sent = False
            status_code = 500
            chunks = []

            async def receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body_bytes, "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                elif message["type"] == "http.response.body":
                    chunks.append(message.get("body", b""))

            await starlette_app(scope, receive, send)
            return status_code, b"".join(chunks).decode("utf-8")

        st_live, body_live = asyncio.run(call_asgi("GET", "/api/weather/live", "district=cai_rang"))
        self.assertEqual(st_live, 200)
        live_json = json.loads(body_live)
        self.assertEqual(live_json["selected_district_id"], "cai_rang")
        self.assertEqual(live_json["timezone"], "Asia/Ho_Chi_Minh")

        # Out-of-bounds incident coordinates should return HTTP 400
        st_bad_inc, _ = asyncio.run(
            call_asgi(
                "POST",
                "/api/incidents",
                payload={"lat": 21.0285, "lon": 105.8542, "reported_depth_cm": 30},
            )
        )
        self.assertEqual(st_bad_inc, 400)

        # NaN depth in incident report must be rejected with HTTP 400 (not corrupting clusters)
        st_nan_inc, _ = asyncio.run(
            call_asgi(
                "POST",
                "/api/incidents",
                payload={"lat": 10.035, "lon": 105.776, "reported_depth_cm": "NaN"},
            )
        )
        self.assertEqual(st_nan_inc, 400)

        # Out-of-bounds or negative rainfall on /api/simulate should return HTTP 400
        st_bad_sim, _ = asyncio.run(
            call_asgi(
                "POST",
                "/api/simulate",
                payload={"tide_level_m": 2.1, "rain_mm_h": -15.0},
            )
        )
        self.assertEqual(st_bad_sim, 400)

        # Invalid node ID on /api/route should return HTTP 400
        st_bad_route, _ = asyncio.run(
            call_asgi(
                "POST",
                "/api/route",
                payload={"start_id": "invalid_node", "end_id": "ben_ninh_kieu"},
            )
        )
        self.assertEqual(st_bad_route, 400)

        # ST-DBSCAN with mixed timezone-aware (+07:00) and naive ISO timestamps must not crash
        mixed_tz_reports = [
            FloodReport("TZ1", 10.035, 105.776, 30.0, "2026-11-14T17:15:00+07:00", "Mưa", "U1", "Mậu Thân"),
            FloodReport("TZ2", 10.0352, 105.7762, 32.0, "2026-11-14T17:20:00", "Ngập", "U2", "Mậu Thân"),
        ]
        res_tz = run_st_dbscan(mixed_tz_reports)
        self.assertEqual(len(res_tz["master_incidents"]), 1)

    def _call_asgi_full(self, method: str, path: str, query_string: str = "", payload: dict | bytes | None = None):
        """Invoke Starlette ASGI app and return (status, headers_dict, decoded_body)."""
        import json
        from app import app as starlette_app

        if isinstance(payload, (bytes, bytearray)):
            body_bytes = bytes(payload)
        elif payload is not None:
            body_bytes = json.dumps(payload).encode("utf-8")
        else:
            body_bytes = b""

        headers = []
        if payload is not None:
            headers.append((b"content-type", b"application/json"))
            headers.append((b"content-length", str(len(body_bytes)).encode("ascii")))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "headers": headers,
        }
        sent = False
        status_code = 500
        resp_headers = {}
        chunks = []

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal status_code, resp_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                resp_headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        asyncio.run(starlette_app(scope, receive, send))
        return status_code, resp_headers, b"".join(chunks).decode("utf-8")

    def test_security_headers_and_request_id(self) -> None:
        """Verify OWASP security headers, CSP anti-framing, and X-Request-ID tracing."""
        from backend.security import rate_limiter

        rate_limiter.reset()
        status, headers, _ = self._call_asgi_full("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("x-frame-options"), "DENY")
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        self.assertIn("frame-ancestors 'none'", headers.get("content-security-policy", ""))
        self.assertTrue(bool(headers.get("x-request-id")))

    def test_tiered_cache_control_headers(self) -> None:
        """Verify Vercel Edge CDN s-maxage on live weather vs no-store on mutations."""
        from backend.security import rate_limiter

        rate_limiter.reset()
        st_live, h_live, _ = self._call_asgi_full("GET", "/api/weather/live", "district=ninh_kieu")
        self.assertEqual(st_live, 200)
        self.assertIn("s-maxage=25", h_live.get("cache-control", ""))

        st_inc, h_inc, _ = self._call_asgi_full("GET", "/api/incidents")
        self.assertEqual(st_inc, 200)
        self.assertIn("no-store", h_inc.get("cache-control", ""))

    def test_owasp_input_hardening_honeypot_payload_and_route_bounds(self) -> None:
        """Verify 16KB body cap (413), honeypot rejection (400), /api/route bounds, and no traceback leaks."""
        import json
        from backend.security import rate_limiter

        rate_limiter.reset()

        # 1. Payload > 16 KB must be rejected with 413
        oversized = b'{"note": "' + (b"x" * 17000) + b'"}'
        st_413, _, _ = self._call_asgi_full("POST", "/api/simulate", payload=oversized)
        self.assertEqual(st_413, 413)

        # 2. Bot filling hidden honeypot field 'website' must be rejected cleanly
        st_hp, _, _ = self._call_asgi_full(
            "POST",
            "/api/incidents",
            payload={"lat": 10.035, "lon": 105.776, "reported_depth_cm": 30, "website": "http://spam.bot"},
        )
        self.assertEqual(st_hp, 400)

        # 3. /api/route physical bounds check & malformed JSON without stacktrace leak
        st_bounds, _, body_bounds = self._call_asgi_full(
            "POST",
            "/api/route",
            payload={"start_id": "dhct_khu2", "end_id": "ben_ninh_kieu", "tide_level_m": 9.9},
        )
        self.assertEqual(st_bounds, 400)
        self.assertNotIn("Traceback", body_bounds)

        st_malformed, _, body_mal = self._call_asgi_full("POST", "/api/simulate", payload=b"{bad json")
        self.assertEqual(st_malformed, 400)
        self.assertNotIn("Expecting", body_mal)
        self.assertTrue(bool(json.loads(body_mal).get("error")))

    def test_rate_limiting_returns_429_and_resets(self) -> None:
        """Verify sliding-window IP rate limiter throttles bursts with HTTP 429 & Retry-After."""
        from backend.security import rate_limiter

        rate_limiter.reset()
        got_429 = False
        retry_hdr = ""
        # Default write bucket limit is 12/min; send 14 small invalid requests (or valid) cleanly
        for _ in range(15):
            st, hdrs, _ = self._call_asgi_full(
                "POST",
                "/api/incidents",
                payload={"lat": 21.0, "lon": 105.0, "reported_depth_cm": 10},
            )
            if st == 429:
                got_429 = True
                retry_hdr = hdrs.get("retry-after", "")
                break
        rate_limiter.reset()
        self.assertTrue(got_429)
        self.assertTrue(retry_hdr.isdigit())

    def test_sqlite_incident_persistence_across_restarts(self) -> None:
        """Verify Dual-Mode SQLite store persists citizen reports & uses real live timestamps."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "test_incidents.db"
            store1 = IncidentStore(backend="sqlite", sqlite_path=db_file, seed_demo=True)
            self.assertEqual(store1.storage_backend, "sqlite")
            res1 = store1.add_report(
                lat=10.0365,
                lon=105.7760,
                reported_depth_cm=31.0,
                description="<b>Ngập mới test</b>",
                location_hint="Mậu Thân - 3/2",
                rain_mm_h=35.0,
                tide_level_m=2.10,
            )
            self.assertEqual(res1["total_reports"], 9)

            # Re-open a fresh IncidentStore on the same SQLite database file
            store2 = IncidentStore(backend="sqlite", sqlite_path=db_file, seed_demo=True)
            res2 = store2.get_clustered_incidents(rain_mm_h=35.0, tide_level_m=2.10)
            self.assertEqual(res2["total_reports"], 9)
            newest_ts = res2["raw_reports"][0]["timestamp_iso"]
            self.assertFalse(newest_ts.startswith("2026-11-14"))

    def test_all_42_roads_street_snapped_no_straight_chords(self) -> None:
        """Verify 42/42 Can Tho roads have multi-point OSM street geometry and zero house-cutting straight lines."""
        from backend.cantho_geo import NODES_BY_ID, haversine_m

        self.assertEqual(len(ROAD_SEGMENTS), 42)
        for seg in ROAD_SEGMENTS:
            self.assertGreaterEqual(len(seg.coords), 3, f"{seg.id} must not be a 2-point straight chord")
            nu, nv = NODES_BY_ID[seg.u], NODES_BY_ID[seg.v]
            self.assertLessEqual(haversine_m(nu.lat, nu.lon, seg.coords[0][0], seg.coords[0][1]), 15.0)
            self.assertLessEqual(haversine_m(nv.lat, nv.lon, seg.coords[-1][0], seg.coords[-1][1]), 15.0)
        for sid in ("road_08", "road_17", "road_23", "road_31"):
            seg = next(s for s in ROAD_SEGMENTS if s.id == sid)
            self.assertGreaterEqual(len(seg.coords), 8, f"{sid} must follow true street geometry")

    def test_glofas_discharge_and_harmonic_tide_fields(self) -> None:
        """Verify live/fallback GloFAS river discharge & 4-constituent harmonic tide model metadata."""
        from backend.data_pipeline import fetch_cantho_realtime_hub

        hub = fetch_cantho_realtime_hub("ninh_kieu", force_refresh=False)
        astro = hub["astronomy_and_tide"]
        self.assertGreater(astro["river_discharge_m3s"], 500.0)
        self.assertGreaterEqual(astro["river_discharge_max_7d_m3s"], astro["river_discharge_m3s"] - 1.0)
        self.assertIn("GloFAS", astro["harmonic_model"])

    def test_incidents_default_empty_and_demo_non_destructive(self) -> None:
        """Verify clean empty default DB (no fake default pins) and RAM-only ?demo=true."""
        import json
        import tempfile
        import app as app_module
        from backend.security import rate_limiter

        rate_limiter.reset()
        orig_store = app_module.incident_store
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app_module.incident_store = IncidentStore(backend="sqlite", sqlite_path=Path(tmpdir) / "clean.db")
                st_real, _, body_real = self._call_asgi_full("GET", "/api/incidents")
                self.assertEqual(st_real, 200)
                real_json = json.loads(body_real)
                self.assertEqual(real_json["total_reports"], 0)
                self.assertEqual(len(real_json["master_incidents"]), 0)
                self.assertFalse(real_json["is_demo"])

                st_demo, _, body_demo = self._call_asgi_full("GET", "/api/incidents", "demo=true")
                self.assertEqual(st_demo, 200)
                demo_json = json.loads(body_demo)
                self.assertTrue(demo_json["is_demo"])
                self.assertEqual(len(demo_json["master_incidents"]), 3)

                # Confirm demo preview did not pollute clean DB
                _, _, body_after = self._call_asgi_full("GET", "/api/incidents")
                self.assertEqual(json.loads(body_after)["total_reports"], 0)
        finally:
            app_module.incident_store = orig_store
            rate_limiter.reset()

    def test_dynamic_weather_ttl_and_hydro_plausibility_guard(self) -> None:
        """Verify 60m dry vs 180m wet dynamic TTL and Hydro-Plausibility Guard anti-poisoning."""
        from datetime import datetime, timedelta
        from backend.incident_engine import VN_TZ, compute_hydro_plausibility

        now_vn = datetime.now(VN_TZ).replace(tzinfo=None, microsecond=0)
        store = IncidentStore(backend="memory", seed_demo=False)
        store.add_report(
            lat=10.0365,
            lon=105.7760,
            reported_depth_cm=28.0,
            description="Ngập test 95 phút trước",
            timestamp_iso=(now_vn - timedelta(minutes=95)).isoformat(),
        )

        # Active during wet/high-tide regime (TTL = 180m)
        wet = store.get_clustered_incidents(rain_mm_h=25.0, tide_level_m=2.05, now_vn=now_vn)
        self.assertEqual(wet["ttl_minutes"], 180)
        self.assertEqual(wet["total_reports"], 1)
        self.assertEqual(len(wet["pending_reports"]), 1)

        # Auto-expired during dry low-tide regime (TTL = 60m)
        dry = store.get_clustered_incidents(rain_mm_h=0.0, tide_level_m=1.35, now_vn=now_vn)
        self.assertEqual(dry["ttl_minutes"], 60)
        self.assertEqual(dry["total_reports"], 0)
        self.assertEqual(dry["expired_count"], 1)

        # Anomalous deep claim on high-elevation san_bay_can_tho in dry weather without photo requires >= 3 confirms
        anomalous = compute_hydro_plausibility(
            lat=10.077523, lon=105.714188, reported_depth_cm=50.0, rain_mm_h=0.0, tide_level_m=1.30, has_photo=False, report_count=2
        )
        self.assertLess(anomalous["score"], 50)
        self.assertEqual(anomalous["min_confirmations_for_fusion"], 3)

    def test_social_photo_svg_xss_block_vote_comment_and_astar_fusion(self) -> None:
        """Verify JPEG photo upload, SVG XSS block, +1 confirm -> ST-DBSCAN -> A* sensor fusion, comment & clear vote."""
        import json
        import tempfile
        import app as app_module
        from backend.security import rate_limiter

        rate_limiter.reset()
        orig_store = app_module.incident_store
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app_module.incident_store = IncidentStore(backend="sqlite", sqlite_path=Path(tmpdir) / "social.db")

                # 1. Block SVG XSS payload
                st_xss, _, _ = self._call_asgi_full(
                    "POST",
                    "/api/incidents",
                    payload={
                        "lat": 10.0365,
                        "lon": 105.7760,
                        "reported_depth_cm": 35.0,
                        "photo_data": "data:image/svg+xml;base64,PHN2Zy8+",
                    },
                )
                self.assertEqual(st_xss, 400)

                # 2. Post valid community report with JPEG data URL at Mau Than - Tran Hung Dao
                st_post, _, body_post = self._call_asgi_full(
                    "POST",
                    "/api/incidents",
                    payload={
                        "lat": 10.0365,
                        "lon": 105.7760,
                        "reported_depth_cm": 38.0,
                        "description": "Vỡ ống nước gây ngập cục bộ Mậu Thân",
                        "reporter_name": "Phát Võ",
                        "location_hint": "Mậu Thân - Trần Hưng Đạo",
                        "category": "flood_deep",
                        "district": "ninh_kieu",
                        "photo_data": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                    },
                )
                self.assertEqual(st_post, 201)
                posted = json.loads(body_post)
                self.assertEqual(len(posted["pending_reports"]), 1)
                rep_id = posted["pending_reports"][0]["primary_report_id"]

                # 3. Confirm +1 -> upgrades to verified ST-DBSCAN cluster (2 reports) & fusion_eligible=True
                st_vote, _, body_vote = self._call_asgi_full(
                    "POST",
                    "/api/incidents/vote",
                    payload={
                        "action": "confirm",
                        "lat": 10.0365,
                        "lon": 105.7760,
                        "reported_depth_cm": 38.0,
                        "location_hint": "Mậu Thân - Trần Hưng Đạo",
                    },
                )
                self.assertEqual(st_vote, 201)
                voted = json.loads(body_vote)
                self.assertEqual(len(voted["master_incidents"]), 1)
                self.assertTrue(voted["master_incidents"][0]["fusion_eligible"])

                # 4. Comment on the post
                st_cmt, _, body_cmt = self._call_asgi_full(
                    "POST",
                    "/api/incidents/comment",
                    payload={"report_id": rep_id, "author_name": "Minh", "content": "Đã xác nhận ngập sâu."},
                )
                self.assertEqual(st_cmt, 201)
                cmted = json.loads(body_cmt)
                self.assertEqual(len(cmted["master_incidents"][0]["comments"]), 1)

                # 5. Sensor Fusion in DRY weather (tide=1.35m, rain=0) injects 38cm into Mau Than & reroutes A*
                st_rt, _, body_rt = self._call_asgi_full(
                    "POST",
                    "/api/route",
                    payload={
                        "start_id": "dhct_khu2",
                        "end_id": "ben_ninh_kieu",
                        "vehicle_type": "scooter",
                        "tide_level_m": 1.35,
                        "rain_mm_h": 0.0,
                        "rain_3h_cum": 0.0,
                        "tide_delta_m": 0.0,
                        "horizon": "now",
                        "include_community": True,
                        "include_departure": True,
                    },
                )
                self.assertEqual(st_rt, 200)
                rt_json = json.loads(body_rt)
                self.assertGreater(len(rt_json["community_fused_roads"]), 0)
                self.assertEqual(len(rt_json["departure_advisor"]["windows"]), 4)
                self.assertTrue(rt_json["flood_aware_route"]["is_safe"])

                # 6. Clear twice resolves verified cluster cleanly
                self._call_asgi_full("POST", "/api/incidents/vote", payload={"action": "clear", "lat": 10.0365, "lon": 105.7760, "reported_depth_cm": 0})
                st_clr, _, body_clr = self._call_asgi_full("POST", "/api/incidents/vote", payload={"action": "clear", "lat": 10.0365, "lon": 105.7760, "reported_depth_cm": 0})
                self.assertEqual(st_clr, 201)
                self.assertEqual(len(json.loads(body_clr)["master_incidents"]), 0)
        finally:
            app_module.incident_store = orig_store
            rate_limiter.reset()

    def test_hybrid_pins_cameras_geocode_and_vote_security(self) -> None:
        """Verify hybrid custom coords routing, 8-station /api/cameras, /api/geocode & vote OWASP protection."""
        import json
        from backend.security import rate_limiter

        rate_limiter.reset()
        # Hybrid custom coordinates (no preset IDs required)
        st_hyb, _, body_hyb = self._call_asgi_full(
            "POST",
            "/api/route",
            payload={
                "start_id": "vvk_mau_than",
                "end_id": "ben_ninh_kieu",
                "start_coord": [10.0427, 105.7670],
                "end_coord": [10.0315, 105.7875],
                "start_label": "Lotte Mart",
                "end_label": "Chợ đêm Ninh Kiều",
                "vehicle_type": "scooter",
                "tide_level_m": 1.45,
                "rain_mm_h": 0.0,
                "horizon": "now",
                "include_departure": False,
            },
        )
        self.assertEqual(st_hyb, 200)
        hyb = json.loads(body_hyb)
        self.assertIn("hybrid_endpoints", hyb)
        self.assertEqual(hyb["flood_aware_route"]["node_names"][0], "Lotte Mart")

        # 8-station Cameras endpoint
        st_cam, _, body_cam = self._call_asgi_full("GET", "/api/cameras")
        self.assertEqual(st_cam, 200)
        cams = json.loads(body_cam)
        self.assertEqual(cams["total"], 8)
        self.assertIn("google_street_view_url", cams["cameras"][0])

        # Geocode endpoint offline node matching
        st_geo, _, body_geo = self._call_asgi_full("GET", "/api/geocode", "q=Ninh")
        self.assertEqual(st_geo, 200)
        self.assertGreater(len(json.loads(body_geo)["results"]), 0)

        # Honeypot on vote endpoint must fail with 400
        st_hp, _, _ = self._call_asgi_full(
            "POST",
            "/api/incidents/vote",
            payload={"action": "confirm", "lat": 10.0365, "lon": 105.7760, "website": "spam.bot"},
        )
        self.assertEqual(st_hp, 400)
        rate_limiter.reset()

    def test_production_minified_assets_and_fast_ml_cold_start(self) -> None:
        """Verify esbuild production assets exist, reduce size, and ML engine initializes cleanly."""
        import time
        from pathlib import Path
        from backend.ml_engine import DATASET_STATS_PATH, CanThoFloodMLEngine

        root = Path(__file__).resolve().parent.parent
        self.assertTrue(DATASET_STATS_PATH.exists())
        js_src = root / "static" / "app.js"
        js_min = root / "public" / "assets" / "app.min.js"
        css_src = root / "static" / "styles.css"
        css_min = root / "public" / "assets" / "styles.min.css"
        self.assertTrue(js_min.exists())
        self.assertTrue(css_min.exists())
        self.assertLess(js_min.stat().st_size, js_src.stat().st_size)
        self.assertLess(css_min.stat().st_size, css_src.stat().st_size)

        t0 = time.perf_counter()
        engine = CanThoFloodMLEngine(force_retrain=False)
        elapsed = time.perf_counter() - t0
        self.assertGreater(engine.dataset_stats["total_records"], 1000)
        self.assertLess(elapsed, 3.0)

    def test_zero_parallel_double_lines_and_uturn_loops(self) -> None:
        """Verify 42/42 road segments and stitched A* routes contain zero dual-carriageway double lines."""
        import math
        from backend.cantho_geo import haversine_m

        def _bearing(a, b):
            lat1, lon1 = math.radians(a[0]), math.radians(a[1])
            lat2, lon2 = math.radians(b[0]), math.radians(b[1])
            dl = lon2 - lon1
            y = math.sin(dl) * math.cos(lat2)
            x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dl)
            return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

        def _angle_diff(b1, b2):
            d = abs(b1 - b2) % 360.0
            return 360.0 - d if d > 180.0 else d

        def _assert_no_double_back(coords, label):
            cum = [0.0]
            for k in range(1, len(coords)):
                cum.append(cum[-1] + haversine_m(coords[k - 1][0], coords[k - 1][1], coords[k][0], coords[k][1]))
            for i in range(len(coords) - 2):
                for j in range(i + 2, len(coords)):
                    arc = cum[j] - cum[i]
                    if 70.0 <= arc <= 700.0:
                        gap = haversine_m(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
                        if gap < 30.0:
                            mid = max(range(i + 1, j), key=lambda m: haversine_m(coords[i][0], coords[i][1], coords[m][0], coords[m][1]))
                            heading_rev = _angle_diff(_bearing(coords[i], coords[mid]), _bearing(coords[mid], coords[j]))
                            self.assertLess(
                                heading_rev,
                                120.0,
                                f"{label} contains U-turn double line (idx={i}..{j}, arc={arc:.1f}m, gap={gap:.1f}m, angle={heading_rev:.1f})",
                            )

        for seg in ROAD_SEGMENTS:
            _assert_no_double_back(list(seg.coords), seg.id)

        dry_depths = {seg.id: 0.0 for seg in ROAD_SEGMENTS}
        for s_id, e_id in (
            ("mauthan_thd", "dhct_khu2"),
            ("dhct_khu2", "ben_ninh_kieu"),
            ("vvk_mau_than", "ben_ninh_kieu"),
            ("dhct_khu2", "san_bay_can_tho"),
            ("mauthan_thd", "kdc_hung_phu"),
        ):
            res = find_flood_aware_route(s_id, e_id, "scooter", dry_depths)
            fa = res["flood_aware_route"]
            _assert_no_double_back(fa["coords"], f"{s_id}->{e_id}")

        cam01_route = find_flood_aware_route("mauthan_thd", "dhct_khu2", "scooter", dry_depths)["flood_aware_route"]
        self.assertLessEqual(cam01_route["total_length_km"], 1.45)

    def test_osm_graph_free_pin_direct_routing(self) -> None:
        """Verify Free-Pin A->B on Mau Than / Nguyen Van Troi routes directly (<0.95 km, <150 ms) with zero detours."""
        import time
        from backend.cantho_geo import haversine_m
        from backend.routing_engine import find_hybrid_flood_aware_route

        dry_pred = self.ml_engine.predict_city_flood_map(1.01, 0.0, 0.0, 0.0)
        dry_hex = [float(c["depth_cm"]) for c in dry_pred["hex_cells"]]

        t0 = time.perf_counter()
        res = find_hybrid_flood_aware_route(
            start_id="vvk_mau_than",
            end_id="ben_ninh_kieu",
            vehicle_type="scooter",
            road_depths=dry_pred["road_depths"],
            start_coord=(10.0412, 105.7683),
            end_coord=(10.0376, 105.7724),
            start_label="Điểm A",
            end_label="Điểm B",
            hex_depths=dry_hex,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        fa = res["flood_aware_route"]
        self.assertLess(elapsed_ms, 150.0)
        self.assertLessEqual(fa["total_length_km"], 0.95)
        self.assertGreaterEqual(len(fa["coords"]), 4)
        # No parallel double-back along result
        coords = fa["coords"]
        cum = [0.0]
        for k in range(1, len(coords)):
            cum.append(cum[-1] + haversine_m(coords[k - 1][0], coords[k - 1][1], coords[k][0], coords[k][1]))
        for i in range(len(coords) - 2):
            for j in range(i + 2, len(coords)):
                if 70.0 <= (cum[j] - cum[i]) <= 700.0:
                    self.assertGreaterEqual(haversine_m(coords[i][0], coords[i][1], coords[j][0], coords[j][1]), 30.0)

    def test_osm_graph_flood_avoidance_and_google_maps_url(self) -> None:
        """Verify OSM Flood-Aware A* avoids spring-tide flooding and builds valid Google Maps waypoint deep-link."""
        pred = self.ml_engine.predict_city_flood_map(2.22, 45.0, 82.0, 0.18)
        hex_d = [float(c["depth_cm"]) for c in pred["hex_cells"]]
        res = find_flood_aware_route(
            start_id="dhct_khu2",
            end_id="ben_ninh_kieu",
            vehicle_type="scooter",
            road_depths=pred["road_depths"],
            hex_depths=hex_d,
        )
        std = res["standard_route"]
        safe = res["flood_aware_route"]
        self.assertFalse(std["is_safe"])
        self.assertTrue(safe["is_safe"])
        self.assertGreater(safe["total_length_km"], std["total_length_km"])
        self.assertIn("https://www.google.com/maps/dir/?api=1", res["google_maps_url"])
        self.assertIn("waypoints=", res["google_maps_url"])

    def test_preset_26_nodes_real_coordinates_and_zero_duplicates(self) -> None:
        """Verify all 26 preset Can Tho nodes are pairwise distinct (>=150m) and snap closely (<40m) to OSM roads."""
        from backend.cantho_geo import haversine_m
        from backend.osm_graph import get_osm_graph

        osm_g = get_osm_graph()
        self.assertIsNotNone(osm_g)
        self.assertGreater(len(osm_g.nodes), 4000)
        for i in range(len(CANTHO_NODES)):
            anchor = osm_g.snap_coord(CANTHO_NODES[i].lat, CANTHO_NODES[i].lon)
            self.assertLessEqual(anchor.snap_offset_m, 40.0, f"{CANTHO_NODES[i].id} snapped too far: {anchor.snap_offset_m}m")
            for j in range(i + 1, len(CANTHO_NODES)):
                d = haversine_m(CANTHO_NODES[i].lat, CANTHO_NODES[i].lon, CANTHO_NODES[j].lat, CANTHO_NODES[j].lon)
                self.assertGreaterEqual(d, 150.0, f"Nodes {CANTHO_NODES[i].id} & {CANTHO_NODES[j].id} overlap ({d:.1f}m)")

    def test_poi_search_exact_lotte_entrance_and_accent_insensitive(self) -> None:
        """Verify /api/geocode returns LOTTE Mart entrance #1 (<60m from gate) and handles accent-insensitive POIs."""
        import json
        from backend.cantho_geo import haversine_m

        st_l, _, body_l = self._call_asgi_full("GET", "/api/geocode", "q=Lotte")
        self.assertEqual(st_l, 200)
        hits_l = json.loads(body_l)["results"]
        self.assertGreaterEqual(len(hits_l), 1)
        self.assertIn("LOTTE", hits_l[0]["label"].upper())
        # Must sit right at LOTTE Mart entrance (10.042184, 105.766645), NOT 220m away at Võ Văn Kiệt intersection
        self.assertLessEqual(haversine_m(hits_l[0]["lat"], hits_l[0]["lon"], 10.042184, 105.766645), 60.0)

        for q in ("sense city", "dh fpt", "vincom xuan khanh", "ben xe trung tam", "go can tho"):
            st_q, _, body_q = self._call_asgi_full("GET", "/api/geocode", f"q={q.replace(' ', '%20')}")
            self.assertEqual(st_q, 200)
            res_q = json.loads(body_q)["results"]
            self.assertGreaterEqual(len(res_q), 1, f"Expected POI hits for '{q}'")
            self.assertIn("category_label", res_q[0])

        st_pop, _, body_pop = self._call_asgi_full("GET", "/api/geocode", "popular=1")
        self.assertEqual(st_pop, 200)
        self.assertGreaterEqual(len(json.loads(body_pop)["results"]), 8)

    def test_extended_osm_graph_dh_fpt_and_lotte_routing(self) -> None:
        """Verify extended OSM graph routes smoothly from DH FPT & LOTTE Mart gates (<40m snap, <150ms)."""
        import time
        from backend.routing_engine import find_hybrid_flood_aware_route

        dry_pred = self.ml_engine.predict_city_flood_map(1.11, 0.0, 0.0, 0.0)
        dry_hex = [float(c["depth_cm"]) for c in dry_pred["hex_cells"]]

        for s_lbl, s_pt, e_id, e_pt in (
            ("Đại học FPT Cần Thơ", (10.011934, 105.731558), "ben_ninh_kieu", (10.033663, 105.788349)),
            ("LOTTE Mart Cần Thơ", (10.042184, 105.766645), "ly_tu_trong", (10.035297, 105.777151)),
        ):
            t0 = time.perf_counter()
            res = find_hybrid_flood_aware_route(
                start_id="dhct_khu2",
                end_id=e_id,
                vehicle_type="scooter",
                road_depths=dry_pred["road_depths"],
                start_coord=s_pt,
                end_coord=e_pt,
                start_label=s_lbl,
                end_label=e_id,
                hex_depths=dry_hex,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertLess(elapsed_ms, 150.0)
            self.assertLessEqual(res["osm_snap"]["start_offset_m"], 40.0)
            self.assertLessEqual(res["osm_snap"]["end_offset_m"], 40.0)
            self.assertGreaterEqual(len(res["flood_aware_route"]["coords"]), 6)

    def test_realtime_state_delta_and_signature_invalidation(self) -> None:
        """Verify O(1) signature invalidation on report/vote/comment and delta state payload."""
        import json
        import tempfile
        import app as app_module
        from backend.security import rate_limiter

        rate_limiter.reset()
        orig_store = app_module.incident_store
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app_module.incident_store = IncidentStore(backend="sqlite", sqlite_path=Path(tmpdir) / "rt_delta.db")

                st1, h1, b1 = self._call_asgi_full("GET", "/api/realtime/state")
                self.assertEqual(st1, 200)
                self.assertIn("no-store", h1.get("cache-control", ""))
                d1 = json.loads(b1)
                self.assertTrue(d1["changed"])
                sig1 = d1["signature"]

                st_same, _, b_same = self._call_asgi_full("GET", "/api/realtime/state", f"since={sig1}")
                self.assertEqual(st_same, 200)
                d_same = json.loads(b_same)
                self.assertFalse(d_same["changed"])
                self.assertNotIn("incidents", d_same)

                st_post, _, _ = self._call_asgi_full(
                    "POST",
                    "/api/incidents",
                    payload={"lat": 10.0365, "lon": 105.7760, "reported_depth_cm": 28.0, "location_hint": "Mậu Thân"},
                )
                self.assertEqual(st_post, 201)

                st2, _, b2 = self._call_asgi_full("GET", "/api/realtime/state", f"since={sig1}")
                self.assertEqual(st2, 200)
                d2 = json.loads(b2)
                self.assertTrue(d2["changed"])
                self.assertNotEqual(d2["signature"], sig1)
                self.assertEqual(d2["incidents"]["total_reports"], 1)
        finally:
            app_module.incident_store = orig_store
            rate_limiter.reset()

    def test_realtime_sse_stream_subsecond_push(self) -> None:
        """Verify SSE endpoint emits initial pulse and sub-second flood_update when bus publishes."""
        import asyncio
        import app as app_module
        from backend.realtime_hub import realtime_bus
        from backend.security import rate_limiter

        rate_limiter.reset()

        async def _run_stream_test():
            captured = []
            req_sent = False
            done_ev = asyncio.Event()

            async def _receive():
                nonlocal req_sent
                if not req_sent:
                    req_sent = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await done_ev.wait()
                return {"type": "http.disconnect"}

            async def _send(message):
                captured.append(message)
                if message.get("type") == "http.response.body" and not message.get("more_body", False):
                    done_ev.set()

            async def _trigger_publish():
                await asyncio.sleep(0.08)
                realtime_bus.publish("flood_update", {"reason": "unit_test", "signature": "test-sig-2"})

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/api/realtime/stream",
                "raw_path": b"/api/realtime/stream",
                "query_string": b"max_seconds=0.45",
                "headers": [],
                "client": ("127.0.0.1", 55000),
                "server": ("127.0.0.1", 8088),
            }
            pub_task = asyncio.create_task(_trigger_publish())
            await app_module.app(scope, _receive, _send)
            await pub_task
            return captured

        msgs = asyncio.run(_run_stream_test())
        start_msg = next(m for m in msgs if m["type"] == "http.response.start")
        self.assertEqual(start_msg["status"], 200)
        hdrs = {k.decode("latin1").lower(): v.decode("latin1") for k, v in start_msg["headers"]}
        self.assertIn("text/event-stream", hdrs.get("content-type", ""))
        self.assertEqual(hdrs.get("x-accel-buffering"), "no")

        body_text = b"".join(m.get("body", b"") for m in msgs if m["type"] == "http.response.body").decode("utf-8")
        self.assertIn("event: pulse", body_text)
        self.assertIn("event: flood_update", body_text)
        self.assertIn("test-sig-2", body_text)


if __name__ == "__main__":
    unittest.main()

"""OWASP Security Hardening, Tiered Edge CDN Caching, Rate Limiting & JSON Logging.

Designed for both Vercel Fluid Compute production and local ASGI development.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

MAX_POST_BODY_BYTES = 16 * 1024  # 16 KB cap for standard JSON API payloads
MAX_INCIDENT_POST_BYTES = 160 * 1024  # 160 KB cap for community posts with compressed JPEG/WebP photo
MAX_PHOTO_DATA_URL_CHARS = 125_000
HTML_TAG_RE = re.compile(r"<[^>]*>")
CTRL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
SAFE_PHOTO_RE = re.compile(r"^data:image/(?:jpeg|webp);base64,[A-Za-z0-9+/=]+$")

logger = logging.getLogger("cantho.access")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def sanitize_text(val: object, max_len: int = 280) -> str:
    """Strip HTML tags and control characters, normalize whitespace, and clamp length."""
    cleaned = HTML_TAG_RE.sub("", str(val or ""))
    cleaned = CTRL_CHAR_RE.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    return cleaned[:max_len]


def validate_photo_data_url(raw: object) -> Tuple[bool, str]:
    """Allow empty string or strict compressed JPEG/WebP base64 data URL (blocks SVG/HTML XSS)."""
    val = str(raw or "").strip()
    if not val:
        return True, ""
    if len(val) > MAX_PHOTO_DATA_URL_CHARS:
        return False, ""
    if not SAFE_PHOTO_RE.match(val):
        return False, ""
    return True, val


def get_client_ip(request: Request) -> str:
    """Extract client IP safely across reverse proxies and synthetic ASGI scopes."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    xri = request.headers.get("x-real-ip", "")
    if xri:
        return xri.strip()
    client = getattr(request, "client", None)
    if client and getattr(client, "host", None):
        return str(client.host)
    return "127.0.0.1"


def anonymize_ip(ip: str) -> str:
    """Return short irreversible hash of IP address for GDPR-friendly access logs."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:10]


def parse_allowed_origins(is_prod: bool) -> List[str]:
    """Resolve strict CORS allowlist from environment variables (never '*' in production)."""
    raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
    origins = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    if is_prod:
        for env_key in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
            host = os.environ.get(env_key, "").strip()
            if host:
                url = host if host.startswith("https://") else f"https://{host}"
                if url not in origins:
                    origins.append(url)
        return origins
    if not origins:
        origins.extend(["http://127.0.0.1:8088", "http://localhost:8088"])
    return origins


class SlidingWindowRateLimiter:
    """Thread/async-friendly per-IP sliding-window rate limiter with bucket quotas."""

    def __init__(self) -> None:
        self._hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

    def reset(self) -> None:
        """Clear all recorded rate-limit windows (useful for test isolation)."""
        self._hits.clear()

    def check(self, key: Tuple[str, str], limit: int, window_sec: float = 60.0) -> Tuple[bool, int]:
        """Record a hit and return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        dq = self._hits[key]
        cutoff = now - window_sec
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = max(1, int(round(window_sec - (now - dq[0]))))
            return False, retry_after
        dq.append(now)
        return True, 0


rate_limiter = SlidingWindowRateLimiter()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach hardened OWASP HTTP response headers including strict CSP & anti-framing."""

    def __init__(self, app, is_prod: bool = False) -> None:
        super().__init__(app)
        self.is_prod = is_prod
        self.csp = (
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: blob: https://tile.openstreetmap.org https://*.tile.openstreetmap.org https://server.arcgisonline.com https://unpkg.com; "
            "connect-src 'self'; "
            "worker-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = self.csp
        if self.is_prod or request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce 16 KB POST body limit and sliding-window IP throttling across APIs."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method.upper()

        if method in ("POST", "PUT", "PATCH"):
            max_allowed_bytes = (
                MAX_INCIDENT_POST_BYTES
                if (method == "POST" and path == "/api/incidents")
                else MAX_POST_BODY_BYTES
            )
            limit_label = "160 KB" if max_allowed_bytes == MAX_INCIDENT_POST_BYTES else "16 KB"

            content_len = request.headers.get("content-length")
            if content_len is not None:
                try:
                    if int(content_len) > max_allowed_bytes:
                        return JSONResponse(
                            {"error": f"Dữ liệu gửi lên vượt quá giới hạn {limit_label}."},
                            status_code=413,
                        )
                except ValueError:
                    return JSONResponse({"error": "Header Content-Length không hợp lệ."}, status_code=400)

            body = await request.body()
            if len(body) > max_allowed_bytes:
                return JSONResponse(
                    {"error": f"Dữ liệu gửi lên vượt quá giới hạn {limit_label}."},
                    status_code=413,
                )

        if path.startswith("/api/") and path != "/api/health":
            ip = get_client_ip(request)
            if path.startswith("/api/realtime/"):
                bucket = "realtime"
                limit = int(os.environ.get("RATE_LIMIT_REALTIME_RPM", "240"))
            elif method == "POST" and path.startswith("/api/incidents"):
                bucket = "write"
                limit = int(os.environ.get("RATE_LIMIT_WRITE_RPM", "12"))
            elif method == "POST":
                bucket = "compute"
                limit = int(os.environ.get("RATE_LIMIT_COMPUTE_RPM", "60"))
            else:
                bucket = "read"
                limit = int(os.environ.get("RATE_LIMIT_READ_RPM", "120"))

            allowed, retry_after = rate_limiter.check((ip, bucket), limit=limit, window_sec=60.0)
            if not allowed:
                return JSONResponse(
                    {"error": "Bạn gửi quá nhiều yêu cầu liên tiếp. Vui lòng thử lại sau ít giây."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )

        return await call_next(request)


class TieredCacheMiddleware(BaseHTTPMiddleware):
    """Differentiate Edge CDN cacheability across static assets, live weather, and mutations."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        method = request.method.upper()

        if method != "GET":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return response

        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
        elif path == "/api/weather/live":
            refresh_raw = request.query_params.get("refresh", "false").lower()
            if refresh_raw in ("1", "true", "yes"):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            else:
                response.headers["Cache-Control"] = "public, s-maxage=25, stale-while-revalidate=60"
        elif path == "/api/init":
            response.headers["Cache-Control"] = "public, s-maxage=300, stale-while-revalidate=600"
        elif path in ("/manifest.json", "/sw.js", "/api/health"):
            response.headers["Cache-Control"] = "public, max-age=60"
        elif path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Attach X-Request-ID and emit structured single-line JSON telemetry per request."""

    async def dispatch(self, request: Request, call_next):
        req_id = sanitize_text(request.headers.get("x-request-id", ""), 24) or uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = req_id
            return response
        finally:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            record = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "req_id": req_id,
                "method": request.method,
                "path": request.url.path,
                "status": status_code,
                "ms": elapsed_ms,
                "ip_hash": anonymize_ip(get_client_ip(request)),
            }
            logger.info(json.dumps(record, ensure_ascii=False))

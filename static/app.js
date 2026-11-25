/* ============================================================================
   Can Tho Weather, Tide & Flood Routing Frontend
   ============================================================================ */

(() => {
  "use strict";

  let svgUid = 0;
  const nextUid = (prefix) => `${prefix}_${++svgUid}`;

  const state = {
    mode: "weather",
    mood: "vellum",
    selectedDistrictId: "ninh_kieu",
    selectedVehicleId: "scooter",
    activeHorizon: "now",
    activeBasemap: "street",
    isSimulationOverride: false,
    showAllFlooded: false,
    sheetCollapsed: false,
    pinMode: false,
    pinTarget: null, // "incident" | "route_start" | "route_end" | null
    pinnedCoord: null,
    routeStart: { nodeId: "dhct_khu2", lat: 10.031108, lon: 105.773046, label: "ĐH Cần Thơ Khu II (Cổng A · Đường 3/2)", custom: false },
    routeEnd: { nodeId: "ben_ninh_kieu", lat: 10.033663, lon: 105.788349, label: "Bến Ninh Kiều (Hai Bà Trưng)", custom: false },
    popularPlacesCache: null,
    lastGoogleMapsUrl: "",
    incidentDemoMode: false,
    incidentCategory: "flood_deep",
    incidentPhotoData: "",
    latestIncidentsData: null,
    feedFilter: "all",
    feedDistrictFilter: "all",
    expandedComments: {},
    camerasData: null,
    selectedCameraId: "cam_32_mauthan",
    initData: null,
    weatherData: null,
    liveSimData: null,
    activeSimData: null,
    activeRouteLabelCoords: [],
    placedFloodLabelCoords: [],
    map: null,
    basemaps: {},
    layers: {
      roads: null,
      cameras: null,
      stations: null,
      incidents: null,
      hex: null,
      route: null,
      landmarks: null,
      pinMarker: null,
      customPins: null,
    },
    r3f: {
      ready: false,
      hoverX: null,
      hoverY: null,
      curElevM: 1.35,
      targetElevM: 1.35,
      curTideM: 1.41,
      targetTideM: 1.41,
      tideDelta: 0.0,
      maxDepthCm: 0,
      districtName: "Ninh Kiều",
      isDay: true,
      isRaining: false,
      cloudPct: 45,
      windKmh: 6,
      lunarDay: 15,
      moonPct: 100,
      floodTier: "safe",
      drops: [],
      rafId: null,
      resize: null,
    },
    realtime: {
      sig: null,
      es: null,
      bc: null,
      connected: false,
      lastSyncTs: 0,
      failCount: 0,
      fallbackTimer: null,
      sseRetryTimer: null,
      hideTimer: null,
      handlingUpdate: false,
    },
  };

  /* --------------------------------------------------------------------------
     Helpers & Vector SVG Icon Library (Zero Emoji Policy)
     -------------------------------------------------------------------------- */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function escapeHtml(val) {
    return String(val ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function stripEmoji(text) {
    return String(text ?? "")
      .replace(/^[\p{Extended_Pictographic}\u2600-\u27BF\uFE0F\u200D\s]+/gu, "")
      .trim();
  }

  function shortStreetName(fullName) {
    let clean = String(fullName || "").split("(")[0].split("->")[0].trim();
    clean = clean.replace(/^Đường\s+/i, "").replace(/^Đại lộ\s+/i, "ĐL ").replace(/^Khu vực\s+/i, "");
    return clean.length > 22 ? `${clean.slice(0, 20)}...` : clean;
  }

  function roundOne(v) {
    return Math.round(Number(v || 0) * 10) / 10;
  }

  function showToast(msg) {
    const container = $("#toast-container");
    if (!container) return;
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = stripEmoji(msg);
    container.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity 0.2s ease";
      setTimeout(() => el.remove(), 220);
    }, 3000);
  }

  const UI_ICONS = {
    shieldCheck: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></svg>`,
    alertTriangle: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>`,
    wavesDanger: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12c2.2-1.6 4.4-1.6 6.6 0s4.4 1.6 6.6 0 4.4-1.6 6.8 0"/><path d="M2 18c2.2-1.6 4.4-1.6 6.6 0s4.4 1.6 6.6 0 4.4-1.6 6.8 0"/><path d="M12 3v6"/></svg>`,
    scooter: `<svg class="veh-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="17" r="2.5"/><circle cx="18" cy="17" r="2.5"/><path d="M8.5 17H13l2.5-6.5h2.5"/><path d="M10 10h4"/><path d="M15 6h2.5l1 4.5"/></svg>`,
    motorbike: `<svg class="veh-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"><circle cx="5.5" cy="16.5" r="3"/><circle cx="18.5" cy="16.5" r="3"/><path d="M8.5 16.5h4.5l3-6H12l-2.5 3.5H6"/><path d="M15 7h3l1 3.5"/></svg>`,
    sedan: `<svg class="veh-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"><path d="M5 17h14v-4l-2.2-4.5H7.2L5 13v4Z"/><circle cx="7.5" cy="17" r="2"/><circle cx="16.5" cy="17" r="2"/><path d="M3 13h18"/></svg>`,
    suv: `<svg class="veh-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"><path d="M3 16h18v-5l-2-4H6.5L4 11v5Z"/><circle cx="7" cy="16.5" r="2.2"/><circle cx="17" cy="16.5" r="2.2"/><path d="M3 12h18"/></svg>`,
  };

  function vehicleIconFor(id) {
    const key = String(id || "").toLowerCase();
    if (key.includes("suv")) return UI_ICONS.suv;
    if (key.includes("sedan") || key.includes("car")) return UI_ICONS.sedan;
    if (key.includes("manual") || key.includes("wave")) return UI_ICONS.motorbike;
    return UI_ICONS.scooter;
  }

  function getWeatherSVG(iconKey = "partly-cloudy") {
    const key = String(iconKey || "partly-cloudy").toLowerCase();
    const sunG = nextUid("sun");
    const cloudG = nextUid("cloud");

    if (key.includes("thunder") || key.includes("storm")) {
      return `<svg viewBox="0 0 48 48" fill="none">
        <defs><linearGradient id="${cloudG}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#cbd5e1"/><stop offset="100%" stop-color="#64748b"/></linearGradient></defs>
        <path d="M14 29h21a7 7 0 0 0 1-13.9A10 10 0 0 0 16.6 13a7.5 7.5 0 0 0-2.6 16Z" fill="url(#${cloudG})"/>
        <path d="M25 29l-4 7h4.5L22 44l8.5-9.5H26L28.5 29Z" fill="#fbbf24"/>
      </svg>`;
    }

    if (key.includes("rain") || key.includes("drizzle") || key.includes("shower")) {
      return `<svg viewBox="0 0 48 48" fill="none">
        <defs><linearGradient id="${cloudG}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#e2e8f0"/><stop offset="100%" stop-color="#64748b"/></linearGradient></defs>
        <path d="M14 29h21a7 7 0 0 0 1-13.9A10 10 0 0 0 16.6 13a7.5 7.5 0 0 0-2.6 16Z" fill="url(#${cloudG})"/>
        <path d="M18 33l-2 6M25 33l-2 7M32 33l-2 6" stroke="#38bdf8" stroke-width="2.4" stroke-linecap="round"/>
      </svg>`;
    }

    if (key === "clear" || key === "sunny") {
      return `<svg viewBox="0 0 48 48" fill="none">
        <defs><radialGradient id="${sunG}" cx="35%" cy="35%" r="65%"><stop offset="0%" stop-color="#fde68a"/><stop offset="100%" stop-color="#f59e0b"/></radialGradient></defs>
        <circle cx="24" cy="24" r="10.5" fill="url(#${sunG})"/>
        <path d="M24 6v4M24 38v4M6 24h4M38 24h4M11.3 11.3l2.8 2.8M33.9 33.9l2.8 2.8M11.3 36.7l2.8-2.8M33.9 14.1l2.8-2.8" stroke="#fbbf24" stroke-width="2.2" stroke-linecap="round"/>
      </svg>`;
    }

    if (key.includes("cloud") && !key.includes("partly")) {
      return `<svg viewBox="0 0 48 48" fill="none">
        <defs><linearGradient id="${cloudG}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f1f5f9"/><stop offset="100%" stop-color="#94a3b8"/></linearGradient></defs>
        <path d="M13 33h23a7 7 0 0 0 1-13.9A10 10 0 0 0 17.6 17a7.5 7.5 0 0 0-4.6 16Z" fill="url(#${cloudG})"/>
      </svg>`;
    }

    return `<svg viewBox="0 0 48 48" fill="none">
      <defs>
        <radialGradient id="${sunG}" cx="35%" cy="35%" r="65%"><stop offset="0%" stop-color="#fde68a"/><stop offset="100%" stop-color="#f59e0b"/></radialGradient>
        <linearGradient id="${cloudG}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f8fafc"/><stop offset="100%" stop-color="#94a3b8"/></linearGradient>
      </defs>
      <circle cx="19" cy="19" r="7.5" fill="url(#${sunG})"/>
      <path d="M19 8v2.5M19 27.5V30M8 19h2.5M11.2 11.2l1.8 1.8M26.8 11.2l-1.8 1.8" stroke="#fbbf24" stroke-width="1.8" stroke-linecap="round"/>
      <path d="M16 34h19a6 6 0 0 0 .8-11.9A8.5 8.5 0 0 0 19 20a6.5 6.5 0 0 0-3 14Z" fill="url(#${cloudG})"/>
    </svg>`;
  }

  /* --------------------------------------------------------------------------
     Cross-Section Interactive Hover Crosshair (#r3f-canvas)
     -------------------------------------------------------------------------- */
  function initPointerTracking() {
    const canvas = $("#r3f-canvas");
    if (!canvas) return;
    const clearHover = () => {
      state.r3f.hoverX = null;
      state.r3f.hoverY = null;
    };
    canvas.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse") return;
      const rect = canvas.getBoundingClientRect();
      state.r3f.hoverX = e.clientX - rect.left;
      state.r3f.hoverY = e.clientY - rect.top;
    });
    canvas.addEventListener("pointerleave", clearHover);
    canvas.addEventListener("pointerup", (e) => {
      if (e.pointerType !== "mouse") clearHover();
    });
    canvas.addEventListener("pointercancel", clearHover);
  }

  /* --------------------------------------------------------------------------
     Live Sky-to-River Cross-Section Instrument ("Trời – Phố – Sông Hậu")
     -------------------------------------------------------------------------- */
  function initThreeWeatherScene() {
    const canvas = $("#r3f-canvas");
    const fallback = $("#r3f-fallback");
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) {
      if (fallback) {
        fallback.innerHTML = getWeatherSVG("partly-cloudy");
        fallback.classList.remove("hidden");
      }
      return;
    }

    const wrap = canvas.parentElement;
    let cw = 540;
    let ch = 340;
    let dpr = 1;

    const resize = () => {
      cw = wrap?.clientWidth || 540;
      ch = wrap?.clientHeight || 290;
      if (cw < 40) cw = 540;
      if (ch < 40) ch = 290;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(cw * dpr);
      canvas.height = Math.round(ch * dpr);
    };
    state.r3f.resize = resize;
    window.addEventListener("resize", resize);
    if (wrap && typeof ResizeObserver !== "undefined") {
      new ResizeObserver(resize).observe(wrap);
    }
    resize();

    // Seed angled engraving rain streaks
    state.r3f.drops = Array.from({ length: 18 }, (_, i) => ({
      xFrac: 0.08 + ((i * 0.053) % 0.84),
      phase: (i * 0.19) % 1,
      speed: 0.72 + (i % 4) * 0.14,
      len: 10 + (i % 3) * 4,
    }));
    state.r3f.ready = true;

    const elevMin = 0.45;
    const elevMax = 2.45;
    const clampElev = (m) => Math.max(elevMin, Math.min(elevMax, m));

    const drawFrame = (timeMs) => {
      const drawer = $("#hydro-details-drawer");
      if (state.mode !== "weather" || document.hidden || (drawer && !drawer.open)) {
        setTimeout(() => { state.r3f.rafId = requestAnimationFrame(drawFrame); }, 240);
        return;
      }

      const r3 = state.r3f;
      const dElev = r3.targetElevM - r3.curElevM;
      const dTide = r3.targetTideM - r3.curTideM;
      r3.curElevM = Math.abs(dElev) < 0.004 ? r3.targetElevM : r3.curElevM + dElev * 0.18;
      r3.curTideM = Math.abs(dTide) < 0.004 ? r3.targetTideM : r3.curTideM + dTide * 0.18;

      const t = timeMs * 0.001;
      const isNocturne = state.mood === "nocturne";

      const inkPrimary = isNocturne ? "#F8FAFC" : "#0F172A";
      const inkSec = isNocturne ? "rgba(203, 213, 225, 0.86)" : "rgba(71, 85, 105, 0.86)";
      const inkMuted = isNocturne ? "rgba(148, 163, 184, 0.82)" : "rgba(100, 116, 139, 0.84)";
      const hairline = isNocturne ? "rgba(148, 163, 184, 0.16)" : "rgba(15, 23, 42, 0.10)";
      const hatchStroke = isNocturne ? "rgba(148, 163, 184, 0.16)" : "rgba(15, 23, 42, 0.10)";
      const earthFill = isNocturne ? "rgba(30, 41, 59, 0.56)" : "rgba(226, 232, 240, 0.62)";
      const paperSolid = isNocturne ? "#131C2E" : "#FFFFFF";
      const terracotta = isNocturne ? "#38BDF8" : "#0284C7";
      const sunCol = isNocturne ? "#FBBF24" : "#F59E0B";
      const riverJade = isNocturne ? "#38BDF8" : "#0284C7";
      const amberCol = isNocturne ? "#FBBF24" : "#D97706";
      const dangerCol = isNocturne ? "#F87171" : "#DC2626";

      const padL = 8;
      const padR = 46;
      const plotW = Math.max(220, cw - padL - padR);
      const staffX = padL + plotW;

      const skyTopY = 14;
      const datumTopY = Math.round(ch * 0.41);
      const datumBotY = Math.round(ch - 20);
      const elevToY = (m) => {
        const norm = (clampElev(m) - elevMin) / (elevMax - elevMin);
        return datumBotY - norm * (datumBotY - datumTopY);
      };
      const yToElev = (py) => {
        const norm = (datumBotY - py) / Math.max(1, datumBotY - datumTopY);
        return clampElev(elevMin + norm * (elevMax - elevMin));
      };

      const terraceEndX = padL + Math.round(plotW * 0.43);
      const slopeFootX = terraceEndX + 28;
      const streetY = elevToY(r3.curElevM);
      const waterY = elevToY(r3.curTideM);

      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, cw, ch);

      // 1. Subtle Vertical Survey Gridlines
      ctx.strokeStyle = hairline;
      ctx.lineWidth = 1;
      for (let i = 1; i <= 4; i++) {
        const gx = Math.round(padL + (plotW * i) / 5) + 0.5;
        ctx.beginPath();
        ctx.moveTo(gx, skyTopY);
        ctx.lineTo(gx, datumBotY);
        ctx.stroke();
      }

      // 2. Sky & Astronomy Stratum (Celestial Meridian Arc + Sun/Moon + Engraved Clouds)
      const arcStartX = padL + 22;
      const arcEndX = staffX - 24;
      const arcMidX = (arcStartX + arcEndX) / 2;
      const arcBaseY = datumTopY - 12;
      const arcPeakY = skyTopY + 14;

      ctx.save();
      ctx.setLineDash([3, 4]);
      ctx.strokeStyle = hairline;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(arcStartX, arcBaseY);
      ctx.quadraticCurveTo(arcMidX, arcPeakY - 26, arcEndX, arcBaseY);
      ctx.stroke();
      ctx.restore();

      const vnNow = new Date();
      const vnHour = Number(
        new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", hour12: false }).format(vnNow)
      );
      const vnMin = Number(
        new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Ho_Chi_Minh", minute: "2-digit" }).format(vnNow)
      );
      const decHour = vnHour + vnMin / 60;
      let orbU = 0.72;
      if (r3.isDay) {
        orbU = Math.max(0.12, Math.min(0.88, (decHour - 6) / 12));
      } else {
        const nightH = decHour >= 18 ? decHour - 18 : decHour + 6;
        orbU = Math.max(0.14, Math.min(0.86, nightH / 12));
      }
      // Evaluate quadratic Bezier point at orbU
      const omt = 1 - orbU;
      const orbX = omt * omt * arcStartX + 2 * omt * orbU * arcMidX + orbU * orbU * arcEndX;
      const orbY = omt * omt * arcBaseY + 2 * omt * orbU * (arcPeakY - 26) + orbU * orbU * arcBaseY + Math.sin(t * 0.9) * 1.5;

      // Celestial disc
      ctx.save();
      if (r3.isDay) {
        const sunGlow = ctx.createRadialGradient(orbX, orbY, 4, orbX, orbY, 34);
        sunGlow.addColorStop(0, "rgba(245, 158, 11, 0.30)");
        sunGlow.addColorStop(1, "rgba(245, 158, 11, 0)");
        ctx.fillStyle = sunGlow;
        ctx.beginPath();
        ctx.arc(orbX, orbY, 34, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = sunCol;
        ctx.beginPath();
        ctx.arc(orbX, orbY, 13, 0, Math.PI * 2);
        ctx.fill();

        ctx.strokeStyle = sunCol;
        ctx.lineWidth = 1.2;
        for (let k = 0; k < 8; k++) {
          const ang = (k * Math.PI) / 4 + t * 0.06;
          ctx.beginPath();
          ctx.moveTo(orbX + Math.cos(ang) * 16.5, orbY + Math.sin(ang) * 16.5);
          ctx.lineTo(orbX + Math.cos(ang) * 20.5, orbY + Math.sin(ang) * 20.5);
          ctx.stroke();
        }
      } else {
        const moonGlow = ctx.createRadialGradient(orbX, orbY, 4, orbX, orbY, 34);
        moonGlow.addColorStop(0, isNocturne ? "rgba(56, 189, 248, 0.24)" : "rgba(2, 132, 199, 0.16)");
        moonGlow.addColorStop(1, "rgba(56, 189, 248, 0)");
        ctx.fillStyle = moonGlow;
        ctx.beginPath();
        ctx.arc(orbX, orbY, 34, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = isNocturne ? "#F8FAFC" : "#E2E8F0";
        ctx.strokeStyle = inkSec;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.arc(orbX, orbY, 12.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();

        // Phase shadow crescent if < 88% illuminated
        if (r3.moonPct < 88) {
          ctx.fillStyle = isNocturne ? "#0B1120" : "#F3F6FA";
          ctx.beginPath();
          ctx.arc(orbX + 5.5, orbY - 1.5, 10.5, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.font = '600 11px "Be Vietnam Pro", system-ui, sans-serif';
      ctx.fillStyle = inkMuted;
      ctx.textAlign = "center";
      ctx.fillText(r3.isDay ? "Mặt trời" : `Trăng ${r3.lunarDay} ÂL`, orbX, Math.min(datumTopY - 18, orbY + 28));
      ctx.restore();

      // Engraved layered atmospheric cloud contours
      const driftSpeed = 4 + Math.min(25, r3.windKmh) * 0.45;
      const cloudShift = Math.sin(t * 0.22 * (driftSpeed / 8)) * 14;
      const cloudOpacity = Math.max(0.18, Math.min(0.85, (r3.cloudPct || 35) / 100));
      const drawEngravedCloud = (bx, by, wScale, alphaMult) => {
        ctx.save();
        ctx.translate(bx, by);
        ctx.beginPath();
        ctx.moveTo(-46 * wScale, 8);
        ctx.bezierCurveTo(-50 * wScale, -6, -26 * wScale, -16, -14 * wScale, -10);
        ctx.bezierCurveTo(-4 * wScale, -24, 24 * wScale, -22, 30 * wScale, -6);
        ctx.bezierCurveTo(48 * wScale, -8, 54 * wScale, 6, 44 * wScale, 8);
        ctx.closePath();

        ctx.fillStyle = isNocturne
          ? `rgba(148, 163, 184, ${(0.12 * alphaMult).toFixed(3)})`
          : `rgba(148, 163, 184, ${(0.14 * alphaMult).toFixed(3)})`;
        ctx.fill();
        ctx.strokeStyle = isNocturne
          ? `rgba(203, 213, 225, ${(0.34 * alphaMult).toFixed(3)})`
          : `rgba(100, 116, 139, ${(0.32 * alphaMult).toFixed(3)})`;
        ctx.lineWidth = 1.1;
        ctx.stroke();

        // Fine contour line inside cloud
        ctx.strokeStyle = hairline;
        ctx.beginPath();
        ctx.moveTo(-30 * wScale, 3);
        ctx.lineTo(32 * wScale, 3);
        ctx.stroke();
        ctx.restore();
      };

      if (r3.cloudPct >= 15 || r3.isRaining) {
        drawEngravedCloud(padL + plotW * 0.30 + cloudShift, datumTopY * 0.56, 1.05, cloudOpacity + 0.2);
        drawEngravedCloud(padL + plotW * 0.68 - cloudShift * 0.7, datumTopY * 0.44, 0.85, cloudOpacity + 0.15);
      }

      // Diagonal rain strokes when raining
      if (r3.isRaining) {
        ctx.save();
        ctx.strokeStyle = isNocturne ? "rgba(56, 189, 248, 0.62)" : "rgba(2, 132, 199, 0.52)";
        ctx.lineWidth = 1.2;
        r3.drops.forEach((d) => {
          const prog = (t * d.speed + d.phase) % 1;
          const rx = padL + d.xFrac * plotW + prog * 18;
          const ry = datumTopY * 0.55 + prog * (datumBotY - datumTopY * 0.55 - 24);
          ctx.beginPath();
          ctx.moveTo(rx, ry);
          ctx.lineTo(rx - 4, ry + d.len);
          ctx.stroke();
        });
        ctx.restore();
      }

      // 3. Horizontal Metric Alarm Datum Lines Across Channel & Terrace Reference
      const drawDatumRule = (meters, label, color, dashed = true, labelBelow = false) => {
        const yy = Math.round(elevToY(meters)) + 0.5;
        ctx.save();
        if (dashed) ctx.setLineDash([4, 4]);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(terraceEndX - 6, yy);
        ctx.lineTo(staffX, yy);
        ctx.stroke();

        if (label) {
          ctx.font = '600 11px "Be Vietnam Pro", system-ui, sans-serif';
          ctx.fillStyle = color;
          ctx.textAlign = "right";
          ctx.fillText(label, staffX - 6, labelBelow ? yy + 12 : yy - 4);
        }
        ctx.restore();
      };

      const alarmSpanPx = Math.abs(elevToY(2.0) - elevToY(1.8));
      drawDatumRule(2.0, "BĐ III 2.00m", isNocturne ? "rgba(248, 113, 113, 0.72)" : "rgba(220, 38, 38, 0.65)", true, false);
      if (alarmSpanPx >= 34) {
        drawDatumRule(1.9, "", isNocturne ? "rgba(251, 191, 36, 0.35)" : "rgba(217, 119, 6, 0.35)", true, false);
      }
      drawDatumRule(1.8, "BĐ I 1.80m", isNocturne ? "rgba(251, 191, 36, 0.72)" : "rgba(217, 119, 6, 0.65)", true, alarmSpanPx < 28);

      // 4. Urban Bench & Riverbank Quay Embankment Section (Left Half)
      const bedY = elevToY(0.82);
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(padL, datumBotY + 6);
      ctx.lineTo(padL, streetY);
      ctx.lineTo(terraceEndX, streetY);
      ctx.lineTo(slopeFootX, bedY);
      ctx.lineTo(staffX, datumBotY + 6);
      ctx.closePath();

      ctx.fillStyle = earthFill;
      ctx.fill();

      // Diagonal 45-deg architectural hatching clipped inside embankment section
      ctx.save();
      ctx.clip();
      ctx.strokeStyle = hatchStroke;
      ctx.lineWidth = 1;
      for (let hx = padL - 220; hx < staffX + 220; hx += 10) {
        ctx.beginPath();
        ctx.moveTo(hx, datumBotY + 8);
        ctx.lineTo(hx + 180, datumBotY + 8 - 180);
        ctx.stroke();
      }
      ctx.restore();

      // Crisp embankment structural boundary
      ctx.strokeStyle = inkPrimary;
      ctx.lineWidth = 1.8;
      ctx.beginPath();
      ctx.moveTo(padL, streetY);
      ctx.lineTo(terraceEndX, streetY);
      ctx.lineTo(slopeFootX, bedY);
      ctx.lineTo(staffX, datumBotY + 4);
      ctx.stroke();

      // Subtle double pavement rule & miniature architectural wharf house silhouette on terrace
      ctx.strokeStyle = hairline;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, streetY + 5);
      ctx.lineTo(terraceEndX - 3, streetY + 5);
      ctx.stroke();

      // Architectural low-rise skyline & riverside lamppost resting on streetY
      const bX = padL + 16;
      ctx.strokeStyle = inkSec;
      ctx.lineWidth = 1.15;
      ctx.beginPath();
      // Left heritage shophouse roofline
      ctx.moveTo(bX, streetY);
      ctx.lineTo(bX, streetY - 22);
      ctx.lineTo(bX + 16, streetY - 32);
      ctx.lineTo(bX + 32, streetY - 22);
      ctx.lineTo(bX + 32, streetY);
      // Wharf railing & bollard near bank edge
      ctx.moveTo(terraceEndX - 18, streetY);
      ctx.lineTo(terraceEndX - 18, streetY - 8);
      ctx.moveTo(terraceEndX - 6, streetY);
      ctx.lineTo(terraceEndX - 6, streetY - 10);
      ctx.moveTo(terraceEndX - 24, streetY - 7);
      ctx.lineTo(terraceEndX - 4, streetY - 7);
      ctx.stroke();

      // Street Datum Callout Marker & Label inside ground block
      ctx.fillStyle = terracotta;
      ctx.beginPath();
      ctx.moveTo(terraceEndX - 34, streetY - 1);
      ctx.lineTo(terraceEndX - 39, streetY - 8);
      ctx.lineTo(terraceEndX - 29, streetY - 8);
      ctx.closePath();
      ctx.fill();

      ctx.font = '600 11px "Be Vietnam Pro", system-ui, sans-serif';
      ctx.fillStyle = inkPrimary;
      ctx.textAlign = "left";
      const labelY = Math.min(datumBotY - 12, streetY + 19);
      const distLabel = r3.districtName || "Cần Thơ";
      if (terraceEndX - padL < 165) {
        ctx.fillText(distLabel, padL + 8, labelY);
        ctx.fillText(`Cốt +${r3.curElevM.toFixed(2)}m`, padL + 8, Math.min(datumBotY - 3, labelY + 14));
      } else {
        ctx.fillText(`Cốt nền ${distLabel} +${r3.curElevM.toFixed(2)}m`, padL + 10, labelY);
      }
      ctx.restore();

      // Only show flood film over street when actual flood depth >= 8cm or tide >= BĐ I (1.80m)
      const hasFlooding = (r3.maxDepthCm || 0) >= 8 || r3.curTideM >= 1.80;
      const simulatedFloodDepthM = hasFlooding
        ? Math.max((r3.maxDepthCm || 0) / 100, Math.max(0, r3.curTideM - r3.curElevM))
        : 0;
      if (simulatedFloodDepthM >= 0.08) {
        const floodPx = Math.min(26, Math.max(4, simulatedFloodDepthM * 36));
        ctx.save();
        ctx.fillStyle = simulatedFloodDepthM >= 0.18
          ? (isNocturne ? "rgba(248, 113, 113, 0.36)" : "rgba(220, 38, 38, 0.25)")
          : (isNocturne ? "rgba(251, 191, 36, 0.34)" : "rgba(217, 119, 6, 0.24)");
        ctx.fillRect(padL, streetY - floodPx, terraceEndX - padL, floodPx);
        ctx.strokeStyle = simulatedFloodDepthM >= 0.18 ? dangerCol : amberCol;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.moveTo(padL, streetY - floodPx);
        ctx.lineTo(terraceEndX, streetY - floodPx);
        ctx.stroke();

        ctx.font = '600 11px "Be Vietnam Pro", system-ui, sans-serif';
        ctx.fillStyle = simulatedFloodDepthM >= 0.18 ? dangerCol : amberCol;
        ctx.textAlign = "left";
        ctx.fillText(`Ngập +${Math.round(simulatedFloodDepthM * 100)} cm`, bX + 42, streetY - floodPx - 4);
        ctx.restore();
      }

      // 5. Hau River Channel Wave & Dynamic Tide Readout (Right Half)
      let riverStroke = riverJade;
      let riverTopRGBA = isNocturne ? "rgba(56, 189, 248, 0.34)" : "rgba(2, 132, 199, 0.24)";
      let riverBotRGBA = isNocturne ? "rgba(56, 189, 248, 0.06)" : "rgba(2, 132, 199, 0.04)";
      if (r3.floodTier === "warn" || r3.curTideM >= 1.8) {
        riverStroke = amberCol;
        riverTopRGBA = isNocturne ? "rgba(251, 191, 36, 0.34)" : "rgba(217, 119, 6, 0.24)";
      }
      if (r3.floodTier === "danger" || r3.curTideM >= 2.0) {
        riverStroke = dangerCol;
        riverTopRGBA = isNocturne ? "rgba(248, 113, 113, 0.38)" : "rgba(220, 38, 38, 0.28)";
      }

      // Compute exact shoreline x-intercept on bank slope (between terraceEndX..slopeFootX)
      const slopeRatio = Math.max(0, Math.min(1, (waterY - streetY) / Math.max(1, bedY - streetY)));
      const shoreX = waterY <= streetY ? terraceEndX : terraceEndX + slopeRatio * (slopeFootX - terraceEndX);

      ctx.save();
      const wg = ctx.createLinearGradient(0, waterY, 0, datumBotY + 6);
      wg.addColorStop(0, riverTopRGBA);
      wg.addColorStop(1, riverBotRGBA);

      ctx.beginPath();
      ctx.moveTo(shoreX, waterY);
      const waveSpan = Math.max(20, staffX - shoreX);
      for (let wx = shoreX; wx <= staffX; wx += 4) {
        const u = (wx - shoreX) / waveSpan;
        const ripple = Math.sin(u * 9 - t * 2.1) * 2.1 + Math.cos(u * 15 + t * 1.4) * 1.0;
        ctx.lineTo(wx, waterY + ripple);
      }
      ctx.lineTo(staffX, datumBotY + 6);
      ctx.lineTo(slopeFootX, datumBotY + 6);
      ctx.lineTo(shoreX, waterY);
      ctx.closePath();
      ctx.fillStyle = wg;
      ctx.fill();

      // Surface crest stroke
      ctx.beginPath();
      for (let wx = shoreX; wx <= staffX; wx += 4) {
        const u = (wx - shoreX) / waveSpan;
        const ripple = Math.sin(u * 9 - t * 2.1) * 2.1 + Math.cos(u * 15 + t * 1.4) * 1.0;
        if (wx === shoreX) ctx.moveTo(wx, waterY + ripple);
        else ctx.lineTo(wx, waterY + ripple);
      }
      ctx.strokeStyle = riverStroke;
      ctx.lineWidth = 2;
      ctx.stroke();

      // Subtle sub-surface flow dashes
      ctx.strokeStyle = riverStroke;
      ctx.globalAlpha = 0.32;
      ctx.lineWidth = 1;
      for (let row = 0; row < 2; row++) {
        const ly = waterY + 14 + row * 14;
        if (ly >= datumBotY - 6) continue;
        const lx1 = shoreX + 26 + ((t * 16 + row * 40) % 45);
        ctx.beginPath();
        ctx.moveTo(lx1, ly);
        ctx.lineTo(Math.min(staffX - 12, lx1 + 48), ly);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      // Crisp River Level Tag positioned neatly above water wave
      const tideTrend = r3.tideDelta >= 0.02 ? "Lên" : r3.tideDelta <= -0.02 ? "Ròng" : "Đứng";
      const isNarrowPlot = cw < 420;
      const riverBadgeTxt = isNarrowPlot
        ? `Triều ${r3.curTideM.toFixed(2)}m · ${tideTrend}`
        : `Sông Hậu ${r3.curTideM.toFixed(2)}m · ${tideTrend}`;
      const badgeW = isNarrowPlot ? Math.min(138, Math.max(106, waveSpan - 10)) : 164;
      const badgeX = Math.max(shoreX + 6, Math.min(staffX - badgeW - 6, shoreX + waveSpan * 0.45 - badgeW / 2));
      const badgeY = Math.max(datumTopY - 22, waterY - 24);
      ctx.fillStyle = paperSolid;
      ctx.strokeStyle = riverStroke;
      ctx.lineWidth = 1.2;
      ctx.fillRect(badgeX, badgeY, badgeW, 20);
      ctx.strokeRect(badgeX, badgeY, badgeW, 20);

      ctx.font = `600 ${isNarrowPlot ? 10.5 : 11}px "Be Vietnam Pro", system-ui, sans-serif`;
      ctx.fillStyle = riverStroke;
      ctx.textAlign = "center";
      ctx.fillText(riverBadgeTxt, badgeX + badgeW / 2, badgeY + 13.5);
      ctx.restore();

      // 6. Vertical Graduated Metric Staff Gauge (Right Edge)
      ctx.save();
      ctx.strokeStyle = inkPrimary;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(staffX, datumTopY);
      ctx.lineTo(staffX, datumBotY);
      ctx.stroke();

      ctx.font = '500 11px "Be Vietnam Pro", system-ui, sans-serif';
      ctx.textAlign = "left";
      for (let tick = 0.6; tick <= 2.25; tick += 0.2) {
        const ty = Math.round(elevToY(tick)) + 0.5;
        const isMajor = Math.abs(Math.round(tick * 10) % 4 - 2) < 0.01 || Math.abs(tick - 1.0) < 0.01 || Math.abs(tick - 1.8) < 0.01;
        ctx.strokeStyle = isMajor ? inkSec : hairline;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(staffX, ty);
        ctx.lineTo(staffX + (isMajor ? 6 : 3.5), ty);
        ctx.stroke();
        if (isMajor) {
          ctx.fillStyle = inkMuted;
          ctx.fillText(`${tick.toFixed(1)}m`, staffX + 8, ty + 3.5);
        }
      }
      ctx.restore();

      // 7. Interactive Hover Elevation Crosshair
      if (r3.hoverY !== null && r3.hoverY >= datumTopY - 6 && r3.hoverY <= datumBotY + 4) {
        const hElev = yToElev(r3.hoverY);
        const hy = Math.round(r3.hoverY) + 0.5;
        ctx.save();
        ctx.setLineDash([2, 3]);
        ctx.strokeStyle = terracotta;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padL, hy);
        ctx.lineTo(staffX, hy);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = terracotta;
        ctx.fillRect(staffX + 2, hy - 9, 44, 18);
        ctx.font = '600 10.5px "Be Vietnam Pro", system-ui, sans-serif';
        ctx.fillStyle = isNocturne ? "#0B1120" : "#FFFFFF";
        ctx.textAlign = "center";
        ctx.fillText(`+${hElev.toFixed(2)}`, staffX + 24, hy + 3.5);
        ctx.restore();
      }

      ctx.restore();
      r3.rafId = requestAnimationFrame(drawFrame);
    };
    state.r3f.rafId = requestAnimationFrame(drawFrame);
  }

  function updateThreeWeatherState(weatherPayload, floodSevereCount, floodWarnCount, maxDepthCm) {
    const r = state.r3f;
    if (!r) return;

    if (weatherPayload?.current) {
      const cur = weatherPayload.current;
      r.isDay = Number(cur.is_day ?? 1) === 1;
      r.targetElevM = Number(cur.elevation_m ?? r.targetElevM ?? 1.35);
      r.districtName = cur.short_name || cur.name || "Cần Thơ";
      r.cloudPct = Number(cur.cloud_cover_pct ?? 40);
      r.windKmh = Number(cur.wind_speed_kmh ?? 6);
      const cat = String(cur.weather_category || "").toLowerCase();
      r.isRaining =
        Number(cur.precip_mm ?? 0) > 0.05 ||
        cat.includes("rain") ||
        cat.includes("storm") ||
        cat.includes("drizzle");
    }

    if (weatherPayload?.astronomy_and_tide) {
      const astro = weatherPayload.astronomy_and_tide;
      r.targetTideM = Number(astro.current_tide_m ?? r.targetTideM ?? 1.41);
      r.tideDelta = Number(astro.current_tide_delta_m_h ?? 0);
      r.lunarDay = Number(astro.lunar_day_approx ?? 15);
      r.moonPct = Number(astro.moon_illumination_pct ?? 100);
    }

    if (typeof maxDepthCm === "number") {
      r.maxDepthCm = maxDepthCm;
    }

    if (typeof floodSevereCount === "number" || typeof floodWarnCount === "number") {
      r.floodTier = (floodSevereCount || 0) > 0 ? "danger" : (floodWarnCount || 0) > 0 ? "warn" : "safe";
    }

    const readout = $("#cross-section-readout");
    if (readout) {
      const diff = Number(r.targetElevM) - Number(r.targetTideM);
      const isFlooding = (r.maxDepthCm || 0) >= 8 || Number(r.targetTideM) >= 1.80;
      const clearance = diff >= 0
        ? `Cao hơn sông +${diff.toFixed(2)}m`
        : (isFlooding
            ? `Nước tràn mặt đường +${Math.abs(diff).toFixed(2)}m`
            : `Cao hơn cốt đất thấp +${Math.abs(diff).toFixed(2)}m (đã có đê bao)`);
      readout.textContent = `Cốt +${Number(r.targetElevM).toFixed(2)}m · Triều ${Number(r.targetTideM).toFixed(2)}m · ${clearance}`;
    }
  }

  /* --------------------------------------------------------------------------
     Live Vietnam Clock
     -------------------------------------------------------------------------- */
  function startVietnamClock() {
    const timeFmt = new Intl.DateTimeFormat("vi-VN", {
      timeZone: "Asia/Ho_Chi_Minh",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const tick = () => {
      const clockEl = $("#vn-live-clock");
      if (clockEl) clockEl.textContent = timeFmt.format(new Date());
    };
    tick();
    setInterval(tick, 10000);
  }

  /* --------------------------------------------------------------------------
     Minimal Silky Bezier Curve for Card 1
     -------------------------------------------------------------------------- */
  function buildSmoothPath(points) {
    if (!points.length) return "";
    if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
    let d = `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
    for (let i = 0; i < points.length - 1; i++) {
      const p0 = points[i === 0 ? i : i - 1];
      const p1 = points[i];
      const p2 = points[i + 1];
      const p3 = points[i + 2 < points.length ? i + 2 : i + 1];
      const cp1x = p1.x + (p2.x - p0.x) / 6;
      const cp1y = p1.y + (p2.y - p0.y) / 6;
      const cp2x = p2.x - (p3.x - p1.x) / 6;
      const cp2y = p2.y - (p3.y - p1.y) / 6;
      d += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
    }
    return d;
  }

  function renderHourlyChartSVG(hourly) {
    const svg = $("#hourly-chart-svg");
    if (!svg || !hourly || !hourly.length) return;
    const slice = hourly.slice(0, 11);
    const W = 560;
    const H = 96;
    const padX = 24;
    const padTop = 20;
    const padBottom = 20;
    const plotW = W - padX * 2;
    const plotH = H - padTop - padBottom;

    const isNocturne = state.mood === "nocturne";
    const lineInk = isNocturne ? "#38BDF8" : "#0284C7";
    const textInk = isNocturne ? "#F8FAFC" : "#0F172A";
    const mutedInk = isNocturne ? "#94A3B8" : "#64748B";
    const rainInk = isNocturne ? "rgba(56, 189, 248, 0.30)" : "rgba(2, 132, 199, 0.22)";

    const temps = slice.map((h) => Number(h.temp_c || 28));
    const minT = Math.floor(Math.min(...temps) - 1);
    const maxT = Math.ceil(Math.max(...temps) + 1);
    const spanT = Math.max(2, maxT - minT);

    const pts = slice.map((h, idx) => {
      const x = padX + (idx / Math.max(1, slice.length - 1)) * plotW;
      const y = padTop + (1 - (h.temp_c - minT) / spanT) * plotH;
      return { x, y, item: h, idx };
    });

    const lineD = buildSmoothPath(pts);
    const areaD = `${lineD} L ${pts[pts.length - 1].x.toFixed(1)} ${(H - padBottom).toFixed(1)} L ${pts[0].x.toFixed(1)} ${(H - padBottom).toFixed(1)} Z`;
    const areaId = nextUid("hrArea");

    const rainBars = pts
      .map((p) => {
        const prob = Number(p.item.precip_prob_pct ?? 0);
        if (prob < 15) return "";
        const bh = Math.max(3, (prob / 100) * 18);
        return `<rect x="${(p.x - 4).toFixed(1)}" y="${(H - padBottom - bh).toFixed(1)}" width="8" height="${bh.toFixed(1)}" rx="2" fill="${rainInk}"/>`;
      })
      .join("");

    const labels = pts
      .filter((p) => p.idx % 2 === 0 || p.idx === pts.length - 1)
      .map((p, i) => {
        const hrLbl = p.idx === 0 ? "Nay" : String(p.item.hour_label || "").slice(0, 2) + "h";
        return `
        <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${i === 0 ? 4 : 2.8}" fill="${lineInk}"/>
        <text x="${p.x.toFixed(1)}" y="${(p.y - 9).toFixed(1)}" text-anchor="middle" fill="${textInk}" font-family="Be Vietnam Pro, system-ui, sans-serif" font-size="12" font-weight="600">${Math.round(p.item.temp_c)}°</text>
        <text x="${p.x.toFixed(1)}" y="${H - 4}" text-anchor="middle" fill="${mutedInk}" font-family="Be Vietnam Pro, system-ui, sans-serif" font-size="11">${escapeHtml(hrLbl)}</text>
      `;
      })
      .join("");

    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = `
      <defs>
        <linearGradient id="${areaId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${lineInk}" stop-opacity="0.18"/>
          <stop offset="100%" stop-color="${lineInk}" stop-opacity="0.01"/>
        </linearGradient>
      </defs>
      ${rainBars}
      <path d="${areaD}" fill="url(#${areaId})"/>
      <path d="${lineD}" fill="none" stroke="${lineInk}" stroke-width="2.2" stroke-linecap="round"/>
      ${labels}
    `;
  }

  function renderHydrographSVG(points) {
    const svg = $("#hydrograph-svg");
    if (!svg || !points.length) return;
    const W = 560;
    const H = 100;
    const padX = 32;
    const padTop = 14;
    const padBot = 20;
    const plotW = W - padX * 2;
    const plotH = H - padTop - padBot;

    const isNocturne = state.mood === "nocturne";
    const lineInk = isNocturne ? "#38BDF8" : "#0284C7";
    const textInk = isNocturne ? "#94A3B8" : "#64748B";
    const alarmInk = isNocturne ? "rgba(248, 113, 113, 0.85)" : "rgba(220, 38, 38, 0.78)";

    const maxTide = 2.5;
    const minTide = 0.4;
    const coords = points.map((pt, i) => {
      const x = padX + (i / Math.max(1, points.length - 1)) * plotW;
      const y = padTop + (1 - (Number(pt.tide_level_m) - minTide) / (maxTide - minTide)) * plotH;
      return { x, y, pt };
    });

    const alarmY = padTop + (1 - (1.8 - minTide) / (maxTide - minTide)) * plotH;
    const lineD = buildSmoothPath(coords);
    const areaD = `${lineD} L ${coords[coords.length - 1].x.toFixed(1)} ${(H - padBot).toFixed(1)} L ${coords[0].x.toFixed(1)} ${(H - padBot).toFixed(1)} Z`;
    const gid = nextUid("hydroArea");

    const ticks = coords
      .filter((_, idx) => idx % 4 === 0 || idx === coords.length - 1)
      .map(
        (c) => `<text x="${c.x.toFixed(1)}" y="${H - 4}" text-anchor="middle" fill="${textInk}" font-family="Be Vietnam Pro, system-ui, sans-serif" font-size="11">${String(c.pt.hour).padStart(2, "0")}h</text>`
      )
      .join("");

    svg.innerHTML = `
      <defs>
        <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${lineInk}" stop-opacity="0.22"/>
          <stop offset="100%" stop-color="${lineInk}" stop-opacity="0.02"/>
        </linearGradient>
      </defs>
      <text x="2" y="${padTop + 4}" fill="${textInk}" font-family="Be Vietnam Pro, system-ui, sans-serif" font-size="10">2.5m</text>
      <text x="2" y="${H - padBot}" fill="${textInk}" font-family="Be Vietnam Pro, system-ui, sans-serif" font-size="10">0.4m</text>
      <line x1="${padX}" y1="${alarmY.toFixed(1)}" x2="${W - padX}" y2="${alarmY.toFixed(1)}" stroke="${alarmInk}" stroke-dasharray="4 4" stroke-width="1.2"/>
      <text x="${W - padX}" y="${(alarmY - 3).toFixed(1)}" text-anchor="end" fill="${alarmInk}" font-family="Be Vietnam Pro, system-ui, sans-serif" font-size="10.5" font-weight="600">BĐ I 1.8m</text>
      <path d="${areaD}" fill="url(#${gid})"/>
      <path d="${lineD}" fill="none" stroke="${lineInk}" stroke-width="2" stroke-linecap="round"/>
      ${ticks}
    `;
  }

  /* --------------------------------------------------------------------------
     Live Weather & Truthful Live Flood Evaluation
     -------------------------------------------------------------------------- */
  async function loadLiveWeather(districtId = state.selectedDistrictId, forceRefresh = false) {
    const refreshBtn = $("#btn-refresh-live");
    if (forceRefresh && refreshBtn) refreshBtn.classList.add("spinning");

    try {
      const qs = new URLSearchParams({ district: districtId, refresh: String(forceRefresh) });
      const res = await fetch(`/api/weather/live?${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload = await res.json();
      state.weatherData = payload;
      state.selectedDistrictId = payload.selected_district_id || districtId;

      renderLiveWeather(payload);
      await evaluateLiveFloodTruth(payload);

      if (forceRefresh) {
        showToast(payload.refresh_throttled ? "Dữ liệu vừa cập nhật, thử lại sau ít phút." : "Đã làm mới dữ liệu Cần Thơ.");
      }
    } catch (err) {
      showToast(`Không thể cập nhật (${err.message}).`);
    } finally {
      if (refreshBtn) refreshBtn.classList.remove("spinning");
    }
  }

  async function evaluateLiveFloodTruth(weatherPayload, opts = {}) {
    const cur = weatherPayload.current || {};
    const astro = weatherPayload.astronomy_and_tide || {};
    const rec = (weatherPayload.auto_flood_mode || {}).recommended_inputs || {};

    const tideM = Number(astro.current_tide_m ?? rec.tide_level_m ?? 1.45);
    const rainMm = Number(cur.precip_mm ?? rec.rain_mm_h ?? 0);
    const rain3h = Number(rec.rain_3h_cum ?? roundOne(rainMm * 2.0));
    const delta = Number(astro.current_tide_delta_m_h ?? 0);

    try {
      const res = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tide_level_m: tideM,
          rain_mm_h: rainMm,
          rain_3h_cum: rain3h,
          tide_delta_m: Math.max(-0.35, Math.min(0.45, delta)),
          is_spring_tide: Boolean(astro.is_spring_tide_window),
          horizon: "now",
          model_key: "hgbr",
          include_community: !state.isSimulationOverride,
        }),
      });
      if (!res.ok) return;
      const simJson = await res.json();
      state.liveSimData = simJson;

      renderLiveFloodHeroCard(simJson, weatherPayload);

      if (!state.isSimulationOverride) {
        state.activeSimData = simJson;
        applyLiveInputsToSliders(tideM, rainMm, rain3h, delta, Boolean(astro.is_spring_tide_window));
        applyActiveSimulationToUI(simJson);
        if (state.mode === "flood") {
          const prevBanner = $("#route-banner")?.textContent || "";
          const prevSafe = $("#route-safe-dist")?.textContent || "";
          await runFloodRoute();
          if (
            opts.fromRealtime &&
            prevSafe &&
            !prevSafe.startsWith("--") &&
            (($("#route-banner")?.textContent || "") !== prevBanner || ($("#route-safe-dist")?.textContent || "") !== prevSafe)
          ) {
            showToast("Vừa có cập nhật điểm ngập mới — đã tự động cập nhật bản đồ và lộ trình.");
          }
        }
      }
    } catch (err) {
      console.warn("Live flood check error:", err);
    }
  }

  function applyLiveInputsToSliders(tideM, rainMm, rain3h, delta, isSpring) {
    if ($("#slider-tide")) $("#slider-tide").value = tideM.toFixed(2);
    if ($("#slider-rain")) $("#slider-rain").value = Math.round(rainMm);
    if ($("#slider-rain3h")) $("#slider-rain3h").value = Math.round(rain3h);
    if ($("#slider-delta")) $("#slider-delta").value = Math.max(-0.35, Math.min(0.45, delta)).toFixed(2);
    if ($("#chk-spring")) $("#chk-spring").checked = Boolean(isSpring);
    updateSliderLabels();
  }

  function formatVnTimeFromIso(isoStr) {
    if (isoStr && typeof isoStr === "string") {
      const m = isoStr.match(/T(\d{2}:\d{2})/);
      if (m) return m[1];
    }
    return new Intl.DateTimeFormat("vi-VN", {
      timeZone: "Asia/Ho_Chi_Minh",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date());
  }

  function renderLiveFloodHeroCard(simJson, weatherPayload) {
    const card = $("#live-flood-card");
    if (!card) return;

    const pred = simJson.prediction || {};
    const roads = pred.roads || [];
    const cur = weatherPayload.current || {};
    const astro = weatherPayload.astronomy_and_tide || {};
    const tideM = Number(astro.current_tide_m ?? simJson.inputs?.tide_level_m ?? 1.4);
    const rainMm = Number(cur.precip_mm ?? simJson.inputs?.rain_mm_h ?? 0);

    const severeRoads = roads.filter((r) => Number(r.depth_cm) >= 18.0).sort((a, b) => b.depth_cm - a.depth_cm);
    const warnRoads = roads.filter((r) => Number(r.depth_cm) >= 8.0 && Number(r.depth_cm) < 18.0).sort((a, b) => b.depth_cm - a.depth_cm);
    const allFlooded = [...severeRoads, ...warnRoads];

    const nowStr = formatVnTimeFromIso(weatherPayload.fetched_at);

    if ($("#live-flood-source-tag")) $("#live-flood-source-tag").textContent = `Cập nhật ${nowStr}`;
    if ($("#live-flood-tide-tag")) $("#live-flood-tide-tag").textContent = `${tideM.toFixed(2)}m · ${astro.current_alarm_status || "Bình thường"}`;

    card.classList.remove("flood-state-safe", "flood-state-warn", "flood-state-danger");
    const hotspotsEl = $("#live-flood-hotspots");
    const navDot = $("#badge-auto-flood-alert");
    const statusTagEl = $("#live-flood-status-tag");

    if (allFlooded.length === 0) {
      card.classList.add("flood-state-safe");
      if (statusTagEl) statusTagEl.textContent = "MẶT ĐƯỜNG THÔNG THOÁNG";
      $("#live-flood-icon").innerHTML = UI_ICONS.shieldCheck;
      $("#live-flood-headline").textContent = "Hiện tại Cần Thơ khô ráo, không ngập";
      $("#live-flood-sub").textContent = `Mưa ${rainMm.toFixed(1)} mm/h · Triều Sông Hậu ${tideM.toFixed(2)}m`;
      if ($("#portal-road-summary")) $("#portal-road-summary").textContent = "42/42 trục đường đều thông thoáng";
      if ($("#portal-tide-chip")) $("#portal-tide-chip").textContent = "Sẵn sàng tra cứu lộ trình né ngập";
      hotspotsEl.classList.add("hidden");
      hotspotsEl.innerHTML = "";
      navDot?.classList.add("hidden");
      updateThreeWeatherState(weatherPayload, 0, 0, 0);
    } else if (severeRoads.length === 0) {
      const topD = warnRoads[0].depth_cm;
      card.classList.add("flood-state-warn");
      if (statusTagEl) statusTagEl.textContent = "CẢNH BÁO NGẬP CỤC BỘ";
      $("#live-flood-icon").innerHTML = UI_ICONS.alertTriangle;
      $("#live-flood-headline").textContent = `Ngập nhẹ cục bộ ${warnRoads.length} đoạn trũng`;
      $("#live-flood-sub").textContent = `Độ sâu ~${Math.round(topD)} cm · Triều ${tideM.toFixed(2)}m (${astro.current_alarm_status || "Triều lên"})`;
      if ($("#portal-road-summary")) $("#portal-road-summary").textContent = `${warnRoads.length} điểm ngập nhẹ dưới 18 cm.`;
      if ($("#portal-tide-chip")) $("#portal-tide-chip").textContent = "Nhấn để xem chi tiết trên bản đồ";
      renderHotspotChips(hotspotsEl, allFlooded.slice(0, 3));
      navDot?.classList.remove("hidden");
      updateThreeWeatherState(weatherPayload, 0, warnRoads.length, Number(topD));
    } else {
      const topD = severeRoads[0].depth_cm;
      card.classList.add("flood-state-danger");
      if (statusTagEl) statusTagEl.textContent = "CẢNH BÁO NGẬP SÂU";
      $("#live-flood-icon").innerHTML = UI_ICONS.wavesDanger;
      $("#live-flood-headline").textContent = `Đang ngập ${severeRoads.length} đoạn đường (${allFlooded.length} điểm cảnh báo)`;
      $("#live-flood-sub").textContent = `Sâu nhất ${Math.round(topD)} cm · Triều ${tideM.toFixed(2)}m (${astro.current_alarm_status || "Ngập cao"})`;
      if ($("#portal-road-summary")) $("#portal-road-summary").textContent = `${severeRoads.length} đoạn ngập ≥ 18 cm nên tránh.`;
      if ($("#portal-tide-chip")) $("#portal-tide-chip").textContent = "Tìm đường né ngập trực tiếp trên bản đồ";
      renderHotspotChips(hotspotsEl, allFlooded.slice(0, 3));
      navDot?.classList.remove("hidden");
      updateThreeWeatherState(weatherPayload, severeRoads.length, warnRoads.length, Number(topD));
    }
  }

  function renderHotspotChips(container, items) {
    if (!container) return;
    container.classList.remove("hidden");
    container.innerHTML = items
      .map(
        (r) => `
        <button type="button" class="hotspot-chip ${r.depth_cm >= 18 ? "severe" : ""}" data-road-id="${escapeHtml(r.id)}">
          <span>${escapeHtml(shortStreetName(r.name))}</span>
          <span class="chip-depth">${Math.round(r.depth_cm)} cm</span>
        </button>
      `
      )
      .join("");

    $$(".hotspot-chip", container).forEach((btn) => {
      btn.addEventListener("click", () => {
        const road = items.find((x) => x.id === btn.dataset.roadId);
        switchMode("flood");
        setTimeout(() => focusMapOnRoad(road), 80);
      });
    });
  }

  function focusMapOnRoad(road) {
    if (!state.map || !road || !road.coords?.length) return;
    const mid = road.coords[Math.floor(road.coords.length / 2)];
    state.map.stop();
    state.map.setView(mid, 15, { animate: false });
    L.popup()
      .setLatLng(mid)
      .setContent(`<strong>${escapeHtml(road.name)}</strong><br/>Mực nước dự báo: <strong>${Number(road.depth_cm).toFixed(0)} cm</strong>`)
      .openOn(state.map);
  }

  function renderLiveWeather(data) {
    const cur = data.current || {};
    const env = data.environment || {};
    const astro = data.astronomy_and_tide || {};

    const sourcePill = $("#live-source-pill");
    const sourceText = $("#live-source-text");
    const isLive = String(data.source || "").toLowerCase().includes("open-meteo");
    if (sourcePill && sourceText) {
      sourcePill.classList.toggle("stale", !isLive);
      sourceText.textContent = isLive ? "LIVE" : "CACHE";
    }

    const pillsWrap = $("#district-pills");
    if (pillsWrap) {
      pillsWrap.innerHTML = (data.districts || [])
        .map(
          (d) => `
          <button type="button" class="district-pill ${d.id === state.selectedDistrictId ? "active" : ""}" data-district="${escapeHtml(d.id)}" role="tab" aria-selected="${d.id === state.selectedDistrictId}">
            <span class="dist-name">${escapeHtml(d.short_name)}</span>
            <span class="dist-temp">${Math.round(d.temp_c)}°</span>
          </button>
        `
        )
        .join("");

      $$(".district-pill", pillsWrap).forEach((btn) => {
        btn.addEventListener("click", () => {
          btn.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
          loadLiveWeather(btn.dataset.district, false);
        });
      });
      pillsWrap.querySelector(".district-pill.active")?.scrollIntoView({ behavior: "auto", block: "nearest", inline: "nearest" });
    }

    if ($("#hero-elevation")) {
      $("#hero-elevation").textContent = `Cốt nền +${Number(cur.elevation_m ?? 1.35).toFixed(2)}m`;
    }
    $("#hero-district-name").textContent = cur.name || "Quận Ninh Kiều";
    $("#hero-hi-lo").textContent = `Cao ${Math.round(cur.temp_max_today_c ?? cur.temp_c)}° · Thấp ${Math.round(cur.temp_min_today_c ?? cur.temp_c - 5)}°`;
    $("#hero-temp").textContent = `${Math.round(cur.temp_c ?? 28)}°`;
    $("#hero-weather-label").textContent = cur.weather_label || "Có mây";
    $("#hero-feels-like").textContent = `Cảm nhận ${Math.round(cur.feels_like_c ?? cur.temp_c)}° · ${Math.round(cur.pressure_hpa ?? 1012)} hPa`;

    $("#hero-rain-now").textContent = `Mưa ${Number(cur.precip_mm ?? 0).toFixed(1)} mm/h`;
    $("#hero-wind").textContent = `${Math.round(cur.wind_speed_kmh ?? 0)} km/h ${cur.wind_compass || ""}`;
    $("#hero-humidity").textContent = `${cur.humidity_pct ?? 75}%`;

    const tideVal = Number(astro.current_tide_m ?? 1.4);
    const tideBadge = $("#tide-alarm-badge");
    tideBadge.textContent = astro.current_alarm_status || "Bình thường";
    tideBadge.className = `status-tag ${tideVal >= 2.0 ? "badge-danger" : tideVal >= 1.8 ? "badge-warn" : "badge-safe"}`;
    $("#tile-tide-val").textContent = `${tideVal.toFixed(2)} m`;
    const highs = (astro.extremes || []).filter((x) => x.kind === "high");
    const highEx = highs.length
      ? highs.reduce((best, x) => (Number(x.level_m) > Number(best.level_m) ? x : best), highs[0])
      : (astro.extremes || [])[0];
    $("#tile-tide-peak").textContent = highEx
      ? `Đỉnh ngày ${highEx.time} · ${Number(highEx.level_m).toFixed(2)}m (${highEx.alarm || "Bình thường"})`
      : "Nửa nhật triều";
    const qVal = Number(astro.river_discharge_m3s ?? 0);
    if ($("#tile-glofas-discharge")) {
      $("#tile-glofas-discharge").textContent = qVal > 0
        ? `Sông Hậu: ${Math.round(qVal).toLocaleString("vi-VN")} m³/s`
        : "Sông Hậu: -- m³/s";
    }

    const uv = env.uv || {};
    const uvVal = Number(uv.uv_index ?? cur.uv_index ?? 0);
    const uvBadge = $("#uv-level-badge");
    uvBadge.textContent = uv.level || "Thấp";
    uvBadge.className = `status-tag ${uv.badge_class || "badge-safe"}`;
    $("#tile-uv-val").textContent = uvVal.toFixed(1);

    const aqi = env.air_quality || {};
    const aqiBadge = $("#aqi-level-badge");
    let aqiLevelShort = aqi.level || "Tốt";
    if (aqiLevelShort.toLowerCase().includes("nhạy cảm")) aqiLevelShort = "Kém";
    aqiBadge.textContent = aqiLevelShort;
    aqiBadge.className = `status-tag ${aqi.badge_class || "badge-safe"}`;
    $("#tile-aqi-val").textContent = `${Math.round(aqi.us_aqi ?? cur.us_aqi ?? 35)}`;
    $("#tile-aqi-sub").textContent = `PM2.5 ${aqi.pm2_5 ?? cur.pm2_5 ?? "--"} µg/m³`;

    const lunarStr = stripEmoji(astro.lunar_label || "-- ÂL").replace("Ngày ", "").replace(" Âm lịch", " ÂL");
    $("#tile-lunar-val").textContent = lunarStr;
    $("#tile-sun-sub").textContent = `Mặt trời ${astro.sunrise || "05:51"} – ${astro.sunset || "17:31"}`;

    renderHourlyChartSVG(data.hourly_24h || []);
    const stripEl = $("#hourly-strip-list");
    if (stripEl) {
      stripEl.innerHTML = (data.hourly_24h || [])
        .filter((_, idx) => idx % 2 === 0)
        .slice(0, 6)
        .map(
          (h, i) => `
          <div class="hour-cell ${i === 0 ? "now" : ""}">
            <span class="hour-time">${i === 0 ? "Nay" : escapeHtml(h.hour_label)}</span>
            <span class="hour-ico">${getWeatherSVG(h.icon_key)}</span>
            <strong class="hour-temp">${Math.round(h.temp_c)}°</strong>
            <span class="hour-rain">${Number(h.precip_prob_pct ?? 0) >= 15 ? `${Math.round(h.precip_prob_pct)}%` : ""}</span>
          </div>
        `
        )
        .join("");
    }

    const dailyList = $("#daily-7d-list");
    const days = data.daily_7d || [];
    if (dailyList && days.length) {
      const globalMin = Math.min(...days.map((d) => Number(d.temp_min_c)));
      const globalMax = Math.max(...days.map((d) => Number(d.temp_max_c)));
      const span = Math.max(4, globalMax - globalMin);

      dailyList.innerHTML = days
        .slice(0, 7)
        .map((d, idx) => {
          const leftPct = ((d.temp_min_c - globalMin) / span) * 100;
          const widthPct = Math.max(12, ((d.temp_max_c - d.temp_min_c) / span) * 100);
          return `
            <div class="day-row">
              <div>
                <span class="day-name">${idx === 0 ? "Hôm nay" : escapeHtml(d.day_name)}</span>
                <span class="day-date">${escapeHtml(d.date_short)}</span>
              </div>
              <div class="day-ico-rain">
                ${getWeatherSVG(d.icon_key)}
                <span class="day-rain-pct">${d.precip_prob_max_pct > 20 ? `${d.precip_prob_max_pct}%` : ""}</span>
              </div>
              <span class="day-t-min">${Math.round(d.temp_min_c)}°</span>
              <div class="temp-bar-track">
                <div class="temp-bar-fill" style="left: ${leftPct.toFixed(1)}%; width: ${widthPct.toFixed(1)}%;"></div>
              </div>
              <span class="day-t-max">${Math.round(d.temp_max_c)}°</span>
            </div>
          `;
        })
        .join("");
    }

    if ($("#colophon-tide-regime")) {
      $("#colophon-tide-regime").textContent = astro.tidal_regime || "Chế độ bán nhật triều";
    }
    if ($("#colophon-tourism-note")) {
      $("#colophon-tourism-note").textContent = astro.tourism_and_navigation_note || "Theo dõi mực nước Sông Hậu theo thời gian thực.";
    }
    if ($("#colophon-data-source")) {
      const hhmm = formatVnTimeFromIso(data.fetched_at);
      const srcMode = isLive ? "Trực tuyến" : "Bộ nhớ đệm";
      $("#colophon-data-source").textContent = `Nguồn: Open-Meteo · GloFAS · Cộng đồng (${srcMode} ${hhmm})`;
    }

    renderWeatherStationsOnMap(data.districts || []);
    updateThreeWeatherState(data);
  }

  /* --------------------------------------------------------------------------
     Map Initialization, Hybrid Basemaps & Pixel-Space De-Collided Callouts
     -------------------------------------------------------------------------- */
  const KEY_LANDMARKS = [
    { name: "Bến Ninh Kiều", lat: 10.0365, lon: 105.7885 },
    { name: "ĐH Cần Thơ Khu II", lat: 10.0299, lon: 105.7706 },
    { name: "Hồ Bún Xáng", lat: 10.0342, lon: 105.7686 },
    { name: "Ngã tư Mậu Thân - 3/2", lat: 10.0321, lon: 105.7745 },
    { name: "Cầu Hưng Lợi", lat: 10.0145, lon: 105.7746 },
    { name: "Cầu Quang Trung", lat: 10.0256, lon: 105.7868 },
    { name: "Chợ nổi Cái Răng", lat: 10.0038, lon: 105.7634 },
    { name: "Sân bay Cần Thơ", lat: 10.0752, lon: 105.7228 },
  ];

  function initMap() {
    if (!window.L || state.map) return;
    const map = L.map("map", {
      center: [10.034, 105.776],
      zoom: 13,
      zoomControl: false,
      attributionControl: false,
    });
    L.control.zoom({ position: "bottomright" }).addTo(map);

    state.basemaps = {
      street: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }),
      satellite: L.layerGroup([
        L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19 }),
        L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, opacity: 0.95 }),
        L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, opacity: 0.95 }),
      ]),
      dark: L.layerGroup([
        L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", { maxNativeZoom: 16, maxZoom: 19 }),
        L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", { maxNativeZoom: 16, maxZoom: 19 }),
      ]),
    };
    state.basemaps.street.addTo(map);

    state.layers.hex = L.layerGroup();
    state.layers.roads = L.layerGroup().addTo(map);
    state.layers.route = L.layerGroup().addTo(map);
    state.layers.landmarks = L.layerGroup().addTo(map);
    state.layers.stations = L.layerGroup().addTo(map);
    state.layers.incidents = L.layerGroup().addTo(map);
    state.layers.cameras = L.layerGroup().addTo(map);
    state.layers.customPins = L.layerGroup().addTo(map);

    map.on("zoomend", () => {
      if (state.activeSimData?.prediction?.roads) {
        renderRoadNetworkOnMap(state.activeSimData.prediction.roads);
      }
      renderSatelliteLandmarks();
    });

    map.on("click", (e) => {
      const lat = Number(e.latlng.lat.toFixed(6));
      const lon = Number(e.latlng.lng.toFixed(6));
      if (!state.pinMode && !state.pinTarget) return;

      if (state.pinTarget === "route_start") {
        state.routeStart = { nodeId: null, lat, lon, label: `Điểm ghim A (${lat.toFixed(4)}, ${lon.toFixed(4)})`, custom: true };
        stopAllMapPinning();
        updateCustomEndpointsUI();
        runFloodRoute();
        return;
      }
      if (state.pinTarget === "route_end") {
        state.routeEnd = { nodeId: null, lat, lon, label: `Điểm ghim B (${lat.toFixed(4)}, ${lon.toFixed(4)})`, custom: true };
        stopAllMapPinning();
        updateCustomEndpointsUI();
        runFloodRoute();
        return;
      }

      state.pinnedCoord = { lat, lon };
      if (state.layers.pinMarker) map.removeLayer(state.layers.pinMarker);
      state.layers.pinMarker = L.circleMarker([lat, lon], {
        radius: 8,
        color: "#ffffff",
        fillColor: "#0284C7",
        fillOpacity: 0.95,
        weight: 2.5,
      }).addTo(map);

      updateIncidentCoordStatus(`Vị trí đã chấm trên bản đồ: ${lat.toFixed(4)}°N, ${lon.toFixed(4)}°E`);
      stopAllMapPinning(false);
      if (state.mode !== "eval") {
        switchMode("eval");
      }
      showToast("Đã chấm tọa độ lên bài đăng Cộng đồng.");
    });

    state.map = map;
  }

  function buildOccupiedScreenPoints(includeFloodLabels = false) {
    const occupied = [];
    if (!state.map) return occupied;
    const pushLL = (lat, lon) => {
      try {
        occupied.push(state.map.latLngToContainerPoint([lat, lon]));
      } catch (_) {}
    };
    if ($("#chk-layer-stations")?.checked && state.weatherData?.districts) {
      state.weatherData.districts.forEach((d) => pushLL(d.lat, d.lon));
    }
    if ($("#chk-layer-cameras")?.checked && state.camerasData?.cameras) {
      state.camerasData.cameras.forEach((c) => pushLL(c.lat, c.lon));
    }
    (state.activeRouteLabelCoords || []).forEach((pt) => {
      if (Array.isArray(pt) && pt.length >= 2) pushLL(pt[0], pt[1]);
    });
    if (includeFloodLabels) {
      (state.placedFloodLabelCoords || []).forEach((pt) => {
        if (Array.isArray(pt) && pt.length >= 2) pushLL(pt[0], pt[1]);
      });
    }
    return occupied;
  }

  function isScreenBoxFree(lat, lon, occupiedPts, minDx = 128, minDy = 34) {
    if (!state.map) return true;
    const pt = state.map.latLngToContainerPoint([lat, lon]);
    const overlaps = occupiedPts.some((p) => Math.abs(pt.x - p.x) < minDx && Math.abs(pt.y - p.y) < minDy);
    if (overlaps) return false;
    occupiedPts.push(pt);
    return true;
  }

  function renderSatelliteLandmarks() {
    if (!state.map || !state.layers.landmarks) return;
    state.layers.landmarks.clearLayers();
    if (state.activeBasemap !== "satellite" || state.map.getZoom() < 13) return;

    const occupiedPts = buildOccupiedScreenPoints(true);
    KEY_LANDMARKS.forEach((lm) => {
      if (!isScreenBoxFree(lm.lat, lm.lon, occupiedPts, 118, 28)) return;
      const icon = L.divIcon({
        className: "landmark-chip-wrapper",
        html: `<div class="landmark-map-chip">${escapeHtml(lm.name)}</div>`,
        iconSize: [0, 0],
        iconAnchor: [45, 10],
      });
      L.marker([lm.lat, lm.lon], { icon, interactive: false }).addTo(state.layers.landmarks);
    });
  }

  function renderWeatherStationsOnMap(districts) {
    if (!state.map || !state.layers.stations) return;
    state.layers.stations.clearLayers();

    districts.forEach((d) => {
      const active = d.id === state.selectedDistrictId;
      const html = `
        <div class="district-map-pin ${active ? "selected" : ""}">
          <span class="pin-ico">${getWeatherSVG(d.icon_key)}</span>
          <span class="pin-name">${escapeHtml(d.short_name)}</span>
          <span class="pin-temp">${Math.round(d.temp_c)}°</span>
        </div>
      `;
      const icon = L.divIcon({
        className: "district-pin-wrapper",
        html,
        iconSize: [0, 0],
        iconAnchor: [48, 15],
      });

      const marker = L.marker([d.lat, d.lon], { icon, zIndexOffset: 200 }).addTo(state.layers.stations);
      marker.on("click", () => loadLiveWeather(d.id, false));
      marker.bindTooltip(`${d.name}: ${d.weather_label} · ${Math.round(d.temp_c)}°C`, { direction: "top", offset: [0, -14] });
    });
  }

  function renderRoadNetworkOnMap(roads = []) {
    if (!state.map || !state.layers.roads) return;
    state.layers.roads.clearLayers();

    const floodedRoads = roads
      .filter((r) => Number(r.depth_cm ?? 0) >= 8.0 && Array.isArray(r.coords) && r.coords.length >= 2)
      .sort((a, b) => Number(a.depth_cm) - Number(b.depth_cm));

    const casingColor = state.activeBasemap === "street" ? "#0F172A" : "#ffffff";

    floodedRoads.forEach((road) => {
      const depth = Number(road.depth_cm);
      const coreColor = depth >= 18.0 ? "#ef4444" : "#f59e0b";

      L.polyline(road.coords, {
        color: casingColor,
        weight: 8.5,
        opacity: 0.72,
        lineCap: "round",
        lineJoin: "round",
      }).addTo(state.layers.roads);

      L.polyline(road.coords, {
        color: coreColor,
        weight: 5,
        opacity: 0.96,
        lineCap: "round",
        lineJoin: "round",
      })
        .bindPopup(
          `<strong>${escapeHtml(road.name)}</strong><br/>Mực nước dự báo: <strong>${depth.toFixed(0)} cm</strong>` +
          (road.source === "community_1022" ? `<br/><span class="post-tag verified">Nguồn: Cộng đồng</span>` : "")
        )
        .addTo(state.layers.roads);
    });

    const occupiedPts = buildOccupiedScreenPoints(false);
    const placedMidpoints = [];
    const labelCandidates = [...floodedRoads].reverse().filter((r) => Number(r.depth_cm) >= 15.0);

    for (const road of labelCandidates) {
      if (placedMidpoints.length >= 4) break;
      const mid = road.coords[Math.floor(road.coords.length / 2)];
      if (!isScreenBoxFree(mid[0], mid[1], occupiedPts, 134, 34)) continue;

      placedMidpoints.push(mid);
      const isDanger = Number(road.depth_cm) >= 18.0;
      const lblIcon = L.divIcon({
        className: "flood-label-wrapper",
        html: `<div class="flood-road-label ${isDanger ? "" : "warn"}"><span>${escapeHtml(shortStreetName(road.name))}</span><span>${Math.round(road.depth_cm)}cm</span></div>`,
        iconSize: [0, 0],
        iconAnchor: [52, 13],
      });
      L.marker(mid, { icon: lblIcon, zIndexOffset: 600 })
        .bindPopup(`<strong>${escapeHtml(road.name)}</strong><br/>Độ sâu: <strong>${Number(road.depth_cm).toFixed(0)} cm</strong>`)
        .addTo(state.layers.roads);
    }
    state.placedFloodLabelCoords = placedMidpoints;
  }

  function depthToColor(depthCm) {
    if (depthCm >= 35) return "#ef4444";
    if (depthCm >= 18) return "#f97316";
    if (depthCm >= 8) return "#f59e0b";
    return "#10b981";
  }

  function renderHexCellsOnMap(hexCells = []) {
    if (!state.map || !state.layers.hex) return;
    state.layers.hex.clearLayers();

    hexCells.forEach((cell) => {
      const depth = Number(cell.depth_cm ?? 0);
      if (depth < 8.0) return;
      const color = depthToColor(depth);
      const verts = cell.vertices || cell.polygon;
      if (verts && verts.length >= 3) {
        L.polygon(verts, {
          color,
          weight: 1,
          opacity: 0.45,
          fillColor: color,
          fillOpacity: Math.min(0.45, 0.16 + depth / 110),
        })
          .bindPopup(`<strong>${escapeHtml(cell.nearest_landmark || "Khu vực Cần Thơ")}</strong><br/>Ngập dự báo: <strong>${depth.toFixed(0)} cm</strong>`)
          .addTo(state.layers.hex);
      }
    });
  }

  /* --------------------------------------------------------------------------
     Simulation & Active Flood State
     -------------------------------------------------------------------------- */
  function updateSliderLabels() {
    $("#val-tide").textContent = `${Number($("#slider-tide").value).toFixed(2)} m`;
    $("#val-rain").textContent = `${$("#slider-rain").value} mm/h`;
    $("#val-rain3h").textContent = `${$("#slider-rain3h").value} mm`;
    const delta = Number($("#slider-delta").value);
    $("#val-delta").textContent = `${delta >= 0 ? "+" : ""}${delta.toFixed(2)} m/h`;
  }

  async function runCustomSimulation() {
    const payload = {
      tide_level_m: Number($("#slider-tide").value),
      rain_mm_h: Number($("#slider-rain").value),
      rain_3h_cum: Number($("#slider-rain3h").value),
      tide_delta_m: Number($("#slider-delta").value),
      is_spring_tide: $("#chk-spring").checked,
      horizon: state.activeHorizon,
      model_key: $("#ml-model-select").value,
    };

    try {
      const res = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.activeSimData = data;
      applyActiveSimulationToUI(data);
    } catch (err) {
      showToast(`Lỗi mô phỏng: ${err.message}`);
    }
  }

  function applyActiveSimulationToUI(data) {
    const pred = data.prediction || {};
    const roads = pred.roads || [];
    const severeOrWarn = roads.filter((r) => Number(r.depth_cm) >= 8.0).sort((a, b) => b.depth_cm - a.depth_cm);
    const maxRoad = severeOrWarn.length ? severeOrWarn[0].depth_cm : 0;

    $("#flood-kpi-roads").textContent = `${roads.filter((r) => r.depth_cm >= 18).length}`;
    $("#flood-kpi-max").textContent = `${Math.round(maxRoad)} cm`;
    $("#flood-kpi-tide").textContent = `${Number(data.inputs?.tide_level_m ?? 1.4).toFixed(2)} m`;

    const simPill = $("#sim-summary-pill");
    if (simPill) {
      if (severeOrWarn.length === 0) simPill.textContent = "Khô ráo";
      else if (severeOrWarn.length > 6 && !state.showAllFlooded) simPill.textContent = `6/${severeOrWarn.length} đoạn`;
      else simPill.textContent = `${severeOrWarn.length} đoạn`;
    }

    const topEl = $("#top-flooded-list");
    if (topEl) {
      const visibleRoads = state.showAllFlooded ? severeOrWarn : severeOrWarn.slice(0, 6);
      const toggleHtml = severeOrWarn.length > 6
        ? `<button type="button" class="btn-utility-sharp btn-show-all-flooded" id="btn-toggle-all-flooded">${state.showAllFlooded ? "Thu gọn danh sách" : `Xem tất cả (${severeOrWarn.length} đoạn)`}</button>`
        : "";
      topEl.innerHTML = severeOrWarn.length
        ? visibleRoads
            .map(
              (r) => `
              <button type="button" class="flooded-row" data-road-id="${escapeHtml(r.id)}">
                <span class="flooded-row-name">${escapeHtml(String(r.name || "").replace(/->/g, "→"))}</span>
                <span class="status-pill ${r.depth_cm >= 18 ? "badge-danger" : "badge-warn"}">${Math.round(r.depth_cm)} cm</span>
              </button>
            `
            )
            .join("") + toggleHtml
        : `<p class="route-status-note">Không có tuyến đường nào bị ngập.</p>`;

      $$(".flooded-row", topEl).forEach((btn) => {
        btn.addEventListener("click", () => {
          const road = severeOrWarn.find((x) => x.id === btn.dataset.roadId);
          focusMapOnRoad(road);
        });
      });
      $("#btn-toggle-all-flooded", topEl)?.addEventListener("click", () => {
        state.showAllFlooded = !state.showAllFlooded;
        applyActiveSimulationToUI(data);
      });
    }

    renderHydrographSVG(data.hydrograph_24h || []);
    renderRoadNetworkOnMap(roads);
    renderHexCellsOnMap(pred.hex_cells || []);
    updateMapAndStripStatusBanner(data);
  }

  function updateMapAndStripStatusBanner(simJson) {
    const pred = simJson.prediction || {};
    const roads = pred.roads || [];
    const severeCount = roads.filter((r) => Number(r.depth_cm) >= 18.0).length;
    const warnCount = roads.filter((r) => Number(r.depth_cm) >= 8.0 && Number(r.depth_cm) < 18.0).length;

    const pill = $("#map-mode-badge");
    const pillText = $("#map-mode-badge-text");
    if (pill && pillText) {
      pill.classList.remove("state-warn", "state-danger");
      if (state.isSimulationOverride) {
        pill.classList.add(severeCount > 0 ? "state-danger" : "state-warn");
        pillText.textContent = severeCount > 0 ? `Giả lập: ${severeCount} đoạn ngập sâu` : `Đang xem giả lập (${state.activeHorizon})`;
      } else if (severeCount > 0) {
        pill.classList.add("state-danger");
        pillText.textContent = `Thực tế: Đang ngập ${severeCount} đoạn trũng`;
      } else if (warnCount > 0) {
        pill.classList.add("state-warn");
        pillText.textContent = `Thực tế: Ngập nhẹ cục bộ ${warnCount} đoạn`;
      } else {
        pillText.textContent = "Thực tế Cần Thơ: Đường thông thoáng";
      }
    }

    const strip = $("#flood-mode-strip");
    const stripText = $("#flood-mode-status-text");
    const resetBtn = $("#btn-reset-to-live");
    if (strip && stripText && resetBtn) {
      strip.classList.toggle("sim-active", state.isSimulationOverride);
      resetBtn.classList.toggle("hidden", !state.isSimulationOverride);
      stripText.textContent = state.isSimulationOverride
        ? `Đang chạy kịch bản giả lập (Triều ${Number(simJson.inputs?.tide_level_m ?? 2).toFixed(2)}m)`
        : "Theo mực nước & mưa thực tế";
    }
  }

  function hasNonLiveSimulationInputs() {
    if (state.activeHorizon !== "now") return true;
    if (($("#ml-model-select")?.value || "hgbr") !== "hgbr") return true;
    if ($("#scenario-list .scenario-chip.active")) return true;
    const liveInp = state.liveSimData?.inputs;
    if (!liveInp) return false;
    const tideDiff = Math.abs(Number($("#slider-tide")?.value ?? liveInp.tide_level_m) - Number(liveInp.tide_level_m));
    const rainDiff = Math.abs(Number($("#slider-rain")?.value ?? liveInp.rain_mm_h) - Number(liveInp.rain_mm_h));
    const rain3Diff = Math.abs(Number($("#slider-rain3h")?.value ?? liveInp.rain_3h_cum) - Number(liveInp.rain_3h_cum));
    const deltaDiff = Math.abs(Number($("#slider-delta")?.value ?? liveInp.tide_delta_m) - Number(liveInp.tide_delta_m));
    return tideDiff > 0.02 || rainDiff > 0.5 || rain3Diff > 0.5 || deltaDiff > 0.02;
  }

  function resetToLiveConditions() {
    state.isSimulationOverride = false;
    state.activeHorizon = "now";
    state.showAllFlooded = false;
    $$("#horizon-tabs .seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.horizon === "now"));
    $$("#scenario-list .scenario-chip").forEach((c) => c.classList.remove("active"));
    const descEl = $("#scenario-desc-note");
    if (descEl) {
      descEl.textContent = "";
      descEl.classList.add("hidden");
    }
    if ($("#ml-model-select")) $("#ml-model-select").value = "hgbr";

    if (state.weatherData && state.liveSimData) {
      const inp = state.liveSimData.inputs || {};
      applyLiveInputsToSliders(
        Number(inp.tide_level_m ?? 1.45),
        Number(inp.rain_mm_h ?? 0),
        Number(inp.rain_3h_cum ?? 0),
        Number(inp.tide_delta_m ?? 0),
        Boolean(state.weatherData.astronomy_and_tide?.is_spring_tide_window)
      );
      state.activeSimData = state.liveSimData;
      applyActiveSimulationToUI(state.liveSimData);
      runFloodRoute();
      loadCameras();
      showToast("Đã quay về điều kiện thực tế Cần Thơ.");
    } else {
      loadLiveWeather(state.selectedDistrictId, false);
    }
  }

  /* --------------------------------------------------------------------------
     Street-Snapped Hybrid Flood-Aware A* Routing & Departure Advisor
     -------------------------------------------------------------------------- */
  function setSheetCollapsed(collapsed) {
    state.sheetCollapsed = Boolean(collapsed);
    const sheet = $("#panel-flood");
    const btn = $("#btn-toggle-sheet");
    if (sheet) sheet.classList.toggle("collapsed", state.sheetCollapsed);
    document.body.classList.toggle("sheet-collapsed", state.sheetCollapsed);
    if (btn) {
      btn.textContent = state.sheetCollapsed ? "Mở bảng" : "Thu gọn";
      btn.setAttribute("aria-expanded", String(!state.sheetCollapsed));
    }
  }

  function startMapPinning(target, message) {
    stopAllMapPinning(false);
    state.pinTarget = target;
    state.pinMode = target === "incident";
    if (target === "route_start") $("#btn-pin-start")?.classList.add("active-pin");
    if (target === "route_end") $("#btn-pin-end")?.classList.add("active-pin");
    if (target === "incident") $("#btn-toggle-pin")?.classList.add("active");
    if (state.map) state.map.getContainer().style.cursor = "crosshair";
    if ($("#map-pin-banner-text")) $("#map-pin-banner-text").textContent = message;
    $("#map-pin-banner")?.classList.remove("hidden");
    document.body.classList.add("pinning-active");
    if (window.innerWidth <= 820 && (target === "route_start" || target === "route_end")) {
      setSheetCollapsed(true);
    }
  }

  function stopAllMapPinning(restoreSheet = true) {
    const wasRoutePin = state.pinTarget === "route_start" || state.pinTarget === "route_end";
    state.pinMode = false;
    state.pinTarget = null;
    $("#btn-toggle-pin")?.classList.remove("active");
    $("#btn-pin-start")?.classList.remove("active-pin");
    $("#btn-pin-end")?.classList.remove("active-pin");
    $("#map-pin-banner")?.classList.add("hidden");
    document.body.classList.remove("pinning-active");
    if (state.map) state.map.getContainer().style.cursor = "";
    if (restoreSheet && wasRoutePin && window.innerWidth <= 820 && state.sheetCollapsed) {
      setSheetCollapsed(false);
    }
  }

  function updateCustomEndpointsUI() {
    const startInp = $("#route-start-input");
    const endInp = $("#route-end-input");
    if (startInp && document.activeElement !== startInp && state.routeStart?.label) {
      startInp.value = state.routeStart.label;
    }
    if (endInp && document.activeElement !== endInp && state.routeEnd?.label) {
      endInp.value = state.routeEnd.label;
    }
    const bar = $("#custom-endpoints-bar");
    const txt = $("#custom-endpoints-text");
    if (!bar || !txt) return;
    const hasCustom = Boolean(state.routeStart?.custom || state.routeEnd?.custom);
    bar.classList.toggle("hidden", !hasCustom);
    if (hasCustom) {
      const parts = [];
      if (state.routeStart?.custom) parts.push(`A: ${state.routeStart.label}`);
      if (state.routeEnd?.custom) parts.push(`B: ${state.routeEnd.label}`);
      txt.textContent = parts.join(" · ");
    }
  }

  function renderDepartureAdvisor(advisor) {
    const box = $("#departure-advisor-box");
    const noteEl = $("#departure-advice-text");
    const slotsEl = $("#departure-slots-grid");
    if (!box || !noteEl || !slotsEl) return;
    if (state.isSimulationOverride) {
      box.classList.remove("hidden");
      noteEl.textContent = "Cố vấn khung giờ xuất hành chỉ áp dụng cho thời tiết thực tế (bấm \"Quay về Thực tế\" để xem).";
      slotsEl.innerHTML = "";
      return;
    }
    const wins = advisor?.windows || [];
    if (!wins.length) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    noteEl.textContent = advisor.best_departure_advice || "";
    slotsEl.innerHTML = wins
      .map((w) => {
        const depth = Math.round(w.direct_max_depth_cm || 0);
        const cls = !w.is_direct_safe ? "detour" : (depth >= 8 ? "warn" : "safe");
        const statusTxt = !w.is_direct_safe
          ? `Vòng ${w.safe_km}km`
          : (depth >= 8 ? "Ngập nhẹ" : "Đi thẳng");
        return `
          <div class="advisor-slot ${cls}">
            <strong>${escapeHtml(w.time_label)}</strong>
            <span>${depth}cm · ${statusTxt}</span>
          </div>
        `;
      })
      .join("");
  }

  async function runFloodRoute() {
    const rs = state.routeStart || {};
    const re = state.routeEnd || {};
    const tooClose =
      rs.lat != null &&
      re.lat != null &&
      Math.hypot(Number(rs.lat) - Number(re.lat), Number(rs.lon) - Number(re.lon)) < 0.00012;
    if (tooClose) {
      showToast("Vui lòng chọn Điểm đi và Điểm đến khác nhau.");
      return;
    }

    const activeInputs = (!state.isSimulationOverride && state.liveSimData?.inputs)
      ? state.liveSimData.inputs
      : {
          tide_level_m: Number($("#slider-tide").value),
          rain_mm_h: Number($("#slider-rain").value),
          rain_3h_cum: Number($("#slider-rain3h").value),
          tide_delta_m: Number($("#slider-delta").value),
          horizon: state.activeHorizon,
          model_key: $("#ml-model-select").value,
        };

    const reqBody = {
      start_id: rs.nodeId || "dhct_khu2",
      end_id: re.nodeId || "ben_ninh_kieu",
      vehicle_type: state.selectedVehicleId,
      tide_level_m: Number(activeInputs.tide_level_m),
      rain_mm_h: Number(activeInputs.rain_mm_h),
      rain_3h_cum: Number(activeInputs.rain_3h_cum),
      tide_delta_m: Number(activeInputs.tide_delta_m),
      horizon: String(activeInputs.horizon || "now"),
      model_key: String(activeInputs.model_key || "hgbr"),
      include_community: !state.isSimulationOverride,
      include_departure: !state.isSimulationOverride,
    };
    if (rs.lat != null && rs.lon != null) {
      reqBody.start_coord = [Number(rs.lat), Number(rs.lon)];
      reqBody.start_label = rs.label || "Điểm A";
    }
    if (re.lat != null && re.lon != null) {
      reqBody.end_coord = [Number(re.lat), Number(re.lon)];
      reqBody.end_label = re.label || "Điểm B";
    }

    try {
      const res = await fetch("/api/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reqBody),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const std = data.standard_route || {};
      const safe = data.flood_aware_route || {};

      $("#route-safe-dist").textContent = `${safe.total_length_km ?? "--"} km · ${safe.estimated_minutes ?? "--"} phút`;
      $("#route-safe-depth").textContent = `Tối đa: ${Number(safe.max_depth_cm ?? 0).toFixed(0)} cm`;
      const safeBadge = $("#route-safe-status");
      safeBadge.textContent = safe.is_safe ? "An toàn cho xe" : "Ít ngập nhất";
      safeBadge.className = `route-badge ${safe.is_safe ? "safe" : "danger"}`;

      $("#route-std-dist").textContent = `${std.total_length_km ?? "--"} km · ${std.estimated_minutes ?? "--"} phút`;
      $("#route-std-depth").textContent = `Tối đa: ${Number(std.max_depth_cm ?? 0).toFixed(0)} cm`;
      const stdBadge = $("#route-std-status");
      const sameDryRoute = std.is_safe && !routesGeometryDiffer(std, safe);
      stdBadge.textContent = std.is_safe
        ? (sameDryRoute ? "Trùng đường an toàn" : "An toàn")
        : `Ngập (${std.dangerous_segments?.length ?? 1} đoạn)`;
      stdBadge.className = `route-badge ${std.is_safe ? "safe" : "danger"}`;

      $("#route-banner").textContent = stripEmoji(data.status_message || "");
      state.lastGoogleMapsUrl = data.google_maps_url || "";
      const gmapsBtn = $("#btn-open-gmaps");
      if (gmapsBtn) {
        const hasWaypoints = state.lastGoogleMapsUrl.includes("waypoints=");
        gmapsBtn.title = hasWaypoints
          ? "Mở lộ trình né ngập (kèm điểm trung gian) trên Google Maps"
          : "Mở chỉ đường lộ trình này trên Google Maps";
      }

      renderDepartureAdvisor(data.departure_advisor);
      renderRoutesOnMap(std, safe);
    } catch (err) {
      showToast(`Lỗi tính lộ trình: ${err.message}`);
    }
  }

  function routesGeometryDiffer(std, safe) {
    if (!std?.coords?.length || !safe?.coords?.length) return false;
    if (Math.abs((std.total_length_km || 0) - (safe.total_length_km || 0)) >= 0.03) return true;
    const stdMid = std.coords[Math.floor(std.coords.length / 2)];
    const safeMid = safe.coords[Math.floor(safe.coords.length / 2)];
    if (!stdMid || !safeMid) return false;
    return Math.hypot(stdMid[0] - safeMid[0], stdMid[1] - safeMid[1]) > 0.00025;
  }

  function renderRoutesOnMap(std, safe) {
    if (!state.map || !state.layers.route) return;
    state.layers.route.clearLayers();
    const bounds = L.latLngBounds([]);
    const routeLabelPts = [];

    const pathsDiffer = routesGeometryDiffer(std, safe);
    if (!std.is_safe && pathsDiffer && std.coords?.length >= 2) {
      L.polyline(std.coords, { color: "#7f1d1d", weight: 8, opacity: 0.72 }).addTo(state.layers.route);

      const redLine = L.polyline(std.coords, {
        color: "#ef4444",
        weight: 4.5,
        opacity: 0.95,
        dashArray: "8 8",
      }).addTo(state.layers.route);
      bounds.extend(redLine.getBounds());

      const worstSeg = (std.dangerous_segments || [])[0];
      const warnPt = worstSeg?.mid_coord || std.coords[Math.floor(std.coords.length / 2)];
      let redAnchor = [58, 12];
      let greenAnchor = [70, 14];
      if (warnPt && safe.coords?.length >= 2) {
        const midPtCheck = safe.coords[Math.floor(safe.coords.length / 2)];
        try {
          const pWarn = state.map.latLngToContainerPoint(warnPt);
          const pSafe = state.map.latLngToContainerPoint(midPtCheck);
          if (Math.abs(pWarn.x - pSafe.x) < 120 && Math.abs(pWarn.y - pSafe.y) < 34) {
            redAnchor = [58, 32];
            greenAnchor = [70, -6];
          }
        } catch (_) {}
      }
      if (warnPt) {
        routeLabelPts.push(warnPt);
        const redCallout = L.divIcon({
          className: "route-callout-wrapper",
          html: `<div class="route-callout-pill route-callout-danger">Ngập ${Math.round(std.max_depth_cm)} cm · Nên tránh</div>`,
          iconSize: [0, 0],
          iconAnchor: redAnchor,
        });
        L.marker(warnPt, { icon: redCallout, interactive: false, zIndexOffset: 800 }).addTo(state.layers.route);
      }
      state._greenAnchorOverride = greenAnchor;
    } else {
      state._greenAnchorOverride = null;
    }

    if (safe.coords?.length >= 2) {
      L.polyline(safe.coords, {
        color: "#064e3b",
        weight: 10,
        opacity: 0.85,
        lineCap: "round",
        lineJoin: "round",
      }).addTo(state.layers.route);

      const greenLine = L.polyline(safe.coords, {
        color: "#10b981",
        weight: 5.5,
        opacity: 0.98,
        lineCap: "round",
        lineJoin: "round",
      }).addTo(state.layers.route);
      bounds.extend(greenLine.getBounds());

      const midPt = safe.coords[Math.floor(safe.coords.length / 2)];
      routeLabelPts.push(midPt);
      const safeCallout = L.divIcon({
        className: "route-callout-wrapper",
        html: `<div class="route-callout-pill route-callout-safe">${safe.is_safe ? "An toàn" : "Ít ngập nhất"} · ${safe.total_length_km} km · ${safe.estimated_minutes} phút</div>`,
        iconSize: [0, 0],
        iconAnchor: state._greenAnchorOverride || [70, 14],
      });
      L.marker(midPt, { icon: safeCallout, interactive: false, zIndexOffset: 800 }).addTo(state.layers.route);

      const startPt = safe.coords[0];
      const endPt = safe.coords[safe.coords.length - 1];
      routeLabelPts.push(startPt, endPt);

      const pinA = L.divIcon({
        className: "endpoint-pin-wrapper",
        html: `<div class="route-endpoint-pin start">A</div>`,
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      });
      const pinB = L.divIcon({
        className: "endpoint-pin-wrapper",
        html: `<div class="route-endpoint-pin end">B</div>`,
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      });
      const markerA = L.marker(startPt, { icon: pinA, zIndexOffset: 900, draggable: true })
        .bindTooltip(`Điểm đi (kéo để chỉnh): ${(safe.node_names || [])[0] || ""}`)
        .addTo(state.layers.route);
      const markerB = L.marker(endPt, { icon: pinB, zIndexOffset: 900, draggable: true })
        .bindTooltip(`Điểm đến (kéo để chỉnh): ${(safe.node_names || []).slice(-1)[0] || ""}`)
        .addTo(state.layers.route);

      const bindPinDrag = (marker, role) => {
        marker.on("dragend", (ev) => {
          const pos = ev.target.getLatLng();
          const lat = Number(pos.lat.toFixed(6));
          const lon = Number(pos.lng.toFixed(6));
          const lbl = `Điểm ghim ${role === "start" ? "A" : "B"} (${lat.toFixed(4)}, ${lon.toFixed(4)})`;
          const payload = { nodeId: null, lat, lon, label: lbl, custom: true };
          if (role === "start") state.routeStart = payload;
          else state.routeEnd = payload;
          updateCustomEndpointsUI();
          runFloodRoute();
        });
      };
      bindPinDrag(markerA, "start");
      bindPinDrag(markerB, "end");
    }

    state.activeRouteLabelCoords = routeLabelPts;
    if (bounds.isValid()) {
      state.map.stop();
      const isCompact = window.innerWidth <= 820;
      const padOpts = isCompact
        ? { paddingTopLeft: [24, 112], paddingBottomRight: [24, Math.round(window.innerHeight * 0.46)], animate: false }
        : { paddingTopLeft: [430, 86], paddingBottomRight: [48, 72], animate: false };
      state.map.fitBounds(bounds, padOpts);
    }
    if (state.activeSimData?.prediction?.roads) {
      renderRoadNetworkOnMap(state.activeSimData.prediction.roads);
    }
    renderSatelliteLandmarks();
  }

  /* --------------------------------------------------------------------------
     Community Reports Feed, Dynamic TTL, Photo Compression & Camera Hub
     -------------------------------------------------------------------------- */
  const CATEGORY_LABELS = {
    flood_deep: "Ngập sâu",
    flood_light: "Ngập nhẹ",
    traffic: "Kẹt xe",
    rain: "Mưa lớn",
    dry: "Khô thoáng",
  };
  const DISTRICT_LABELS = {
    ninh_kieu: "Ninh Kiều",
    binh_thuy: "Bình Thủy",
    cai_rang: "Cái Răng",
    o_mon: "Ô Môn",
    thot_not: "Thốt Nốt",
    phong_dien: "Phong Điền",
  };

  function getInitials(name) {
    const cleanName = String(name || "CT").replace(/\([^)]*\)/g, " ").trim();
    const words = cleanName
      .split(/\s+/)
      .map((w) => w.replace(/[^\p{L}\p{N}]/gu, ""))
      .filter(Boolean);
    if (!words.length) return "CT";
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return (words[0][0] + words[words.length - 1][0]).toUpperCase();
  }

  function formatRelativeAge(ageMin) {
    const m = Math.round(Number(ageMin || 0));
    if (m < 1) return "Vừa xong";
    if (m < 60) return `${m} phút trước`;
    return `${Math.floor(m / 60)}g ${m % 60}p trước`;
  }

  function compressImageFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Không đọc được file ảnh"));
      reader.onload = () => {
        const img = new Image();
        img.onerror = () => reject(new Error("Định dạng ảnh không hợp lệ"));
        img.onload = () => {
          const maxW = 720;
          const scale = Math.min(1, maxW / Math.max(1, img.width));
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, Math.round(img.width * scale));
          canvas.height = Math.max(1, Math.round(img.height * scale));
          const ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

          let out = canvas.toDataURL("image/jpeg", 0.72);
          if (out.length > 110000) out = canvas.toDataURL("image/jpeg", 0.55);
          if (out.length > 120000) out = canvas.toDataURL("image/jpeg", 0.42);
          if (out.length > 124000) {
            reject(new Error("Ảnh quá lớn sau khi nén. Hãy chọn ảnh nhẹ hơn."));
            return;
          }
          resolve(out);
        };
        img.src = String(reader.result || "");
      };
      reader.readAsDataURL(file);
    });
  }

  async function loadIncidents() {
    try {
      const url = state.incidentDemoMode ? "/api/incidents?demo=true" : "/api/incidents";
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      state.latestIncidentsData = data;
      renderIncidents(data);
    } catch (err) {
      console.warn("Incidents load error:", err);
    }
  }

  function renderIncidents(data) {
    const payload = data.clustered || data || {};
    const clusters = payload.master_incidents || [];
    const pending = payload.pending_reports || [];
    const noises = payload.noise_reports || [];
    const feedAll = payload.social_feed || [...clusters, ...pending];
    const ttlMin = Number(payload.ttl_minutes ?? 60);
    const isWet = payload.weather_regime === "wet_or_high_tide";
    const isDemo = Boolean(payload.is_demo);

    if ($("#incident-ttl-strip")) {
      $("#incident-ttl-strip").textContent = isDemo
        ? "Chế độ Dữ liệu mẫu"
        : `Hiệu lực tin: ${ttlMin} phút (${isWet ? "Đang mưa / triều cao" : "Trời tạnh · triều thấp"})`;
    }
    if ($("#incident-stats")) {
      $("#incident-stats").textContent = isDemo
        ? `${clusters.length} cụm mẫu · ${noises.length} tin lẻ`
        : `${clusters.length} xác thực · ${pending.length} chờ`;
    }

    $("#incident-demo-banner")?.classList.toggle("hidden", !isDemo);
    $("#btn-toggle-incident-demo")?.classList.toggle("active-pin", isDemo);

    const filteredFeed = feedAll.filter((item) => {
      if (state.feedFilter === "flood" && (item.category === "dry" || item.category === "traffic" || item.category === "rain")) return false;
      if (state.feedFilter === "flood" && Number(item.avg_depth_cm ?? 0) < 8) return false;
      if (state.feedFilter === "photo" && !item.photo_data) return false;
      if (state.feedDistrictFilter !== "all" && item.district !== state.feedDistrictFilter) return false;
      return true;
    });

    const listEl = $("#incident-list");
    if (listEl) {
      const commentDrafts = {};
      let focusedRepId = null;
      const activeCommentEl = document.activeElement;
      if (activeCommentEl && activeCommentEl.classList?.contains("comment-reply-input")) {
        focusedRepId = activeCommentEl.dataset?.repId || null;
      }
      $$(".comment-reply-input", listEl).forEach((inp) => {
        if (inp.value) commentDrafts[inp.dataset.repId] = inp.value;
      });
      const restoreCommentDrafts = () => {
        Object.entries(commentDrafts).forEach(([rid, val]) => {
          const inp = $(`.comment-reply-input[data-rep-id="${CSS.escape(rid)}"]`, listEl);
          if (inp) inp.value = val;
        });
        if (focusedRepId) {
          const focusedInp = $(`.comment-reply-input[data-rep-id="${CSS.escape(focusedRepId)}"]`, listEl);
          if (focusedInp) focusedInp.focus({ preventScroll: true });
        }
      };

      if (!filteredFeed.length) {
        if (feedAll.length > 0) {
          listEl.innerHTML = `
            <div class="incident-empty-state">
              <strong>Không có bài đăng nào khớp bộ lọc hiện tại</strong>
              <span>Thử bỏ lọc quận hoặc chuyển về tab "Tất cả" để xem toàn bộ ${feedAll.length} tin đang có.</span>
              <div style="margin-top: 6px;">
                <button type="button" class="btn-utility-sharp" id="btn-reset-feed-filters">Xem tất cả bài đăng</button>
              </div>
            </div>
          `;
          $("#btn-reset-feed-filters", listEl)?.addEventListener("click", () => {
            state.feedFilter = "all";
            state.feedDistrictFilter = "all";
            $$("#feed-filter-tabs .seg-btn").forEach((x) => x.classList.toggle("active", x.dataset.filter === "all"));
            if ($("#feed-district-filter")) $("#feed-district-filter").value = "all";
            renderIncidents(data);
          });
        } else {
          listEl.innerHTML = `
            <div class="incident-empty-state">
              <strong>Hiện chưa có tin báo thực tế nào còn hiệu lực</strong>
              <span>Chưa có tin báo ngập nào từ cộng đồng. Bạn có thể đăng tình trạng đường phố ngay phía trên hoặc bấm "Xem dữ liệu mẫu" để xem minh họa.</span>
            </div>
          `;
        }
      } else {
        listEl.innerHTML = filteredFeed
          .map((c, idx) => {
            const title = c.location_label || "Điểm báo cộng đồng";
            const author = c.reporter_name || "Người dân Cần Thơ";
            const desc = Array.isArray(c.descriptions) && c.descriptions.length ? c.descriptions[0] : (c.description || "");
            const catLabel = CATEGORY_LABELS[c.category] || "Báo ngập";
            const distLabel = DISTRICT_LABELS[c.district] || "Cần Thơ";
            const depthVal = Math.round(c.avg_depth_cm ?? 0);
            const depthCls = depthVal >= 18 ? "danger" : (depthVal >= 8 ? "warn" : "");
            const isVerified = c.status === "verified";
            const isPending = c.status === "pending";
            const cardCls = isVerified ? "is-verified" : (isPending ? "is-pending" : "is-civic");
            const statusTag = isVerified
              ? `<span class="post-tag verified">Đã xác thực (${c.report_count} tin${c.fusion_eligible ? " · Tự động né trên bản đồ" : ""})</span>`
              : (isPending
                  ? `<span class="post-tag pending">Chờ xác nhận thêm (1 tin)</span>`
                  : `<span class="post-tag">Tin cộng đồng</span>`);
            const plaus = c.plausibility;
            const plausTag = plaus
              ? `<span class="post-tag ${plaus.tier === "high" ? "plaus-high" : (plaus.tier === "low_needs_review" ? "plaus-low" : "")}">${escapeHtml(plaus.label)} · ${plaus.score}%</span>`
              : "";
            const comments = c.comments || [];
            const repId = c.primary_report_id || c.incident_id;
            const isExpanded = Boolean(state.expandedComments[repId]);

            return `
              <article class="social-post-card ${cardCls}" data-feed-idx="${idx}">
                <div class="post-header">
                  <div class="post-author-wrap">
                    <div class="post-avatar" aria-hidden="true">${escapeHtml(getInitials(author))}</div>
                    <div class="post-author-meta">
                      <strong>${escapeHtml(author)}</strong>
                      <span class="post-time-loc">${escapeHtml(title)} · ${escapeHtml(distLabel)} · ${formatRelativeAge(c.age_minutes)} · còn ${c.expires_in_minutes ?? ttlMin}p</span>
                    </div>
                  </div>
                  ${depthVal >= 8 ? `<div class="post-depth-box ${depthCls}">${depthVal} cm</div>` : `<div class="post-depth-box">${escapeHtml(catLabel)}</div>`}
                </div>

                <div class="post-badges-row">
                  <span class="post-tag">${escapeHtml(catLabel)}</span>
                  ${statusTag}
                  ${plausTag}
                </div>

                <p class="post-body-text">${escapeHtml(stripEmoji(desc))}</p>

                ${c.photo_data ? `<img class="post-photo-thumb" src="${c.photo_data}" alt="Ảnh hiện trường tại ${escapeHtml(title)}" data-caption="${escapeHtml(title)} · ${depthVal} cm" />` : ""}

                <div class="post-actions-bar">
                  <button type="button" class="btn-post-action confirm" data-action="confirm" data-feed-idx="${idx}">+1 Xác nhận (${c.report_count ?? 1})</button>
                  <button type="button" class="btn-post-action clear" data-action="clear" data-feed-idx="${idx}">Đã rút nước${c.cleared_votes ? ` (${c.cleared_votes})` : ""}</button>
                  <button type="button" class="btn-post-action" data-action="toggle-comments" data-rep-id="${escapeHtml(repId)}">Bình luận (${comments.length})</button>
                  <button type="button" class="btn-post-action" data-action="focus-map" data-feed-idx="${idx}">Xem bản đồ</button>
                </div>

                <div class="comments-drawer ${isExpanded ? "" : "hidden"}" id="comments-${escapeHtml(repId)}">
                  ${comments.map((cm) => `
                    <div class="comment-item">
                      <strong>${escapeHtml(cm.author_name)}</strong>
                      <span>${escapeHtml(String(cm.timestamp_iso || "").slice(11, 16))}</span>
                      <div>${escapeHtml(cm.content)}</div>
                    </div>
                  `).join("")}
                  <div class="comment-input-row">
                    <input type="text" class="comment-reply-input" placeholder="Viết bình luận cập nhật..." maxlength="240" data-rep-id="${escapeHtml(repId)}" />
                    <button type="button" class="btn-utility-sharp btn-send-comment" data-rep-id="${escapeHtml(repId)}">Gửi</button>
                  </div>
                </div>
              </article>
            `;
          })
          .join("");

        $$(".post-photo-thumb", listEl).forEach((img) => {
          img.addEventListener("click", () => openPhotoLightbox(img.src, img.dataset.caption || "Ảnh hiện trường"));
        });

        $$("[data-action='confirm'], [data-action='clear']", listEl).forEach((btn) => {
          btn.addEventListener("click", () => {
            const item = filteredFeed[Number(btn.dataset.feedIdx)];
            if (item) voteIncident(btn.dataset.action, item);
          });
        });

        $$("[data-action='toggle-comments']", listEl).forEach((btn) => {
          btn.addEventListener("click", () => {
            const rid = btn.dataset.repId;
            state.expandedComments[rid] = !state.expandedComments[rid];
            $(`#comments-${CSS.escape(rid)}`)?.classList.toggle("hidden", !state.expandedComments[rid]);
          });
        });

        $$(".btn-send-comment", listEl).forEach((btn) => {
          btn.addEventListener("click", () => {
            const rid = btn.dataset.repId;
            const inp = $(`.comment-reply-input[data-rep-id="${CSS.escape(rid)}"]`, listEl);
            if (inp) submitComment(rid, inp.value);
          });
        });

        $$("[data-action='focus-map']", listEl).forEach((btn) => {
          btn.addEventListener("click", () => {
            const item = filteredFeed[Number(btn.dataset.feedIdx)];
            if (!item || !state.map) return;
            switchMode("flood");
            setTimeout(() => {
              state.map.stop();
              state.map.setView([item.lat, item.lon], 15, { animate: false });
              L.popup()
                .setLatLng([item.lat, item.lon])
                .setContent(`<strong>${escapeHtml(item.location_label)}</strong><br/>Mức nước: <strong>${Math.round(item.avg_depth_cm ?? 0)} cm</strong>`)
                .openOn(state.map);
            }, 90);
          });
        });
        restoreCommentDrafts();
      }
    }

    if (state.map && state.layers.incidents) {
      state.layers.incidents.clearLayers();
      clusters.forEach((c) => {
        L.circleMarker([c.lat, c.lon], {
          radius: 8,
          color: "#ffffff",
          fillColor: "#DC2626",
          fillOpacity: 0.94,
          weight: 2,
        })
          .bindPopup(`<strong>${escapeHtml(c.location_label)}</strong> (${c.report_count} tin xác thực)<br/>Độ sâu TB: <strong>${Math.round(c.avg_depth_cm)} cm</strong>`)
          .addTo(state.layers.incidents);
      });
      pending.forEach((p) => {
        L.circleMarker([p.lat, p.lon], {
          radius: 6.5,
          color: "#D97706",
          dashArray: "3 3",
          fillColor: "#F59E0B",
          fillOpacity: 0.72,
          weight: 2,
        })
          .bindPopup(`<strong>${escapeHtml(p.location_label)}</strong> (Chờ xác thực · 1 tin)<br/>Độ sâu: <strong>${Math.round(p.avg_depth_cm)} cm</strong>`)
          .addTo(state.layers.incidents);
      });
    }
  }

  async function voteIncident(action, item) {
    if (state.incidentDemoMode) {
      showToast("Đang xem dữ liệu mẫu minh họa. Bấm 'Quay về Dữ liệu thật' để tương tác trực tiếp.");
      return;
    }
    const author = ($("#incident-author")?.value || "").trim() || "Người dân Cần Thơ";
    if ($("#incident-author")?.value.trim()) {
      localStorage.setItem("cantho_reporter_name", author);
    }

    try {
      const res = await fetch("/api/incidents/vote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          lat: item.lat,
          lon: item.lon,
          reported_depth_cm: item.avg_depth_cm || 22,
          location_hint: item.location_label || "TP. Cần Thơ",
          reporter_name: author,
          district: item.district || "ninh_kieu",
          category: item.category || "flood_deep",
          website: $("#incident-hp")?.value || "",
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.error || `HTTP ${res.status}`);
      }
      const data = await res.json();
      state.latestIncidentsData = data;
      renderIncidents(data);
      broadcastRealtimeUpdate(`vote:${action}`);
      showToast(action === "clear" ? "Đã ghi nhận xác nhận nước đã rút." : "Đã +1 xác nhận điểm ngập.");
      if (state.weatherData && !state.isSimulationOverride) {
        await evaluateLiveFloodTruth(state.weatherData);
      }
      loadCameras();
    } catch (err) {
      showToast(`Không gửi được bình chọn: ${err.message}`);
    }
  }

  async function submitComment(reportId, rawContent) {
    if (state.incidentDemoMode) {
      showToast("Bình luận trực tiếp chỉ bật ở chế độ Dữ liệu thật.");
      return;
    }
    const content = String(rawContent || "").trim();
    if (content.length < 2) {
      showToast("Vui lòng nhập nội dung bình luận.");
      return;
    }
    const author = ($("#incident-author")?.value || "").trim() || "Người dân Cần Thơ";
    if ($("#incident-author")?.value.trim()) {
      localStorage.setItem("cantho_reporter_name", author);
    }
    try {
      const res = await fetch("/api/incidents/comment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          report_id: reportId,
          author_name: author,
          content,
          website: $("#incident-hp")?.value || "",
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.error || `HTTP ${res.status}`);
      }
      state.expandedComments[reportId] = true;
      const data = await res.json();
      state.latestIncidentsData = data;
      renderIncidents(data);
      broadcastRealtimeUpdate("comment");
      showToast("Đã gửi bình luận.");
    } catch (err) {
      showToast(`Lỗi bình luận: ${err.message}`);
    }
  }

  async function submitIncident() {
    if (state.incidentDemoMode) {
      state.incidentDemoMode = false;
    }
    const author = ($("#incident-author")?.value || "").trim() || "Người dân Cần Thơ";
    if ($("#incident-author")?.value.trim()) {
      localStorage.setItem("cantho_reporter_name", author);
    }

    const nodeId = $("#incident-node-select")?.value;
    const nodeObj = (state.initData?.geo?.nodes || []).find((n) => n.id === nodeId);
    const distProfile = (state.weatherData?.districts || []).find((d) => d.id === ($("#incident-district")?.value || "ninh_kieu"));
    const coord = state.pinnedCoord
      || (nodeObj ? { lat: nodeObj.lat, lon: nodeObj.lon } : null)
      || (distProfile ? { lat: distProfile.lat, lon: distProfile.lon } : { lat: 10.0322, lon: 105.7748 });

    const depth = Number($("#incident-depth")?.value ?? 25);
    const fallbackLabel = nodeObj?.name || (distProfile ? `Trung tâm ${distProfile.name}` : "Điểm phản ánh trên bản đồ");
    const hint = ($("#incident-location-hint")?.value || "").trim() || fallbackLabel;
    const note = ($("#incident-note")?.value || "").trim() || `${CATEGORY_LABELS[state.incidentCategory] || "Tình trạng"} tại ${hint}`;
    const hp = $("#incident-hp")?.value || "";
    const district = $("#incident-district")?.value || "ninh_kieu";

    try {
      const res = await fetch("/api/incidents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat: coord.lat,
          lon: coord.lon,
          reported_depth_cm: depth,
          location_hint: hint,
          description: note,
          reporter_name: author,
          category: state.incidentCategory,
          district,
          photo_data: state.incidentPhotoData,
          website: hp,
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.error || `HTTP ${res.status}`);
      }
      const data = await res.json();
      state.latestIncidentsData = data;
      renderIncidents(data);
      broadcastRealtimeUpdate("report");
      if ($("#incident-note")) $("#incident-note").value = "";
      state.incidentPhotoData = "";
      $("#composer-photo-preview-wrap")?.classList.add("hidden");
      if ($("#incident-photo-input")) $("#incident-photo-input").value = "";
      state.pinnedCoord = null;
      updateIncidentCoordStatus();
      showToast("Đã đăng bài lên Bảng tin Cộng đồng.");
      if (state.weatherData && !state.isSimulationOverride) {
        await evaluateLiveFloodTruth(state.weatherData);
      }
      loadCameras();
    } catch (err) {
      showToast(`Không gửi được báo cáo: ${err.message}`);
    }
  }

  /* --------------------------------------------------------------------------
     8-Station Road Camera Hub & Lightbox Modals
     -------------------------------------------------------------------------- */
  async function loadCameras() {
    try {
      const cur = state.weatherData?.current || {};
      const astro = state.weatherData?.astronomy_and_tide || {};
      const useSim = state.isSimulationOverride && state.activeSimData?.inputs;
      const tideVal = useSim ? Number(state.activeSimData.inputs.tide_level_m ?? 1.45) : Number(astro.current_tide_m ?? 1.45);
      const rainVal = useSim ? Number(state.activeSimData.inputs.rain_mm_h ?? 0) : Number(cur.precip_mm ?? 0);
      const qs = new URLSearchParams({
        tide_level_m: String(tideVal),
        rain_mm_h: String(rainVal),
      });
      const res = await fetch(`/api/cameras?${qs}`);
      if (!res.ok) return;
      const data = await res.json();
      state.camerasData = data;
      renderCamerasHub(data);
      renderCamerasOnMap(data.cameras || []);
    } catch (err) {
      console.warn("Camera load error:", err);
    }
  }

  function renderCameraViewerHtml(cam) {
    if (!cam) return "";
    const imgSrc = cam.ioc_feed_url || cam.latest_community_photo || "";
    const statusLabel = cam.ioc_status === "connected" ? "IOC LIVE STREAM" : (cam.latest_community_photo ? "ẢNH CỘNG ĐỒNG TẠI NGÃ TƯ" : "CHỜ KẾT NỐI API IOC");
    const simTag = state.isSimulationOverride ? "MÔ PHỎNG · " : "";
    const floodLabel = cam.ai_max_depth_cm >= 18 ? `${simTag}Ngập sâu ~${cam.ai_max_depth_cm} cm` : (cam.ai_max_depth_cm >= 8 ? `${simTag}Ngập nhẹ ~${cam.ai_max_depth_cm} cm` : `${simTag}Mặt đường thông thoáng`);
    const standbyHtml = imgSrc
      ? `<img src="${imgSrc}" alt="${escapeHtml(cam.name)}" />`
      : `<div class="camera-standby-placeholder">
           <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="square"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
           <strong>Chưa có ảnh trực tiếp từ IOC Cần Thơ</strong>
           <span>Mở Google Street View hoặc góp ảnh góc đường bên dưới</span>
         </div>`;
    return `
      <div class="camera-feed-frame">
        ${standbyHtml}
        <div class="camera-hud-overlay">
          <span class="camera-hud-tag">${escapeHtml(statusLabel)}</span>
          <span class="camera-hud-tag">${escapeHtml(floodLabel)}</span>
        </div>
        <div class="camera-hud-bottom">
          <div><strong>${escapeHtml(cam.street_label)}</strong> (${escapeHtml(cam.district_name)})</div>
          <div>${escapeHtml(cam.direction_note)}</div>
          <div>Triều Sông Hậu: ${cam.tide_level_m}m · Mưa: ${cam.rain_mm_h} mm/h · ${(cam.linked_road_names || []).slice(0, 2).join(" / ")}</div>
          ${cam.latest_community_note ? `<div>Ảnh mới nhất (${escapeHtml(cam.latest_community_author)}): "${escapeHtml(cam.latest_community_note)}"</div>` : ""}
        </div>
      </div>
      <div class="camera-viewer-meta">
        <div class="camera-viewer-actions">
          <a class="btn-utility-sharp" href="${escapeHtml(cam.google_street_view_url)}" target="_blank" rel="noopener noreferrer">Mở Google Street View</a>
          <button type="button" class="btn-utility-sharp btn-cam-contrib" data-cam-id="${escapeHtml(cam.id)}">Góp ảnh góc đường này</button>
          <button type="button" class="btn-utility-sharp btn-cam-map" data-cam-id="${escapeHtml(cam.id)}">Xem trên bản đồ</button>
        </div>
      </div>
    `;
  }

  function wireCameraActionButtons(container, cam) {
    $(".btn-cam-contrib", container)?.addEventListener("click", () => {
      $("#camera-modal")?.classList.add("hidden");
      switchMode("eval");
      state.pinnedCoord = { lat: cam.lat, lon: cam.lon };
      if ($("#incident-district") && cam.district) {
        $("#incident-district").value = cam.district;
        populateIncidentNodesForDistrict(cam.district, cam.node_id);
      } else if ($("#incident-node-select") && cam.node_id) {
        $("#incident-node-select").value = cam.node_id;
      }
      if ($("#incident-location-hint")) {
        $("#incident-location-hint").value = cam.street_label;
        $("#incident-location-hint").dataset.autoFilled = "true";
      }
      updateIncidentCoordStatus(`Góc Camera đã chọn: ${cam.street_label}`);
      $("#social-composer")?.scrollIntoView({ behavior: "smooth", block: "start" });
      $("#incident-photo-input")?.click();
    });
    $(".btn-cam-map", container)?.addEventListener("click", () => {
      $("#camera-modal")?.classList.add("hidden");
      switchMode("flood");
      setTimeout(() => state.map?.setView([cam.lat, cam.lon], 16, { animate: false }), 80);
    });
  }

  function renderCamerasHub(data) {
    if ($("#camera-ioc-notice")) $("#camera-ioc-notice").textContent = data.ioc_notice || "";
    if ($("#camera-ioc-status-pill")) {
      $("#camera-ioc-status-pill").textContent = data.ioc_connected_count > 0 ? `${data.ioc_connected_count}/8 IOC Live` : "8 Trạm · Sẵn sàng IOC";
    }
    const cams = data.cameras || [];
    const activeCam = cams.find((c) => c.id === state.selectedCameraId) || cams[0];
    const viewer = $("#camera-inline-viewer");
    if (viewer && activeCam) {
      viewer.innerHTML = renderCameraViewerHtml(activeCam);
      wireCameraActionButtons(viewer, activeCam);
    }

    const list = $("#camera-station-list");
    if (list) {
      list.innerHTML = cams
        .map((c) => `
          <button type="button" class="camera-station-btn ${c.id === activeCam?.id ? "active" : ""}" data-cam-id="${escapeHtml(c.id)}">
            <strong>${escapeHtml(c.name)}</strong>
            <span>${escapeHtml(c.district_name)} · ${c.ai_max_depth_cm >= 8 ? `~${c.ai_max_depth_cm}cm` : "Khô ráo"}${c.latest_community_photo ? " · Có ảnh" : ""}</span>
          </button>
        `)
        .join("");

      $$(".camera-station-btn", list).forEach((btn) => {
        btn.addEventListener("click", () => {
          state.selectedCameraId = btn.dataset.camId;
          renderCamerasHub(data);
        });
      });
    }
  }

  function openCameraModal(camId) {
    const cams = state.camerasData?.cameras || [];
    const cam = cams.find((c) => c.id === camId) || cams[0];
    if (!cam) return;
    if ($("#camera-modal-title")) $("#camera-modal-title").textContent = cam.name;
    const body = $("#camera-modal-body");
    if (body) {
      body.innerHTML = `<div class="camera-viewer-card">${renderCameraViewerHtml(cam)}</div>`;
      wireCameraActionButtons(body, cam);
    }
    $("#camera-modal")?.classList.remove("hidden");
  }

  function renderCamerasOnMap(cameras = []) {
    if (!state.map || !state.layers.cameras) return;
    state.layers.cameras.clearLayers();
    cameras.forEach((cam, idx) => {
      const icon = L.divIcon({
        className: "camera-pin-wrap",
        html: `<div class="camera-map-pin ${cam.severity || ""}">CAM ${String(idx + 1).padStart(2, "0")}</div>`,
        iconSize: [0, 0],
        iconAnchor: [24, 24],
      });
      const marker = L.marker([cam.lat, cam.lon], { icon, zIndexOffset: 400 }).addTo(state.layers.cameras);
      marker.bindTooltip(`${cam.street_label} (${cam.ai_max_depth_cm} cm)`, { direction: "top", offset: [0, -10] });
      marker.on("click", () => openCameraModal(cam.id));
    });
  }

  function openPhotoLightbox(src, caption) {
    const lb = $("#photo-lightbox");
    const img = $("#lightbox-img");
    const cap = $("#lightbox-caption");
    if (!lb || !img) return;
    img.src = src;
    if (cap) cap.textContent = caption || "Ảnh hiện trường cộng đồng";
    lb.classList.remove("hidden");
  }

  async function fetchGeocodeSuggestions(query, popular = false) {
    if (popular && state.popularPlacesCache) return state.popularPlacesCache;
    const url = popular || !query || query.trim().length < 2
      ? "/api/geocode?popular=1"
      : `/api/geocode?q=${encodeURIComponent(query.trim())}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const hits = data.results || [];
    if ((popular || !query || query.trim().length < 2) && hits.length) {
      state.popularPlacesCache = hits;
    }
    return hits;
  }

  function bindRouteCombobox(role) {
    const isStart = role === "start";
    const input = $(isStart ? "#route-start-input" : "#route-end-input");
    const listEl = $(isStart ? "#combo-start-list" : "#combo-end-list");
    const clearBtn = $(isStart ? "#btn-clear-start" : "#btn-clear-end");
    if (!input || !listEl) return;

    let currentHits = [];
    let activeIdx = -1;
    let debounceTimer = null;

    const closeDropdown = () => {
      listEl.classList.add("hidden");
      activeIdx = -1;
    };

    const selectHit = (hit) => {
      if (!hit) return;
      const endpoint = {
        nodeId: hit.node_id || null,
        lat: Number(hit.lat),
        lon: Number(hit.lon),
        label: String(hit.label || "Điểm chọn"),
        custom: !hit.node_id,
      };
      if (isStart) state.routeStart = endpoint;
      else state.routeEnd = endpoint;
      input.value = endpoint.label;
      closeDropdown();
      updateCustomEndpointsUI();
      runFloodRoute();
    };

    const renderOptions = (hits) => {
      currentHits = hits || [];
      activeIdx = currentHits.length ? 0 : -1;
      if (!currentHits.length) {
        listEl.innerHTML = `<div class="combo-option"><span class="combo-option-title">Không tìm thấy địa điểm tại Cần Thơ</span><span class="combo-option-meta">Thử tên đường, quận hoặc bấm "Chấm trên map"</span></div>`;
        listEl.classList.remove("hidden");
        return;
      }
      listEl.innerHTML = currentHits
        .map((h, i) => `
          <div class="combo-option ${i === 0 ? "active" : ""}" role="option" data-idx="${i}">
            <div class="combo-option-top">
              <span class="combo-option-title">${escapeHtml(h.label)}</span>
              <span class="combo-cat-tag">${escapeHtml(h.category_label || "Địa điểm")}</span>
            </div>
            <div class="combo-option-meta">${escapeHtml(h.subtitle || "TP. Cần Thơ")}</div>
          </div>
        `)
        .join("");
      listEl.classList.remove("hidden");

      $$(".combo-option", listEl).forEach((opt) => {
        opt.addEventListener("mousedown", (e) => {
          e.preventDefault();
          const hit = currentHits[Number(opt.dataset.idx)];
          selectHit(hit);
        });
      });
    };

    const loadList = async (q, popular = false) => {
      try {
        const hits = await fetchGeocodeSuggestions(q, popular);
        renderOptions(hits);
      } catch (err) {
        showToast(`Lỗi tìm địa chỉ: ${err.message}`);
      }
    };

    const openFromInput = () => {
      const curLbl = (isStart ? state.routeStart?.label : state.routeEnd?.label) || "";
      const val = input.value.trim();
      if (window.innerWidth <= 820) {
        input.closest(".route-combo-row")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      if (!val || val === curLbl) {
        loadList("", true);
      } else {
        loadList(val, false);
      }
    };

    input.addEventListener("focus", openFromInput);
    input.addEventListener("click", () => {
      if (listEl.classList.contains("hidden")) openFromInput();
    });

    input.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      const val = input.value.trim();
      debounceTimer = setTimeout(() => {
        if (val.length < 2) loadList("", true);
        else loadList(val, false);
      }, 140);
    });

    input.addEventListener("keydown", (e) => {
      const opts = $$(".combo-option", listEl);
      if (e.key === "ArrowDown" && currentHits.length) {
        e.preventDefault();
        activeIdx = (activeIdx + 1) % currentHits.length;
        opts.forEach((x, idx) => x.classList.toggle("active", idx === activeIdx));
      } else if (e.key === "ArrowUp" && currentHits.length) {
        e.preventDefault();
        activeIdx = (activeIdx - 1 + currentHits.length) % currentHits.length;
        opts.forEach((x, idx) => x.classList.toggle("active", idx === activeIdx));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (currentHits.length && !listEl.classList.contains("hidden")) {
          selectHit(currentHits[Math.max(0, activeIdx)]);
        } else {
          loadList(input.value.trim(), input.value.trim().length < 2);
        }
      } else if (e.key === "Escape") {
        closeDropdown();
      }
    });

    input.addEventListener("blur", () => {
      setTimeout(() => {
        if (document.activeElement === input) return;
        closeDropdown();
        const curLbl = (isStart ? state.routeStart?.label : state.routeEnd?.label) || "";
        if (curLbl) input.value = curLbl;
      }, 160);
    });

    clearBtn?.addEventListener("mousedown", (e) => {
      e.preventDefault();
      input.value = "";
      input.focus();
      loadList("", true);
    });
  }

  function updateIncidentCoordStatus(customText = "") {
    const statusEl = $("#composer-coord-status");
    const resetBtn = $("#btn-reset-incident-pin");
    if (statusEl) {
      if (customText) {
        statusEl.textContent = customText;
      } else if (state.pinnedCoord) {
        statusEl.textContent = `Tọa độ đã chọn: ${state.pinnedCoord.lat.toFixed(4)}°N, ${state.pinnedCoord.lon.toFixed(4)}°E`;
      } else {
        const sel = $("#incident-node-select");
        const name = sel?.options[sel.selectedIndex]?.textContent || "giao lộ đã chọn";
        statusEl.textContent = `Vị trí đăng: ${name}`;
      }
    }
    if (resetBtn) resetBtn.classList.toggle("hidden", !state.pinnedCoord);
  }

  function populateIncidentNodesForDistrict(districtId, preferredNodeId = null) {
    const incSel = $("#incident-node-select");
    if (!incSel) return;
    const nodes = state.initData?.geo?.nodes || [];
    const distName = DISTRICT_LABELS[districtId] || "";
    const matched = distName ? nodes.filter((n) => n.district === distName) : nodes;

    let optionsHtml = "";
    if (matched.length) {
      optionsHtml = matched.map((n) => `<option value="${escapeHtml(n.id)}">${escapeHtml(n.name)}</option>`).join("");
    } else {
      const centerOpt = `<option value="__district_center__">Trung tâm ${escapeHtml(distName || "Quận/Huyện")}</option>`;
      optionsHtml = centerOpt + nodes.map((n) => `<option value="${escapeHtml(n.id)}">${escapeHtml(n.name)}</option>`).join("");
    }
    incSel.innerHTML = optionsHtml;
    if (preferredNodeId && Array.from(incSel.options).some((o) => o.value === preferredNodeId)) {
      incSel.value = preferredNodeId;
    }

    const hintEl = $("#incident-location-hint");
    const selText = incSel.options[incSel.selectedIndex]?.textContent || "";
    if (hintEl && (!hintEl.value.trim() || hintEl.dataset.autoFilled === "true")) {
      hintEl.value = selText;
      hintEl.dataset.autoFilled = "true";
    }
    updateIncidentCoordStatus();
  }

  function renderInitData(data) {
    state.initData = data;
    const geo = data.geo || {};
    const nodes = geo.nodes || [];
    populateIncidentNodesForDistrict($("#incident-district")?.value || "ninh_kieu");

    const defStart = nodes.find((n) => n.id === "dhct_khu2") || nodes[0];
    const defEnd = nodes.find((n) => n.id === "ben_ninh_kieu") || nodes[Math.min(11, Math.max(0, nodes.length - 1))];
    if (defStart && !state.routeStart.custom && (!state.routeStart.nodeId || state.routeStart.nodeId === defStart.id)) {
      state.routeStart = { nodeId: defStart.id, lat: defStart.lat, lon: defStart.lon, label: defStart.name, custom: false };
    }
    if (defEnd && !state.routeEnd.custom && (!state.routeEnd.nodeId || state.routeEnd.nodeId === defEnd.id)) {
      state.routeEnd = { nodeId: defEnd.id, lat: defEnd.lat, lon: defEnd.lon, label: defEnd.name, custom: false };
    }
    updateCustomEndpointsUI();

    const scenEl = $("#scenario-list");
    if (scenEl) {
      scenEl.innerHTML = (data.scenarios || [])
        .map((sc) => `<button type="button" class="scenario-chip" data-scen="${escapeHtml(sc.id)}">${escapeHtml(sc.name)}</button>`)
        .join("");

      $$(".scenario-chip", scenEl).forEach((btn) => {
        btn.addEventListener("click", () => {
          $$(".scenario-chip", scenEl).forEach((x) => x.classList.remove("active"));
          btn.classList.add("active");
          const sc = (data.scenarios || []).find((x) => x.id === btn.dataset.scen);
          if (!sc) return;
          const descEl = $("#scenario-desc-note");
          if (descEl) {
            descEl.textContent = sc.description || "";
            descEl.classList.toggle("hidden", !sc.description);
          }
          state.isSimulationOverride = true;
          $("#slider-tide").value = Number(sc.tide_level_m).toFixed(2);
          $("#slider-rain").value = Math.round(sc.rain_mm_h);
          $("#slider-rain3h").value = Math.round(sc.rain_3h_cum);
          $("#slider-delta").value = Number(sc.tide_delta_m).toFixed(2);
          $("#chk-spring").checked = Boolean(sc.is_spring_tide);
          updateSliderLabels();
          runCustomSimulation().then(() => {
            runFloodRoute();
            loadCameras();
          });
        });
      });
    }

    const vehEl = $("#vehicle-grid");
    if (vehEl) {
      vehEl.innerHTML = (data.vehicles || [])
        .map(
          (v, idx) => `
          <button type="button" class="vehicle-card ${idx === 0 ? "active" : ""}" data-veh="${escapeHtml(v.id)}">
            ${vehicleIconFor(v.id)}
            <strong>${escapeHtml(v.name.split("(")[0].replace("Ô tô gầm cao ", "Ô tô ").trim())}</strong>
            <span>&le; ${v.max_safe_depth_cm}cm</span>
          </button>
        `
        )
        .join("");

      $$(".vehicle-card", vehEl).forEach((btn) => {
        btn.addEventListener("click", () => {
          $$(".vehicle-card", vehEl).forEach((x) => x.classList.remove("active"));
          btn.classList.add("active");
          state.selectedVehicleId = btn.dataset.veh;
          runFloodRoute();
        });
      });
    }

    const mlEval = data.ml_evaluation || {};
    if ($("#eval-dataset-badge")) $("#eval-dataset-badge").textContent = `${mlEval.dataset_stats?.total_records || 2400} mẫu`;
    const tbody = $("#ml-table-body");
    if (tbody) {
      tbody.innerHTML = (mlEval.benchmarks || [])
        .map(
          (b) => `
          <tr>
            <td>${escapeHtml(b.horizon)}</td>
            <td>${escapeHtml(b.model)}</td>
            <td>${Number(b.mae_cm).toFixed(2)}</td>
            <td>${Number(b.rmse_cm).toFixed(2)}</td>
            <td>${Number(b.r2).toFixed(3)}</td>
            <td>${Number(b.f1_alert).toFixed(3)}</td>
          </tr>
        `
        )
        .join("");
    }

    const attrEl = $("#attribution-list");
    if (attrEl) {
      attrEl.innerHTML = Object.entries(mlEval.feature_attribution || {})
        .map(([zone, factors]) => {
          const rows = Object.entries(factors)
            .map(
              ([name, pct]) => `
              <div class="attr-row">
                <span>${escapeHtml(name)}</span>
                <div class="attr-bar-bg"><div class="attr-bar-fill" style="width:${Number(pct)}%"></div></div>
                <strong>${Number(pct).toFixed(0)}%</strong>
              </div>
            `
            )
            .join("");
          return `<div class="attr-zone"><div class="attr-zone-title">${escapeHtml(zone)}</div>${rows}</div>`;
        })
        .join("");
    }

    const reconEl = $("#github-recon-list");
    if (reconEl) {
      reconEl.innerHTML = (data.github_recon_patterns || [])
        .map(
          (g) => `
          <div class="recon-item">
            <a href="${escapeHtml(g.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(g.repo)}</a>
            <p>${escapeHtml(g.applied_pattern)}</p>
          </div>
        `
        )
        .join("");
    }
  }

  /* --------------------------------------------------------------------------
     Mode Switching, Theme Mood & Controls
     -------------------------------------------------------------------------- */
  function switchMode(mode) {
    if (mode !== "flood") {
      stopAllMapPinning(false);
    }
    state.mode = mode;
    document.body.dataset.activeMode = mode;

    $$(".mode-btn").forEach((btn) => {
      const active = btn.dataset.mode === mode;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", String(active));
    });

    $("#panel-flood")?.classList.toggle("hidden", mode !== "flood");
    $("#panel-eval")?.classList.toggle("hidden", mode !== "eval");

    if (mode === "flood" || mode === "eval") {
      requestAnimationFrame(() => {
        if (mode === "flood") {
          state.map?.invalidateSize();
          runFloodRoute();
          loadCameras();
        }
        if (mode === "eval") {
          loadIncidents();
          loadCameras();
        }
      });
    } else if (mode === "weather") {
      state.layers.route?.clearLayers();
      state.activeRouteLabelCoords = [];
      if (state.activeSimData?.prediction?.roads) {
        renderRoadNetworkOnMap(state.activeSimData.prediction.roads);
      }
      renderSatelliteLandmarks();
    }
  }

  function wireControls() {
    $$(".mode-btn").forEach((btn) => btn.addEventListener("click", () => switchMode(btn.dataset.mode)));

    $("#btn-goto-flood-route")?.addEventListener("click", () => switchMode("flood"));
    $("#btn-goto-report-flood")?.addEventListener("click", () => switchMode("eval"));
    $("#btn-open-map-portal")?.addEventListener("click", () => switchMode("flood"));
    $$(".purpose-card").forEach((card) => {
      card.addEventListener("click", () => {
        const purpose = card.dataset.purpose;
        if (purpose === "weather") {
          $("#live-flood-card")?.scrollIntoView({ behavior: "smooth", block: "center" });
        } else if (purpose === "flood") {
          switchMode("flood");
        } else if (purpose === "eval") {
          switchMode("eval");
        }
      });
    });
    $("#hydro-details-drawer")?.addEventListener("toggle", (e) => {
      if (e.target.open) {
        requestAnimationFrame(() => state.r3f.resize?.());
      }
    });
    $("#btn-refresh-live")?.addEventListener("click", () => loadLiveWeather(state.selectedDistrictId, true));
    $("#btn-reset-to-live")?.addEventListener("click", resetToLiveConditions);

    // Theme Toggle: Light <-> Dark
    $("#btn-theme-mood")?.addEventListener("click", () => {
      state.mood = state.mood === "vellum" ? "nocturne" : "vellum";
      document.body.dataset.mood = state.mood;
      const lbl = $("#mood-toggle-label");
      const ico = $("#mood-toggle-ico");
      const isNocturne = state.mood === "nocturne";
      if (lbl) lbl.textContent = isNocturne ? "Sáng" : "Tối";
      if (ico) {
        ico.innerHTML = isNocturne
          ? `<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>`
          : `<path d="M20.35 15.35A9 9 0 0 1 8.65 3.65a9 9 0 1 0 11.7 11.7Z"/>`;
      }
      if (state.weatherData) renderHourlyChartSVG(state.weatherData.hourly_24h || []);
      if (state.activeSimData) renderHydrographSVG(state.activeSimData.hydrograph_24h || []);
      updateThreeWeatherState(state.weatherData);
    });

    // Card 1 Outlook Toggle: 24h <-> 7d
    $("#btn-outlook-24h")?.addEventListener("click", () => {
      $("#btn-outlook-24h")?.classList.add("active");
      $("#btn-outlook-7d")?.classList.remove("active");
      $("#btn-outlook-24h")?.setAttribute("aria-selected", "true");
      $("#btn-outlook-7d")?.setAttribute("aria-selected", "false");
      $("#outlook-24h-pane")?.classList.remove("hidden");
      $("#outlook-7d-pane")?.classList.add("hidden");
    });
    $("#btn-outlook-7d")?.addEventListener("click", () => {
      $("#btn-outlook-7d")?.classList.add("active");
      $("#btn-outlook-24h")?.classList.remove("active");
      $("#btn-outlook-7d")?.setAttribute("aria-selected", "true");
      $("#btn-outlook-24h")?.setAttribute("aria-selected", "false");
      $("#outlook-7d-pane")?.classList.remove("hidden");
      $("#outlook-24h-pane")?.classList.add("hidden");
    });

    $("#btn-swap-route")?.addEventListener("click", () => {
      const prevStart = state.routeStart;
      state.routeStart = state.routeEnd;
      state.routeEnd = prevStart;
      updateCustomEndpointsUI();
      runFloodRoute();
    });

    $("#btn-calc-flood-route")?.addEventListener("click", runFloodRoute);
    $("#btn-gps-nearest")?.addEventListener("click", locateUserNearestNode);
    $("#btn-share-route")?.addEventListener("click", shareCurrentRoute);
    $("#btn-open-gmaps")?.addEventListener("click", () => {
      if (!state.lastGoogleMapsUrl) {
        showToast("Hãy tính lộ trình trước khi mở Google Maps.");
        return;
      }
      window.open(state.lastGoogleMapsUrl, "_blank", "noopener,noreferrer");
    });

    $$("#horizon-tabs .seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        $$("#horizon-tabs .seg-btn").forEach((x) => x.classList.remove("active"));
        btn.classList.add("active");
        state.activeHorizon = btn.dataset.horizon;
        state.isSimulationOverride = hasNonLiveSimulationInputs();
        runCustomSimulation().then(() => {
          runFloodRoute();
          loadCameras();
        });
      });
    });

    $("#ml-model-select")?.addEventListener("change", () => {
      state.isSimulationOverride = hasNonLiveSimulationInputs();
      runCustomSimulation().then(() => {
        runFloodRoute();
        loadCameras();
      });
    });

    let sliderTimer = null;
    ["#slider-tide", "#slider-rain", "#slider-rain3h", "#slider-delta", "#chk-spring"].forEach((sel) => {
      $(sel)?.addEventListener("input", () => {
        $$("#scenario-list .scenario-chip").forEach((x) => x.classList.remove("active"));
        const descEl = $("#scenario-desc-note");
        if (descEl) {
          descEl.textContent = "";
          descEl.classList.add("hidden");
        }
        state.isSimulationOverride = true;
        updateSliderLabels();
        clearTimeout(sliderTimer);
        sliderTimer = setTimeout(() => {
          runCustomSimulation().then(() => {
            runFloodRoute();
            loadCameras();
          });
        }, 160);
      });
    });

    $$("#basemap-switcher .basemap-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.basemap;
        $$("#basemap-switcher .basemap-btn").forEach((x) => x.classList.remove("active"));
        btn.classList.add("active");
        if (state.map && state.basemaps[key]) {
          state.map.stop();
          Object.values(state.basemaps).forEach((l) => {
            if (state.map.hasLayer(l)) state.map.removeLayer(l);
          });
          state.basemaps[key].addTo(state.map);
          state.activeBasemap = key;
          renderSatelliteLandmarks();
          if (state.activeSimData?.prediction?.roads) {
            renderRoadNetworkOnMap(state.activeSimData.prediction.roads);
          }
        }
      });
    });

    const layerBtn = $("#btn-toggle-layers");
    const layerPop = $("#map-layers-popover");
    layerBtn?.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = layerPop.classList.contains("hidden");
      layerPop.classList.toggle("hidden", !willOpen);
      layerBtn.setAttribute("aria-expanded", String(willOpen));
    });
    document.addEventListener("click", (e) => {
      if (layerPop && !layerPop.classList.contains("hidden") && !layerPop.contains(e.target) && !layerBtn.contains(e.target)) {
        layerPop.classList.add("hidden");
        layerBtn?.setAttribute("aria-expanded", "false");
      }
    });

    const bindLayerToggle = (chkSel, layerKey) => {
      $(chkSel)?.addEventListener("change", (e) => {
        if (!state.map || !state.layers[layerKey]) return;
        if (e.target.checked) state.map.addLayer(state.layers[layerKey]);
        else state.map.removeLayer(state.layers[layerKey]);
        if (layerKey === "stations") {
          if (state.activeSimData?.prediction?.roads) renderRoadNetworkOnMap(state.activeSimData.prediction.roads);
          renderSatelliteLandmarks();
        }
      });
    };
    bindLayerToggle("#chk-layer-roads", "roads");
    bindLayerToggle("#chk-layer-cameras", "cameras");
    bindLayerToggle("#chk-layer-stations", "stations");
    bindLayerToggle("#chk-layer-incidents", "incidents");
    bindLayerToggle("#chk-layer-hex", "hex");

    // Ensure cameras layer respects default checked state
    if ($("#chk-layer-cameras")?.checked && state.map && state.layers.cameras) {
      state.map.addLayer(state.layers.cameras);
    }

    // Mode 02: Dual Smart A/B Comboboxes & Custom A/B Map Pinning
    bindRouteCombobox("start");
    bindRouteCombobox("end");

    $("#btn-toggle-sheet")?.addEventListener("click", () => {
      setSheetCollapsed(!state.sheetCollapsed);
    });
    $("#btn-cancel-map-pin")?.addEventListener("click", () => {
      stopAllMapPinning(true);
    });

    $("#btn-pin-start")?.addEventListener("click", () => {
      const active = state.pinTarget === "route_start";
      stopAllMapPinning();
      if (!active) {
        startMapPinning("route_start", "Đang chọn Điểm đi (A) — Nhấn vào bản đồ để đặt vị trí");
        showToast("Nhấn vào bất kỳ vị trí nào trên bản đồ để đặt Điểm đi (A).");
      }
    });
    $("#btn-pin-end")?.addEventListener("click", () => {
      const active = state.pinTarget === "route_end";
      stopAllMapPinning();
      if (!active) {
        startMapPinning("route_end", "Đang chọn Điểm đến (B) — Nhấn vào bản đồ để đặt vị trí");
        showToast("Nhấn vào bất kỳ vị trí nào trên bản đồ để đặt Điểm đến (B).");
      }
    });
    $("#btn-clear-custom-pins")?.addEventListener("click", () => {
      const nodes = state.initData?.geo?.nodes || [];
      const defA = nodes.find((n) => n.id === "dhct_khu2") || nodes[0];
      const defB = nodes.find((n) => n.id === "ben_ninh_kieu") || nodes[11];
      if (defA) state.routeStart = { nodeId: defA.id, lat: defA.lat, lon: defA.lon, label: defA.name, custom: false };
      if (defB) state.routeEnd = { nodeId: defB.id, lat: defB.lat, lon: defB.lon, label: defB.name, custom: false };
      if (state.layers.pinMarker && state.map) {
        state.map.removeLayer(state.layers.pinMarker);
        state.layers.pinMarker = null;
      }
      updateCustomEndpointsUI();
      runFloodRoute();
    });

    // Mode 03: Social Composer, Photo Compression, Demo Toggle & Camera/Lightbox Modals
    const savedName = localStorage.getItem("cantho_reporter_name");
    if (savedName && $("#incident-author")) $("#incident-author").value = savedName;

    $$("#composer-cat-tabs .cat-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        $$("#composer-cat-tabs .cat-tab").forEach((x) => x.classList.remove("active"));
        btn.classList.add("active");
        const cat = btn.dataset.cat;
        state.incidentCategory = cat;
        const depthInp = $("#incident-depth");
        if (depthInp) {
          depthInp.disabled = cat === "dry";
          if (cat === "dry" || cat === "traffic" || cat === "rain") depthInp.value = "0";
          else if (cat === "flood_light") depthInp.value = "12";
          else if (cat === "flood_deep") depthInp.value = "30";
        }
      });
    });

    $("#incident-district")?.addEventListener("change", (e) => {
      state.pinnedCoord = null;
      populateIncidentNodesForDistrict(e.target.value);
    });

    $("#incident-location-hint")?.addEventListener("input", (e) => {
      e.target.dataset.autoFilled = "false";
    });

    $("#incident-node-select")?.addEventListener("change", () => {
      state.pinnedCoord = null;
      const sel = $("#incident-node-select");
      const name = sel?.options[sel.selectedIndex]?.textContent || "Giao lộ đã chọn";
      const hintEl = $("#incident-location-hint");
      if (hintEl && (!hintEl.value.trim() || hintEl.dataset.autoFilled === "true")) {
        hintEl.value = name;
        hintEl.dataset.autoFilled = "true";
      }
      updateIncidentCoordStatus();
    });

    $("#btn-reset-incident-pin")?.addEventListener("click", () => {
      state.pinnedCoord = null;
      if (state.layers.pinMarker && state.map) {
        state.map.removeLayer(state.layers.pinMarker);
        state.layers.pinMarker = null;
      }
      updateIncidentCoordStatus();
    });

    $("#btn-post-gps")?.addEventListener("click", () => {
      if (!navigator.geolocation) {
        showToast("Trình duyệt không hỗ trợ GPS.");
        return;
      }
      showToast("Đang lấy tọa độ GPS bài đăng...");
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const lat = Number(pos.coords.latitude.toFixed(6));
          const lon = Number(pos.coords.longitude.toFixed(6));
          if (lat < 9.8 || lat > 10.3 || lon < 105.5 || lon > 106.0) {
            showToast("GPS hiện nằm ngoài TP. Cần Thơ — hãy chọn giao lộ Cần Thơ.");
            return;
          }
          state.pinnedCoord = { lat, lon };
          updateIncidentCoordStatus(`Tọa độ GPS của bạn: ${lat.toFixed(4)}°N, ${lon.toFixed(4)}°E`);
          showToast("Đã lấy tọa độ GPS thực tế vào bài đăng.");
        },
        () => showToast("Không lấy được GPS. Hãy chọn giao lộ hoặc chấm trên bản đồ.")
      );
    });

    $("#btn-toggle-pin")?.addEventListener("click", () => {
      switchMode("flood");
      startMapPinning("incident", "Đang chọn vị trí Cộng đồng — Nhấn vào bản đồ Cần Thơ");
      showToast("Nhấn vào điểm trên bản đồ Cần Thơ — hệ thống sẽ tự quay lại Cộng đồng.");
    });

    $("#incident-photo-input")?.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const compressed = await compressImageFile(file);
        state.incidentPhotoData = compressed;
        if ($("#composer-photo-preview")) $("#composer-photo-preview").src = compressed;
        $("#composer-photo-preview-wrap")?.classList.remove("hidden");
        showToast("Đã nén ảnh hiện trường sẵn sàng đăng.");
      } catch (err) {
        showToast(err.message || "Không xử lý được ảnh.");
      }
    });

    $("#btn-remove-photo")?.addEventListener("click", () => {
      state.incidentPhotoData = "";
      if ($("#incident-photo-input")) $("#incident-photo-input").value = "";
      $("#composer-photo-preview-wrap")?.classList.add("hidden");
    });

    $("#btn-submit-incident")?.addEventListener("click", submitIncident);

    $("#btn-toggle-incident-demo")?.addEventListener("click", () => {
      state.incidentDemoMode = !state.incidentDemoMode;
      loadIncidents();
    });
    $("#btn-exit-incident-demo")?.addEventListener("click", () => {
      state.incidentDemoMode = false;
      loadIncidents();
    });

    $$("#feed-filter-tabs .seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        $$("#feed-filter-tabs .seg-btn").forEach((x) => x.classList.remove("active"));
        btn.classList.add("active");
        state.feedFilter = btn.dataset.filter || "all";
        if (state.latestIncidentsData) renderIncidents(state.latestIncidentsData);
      });
    });

    $("#feed-district-filter")?.addEventListener("change", (e) => {
      state.feedDistrictFilter = e.target.value || "all";
      if (state.latestIncidentsData) renderIncidents(state.latestIncidentsData);
    });

    $("#btn-close-camera-modal")?.addEventListener("click", () => $("#camera-modal")?.classList.add("hidden"));
    $("#camera-modal-backdrop")?.addEventListener("click", () => $("#camera-modal")?.classList.add("hidden"));
    $("#btn-close-lightbox")?.addEventListener("click", () => $("#photo-lightbox")?.classList.add("hidden"));
    $("#photo-lightbox-backdrop")?.addEventListener("click", () => $("#photo-lightbox")?.classList.add("hidden"));

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        $("#camera-modal")?.classList.add("hidden");
        $("#photo-lightbox")?.classList.add("hidden");
        if (state.pinTarget || state.pinMode) stopAllMapPinning(true);
      }
    });
  }

  /* --------------------------------------------------------------------------
     GPS Geolocation & Shareable Route Deep-Linking
     -------------------------------------------------------------------------- */
  function haversineKm(lat1, lon1, lat2, lon2) {
    const toRad = (deg) => (deg * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 6371 * (2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
  }

  function locateUserNearestNode() {
    if (!navigator.geolocation) {
      showToast("Trình duyệt không hỗ trợ định vị GPS.");
      return;
    }
    const nodes = state.initData?.geo?.nodes || [];
    if (!nodes.length) {
      showToast("Đang nạp danh sách điểm giao lộ Cần Thơ...");
      return;
    }
    showToast("Đang lấy vị trí GPS...");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const uLat = pos.coords.latitude;
        const uLon = pos.coords.longitude;
        let bestNode = nodes[0];
        let bestKm = Infinity;
        nodes.forEach((n) => {
          const dist = haversineKm(uLat, uLon, Number(n.lat), Number(n.lon));
          if (dist < bestKm) {
            bestKm = dist;
            bestNode = n;
          }
        });

        if (bestKm <= 25) {
          state.routeStart = {
            nodeId: null,
            lat: Number(uLat.toFixed(6)),
            lon: Number(uLon.toFixed(6)),
            label: `Vị trí GPS của tôi (${bestNode ? `gần ${bestNode.name}` : "Cần Thơ"})`,
            custom: true,
          };
          showToast(`Đã đặt Điểm đi A tại vị trí GPS của bạn.`);
        } else if (bestNode) {
          state.routeStart = {
            nodeId: bestNode.id,
            lat: bestNode.lat,
            lon: bestNode.lon,
            label: bestNode.name,
            custom: false,
          };
          showToast(`Vị trí ngoài Cần Thơ (~${bestKm.toFixed(0)} km), tạm đặt gần ${bestNode.name}.`);
        }
        updateCustomEndpointsUI();

        if (state.map) {
          if (state.layers.pinMarker) state.map.removeLayer(state.layers.pinMarker);
          const gpsIcon = L.divIcon({
            className: "gps-pin-wrap",
            html: `<div class="gps-user-pin"></div>`,
            iconSize: [14, 14],
            iconAnchor: [7, 7],
          });
          state.layers.pinMarker = L.marker([uLat, uLon], { icon: gpsIcon })
            .bindTooltip("Vị trí GPS của bạn")
            .addTo(state.map);
        }

        if (bestKm <= 25) {
          /* already toasted above */
        }
        runFloodRoute();
      },
      () => {
        showToast("Không lấy được quyền GPS. Vui lòng chọn Điểm đi thủ công.");
      },
      { enableHighAccuracy: true, timeout: 7000 }
    );
  }

  function shareCurrentRoute() {
    const url = new URL(window.location.href);
    url.searchParams.set("mode", "flood");
    if (state.routeStart?.nodeId && !state.routeStart.custom) {
      url.searchParams.set("from", state.routeStart.nodeId);
      url.searchParams.delete("alat");
      url.searchParams.delete("alon");
    } else if (state.routeStart?.lat != null) {
      url.searchParams.set("alat", String(state.routeStart.lat));
      url.searchParams.set("alon", String(state.routeStart.lon));
    }
    if (state.routeEnd?.nodeId && !state.routeEnd.custom) {
      url.searchParams.set("to", state.routeEnd.nodeId);
      url.searchParams.delete("blat");
      url.searchParams.delete("blon");
    } else if (state.routeEnd?.lat != null) {
      url.searchParams.set("blat", String(state.routeEnd.lat));
      url.searchParams.set("blon", String(state.routeEnd.lon));
    }
    url.searchParams.set("veh", state.selectedVehicleId || "scooter");

    window.history.replaceState({}, "", url.toString());
    if (navigator.clipboard?.writeText) {
      navigator.clipboard
        .writeText(url.toString())
        .then(() => showToast("Đã sao chép liên kết lộ trình né ngập."))
        .catch(() => showToast("Đã cập nhật URL lộ trình trên thanh địa chỉ."));
    } else {
      showToast("Đã cập nhật URL lộ trình trên thanh địa chỉ.");
    }
  }

  function applyUrlRouteParams() {
    const params = new URLSearchParams(window.location.search);
    const mode = params.get("mode");
    const fromId = params.get("from");
    const toId = params.get("to");
    const alat = Number(params.get("alat"));
    const alon = Number(params.get("alon"));
    const blat = Number(params.get("blat"));
    const blon = Number(params.get("blon"));
    const vehId = params.get("veh");
    const nodes = state.initData?.geo?.nodes || [];

    if (Number.isFinite(alat) && Number.isFinite(alon) && alat >= 9.8 && alat <= 10.3 && alon >= 105.5 && alon <= 106.0) {
      state.routeStart = { nodeId: null, lat: alat, lon: alon, label: `Điểm A (${alat.toFixed(4)}, ${alon.toFixed(4)})`, custom: true };
    } else if (fromId) {
      const fn = nodes.find((n) => n.id === fromId);
      if (fn) state.routeStart = { nodeId: fn.id, lat: fn.lat, lon: fn.lon, label: fn.name, custom: false };
    }

    if (Number.isFinite(blat) && Number.isFinite(blon) && blat >= 9.8 && blat <= 10.3 && blon >= 105.5 && blon <= 106.0) {
      state.routeEnd = { nodeId: null, lat: blat, lon: blon, label: `Điểm B (${blat.toFixed(4)}, ${blon.toFixed(4)})`, custom: true };
    } else if (toId) {
      const tn = nodes.find((n) => n.id === toId);
      if (tn) state.routeEnd = { nodeId: tn.id, lat: tn.lat, lon: tn.lon, label: tn.name, custom: false };
    }
    updateCustomEndpointsUI();
    if (vehId) {
      const vehBtn = $(`.vehicle-card[data-veh="${CSS.escape(vehId)}"]`);
      if (vehBtn) {
        $$(".vehicle-card").forEach((x) => x.classList.remove("active"));
        vehBtn.classList.add("active");
        state.selectedVehicleId = vehId;
      }
    }
    if (mode === "flood" || mode === "eval") {
      switchMode(mode);
    }
  }

  /* --------------------------------------------------------------------------
     Sub-Second Real-Time Sync (SSE Push + Delta Polling + BroadcastChannel)
     -------------------------------------------------------------------------- */
  function updateRealtimePill(isSseLive) {
    const pill = $("#live-source-pill");
    if (!pill) return;
    pill.classList.toggle("sse-live", Boolean(isSseLive));
    pill.title = isSseLive
      ? "Kết nối thời gian thực (SSE đẩy tức thời)"
      : "Đang đồng bộ dữ liệu thời gian thực";
  }

  function broadcastRealtimeUpdate(reason) {
    if (!state.realtime.bc) return;
    try {
      state.realtime.bc.postMessage({
        reason: reason || "local_mutation",
        ts: Date.now(),
      });
    } catch {
      /* ignore BroadcastChannel closed */
    }
  }

  function applyRealtimePulseSnapshot(pulse) {
    if (!pulse || typeof pulse !== "object") return;
    state.realtime.lastSyncTs = Date.now();
    const tideM = Number(pulse.tide_m);
    if (Number.isFinite(tideM) && state.weatherData?.astronomy_and_tide) {
      const prevTide = Number(state.weatherData.astronomy_and_tide.current_tide_m ?? tideM);
      if (Math.abs(tideM - prevTide) >= 0.01) {
        state.weatherData.astronomy_and_tide.current_tide_m = tideM;
        state.weatherData.astronomy_and_tide.current_tide_delta_m_h = Number(pulse.tide_delta_m_h ?? 0);
        if ($("#tile-tide-val")) $("#tile-tide-val").textContent = `${tideM.toFixed(2)} m`;
        if (state.r3f) state.r3f.targetTideM = tideM;
      }
    }
    const rainMm = Number(pulse.rain_mm_h);
    if (Number.isFinite(rainMm) && state.weatherData?.current) {
      state.weatherData.current.precip_mm = rainMm;
    }
  }

  async function handleRealtimeFloodUpdate(payload = {}, opts = {}) {
    const nextSig = payload?.signature || null;
    if (nextSig && nextSig === state.realtime.sig && !opts.force) {
      return;
    }
    if (nextSig) {
      state.realtime.sig = nextSig;
    }
    if (state.realtime.handlingUpdate) return;
    state.realtime.handlingUpdate = true;
    try {
      const tasks = [loadCameras()];
      if (!state.incidentDemoMode) {
        tasks.push(loadIncidents());
      }
      await Promise.all(tasks);

      if (state.weatherData && !state.isSimulationOverride) {
        await evaluateLiveFloodTruth(state.weatherData, { fromRealtime: !opts.silentToast });
      } else if (!state.isSimulationOverride && state.mode === "flood") {
        await runFloodRoute();
      }
    } catch (err) {
      console.warn("Realtime update sync error:", err);
    } finally {
      state.realtime.handlingUpdate = false;
    }
  }

  async function syncRealtimeDelta(opts = {}) {
    try {
      const qs = new URLSearchParams({ district: state.selectedDistrictId || "ninh_kieu" });
      if (state.realtime.sig && !opts.force) {
        qs.set("since", state.realtime.sig);
      }
      const res = await fetch(`/api/realtime/state?${qs}`);
      if (!res.ok) return;
      const data = await res.json();
      applyRealtimePulseSnapshot(data);
      if (!data.changed) return;

      state.realtime.sig = data.signature || state.realtime.sig;
      if (data.incidents && !state.incidentDemoMode) {
        state.latestIncidentsData = data.incidents;
        renderIncidents(data.incidents);
      }
      await loadCameras();
      if (state.weatherData && !state.isSimulationOverride) {
        await evaluateLiveFloodTruth(state.weatherData, { fromRealtime: !opts.silentToast });
      }
    } catch (err) {
      console.warn("Realtime delta sync warning:", err);
    }
  }

  function stopRealtimeDeltaFallback() {
    if (state.realtime.fallbackTimer) {
      clearInterval(state.realtime.fallbackTimer);
      state.realtime.fallbackTimer = null;
    }
    if (state.realtime.sseRetryTimer) {
      clearTimeout(state.realtime.sseRetryTimer);
      state.realtime.sseRetryTimer = null;
    }
  }

  function startRealtimeDeltaFallback() {
    if (!state.realtime.fallbackTimer) {
      state.realtime.fallbackTimer = setInterval(() => {
        if (!document.hidden) syncRealtimeDelta();
      }, 5_000);
    }
    if (!state.realtime.sseRetryTimer) {
      state.realtime.sseRetryTimer = setTimeout(() => {
        state.realtime.sseRetryTimer = null;
        state.realtime.failCount = 0;
        if (!document.hidden) connectRealtimeStream();
      }, 30_000);
    }
  }

  function closeRealtimeStream() {
    if (state.realtime.es) {
      try {
        state.realtime.es.close();
      } catch {
        /* ignore */
      }
      state.realtime.es = null;
    }
    state.realtime.connected = false;
    updateRealtimePill(false);
  }

  function connectRealtimeStream() {
    if (typeof EventSource === "undefined") {
      startRealtimeDeltaFallback();
      return;
    }
    if (document.hidden) return;
    closeRealtimeStream();

    const qs = new URLSearchParams({ district: state.selectedDistrictId || "ninh_kieu" });
    const es = new EventSource(`/api/realtime/stream?${qs}`);
    state.realtime.es = es;

    es.onopen = () => {
      state.realtime.connected = true;
      state.realtime.failCount = 0;
      stopRealtimeDeltaFallback();
      updateRealtimePill(true);
    };

    es.addEventListener("pulse", (ev) => {
      try {
        const pulse = JSON.parse(ev.data || "{}");
        applyRealtimePulseSnapshot(pulse);
        if (pulse.signature) {
          if (state.realtime.sig === null) {
            state.realtime.sig = pulse.signature;
          } else if (pulse.signature !== state.realtime.sig) {
            handleRealtimeFloodUpdate(pulse, { silentToast: true });
          }
        }
      } catch {
        /* ignore malformed pulse */
      }
    });

    es.addEventListener("flood_update", (ev) => {
      try {
        const payload = JSON.parse(ev.data || "{}");
        handleRealtimeFloodUpdate(payload);
      } catch {
        handleRealtimeFloodUpdate({}, { force: true });
      }
    });

    es.onerror = () => {
      state.realtime.connected = false;
      state.realtime.failCount += 1;
      updateRealtimePill(false);
      if (state.realtime.failCount >= 3) {
        closeRealtimeStream();
        startRealtimeDeltaFallback();
      }
    };
  }

  function initRealtimeSync() {
    if (typeof BroadcastChannel !== "undefined" && !state.realtime.bc) {
      try {
        state.realtime.bc = new BroadcastChannel("cantho_flood_realtime");
        state.realtime.bc.onmessage = (ev) => {
          handleRealtimeFloodUpdate(ev?.data || {}, { force: true });
        };
      } catch {
        state.realtime.bc = null;
      }
    }

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (state.realtime.hideTimer) clearTimeout(state.realtime.hideTimer);
        state.realtime.hideTimer = setTimeout(() => {
          if (document.hidden) closeRealtimeStream();
        }, 15_000);
      } else {
        if (state.realtime.hideTimer) {
          clearTimeout(state.realtime.hideTimer);
          state.realtime.hideTimer = null;
        }
        syncRealtimeDelta({ silentToast: true });
        if (!state.realtime.es) connectRealtimeStream();
      }
    });

    window.addEventListener("online", () => {
      syncRealtimeDelta({ silentToast: true });
      connectRealtimeStream();
      loadLiveWeather(state.selectedDistrictId, false);
    });

    window.addEventListener("beforeunload", closeRealtimeStream);
    connectRealtimeStream();
  }

  /* --------------------------------------------------------------------------
     Bootstrap
     -------------------------------------------------------------------------- */
  async function bootstrap() {
    startVietnamClock();
    initPointerTracking();
    initThreeWeatherScene();
    initMap();
    wireControls();
    updateSliderLabels();

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }

    try {
      const [initRes] = await Promise.all([
        fetch("/api/init"),
        loadLiveWeather(state.selectedDistrictId, false),
      ]);
      if (initRes.ok) renderInitData(await initRes.json());
      applyUrlRouteParams();
      await Promise.all([loadIncidents(), loadCameras()]);
    } catch (err) {
      showToast(`Lỗi khởi tạo: ${err.message}`);
    }

    initRealtimeSync();
    setInterval(() => {
      if (!document.hidden) loadLiveWeather(state.selectedDistrictId, false);
    }, 45_000);
  }

  document.addEventListener("DOMContentLoaded", bootstrap);
})();

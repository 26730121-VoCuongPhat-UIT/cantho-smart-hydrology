"""Frontend Asset Minifier & Production HTML Builder for Vercel Deployment.

Bundles & minifies static/app.js -> public/assets/app.min.js and
static/styles.css -> public/assets/styles.min.css (zero source maps) using esbuild
(with a safe Python regex fallback when running offline without npm/npx cache).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"
PUBLIC_DIR = ROOT_DIR / "public"
ASSETS_DIR = PUBLIC_DIR / "assets"
CACHE_TAG = "20261126v11"


def _run_esbuild(src: Path, dst: Path, is_js: bool) -> bool:
    """Invoke local node_modules/.bin/esbuild first, then fallback to npx esbuild."""
    bin_name = "esbuild.cmd" if sys.platform.startswith("win") else "esbuild"
    local_bin = ROOT_DIR / "node_modules" / ".bin" / bin_name
    if local_bin.exists():
        cmd = [str(local_bin)]
    else:
        npx_cmd = "npx.cmd" if sys.platform.startswith("win") else "npx"
        cmd = [npx_cmd, "--yes", "esbuild"]

    cmd.extend([
        str(src),
        "--minify",
        "--legal-comments=none",
        f"--outfile={dst}",
    ])
    if is_js:
        cmd.append("--bundle")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        return proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    except Exception:
        return False


def _fallback_minify_css(raw: str) -> str:
    """Lossless lightweight CSS minifier fallback."""
    no_comments = re.sub(r"/\*[\s\S]*?\*/", "", raw)
    collapsed = re.sub(r"\s+", " ", no_comments)
    tight = re.sub(r"\s*([{:;,}])\s*", r"\1", collapsed)
    return tight.strip()


def build_all() -> dict:
    """Build minified assets into public/ and return byte-size report."""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    js_src = STATIC_DIR / "app.js"
    js_dst = ASSETS_DIR / "app.min.js"
    css_src = STATIC_DIR / "styles.css"
    css_dst = ASSETS_DIR / "styles.min.css"

    js_used_esbuild = _run_esbuild(js_src, js_dst, is_js=True)
    if not js_used_esbuild:
        shutil.copy2(js_src, js_dst)

    css_used_esbuild = _run_esbuild(css_src, css_dst, is_js=False)
    if not css_used_esbuild:
        css_dst.write_text(_fallback_minify_css(css_src.read_text(encoding="utf-8")), encoding="utf-8")

    # Copy PWA files
    for fname in ("manifest.json", "sw.js"):
        src_file = STATIC_DIR / fname
        if src_file.exists():
            shutil.copy2(src_file, PUBLIC_DIR / fname)

    # Emit production index.html pointing at /assets/*.min.*
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = re.sub(
        r"/static/styles\.css(?:\?v=[^\"]+)?",
        f"/assets/styles.min.css?v={CACHE_TAG}",
        html,
    )
    html = re.sub(
        r"/static/app\.js(?:\?v=[^\"]+)?",
        f"/assets/app.min.js?v={CACHE_TAG}",
        html,
    )
    (PUBLIC_DIR / "index.html").write_text(html, encoding="utf-8")

    return {
        "js_before": js_src.stat().st_size,
        "js_after": js_dst.stat().st_size,
        "js_esbuild": js_used_esbuild,
        "css_before": css_src.stat().st_size,
        "css_after": css_dst.stat().st_size,
        "css_esbuild": css_used_esbuild,
    }


if __name__ == "__main__":
    stats = build_all()
    print(
        f"Built public assets: app.js {stats['js_before']:,}B -> {stats['js_after']:,}B (esbuild={stats['js_esbuild']}); "
        f"styles.css {stats['css_before']:,}B -> {stats['css_after']:,}B (esbuild={stats['css_esbuild']})"
    )

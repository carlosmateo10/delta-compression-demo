"""
Delta Compression Demo Server

Interactive website demonstrating Compression Dictionary Transport (RFC 9842)
with delta compression for dynamic content.

Every page shows a live metrics panel comparing:
  - Standard Brotli (no dictionary)
  - Static shared dictionary (RFC 9842 standard approach)
  - Delta compression (previous response as Brotli dictionary, dcb)

Usage:
    python app.py [--port 8080]
    gunicorn app:app --bind 0.0.0.0:8080
"""

import re
import argparse
import base64
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from collections import OrderedDict
from datetime import datetime

try:
    import brotli
except ImportError:
    sys.exit("Error: 'brotli' package required. pip install brotli")

from flask import Flask, request, render_template, Response, jsonify
from page_generator import generate_dynamic_content, generate_heavy_session_payload, generate_sidebar_content

# Configure extensive debug logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] %(message)s'
)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# ===========================================================================
# 1. APP & CACHE CONFIGURATION
# ===========================================================================

app = Flask(__name__)

BROTLI_QUALITY = 6
MAX_CACHE = 400
DICT_TTL_SECONDS = 600
_dict_cache: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
_static_dict: bytes = b""
_static_dict_hash_hex: str = ""
_metrics = []

def _evict_expired() -> None:
    now = time.monotonic()
    evicted_count = 0
    while _dict_cache:
        key, (_, ts) = next(iter(_dict_cache.items()))
        if now - ts > DICT_TTL_SECONDS:
            _dict_cache.pop(key)
            evicted_count += 1
        else:
            break
    if evicted_count > 0:
        logging.debug(f"Evicted {evicted_count} expired dictionaries from cache.")

def cache_put(sha_hex: str, raw: bytes) -> None:
    _evict_expired()
    if sha_hex in _dict_cache:
        _dict_cache.move_to_end(sha_hex)
        _dict_cache[sha_hex] = (_dict_cache[sha_hex][0], time.monotonic())
        logging.debug(f"Cache PUT: Updated access time for existing dict {sha_hex[:8]}...")
    else:
        if len(_dict_cache) >= MAX_CACHE:
            popped = _dict_cache.popitem(last=False)
            logging.debug(f"Cache PUT: Max capacity reached. Evicted oldest dict {popped[0][:8]}...")
        _dict_cache[sha_hex] = (raw, time.monotonic())
        logging.debug(f"Cache PUT: Stored new dict {sha_hex}... (Size: {len(raw)} bytes). Cache size: {len(_dict_cache)}")

def cache_get(sha_hex: str) -> bytes | None:
    _evict_expired()
    # Use pop to remove the item immediately upon retrieval to mimic a one-time-use delta dictionary
    if not sha_hex:
        logging.warning(f"Cache key not available")
        return None

    entry = _dict_cache.pop(sha_hex, None) 
    
    if entry is not None:
        raw, ts = entry
        if time.monotonic() - ts <= DICT_TTL_SECONDS:
            logging.debug(f"Cache GET & POP: HIT for dict {sha_hex[:8]}...")
            return raw
        else:
            logging.debug(f"Cache GET & POP: MISS (Expired) for dict {sha_hex[:8]}...")
    else:
        logging.debug(f"Cache GET: MISS (Not found or already popped) for dict {sha_hex[:8]}...")
    return None

def compress_standard(data: bytes) -> bytes:
    return brotli.compress(data, quality=BROTLI_QUALITY)

def compress_with_dict(data: bytes, dictionary: bytes, dict_hash_hex: str) -> bytes:
    compressed = brotli.compress(data, quality=BROTLI_QUALITY, dictionary=dictionary)
    # The 'dcb' protocol strictly requires a 4-byte magic number (\xffDCB)
    # followed by the 32-byte dictionary hash to be prepended to the compressed stream.
    magic_number = b'\xffDCB'
    return magic_number + bytes.fromhex(dict_hash_hex) + compressed

def parse_available_dictionary(header: str | None) -> str | None:
    """Parse the single Base64 hash from the Available-Dictionary header."""
    if not header:
        return None
    v = header.strip()
    if v.startswith(":") and v.endswith(":"):
        try:
            raw = base64.b64decode(v[1:-1])
            if len(raw) == 32:
                return raw.hex()
        except Exception as e:
            logging.warning(f"Failed to parse Available-Dictionary header: {e}")
    return None

# ===========================================================================
# 2. PAGE BUILDER
# ===========================================================================

def build_page(query: str, page_num: int, session_id: str) -> str:
    logging.debug(f"Starting page generation for query: '{query}', page: {page_num}, session: {session_id}")
    dynamic_content = generate_dynamic_content(query, page_num, 10)
    heavy_state = generate_heavy_session_payload(session_id)
    sidebar_content = generate_sidebar_content(session_id)
    
    req_data = {
        "requestId": uuid.uuid4().hex,
        "serverTs": datetime.utcnow().isoformat() + "Z"
    }
    
    # Render using the external page.html template
    page_html = render_template(
        "page.html",
        query=query,
        session_short=session_id[:8],
        content_html=dynamic_content,
        sidebar_content=sidebar_content,
        heavy_state=heavy_state,
        page_data=json.dumps(req_data),
        metrics_html="<!-- METRICS_PANEL_PLACEHOLDER -->"
    )
    logging.debug(f"Page generation complete. Total HTML size: {len(page_html)} bytes")
    return page_html

def build_static_dictionary(max_size: int = 150000) -> bytes:
    logging.info("Building global static dictionary...")
    page = build_page("__SYSTEM_BASELINE__", 1, "global-static-baseline")
    encoded_page = page.encode("utf-8")[:max_size]
    logging.info(f"Global static dictionary built: {len(encoded_page)} bytes")
    return encoded_page


# ===========================================================================
# 3. METRICS OVERLAY & FLASK ROUTES
# ===========================================================================

def build_metrics_html(
    raw_bytes: int,
    std_bytes: int,
    static_bytes: int,
    delta_bytes: int | None,
    dict_hash: str | None,
    request_number: int,
) -> str:
    std_pct = (1 - std_bytes / raw_bytes) * 100 if raw_bytes else 0
    static_pct = (1 - static_bytes / raw_bytes) * 100 if raw_bytes else 0
    std_bar = std_bytes / raw_bytes * 100 if raw_bytes else 0
    static_bar = static_bytes / raw_bytes * 100 if raw_bytes else 0

    if delta_bytes is not None and delta_bytes > 0:
        delta_pct = (1 - delta_bytes / raw_bytes) * 100
        delta_bar = delta_bytes / raw_bytes * 100
        delta_ratio = std_bytes / delta_bytes
        delta_highlight = f"{delta_ratio:.1f}\u00d7"
        delta_sub = "smaller than standard Brotli"
        delta_bar_html = (
            f'<div class="bar-item">'
            f'<span class="bar-label">Delta (dcb)</span>'
            f'<div class="bar-track">'
            f'<div class="bar-fill delta" style="width:{max(delta_bar, 3):.1f}%">'
            f'{delta_bytes:,} B ({delta_pct:.1f}%)</div></div></div>'
        )
        encoding_note = (
            f'<code>Content-Encoding: dcb</code> '
            f'Dictionary: <code>{dict_hash}</code>'
        )
    else:
        delta_highlight = "\u2014"
        delta_sub = "search again to see delta compression"
        delta_bar_html = (
            '<div class="bar-item">'
            '<span class="bar-label">Delta (dcb)</span>'
            '<div class="bar-track">'
            '<div class="bar-fill delta" style="width:0%"></div></div></div>'
        )
        encoding_note = (
            '<code>Content-Encoding: br</code> '
            'No dictionary available yet'
        )

    note_html = ""
    if delta_bytes is None:
        note_html = (
            '<div class="metrics-note">'
            '<strong>First page load</strong> \u2014 no previous response exists '
            'to use as a dictionary. Click any search result or search for '
            'something new. The next page will show delta compression metrics '
            'using <em>this</em> page as the Brotli dictionary.'
            '</div>'
        )

    return f'''
<div class="metrics-panel">
<div class="metrics-card">
<h2>Compression Dictionary Transport \u2014 Live Metrics</h2>
<div class="metrics-grid">
<div class="metric-item">
<span class="metric-label">Page size (raw HTML)</span>
<span class="metric-value">{raw_bytes:,} B</span>
</div>
<div class="metric-item">
<span class="metric-label">Standard Brotli</span>
<span class="metric-value">{std_bytes:,} B</span>
<span class="metric-sub">{std_pct:.1f}% savings</span>
</div>
<div class="metric-item">
<span class="metric-label">Static Dictionary</span>
<span class="metric-value primary">{static_bytes:,} B</span>
<span class="metric-sub">{static_pct:.1f}% savings</span>
</div>
<div class="metric-item">
<span class="metric-label">Delta vs Standard</span>
<span class="metric-value highlight">{delta_highlight}</span>
<span class="metric-sub">{delta_sub}</span>
</div>
</div>
<div class="metrics-bar-row">
<div class="bar-container">
<div class="bar-item">
<span class="bar-label">Raw HTML</span>
<div class="bar-track">
<div class="bar-fill standard" style="width:100%">{raw_bytes:,} B</div>
</div>
</div>
<div class="bar-item">
<span class="bar-label">Standard Brotli (br)</span>
<div class="bar-track">
<div class="bar-fill standard" style="width:{max(std_bar, 3):.1f}%">{std_bytes:,} B ({std_pct:.1f}%)</div>
</div>
</div>
<div class="bar-item">
<span class="bar-label">Static Dict (dcb)</span>
<div class="bar-track">
<div class="bar-fill static-dict" style="width:{max(static_bar, 3):.1f}%">{static_bytes:,} B ({static_pct:.1f}%)</div>
</div>
</div>
{delta_bar_html}
</div>
</div>
{note_html}
<div class="metrics-footer">
<span>Request #{request_number} \u00b7 {encoding_note}</span>
<span>Brotli quality {BROTLI_QUALITY} \u00b7
<a href="/metrics" style="color:#7eb8da" target="_blank">JSON API</a> \u00b7
<a href="https://datatracker.ietf.org/doc/rfc9842/" style="color:#7eb8da" target="_blank">RFC 9842</a></span>
</div>
</div>
</div>
'''

@app.route("/robots.txt")
def robots():
    """Stop crawlers from endlessly following dynamic search links."""
    return Response(
        "User-agent: *\nDisallow: /\n",
        mimetype="text/plain"
    )

@app.route("/static.dict")
def serve_static_dict():
    """Serves the generic baseline static dictionary for clients that request it."""
    response = Response(_static_dict, mimetype="text/plain")
    response.headers["Use-As-Dictionary"] = 'match="/*"'
    response.headers["Cache-Control"] = "public, max-age=31536000"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response

BOT_PATTERN = re.compile(
    r"bot|spider|crawler|crawling|slurp|mediapartners|"
    r"googleother|bingpreview|facebookexternalhit|"
    r"bytespider|yandex|baidu|semrush|ahref|mj12bot|"
    r"dotbot|petalbot|gptbot|claudebot|ccbot",
    re.IGNORECASE
)

@app.route("/")
def search():
    user_agent = request.headers.get("User-Agent", "")
    if BOT_PATTERN.search(user_agent):
        return Response("Bots are not allowed on this dynamic demo", status=403)

    logging.info(f"--- Incoming Request: {request.url} ---")
    
    query = request.args.get("q", "distributed systems")
    page_num = int(request.args.get("p", 1))

    session_id = request.cookies.get("_session_id")
    new_session = False
    if not session_id:
        session_id = uuid.uuid4().hex
        new_session = True
        logging.info(f"Generated new session ID: {session_id}")

    # 1. Generate clean page for metrics simulation
    clean_page = build_page(query, page_num, session_id)
    clean_bytes = clean_page.encode("utf-8")
    logging.info(f"Page generated successfully. Final uncompressed size: {len(clean_bytes)} bytes.")

    # 2. Find Available Dictionary
    avail_header = request.headers.get("Available-Dictionary")
    client_hash = parse_available_dictionary(avail_header)
    cookie_hash = request.cookies.get("_dict_hash")

    # Separate out the specific delta dictionary for UI metrics tracking
    delta_dict_bytes = None
    delta_dict_hash = None

    cache_key = client_hash if client_hash and client_hash != _static_dict_hash_hex else cookie_hash
    cached = cache_get(cache_key)
    if cached:
        delta_dict_bytes = cached
        delta_dict_hash = cache_key

    # Decide which dictionary to ACTUALLY use for the real HTTP response
    active_dict_bytes = None
    active_dict_hash_hex = None
    active_encoding = "br"

    accept_encoding = request.headers.get("Accept-Encoding", "")
    supports_dcb = "dcb" in accept_encoding


    # Render's proxy strips "dcb" from Accept-Encoding, but it won't touch
    # Available-Dictionary. If the client sent a valid dictionary hash,
    # it necessarily supports dcb — that's the only reason it would
    # advertise a dictionary.
    if not supports_dcb and client_hash:
        supports_dcb = True
        logging.info("Inferred dcb support from Available-Dictionary header")

    # ONLY actually compress with a dictionary if the browser provided a
    # valid dictionary hash.
    if supports_dcb and client_hash:
        if client_hash == delta_dict_hash:
            active_dict_bytes = delta_dict_bytes
            active_dict_hash_hex = delta_dict_hash
            active_encoding = "dcb"
        elif client_hash == _static_dict_hash_hex:
            active_dict_bytes = _static_dict
            active_dict_hash_hex = _static_dict_hash_hex
            active_encoding = "dcb"

    # 3. Measure Compression (Simulated on clean bytes strictly for UI display)
    std_compressed = compress_standard(clean_bytes)
    static_compressed = compress_with_dict(clean_bytes, _static_dict, _static_dict_hash_hex)
    delta_compressed = compress_with_dict(clean_bytes, delta_dict_bytes, delta_dict_hash) if delta_dict_bytes else None

    # Track Metrics
    measurement = {
        "request": len(_metrics) + 1,
        "query": query,
        "page": page_num,
        "raw_bytes": len(clean_bytes),
        "standard_brotli_bytes": len(std_compressed),
        "static_dict_bytes": len(static_compressed),
        "delta_bytes": len(delta_compressed) if delta_compressed else None,
        "delta_vs_standard": round(len(std_compressed) / len(delta_compressed), 2) if delta_compressed else None,
        "dictionary_hash": delta_dict_hash
    }
    _metrics.append(measurement)

    # 4. Inject Metrics Panel
    metrics_html = build_metrics_html(
        raw_bytes=len(clean_bytes),
        std_bytes=len(std_compressed),
        static_bytes=len(static_compressed),
        delta_bytes=len(delta_compressed) if delta_compressed else None,
        dict_hash=delta_dict_hash if delta_dict_hash else None,
        request_number=len(_metrics),
    )
    display_page = clean_page.replace('<!-- METRICS_PANEL_PLACEHOLDER -->', metrics_html)
    
    # 5. Lock in the final payload
    final_bytes = display_page.encode("utf-8")
    final_hash_hex = hashlib.sha256(final_bytes).hexdigest()

    # 6. Actually Compress the HTTP Response!
    if active_encoding == "dcb" and active_dict_bytes:
        response_payload = compress_with_dict(final_bytes, active_dict_bytes, active_dict_hash_hex)
        logging.info(f"Sending REAL dcb response using dict {active_dict_hash_hex[:8]}... (Size: {len(response_payload)} bytes)")
    else:
        response_payload = compress_standard(final_bytes)
        active_encoding = "br"
        logging.info(f"Sending standard br response (Size: {len(response_payload)} bytes)")

    # 7. Build HTTP Response
    response = Response(response_payload, content_type="text/html; charset=utf-8")
    response.headers["Content-Encoding"] = active_encoding
    
    # Delta dictionary uses /** to ensure higher priority than the static dictionary (/*)
    response.headers["Use-As-Dictionary"] = 'match="/**", ttl=600'
    response.headers["Link"] = '</static.dict>; rel="compression-dictionary"'
    response.headers["Vary"] = "Accept-Encoding, Available-Dictionary"
    
    response.headers["Cache-Control"] = "private, no-cache" 
    
    # Cache the FINAL payload to serve as the Delta Dict for the next request
    cache_put(final_hash_hex, final_bytes)
    response.set_cookie("_dict_hash", final_hash_hex, max_age=600, httponly=True, samesite="Lax")
    
    if new_session:
        response.set_cookie("_session_id", session_id, max_age=86400, httponly=True, samesite="Lax")
    
    return response

@app.route("/about")
def about():
    """Serves the restored Readme/About documentation."""
    return render_template("about.html")

@app.route("/metrics")
def metrics():
    result = {
        "measurements": _metrics,
        "total": len(_metrics)
    }
    std_savings = [(1 - m["standard_brotli_bytes"] / m["raw_bytes"]) * 100 for m in _metrics if m["raw_bytes"]]
    delta_items = [m for m in _metrics if m["delta_bytes"] is not None and m["raw_bytes"]]
    
    if std_savings:
        result["avg_standard_savings_pct"] = round(sum(std_savings) / len(std_savings), 2)
    if delta_items:
        ds = [(1 - m["delta_bytes"] / m["raw_bytes"]) * 100 for m in delta_items]
        dr = [m["delta_vs_standard"] for m in delta_items]
        result["avg_delta_savings_pct"] = round(sum(ds) / len(ds), 2)
        result["avg_delta_vs_standard"] = round(sum(dr) / len(dr), 2)
        
    return jsonify(result)

@app.route("/metrics/reset", methods=["POST"])
def reset():
    _metrics.clear()
    _dict_cache.clear()
    return jsonify({"status": "reset"})

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "cached_dictionaries": len(_dict_cache),
        "total_requests": len(_metrics),
        "static_dict_bytes": len(_static_dict),
    })

# ===========================================================================
# 4. SERVER LAUNCH
# ===========================================================================

with app.app_context():
    _static_dict = build_static_dictionary()
    _static_dict_hash_hex = hashlib.sha256(_static_dict).hexdigest()
    logging.info(f"Static Dictionary Hash Hex: {_static_dict_hash_hex}\n")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    args = p.parse_args()
    logging.info(f"[CDT Demo] Running on http://localhost:{args.port}")
    logging.info(f"[CDT Demo] Ready! Navigate around the site to see Delta Compression in action.\n")
    app.run(host="0.0.0.0", port=args.port, debug=False)

if __name__ == "__main__":
    main()
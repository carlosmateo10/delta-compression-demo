#!/usr/bin/env python3
"""
Delta Compression Benchmark

This script simulates sequential user browsing sessions to measure the real-world
impact of Compression Dictionary Transport (RFC 9842) on dynamic HTML.

It generates realistic web pages loaded with high-entropy, user-specific session
state. It then measures the payload size across three compression modes:
  1. Standard Brotli (Baseline)
  2. Static Dictionary (Pre-built baseline dictionary)
  3. Delta Compression (Using the previous page in the session as a dictionary)

Reports distributional statistics (median / p75 / p90) representing the 
improvement multiplier of Delta Compression over Standard Brotli.

Usage:
    python scripts/run_bench.py [--out results.json]
"""

import argparse
import json
import logging
import os
import sys

# Globally disable all log messages at INFO level and below 
# to keep the benchmark output completely clean.
logging.disable(logging.INFO)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")
sys.path.insert(0, SERVER_DIR)

try:
    import brotli
except ImportError:
    sys.exit("Error: 'Brotli' package required. pip install Brotli>=1.1.0")

# Import logic directly from the Flask app to ensure parity with the live server
from app import app, build_page, build_static_dictionary
from page_generator import generate_dynamic_content

BROTLI_QUALITY = 6

def compress_std(data: bytes) -> bytes:
    return brotli.compress(data, quality=BROTLI_QUALITY)

def compress_dict(data: bytes, d: bytes) -> bytes:
    return brotli.compress(data, quality=BROTLI_QUALITY, dictionary=d)

def render_page(query: str, page: int, session_id: str) -> bytes:
    """Render full page (skeleton + content + heavy state) as bytes."""
    with app.app_context():
        # Inject the heavy state exactly as the live server does
        html = build_page(query, page, session_id)
    return html.encode("utf-8")

def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f) if f != c else s[f]

SCENARIOS = [
    ("Research session", [
        ("compression algorithms", 1), ("brotli dictionary compression", 1),
        ("zstandard vs brotli", 1), ("HTTP content encoding", 1),
        ("content encoding negotiation", 1),
    ]),
    ("Performance session", [
        ("largest contentful paint", 1), ("core web vitals", 1),
        ("cumulative layout shift", 1), ("interaction to next paint", 1),
        ("web performance monitoring", 1),
    ]),
    ("Pagination", [
        ("web performance", 1), ("web performance", 2),
        ("web performance", 3), ("web performance", 4),
        ("web performance", 5),
    ]),
    ("Mixed browsing", [
        ("javascript performance", 1), ("javascript bundle size", 1),
        ("javascript bundle size", 2), ("react server components", 1),
        ("next.js streaming SSR", 1), ("next.js streaming SSR", 2),
        ("web vitals monitoring", 1), ("lighthouse CI setup", 1),
    ]),
]

def run() -> dict:
    # 1. Build Static Dictionary exactly like the server
    with app.app_context():
        static_dict = build_static_dictionary()

    all_dvs = []  # delta_vs_standard ratios
    all_svs = []  # static_vs_standard ratios
    scenario_results = []
    
    # Use a fixed session ID for the benchmark to ensure consistent hydration state size
    bench_session = "benchmark-session-uuid-1234"

    for name, queries in SCENARIOS:
        prev = None
        dvs = []
        rows = []
        for query, page in queries:
            html = render_page(query, page, bench_session)
            std = compress_std(html)
            static = compress_dict(html, static_dict)
            
            # Track Static vs Standard for every page
            static_ratio = len(std) / len(static) if len(static) > 0 else 1.0
            all_svs.append(static_ratio)
            
            if prev is not None:
                delta = compress_dict(html, prev)
                ratio = len(std) / len(delta)
                dvs.append(ratio)
                all_dvs.append(ratio)
                rows.append({
                    "query": query, "page": page,
                    "raw": len(html), "std": len(std),
                    "static": len(static), "delta": len(delta),
                    "static_vs_std": round(static_ratio, 3),
                    "delta_vs_std": round(ratio, 3),
                })
            prev = html

        scenario_results.append({
            "name": name,
            "transitions": len(dvs),
            "median": round(percentile(dvs, 0.5), 3) if dvs else None,
            "p75": round(percentile(dvs, 0.75), 3) if dvs else None,
            "p90": round(percentile(dvs, 0.9), 3) if dvs else None,
            "rows": rows,
        })

    # Sample page stats
    sample = render_page("test", 1, bench_session)
    sample_content = generate_dynamic_content("test", 1).encode("utf-8")

    return {
        "metadata": {
            "brotli_quality": BROTLI_QUALITY,
            "static_dict_bytes": len(static_dict),
            "sample_page_bytes": len(sample),
            "sample_content_bytes": len(sample_content),
            "skeleton_and_state_bytes": len(sample) - len(sample_content),
            "skeleton_and_state_pct": round(
                (len(sample) - len(sample_content)) / len(sample) * 100, 1
            ),
        },
        "scenarios": scenario_results,
        "global": {
            "transitions": len(all_dvs),
            "median_static_vs_std": round(percentile(all_svs, 0.5), 3) if all_svs else None,
            "median_delta_vs_std": round(percentile(all_dvs, 0.5), 3),
            "p75_delta_vs_std": round(percentile(all_dvs, 0.75), 3),
            "p90_delta_vs_std": round(percentile(all_dvs, 0.9), 3),
        },
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results.json")
    args = ap.parse_args()

    print("[Benchmark] Running...\n")
    results = run()

    meta = results["metadata"]
    g = results["global"]

    print("=" * 68)
    print("  DELTA COMPRESSION BENCHMARK")
    print("=" * 68)
    print("  Simulating sequential browsing sessions on a dynamic web page")
    print("  loaded with heavy, user-specific JSON session state. Comparing")
    print("  Standard Brotli vs. Static Dict vs. Delta Compression.")
    print("-" * 68)
    print(f"  Brotli quality:    {meta['brotli_quality']}")
    print(f"  Static dict:       {meta['static_dict_bytes']:,} B")
    print(f"  Sample page:       {meta['sample_page_bytes']:,} B "
          f"(Static/State overhead: {meta['skeleton_and_state_pct']}%)")
    print()
    print(f"  {'Scenario':<25} {'N':>3} {'Med':>7} {'p75':>7} {'p90':>7}")
    print(f"  {'-'*25} {'-'*3} {'-'*7} {'-'*7} {'-'*7}")
    for s in results["scenarios"]:
        if s["transitions"]:
            print(f"  {s['name']:<25} {s['transitions']:>3} "
                  f"{s['median']:>6.2f}x {s['p75']:>6.2f}x {s['p90']:>6.2f}x")
    print()
    print(f"  GLOBAL ({g['transitions']} transitions)")
    if g['median_static_vs_std']:
        print(f"    Static Dict vs Std: Median {g['median_static_vs_std']:.2f}x improvement")
    print(f"    Delta Dict vs Std:  Median {g['median_delta_vs_std']:.2f}x improvement")
    print("=" * 68)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Written to: {args.out}")

if __name__ == "__main__":
    main()
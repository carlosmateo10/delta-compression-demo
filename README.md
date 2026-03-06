# Delta Compression for Dynamic HTML

**Using previous responses as compression dictionaries for content served with `Cache-Control: no-cache`**

## What this is

An interactive demo and benchmark showing **delta compression** — using the user's previously visited page as a Brotli compression dictionary for the next page. Every page shows a live metrics panel comparing standard Brotli, a pre-built static dictionary, and delta compression side by side.

The server implements the [Compression Dictionary Transport](https://datatracker.ietf.org/doc/rfc9842/) (CDT) protocol headers. The `Content-Encoding` for dictionary-compressed Brotli is `dcb`.

[**Live demo →**](https://delta-compression-demo.onrender.com)

## How it Works

1. **The Problem:** Dynamic websites resend heavy, user-specific data on every click. In this demo, your **Activity Graph** and **Notifications** simulate this constant, hard-to-compress data overhead.

2. **The Mechanism:** This server uses RFC 9842 to tell your browser to keep the raw code of your current page in its network cache to use as a *reference dictionary*.

3. **The Result:** When you click a link, the server doesn't resend the code for the sidebar. It simply sends the new **articles**, plus tiny instructions to "copy the rest from the dictionary." This drops the transferred data to near-zero!

## What this is not

* Not a production implementation.

* Not a claim of standardization or deployment status.

* Not dependent on proprietary systems. Everything runs locally.

## Why this matters

### The gap in RFC 9842

[RFC 9842](https://datatracker.ietf.org/doc/rfc9842/) documents two primary use cases:

1. **Delta compression for static resources** — `app.v1.js` as dictionary for `app.v2.js`. Requires the previous version to be cached with a long `max-age`.

2. **Pre-built shared dictionaries** — a separately authored dictionary file fetched by the browser during idle time via `<link rel="compression-dictionary">`. Captures general page structure but cannot include per-request content.

Neither addresses the largest class of dynamic web content: **pages served with `Cache-Control: no-cache` or `max-age=0`** — search results, news feeds, social media timelines, e-commerce listings, dashboards.

The problem is mechanical: under RFC 9842, a response's usability as a dictionary is tied to its HTTP cache lifetime. When a dynamic page is served with `no-cache`, the browser discards it from the dictionary cache immediately. **The previous response is gone before the user navigates to the next page.**

### What Dictionary TTL changes

[Dictionary TTL](https://groups.google.com/a/chromium.org/g/blink-dev/c/pW8bjRXGNKs) is an experimental extension to `Use-As-Dictionary` that decouples dictionary lifetime from cache lifetime:

```http
Use-As-Dictionary: match="/search*", ttl=600
```

A dynamic page served with `no-cache` can now survive in the dictionary cache long enough for the next navigation to reference it. This was part of the original CDT design but dropped during the IETF standards process due to a lack of compelling use cases at the time.

**This prototype demonstrates the exact use case that Dictionary TTL enables.**

### Key insight

Standard Brotli exploits repetition *within* a single response. A pre-built dictionary captures *general structure*. Using the *actual previous response* as the dictionary exploits repetition *across* sequential responses, achieving near-optimal deduplication and dropping payload sizes to near-zero.

## Protocol flow

```text
Browser                                Server
  │                                        │
  │  GET /search?q=cats                    │
  │───────────────────────────────────────>│
  │                                        │  generate HTML, compress,
  │  Content-Encoding: br                  │  cache raw HTML by SHA-256
  │  Use-As-Dictionary: match="/*",ttl=600 │
  │  Content-Dictionary: :<base64 hash>:   │
  │  Cache-Control: no-cache               │
  │<───────────────────────────────────────│
  │                                        │
  │  [browser caches response as dict]     │
  │                                        │
  │  GET /search?q=dogs                    │
  │  Available-Dictionary: :<base64 hash>: │
  │───────────────────────────────────────>│
  │                                        │  decode b64 → SHA-256 hex,
  │  Content-Encoding: dcb                 │  look up cached HTML,
  │  Content-Dictionary: :<new hash>:      │  compress with prev as dict
  │<───────────────────────────────────────│
```

## Quick start

### Run locally

```bash
git clone https://github.com/carlosmateo10/delta-compression-demo.git
cd delta-compression-demo

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cd server
python app.py --port 8080
```

Open `http://localhost:8080`, search for anything. First page = standard Brotli. Second page = delta compression metrics appear.

### Docker

```bash
docker build -t delta-demo .
docker run --rm -p 8080:8080 delta-demo
```

### Deploy to Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

Or push to GitHub and connect via `render.yaml`.

### Run benchmark

```bash
python scripts/run_bench.py --out results.json
```

## Repository structure

```text
├── scripts/
│   ├── run_bench.py           ← Benchmark (median/p75/p90 stats)
├── server/
│   ├── app.py                 ← Flask server with CDT protocol
│   ├── page_generator.py      ← Dynamic content generator
│   └── templates/
│       ├── page.html          ← Page skeleton + metrics panel
│       └── about.html         ← Methodology explainer
├── Dockerfile             ← Container definition
├── render.yaml            ← Render one-click deploy config
├── requirements.txt       ← Python dependencies
└── README.md
```

## How measurement works

The server renders each page in two phases:

1. **Clean render** — skeleton + dynamic content, no metrics panel. This is what gets compressed, measured, and cached as a dictionary.

2. **Display render** — same page with the metrics panel injected.

This ensures the compression ratios reflect real-world behavior. The metrics panel is an educational overlay; it wouldn't exist in production.

Three compression modes per page:

| Mode | Dictionary | Content-Encoding | 
 | ----- | ----- | ----- | 
| Standard Brotli | None | `br` | 
| Static Dictionary | Pre-built from generic baseline page | `dcb` | 
| Delta Compression | Previous response (full page bytes) | `dcb` | 

## Relationship to RFC 9842

This technique is **not part of RFC 9842**. It applies CDT's protocol mechanism (`Use-As-Dictionary`, `Available-Dictionary`, `dcb`) to a workload class the standard's documented use cases don't cover: dynamic HTML with `no-cache`.

The Dictionary TTL extension removes the blocker. This prototype demonstrates the compression benefit it unlocks. The underlying mechanism — Brotli with a custom dictionary — is identical to standard CDT.

## Author

**Carlos Mateo Muñoz** — [LinkedIn](https://www.linkedin.com/in/carlosmateom/)

## License

MIT. See [LICENSE](LICENSE) for details.

## References

* [RFC 9842: Compression Dictionary Transport](https://datatracker.ietf.org/doc/rfc9842/)

* [RFC 7932: Brotli Compressed Data Format](https://datatracker.ietf.org/doc/rfc7932/)

* [Ready for Developer Testing: Compression Dictionary TTL](https://groups.google.com/a/chromium.org/g/blink-dev/c/pW8bjRXGNKs) (Chromium blink-dev)

* [Chrome for Developers: Improving Google Search with Compression Dictionaries](https://developer.chrome.com/blog/search-compression-dictionaries)

* [MDN: Compression Dictionary Transport](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Compression_dictionary_transport)
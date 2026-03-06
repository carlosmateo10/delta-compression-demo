"""
Dynamic Page Generator

Produces realistic HTML pages (~30-150 KB) with a byte-for-byte identical
skeleton (inline CSS/JS, header, nav, footer) and per-request dynamic content
(search results, sidebar).  Deterministic given (query, page).
"""

import base64
import hashlib
import json
import random
import uuid
from datetime import datetime
from faker import Faker

def _seed_from(query: str, page: int) -> int:
    return int(hashlib.md5(f"{query}:{page}".encode()).hexdigest()[:8], 16)

def _sess_seed(session_id: str) -> int:
    return int(hashlib.md5(session_id.encode()).hexdigest()[:8], 16)

def _result_card(rng: random.Random, fake: Faker) -> str:
    # Use Faker for unique titles instead of a fixed static list
    title = fake.catch_phrase().title()
    # Randomize snippet length for wider HTML size variance
    snippet = fake.paragraph(nb_sentences=rng.randint(2, 5))
    tags = "".join(f'<span class="tag">{fake.word()}</span>' for _ in range(rng.randint(2, 4)))
    query_link = f"/?q={title.replace(' ', '+')}"
    
    return f"""
    <article class="result-card">
        <h2 class="result-title"><a href="{query_link}">{title}</a></h2>
        <p class="result-snippet">{snippet}</p>
        <div class="result-meta"><span>{fake.name()}</span><span>{rng.randint(1,20)} min read</span></div>
        <div style="margin-top:10px; display:flex; gap:5px;">{tags}</div>
    </article>"""

def generate_dynamic_content(query: str, page: int = 1, num_results: int = 10) -> str:
    """Generate ONLY the dynamic content portion (no skeleton)."""
    seed = _seed_from(query, page)
    rng = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)

    parts = []

    # Randomize the number of results per page to widen HTML size variance
    actual_num_results = rng.randint(5, 10)
    for _ in range(actual_num_results):
        parts.append(_result_card(rng, fake))

    # Pagination
    total_pages = 10
    pag = ['<div class="pagination">']
    pag.append(f'<a href="/?q={query}&p={max(1, page-1)}">&laquo; Prev</a>')
    for p in range(1, total_pages + 1):
        if p == page:
            pag.append(f'<span class="current">{p}</span>')
        else:
            pag.append(f'<a href="/?q={query}&p={p}">{p}</a>')
    pag.append(f'<a href="/?q={query}&p={min(page+1, total_pages)}">Next &raquo;</a>')
    pag.append('</div>\n')
    
    parts.append("".join(pag))
    return "".join(parts)

def generate_heavy_session_payload(session_id: str) -> str:
    """
    Generates structured JSON with injected high-entropy Base64 data.
    Standard Brotli struggles to compress the random entropy (making the
    baseline much more realistic), while Delta Dict perfectly eliminates 
    it because the values carry over seamlessly between pages.
    """
    rng = random.Random(_sess_seed(session_id) + 999)
    fake = Faker()
    fake.seed_instance(_sess_seed(session_id))
    
    companies = [fake.company() for _ in range(10)]
    cities = [fake.city() for _ in range(10)]
    phrases = [fake.catch_phrase() for _ in range(10)]
    
    inventory = []
    # Randomize session size between 50 and 350 items for massive size variance across sessions
    num_items = rng.randint(20, 80)
    for _ in range(num_items):
        inventory.append({
            "item_id": f"item_{rng.getrandbits(48):012x}",
            "category": rng.choice(["equipment", "consumable", "material", "key_item"]),
            "attributes": {
                "durability": rng.randint(0, 100),
                "weight": round(rng.uniform(0.1, 50.0), 2),
                "is_bound": rng.choice([True, False]),
                "element": rng.choice(["fire", "water", "earth", "wind", "light", "dark", "none"])
            },
            "metadata": {
                "acquired_from": rng.choice(companies),
                "location": rng.choice(cities),
                "notes": rng.choice(phrases)
            }
        })
        
    # Inject High Entropy data (e.g., mimicking encrypted tokens or opaque cursors)
    # This specifically lowers standard Brotli efficiency while preserving Delta.
    opaque_cursors = []
    
    # Randomize entropy size
    num_cursors = rng.randint(20, 80)
    for _ in range(num_cursors):
        # Generate 64 random bytes and base64 encode them using the seeded PRNG
        entropy_bytes = bytes([rng.randint(0, 255) for _ in range(64)])
        opaque_cursors.append(base64.b64encode(entropy_bytes).decode('ascii'))
    
    sess = {
        "_id": session_id,
        "user_name": fake.name(),
        "locale": fake.locale()
    }
    
    payload = {
        "session_id": session_id,
        "user_profile": sess,
        "inventory_data": inventory,
        "opaque_state_cursors": opaque_cursors,
        "active_experiments": {f"exp_{i}": rng.choice(["control", "variant_a", "variant_b"]) for i in range(10)}
    }
    return json.dumps(payload, separators=(',', ':'))

def generate_sidebar_content(session_id: str) -> str:
    rng = random.Random(_sess_seed(session_id) + 5678)
    paths = []
    for _ in range(15): 
        points = " ".join(f"{rng.randint(0,1000)},{rng.randint(0,400)}" for _ in range(10))
        paths.append(f'<polyline points="{points}" fill="none" stroke="#{rng.getrandbits(24):06x}" stroke-width="{rng.uniform(1, 3):.1f}" opacity="0.6"/>')
    svg_graph = f'<svg viewBox="0 0 1000 400" style="width:100%; height:auto; background:#f8f9fa; border-radius:4px; margin-bottom: 15px;">{"".join(paths)}</svg>'

    notifications = []
    for _ in range(rng.randint(5, 12)):
        nid = uuid.UUID(int=rng.getrandbits(128)).hex
        notifications.append(f'<div class="sidebar-item" data-nid="{nid}">'
                             f'<strong style="color:#1a0dab">User_{rng.getrandbits(32):08x}</strong> '
                             f'<span style="display:block; margin:4px 0;">System event {rng.getrandbits(64):016x} triggered.</span>'
                             f'</div>')
    
    templates = []
    for _ in range(5):
        exp_id = f"{rng.getrandbits(64):016x}"
        templates.append(f'<template id="exp-{exp_id}">'
                         f'<div class="exp-panel" data-variant="{rng.choice(["A", "B", "C", "D"])}">'
                         f'<h4>Experiment {exp_id}</h4>'
                         f'<button data-action="{uuid.UUID(int=rng.getrandbits(128)).hex}">Interact</button>'
                         f'</div></template>')

    return f"""
    <div style="display:none;" id="session-templates">
        {"".join(templates)}
    </div>
    <div class="sidebar-card" style="margin-top: 20px;">
        <h3 style="margin-bottom:15px; font-size: 1.1rem; font-weight:normal;">Your Activity Graph</h3>
        {svg_graph}
        <h3 style="margin: 25px 0 10px 0; font-size: 1.1rem; font-weight:normal;">Recent Notifications ({len(notifications)})</h3>
        <div style="max-height: 500px; overflow-y: auto; padding-right: 10px;">
            {"".join(notifications)}
        </div>
    </div>
    """
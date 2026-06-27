#!/usr/bin/env python3
"""Fix anime covers that are the site logo.

Scans pic/data/*/images/1.jpg, checks hash against known logo.
If logo detected, tries to find the correct anime_id from:
  1. pa_anime_id.txt
  2. Sitemap name matching (fuzzy)
Then re-downloads the cover from CDN.
"""

import json, hashlib, re, time, sys, os
from pathlib import Path

import requests

LOGO_HASHES = {"0d50dacaf072761b4f425f2cb6fd89da"}
CDN_BASE = "https://cdn.animepilgrimage.com"
IMG_BASE = "https://image.xinu.ink/pic/data"
BASE_DIR = Path("pic/data")


def load_anime_id_map():
    path = Path("pa_anime_id.txt")
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_sitemap(session):
    """Fetch sitemap and return {anime_id: slug} dict."""
    resp = session.get("https://www.animepilgrimage.com/sitemap.xml", timeout=30)
    if resp.status_code != 200:
        print(f"Failed to fetch sitemap: {resp.status_code}")
        return {}
    matches = re.findall(
        r"https://www\.animepilgrimage\.com/maps/anime/([a-zA-Z0-9_-]+)/([a-z0-9-]+)",
        resp.text,
    )
    seen = {}
    for aid, slug in matches:
        if aid not in seen:
            seen[aid] = slug
    return seen


def normalize(name):
    """Normalize a name for fuzzy matching."""
    return re.sub(r"[^\w]", "", name).lower()


def find_anime_id_by_name(name, sitemap):
    """Try to find anime_id by matching name against sitemap slugs."""
    norm = normalize(name)
    # Exact slug match
    for aid, slug in sitemap.items():
        slug_norm = slug.replace("-", "")
        if slug_norm == norm:
            return aid
    # Partial match
    for aid, slug in sitemap.items():
        slug_norm = slug.replace("-", "")
        if norm in slug_norm or slug_norm in norm:
            return aid
        # Also try each word
        for word in norm:
            if len(word) >= 3 and word in slug_norm:
                return aid
    return None


def download_cover(session, anime_id, save_path):
    """Try multiple formats to download cover from CDN."""
    for ext in [".webp", ".jpg", ".jpeg", ".png", ".avif", ".gif", ".bmp", ".tiff", ".svg", ""]:
        url = f"{CDN_BASE}/anime/{anime_id}{ext}"
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200 and len(resp.content) > 500:
                h = hashlib.md5(resp.content).hexdigest()
                if h in LOGO_HASHES:
                    continue  # Skip if CDN returns logo
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                return url
        except Exception:
            pass
    return None


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    })

    print("Loading pa_anime_id.txt...")
    id_map = load_anime_id_map()
    print(f"  {len(id_map)} entries")

    print("Fetching sitemap for name matching...")
    sitemap = fetch_sitemap(session)
    print(f"  {len(sitemap)} anime in sitemap")

    # Load index.json for anime names
    index_data = {}
    for idx_path in [BASE_DIR / "index.json", Path("index.json")]:
        if idx_path.exists():
            with open(idx_path, "r", encoding="utf-8") as f:
                index_data = json.load(f)
            break

    # Scan all covers
    print("\nScanning covers...")
    fixed = 0
    skipped = 0
    failed = 0

    for folder in sorted(BASE_DIR.glob("*")):
        if not folder.is_dir() or not folder.name.isdigit():
            continue
        local_id = folder.name
        cover_path = folder / "images" / "1.jpg"
        if not cover_path.exists():
            continue

        try:
            data = cover_path.read_bytes()
            h = hashlib.md5(data).hexdigest()
        except Exception:
            continue

        if h not in LOGO_HASHES:
            continue

        # Found a logo cover
        anime_name = index_data.get(local_id, {}).get("name", local_id)
        print(f"\n  ❌ ID {local_id}: {anime_name} — logo cover detected")

        # Strategy 1: pa_anime_id.txt
        anime_id = id_map.get(local_id, "")

        # Strategy 2: sitemap name matching
        if not anime_id and anime_name:
            anime_id = find_anime_id_by_name(anime_name, sitemap)
            if anime_id:
                print(f"    Matched by name: {anime_id} ({sitemap[anime_id]})")

        if not anime_id:
            print(f"    ⚠️ Could not find anime_id, skipping")
            skipped += 1
            continue

        # Download cover
        cdn_url = download_cover(session, anime_id, cover_path)
        if cdn_url:
            new_cover = f"{IMG_BASE}/{local_id}/images/1.jpg"
            print(f"    ✅ Fixed: {cdn_url}")

            # Update info.json
            info_path = folder / "info.json"
            if info_path.exists():
                with open(info_path, "r", encoding="utf-8") as f:
                    info = json.load(f)
                info["cover"] = new_cover
                with open(info_path, "w", encoding="utf-8") as f:
                    json.dump(info, f, ensure_ascii=False, indent=2)

            # Update index.json
            if local_id in index_data:
                index_data[local_id]["cover"] = new_cover

            # Save to pa_anime_id.txt for future runs
            if local_id not in id_map:
                id_map[local_id] = anime_id

            fixed += 1
        else:
            print(f"    ❌ CDN download failed for anime_id={anime_id}")
            failed += 1

        time.sleep(1)

    # Save updated index.json
    if fixed > 0:
        for idx_path in [BASE_DIR / "index.json", Path("index.json")]:
            if idx_path.exists():
                with open(idx_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data.update(index_data)
                with open(idx_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

        # Save updated pa_anime_id.txt
        with open("pa_anime_id.txt", "w", encoding="utf-8") as f:
            json.dump(id_map, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"Fixed: {fixed}, Skipped: {skipped}, Failed: {failed}")
    return fixed


if __name__ == "__main__":
    fixed = main()
    sys.exit(0 if fixed >= 0 else 1)

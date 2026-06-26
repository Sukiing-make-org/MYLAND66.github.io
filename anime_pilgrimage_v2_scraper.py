#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Anime Pilgrimage V2 Scraper
============================
Pure requests-based scraper that parses Next.js RSC (React Server Components)
streaming payloads to extract pilgrimage point data including coordinates.

No Selenium required. All data (names, coordinates, images, episodes) is
embedded in the page HTML via __next_f.push() calls.

Usage:
    # Scrape recently updated anime (default 5)
    python anime_pilgrimage_v2_scraper.py --auto --max-anime 5

    # Fix existing zero-coordinate points
    python anime_pilgrimage_v2_scraper.py --only-fix-coords

    # Scrape + fix
    python anime_pilgrimage_v2_scraper.py --auto --max-anime 50 --fix-coords
"""

import os
import re
import json
import time
import random
import logging
import argparse
import hashlib
import sys
from pathlib import Path
from datetime import datetime

import requests

BASE_DIR = "pic/data"
LOCK_FILE = "anime_pilgrimage_v2_scraper.lock"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.5",
}

CDN_BASE = "https://cdn.animepilgrimage.com"
IMG_BASE = "https://image.xinu.ink/pic/data"


def setup_logging(name="V2Scraper"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        fh = logging.FileHandler("anime_pilgrimage_v2_scraper.log")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


class AnimePilgrimageV2Scraper:
    def __init__(self, base_dir=BASE_DIR, use_selenium=False):
        self.logger = setup_logging()
        self.base_dir = Path(base_dir)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.timeout = 30
        self.use_selenium = use_selenium
        self._driver = None

    def _get_selenium_driver(self):
        """Lazy-init Selenium Chrome driver."""
        if self._driver is None:
            try:
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options
                opts = Options()
                opts.add_argument("--headless")
                opts.add_argument("--no-sandbox")
                opts.add_argument("--disable-dev-shm-usage")
                opts.add_argument("--disable-gpu")
                self._driver = webdriver.Chrome(options=opts)
                self.logger.info("Selenium Chrome driver initialized")
            except Exception as e:
                self.logger.error(f"Failed to init Selenium: {e}")
                return None
        return self._driver

    def _close_selenium(self):
        if self._driver:
            try:
                self._driver.quit()
            except:
                pass
            self._driver = None

    # ── HTTP helpers ──────────────────────────────────────────────

    def _get(self, url, retries=3):
        for attempt in range(retries):
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    return resp.text
                self.logger.warning(f"HTTP {resp.status_code} for {url}")
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 10))
                    self.logger.info(f"Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    time.sleep(5 * (attempt + 1))
                    continue
                return None
            except requests.RequestException as e:
                self.logger.warning(f"Request error (attempt {attempt+1}): {e}")
                time.sleep(3 * (attempt + 1))
        # Fallback to Selenium if enabled
        if self.use_selenium:
            self.logger.info("Falling back to Selenium...")
            return self._get_via_selenium(url)
        return None

    def _get_via_selenium(self, url, wait_seconds=8):
        """Fetch page HTML using Selenium (handles Cloudflare + JS rendering)."""
        driver = self._get_selenium_driver()
        if not driver:
            return None
        try:
            driver.get(url)
            time.sleep(wait_seconds)
            # Scroll to trigger lazy loading
            for _ in range(3):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
            return driver.page_source
        except Exception as e:
            self.logger.error(f"Selenium error: {e}")
            return None

    # ── RSC payload parsing ───────────────────────────────────────

    def _extract_rsc_payloads(self, html):
        """Extract all __next_f.push() RSC payloads from HTML"""
        return re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)

    def _unescape_rsc(self, payload):
        """Unescape RSC escaped JSON string"""
        s = payload.replace('\\"', '"').replace('\\\\', '\\')
        s = s.replace('\\n', '\n').replace('\\t', '\t')
        return s

    def _extract_places_from_html(self, html):
        """Extract all place/marker objects from the HTML page.

        Tries multiple data sources in order:
        1. JSON-LD structured data (Schema.org TouristAttraction + GeoCoordinates)
        2. RSC __next_f.push() payloads with geo data
        3. Raw regex on the full HTML for geo patterns

        Returns a list of dicts with: name_ja, name_en, lat, lng, ep, type, image, etc.
        """
        places = []
        seen_coords = set()

        # ── Strategy 1: JSON-LD structured data ──────────────────
        ld_places = self._extract_from_jsonld(html)
        for p in ld_places:
            key = (round(p["lat"], 5), round(p["lng"], 5))
            if key not in seen_coords:
                seen_coords.add(key)
                places.append(p)
        if places:
            self.logger.info(f"  JSON-LD: {len(places)} places")

        # ── Strategy 2: RSC payloads ─────────────────────────────
        rsc_places = self._extract_from_rsc(html)
        for p in rsc_places:
            key = (round(p["lat"], 5), round(p["lng"], 5))
            if key not in seen_coords:
                seen_coords.add(key)
                places.append(p)
        if rsc_places:
            self.logger.info(f"  RSC: {len(rsc_places)} places ({len(places)} total unique)")

        # ── Strategy 3: Raw HTML geo pattern ─────────────────────
        if not places:
            raw_places = self._extract_from_raw_geo(html)
            for p in raw_places:
                key = (round(p["lat"], 5), round(p["lng"], 5))
                if key not in seen_coords:
                    seen_coords.add(key)
                    places.append(p)
            if raw_places:
                self.logger.info(f"  Raw HTML: {len(raw_places)} places")

        self.logger.info(f"Total extracted: {len(places)} places")
        return places

    def _extract_from_jsonld(self, html):
        """Extract places from Schema.org JSON-LD structured data."""
        places = []
        ld_scripts = re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        for script in ld_scripts:
            try:
                data = json.loads(script)
                items = data if isinstance(data, list) else data.get("@graph", [data])
                for item in items:
                    item_type = item.get("@type", "")
                    geo = item.get("geo", {})
                    if geo.get("@type") == "GeoCoordinates" and "latitude" in geo:
                        name = item.get("name", "")
                        # Try to get episode from the description or additionalProperty
                        ep = ""
                        for prop in item.get("additionalProperty", []):
                            if prop.get("name") == "episode":
                                ep = str(prop.get("value", ""))

                        # Get address for context
                        address = item.get("address", {})
                        address_str = address.get("streetAddress", "") if isinstance(address, dict) else ""

                        places.append({
                            "place_id": item.get("@id", ""),
                            "name_ja": name,
                            "name_en": name,
                            "lat": float(geo["latitude"]),
                            "lng": float(geo["longitude"]),
                            "ep": ep,
                            "type": "",
                            "image": "",
                            "title_ja": "",
                            "title_en": "",
                            "anime_id": "",
                            "anime_slug": "",
                            "street_view_url": "",
                            "scene_timestamp_sec": "",
                            "address": address_str,
                        })
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return places

    def _extract_from_rsc(self, html):
        """Extract places from Next.js RSC __next_f.push() payloads."""
        places = []
        payloads = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)

        for payload in payloads:
            # Unescape the RSC payload (double-escaped JSON)
            u = payload.replace('\\"', '"').replace('\\\\', '\\')

            # Find all geo entries
            geo_iter = re.finditer(
                r'"geo"\s*:\s*\{"latitude"\s*:\s*(-?\d+\.?\d*)\s*,\s*"longitude"\s*:\s*(-?\d+\.?\d*)\}',
                u
            )

            for geo_m in geo_iter:
                lat = float(geo_m.group(1))
                lng = float(geo_m.group(2))

                if lat == 0 and lng == 0:
                    continue

                # Search backwards from geo for name, ep, image
                search_start = max(0, geo_m.start() - 2000)
                search_end = min(len(u), geo_m.end() + 500)
                before = u[search_start:geo_m.start()]
                after = u[geo_m.start():search_end]

                # Name: last "name":"..." before geo
                name_matches = re.findall(r'"name"\s*:\s*"([^"]*)"', before)
                name_ja = name_matches[-1] if name_matches else ""

                # Episode: first "ep":N after geo
                ep_m = re.search(r'"ep"\s*:\s*(\d+)', after)
                ep = ep_m.group(1) if ep_m else ""

                # Type: "type":"EP" etc
                type_m = re.search(r'"type"\s*:\s*"([^"]*)"', after)
                ep_type = type_m.group(1) if type_m else ""

                # Image: "image":"..." after geo
                img_m = re.search(r'"image"\s*:\s*"([^"]*)"', after)
                image = img_m.group(1) if img_m else ""

                # Street View URL
                sv_m = re.search(r'"streetViewUrl"\s*:\s*"([^"]*)"', after)
                sv_url = sv_m.group(1) if sv_m else ""

                places.append({
                    "place_id": "",
                    "name_ja": name_ja,
                    "name_en": "",
                    "lat": lat,
                    "lng": lng,
                    "ep": ep,
                    "type": ep_type,
                    "image": image,
                    "title_ja": "",
                    "title_en": "",
                    "anime_id": "",
                    "anime_slug": "",
                    "street_view_url": sv_url,
                    "scene_timestamp_sec": "",
                })

        return places

    def _extract_from_raw_geo(self, html):
        """Fallback: extract geo data directly from raw HTML."""
        places = []
        # Pattern: "geo":{"latitude":X,"longitude":Y}
        for m in re.finditer(
            r'"geo"\s*:\s*\{"latitude"\s*:\s*(-?\d+\.?\d*)\s*,\s*"longitude"\s*:\s*(-?\d+\.?\d*)\}',
            html
        ):
            lat = float(m.group(1))
            lng = float(m.group(2))
            if lat == 0 and lng == 0:
                continue

            before = html[max(0, m.start() - 1000):m.start()]
            after = html[m.start():min(len(html), m.end() + 500)]

            name_matches = re.findall(r'"name"\s*:\s*"([^"]*)"', before)
            name = name_matches[-1] if name_matches else ""
            ep_m = re.search(r'"ep"\s*:\s*(\d+)', after)
            ep = ep_m.group(1) if ep_m else ""

            places.append({
                "place_id": "",
                "name_ja": name,
                "name_en": "",
                "lat": lat,
                "lng": lng,
                "ep": ep,
                "type": "",
                "image": "",
                "title_ja": "",
                "title_en": "",
                "anime_id": "",
                "anime_slug": "",
                "street_view_url": "",
                "scene_timestamp_sec": "",
            })
        return places

    # ── Anime list scraping ───────────────────────────────────────

    def get_anime_list(self, locale="ja", scroll_to_end=True):
        """Get anime list from the maps page.

        If scroll_to_end=True, uses Selenium to scroll through the page
        and load ALL anime (not just the initial batch).
        Falls back to single-page requests if Selenium is not available.

        Returns list of dicts: [{title, anime_id, slug, url}, ...]
        """
        url = f"https://www.animepilgrimage.com/{locale}/maps"
        self.logger.info(f"Fetching anime list from {url}")

        html = None

        # Try Selenium scrolling first (gets ALL anime)
        if scroll_to_end:
            html = self._get_anime_list_via_selenium(url)

        # Fallback to simple requests (only gets initial batch)
        if not html:
            self.logger.info("Falling back to requests (initial batch only)")
            html = self._get(url)

        if not html:
            self.logger.error("Failed to fetch anime list page")
            return []

        # Extract anime links: /maps/anime/{id}/{slug}
        anime_links = re.findall(
            r'/maps/anime/([a-zA-Z0-9_-]+)/([a-z0-9-]+)',
            html
        )

        # Extract titles from RSC payloads
        payloads = self._extract_rsc_payloads(html)
        title_map = {}
        for payload in payloads:
            unescaped = self._unescape_rsc(payload)
            links = re.findall(
                r'"href"\s*:\s*"/maps/anime/([a-zA-Z0-9_-]+)/([a-z0-9-]+)".*?"title"\s*:\s*"([^"]*)"',
                unescaped
            )
            for aid, slug, title in links:
                title_map[aid] = title

            items = re.findall(
                r'/maps/anime/([a-zA-Z0-9_-]+)/([a-z0-9-]+)',
                unescaped
            )
            for aid, slug in items:
                if aid not in title_map:
                    idx = unescaped.find(aid)
                    if idx >= 0:
                        nearby = unescaped[max(0,idx-500):idx+500]
                        tm = re.search(r'"children"\s*:\s*"([^"]{3,})"', nearby)
                        if tm:
                            title_map[aid] = tm.group(1)

        # Deduplicate
        seen = set()
        anime_list = []
        for aid, slug in anime_links:
            if aid in seen:
                continue
            seen.add(aid)
            anime_list.append({
                "anime_id": aid,
                "slug": slug,
                "title": title_map.get(aid, slug),
                "url": f"https://www.animepilgrimage.com/{locale}/maps/anime/{aid}/{slug}",
            })

        self.logger.info(f"Found {len(anime_list)} anime")
        for i, a in enumerate(anime_list[:5], 1):
            self.logger.info(f"  {i}. {a['title']} ({a['anime_id']})")
        if len(anime_list) > 5:
            self.logger.info(f"  ... and {len(anime_list)-5} more")

        return anime_list

    def _get_anime_list_via_selenium(self, url):
        """Use Selenium to scroll through the anime list page and get ALL anime.

        Returns the full page source HTML after all scrolling is complete.
        """
        driver = self._get_selenium_driver()
        if not driver:
            return None

        try:
            driver.get(url)
            time.sleep(5)

            # Scroll to load all anime (similar to old scraper's approach)
            last_height = driver.execute_script("return document.body.scrollHeight")
            scroll_attempts = 0
            max_scroll_attempts = 80
            no_change_count = 0
            max_no_change = 5

            while scroll_attempts < max_scroll_attempts and no_change_count < max_no_change:
                # Scroll in increments
                current_height = driver.execute_script("return document.body.scrollHeight")
                for i in range(3):
                    pos = current_height // 3 * (i + 1)
                    driver.execute_script(f"window.scrollTo(0, {pos});")
                    time.sleep(0.5)

                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)

                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    no_change_count += 1
                else:
                    no_change_count = 0
                last_height = new_height
                scroll_attempts += 1

                if scroll_attempts % 10 == 0:
                    # Count current links
                    current_links = re.findall(
                        r'/maps/anime/([a-zA-Z0-9_-]+)/([a-z0-9-]+)',
                        driver.page_source
                    )
                    self.logger.info(f"  Scroll {scroll_attempts}: {len(set(current_links))} anime loaded")

            html = driver.page_source
            final_links = re.findall(r'/maps/anime/([a-zA-Z0-9_-]+)/([a-z0-9-]+)', html)
            self.logger.info(f"Selenium scrolling complete: {len(set(final_links))} unique anime")
            return html

        except Exception as e:
            self.logger.error(f"Selenium scrolling error: {e}")
            return None

        self.logger.info(f"Found {len(anime_list)} anime")
        for i, a in enumerate(anime_list[:5], 1):
            self.logger.info(f"  {i}. {a['title']} ({a['anime_id']})")
        if len(anime_list) > 5:
            self.logger.info(f"  ... and {len(anime_list)-5} more")

        return anime_list

    # ── Single anime scraping ─────────────────────────────────────

    def scrape_anime(self, anime_info, local_folder_id):
        """Scrape a single anime's pilgrimage points.

        Args:
            anime_info: dict with keys: anime_id, slug, title, url
            local_folder_id: local folder ID to save data

        Returns:
            dict with anime_data, or None on failure
        """
        url = anime_info["url"]
        self.logger.info(f"Scraping: {anime_info['title']} -> {url}")

        html = self._get(url)
        if not html:
            self.logger.error(f"Failed to fetch {url}")
            return None

        # Extract places from RSC payloads
        places = self._extract_places_from_html(html)
        if not places:
            self.logger.warning(f"No places found for {anime_info['title']}")
            return None

        # Determine anime title (prefer Japanese)
        anime_title = places[0].get("title_ja", "") or anime_info.get("title", "")
        anime_title_en = places[0].get("title_en", "")

        # Build points list in the existing format
        folder_path = self.base_dir / str(local_folder_id)
        images_folder = folder_path / "images"
        os.makedirs(images_folder, exist_ok=True)

        points = []
        for i, place in enumerate(places, 1):
            # Download image if available
            img_url = ""
            if place["image"]:
                cdn_url = f"{CDN_BASE}/{place['image']}"
                img_filename = f"{local_folder_id}-{i}.jpg"
                img_path = images_folder / img_filename
                if self._download_image(cdn_url, img_path):
                    img_url = f"{IMG_BASE}/{local_folder_id}/images/{img_filename}"

            # Build point name (prefer Japanese, fallback to English)
            name = place["name_ja"] or place["name_en"] or f"Point {i}"

            # Build episode string
            ep_str = place["ep"] or ""
            if place["type"] and place["type"] != "EP":
                ep_str = place["type"] + (ep_str if ep_str else "")

            point = {
                "id": f"{local_folder_id}-{i}",
                "name": name,
                "image": img_url,
                "ep": ep_str,
                "geo": [place["lat"], place["lng"]],
            }

            # Skip points with zero coordinates (shouldn't happen with new data)
            if place["lat"] == 0 and place["lng"] == 0:
                self.logger.warning(f"  Skipping point with 0,0 coordinates: {name}")
                continue

            points.append(point)

        if not points:
            self.logger.warning(f"No valid points for {anime_info['title']}")
            return None

        # Save points.json
        points_path = folder_path / "points.json"
        with open(points_path, "w", encoding="utf-8") as f:
            json.dump({"points": points}, f, ensure_ascii=False, indent=2)

        # Create info.json
        info_data = {
            "id": self._generate_id(),
            "cn": "",  # Will be filled by Chinese name updater
            "title": anime_title,
            "cover": "",
            "pointsLength": len(points),
            "local_id": local_folder_id,
        }

        # Try to get cover from the HTML
        og_image = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
        if og_image:
            cover_url = og_image.group(1)
            cover_path = images_folder / "1.jpg"
            if self._download_image(cover_url, cover_path):
                info_data["cover"] = f"{IMG_BASE}/{local_folder_id}/images/1.jpg"

        info_path = folder_path / "info.json"
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(info_data, f, ensure_ascii=False, indent=2)

        # Build result for index.json update
        anime_data = {
            "name": anime_title,
            "name_cn": "",  # Will be filled by Chinese name updater
            "cover": info_data["cover"],
            "theme_color": "#7f6a95",
            "points": points,
        }

        self.logger.info(f"Scraped {len(points)} points for {anime_title}")
        return {
            "local_id": local_folder_id,
            "anime_data": anime_data,
        }

    # ── Fix zero-coordinate points ────────────────────────────────

    def fix_zero_coordinate_points(self, max_fix=0):
        """Scan database for [0,0] points and re-scrape them from the new site."""
        self.logger.info("Scanning for zero-coordinate points...")

        # Load index.json for anime info
        index_data = {}
        index_path = self.base_dir / "index.json"
        if index_path.exists():
            with open(index_path, "r", encoding="utf-8") as f:
                index_data = json.load(f)

        # Find anime with zero-coordinate points
        broken = []
        for str_id, data in index_data.items():
            points = data.get("points", [])
            has_zero = any(
                len(pt.get("geo", [])) == 2 and pt["geo"][0] == 0 and pt["geo"][1] == 0
                for pt in points
            )
            if has_zero:
                broken.append((str_id, data))

        if not broken:
            self.logger.info("No zero-coordinate points found!")
            return 0

        if max_fix > 0:
            broken = broken[:max_fix]

        self.logger.info(f"Found {len(broken)} anime with zero-coordinate points")

        # Fetch the anime list ONCE to build a name->URL mapping
        self.logger.info("Fetching anime list for URL mapping...")
        anime_list = self.get_anime_list(scroll_to_end=True)

        # Build lookup: normalized_name -> (anime_id, slug)
        name_to_url = {}
        for anime in anime_list:
            # Map by title
            title = anime.get("title", "")
            if title:
                normalized = re.sub(r'[^\w]', '', title).lower()
                name_to_url[normalized] = anime
            # Map by slug
            slug = anime.get("slug", "")
            if slug:
                slug_normalized = slug.replace("-", "").lower()
                name_to_url[slug_normalized] = anime

        total_fixed = 0
        for str_id, data in broken:
            try:
                anime_name = data.get("name", str_id)
                self.logger.info(f"Fixing: {anime_name} (ID {str_id})")

                # Look up the anime URL
                normalized = re.sub(r'[^\w]', '', anime_name).lower()
                matched = name_to_url.get(normalized)

                # Fuzzy match if exact match fails
                if not matched:
                    for key, anime in name_to_url.items():
                        if normalized in key or key in normalized:
                            matched = anime
                            break

                if not matched:
                    self.logger.warning(f"  Could not find '{anime_name}' on the new site")
                    continue

                # Fetch the anime detail page
                detail_url = matched["url"]
                self.logger.info(f"  Fetching {detail_url}")
                detail_html = self._get(detail_url)
                if not detail_html:
                    continue

                places = self._extract_places_from_html(detail_html)
                if not places:
                    self.logger.warning(f"  No places found for {anime_name}")
                    continue

                # Load existing points
                folder_path = self.base_dir / str_id
                points_path = folder_path / "points.json"
                if not points_path.exists():
                    continue

                with open(points_path, "r", encoding="utf-8") as f:
                    pts_data = json.load(f)
                existing_points = pts_data if isinstance(pts_data, list) else pts_data.get("points", [])

                # Match and fix zero-coordinate points by name
                fixed = 0
                for i, pt in enumerate(existing_points):
                    geo = pt.get("geo", [])
                    if len(geo) != 2 or geo[0] != 0 or geo[1] != 0:
                        continue

                    pt_name = pt.get("name", "")
                    best_match = None

                    # Exact match
                    for place in places:
                        if pt_name == place["name_ja"] or pt_name == place["name_en"]:
                            best_match = place
                            break

                    # Partial match
                    if not best_match:
                        for place in places:
                            if pt_name and (
                                pt_name in place["name_ja"]
                                or place["name_ja"] in pt_name
                                or pt_name in place["name_en"]
                                or place["name_en"] in pt_name
                            ):
                                best_match = place
                                break

                    if best_match:
                        existing_points[i]["geo"] = [best_match["lat"], best_match["lng"]]
                        self.logger.info(f"  Fixed '{pt_name}' -> [{best_match['lat']}, {best_match['lng']}]")
                        fixed += 1
                    else:
                        self.logger.warning(f"  Could not match '{pt_name}'")

                if fixed > 0:
                    with open(points_path, "w", encoding="utf-8") as f:
                        json.dump({"points": existing_points}, f, ensure_ascii=False, indent=2)

                    index_data[str_id]["points"] = existing_points
                    with open(index_path, "w", encoding="utf-8") as f:
                        json.dump(index_data, f, ensure_ascii=False, indent=2)

                    root_index = Path("index.json")
                    if root_index.exists():
                        with open(root_index, "r", encoding="utf-8") as f:
                            root_data = json.load(f)
                        if str_id in root_data:
                            root_data[str_id]["points"] = existing_points
                            with open(root_index, "w", encoding="utf-8") as f:
                                json.dump(root_data, f, ensure_ascii=False, indent=2)

                    total_fixed += fixed

                time.sleep(2)

            except Exception as e:
                self.logger.error(f"Error fixing {str_id}: {e}")
                continue

        self.logger.info(f"Fixed {total_fixed} zero-coordinate points total")
        return total_fixed

    # ── Index.json management ─────────────────────────────────────

    def update_index_json(self, anime_data_list):
        """Update index.json with new anime data."""
        index_path = self.base_dir / "index.json"
        if index_path.exists():
            with open(index_path, "r", encoding="utf-8") as f:
                index_data = json.load(f)
        else:
            index_data = {}

        for item in anime_data_list:
            local_id = str(item["local_id"])
            index_data[local_id] = {
                "name": item["anime_data"]["name"],
                "name_cn": item["anime_data"].get("name_cn", ""),
                "cover": item["anime_data"].get("cover", ""),
                "theme_color": item["anime_data"].get("theme_color", "#7f6a95"),
                "points": item["anime_data"]["points"],
                "inform": f"{IMG_BASE}/{local_id}/points.json",
            }

        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        root_index = Path("index.json")
        with open(root_index, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

    # ── Utility ───────────────────────────────────────────────────

    def get_next_available_local_id(self):
        folders = [int(f.name) for f in self.base_dir.glob("*") if f.is_dir() and f.name.isdigit()]
        return max(folders, default=0) + 1

    def is_anime_in_index(self, anime_id):
        """Check if anime already exists in index.json by anime_id (new format)."""
        index_path = self.base_dir / "index.json"
        if not index_path.exists():
            return False, None
        with open(index_path, "r", encoding="utf-8") as f:
            index_data = json.load(f)
        for str_id, data in index_data.items():
            # Check if this entry's anime_id matches
            # (We store anime_id in the slug or name)
            if data.get("anime_id") == anime_id:
                return True, int(str_id)
        return False, None

    def _generate_id(self):
        now = datetime.now()
        return int(f"{now.strftime('%Y%m%d%H%M%S')}{random.randint(100, 999)}")

    def _download_image(self, url, save_path):
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                save_path = Path(save_path)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                return True
        except Exception as e:
            self.logger.debug(f"Image download failed: {e}")
        return False

    # ── Main run ──────────────────────────────────────────────────

    def run(self, auto_mode=True, max_anime=5, fix_coords=False):
        """Main entry point.

        Args:
            auto_mode: Always True for V2 (no manual mode)
            max_anime: Max anime to scrape
            fix_coords: Also fix existing zero-coordinate points

        Returns:
            dict with results, or False on error
        """
        self.logger.info("=" * 60)
        self.logger.info("Anime Pilgrimage V2 Scraper - Starting")
        self.logger.info("=" * 60)

        try:
            # Get anime list
            anime_list = self.get_anime_list()
            if not anime_list:
                self.logger.error("No anime found. Exiting.")
                return False

            # Limit to max_anime
            anime_to_scrape = anime_list[:max_anime]
            self.logger.info(f"Will scrape {len(anime_to_scrape)} anime")

            local_id = self.get_next_available_local_id()
            results = []
            new_anime = []

            for i, anime_info in enumerate(anime_to_scrape, 1):
                self.logger.info(f"\n[{i}/{len(anime_to_scrape)}] {anime_info['title']}")

                result = self.scrape_anime(anime_info, local_id)
                if result:
                    results.append(result)
                    new_anime.append({
                        "name": anime_info["title"],
                        "id": local_id,
                        "points": result["anime_data"]["points"],
                    })
                    self.update_index_json([result])
                    self.logger.info(f"  Saved {len(result['anime_data']['points'])} points")
                else:
                    self.logger.warning(f"  No data for {anime_info['title']}")

                local_id += 1
                time.sleep(2)  # Rate limiting between anime

            # Fix zero coordinates if requested
            fixed_count = 0
            if fix_coords:
                self.logger.info("\n--- Fixing zero-coordinate points ---")
                fixed_count = self.fix_zero_coordinate_points()

            # Return results
            return {
                "new_anime": new_anime,
                "fixed_coords": fixed_count,
                "total_scraped": len(results),
            }

        except Exception as e:
            self.logger.error(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self._close_selenium()


def main():
    parser = argparse.ArgumentParser(description="Anime Pilgrimage V2 Scraper")
    parser.add_argument("--auto", action="store_true", default=True)
    parser.add_argument("--max-anime", type=int, default=5)
    parser.add_argument("--base-dir", type=str, default=BASE_DIR)
    parser.add_argument("--fix-coords", action="store_true", default=False)
    parser.add_argument("--only-fix-coords", action="store_true", default=False)
    parser.add_argument("--use-selenium", action="store_true", default=False,
                        help="Use Selenium for fetching pages (handles Cloudflare)")
    args = parser.parse_args()

    scraper = AnimePilgrimageV2Scraper(
        base_dir=args.base_dir,
        use_selenium=args.use_selenium,
    )

    if args.only_fix_coords:
        fixed = scraper.fix_zero_coordinate_points()
        print(f"\nFixed {fixed} zero-coordinate points.")
        sys.exit(0 if fixed >= 0 else 1)

    result = scraper.run(
        auto_mode=True,
        max_anime=args.max_anime,
        fix_coords=args.fix_coords,
    )

    if result:
        print(f"\nDone! Scraped {result['total_scraped']} anime, fixed {result['fixed_coords']} coords.")
        sys.exit(0)
    else:
        print("\nFailed.")
        sys.exit(1)


if __name__ == "__main__":
    main()

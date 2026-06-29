#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AniTabi.jp Crawler
==================
Crawls https://www.anitabi.jp for anime pilgrimage (seichi junrei) data.

Architecture:
  Phase 1: GET /works → extract all work_id + title (single page, 313+ works)
  Phase 2: GET /works/{id} → parse JSON-LD for work info + spot_id list
  Phase 3: GET /spots/{id} → parse JSON-LD for coordinates (lat, lng)
  Phase 4: Download images → save to pic/data/{local_id}/images/

Key insight: Coordinates are ONLY on /spots/{id} pages, NOT on /works/{id}.
All structured data is in <script type="application/ld+json"> tags.

Usage:
    # Full crawl (all works)
    python anitabi_jp_crawler.py --full

    # Crawl specific works by ID
    python anitabi_jp_crawler.py --work-ids 1 2 5

    # Incremental update (only new/changed works)
    python anitabi_jp_crawler.py --update

    # Limit number of works
    python anitabi_jp_crawler.py --full --max-works 10
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
from collections import Counter
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import requests

BASE_DIR = "pic/data"
LOCK_FILE = "anitabi_jp_crawler.lock"
MAPPING_FILE = "pa_anitabi_jp.txt"
IMG_BASE = "https://image.xinu.ink/pic/data"

BASE_URL = "https://www.anitabi.jp"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.5",
    "Referer": "https://www.anitabi.jp/",
}

IMG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://www.anitabi.jp/",
}


def setup_logging(name="AniTabiJP"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        fh = logging.FileHandler("anitabi_jp_crawler.log")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


class AniTabiJPCrawler:
    def __init__(self, base_dir=BASE_DIR):
        self.logger = setup_logging()
        self.base_dir = Path(base_dir)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._img_session = requests.Session()
        self._img_session.headers.update(IMG_HEADERS)

    # ── HTTP helpers ──────────────────────────────────────────────

    def _get(self, url, retries=3):
        for attempt in range(retries):
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    return resp.text
                self.logger.warning(f"HTTP {resp.status_code} for {url}")
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After", "10")
                    wait = int(ra) if ra.isdigit() else 10
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
        return None

    def _download_image(self, url, save_path, retries=3):
        for attempt in range(retries):
            try:
                resp = self._img_session.get(url, timeout=20)
                if resp.status_code == 200 and len(resp.content) > 500:
                    save_path = Path(save_path)
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(save_path, "wb") as f:
                        f.write(resp.content)
                    return True
                elif resp.status_code == 200:
                    self.logger.debug(f"Image too small ({len(resp.content)} bytes): {url}")
                else:
                    self.logger.debug(f"Image HTTP {resp.status_code}: {url}")
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(3 * (attempt + 1))
                else:
                    self.logger.warning(f"Image download failed: {url} ({e})")
        return False

    def _extract_theme_color(self, image_path, default="#7f6a95"):
        """Extract a representative theme color from a cover image.

        Uses color quantization + frequency counting, excluding near-white,
        near-black, and near-grey pixels so book covers with white backgrounds
        don't produce a washed-out grey. Falls back to a simple average if the
        frequency approach finds nothing valid.

        Args:
            image_path: path to the downloaded cover image
            default: fallback color if extraction fails

        Returns:
            hex color string like "#a1b2c3"
        """
        try:
            from PIL import Image
        except ImportError:
            self.logger.debug("Pillow not installed, using default theme_color")
            return default

        import warnings
        try:
            img = Image.open(image_path)
            img = img.convert("RGB")
            img = img.resize((80, 80))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                pixels = list(img.getdata())
            img.close()

            valid = []
            for r, g, b in pixels:
                mx = max(r, g, b)
                mn = min(r, g, b)
                if mx > 235 and mn > 220:
                    continue
                if mx < 30:
                    continue
                if mx - mn < 15:
                    continue
                valid.append((r, g, b))

            if not valid:
                self.logger.debug(f"  No saturated pixels, using average")
                r_avg = sum(p[0] for p in pixels) // len(pixels)
                g_avg = sum(p[1] for p in pixels) // len(pixels)
                b_avg = sum(p[2] for p in pixels) // len(pixels)
                return "#{:02x}{:02x}{:02x}".format(r_avg, g_avg, b_avg)

            quantized = Counter(
                (r // 24, g // 24, b // 24) for r, g, b in valid
            )
            top = quantized.most_common(1)[0][0]
            r = top[0] * 24 + 12
            g = top[1] * 24 + 12
            b = top[2] * 24 + 12
            hex_color = "#{:02x}{:02x}{:02x}".format(min(r, 255), min(g, 255), min(b, 255))
            self.logger.info(f"  Extracted theme_color: {hex_color}")
            return hex_color
        except Exception as e:
            self.logger.debug(f"Error extracting theme color: {e}")
            return default

    # ── JSON-LD extraction ────────────────────────────────────────

    def _extract_jsonld(self, html):
        """Extract all JSON-LD blocks from HTML."""
        scripts = re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        results = []
        for script in scripts:
            try:
                data = json.loads(script)
                if isinstance(data, list):
                    results.extend(data)
                else:
                    results.append(data)
            except json.JSONDecodeError:
                continue
        return results

    # ── Phase 1: Works list ──────────────────────────────────────

    def get_works_list(self):
        """Fetch /works and extract all work_id + title.

        The works list page is paginated with ?page=N (about 34 works per page).
        Iterates through all pages until an empty/duplicate page is found.

        Returns list of dicts: [{work_id, title, url}, ...]
        """
        self.logger.info("Fetching works list from /works (paginated) ...")

        works = []
        seen = set()
        empty_page_count = 0
        max_empty_pages = 2
        page = 1

        while empty_page_count < max_empty_pages:
            url = f"{BASE_URL}/works?page={page}" if page > 1 else f"{BASE_URL}/works"
            html = self._get(url)
            if not html:
                self.logger.warning(f"Failed to fetch works page {page}")
                empty_page_count += 1
                page += 1
                continue

            page_works = self._extract_works_from_html(html)

            new_count = 0
            for w in page_works:
                if w["work_id"] not in seen:
                    seen.add(w["work_id"])
                    works.append(w)
                    new_count += 1

            self.logger.info(f"  Page {page}: {len(page_works)} works ({new_count} new, {len(works)} total)")

            if new_count == 0:
                empty_page_count += 1
            else:
                empty_page_count = 0

            page += 1
            time.sleep(1.0)

            if page > 30:
                break

        works.sort(key=lambda w: w["work_id"])
        self.logger.info(f"Found {len(works)} works across {page - 1} pages")
        return works

    def _extract_works_from_html(self, html):
        """Extract work_id + title from a single works list page HTML."""
        works = []
        seen = set()

        for m in re.finditer(
            r'<a\s+href="https://www\.anitabi\.jp/works/(\d+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        ):
            wid = int(m.group(1))
            if wid in seen:
                continue
            seen.add(wid)

            block = m.group(2)
            title = ""
            title_m = re.search(r'<h3[^>]*>([^<]+)</h3>', block)
            if title_m:
                title = title_m.group(1).strip()

            works.append({
                "work_id": wid,
                "title": title,
                "url": f"{BASE_URL}/works/{wid}",
            })

        return works

    # ── Phase 2: Work detail ─────────────────────────────────────

    def get_work_detail(self, work_id):
        """Fetch /works/{id} and extract work info + spot_id list.

        Returns dict: {
            work_id, title, title_en, year, genre, studio, description,
            cover_url, official_url, spots: [{spot_id, name, url}, ...]
        }
        """
        url = f"{BASE_URL}/works/{work_id}"
        self.logger.info(f"Fetching work detail: {url}")
        html = self._get(url)
        if not html:
            self.logger.error(f"Failed to fetch work {work_id}")
            return None

        result = {
            "work_id": work_id,
            "title": "",
            "title_en": "",
            "year": "",
            "genre": "",
            "studio": "",
            "description": "",
            "cover_url": "",
            "official_url": "",
            "spots": [],
        }

        jsonld_blocks = self._extract_jsonld(html)
        for block in jsonld_blocks:
            atype = block.get("@type", "")

            if atype == "TVSeries" or atype == "Movie":
                result["title"] = block.get("name", "")
                result["title_en"] = block.get("alternateName", "")
                result["description"] = block.get("description", "")
                result["cover_url"] = block.get("image", "")
                result["year"] = block.get("datePublished", "")
                genre = block.get("genre", "")
                if isinstance(genre, list):
                    genre = ", ".join(genre)
                result["genre"] = genre
                prod = block.get("productionCompany", {})
                if isinstance(prod, dict):
                    result["studio"] = prod.get("name", "")

            if atype == "ItemList":
                for item in block.get("itemListElement", []):
                    spot_url = item.get("url", "")
                    spot_name = item.get("name", "")
                    spot_id_match = re.search(r'/spots/(\d+)$', spot_url)
                    if spot_id_match:
                        result["spots"].append({
                            "spot_id": int(spot_id_match.group(1)),
                            "name": spot_name,
                            "url": spot_url,
                        })

        if not result["title"]:
            og_title = re.search(r'<meta[^>]*og:title[^>]*content="([^"]*)"', html)
            if og_title:
                result["title"] = og_title.group(1).split(" - ")[0].strip()

        if not result["cover_url"]:
            og_image = re.search(r'<meta[^>]*og:image[^>]*content="([^"]*)"', html)
            if og_image:
                result["cover_url"] = og_image.group(1)

        if not result["spots"]:
            self.logger.info(f"JSON-LD had no spots for work {work_id}, parsing HTML cards")
            result["spots"] = self._extract_spot_ids_from_html(html)

        page = 2
        existing_ids = {s["spot_id"] for s in result["spots"]}
        while page <= 30:
                page_html = self._get(f"{BASE_URL}/works/{work_id}?page={page}")
                if not page_html:
                    break
                page_spots = self._extract_spot_ids_from_html(page_html)
                if not page_spots:
                    break
                added = 0
                for s in page_spots:
                    if s["spot_id"] not in existing_ids:
                        result["spots"].append(s)
                        existing_ids.add(s["spot_id"])
                        added += 1
                if added == 0:
                    break
                page += 1
                time.sleep(1.0)

        self.logger.info(f"Work {work_id}: '{result['title']}' - {len(result['spots'])} spots")
        return result

    def _extract_spot_ids_from_html(self, html):
        """Extract spot IDs from HTML card links as fallback."""
        spots = []
        seen = set()
        for m in re.finditer(r'href="https://www\.anitabi\.jp/spots/(\d+)"', html):
            spot_id = int(m.group(1))
            if spot_id not in seen:
                seen.add(spot_id)
                name = ""
                after = html[m.end():m.end() + 500]
                name_m = re.search(r'<h3[^>]*>([^<]+)</h3>', after)
                if name_m:
                    name = name_m.group(1).strip()
                spots.append({
                    "spot_id": spot_id,
                    "name": name,
                    "url": f"{BASE_URL}/spots/{spot_id}",
                })
        return spots

    # ── Phase 3: Spot detail ─────────────────────────────────────

    def get_spot_detail(self, spot_id):
        """Fetch /spots/{id} and extract name + coordinates.

        Returns dict: {spot_id, name, name_en, lat, lng, address, image_url, description}
        or None on failure.
        """
        url = f"{BASE_URL}/spots/{spot_id}"
        html = self._get(url)
        if not html:
            self.logger.warning(f"Failed to fetch spot {spot_id}")
            return None

        result = {
            "spot_id": spot_id,
            "name": "",
            "name_en": "",
            "lat": None,
            "lng": None,
            "address": "",
            "image_url": "",
            "description": "",
        }

        jsonld_blocks = self._extract_jsonld(html)
        for block in jsonld_blocks:
            if block.get("@type") == "TouristAttraction":
                result["name"] = block.get("name", "")
                result["description"] = block.get("description", "")
                result["image_url"] = block.get("image", "")
                address = block.get("address", {})
                if isinstance(address, dict):
                    result["address"] = address.get("streetAddress", "")
                elif isinstance(address, str):
                    result["address"] = address
                geo = block.get("geo", {})
                if geo.get("@type") == "GeoCoordinates":
                    lat = geo.get("latitude")
                    lng = geo.get("longitude")
                    if lat is not None and lng is not None:
                        result["lat"] = float(lat)
                        result["lng"] = float(lng)

        if result["lat"] is None or result["lng"] is None:
            coord_m = re.search(r'>(\d{2,3}\.\d{3,})\s*,\s*(\d{2,3}\.\d{3,})<', html)
            if coord_m:
                lat = float(coord_m.group(1))
                lng = float(coord_m.group(2))
                if 20 <= lat <= 50 and 120 <= lng <= 160:
                    result["lat"] = lat
                    result["lng"] = lng

        if result["lat"] is None or result["lng"] is None:
            maps_m = re.search(r'destination=(\d+\.\d+),(\d+\.\d+)', html)
            if maps_m:
                lat = float(maps_m.group(1))
                lng = float(maps_m.group(2))
                if 20 <= lat <= 50 and 120 <= lng <= 160:
                    result["lat"] = lat
                    result["lng"] = lng

        if not result["name"]:
            title_m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
            if title_m:
                result["name"] = title_m.group(1).strip()

        if not result["name_en"]:
            h1_pos = html.find('<h1')
            if h1_pos >= 0:
                after_h1 = html[h1_pos:h1_pos + 2000]
                en_m = re.search(r'<p\s+class="[^"]*text-gray-400[^"]*">([^<]+)</p>', after_h1)
                if en_m:
                    en_val = en_m.group(1).strip()
                    skip = {"言語", "language", "Language", "home", "作品一覧", "マップ", "ニュース"}
                    if en_val and en_val not in skip and en_val != result["name"]:
                        result["name_en"] = en_val

        return result

    # ── Data saving ──────────────────────────────────────────────

    def get_next_local_id(self):
        folders = [int(f.name) for f in self.base_dir.glob("*")
                   if f.is_dir() and f.name.isdigit()]
        return max(folders, default=0) + 1

    def _generate_id(self):
        now = datetime.now()
        return int(f"{now.strftime('%Y%m%d%H%M%S')}{random.randint(100, 999)}")

    def save_work(self, work_detail, spots_data, local_id):
        """Save work data to pic/data/{local_id}/.

        Args:
            work_detail: dict from get_work_detail()
            spots_data: list of dicts from get_spot_detail()
            local_id: local folder ID
        """
        folder_path = self.base_dir / str(local_id)
        images_dir = folder_path / "images"
        os.makedirs(images_dir, exist_ok=True)

        cover_url = ""
        theme_color = "#7f6a95"
        if work_detail.get("cover_url"):
            original_url = work_detail["cover_url"].split("?")[0]
            ext = os.path.splitext(urlparse(original_url).path)[1] or ".jpg"
            cover_filename = f"cover{ext}"
            cover_path = images_dir / cover_filename
            if self._download_image(original_url, cover_path):
                cover_url = f"{IMG_BASE}/{local_id}/images/{cover_filename}"
                theme_color = self._extract_theme_color(cover_path)

        info_data = {
            "id": self._generate_id(),
            "cn": "",
            "title": work_detail.get("title", ""),
            "cover": cover_url,
            "theme_color": theme_color,
            "pointsLength": len(spots_data),
            "local_id": local_id,
        }

        if work_detail.get("title_en"):
            info_data["title_en"] = work_detail["title_en"]
        if work_detail.get("year"):
            info_data["year"] = work_detail["year"]
        if work_detail.get("genre"):
            info_data["genre"] = work_detail["genre"]
        if work_detail.get("studio"):
            info_data["studio"] = work_detail["studio"]

        with open(folder_path / "info.json", "w", encoding="utf-8") as f:
            json.dump(info_data, f, ensure_ascii=False, indent=2)

        points = []
        for i, spot in enumerate(spots_data, 1):
            if spot is None:
                continue

            img_url = ""
            if spot.get("image_url"):
                original_img_url = spot["image_url"].split("?")[0]
                ext = os.path.splitext(urlparse(original_img_url).path)[1] or ".jpg"
                img_filename = f"{spot['spot_id']}{ext}"
                img_path = images_dir / img_filename
                if self._download_image(original_img_url, img_path):
                    img_url = f"{IMG_BASE}/{local_id}/images/{img_filename}"

            point = {
                "id": str(spot["spot_id"]),
                "name": spot.get("name", ""),
                "image": img_url,
                "geo": [spot["lat"], spot["lng"]] if spot["lat"] is not None and spot["lng"] is not None else [],
            }

            if spot.get("name_en"):
                point["name_en"] = spot["name_en"]
            if spot.get("address"):
                point["address"] = spot["address"]

            points.append(point)

        with open(folder_path / "points.json", "w", encoding="utf-8") as f:
            json.dump({"points": points}, f, ensure_ascii=False, indent=2)

        anime_data = {
            "name": work_detail.get("title", ""),
            "name_cn": "",
            "cover": cover_url,
            "theme_color": theme_color,
            "points": points,
        }

        self.logger.info(f"Saved work '{work_detail.get('title', '')}' -> local_id {local_id} ({len(points)} points)")
        return {
            "local_id": local_id,
            "anime_data": anime_data,
        }

    # ── Index.json management ─────────────────────────────────────

    def update_index_json(self, anime_data_list):
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

    # ── Mapping file management ───────────────────────────────────

    def _load_mapping(self):
        """Load pa_anitabi_jp.txt: {local_id_str: work_id_int}"""
        path = Path(MAPPING_FILE)
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_mapping(self, data):
        with open(MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_work_mapping(self, local_id, work_id):
        data = self._load_mapping()
        data[str(local_id)] = work_id
        self._save_mapping(data)

    def _find_local_id_by_work_id(self, work_id):
        data = self._load_mapping()
        for str_id, wid in data.items():
            if wid == work_id:
                return int(str_id)
        return None

    # ── Duplicate detection ───────────────────────────────────────

    def _is_work_in_index(self, title):
        if not title:
            return False
        for idx_path in [self.base_dir / "index.json", Path("index.json")]:
            if not idx_path.exists():
                continue
            try:
                with open(idx_path, "r", encoding="utf-8") as f:
                    index_data = json.load(f)
                for _, entry in index_data.items():
                    db_name = entry.get("name", "")
                    if db_name and db_name == title:
                        return True
                    normalized = re.sub(r'[^\w\s\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf]', '', title)
                    normalized = re.sub(r'\s+', '', normalized).lower()
                    db_normalized = re.sub(r'[^\w\s\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf]', '', db_name)
                    db_normalized = re.sub(r'\s+', '', db_normalized).lower()
                    if normalized and db_normalized and normalized == db_normalized:
                        return True
            except Exception:
                continue
        return False

    # ── Lock file ────────────────────────────────────────────────

    @staticmethod
    def create_lock_file():
        try:
            with open(LOCK_FILE, "w") as f:
                f.write(str(datetime.now()))
            return True
        except Exception as e:
            logging.error(f"Error creating lock file: {e}")
            return False

    @staticmethod
    def remove_lock_file():
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
            return True
        except Exception as e:
            logging.error(f"Error removing lock file: {e}")
            return False

    @staticmethod
    def is_process_running():
        return os.path.exists(LOCK_FILE)

    # ── Incremental update ────────────────────────────────────────

    def update_existing_work(self, work_detail, local_id):
        """Incremental update: fetch new spots and add only new ones."""
        folder_path = self.base_dir / str(local_id)
        points_path = folder_path / "points.json"
        if not points_path.exists():
            self.logger.warning(f"No points.json for ID {local_id}, will scrape fresh")
            return None

        with open(points_path, "r", encoding="utf-8") as f:
            pts_data = json.load(f)
        existing_points = pts_data if isinstance(pts_data, list) else pts_data.get("points", [])

        existing_ids = set()
        existing_coords = set()
        for pt in existing_points:
            pt_id = pt.get("id", "")
            if pt_id:
                existing_ids.add(str(pt_id))
            geo = pt.get("geo", [])
            if len(geo) == 2:
                existing_coords.add((round(geo[0], 5), round(geo[1], 5)))

        self.logger.info(f"  Existing points: {len(existing_points)}")

        new_spot_ids = []
        for spot_info in work_detail.get("spots", []):
            sid = str(spot_info["spot_id"])
            if sid not in existing_ids:
                new_spot_ids.append(spot_info)

        if not new_spot_ids:
            self.logger.info(f"  No new spots found")
            return None

        self.logger.info(f"  Found {len(new_spot_ids)} new spots to fetch")

        images_dir = folder_path / "images"
        os.makedirs(images_dir, exist_ok=True)

        new_points = []
        next_idx = len(existing_points) + 1
        for spot_info in new_spot_ids:
            spot = self.get_spot_detail(spot_info["spot_id"])
            if not spot or spot["lat"] is None:
                self.logger.warning(f"  Skipping spot {spot_info['spot_id']}: no coordinates")
                time.sleep(1.5)
                continue

            coord_key = (round(spot["lat"], 5), round(spot["lng"], 5))
            if coord_key in existing_coords:
                self.logger.debug(f"  Skipping spot {spot_info['spot_id']}: duplicate coordinates")
                time.sleep(1.5)
                continue

            img_url = ""
            if spot.get("image_url"):
                original_img_url = spot["image_url"].split("?")[0]
                ext = os.path.splitext(urlparse(original_img_url).path)[1] or ".jpg"
                img_filename = f"{spot['spot_id']}{ext}"
                img_path = images_dir / img_filename
                if self._download_image(original_img_url, img_path):
                    img_url = f"{IMG_BASE}/{local_id}/images/{img_filename}"

            point = {
                "id": str(spot["spot_id"]),
                "name": spot.get("name", ""),
                "image": img_url,
                "geo": [spot["lat"], spot["lng"]],
            }
            if spot.get("name_en"):
                point["name_en"] = spot["name_en"]
            if spot.get("address"):
                point["address"] = spot["address"]

            new_points.append(point)
            existing_coords.add(coord_key)
            existing_ids.add(str(spot["spot_id"]))

            time.sleep(1.5)

        if not new_points:
            self.logger.info(f"  No new valid points after fetching")
            return None

        all_points = existing_points + new_points
        with open(points_path, "w", encoding="utf-8") as f:
            json.dump({"points": all_points}, f, ensure_ascii=False, indent=2)

        info_path = folder_path / "info.json"
        existing_cover = ""
        theme_color = "#7f6a95"
        if info_path.exists():
            with open(info_path, "r", encoding="utf-8") as f:
                info_data = json.load(f)
            info_data["pointsLength"] = len(all_points)
            existing_cover = info_data.get("cover", "")
            theme_color = info_data.get("theme_color", "#7f6a95")
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(info_data, f, ensure_ascii=False, indent=2)

            if theme_color == "#7f6a95" and existing_cover:
                cover_match = re.search(r'/images/(cover\.\w+)', existing_cover)
                if cover_match:
                    cover_path = folder_path / "images" / cover_match.group(1)
                    if cover_path.exists():
                        theme_color = self._extract_theme_color(cover_path)
                        with open(info_path, "r", encoding="utf-8") as f:
                            info_data = json.load(f)
                        info_data["theme_color"] = theme_color
                        with open(info_path, "w", encoding="utf-8") as f:
                            json.dump(info_data, f, ensure_ascii=False, indent=2)

        anime_data = {
            "name": work_detail.get("title", ""),
            "name_cn": "",
            "cover": existing_cover,
            "theme_color": theme_color,
            "points": all_points,
        }

        self.update_index_json([{"local_id": local_id, "anime_data": anime_data}])

        self.logger.info(f"  Added {len(new_points)} new points (total: {len(all_points)})")
        return {
            "local_id": local_id,
            "anime_data": anime_data,
            "new_points_count": len(new_points),
        }

    # ── Main run ──────────────────────────────────────────────────

    def run(self, mode="full", max_works=0, work_ids=None):
        """Main entry point.

        Args:
            mode: 'full' for all works, 'update' for incremental
            max_works: limit number of works to process (0 = no limit)
            work_ids: specific work IDs to process
        """
        self.logger.info("=" * 60)
        self.logger.info("AniTabi.jp Crawler - Starting")
        self.logger.info(f"Mode: {mode}")
        self.logger.info("=" * 60)

        if self.is_process_running():
            self.logger.warning("Another instance is already running")
            return False

        if not self.create_lock_file():
            self.logger.error("Failed to create lock file")
            return False

        try:
            mapping = self._load_mapping()
            existing_by_work_id = {}
            for str_id, wid in mapping.items():
                existing_by_work_id[wid] = int(str_id)

            if work_ids:
                works_to_process = [{"work_id": wid, "title": "", "url": f"{BASE_URL}/works/{wid}"} for wid in work_ids]
            else:
                all_works = self.get_works_list()
                if not all_works:
                    self.logger.error("No works found")
                    return False

                if mode == "update":
                    works_to_process = []
                    for w in all_works:
                        if w["work_id"] not in existing_by_work_id:
                            works_to_process.append(w)
                    self.logger.info(f"Update mode: {len(works_to_process)} new works out of {len(all_works)}")
                else:
                    works_to_process = all_works

            if max_works > 0:
                works_to_process = works_to_process[:max_works]

            self.logger.info(f"Will process {len(works_to_process)} works")

            local_id = self.get_next_local_id()
            results = []
            new_works = []
            updated_works = []

            for i, work_info in enumerate(works_to_process, 1):
                work_id = work_info["work_id"]
                self.logger.info(f"\n[{i}/{len(works_to_process)}] Work {work_id}: {work_info.get('title', '')}")

                work_detail = self.get_work_detail(work_id)
                if not work_detail:
                    self.logger.warning(f"  Failed to get work detail for {work_id}")
                    time.sleep(2)
                    continue

                if not work_detail["spots"]:
                    self.logger.info(f"  No spots for work {work_id}, skipping")
                    time.sleep(2)
                    continue

                if work_id in existing_by_work_id:
                    existing_local_id = existing_by_work_id[work_id]
                    self.logger.info(f"  Already exists (local_id {existing_local_id}), checking for updates...")
                    update_result = self.update_existing_work(work_detail, existing_local_id)
                    if update_result:
                        updated_works.append({
                            "name": work_detail["title"],
                            "id": existing_local_id,
                            "new_points": update_result["new_points_count"],
                        })
                        self.logger.info(f"  Added {update_result['new_points_count']} new points")
                    else:
                        self.logger.info(f"  No updates needed")
                    time.sleep(2)
                    continue

                if self._is_work_in_index(work_detail["title"]):
                    self.logger.info(f"  '{work_detail['title']}' already in index.json (different source), skipping")
                    time.sleep(2)
                    continue

                spots_data = []
                for j, spot_info in enumerate(work_detail["spots"], 1):
                    self.logger.info(f"  [{j}/{len(work_detail['spots'])}] Fetching spot {spot_info['spot_id']}: {spot_info.get('name', '')}")
                    spot = self.get_spot_detail(spot_info["spot_id"])
                    spots_data.append(spot)
                    time.sleep(1.5)

                valid_spots = [s for s in spots_data if s and s["lat"] is not None]
                if not valid_spots:
                    self.logger.warning(f"  No valid spots with coordinates for work {work_id}")
                    time.sleep(2)
                    continue

                result = self.save_work(work_detail, spots_data, local_id)
                if result:
                    self._save_work_mapping(local_id, work_id)
                    results.append(result)
                    new_works.append({
                        "name": work_detail["title"],
                        "id": local_id,
                        "work_id": work_id,
                        "points": len(valid_spots),
                    })
                    self.update_index_json([result])
                    local_id += 1

                time.sleep(2)

            summary = f"Done! New: {len(new_works)}, Updated: {len(updated_works)}"
            self.logger.info(summary)

            return {
                "new_works": new_works,
                "updated_works": updated_works,
                "total_processed": len(results),
            }

        except Exception as e:
            self.logger.error(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self.remove_lock_file()
            self.logger.info("Lock file removed")


def main():
    parser = argparse.ArgumentParser(description="AniTabi.jp Crawler")
    parser.add_argument("--full", action="store_true", help="Full crawl of all works")
    parser.add_argument("--update", action="store_true", help="Incremental update (new works only)")
    parser.add_argument("--work-ids", type=int, nargs="+", help="Specific work IDs to crawl")
    parser.add_argument("--max-works", type=int, default=0, help="Limit number of works to process")
    parser.add_argument("--base-dir", type=str, default=BASE_DIR, help="Base data directory")
    args = parser.parse_args()

    crawler = AniTabiJPCrawler(base_dir=args.base_dir)

    if args.work_ids:
        result = crawler.run(mode="full", work_ids=args.work_ids, max_works=args.max_works)
    elif args.update:
        result = crawler.run(mode="update", max_works=args.max_works)
    elif args.full:
        result = crawler.run(mode="full", max_works=args.max_works)
    else:
        parser.print_help()
        sys.exit(1)

    if result:
        print(f"\nDone! New: {len(result['new_works'])}, Updated: {len(result['updated_works'])}")
        sys.exit(0)
    else:
        print("\nFailed.")
        sys.exit(1)


if __name__ == "__main__":
    main()

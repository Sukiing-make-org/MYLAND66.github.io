#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import requests
import re
import datetime
import random
import argparse
import sys
import logging
import urllib.parse
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from PIL import Image
import colorsys

# Lock file path for process coordination
LOCK_FILE = "anime_pilgrimage_scraper.lock"
# Base directory for anime data
BASE_DIR = "pic/data"

class AnimePilgrimageScraper:
    def __init__(self, base_dir=BASE_DIR, headless=True, auto_mode=True):
        # Set up logging
        self.logger = self.setup_logging()

        self.base_url = "https://www.animepilgrimage.com/ja"
        self.recently_updated_url = f"{self.base_url}/RecentlyUpdated"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
        }
        self.base_dir = Path(base_dir)
        self.headless = headless
        self.auto_mode = auto_mode
        self.setup_driver()

    def setup_logging(self):
        """Set up logging configuration"""
        logger = logging.getLogger("AnimePilgrimageScraper")
        logger.setLevel(logging.INFO)

        # Create handlers
        c_handler = logging.StreamHandler()
        f_handler = logging.FileHandler("anime_pilgrimage_scraper.log")
        c_handler.setLevel(logging.INFO)
        f_handler.setLevel(logging.INFO)

        # Create formatters and add to handlers
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        c_handler.setFormatter(formatter)
        f_handler.setFormatter(formatter)

        # Add handlers to the logger
        logger.addHandler(c_handler)
        logger.addHandler(f_handler)

        return logger

    def setup_driver(self):
        """Set up the Chrome driver with mobile emulation"""
        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument("--headless")  # Run in headless mode
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        # Set up mobile emulation
        mobile_emulation = {
            "deviceMetrics": {"width": 375, "height": 812, "pixelRatio": 3.0},
            "userAgent": self.headers["User-Agent"]
        }
        chrome_options.add_experimental_option("mobileEmulation", mobile_emulation)

        self.driver = webdriver.Chrome(options=chrome_options)
        self.logger.info("Chrome driver initialized successfully")

    def get_anime_list(self):
        """Get the list of anime from the recently updated page"""
        self.logger.info("Fetching anime list from recently updated page...")
        self.driver.get(self.recently_updated_url)

        # Save the page source for debugging before waiting
        with open("page_source_initial.html", "w", encoding="utf-8") as f:
            f.write(self.driver.page_source)
        self.logger.info("Saved initial page source to page_source_initial.html")

        # Wait for the page to load with expanded selectors
        selectors_to_try = [
            ".anime-list .anime-item",
            "a[href*='/Map?data=']",
            "a[href*='/ja/Map?data=']",
            ".anime-item",
            ".anime-card",
            ".anime-entry",
            "a[href*='Map']",
            "div[class*='anime']",
            "a[href*='anime']",
            "a[href*='data=']",
            "a[href*='pilgrimage']",
            "a[href*='location']",
            "a[href*='spot']",
            "a[href*='point']",
            "a"
        ]

        found_selector = None
        for selector in selectors_to_try:
            try:
                self.logger.info(f"Trying selector: {selector}")
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                found_selector = selector
                self.logger.info(f"Found elements with selector: {selector}")
                break
            except TimeoutException:
                self.logger.warning(f"Selector {selector} failed")
                continue

        if not found_selector:
            self.logger.error("Could not find anime list elements with any selector. The website structure might have changed.")
            # Save the page source for debugging
            with open("page_source.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            self.logger.error("Saved page source to page_source.html for debugging.")
            return []

        # Scroll to the bottom to load all anime
        print("Scrolling to load all anime...")
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        scroll_attempts = 0
        max_scroll_attempts = 50  # Increase max scroll attempts
        no_change_count = 0
        max_no_change = 5  # Number of consecutive no-change scrolls before stopping

        while scroll_attempts < max_scroll_attempts and no_change_count < max_no_change:
            # Scroll down in smaller increments to ensure content loads
            for i in range(3):  # Scroll in 3 steps
                current_height = self.driver.execute_script("return document.body.scrollHeight")
                scroll_position = current_height // 3 * (i + 1)  # Scroll 1/3, 2/3, then full height
                self.driver.execute_script(f"window.scrollTo(0, {scroll_position});")
                time.sleep(1)  # Short pause between incremental scrolls

            # Final scroll to bottom
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(4)  # Wait longer for content to load

            # Check if more content loaded
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                no_change_count += 1
                print(f"No new content loaded: {no_change_count}/{max_no_change}")
            else:
                no_change_count = 0  # Reset counter if height changed

            last_height = new_height
            scroll_attempts += 1
            print(f"Scroll attempt {scroll_attempts}/{max_scroll_attempts} - Page height: {new_height}px")

            # Add a manual pause every 10 scrolls to let user verify progress
            if scroll_attempts % 10 == 0:
                print("\nPausing to let content load. Press Enter to continue scrolling...")
                input()

        # Extract anime items - try different selectors
        anime_list = []

        # Use the found selector or try all selectors in sequence
        anime_items = []
        selectors_for_items = [
            ".anime-list .anime-item",
            "a[href*='/Map?data=']",
            "a[href*='/ja/Map?data=']",
            ".anime-item",
            ".anime-card",
            ".anime-entry",
            "a[href*='Map']",
            "div[class*='anime']",
            "a[href*='anime']",
            "a[href*='data=']",
            "a[href*='pilgrimage']",
            "a[href*='location']",
            "a[href*='spot']",
            "a[href*='point']"
        ]

        # If we found a working selector earlier, try it first
        if found_selector:
            selectors_for_items.insert(0, found_selector)

        for selector in selectors_for_items:
            self.logger.info(f"Trying to find anime items with selector: {selector}")
            anime_items = self.driver.find_elements(By.CSS_SELECTOR, selector)
            if anime_items:
                self.logger.info(f"Found {len(anime_items)} anime items with selector: {selector}")
                break

        # If still no items found, try a more generic approach - any links
        if not anime_items:
            self.logger.warning("No anime items found with specific selectors, trying generic links")
            anime_items = self.driver.find_elements(By.TAG_NAME, "a")
            # Filter links that might be anime items (contain 'Map' or 'anime' in href)
            anime_items = [item for item in anime_items if item.get_attribute("href") and
                          ("Map" in item.get_attribute("href") or
                           "anime" in item.get_attribute("href") or
                           "data=" in item.get_attribute("href"))]
            self.logger.info(f"Found {len(anime_items)} potential anime items with generic link selector")

        self.logger.info(f"Found {len(anime_items)} anime items total")

        for i, item in enumerate(anime_items, 1):
            try:
                # Try different ways to extract title
                try:
                    title = item.find_element(By.CSS_SELECTOR, ".anime-title").text
                except:
                    try:
                        title = item.find_element(By.CSS_SELECTOR, "h3").text
                    except:
                        try:
                            title = item.get_attribute("title")
                        except:
                            # Extract title from href as last resort
                            href = item.get_attribute("href")
                            data_part = href.split("data=")[-1]
                            title = data_part.replace("-", " ").title()

                # Get the link
                link = item.get_attribute("href")

                # Make sure link is absolute
                if not link.startswith("http"):
                    link = f"https://www.animepilgrimage.com{link}"

                anime_list.append({
                    "id": i,
                    "title": title,
                    "link": link
                })
                print(f"{i}. {title} - {link}")
            except Exception as e:
                print(f"Error extracting anime item {i}: {e}")

        return anime_list

    def generate_timestamp_id(self):
        """Generate a unique ID based on current timestamp"""
        now = datetime.datetime.now()
        timestamp = now.strftime("%Y%m%d%H%M%S")
        # Add a random number to avoid collisions
        random_suffix = random.randint(100, 999)
        return int(f"{timestamp}{random_suffix}")

    @staticmethod
    def create_lock_file():
        """Create a lock file to indicate that the scraper is running"""
        try:
            with open(LOCK_FILE, 'w') as f:
                f.write(str(datetime.datetime.now()))
            return True
        except Exception as e:
            logging.error(f"Error creating lock file: {e}")
            return False

    @staticmethod
    def remove_lock_file():
        """Remove the lock file"""
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
            return True
        except Exception as e:
            logging.error(f"Error removing lock file: {e}")
            return False

    @staticmethod
    def is_process_running():
        """Check if another instance of the scraper is running"""
        exists = os.path.exists(LOCK_FILE)
        print(f"DEBUG: Checking if lock file exists: {LOCK_FILE}, result: {exists}")
        if exists:
            print(f"DEBUG: Lock file content: {open(LOCK_FILE, 'r').read() if os.path.exists(LOCK_FILE) else 'File not found'}")
        return exists

    @staticmethod
    def is_monthly_updater_running():
        """Check if the monthly updater is running by looking for its lock file"""
        return os.path.exists("anitabi_updater.lock")

    def get_next_available_local_id(self):
        """Find the next available local ID by checking existing folders and apiid.json"""
        try:
            # Get the highest folder number in pic/data
            folders = [int(f.name) for f in self.base_dir.glob('*') if f.is_dir() and f.name.isdigit()]
            highest_folder = max(folders) if folders else 0

            # Check apiid.json for the highest local ID
            highest_api_id = 0
            if os.path.exists('apiid.json'):
                with open('apiid.json', 'r', encoding='utf-8') as f:
                    apiid_data = json.load(f)
                    local_ids = [int(k) for k in apiid_data.keys()]
                    highest_api_id = max(local_ids) if local_ids else 0

            # Use the higher of the two values and add 1
            next_id = max(highest_folder, highest_api_id) + 1
            self.logger.info(f"Next available local ID: {next_id}")
            return next_id
        except Exception as e:
            self.logger.error(f"Error finding next available local ID: {e}")
            return 5901  # Default fallback value

    def get_chinese_name_from_bangumi(self, japanese_name):
        """Get Chinese name for an anime from Bangumi API

        Args:
            japanese_name: The Japanese name of the anime

        Returns:
            str: Chinese name if found, otherwise the original Japanese name
        """
        try:
            self.logger.info(f"Fetching Chinese name for anime: {japanese_name}")

            # URL encode the Japanese name for the API request
            encoded_name = urllib.parse.quote(japanese_name)
            url = f"https://api.bgm.tv/search/subject/{encoded_name}?type=1&responseGroup=small"

            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                data = response.json()

                # Check if we got results
                if data.get('results', 0) > 0 and len(data.get('list', [])) > 0:
                    # Get the first result with a non-empty Chinese name
                    for item in data['list']:
                        if item.get('name_cn') and item.get('name_cn').strip():
                            chinese_name = item['name_cn']
                            self.logger.info(f"Found Chinese name: {chinese_name}")
                            return chinese_name

                    # If no item has a Chinese name, return the original name
                    self.logger.info(f"No Chinese name found in Bangumi API results")
                    return japanese_name
                else:
                    self.logger.info(f"No results found in Bangumi API for: {japanese_name}")
                    return japanese_name
            else:
                self.logger.warning(f"Failed to get data from Bangumi API: {response.status_code}")
                return japanese_name
        except Exception as e:
            self.logger.error(f"Error fetching Chinese name from Bangumi API: {e}")
            return japanese_name

    def extract_dominant_color(self, image_path):
        """Extract the dominant color from an image"""
        try:
            img = Image.open(image_path)
            img = img.resize((100, 100))  # Resize for faster processing
            img = img.convert("RGBA")

            pixels = list(img.getdata())
            r_total = g_total = b_total = count = 0

            for r, g, b, a in pixels:
                if a > 200:  # Only consider mostly opaque pixels
                    r_total += r
                    g_total += g
                    b_total += b
                    count += 1

            if count == 0:
                return "#7f6a95"  # Default color if no valid pixels

            r_avg = r_total // count
            g_avg = g_total // count
            b_avg = b_total // count

            # Convert to hex
            hex_color = "#{:02x}{:02x}{:02x}".format(r_avg, g_avg, b_avg)
            return hex_color
        except Exception as e:
            print(f"Error extracting dominant color: {e}")
            return "#7f6a95"  # Default color

    def create_info_json(self, anime_data, local_folder_id):
        """Create info.json file for the anime"""
        try:
            # Generate a unique ID based on timestamp
            unique_id = self.generate_timestamp_id()

            # Create info.json content
            info_data = {
                "id": unique_id,
                "cn": anime_data["name_cn"],  # Use Chinese name from Bangumi API
                "title": anime_data["name"],
                "cover": anime_data["cover"],
                "pointsLength": len(anime_data["points"]),
                "local_id": local_folder_id
            }

            # Save info.json
            folder_path = self.base_dir / str(local_folder_id)
            os.makedirs(folder_path, exist_ok=True)
            info_path = folder_path / "info.json"

            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(info_data, f, ensure_ascii=False, indent=2)

            print(f"Created info.json with ID: {unique_id}")
            return info_path
        except Exception as e:
            print(f"Error creating info.json: {e}")
            return None

    def download_image(self, url, save_path):
        """Download an image from a URL"""
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                # Ensure save_path is a Path object
                save_path = Path(save_path)
                # Ensure parent directory exists
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                return True
            else:
                print(f"Failed to download image: {url}, status code: {response.status_code}")
                return False
        except Exception as e:
            print(f"Error downloading image {url}: {e}")
            return False

    def _extract_coordinates_from_point(self, point_elem):
        """Extract coordinates from a point element using all available methods

        Tries in order:
        1. Direct link inspection (debug logging)
        2. CSS selectors for map links with regex pattern extraction
        3. HTML content regex fallback (href attributes, element IDs, text)

        Args:
            point_elem: Selenium WebElement for a pilgrimage point

        Returns:
            tuple: (lat, lng, map_url)
        """
        lat = lng = 0
        map_url = ""

        # Debug: print all links in the element
        all_links = []
        try:
            all_links = point_elem.find_elements(By.TAG_NAME, "a")
            self.logger.info(f"  Found {len(all_links)} links in point element")
            for link in all_links:
                try:
                    href = link.get_attribute("href")
                    if href and ("map" in href.lower() or "google" in href.lower()):
                        self.logger.info(f"  Link: {href}")
                except:
                    pass
        except Exception as e:
            self.logger.info(f"  Error finding links: {e}")

        # If no links found directly, try regex on the HTML
        if len(all_links) == 0:
            try:
                point_html = point_elem.get_attribute('outerHTML')
                html_links = re.findall(r'href="([^"]*google[^"]*map[^"]*)"', point_html)
                html_links.extend(re.findall(r"href='([^']*google[^']*map[^']*)'", point_html))
                self.logger.info(f"  Found {len(html_links)} Google Maps links in HTML")
                for href in html_links:
                    self.logger.info(f"  Link from HTML: {href}")
            except Exception as e:
                self.logger.info(f"  Error extracting links from HTML: {e}")

        # Comprehensive patterns for extracting coordinates from map URLs
        url_patterns = [
            r'[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'@(-?\d+\.\d+),(-?\d+\.\d+)',
            r'[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'[?&]center=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'[?&]destination=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'[?&]daddr=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'[?&]saddr=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'[?&]query=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'[?&]loc:(-?\d+\.\d+),(-?\d+\.\d+)',
            r'[?&]loc=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'[?&]lat=(-?\d+\.\d+).*[?&]lon=(-?\d+\.\d+)',
            r'[?&]lat=(-?\d+\.\d+).*[?&]lng=(-?\d+\.\d+)',
            r'[?&]latitude=(-?\d+\.\d+).*[?&]longitude=(-?\d+\.\d+)',
        ]

        # CSS selectors for map links
        map_link_selectors = [
            (By.CSS_SELECTOR, "a[href*='google.com/maps']"),
            (By.CSS_SELECTOR, "a[href*='maps.google']"),
            (By.CSS_SELECTOR, "a[href*='goo.gl/maps']"),
            (By.CSS_SELECTOR, "a[href*='maps']"),
            (By.CSS_SELECTOR, "a[href*='map']"),
            (By.CSS_SELECTOR, "a[href*='location']"),
            (By.CSS_SELECTOR, "a[href*='place']"),
            (By.CSS_SELECTOR, "a[href*='geo']"),
            (By.CSS_SELECTOR, "a[href*='coordinates']"),
            (By.CSS_SELECTOR, "a[href*='lat']"),
            (By.CSS_SELECTOR, "a[href*='lng']"),
            (By.CSS_SELECTOR, "a[href*='position']"),
            (By.CSS_SELECTOR, "a[href*='directions']"),
            (By.CSS_SELECTOR, "a[href*='route']"),
            (By.CSS_SELECTOR, "a[href*='navigate']"),
            (By.CSS_SELECTOR, "a[href*='osm']"),
            (By.CSS_SELECTOR, "a[href*='openstreetmap']"),
            (By.CSS_SELECTOR, "a[href*='bing.com/maps']"),
            (By.CSS_SELECTOR, "a[href*='apple.com/maps']"),
            (By.CSS_SELECTOR, "a[href*='waze']"),
            (By.CSS_SELECTOR, "a[href*='yandex']"),
            (By.CSS_SELECTOR, "a[href*='2gis']"),
            (By.CSS_SELECTOR, "a[href*='mapbox']"),
            (By.CSS_SELECTOR, "a[href*='here.com']"),
            (By.CSS_SELECTOR, "a[href*='mapquest']"),
            (By.CSS_SELECTOR, "a[href*='baidu']"),
            (By.CSS_SELECTOR, "a[href*='amap']"),
            (By.CSS_SELECTOR, "a[href*='tencent']"),
            (By.CSS_SELECTOR, "a[href*='naver']"),
            (By.CSS_SELECTOR, "a[href*='kakao']"),
        ]

        for selector in map_link_selectors:
            try:
                map_link_elems = point_elem.find_elements(*selector)
                for map_link_elem in map_link_elems:
                    href = map_link_elem.get_attribute("href")
                    if href:
                        self.logger.info(f"  Found map link: {href}")
                        for pattern in url_patterns:
                            coords_match = re.search(pattern, href)
                            if coords_match:
                                lat = float(coords_match.group(1))
                                lng = float(coords_match.group(2))
                                map_url = href
                                self.logger.info(f"  Found coordinates: {lat}, {lng}")
                                break
                        if lat != 0 and lng != 0:
                            break
                if lat != 0 and lng != 0:
                    break
            except Exception as e:
                self.logger.info(f"  Error with map link selector {selector}: {e}")
                continue

        # Fallback: try to find coordinates in the HTML content
        if lat == 0 and lng == 0:
            try:
                point_html = point_elem.get_attribute('outerHTML')

                html_href_patterns = [
                    r'destination=(-?\d+\.\d+),(-?\d+\.\d+)',
                    r'[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)',
                    r'@(-?\d+\.\d+),(-?\d+\.\d+)',
                    r'[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)',
                ]
                for pattern in html_href_patterns:
                    coords_match = re.search(pattern, point_html)
                    if coords_match:
                        lat = float(coords_match.group(1))
                        lng = float(coords_match.group(2))
                        self.logger.info(f"  Found coordinates in HTML href: {lat}, {lng}")
                        break

                # Try element ID (common pattern: id="136.39212")
                if lat == 0 and lng == 0:
                    id_match = re.search(r'id="(\d+\.\d+)"', point_html)
                    if id_match:
                        potential_lng = float(id_match.group(1))
                        lat_match = re.search(
                            r'(\d+\.\d+)[^\d]+' + re.escape(id_match.group(1)), point_html
                        ) or re.search(
                            re.escape(id_match.group(1)) + r'[^\d]+(\d+\.\d+)', point_html
                        )
                        if lat_match:
                            lat = float(lat_match.group(1))
                            lng = potential_lng
                            self.logger.info(f"  Found coordinates from element ID: {lat}, {lng}")

                # Try text content
                if lat == 0 and lng == 0:
                    all_text = point_elem.text
                    text_patterns = [
                        r'(-?\d+\.\d+),\s*(-?\d+\.\d+)',
                        r'lat\s*[=:]\s*(-?\d+\.\d+).*lon\s*[=:]\s*(-?\d+\.\d+)',
                        r'latitude\s*[=:]\s*(-?\d+\.\d+).*longitude\s*[=:]\s*(-?\d+\.\d+)',
                        r'N\s*(-?\d+\.\d+).*E\s*(-?\d+\.\d+)',
                        r'\((-?\d+\.\d+),\s*(-?\d+\.\d+)\)',
                    ]
                    for pattern in text_patterns:
                        coords_match = re.search(pattern, all_text)
                        if coords_match:
                            lat = float(coords_match.group(1))
                            lng = float(coords_match.group(2))
                            self.logger.info(f"  Found coordinates in text: {lat}, {lng}")
                            break
            except Exception as e:
                self.logger.info(f"  Error finding coordinates in HTML/text: {e}")

        return lat, lng, map_url

    def scrape_anime(self, anime_info, local_folder_id, manual_edit=False):
        """Scrape a specific anime page"""
        self.logger.info(f"Scraping anime: {anime_info['title']}")

        # Create folder structure
        folder_path = self.base_dir / str(local_folder_id)
        images_folder = folder_path / "images"
        os.makedirs(images_folder, exist_ok=True)

        # Visit the anime page
        self.driver.get(anime_info['link'])

        # Wait for the page to load
        try:
            # Try different selectors for the anime page
            selectors_to_try = [
                (By.CSS_SELECTOR, ".anime-detail"),
                (By.CSS_SELECTOR, ".anime-header"),
                (By.CSS_SELECTOR, "h1"),
                (By.TAG_NAME, "img")  # At least there should be images on the page
            ]

            for selector in selectors_to_try:
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located(selector)
                    )
                    print(f"Page loaded, found element with selector: {selector}")
                    break
                except TimeoutException:
                    continue
            else:
                print(f"Timeout waiting for anime page to load: {anime_info['link']}")
                # Save the page source for debugging
                with open(f"anime_page_{local_folder_id}.html", "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                print(f"Saved page source to anime_page_{local_folder_id}.html for debugging.")
                return None
        except Exception as e:
            print(f"Error waiting for page to load: {e}")
            return None

        # Extract anime title
        anime_title = anime_info['title']  # Default to the title from the list
        try:
            # Try different selectors for the title
            title_selectors = [
                (By.CSS_SELECTOR, ".anime-detail__title"),
                (By.CSS_SELECTOR, ".anime-header h1"),
                (By.CSS_SELECTOR, "h1"),
                (By.CSS_SELECTOR, ".title")
            ]

            for selector in title_selectors:
                try:
                    title_elem = self.driver.find_element(*selector)
                    if title_elem.text.strip():
                        anime_title = title_elem.text.strip()
                        print(f"Found title: {anime_title}")
                        break
                except:
                    continue
        except Exception as e:
            print(f"Error extracting anime title, using default: {e}")

        # Extract cover image
        cover_image_url = ""  # Default empty URL
        try:
            # Try different selectors for the cover image
            cover_selectors = [
                (By.CSS_SELECTOR, ".anime-detail__cover"),
                (By.CSS_SELECTOR, ".anime-header img"),
                (By.CSS_SELECTOR, ".cover-image"),
                (By.CSS_SELECTOR, "img[alt*='cover']"),
                (By.CSS_SELECTOR, "img.main-image"),
                (By.CSS_SELECTOR, "img.anime-image"),
                (By.CSS_SELECTOR, "img.header-image"),
                (By.CSS_SELECTOR, "img:not([alt*='logo'])")  # Any image that's not a logo
            ]

            # First try specific selectors
            for selector in cover_selectors:
                try:
                    cover_img = self.driver.find_element(*selector)
                    cover_url = cover_img.get_attribute("src")
                    if cover_url and (cover_url.endswith(".jpg") or cover_url.endswith(".png") or cover_url.endswith(".jpeg") or "image" in cover_url):
                        cover_path = images_folder / "1.jpg"
                        if self.download_image(cover_url, cover_path):
                            cover_image_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/1.jpg"
                            print(f"Downloaded cover image: {cover_url}")
                            break
                except:
                    continue

            # If still no cover, try to find the largest image on the page
            if not cover_image_url:
                print("Trying to find the largest image on the page...")
                try:
                    all_images = self.driver.find_elements(By.TAG_NAME, "img")
                    largest_image = None
                    largest_size = 0

                    for img in all_images:
                        try:
                            # Skip small icons and logos
                            width = int(img.get_attribute("width") or 0)
                            height = int(img.get_attribute("height") or 0)
                            size = width * height

                            # Skip very small images (likely icons)
                            if width < 100 or height < 100:
                                continue

                            # Skip images with certain keywords in src or alt
                            src = img.get_attribute("src") or ""
                            alt = img.get_attribute("alt") or ""
                            if any(keyword in src.lower() or keyword in alt.lower() for keyword in ["logo", "icon", "button"]):
                                continue

                            if size > largest_size:
                                largest_size = size
                                largest_image = img
                        except:
                            continue

                    if largest_image is not None:
                        cover_url = largest_image.get_attribute("src")
                        if cover_url:
                            cover_path = images_folder / "1.jpg"
                            if self.download_image(cover_url, cover_path):
                                cover_image_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/1.jpg"
                                print(f"Downloaded largest image as cover: {cover_url}")
                except Exception as e:
                    print(f"Error finding largest image: {e}")

            # If still no cover, try to take a screenshot of the header area
            if not cover_image_url:
                print("Taking screenshot of header area as cover image...")
                try:
                    # Try to find a header element
                    header_selectors = [
                        (By.CSS_SELECTOR, ".anime-header"),
                        (By.CSS_SELECTOR, ".header"),
                        (By.CSS_SELECTOR, "header")
                    ]

                    # Initialize header_elem as None
                    header_elem = None

                    # First try to find header using selectors
                    for selector in header_selectors:
                        try:
                            header_elem = self.driver.find_element(*selector)
                            break
                        except:
                            continue

                    # If no header found, try to use h1's parent
                    if header_elem is None:
                        try:
                            h1_elem = self.driver.find_element(By.TAG_NAME, "h1")
                            header_elem = h1_elem.find_element(By.XPATH, "./..")
                            print("Using h1 parent as header element")
                        except:
                            pass

                    if header_elem:
                        # Take screenshot of the header element
                        cover_path = f"{images_folder}/1.jpg"
                        header_elem.screenshot(cover_path)
                        cover_image_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/1.jpg"
                        print("Created cover image from header screenshot")
                    else:
                        # Take screenshot of the top portion of the page
                        cover_path = f"{images_folder}/1.jpg"
                        self.driver.save_screenshot(cover_path)
                        cover_image_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/1.jpg"
                        print("Created cover image from page screenshot")
                except Exception as e:
                    print(f"Error taking screenshot for cover: {e}")

            if not cover_image_url:
                print("Could not find or create a valid cover image")
        except Exception as e:
            print(f"Error extracting cover image: {e}")

        # Extract theme color from cover image
        theme_color = "#7f6a95"  # Default color
        if os.path.exists(f"{images_folder}/1.jpg"):
            theme_color = self.extract_dominant_color(f"{images_folder}/1.jpg")

        # Scroll to load all pilgrimage points
        print("Scrolling to load all pilgrimage points...")
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        scroll_attempts = 0
        max_scroll_attempts = 15

        while scroll_attempts < max_scroll_attempts:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)  # Wait longer for content to load
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                # Try one more scroll to be sure
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)
                new_height = self.driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
            last_height = new_height
            scroll_attempts += 1
            print(f"Scroll attempt {scroll_attempts}/{max_scroll_attempts}")

        # Extract pilgrimage points
        points = []
        try:
            # Try different selectors for pilgrimage points based on the provided example
            point_selectors = [
                (By.CSS_SELECTOR, ".map-side__component"),  # Main container for each point
                (By.CSS_SELECTOR, ".component__inner"),     # Inner container
                (By.CSS_SELECTOR, ".pilgrimage-point"),     # Fallback
                (By.CSS_SELECTOR, ".location-item"),        # Fallback
                (By.CSS_SELECTOR, ".map-marker"),           # Fallback
                (By.CSS_SELECTOR, "div[id^='3']"),          # Divs with IDs starting with numbers (might be coordinates)
                (By.CSS_SELECTOR, "div[class*='map']"),     # Any div with 'map' in class name
                (By.CSS_SELECTOR, "div[class*='point']"),    # Any div with 'point' in class name
                (By.CSS_SELECTOR, "div[class*='location']")  # Any div with 'location' in class name
            ]

            point_elements = []
            for selector in point_selectors:
                try:
                    elements = self.driver.find_elements(*selector)
                    if elements:
                        point_elements = elements
                        print(f"Found {len(elements)} pilgrimage points with selector: {selector}")
                        break
                except:
                    continue

            # If we still don't have points, try to find any elements with Google Maps links
            if not point_elements:
                try:
                    # Try different types of map links
                    map_link_patterns = [
                        "a[href*='google.com/maps']",
                        "a[href*='maps.google']",
                        "a[href*='goo.gl/maps']"
                    ]

                    for pattern in map_link_patterns:
                        map_links = self.driver.find_elements(By.CSS_SELECTOR, pattern)
                        if map_links:
                            print(f"Found {len(map_links)} Google Maps links with pattern: {pattern}")
                            # For each map link, try to find its parent container
                            for link in map_links:
                                try:
                                    # Try to get a parent container that might contain more information
                                    # First try direct parent
                                    parent = link.find_element(By.XPATH, "./..")
                                    point_elements.append(parent)
                                except:
                                    try:
                                        # Try grandparent if direct parent doesn't work
                                        parent = link.find_element(By.XPATH, "./../..")
                                        point_elements.append(parent)
                                    except:
                                        # If all else fails, use the link itself
                                        point_elements.append(link)
                                        print("Using map link directly as a point element")

                            # If we found links with this pattern, no need to try others
                            if point_elements:
                                break

                except Exception as e:
                    print(f"Error finding Google Maps links: {e}")

            # If we still have no points, try a more aggressive approach
            if not point_elements:
                print("No point elements found with standard methods. Trying alternative approach...")
                try:
                    # Look for any elements that might contain location information
                    # First try to find elements with location-related class names
                    location_patterns = [
                        "*[class*='location']",
                        "*[class*='place']",
                        "*[class*='spot']",
                        "*[class*='point']",
                        "*[class*='map']",
                        "div.card",  # Common pattern for location cards
                        "li.item"     # Common pattern for list items
                    ]

                    for pattern in location_patterns:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, pattern)
                        if elements:
                            print(f"Found {len(elements)} potential location elements with pattern: {pattern}")
                            point_elements.extend(elements)
                            break

                    # If still no elements, look for images that might be pilgrimage points
                    if not point_elements:
                        images = self.driver.find_elements(By.TAG_NAME, "img")
                        non_icon_images = [img for img in images if int(img.get_attribute("width") or 0) > 100]
                        if non_icon_images:
                            print(f"Found {len(non_icon_images)} potential location images")
                            for img in non_icon_images:
                                try:
                                    # Try to get parent container of image
                                    parent = img.find_element(By.XPATH, "./../..")
                                    point_elements.append(parent)
                                except:
                                    continue

                except Exception as e:
                    print(f"Error in alternative point finding approach: {e}")

            print(f"Processing {len(point_elements)} pilgrimage points")

            for i, point_elem in enumerate(point_elements, 1):
                try:
                    # Extract point name
                    name = "Unknown Location"  # Default name

                    # Print the HTML of the point element for debugging
                    try:
                        point_html = point_elem.get_attribute('outerHTML')
                        print(f"\nPoint element HTML (first 200 chars): {point_html[:200]}...")
                    except Exception as e:
                        print(f"Could not get point element HTML: {e}")

                    # Try more specific name selectors first based on the provided example
                    name_selectors = [
                        (By.CSS_SELECTOR, ".title__text"),       # From example
                        (By.CSS_SELECTOR, "h2.title__text"),     # From example
                        (By.CSS_SELECTOR, "h2"),                # Any h2
                        (By.CSS_SELECTOR, ".inner__title h2"),  # From example structure
                        (By.CSS_SELECTOR, ".point-name"),       # Fallback
                        (By.CSS_SELECTOR, ".location-name"),    # Fallback
                        (By.CSS_SELECTOR, "h3"),               # Fallback
                        (By.CSS_SELECTOR, "h4"),               # Fallback
                        (By.CSS_SELECTOR, "strong"),            # Fallback
                        (By.CSS_SELECTOR, ".title"),            # Fallback
                        (By.CSS_SELECTOR, "[class*='title']"),  # Fallback
                        (By.CSS_SELECTOR, "[class*='name']"),   # Fallback
                        (By.CSS_SELECTOR, "p"),                 # Sometimes names are in paragraphs
                        (By.CSS_SELECTOR, "span"),               # Or in spans
                        (By.CSS_SELECTOR, "img[title]")          # Try to get title from img title attribute
                    ]

                    for selector in name_selectors:
                        try:
                            name_elems = point_elem.find_elements(*selector)
                            for name_elem in name_elems:
                                text = name_elem.text.strip()
                                if text and len(text) > 1:  # Ensure it's not just a single character
                                    name = text
                                    print(f"  Found name '{name}' with selector {selector}")
                                    break
                            if name != "Unknown Location":
                                break
                        except Exception as e:
                            print(f"  Error with name selector {selector}: {e}")
                            continue

                    # If still no name, try to get any text content
                    if name == "Unknown Location":
                        try:
                            # Get all text from the element
                            all_text = point_elem.text.strip()
                            if all_text:
                                # Split by newlines and take the first non-empty line
                                lines = [line.strip() for line in all_text.split('\n') if line.strip()]
                                if lines:
                                    # Use the first line that's not too long as the name
                                    for line in lines:
                                        if 1 < len(line) < 50:  # Reasonable length for a name
                                            name = line
                                            print(f"  Using first text line as name: '{name}'")
                                            break
                        except Exception as e:
                            print(f"  Error getting text content: {e}")

                    # Extract episode number based on the provided example
                    ep = ""  # Default empty episode
                    ep_selectors = [
                        (By.CSS_SELECTOR, ".type__text"),         # From example
                        (By.CSS_SELECTOR, ".info__container span"),  # From example
                        (By.CSS_SELECTOR, ".point-episode"),     # Fallback
                        (By.CSS_SELECTOR, ".episode"),           # Fallback
                        (By.CSS_SELECTOR, "span[class*='type']"),  # Based on example
                        (By.CSS_SELECTOR, "span[class*='ep']"),  # Fallback
                        (By.CSS_SELECTOR, "div[class*='ep']"),   # Fallback
                        (By.CSS_SELECTOR, "span")                # Any span as last resort
                    ]

                    for selector in ep_selectors:
                        try:
                            ep_elems = point_elem.find_elements(*selector)
                            for ep_elem in ep_elems:
                                ep_text = ep_elem.text.strip()
                                if ep_text:
                                    # Check if this looks like an episode indicator
                                    if any(marker in ep_text.upper() for marker in ["EP", "OP", "ED", "OVA", "EPISODE", "SPECIAL", "SP"]):
                                        # Clean up the episode text
                                        ep = ep_text.replace("EP", "").replace("Episode", "").strip()
                                        if ep == "OP" or ep == "ED":  # Keep OP and ED as is
                                            pass
                                        elif ep.isdigit() or (ep and ep[0].isdigit()):  # If it's a number or starts with a number
                                            pass
                                        else:  # If it's not a recognized format, keep the original text
                                            ep = ep_text
                                        print(f"  Found episode: {ep} with selector {selector}")
                                        break

                            if ep:  # Break out of the selector loop if we found an episode
                                break
                        except Exception as e:
                            print(f"  Error with episode selector {selector}: {e}")
                            continue

                    # Extract coordinates from point element
                    lat, lng, map_url = self._extract_coordinates_from_point(point_elem)

                    # Extract point image based on the provided example
                    img_url = ""  # Default empty image URL
                    img_selectors = [
                        (By.CSS_SELECTOR, ".component__img img"),  # From example
                        (By.CSS_SELECTOR, ".img__inner img"),      # From example
                        (By.CSS_SELECTOR, ".point-image"),        # Fallback
                        (By.CSS_SELECTOR, ".location-image"),     # Fallback
                        (By.CSS_SELECTOR, "img[loading='lazy']"),  # From example
                        (By.CSS_SELECTOR, "img[decoding='async']"),  # From example
                        (By.CSS_SELECTOR, "img[data-nimg='1']"),  # From example
                        (By.CSS_SELECTOR, "img")                  # Any image in the point element
                    ]

                    for selector in img_selectors:
                        try:
                            img_elems = point_elem.find_elements(*selector)
                            for img_elem in img_elems:
                                # Try different attributes for the image source
                                for attr in ["src", "data-src", "srcset"]:
                                    src = img_elem.get_attribute(attr)
                                    if src:
                                        # For srcset, take the first URL
                                        if attr == "srcset" and " " in src:
                                            src = src.split(" ")[0]

                                        # Clean up the URL if needed
                                        if src.startswith("/_next/image?"):
                                            # Try to extract the original URL from the Next.js image URL
                                            url_param_match = re.search(r'url=([^&]+)', src)
                                            if url_param_match:
                                                encoded_url = url_param_match.group(1)
                                                try:
                                                    # URL decode the parameter
                                                    from urllib.parse import unquote
                                                    src = unquote(encoded_url)
                                                    print(f"  Extracted original image URL: {src}")
                                                except:
                                                    pass

                                        # Check if it's a valid image URL
                                        if any(ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]):
                                            img_path = f"{images_folder}/{local_folder_id}-{i}.jpg"
                                            if self.download_image(src, img_path):
                                                img_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/{local_folder_id}-{i}.jpg"
                                                print(f"  Downloaded point image: {src}")
                                                break

                                if img_url:  # Break out of the img_elem loop if we found an image
                                    break

                            if img_url:  # Break out of the selector loop if we found an image
                                break
                        except Exception as e:
                            print(f"  Error with image selector {selector}: {e}")
                            continue

                    # If still no image, try to extract from Firebase Storage URL in the HTML
                    if not img_url:
                        try:
                            point_html = point_elem.get_attribute('outerHTML')
                            firebase_match = re.search(r'firebasestorage\.googleapis\.com/v0/b/[^"&]+', point_html)
                            if firebase_match:
                                firebase_url = "https://" + firebase_match.group(0)
                                firebase_url = firebase_url.replace('%252F', '/')
                                print(f"  Found Firebase Storage URL: {firebase_url}")
                                img_path = images_folder / f"{local_folder_id}-{i}.jpg"
                                if self.download_image(firebase_url, img_path):
                                    img_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/{local_folder_id}-{i}.jpg"
                                    print(f"  Downloaded point image from Firebase: {firebase_url}")
                        except Exception as e:
                            print(f"  Error extracting Firebase image URL: {e}")

                    # Create point data
                    point_data = {
                        "id": f"{local_folder_id}-{i}",
                        "name": name,
                        "image": img_url,
                        "ep": ep,
                        "geo": [lat, lng]
                    }

                    # Ask user if they want to manually edit this point's data (only if manual_edit is True)
                    if manual_edit:
                        print(f"\n  Point {i} data:")
                        print(f"    Name: {name}")
                        print(f"    Episode: {ep}")
                        print(f"    Coordinates: {lat}, {lng}")
                        print(f"    Image: {'Yes' if img_url else 'No'}")

                        edit = input("  Edit this point? (y/n/s - yes/no/skip): ").strip().lower()

                        if edit == 's':
                            print("  Skipping this point")
                            continue  # Skip this point
                        elif edit == 'y':
                            # Allow user to edit point data
                            new_name = input(f"  Enter name (current: {name}): ").strip()
                            if new_name:
                                point_data['name'] = new_name

                            new_ep = input(f"  Enter episode (current: {ep}): ").strip()
                            if new_ep:
                                point_data['ep'] = new_ep

                            new_coords = input(f"  Enter coordinates as 'lat,lng' (current: {lat},{lng}): ").strip()
                            if new_coords and ',' in new_coords:
                                try:
                                    lat_str, lng_str = new_coords.split(',')
                                    point_data['geo'] = [float(lat_str.strip()), float(lng_str.strip())]
                                    print(f"  Updated coordinates to: {point_data['geo']}")
                                except Exception as e:
                                    print(f"  Error parsing coordinates: {e}. Using original coordinates.")

                    points.append(point_data)
                    print(f"  Added point {i}: {point_data['name']}")

                except Exception as e:
                    print(f"  Error extracting point {i}: {e}")

        except Exception as e:
            print(f"Error extracting pilgrimage points: {e}")

        # Get Chinese name from Bangumi API
        chinese_name = self.get_chinese_name_from_bangumi(anime_title)

        # Create anime data
        anime_data = {
            "name": anime_title,
            "name_cn": chinese_name,  # Using Chinese name from Bangumi API
            "cover": cover_image_url,
            "theme_color": theme_color,
            "points": points
        }

        # Save points.json
        points_path = folder_path / "points.json"
        with open(points_path, 'w', encoding='utf-8') as f:
            json.dump({"points": points}, f, ensure_ascii=False, indent=2)

        # Create info.json
        self.create_info_json(anime_data, local_folder_id)

        return {
            "local_id": local_folder_id,
            "anime_data": anime_data
        }

    def is_anime_already_in_database(self, anime_title):
        """Check if an anime is already in the database by checking index.json

        Returns:
            tuple: (exists, local_id) where exists is a boolean indicating if the anime exists,
                  and local_id is the local ID of the anime if it exists, otherwise None
        """
        # First check the index.json in the data directory
        index_path = self.base_dir / 'index.json'
        result = self._check_anime_in_index(index_path, anime_title)
        if result[0]:
            return result

        # Then check the index.json in the root directory
        root_index_path = Path('index.json')
        if root_index_path.exists():
            result = self._check_anime_in_index(root_index_path, anime_title)
            if result[0]:
                return result

        return (False, None)

    def _check_anime_in_index(self, index_path, anime_title):
        """Helper method to check if an anime exists in a specific index.json file

        Returns:
            tuple: (exists, local_id) where exists is a boolean indicating if the anime exists,
                  and local_id is the local ID of the anime if it exists, otherwise None
        """
        if not index_path.exists():
            self.logger.info(f"Index file {index_path} does not exist")
            return (False, None)

        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                index_data = json.load(f)

            self.logger.info(f"Checking if anime '{anime_title}' exists in {index_path} with {len(index_data)} entries")

            # 标准化函数：移除标点符号、空格，并转为小写
            def normalize_name(name):
                if not name:
                    return ""
                # 移除标点符号和空格，转为小写
                import re
                return re.sub(r'[^\w\s]', '', name).lower().replace(' ', '')

            # 标准化搜索的番剧名称
            normalized_anime_title = normalize_name(anime_title)
            self.logger.info(f"Normalized search title: '{normalized_anime_title}'")

            # 收集所有可能的匹配项，稍后选择最佳匹配
            exact_matches = []  # 精确匹配
            normalized_matches = []  # 标准化后的精确匹配
            substring_matches = []  # 子字符串匹配

            # 检查番剧是否存在于索引中
            for local_id, anime_data in index_data.items():
                # 获取日语和中文名称进行比较
                jp_name = anime_data.get('name', '')
                cn_name = anime_data.get('name_cn', '')

                # 标准化数据库中的名称
                normalized_jp_name = normalize_name(jp_name)
                normalized_cn_name = normalize_name(cn_name)

                # 首先检查精确匹配（原始名称）
                if jp_name == anime_title or cn_name == anime_title:
                    self.logger.info(f"✓ Anime '{anime_title}' exactly matches existing anime in {index_path} with ID {local_id}")
                    self.logger.info(f"  Existing entry: JP='{jp_name}', CN='{cn_name}'")
                    exact_matches.append((local_id, jp_name, cn_name, 100))  # 100表示最高匹配度

                # 然后检查标准化后的精确匹配
                elif (normalized_jp_name and normalized_jp_name == normalized_anime_title) or \
                     (normalized_cn_name and normalized_cn_name == normalized_anime_title):
                    self.logger.info(f"✓ Anime '{anime_title}' matches existing anime after normalization in {index_path} with ID {local_id}")
                    self.logger.info(f"  Existing entry: JP='{jp_name}', CN='{cn_name}'")
                    self.logger.info(f"  Normalized: JP='{normalized_jp_name}', CN='{normalized_cn_name}'")
                    normalized_matches.append((local_id, jp_name, cn_name, 90))  # 90表示次高匹配度

                # 检查包含关系（如果标准化后的名称是另一个的子字符串）
                # 只有当名称长度超过一定值时才考虑包含关系，以避免误匹配
                else:
                    min_length = 5  # 最小长度阈值

                    if normalized_anime_title and len(normalized_anime_title) >= min_length:
                        # 计算匹配分数 - 基于匹配的字符串长度和相似度
                        match_score = 0
                        match_type = ""

                        # 特殊情况：检查搜索名称是否是数据库名称的前缀（如"Test Anime 2"是"Test Anime 2: The Sequel"的前缀）
                        # 这种情况应该优先匹配
                        if normalized_jp_name and normalized_jp_name.startswith(normalized_anime_title) and len(normalized_anime_title) >= min_length:
                            # 给予更高的分数，确保这种匹配优先级最高
                            score = 95 - (len(normalized_jp_name) - len(normalized_anime_title)) * 0.1  # 减去一点差异长度的惩罚
                            if score > match_score:
                                match_score = score
                                match_type = "JP starts with search (prefix match)"

                        if normalized_cn_name and normalized_cn_name.startswith(normalized_anime_title) and len(normalized_anime_title) >= min_length:
                            score = 95 - (len(normalized_cn_name) - len(normalized_anime_title)) * 0.1
                            if score > match_score:
                                match_score = score
                                match_type = "CN starts with search (prefix match)"

                        # 检查数据库名称是否包含搜索名称（但不是前缀）
                        if normalized_jp_name and normalized_anime_title in normalized_jp_name and not normalized_jp_name.startswith(normalized_anime_title) and len(normalized_jp_name) >= min_length:
                            # 计算匹配分数：搜索名称长度占数据库名称长度的百分比
                            score = (len(normalized_anime_title) / len(normalized_jp_name)) * 80
                            if score > match_score:
                                match_score = score
                                match_type = "JP contains search"

                        if normalized_cn_name and normalized_anime_title in normalized_cn_name and not normalized_cn_name.startswith(normalized_anime_title) and len(normalized_cn_name) >= min_length:
                            score = (len(normalized_anime_title) / len(normalized_cn_name)) * 80
                            if score > match_score:
                                match_score = score
                                match_type = "CN contains search"

                        # 检查搜索名称是否包含数据库名称
                        if normalized_jp_name and normalized_jp_name in normalized_anime_title and len(normalized_jp_name) >= min_length:
                            # 计算匹配分数：数据库名称长度占搜索名称长度的百分比
                            score = (len(normalized_jp_name) / len(normalized_anime_title)) * 70
                            if score > match_score:
                                match_score = score
                                match_type = "Search contains JP"

                        if normalized_cn_name and normalized_cn_name in normalized_anime_title and len(normalized_cn_name) >= min_length:
                            score = (len(normalized_cn_name) / len(normalized_anime_title)) * 70
                            if score > match_score:
                                match_score = score
                                match_type = "Search contains CN"

                        # 如果有匹配，添加到子字符串匹配列表
                        if match_score > 0:
                            self.logger.info(f"✓ Substring match ({match_type}) for '{anime_title}' with ID {local_id}, score: {match_score:.1f}")
                            self.logger.info(f"  Existing entry: JP='{jp_name}', CN='{cn_name}'")
                            substring_matches.append((local_id, jp_name, cn_name, match_score))

                # 记录接近匹配以便调试
                if (jp_name and anime_title in jp_name) or (cn_name and anime_title in cn_name):
                    self.logger.info(f"  Near match found but not exact: ID={local_id}, JP='{jp_name}', CN='{cn_name}'")
                if (jp_name and jp_name in anime_title) or (cn_name and cn_name in anime_title):
                    self.logger.info(f"  Reverse near match found but not exact: ID={local_id}, JP='{jp_name}', CN='{cn_name}'")

            # 选择最佳匹配
            if exact_matches:
                # 如果有精确匹配，返回第一个
                best_match = exact_matches[0]
                return (True, best_match[0])
            elif normalized_matches:
                # 如果有标准化匹配，返回第一个
                best_match = normalized_matches[0]
                return (True, best_match[0])
            elif substring_matches:
                # 如果有子字符串匹配，返回分数最高的
                best_match = max(substring_matches, key=lambda x: x[3])
                self.logger.info(f"Best substring match for '{anime_title}': ID={best_match[0]}, score: {best_match[3]:.1f}")
                return (True, best_match[0])

            self.logger.info(f"✗ Anime '{anime_title}' not found in {index_path}")
            return (False, None)
        except Exception as e:
            self.logger.error(f"Error checking {index_path}: {e}")
            return (False, None)

    def update_existing_anime(self, anime_info, local_id):
        """Update an existing anime with new pilgrimage points

        Args:
            anime_info: Dictionary containing anime information (title, link)
            local_id: The local ID of the existing anime

        Returns:
            dict: Updated anime data if successful, None otherwise
        """
        self.logger.info(f"Updating existing anime: {anime_info['title']} with ID {local_id}")

        # Load existing anime data
        folder_path = self.base_dir / str(local_id)
        points_path = folder_path / "points.json"
        info_path = folder_path / "info.json"
        images_folder = folder_path / "images"

        # Ensure the folder exists
        if not folder_path.exists():
            self.logger.error(f"Folder for anime ID {local_id} does not exist at {folder_path}")
            return None

        # Load existing points data
        existing_points = []
        try:
            if points_path.exists():
                with open(points_path, 'r', encoding='utf-8') as f:
                    points_data = json.load(f)
                    if isinstance(points_data, list):
                        existing_points = points_data
                    else:
                        existing_points = points_data.get("points", [])
                    self.logger.info(f"Loaded {len(existing_points)} existing points from {points_path}")

                    # Log the first few existing points for debugging
                    for i, point in enumerate(existing_points[:3]):
                        if "geo" in point and len(point["geo"]) == 2:
                            self.logger.info(f"  Existing point {i+1}: name='{point.get('name', 'Unknown')}', geo={point['geo']}")
        except Exception as e:
            self.logger.error(f"Error loading existing points data: {e}")
            return None

        # Load existing info data
        existing_info = {}
        try:
            if info_path.exists():
                with open(info_path, 'r', encoding='utf-8') as f:
                    existing_info = json.load(f)
                    self.logger.info(f"Loaded existing info from {info_path}")
        except Exception as e:
            self.logger.error(f"Error loading existing info data: {e}")
            return None

        # Scrape new points from the anime page
        self.logger.info(f"Scraping new points for anime: {anime_info['title']}")

        # Visit the anime page
        self.driver.get(anime_info['link'])

        # Wait for the page to load (similar to scrape_anime method)
        try:
            selectors_to_try = [
                (By.CSS_SELECTOR, ".anime-detail"),
                (By.CSS_SELECTOR, ".anime-header"),
                (By.CSS_SELECTOR, "h1"),
                (By.TAG_NAME, "img")
            ]

            for selector in selectors_to_try:
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located(selector)
                    )
                    self.logger.info(f"Page loaded, found element with selector: {selector}")
                    break
                except TimeoutException:
                    continue
            else:
                self.logger.error(f"Timeout waiting for anime page to load: {anime_info['link']}")
                return None
        except Exception as e:
            self.logger.error(f"Error waiting for page to load: {e}")
            return None

        # Extract anime title (use existing title if available)
        anime_title = anime_info['title']

        # Extract cover image (use existing cover if available)
        cover_image_url = existing_info.get("cover", "")
        theme_color = existing_info.get("theme_color", "#7f6a95")

        # Extract pilgrimage points (similar to scrape_anime method)
        # Scroll to load all pilgrimage points
        self.logger.info("Scrolling to load all pilgrimage points...")
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        scroll_attempts = 0
        max_scroll_attempts = 20  # Increased from 15 to ensure more content is loaded

        while scroll_attempts < max_scroll_attempts:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)  # Wait longer for content to load
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                # Try one more scroll to be sure
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)
                new_height = self.driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
            last_height = new_height
            scroll_attempts += 1
            self.logger.info(f"Scroll attempt {scroll_attempts}/{max_scroll_attempts}")

        # Extract pilgrimage points
        new_points = []
        try:
            # Try different selectors for pilgrimage points
            point_selectors = [
                (By.CSS_SELECTOR, ".map-side__component"),
                (By.CSS_SELECTOR, ".component__inner"),
                (By.CSS_SELECTOR, ".pilgrimage-point"),
                (By.CSS_SELECTOR, ".location-item"),
                (By.CSS_SELECTOR, ".map-marker"),
                (By.CSS_SELECTOR, "div[id^='3']"),
                (By.CSS_SELECTOR, "div[class*='map']"),
                (By.CSS_SELECTOR, "div[class*='point']"),
                (By.CSS_SELECTOR, "div[class*='location']")
            ]

            point_elements = []
            for selector in point_selectors:
                try:
                    elements = self.driver.find_elements(*selector)
                    if elements:
                        point_elements = elements
                        self.logger.info(f"Found {len(elements)} pilgrimage points with selector: {selector}")
                        break
                except:
                    continue

            # If we still don't have points, try to find any elements with Google Maps links
            if not point_elements:
                try:
                    map_link_patterns = [
                        "a[href*='google.com/maps']",
                        "a[href*='maps.google']",
                        "a[href*='goo.gl/maps']"
                    ]

                    for pattern in map_link_patterns:
                        map_links = self.driver.find_elements(By.CSS_SELECTOR, pattern)
                        if map_links:
                            self.logger.info(f"Found {len(map_links)} Google Maps links with pattern: {pattern}")
                            for link in map_links:
                                try:
                                    parent = link.find_element(By.XPATH, "./..")
                                    point_elements.append(parent)
                                except:
                                    try:
                                        parent = link.find_element(By.XPATH, "./../..")
                                        point_elements.append(parent)
                                    except:
                                        point_elements.append(link)

                            if point_elements:
                                break
                except Exception as e:
                    self.logger.error(f"Error finding Google Maps links: {e}")

            self.logger.info(f"Processing {len(point_elements)} pilgrimage points")

            # Create a set of existing point coordinates to check for duplicates
            existing_coords = set()
            for point in existing_points:
                if "geo" in point and len(point["geo"]) == 2:
                    # Round coordinates to 5 decimal places for comparison (about 1.1 meters precision)
                    # This helps avoid missing points due to tiny coordinate differences
                    lat = round(point["geo"][0], 5)
                    lng = round(point["geo"][1], 5)
                    existing_coords.add((lat, lng))

            self.logger.info(f"Found {len(existing_coords)} existing point coordinates")

            # Process each point element
            for i, point_elem in enumerate(point_elements, 1):
                try:
                    # Extract point name (similar to scrape_anime method)
                    name = "Unknown Location"
                    name_selectors = [
                        (By.CSS_SELECTOR, ".title__text"),
                        (By.CSS_SELECTOR, "h2.title__text"),
                        (By.CSS_SELECTOR, "h2"),
                        (By.CSS_SELECTOR, ".inner__title h2"),
                        (By.CSS_SELECTOR, ".point-name"),
                        (By.CSS_SELECTOR, ".location-name"),
                        (By.CSS_SELECTOR, "h3"),
                        (By.CSS_SELECTOR, "h4"),
                        (By.CSS_SELECTOR, "strong"),
                        (By.CSS_SELECTOR, ".title"),
                        (By.CSS_SELECTOR, "[class*='title']"),
                        (By.CSS_SELECTOR, "[class*='name']"),
                        (By.CSS_SELECTOR, "p"),
                        (By.CSS_SELECTOR, "span"),
                        (By.CSS_SELECTOR, "img[title]")
                    ]

                    for selector in name_selectors:
                        try:
                            name_elems = point_elem.find_elements(*selector)
                            for name_elem in name_elems:
                                text = name_elem.text.strip()
                                if text and len(text) > 1:
                                    name = text
                                    self.logger.info(f"  Found name '{name}' with selector {selector}")
                                    break
                            if name != "Unknown Location":
                                break
                        except Exception as e:
                            continue

                    # If still no name, try to get any text content
                    if name == "Unknown Location":
                        try:
                            all_text = point_elem.text.strip()
                            if all_text:
                                lines = [line.strip() for line in all_text.split('\n') if line.strip()]
                                if lines:
                                    for line in lines:
                                        if 1 < len(line) < 50:
                                            name = line
                                            self.logger.info(f"  Using first text line as name: '{name}'")
                                            break
                        except Exception as e:
                            self.logger.error(f"  Error getting text content: {e}")

                    # Extract episode number
                    ep = ""
                    ep_selectors = [
                        (By.CSS_SELECTOR, ".type__text"),
                        (By.CSS_SELECTOR, ".info__container span"),
                        (By.CSS_SELECTOR, ".point-episode"),
                        (By.CSS_SELECTOR, ".episode"),
                        (By.CSS_SELECTOR, "span[class*='type']"),
                        (By.CSS_SELECTOR, "span[class*='ep']"),
                        (By.CSS_SELECTOR, "div[class*='ep']"),
                        (By.CSS_SELECTOR, "span")
                    ]

                    for selector in ep_selectors:
                        try:
                            ep_elems = point_elem.find_elements(*selector)
                            for ep_elem in ep_elems:
                                ep_text = ep_elem.text.strip()
                                if ep_text:
                                    if any(marker in ep_text.upper() for marker in ["EP", "OP", "ED", "OVA", "EPISODE", "SPECIAL", "SP"]):
                                        ep = ep_text.replace("EP", "").replace("Episode", "").strip()
                                        if ep == "OP" or ep == "ED":
                                            pass
                                        elif ep.isdigit() or (ep and ep[0].isdigit()):
                                            pass
                                        else:
                                            ep = ep_text
                                        self.logger.info(f"  Found episode: {ep} with selector {selector}")
                                        break
                            if ep:
                                break
                        except Exception as e:
                            continue

                    # Extract coordinates from point element
                    lat, lng, map_url = self._extract_coordinates_from_point(point_elem)

                    # If we have valid coordinates, check if this point already exists
                    if lat != 0 and lng != 0:
                        # Round coordinates to 5 decimal places for comparison (about 1.1 meters precision)
                        lat_rounded = round(lat, 5)
                        lng_rounded = round(lng, 5)

                        # Check if this point is too close to any existing point
                        # Define a small threshold for considering points as duplicates (0.0001 is about 11 meters)
                        threshold = 0.0001
                        is_duplicate = False

                        # First check exact match
                        if (lat_rounded, lng_rounded) in existing_coords:
                            self.logger.info(f"  Skipping point with coordinates {lat}, {lng} as it exactly matches an existing point")
                            is_duplicate = True
                        else:
                            # Then check for nearby points within threshold
                            for existing_lat, existing_lng in existing_coords:
                                if (abs(lat_rounded - existing_lat) < threshold and
                                    abs(lng_rounded - existing_lng) < threshold):
                                    self.logger.info(f"  Skipping point with coordinates {lat}, {lng} as it's very close to existing point at {existing_lat}, {existing_lng}")
                                    is_duplicate = True
                                    break

                        if is_duplicate:
                            continue

                        # Add to existing coordinates set to avoid duplicates in this run
                        existing_coords.add((lat_rounded, lng_rounded))

                        # Log the map URL for debugging
                        self.logger.info(f"  Found map URL: https://www.google.com/maps/dir/?api=1&destination={lat},{lng}")

                        # Download image for this point
                        img_url = ""
                        try:
                            # Try to find an image in the point element
                            images = point_elem.find_elements(By.TAG_NAME, "img")
                            for img in images:
                                src = img.get_attribute("src")
                                if src and (src.endswith(".jpg") or src.endswith(".png") or src.endswith(".jpeg") or "image" in src):
                                    # Skip small icons
                                    width = int(img.get_attribute("width") or 0)
                                    height = int(img.get_attribute("height") or 0)
                                    if width < 50 or height < 50:
                                        continue

                                    # Download the image
                                    img_filename = f"{local_id}-{len(existing_points) + len(new_points) + 1}.jpg"
                                    img_path = images_folder / img_filename
                                    if self.download_image(src, img_path):
                                        img_url = f"https://image.xinu.ink/pic/data/{local_id}/images/{img_filename}"
                                        self.logger.info(f"  Downloaded point image: {src}")
                                        break
                        except Exception as e:
                            self.logger.error(f"  Error downloading point image: {e}")

                        # Create point data
                        point_data = {
                            "id": f"{local_id}-{len(existing_points) + len(new_points) + 1}",
                            "name": name,
                            "image": img_url,
                            "ep": ep,
                            "geo": [lat, lng]
                        }

                        new_points.append(point_data)
                        self.logger.info(f"  Added new point: {point_data['name']} at {lat}, {lng}")
                except Exception as e:
                    self.logger.error(f"  Error extracting point {i}: {e}")

            self.logger.info(f"Found {len(new_points)} new points")

            # If no new points were found, return None
            if not new_points:
                self.logger.info("No new points found for this anime")
                return None

            # Combine existing and new points
            combined_points = existing_points + new_points
            self.logger.info(f"Combined {len(existing_points)} existing points with {len(new_points)} new points")

            # Update points.json
            with open(points_path, 'w', encoding='utf-8') as f:
                json.dump({"points": combined_points}, f, ensure_ascii=False, indent=2)

            # Update info.json
            updated_info = existing_info.copy()
            updated_info["pointsLength"] = len(combined_points)

            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(updated_info, f, ensure_ascii=False, indent=2)

            # Get Chinese name from Bangumi API
            chinese_name = self.get_chinese_name_from_bangumi(anime_title)

            # Create anime data for index.json update
            anime_data = {
                "name": anime_title,
                "name_cn": chinese_name,  # Using Chinese name from Bangumi API
                "cover": cover_image_url,
                "theme_color": theme_color,
                "points": combined_points
            }

            # Update the Chinese name in info.json as well
            updated_info["cn"] = chinese_name

            # Write the updated info.json again with the new Chinese name
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(updated_info, f, ensure_ascii=False, indent=2)

            self.logger.info(f"Updated info.json with Chinese name: {chinese_name}")

            return {
                "local_id": local_id,
                "anime_data": anime_data,
                "new_points_count": len(new_points)
            }

        except Exception as e:
            self.logger.error(f"Error extracting pilgrimage points: {e}")
            return None

    def update_index_json(self, anime_data_list, update_mode=False):
        """Update the index.json file with new anime data

        Args:
            anime_data_list: List of anime data to add or update
            update_mode: If True, update existing entries instead of replacing them
        """
        index_path = self.base_dir / "index.json"

        # Load existing index.json if it exists
        if os.path.exists(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
        else:
            index_data = {}

        # Track the number of new and updated entries
        new_entries = 0
        updated_entries = 0

        # Add or update anime data
        for anime_data in anime_data_list:
            local_id = str(anime_data["local_id"])
            is_update = local_id in index_data and update_mode

            # Format the points data according to the existing format
            formatted_points = []
            for point in anime_data["anime_data"]["points"]:
                # Generate a unique ID for each point if not already present
                if "id" not in point or point["id"].startswith(local_id):
                    # Create a more random ID format similar to existing ones
                    timestamp = int(time.time() * 1000)
                    random_chars = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=8))
                    point_id = f"{random_chars}"
                else:
                    point_id = point["id"]

                formatted_point = {
                    "id": point_id,
                    "name": point["name"],
                    "image": point["image"],
                    "ep": point["ep"],
                    "geo": point["geo"]
                }

                # Add optional fields if they exist
                if "cn" in point:
                    formatted_point["cn"] = point["cn"]
                if "s" in point:
                    formatted_point["s"] = point["s"]

                formatted_points.append(formatted_point)

            # Create or update the entry for index.json
            if is_update:
                # Update existing entry
                self.logger.info(f"Updating existing entry for anime ID {local_id} in index.json")
                index_data[local_id]["points"] = formatted_points
                # Update other fields if needed
                index_data[local_id]["name"] = anime_data["anime_data"]["name"]
                index_data[local_id]["name_cn"] = anime_data["anime_data"]["name_cn"]
                # Only update cover and theme_color if they are provided and not empty
                if anime_data["anime_data"]["cover"]:
                    index_data[local_id]["cover"] = anime_data["anime_data"]["cover"]
                if anime_data["anime_data"].get("theme_color"):
                    index_data[local_id]["theme_color"] = anime_data["anime_data"]["theme_color"]
                updated_entries += 1
            else:
                # Create new entry
                index_data[local_id] = {
                    "name": anime_data["anime_data"]["name"],
                    "name_cn": anime_data["anime_data"]["name_cn"],
                    "cover": anime_data["anime_data"]["cover"],
                    "theme_color": anime_data["anime_data"].get("theme_color", "#7f6a95"),
                    "points": formatted_points,
                    "inform": f"https://image.xinu.ink/pic/data/{local_id}/points.json"
                }
                new_entries += 1

        # Save updated index.json in data directory
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        # Also save a copy to the root directory
        root_index_path = Path("index.json")
        with open(root_index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        if update_mode:
            self.logger.info(f"Updated index.json files with {new_entries} new and {updated_entries} updated anime entries")
        else:
            self.logger.info(f"Updated index.json files with {len(anime_data_list)} new anime entries")

    def get_anime_list_with_manual_control(self):
        """Get the list of anime with manual control over scrolling"""
        print("Fetching anime list from recently updated page...")
        self.driver.get(self.recently_updated_url)

        # Wait for the page to load (using the same logic as get_anime_list)
        try:
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".anime-list .anime-item"))
            )
        except TimeoutException:
            try:
                WebDriverWait(self.driver, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/Map?data=']"))
                )
            except TimeoutException:
                print("Could not find anime list elements. The website structure might have changed.")
                with open("page_source.html", "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                print("Saved page source to page_source.html for debugging.")
                return []

        # Manual scrolling control
        print("\nManual scrolling mode activated.")
        print("Instructions:")
        print("1. Type 'scroll' to scroll down and load more content")
        print("2. Type 'done' when you've loaded all anime")
        print("3. Type 'extract' to extract the current anime list without further scrolling")

        while True:
            command = input("\nEnter command (scroll/done/extract): ").strip().lower()

            if command == "scroll":
                # Scroll down in smaller increments
                current_height = self.driver.execute_script("return document.body.scrollHeight")
                for i in range(4):  # Scroll in 4 steps
                    scroll_position = current_height // 4 * (i + 1)
                    self.driver.execute_script(f"window.scrollTo(0, {scroll_position});")
                    time.sleep(1)

                # Final scroll to bottom
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)  # Wait for content to load

                new_height = self.driver.execute_script("return document.body.scrollHeight")
                print(f"Scrolled to {new_height}px")

                # Count visible anime items
                anime_items = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/Map?data=']")
                if not anime_items:
                    anime_items = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/ja/Map?data=']")
                print(f"Currently visible anime items: {len(anime_items)}")

            elif command == "done" or command == "extract":
                break
            else:
                print("Invalid command. Please try again.")

        # Extract anime items (same logic as in get_anime_list)
        anime_list = []
        anime_items = self.driver.find_elements(By.CSS_SELECTOR, ".anime-list .anime-item")
        if not anime_items:
            anime_items = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/Map?data=']")
        if not anime_items:
            anime_items = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/ja/Map?data=']")

        print(f"Found {len(anime_items)} anime items")

        for i, item in enumerate(anime_items, 1):
            try:
                # Try different ways to extract title (same as in get_anime_list)
                try:
                    title = item.find_element(By.CSS_SELECTOR, ".anime-title").text
                except:
                    try:
                        title = item.find_element(By.CSS_SELECTOR, "h3").text
                    except:
                        try:
                            title = item.get_attribute("title")
                        except:
                            href = item.get_attribute("href")
                            data_part = href.split("data=")[-1]
                            title = data_part.replace("-", " ").title()

                link = item.get_attribute("href")
                if not link.startswith("http"):
                    link = f"https://www.animepilgrimage.com{link}"

                anime_list.append({
                    "id": i,
                    "title": title,
                    "link": link
                })
                print(f"{i}. {title} - {link}")
            except Exception as e:
                print(f"Error extracting anime item {i}: {e}")

        return anime_list

    def _fix_anime_coordinates(self, local_id, api_id, anime_name):
        """Re-scrape a single anime page to fix zero coordinates in existing points

        Reads the existing points.json, visits the anime page, re-extracts coordinates
        for any point with [0, 0], then updates points.json and index.json.

        Args:
            local_id: Local folder ID (integer)
            api_id: API ID on animepilgrimage.com
            anime_name: Name of the anime (for logging)

        Returns:
            int: Number of points fixed, or 0 if none needed / error
        """
        folder_path = self.base_dir / str(local_id)
        points_path = folder_path / "points.json"
        images_folder = folder_path / "images"

        if not folder_path.exists():
            self.logger.warning(f"Folder for anime ID {local_id} does not exist")
            return 0

        # Load existing points
        try:
            with open(points_path, 'r', encoding='utf-8') as f:
                points_data = json.load(f)
            if isinstance(points_data, list):
                all_points = points_data
            else:
                all_points = points_data.get("points", [])
        except Exception as e:
            self.logger.error(f"Error loading points.json for {local_id}: {e}")
            return 0

        if not all_points:
            self.logger.info(f"No points to fix for {anime_name} (ID {local_id})")
            return 0

        # Identify zero-coordinate points
        zero_coord_indices = []
        for idx, pt in enumerate(all_points):
            geo = pt.get("geo", [])
            if len(geo) == 2 and geo[0] == 0 and geo[1] == 0:
                zero_coord_indices.append(idx)

        if not zero_coord_indices:
            self.logger.info(f"No zero-coordinate points found for {anime_name} (ID {local_id})")
            return 0

        self.logger.info(f"Found {len(zero_coord_indices)} zero-coordinate points in {anime_name} (ID {local_id})")

        # Construct the anime page URL
        link = f"{self.base_url}/Map?data={api_id}"
        self.logger.info(f"Visiting {link} to fix coordinates...")

        # Visit the page
        self.driver.get(link)

        try:
            selectors_to_try = [
                (By.CSS_SELECTOR, ".map-side__component"),
                (By.CSS_SELECTOR, ".component__inner"),
                (By.CSS_SELECTOR, "h1"),
                (By.TAG_NAME, "img"),
            ]
            for selector in selectors_to_try:
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located(selector)
                    )
                    break
                except TimeoutException:
                    continue
            else:
                self.logger.error(f"Timeout loading page for {anime_name}")
                return 0
        except Exception as e:
            self.logger.error(f"Error waiting for page to load: {e}")
            return 0

        # Scroll to load all points
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        for _ in range(20):
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                time.sleep(2)
                new_height = self.driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
            last_height = new_height

        # Find point elements on the page
        point_elements = []
        point_selectors = [
            (By.CSS_SELECTOR, ".map-side__component"),
            (By.CSS_SELECTOR, ".component__inner"),
            (By.CSS_SELECTOR, "div[class*='map']"),
            (By.CSS_SELECTOR, "div[class*='point']"),
        ]
        for selector in point_selectors:
            try:
                elements = self.driver.find_elements(*selector)
                if elements:
                    point_elements = elements
                    self.logger.info(f"Found {len(elements)} point elements on page")
                    break
            except:
                continue

        if not point_elements:
            # Fallback: try to find map links
            try:
                map_links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='google.com/maps'], a[href*='maps.google'], a[href*='goo.gl/maps']")
                if map_links:
                    for link_elem in map_links:
                        try:
                            parent = link_elem.find_element(By.XPATH, "./..")
                            point_elements.append(parent)
                        except:
                            point_elements.append(link_elem)
                    self.logger.info(f"Found {len(point_elements)} point elements via map links")
            except:
                pass

        if not point_elements:
            self.logger.warning(f"No point elements found on page for {anime_name}")
            return 0

        self.logger.info(f"Processing {len(point_elements)} page elements to fix {len(zero_coord_indices)} broken points")

        # For each zero-coordinate point, try to find its corrected version
        fixed_count = 0
        for idx in zero_coord_indices:
            broken_point = all_points[idx]
            broken_name = broken_point.get("name", "")
            self.logger.info(f"  Trying to fix point: '{broken_name}'")

            corrected = False

            for page_elem in point_elements:
                lat, lng, _ = self._extract_coordinates_from_point(page_elem)

                if lat != 0.0 and lng != 0.0:
                    # Try to match by name
                    try:
                        page_name = "Unknown"
                        for sel in [
                            (By.CSS_SELECTOR, ".title__text"),
                            (By.CSS_SELECTOR, "h2"),
                            (By.CSS_SELECTOR, "[class*='title']"),
                        ]:
                            try:
                                elems = page_elem.find_elements(*sel)
                                for e in elems:
                                    t = e.text.strip()
                                    if t and len(t) > 1:
                                        page_name = t
                                        break
                                if page_name != "Unknown":
                                    break
                            except:
                                continue

                        if page_name == "Unknown":
                            page_name = page_elem.text.strip()[:50]
                    except:
                        page_name = ""

                    # Check if name matches
                    if broken_name and page_name and (
                        broken_name == page_name
                        or broken_name in page_name
                        or page_name in broken_name
                    ):
                        all_points[idx]["geo"] = [lat, lng]
                        self.logger.info(f"    Fixed '{broken_name}' -> [{lat}, {lng}] (matched by name)")
                        corrected = True
                        fixed_count += 1
                        break

            if not corrected:
                # Second pass: if we couldn't match by name but have remaining
                # unmatched page elements, try positional assignment
                self.logger.info(f"    Could not match '{broken_name}' by name on page, will attempt positional matching")

        # If we couldn't match by name, try positional assignment.
        # Collect all non-zero coords from the page
        page_coords = []
        for page_elem in point_elements:
            lat, lng, _ = self._extract_coordinates_from_point(page_elem)
            if lat != 0.0 and lng != 0.0:
                page_coords.append((lat, lng))

        # For remaining zero-coord points, assign coords positionally
        remaining = [idx for idx in zero_coord_indices
                     if all_points[idx].get("geo", [0, 0]) == [0, 0]]
        if remaining and page_coords:
            # Try to match remaining broken points to unused coords by position
            for i, idx in enumerate(remaining):
                if i < len(page_coords):
                    lat, lng = page_coords[i]
                    # Only assign if this coordinate isn't already used by another point
                    already_used = any(
                        abs(pt.get("geo", [0, 0])[0] - lat) < 0.0001 and
                        abs(pt.get("geo", [0, 0])[1] - lng) < 0.0001
                        for pt in all_points
                    )
                    if not already_used:
                        all_points[idx]["geo"] = [lat, lng]
                        self.logger.info(f"    Fixed '{all_points[idx].get('name', '?')}' -> [{lat}, {lng}] (positional)")
                        fixed_count += 1

        if fixed_count == 0:
            self.logger.warning(f"  Could not fix any coordinates for {anime_name}")
            return 0

        # Save updated points.json
        with open(points_path, 'w', encoding='utf-8') as f:
            json.dump({"points": all_points}, f, ensure_ascii=False, indent=2)
        self.logger.info(f"  Saved updated points.json for ID {local_id}")

        # Update info.json
        info_path = folder_path / "info.json"
        try:
            with open(info_path, 'r', encoding='utf-8') as f:
                info_data = json.load(f)
            info_data["pointsLength"] = len(all_points)
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(info_data, f, ensure_ascii=False, indent=2)
            self.logger.info(f"  Updated info.json for ID {local_id}")
        except:
            pass

        # Update index.json
        try:
            index_path = self.base_dir / "index.json"
            if index_path.exists():
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
                str_id = str(local_id)
                if str_id in index_data:
                    # Update points in index.json
                    index_data[str_id]["points"] = all_points
                    with open(index_path, 'w', encoding='utf-8') as f:
                        json.dump(index_data, f, ensure_ascii=False, indent=2)
                    self.logger.info(f"  Updated index.json for ID {local_id}")

                    # Also update root index.json
                    root_index = Path("index.json")
                    if root_index.exists():
                        with open(root_index, 'r', encoding='utf-8') as f:
                            root_data = json.load(f)
                        if str_id in root_data:
                            root_data[str_id]["points"] = all_points
                            with open(root_index, 'w', encoding='utf-8') as f:
                                json.dump(root_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Error updating index.json for {local_id}: {e}")

        self.logger.info(f"Fixed {fixed_count} coordinates for {anime_name} (ID {local_id})")
        return fixed_count

    def fix_zero_coordinate_points(self, apiid_path="apiid.json", max_anime=0):
        """Scan the entire database and fix points with [0, 0] coordinates

        Iterates through all folders in the base directory, finds points
        with zero coordinates, and re-scrapes them from the website.

        Args:
            apiid_path: Path to apiid.json mapping local_id -> api_id
            max_anime: Maximum number of anime to fix (0 = unlimited)

        Returns:
            int: Total number of points fixed
        """
        self.logger.info("Starting zero-coordinate point fix scan...")

        # Load apiid.json to get API IDs
        apiid_map = {}
        if os.path.exists(apiid_path):
            try:
                with open(apiid_path, 'r', encoding='utf-8') as f:
                    apiid_map = json.load(f)
                self.logger.info(f"Loaded {len(apiid_map)} API ID mappings from {apiid_path}")
            except Exception as e:
                self.logger.error(f"Error loading {apiid_path}: {e}")

        # Find all anime folders with broken points
        broken_anime = []
        for folder in sorted(self.base_dir.glob('*'), key=lambda p: int(p.name) if p.name.isdigit() else 0):
            if not folder.is_dir() or not folder.name.isdigit():
                continue
            local_id = int(folder.name)
            points_path = folder / "points.json"
            if not points_path.exists():
                continue

            try:
                with open(points_path, 'r', encoding='utf-8') as f:
                    points_data = json.load(f)
                if isinstance(points_data, list):
                    all_points = points_data
                else:
                    all_points = points_data.get("points", [])
                has_zero = any(
                    len(pt.get("geo", [])) == 2 and pt["geo"][0] == 0 and pt["geo"][1] == 0
                    for pt in all_points
                )
                if has_zero:
                    broken_anime.append(local_id)
                    self.logger.info(f"  Found broken points in folder {local_id}")
            except Exception as e:
                self.logger.error(f"Error reading {points_path}: {e}")

        if not broken_anime:
            self.logger.info("No zero-coordinate points found in the database!")
            return 0

        # Limit the number of anime to fix
        if max_anime > 0:
            broken_anime = broken_anime[:max_anime]

        self.logger.info(f"Found {len(broken_anime)} anime with zero-coordinate points")

        # Load index.json for anime name lookup
        index_data = {}
        try:
            index_path = self.base_dir / "index.json"
            if index_path.exists():
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
        except:
            pass

        total_fixed = 0
        for local_id in broken_anime:
            try:
                # Get anime name
                anime_name = str(local_id)
                str_id = str(local_id)
                if str_id in index_data:
                    anime_name = index_data[str_id].get("name", str(local_id))

                # Get API ID
                api_id = apiid_map.get(str_id, 0)
                if api_id == 0:
                    self.logger.warning(f"No API ID found for local ID {local_id}, skipping")
                    continue

                self.logger.info(f"\n--- Fixing coordinates for {anime_name} (ID {local_id}, API {api_id}) ---")
                fixed = self._fix_anime_coordinates(local_id, api_id, anime_name)
                total_fixed += fixed

                # Brief pause between anime to avoid rate limiting
                time.sleep(2)

            except Exception as e:
                self.logger.error(f"Error fixing coordinates for local ID {local_id}: {e}")
                continue

        self.logger.info(f"Coordinate fix scan complete. Total points fixed: {total_fixed}")
        return total_fixed

    def run(self, auto_mode=False, max_anime=5, wait_time=1800, max_wait_attempts=3, fix_coords=False):
        """Run the scraper

        Args:
            auto_mode: If True, run in automatic mode without user interaction
            max_anime: Maximum number of anime to scrape in auto mode
            fix_coords: If True, scan and fix zero-coordinate points after scraping
        """
        try:
            # When called from anime_pilgrimage_daily_updater.py, we don't need to check for locks
            # or create locks here, as that's handled by the caller

            # Only check for locks when run directly (not from daily updater)
            if not auto_mode:
                # Check if another instance is running
                if self.is_process_running():
                    self.logger.warning("Another instance of the anime pilgrimage scraper is already running")
                    return False

                # Check if monthly updater is running
                wait_attempts = 0
                while self.is_monthly_updater_running() and wait_attempts < max_wait_attempts:
                    wait_attempts += 1
                    self.logger.warning(f"Monthly updater is running. Waiting {wait_time/60} minutes (attempt {wait_attempts}/{max_wait_attempts})")
                    time.sleep(wait_time)  # Wait for the specified time

                    # If we've waited the maximum number of times, delay for 12 hours
                    if wait_attempts == max_wait_attempts:
                        self.logger.warning("Maximum wait attempts reached. Delaying for 12 hours.")
                        time.sleep(43200)  # 12 hours in seconds

                        # Check one more time
                        if self.is_monthly_updater_running():
                            self.logger.error("Monthly updater is still running after 12 hours. Exiting.")
                            return False

                # Create lock file
                if not self.create_lock_file():
                    self.logger.error("Failed to create lock file. Exiting.")
                    return False

            self.logger.info("Starting anime pilgrimage scraper")

            # Run extract_apiid.py to refresh apiid.json
            try:
                self.logger.info("Running extract_apiid.py to refresh apiid.json")
                import extract_apiid
                extract_apiid.extract_apiid(base_dir='pic/data')
                self.logger.info("Successfully refreshed apiid.json")
            except Exception as e:
                self.logger.error(f"Error refreshing apiid.json: {e}")
                # Continue anyway as this is not critical

            try:
                # Get anime list
                if auto_mode:
                    self.logger.info("Running in automatic mode")
                    anime_list = self.get_anime_list()
                else:
                    # Ask user which mode to use for getting anime list
                    print("\nChoose how to get the anime list:")
                    print("1. Automatic scrolling (may not get all anime)")
                    print("2. Manual control (recommended for getting all anime)")
                    mode = input("Enter your choice (1/2): ").strip()

                    # Get anime list based on selected mode
                    if mode == "2":
                        anime_list = self.get_anime_list_with_manual_control()
                    else:
                        anime_list = self.get_anime_list()

                if not anime_list:
                    self.logger.warning("No anime found. Exiting.")
                    return False

                # Determine anime range
                if auto_mode:
                    # In auto mode, just take the first max_anime entries
                    start_idx = 1
                    end_idx = min(max_anime, len(anime_list))
                    self.logger.info(f"Auto mode: Scraping anime {start_idx} to {end_idx} out of {len(anime_list)}")
                else:
                    # Get user input for anime range
                    start_idx = int(input("\nEnter the starting anime number to scrape: "))
                    end_idx = int(input("Enter the ending anime number to scrape: "))

                    # Validate input
                    if start_idx < 1 or end_idx > len(anime_list) or start_idx > end_idx:
                        print("Invalid range. Exiting.")
                        return False

                # Get starting local folder ID
                if auto_mode:
                    local_folder_id = self.get_next_available_local_id()
                    self.logger.info(f"Auto mode: Using local folder ID {local_folder_id}")
                else:
                    local_folder_id = int(input("Enter the starting local folder ID: "))

                # Always use automatic mode for point extraction
                if not auto_mode:
                    print("Automatic mode enabled for point extraction. Points will be extracted without manual intervention.")

                # Scrape selected anime
                anime_data_list = []
                updated_anime = []  # Track updated anime
                new_anime = []      # Track new anime

                for i in range(start_idx - 1, end_idx):
                    anime_info = anime_list[i]
                    self.logger.info(f"[{i+1}/{end_idx}] Checking anime: {anime_info['title']}")

                    # Check if this anime is already in the database
                    exists, existing_id = self.is_anime_already_in_database(anime_info['title'])
                    if exists:
                        self.logger.info(f"Anime '{anime_info['title']}' already exists with ID {existing_id}, checking for updates")
                        # Try to update the existing anime with new pilgrimage points
                        updated_data = self.update_existing_anime(anime_info, existing_id)
                        if updated_data:
                            new_points_count = updated_data.get('new_points_count', 0)
                            self.logger.info(f"Updated anime '{anime_info['title']}' with {new_points_count} new points")
                            anime_data_list.append(updated_data)

                            # Add to updated anime list with detailed info
                            latest_point = None
                            if updated_data['anime_data']['points'] and len(updated_data['anime_data']['points']) > 0:
                                latest_point = updated_data['anime_data']['points'][-1]

                            updated_anime.append({
                                'name': anime_info['title'],
                                'id': existing_id,
                                'new_points': new_points_count,
                                'latest_point': latest_point
                            })

                            # Update index.json with the updated anime data
                            self.logger.info("Saving updates to index.json...")
                            self.update_index_json([updated_data], update_mode=True)
                            self.logger.info("Updates saved.")
                        else:
                            self.logger.info(f"No updates found for anime '{anime_info['title']}'")
                        continue

                    self.logger.info(f"Scraping anime: {anime_info['title']}")
                    anime_data = self.scrape_anime(anime_info, local_folder_id, False)  # Always use automatic mode

                    if anime_data:
                        anime_data_list.append(anime_data)

                        # Add to new anime list with detailed info
                        new_anime.append({
                            'name': anime_info['title'],
                            'id': local_folder_id,
                            'points': anime_data['anime_data']['points']
                        })

                        # Save progress after each anime
                        self.logger.info("Saving progress to index.json...")
                        self.update_index_json([anime_data])
                        self.logger.info("Progress saved.")

                    local_folder_id += 1

                # Run zero-coordinate fix if requested (scans all existing data)
                fix_fixed_count = 0
                if fix_coords:
                    self.logger.info("Running zero-coordinate point fix scan...")
                    try:
                        fix_fixed_count = self.fix_zero_coordinate_points()
                        if fix_fixed_count > 0:
                            self.logger.info(f"Fixed {fix_fixed_count} zero-coordinate points")
                    except Exception as e:
                        self.logger.error(f"Error during coordinate fix scan: {e}")

                # Final update to index.json is not needed since we save after each anime
                if not anime_data_list and fix_fixed_count == 0:
                    self.logger.warning("No anime data was collected. No new anime or updates found.")
                    # Return a special status code (2) to indicate no new data but successful execution
                    return 2
                elif not anime_data_list and fix_fixed_count > 0:
                    # No new data, but fixed existing coordinates
                    self.logger.info(f"Fixed {fix_fixed_count} zero-coordinate points (no new anime).")
                    if auto_mode:
                        result_data = {
                            'updated_anime': updated_anime,
                            'new_anime': new_anime,
                            'fixed_coords': fix_fixed_count
                        }
                        return result_data
                    else:
                        return True
                else:
                    self.logger.info(f"Successfully scraped {len(anime_data_list)} anime.")

                    # Return detailed information about the updates
                    if auto_mode:
                        result_data = {
                            'updated_anime': updated_anime,
                            'new_anime': new_anime,
                            'fixed_coords': fix_fixed_count
                        }
                        self.logger.info(f"Returning detailed update information: {len(updated_anime)} updated anime, {len(new_anime)} new anime")
                        return result_data
                    else:
                        # For manual mode, just return True
                        self.logger.info("Scraping completed successfully!")
                        return True

            finally:
                # Only remove the lock file if we created it
                if not auto_mode:
                    self.remove_lock_file()
                self.driver.quit()

        except Exception as e:
            self.logger.error(f"Error running scraper: {e}")
            # Make sure to remove the lock file in case of error, but only if we created it
            if not auto_mode:
                self.remove_lock_file()
            return False

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Anime Pilgrimage Scraper')
    parser.add_argument('--auto', action='store_true', help='Run in automatic mode without user interaction')
    parser.add_argument('--max-anime', type=int, default=5, help='Maximum number of anime to scrape in auto mode')
    parser.add_argument('--wait-time', type=int, default=1800, help='Time to wait in seconds if another process is running')
    parser.add_argument('--max-wait-attempts', type=int, default=3, help='Maximum number of times to wait before giving up')
    parser.add_argument('--headless', action='store_true', default=True, help='Run Chrome in headless mode')
    parser.add_argument('--base-dir', type=str, default=BASE_DIR, help='Base directory for anime data')
    parser.add_argument('--fix-coords', action='store_true', default=False,
                        help='Scan and fix zero-coordinate points after scraping')
    parser.add_argument('--only-fix-coords', action='store_true', default=False,
                        help='Only fix zero-coordinate points, skip scraping new/updated anime')

    args = parser.parse_args()

    # --only-fix-coords mode: just fix existing points, no scraping
    if args.only_fix_coords:
        scraper = AnimePilgrimageScraper(
            base_dir=args.base_dir,
            headless=args.headless,
            auto_mode=True
        )
        try:
            fixed = scraper.fix_zero_coordinate_points()
            print(f"\nFixed {fixed} zero-coordinate points in total.")
            sys.exit(0 if fixed >= 0 else 1)
        except Exception as e:
            print(f"Error during coordinate fix: {e}")
            sys.exit(1)
        finally:
            scraper.driver.quit()

    # Normal mode with optional fix_coords
    scraper = AnimePilgrimageScraper(
        base_dir=args.base_dir,
        headless=args.headless,
        auto_mode=args.auto
    )

    success = scraper.run(
        auto_mode=args.auto,
        max_anime=args.max_anime,
        wait_time=args.wait_time,
        max_wait_attempts=args.max_wait_attempts,
        fix_coords=args.fix_coords
    )

    # Return appropriate exit code
    # success can be True (success with updates), 2 (success but no new data), or False (error)
    if success is True or success == 2:
        sys.exit(0)  # Success
    else:
        sys.exit(1)  # Error

if __name__ == "__main__":
    main()
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
        }
        self.base_dir = Path(base_dir)
        self.headless = headless
        self.auto_mode = auto_mode
        self.setup_driver()

    def setup_logging(self):
        logger = logging.getLogger("AnimePilgrimageScraper")
        logger.setLevel(logging.INFO)

        c_handler = logging.StreamHandler()
        f_handler = logging.FileHandler("anime_pilgrimage_scraper.log")
        c_handler.setLevel(logging.INFO)
        f_handler.setLevel(logging.INFO)

        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        c_handler.setFormatter(formatter)
        f_handler.setFormatter(formatter)

        logger.addHandler(c_handler)
        logger.addHandler(f_handler)

        return logger

    def setup_driver(self):
        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=375,812")

        mobile_emulation = {
            "deviceMetrics": {"width": 375, "height": 812, "pixelRatio": 3.0},
            "userAgent": self.headers["User-Agent"]
        }
        chrome_options.add_experimental_option("mobileEmulation", mobile_emulation)

        self.driver = webdriver.Chrome(options=chrome_options)
        self.logger.info("Chrome浏览器驱动初始化成功")

    def get_anime_list(self):
        self.logger.info("从最近更新页面获取动漫列表...")

        RECENTLY_UPDATED_API = "https://recently-updated.animepilgrimage.com/updated"

        anime_list = []
        stop_found = False

        try:
            self.logger.info("尝试通过 API 获取第 1 页数据...")
            resp = requests.get(f"{RECENTLY_UPDATED_API}/page-1.json", headers=self.headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                has_next = data.get("hasNext", False)
                self.logger.info(f"API 第 1 页返回 {len(items)} 条, hasNext={has_next}")

                for item in items:
                    title = ""
                    for locale in ["ja", "en"]:
                        t = item.get("title", {}).get(locale, "")
                        if t:
                            title = t
                            break
                    anime_id = item.get("animeId", "")
                    anime_slug = item.get("animeSlug", "")
                    link = f"https://www.animepilgrimage.com/ja/maps/anime/{anime_id}/{anime_slug}" if anime_id else ""

                    if title and link:
                        anime_list.append({"title": title, "link": link})
                        if STOP_ANIME_TITLE in title:
                            stop_found = True

                page = 2
                while has_next and not stop_found and page <= 500:
                    try:
                        resp = requests.get(f"{RECENTLY_UPDATED_API}/page-{page}.json", headers=self.headers, timeout=15)
                        if resp.status_code != 200:
                            self.logger.warning(f"API 第 {page} 页返回状态码 {resp.status_code}")
                            break
                        data = resp.json()
                        items = data.get("items", [])
                        has_next = data.get("hasNext", False)
                        self.logger.info(f"API 第 {page} 页返回 {len(items)} 条, hasNext={has_next}")

                        for item in items:
                            title = ""
                            for locale in ["ja", "en"]:
                                t = item.get("title", {}).get(locale, "")
                                if t:
                                    title = t
                                    break
                            anime_id = item.get("animeId", "")
                            anime_slug = item.get("animeSlug", "")
                            link = f"https://www.animepilgrimage.com/ja/maps/anime/{anime_id}/{anime_slug}" if anime_id else ""

                            if title and link:
                                anime_list.append({"title": title, "link": link})
                                if STOP_ANIME_TITLE in title:
                                    stop_found = True

                        page += 1
                        time.sleep(1)
                    except Exception as e:
                        self.logger.warning(f"获取 API 第 {page} 页失败: {e}")
                        break

                if anime_list:
                    self.logger.info(f"API 方式共提取 {len(anime_list)} 部动漫")
                    if stop_found:
                        self.logger.info(f"已找到终止番剧: {STOP_ANIME_TITLE}")
                    return anime_list
            else:
                self.logger.warning(f"API 第 1 页返回状态码 {resp.status_code}, 回退到 Selenium 方式")
        except Exception as e:
            self.logger.warning(f"API 获取失败: {e}, 回退到 Selenium 方式")

        self.driver.get(self.recently_updated_url)

        with open("page_source_initial.html", "w", encoding="utf-8") as f:
            f.write(self.driver.page_source)
        self.logger.info("已保存初始页面源代码到 page_source_initial.html")

        primary_selector = "div.container__poster"
        fallback_selectors = [
            "a[href*='/maps/anime/']",
            "div.poster__inner",
            "h3.poster__title",
            "a[href*='/ja/maps/anime/']",
        ]

        found_selector = None
        for selector in [primary_selector] + fallback_selectors:
            try:
                self.logger.info(f"Trying selector: {selector}")
                WebDriverWait(self.driver, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                found_selector = selector
                self.logger.info(f"Found elements with selector: {selector}")
                break
            except TimeoutException:
                self.logger.warning(f"Selector {selector} failed")
                continue

        if not found_selector:
            self.logger.error("Could not find anime list elements with any selector. The website structure might have changed.")
            with open("page_source.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            self.logger.error("Saved page source to page_source.html for debugging.")
            return []

        self.logger.info("开始无限滚动加载全部动漫...")
        time.sleep(3)

        sidebar = None
        sidebar_selectors = [
            "[class*='mapSideNavOuter']",
            "[class*='mapSideNav__CgqtP']",
            "[class*='mapSideNav']",
            "[class*='mapSide___']",
        ]
        for sel in sidebar_selectors:
            try:
                elems = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for elem in elems:
                    sh = self.driver.execute_script("return arguments[0].scrollHeight", elem)
                    ch = self.driver.execute_script("return arguments[0].clientHeight", elem)
                    overflow_y = self.driver.execute_script("return getComputedStyle(arguments[0]).overflowY", elem)
                    self.logger.info(f"检查侧边栏: {sel} scrollHeight={sh}, clientHeight={ch}, overflowY={overflow_y}")
                    if sh > ch or overflow_y in ('scroll', 'auto'):
                        sidebar = elem
                        self.logger.info(f"找到可滚动侧边栏: {sel}")
                        break
                if sidebar:
                    break
            except Exception as e:
                self.logger.warning(f"检查选择器 {sel} 时出错: {e}")
                continue

        if not sidebar:
            try:
                sidebar = self.driver.execute_script("""
                    var posters = document.querySelectorAll('div.container__poster');
                    if (posters.length > 0) {
                        var el = posters[0];
                        while (el && el !== document.body) {
                            var style = getComputedStyle(el);
                            if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
                                return el;
                            }
                            el = el.parentElement;
                        }
                    }
                    return null;
                """)
                if sidebar:
                    self.logger.info("通过 JS 查找到 container__poster 的可滚动父元素")
            except Exception as e:
                self.logger.warning(f"JS 查找可滚动父元素失败: {e}")

        scroll_attempts = 0
        max_scroll_attempts = 200
        no_change_count = 0
        max_no_change = 5
        prev_count = len(self.driver.find_elements(By.CSS_SELECTOR, "div.container__poster"))

        while scroll_attempts < max_scroll_attempts and no_change_count < max_no_change and not stop_found:
            if sidebar:
                self.driver.execute_script("""
                    arguments[0].scrollTop = arguments[0].scrollHeight;
                    arguments[0].dispatchEvent(new Event('scroll', {bubbles: true}));
                """, sidebar)
            else:
                self.driver.execute_script("""
                    window.scrollTo(0, document.body.scrollHeight);
                    window.dispatchEvent(new Event('scroll', {bubbles: true}));
                """)
            time.sleep(3)

            page_text = self.driver.page_source
            if STOP_ANIME_TITLE in page_text:
                self.logger.info(f"到达底部番剧: {STOP_ANIME_TITLE}，停止滚动")
                stop_found = True
                time.sleep(2)
                break

            current_count = len(self.driver.find_elements(By.CSS_SELECTOR, "div.container__poster"))
            if current_count > prev_count:
                self.logger.info(f"滚动 {scroll_attempts+1}: 加载了 {current_count} 个动漫 (新增 {current_count - prev_count})")
                prev_count = current_count
                no_change_count = 0
            else:
                no_change_count += 1
                self.logger.info(f"无新内容加载: {no_change_count}/{max_no_change}")

            scroll_attempts += 1
            if scroll_attempts % 10 == 0:
                self.logger.info(f"滚动尝试 {scroll_attempts}/{max_scroll_attempts} - 当前 {current_count} 个动漫")

        if not stop_found:
            self.logger.warning(f"滚动 {max_scroll_attempts} 次后未找到终止番剧 '{STOP_ANIME_TITLE}'")

        anime_items = self.driver.find_elements(By.CSS_SELECTOR, "div.container__poster")
        self.logger.info(f"使用 container__poster 找到 {len(anime_items)} 个动漫条目")

        if not anime_items:
            link_items = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/maps/anime/']")
            self.logger.info(f"使用链接选择器找到 {len(link_items)} 个动漫条目")
            for item in link_items:
                try:
                    title = item.get_attribute("title") or ""
                    if not title:
                        try:
                            title = item.find_element(By.CSS_SELECTOR, "h3").text.strip()
                        except:
                            pass
                    if not title:
                        href = item.get_attribute("href") or ""
                        parts = href.rstrip("/").split("/")
                        title = parts[-1].replace("-", " ").title() if parts else "Unknown"

                    link = item.get_attribute("href") or ""
                    if link and not link.startswith("http"):
                        link = f"https://www.animepilgrimage.com{link}"

                    if title and link:
                        anime_list.append({"title": title, "link": link})
                except Exception as e:
                    self.logger.warning(f"提取链接条目时出错: {e}")
            return anime_list

        for i, item in enumerate(anime_items, 1):
            try:
                title = ""
                link = ""

                try:
                    link_elem = item.find_element(By.CSS_SELECTOR, "a[href*='/maps/anime/']")
                    link = link_elem.get_attribute("href") or ""
                    title = link_elem.get_attribute("title") or ""
                except:
                    pass

                if not title:
                    try:
                        title = item.find_element(By.CSS_SELECTOR, "h3.poster__title").text.strip()
                    except:
                        pass

                if not title:
                    try:
                        title = item.find_element(By.CSS_SELECTOR, "h3").text.strip()
                    except:
                        pass

                if not link:
                    try:
                        a_elem = item.find_element(By.TAG_NAME, "a")
                        link = a_elem.get_attribute("href") or ""
                    except:
                        pass

                if link and not link.startswith("http"):
                    link = f"https://www.animepilgrimage.com{link}"

                if not title and link:
                    parts = link.rstrip("/").split("/")
                    title = parts[-1].replace("-", " ").title() if parts else "Unknown"

                if title and link:
                    anime_list.append({"title": title, "link": link})
                    if i <= 10:
                        self.logger.info(f"{i}. {title}")
                else:
                    self.logger.warning(f"条目 {i} 缺少标题或链接")
            except Exception as e:
                self.logger.warning(f"提取动漫条目 {i} 时出错: {e}")

        self.logger.info(f"共提取 {len(anime_list)} 部动漫")
        return anime_list

    def generate_timestamp_id(self):
        now = datetime.datetime.now()
        timestamp = now.strftime("%Y%m%d%H%M%S")
        random_suffix = random.randint(100, 999)
        return int(f"{timestamp}{random_suffix}")

    @staticmethod
    def create_lock_file():
        try:
            with open(LOCK_FILE, 'w') as f:
                f.write(str(datetime.datetime.now()))
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
        exists = os.path.exists(LOCK_FILE)
        print(f"DEBUG: Checking if lock file exists: {LOCK_FILE}, result: {exists}")
        if exists:
            print(f"DEBUG: Lock file content: {open(LOCK_FILE, 'r').read() if os.path.exists(LOCK_FILE) else 'File not found'}")
        return exists

    @staticmethod
    def is_monthly_updater_running():
        return os.path.exists("anitabi_updater.lock")

    def get_next_available_local_id(self):
        try:
            folders = [int(f.name) for f in self.base_dir.glob('*') if f.is_dir() and f.name.isdigit()]
            highest_folder = max(folders) if folders else 0

            highest_api_id = 0
            if os.path.exists('apiid.json'):
                with open('apiid.json', 'r', encoding='utf-8') as f:
                    apiid_data = json.load(f)
                    local_ids = [int(k) for k in apiid_data.keys()]
                    highest_api_id = max(local_ids) if local_ids else 0

            next_id = max(highest_folder, highest_api_id) + 1
            self.logger.info(f"Next available local ID: {next_id}")
            return next_id
        except Exception as e:
            self.logger.error(f"Error finding next available local ID: {e}")
            return 5901

    def extract_dominant_color(self, image_path):
        try:
            img = Image.open(image_path)
            img = img.resize((100, 100))
            img = img.convert("RGBA")

            pixels = list(img.getdata())
            r_total = g_total = b_total = count = 0

            for r, g, b, a in pixels:
                if a > 200:
                    r_total += r
                    g_total += g
                    b_total += b
                    count += 1

            if count == 0:
                return "#7f6a95"

            r_avg = r_total // count
            g_avg = g_total // count
            b_avg = b_total // count

            hex_color = "#{:02x}{:02x}{:02x}".format(r_avg, g_avg, b_avg)
            return hex_color
        except Exception as e:
            print(f"Error extracting dominant color: {e}")
            return "#7f6a95"

    def create_info_json(self, anime_data, local_folder_id):
        try:
            unique_id = self.generate_timestamp_id()

            info_data = {
                "id": unique_id,
                "cn": anime_data["name_cn"],
                "title": anime_data["name"],
                "cover": anime_data["cover"],
                "pointsLength": len(anime_data["points"]),
                "local_id": local_folder_id
            }

            folder_path = self.base_dir / str(local_folder_id)
            os.makedirs(folder_path, exist_ok=True)
            info_path = folder_path / "info.json"

            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(info_data, f, ensure_ascii=False, indent=2)

            print(f"Created info.json with ID: {unique_id}")
            return info_path
        except Exception as e:
            print(f"Error creating info.json: {e}")
            return None

    def download_image(self, url, save_path):
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code == 200:
                save_path = Path(save_path)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                return True
            else:
                print(f"Failed to download image: {url}, status code: {response.status_code}")
                return False
        except Exception as e:
            print(f"Error downloading image {url}: {e}")
            return False

    def _extract_coordinates_from_url(self, url):
        if not url:
            return 0, 0

        url = url.replace(" ", "").replace("%2C", ",")

        coordinate_patterns = [
            (r'@(-?\d+\.\d+),(-?\d+\.\d+)', "@"),
            (r'destination=(-?\d+\.\d+),(-?\d+\.\d+)', "destination"),
            (r'\?q=(-?\d+\.\d+),(-?\d+\.\d+)', "q"),
            (r'll=(-?\d+\.\d+),(-?\d+\.\d+)', "ll"),
            (r'query=(-?\d+\.\d+),(-?\d+\.\d+)', "query"),
            (r'center=(-?\d+\.\d+),(-?\d+\.\d+)', "center"),
            (r'daddr=(-?\d+\.\d+),(-?\d+\.\d+)', "daddr"),
            (r'saddr=(-?\d+\.\d+),(-?\d+\.\d+)', "saddr"),
            (r'loc:(-?\d+\.\d+),(-?\d+\.\d+)', "loc:"),
            (r'loc=(-?\d+\.\d+),(-?\d+\.\d+)', "loc="),
            (r'lat=(-?\d+\.\d+).*lon=(-?\d+\.\d+)', "lat/lon"),
            (r'lat=(-?\d+\.\d+).*lng=(-?\d+\.\d+)', "lat/lng"),
            (r'latitude=(-?\d+\.\d+).*longitude=(-?\d+\.\d+)', "latitude/longitude"),
        ]

        for pattern, pattern_name in coordinate_patterns:
            match = re.search(pattern, url)
            if match:
                lat = float(match.group(1))
                lng = float(match.group(2))
                if -90 <= lat <= 90 and -180 <= lng <= 180:
                    return lat, lng

        decimal_numbers = re.findall(r'(-?\d+\.\d+)', url)
        if len(decimal_numbers) >= 2:
            for i in range(len(decimal_numbers) - 1):
                potential_lat = float(decimal_numbers[i])
                potential_lng = float(decimal_numbers[i + 1])
                if -90 <= potential_lat <= 90 and -180 <= potential_lng <= 180:
                    return potential_lat, potential_lng

        return 0, 0

    def _resolve_and_extract_coords(self, url):
        if not url:
            return 0, 0

        lat, lng = self._extract_coordinates_from_url(url)
        if lat != 0 and lng != 0:
            return lat, lng

        if "goo.gl" in url or "maps.app" in url:
            try:
                self.logger.info(f"解析短链接: {url}")
                resp = requests.head(url, allow_redirects=True, timeout=8, headers=self.headers)
                full_url = resp.url
                self.logger.info(f"重定向到: {full_url[:120]}")
                lat, lng = self._extract_coordinates_from_url(full_url)
                if lat != 0 and lng != 0:
                    return lat, lng
            except Exception as e:
                self.logger.warning(f"解析短链接失败: {e}")

            try:
                resp = requests.get(url, allow_redirects=True, timeout=8, headers=self.headers, stream=True)
                full_url = resp.url
                resp.close()
                self.logger.info(f"GET重定向到: {full_url[:120]}")
                lat, lng = self._extract_coordinates_from_url(full_url)
                if lat != 0 and lng != 0:
                    return lat, lng
            except Exception as e:
                self.logger.warning(f"GET解析短链接失败: {e}")

            try:
                current_url = self.driver.current_url
                self.driver.get(url)
                time.sleep(5)
                resolved = self.driver.current_url
                self.logger.info(f"Selenium重定向到: {resolved[:120]}")
                lat, lng = self._extract_coordinates_from_url(resolved)
                self.driver.get(current_url)
                time.sleep(3)
                if lat != 0 and lng != 0:
                    return lat, lng
            except Exception as e:
                self.logger.warning(f"Selenium解析短链接失败: {e}")

        return 0, 0

    def _get_google_maps_link_from_detail(self):
        link_selectors = [
            "a[href*='google.com/maps/dir']",
            "a[href*='google.com/maps']",
            "a[href*='maps.google']",
            "a[href*='goo.gl/maps']",
            "a[href*='maps.app.goo.gl']",
        ]
        for sel in link_selectors:
            try:
                links = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for link in links:
                    href = link.get_attribute("href") or ""
                    if href and ("google" in href.lower() or "goo.gl" in href.lower()):
                        return href
            except:
                continue
        return ""

    def _extract_card_info(self, card):
        img_url = ""
        try:
            img = card.find_element(By.CSS_SELECTOR, "img")
            src = img.get_attribute("src") or ""
            if src:
                if src.startswith("/_next/image?"):
                    url_match = re.search(r'url=([^&]+)', src)
                    if url_match:
                        from urllib.parse import unquote
                        src = unquote(url_match.group(1))
                img_url = src
        except:
            pass

        name = ""
        try:
            name_elem = card.find_element(By.CSS_SELECTOR, "[class*='horizontalCardName']")
            name = name_elem.text.strip()
        except:
            pass

        ep = ""
        try:
            meta_elem = card.find_element(By.CSS_SELECTOR, "[class*='horizontalCardMeta']")
            ep = meta_elem.text.strip()
        except:
            pass

        return img_url, name, ep

    def _find_point_elements(self):
        sidebar = None
        for sel in ["[class*='mapSideContainer']", "[class*='mapSideNav__CgqtP']", "[class*='mapSideNav']"]:
            try:
                elems = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for elem in elems:
                    cards = elem.find_elements(By.CSS_SELECTOR, "[class*='horizontalCard__Ev']")
                    if cards:
                        sidebar = elem
                        break
                if sidebar:
                    break
            except:
                continue

        search_root = sidebar if sidebar else self.driver

        cards = search_root.find_elements(By.CSS_SELECTOR, "[class*='horizontalCard__Ev']")
        if cards:
            filtered = []
            for c in cards:
                try:
                    c.find_element(By.XPATH, "./ancestor::div[@data-locked='true']")
                    continue
                except:
                    filtered.append(c)
            self.logger.info(f"找到 {len(filtered)} 个巡礼点位 (过滤锁定 {len(cards)-len(filtered)} 个)")
            return filtered

        return []

    def _make_point(self, local_folder_id, point_index, card_data):
        point = {
            "id": f"{local_folder_id}-{point_index}",
            "name": card_data.get("name", "Unknown"),
            "image": card_data.get("img_url", ""),
            "ep": card_data.get("ep", ""),
            "geo": card_data.get("geo", [0, 0])
        }
        if card_data.get("ts"):
            point["s"] = card_data["ts"]
        return point

    def _scroll_detail_page(self):
        self.logger.info("滚动侧边栏以加载所有巡礼点位...")

        sidebar = None
        sidebar_selectors = [
            "[class*='mapSideNavOuter']",
            "[class*='mapSideNav__CgqtP']",
            "[class*='mapSideNav']",
            "[class*='mapSide___']",
        ]
        for sel in sidebar_selectors:
            try:
                elems = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for elem in elems:
                    sh = self.driver.execute_script("return arguments[0].scrollHeight", elem)
                    ch = self.driver.execute_script("return arguments[0].clientHeight", elem)
                    overflow_y = self.driver.execute_script("return getComputedStyle(arguments[0]).overflowY", elem)
                    if sh > ch or overflow_y in ('scroll', 'auto'):
                        sidebar = elem
                        self.logger.info(f"找到可滚动侧边栏: {sel} (scrollHeight={sh}, clientHeight={ch})")
                        break
                if sidebar:
                    break
            except:
                continue

        if not sidebar:
            try:
                sidebar = self.driver.execute_script("""
                    var cards = document.querySelectorAll('[class*="horizontalCard__Ev"]');
                    if (cards.length > 0) {
                        var el = cards[0];
                        while (el && el !== document.body) {
                            var style = getComputedStyle(el);
                            if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
                                return el;
                            }
                            el = el.parentElement;
                        }
                    }
                    return null;
                """)
                if sidebar:
                    self.logger.info("通过 JS 查找到卡片的可滚动父元素")
            except Exception as e:
                self.logger.warning(f"JS 查找可滚动父元素失败: {e}")

        if not sidebar:
            self.logger.warning("未找到可滚动侧边栏，使用页面滚动")
            last_height = self.driver.execute_script("return document.body.scrollHeight")
            for i in range(10):
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = self.driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height
            return

        prev_count = 0
        no_change = 0
        max_no_change = 5
        max_scrolls = 100

        for i in range(max_scrolls):
            self.driver.execute_script("""
                arguments[0].scrollTop = arguments[0].scrollHeight;
                arguments[0].dispatchEvent(new Event('scroll', {bubbles: true}));
            """, sidebar)
            time.sleep(3)

            cards = self.driver.find_elements(By.CSS_SELECTOR, "[class*='horizontalCard__Ev']")
            current_count = len(cards)

            if current_count > prev_count:
                self.logger.info(f"滚动 {i+1}: 加载了 {current_count} 个点位 (新增 {current_count - prev_count})")
                prev_count = current_count
                no_change = 0
            else:
                no_change += 1
                if no_change >= max_no_change:
                    self.logger.info(f"连续 {max_no_change} 次无新内容，停止滚动。共 {current_count} 个点位")
                    break

        self.driver.execute_script("arguments[0].scrollTop = 0", sidebar)
        time.sleep(1)

    def _extract_cover_image(self, images_folder, local_folder_id):
        cover_image_url = ""

        cover_selectors = [
            (By.CSS_SELECTOR, "[class*='posterInner'] img"),
            (By.CSS_SELECTOR, "[class*='locationsPoster'] img"),
            (By.CSS_SELECTOR, "img[alt*='poster']"),
            (By.CSS_SELECTOR, "img[fetchpriority='high']"),
        ]

        for selector in cover_selectors:
            try:
                cover_img = self.driver.find_element(*selector)
                cover_url = cover_img.get_attribute("src")
                if cover_url:
                    cover_path = images_folder / "1.jpg"
                    if self.download_image(cover_url, cover_path):
                        cover_image_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/1.jpg"
                        self.logger.info(f"Downloaded cover image: {cover_url}")
                        return cover_image_url
            except:
                continue

        if not cover_image_url:
            try:
                og_img = self.driver.find_element(By.CSS_SELECTOR, "meta[property='og:image']")
                og_url = og_img.get_attribute("content")
                if og_url:
                    cover_path = images_folder / "1.jpg"
                    if self.download_image(og_url, cover_path):
                        cover_image_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/1.jpg"
                        self.logger.info(f"Downloaded cover from og:image: {og_url}")
                        return cover_image_url
            except:
                pass

        if not cover_image_url:
            try:
                all_images = self.driver.find_elements(By.TAG_NAME, "img")
                largest_image = None
                largest_size = 0

                for img in all_images:
                    try:
                        width = int(img.get_attribute("width") or 0)
                        height = int(img.get_attribute("height") or 0)
                        size = width * height

                        if width < 100 or height < 100:
                            continue

                        src = img.get_attribute("src") or ""
                        alt = img.get_attribute("alt") or ""
                        if any(kw in src.lower() or kw in alt.lower() for kw in ["logo", "icon", "button", "avatar"]):
                            continue

                        if size > largest_size:
                            largest_size = size
                            largest_image = img
                    except:
                        continue

                if largest_image:
                    cover_url = largest_image.get_attribute("src")
                    if cover_url:
                        cover_path = images_folder / "1.jpg"
                        if self.download_image(cover_url, cover_path):
                            cover_image_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/1.jpg"
                            self.logger.info(f"Downloaded largest image as cover: {cover_url}")
            except Exception as e:
                self.logger.error(f"Error finding largest image: {e}")

        return cover_image_url

    def _extract_anime_title(self, default_title):
        title_selectors = [
            (By.CSS_SELECTOR, "h1[class*='locationsTitle']"),
            (By.CSS_SELECTOR, "h1"),
            (By.CSS_SELECTOR, "[class*='anime-detail__title']"),
            (By.CSS_SELECTOR, ".title"),
        ]

        for selector in title_selectors:
            try:
                title_elem = self.driver.find_element(*selector)
                text = title_elem.text.strip()
                if text:
                    return text
            except:
                continue

        try:
            page_title = self.driver.title
            if page_title and " - " in page_title:
                return page_title.split(" - ")[0].strip()
        except:
            pass

        return default_title

    def scrape_anime(self, anime_info, local_folder_id, manual_edit=False):
        self.logger.info(f"Scraping anime: {anime_info['title']}")

        folder_path = self.base_dir / str(local_folder_id)
        images_folder = folder_path / "images"
        os.makedirs(images_folder, exist_ok=True)

        self.driver.get(anime_info['link'])
        time.sleep(8)

        try:
            selectors_to_try = [
                (By.CSS_SELECTOR, "h1"),
                (By.CSS_SELECTOR, "[class*='locationsTitle']"),
                (By.CSS_SELECTOR, "[class*='posterInner'] img"),
                (By.TAG_NAME, "img"),
            ]

            for selector in selectors_to_try:
                try:
                    WebDriverWait(self.driver, 30).until(
                        EC.presence_of_element_located(selector)
                    )
                    self.logger.info(f"页面加载完成，找到元素: {selector}")
                    break
                except TimeoutException:
                    continue
            else:
                self.logger.error(f"等待动漫页面加载超时: {anime_info['link']}")
                return None
        except Exception as e:
            self.logger.error(f"Error waiting for page to load: {e}")
            return None

        anime_title = self._extract_anime_title(anime_info['title'])

        cover_image_url = self._extract_cover_image(images_folder, local_folder_id)

        theme_color = "#7f6a95"
        if os.path.exists(f"{images_folder}/1.jpg"):
            theme_color = self.extract_dominant_color(f"{images_folder}/1.jpg")

        self._scroll_detail_page()

        point_elements = self._find_point_elements()
        total = len(point_elements)
        self.logger.info(f"共 {total} 个点位，开始逐个提取...")

        points = []
        for i, card in enumerate(point_elements):
            try:
                img_src, name, ep = self._extract_card_info(card)

                img_url = ""
                if img_src:
                    img_path = f"{images_folder}/{local_folder_id}-{i+1}.jpg"
                    if self.download_image(img_src, img_path):
                        img_url = f"https://image.xinu.ink/pic/data/{local_folder_id}/images/{local_folder_id}-{i+1}.jpg"

                geo = [0, 0]
                try:
                    card.click()
                    time.sleep(2)

                    maps_link = self._get_google_maps_link_from_detail()
                    if maps_link:
                        lat, lng = self._resolve_and_extract_coords(maps_link)
                        if lat != 0 or lng != 0:
                            geo = [lat, lng]
                            self.logger.info(f"  [{i+1}/{total}] {name[:25]} 坐标: ({lat:.6f}, {lng:.6f})")
                        else:
                            self.logger.warning(f"  [{i+1}/{total}] {name[:25]} 无法解析坐标")
                    else:
                        self.logger.warning(f"  [{i+1}/{total}] {name[:25]} 未找到Google地图链接")
                except Exception as e:
                    self.logger.warning(f"  [{i+1}/{total}] {name[:25]} 获取坐标失败: {e}")

                point_data = {
                    "id": f"{local_folder_id}-{i+1}",
                    "name": name,
                    "image": img_url,
                    "ep": ep,
                    "geo": geo
                }
                points.append(point_data)
                self.logger.info(f"  [{i+1}/{total}] {name[:25]} ep={ep} img={'有' if img_url else '无'} geo={geo}")

            except Exception as e:
                self.logger.warning(f"  提取点位 {i+1} 失败: {e}")

        anime_data = {
            "name": anime_title,
            "name_cn": anime_title,
            "cover": cover_image_url,
            "theme_color": theme_color,
            "points": points
        }

        points_path = folder_path / "points.json"
        with open(points_path, 'w', encoding='utf-8') as f:
            json.dump({"points": points}, f, ensure_ascii=False, indent=2)

        self.create_info_json(anime_data, local_folder_id)

        return {
            "local_id": local_folder_id,
            "anime_data": anime_data
        }

    def is_anime_already_in_database(self, anime_title):
        index_path = self.base_dir / 'index.json'
        result = self._check_anime_in_index(index_path, anime_title)
        if result[0]:
            return result

        root_index_path = Path('index.json')
        if root_index_path.exists():
            result = self._check_anime_in_index(root_index_path, anime_title)
            if result[0]:
                return result

        return (False, None)

    def _check_anime_in_index(self, index_path, anime_title):
        if not index_path.exists():
            self.logger.info(f"Index file {index_path} does not exist")
            return (False, None)

        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                index_data = json.load(f)

            self.logger.info(f"Checking if anime '{anime_title}' exists in {index_path} with {len(index_data)} entries")

            def normalize_name(name):
                if not name:
                    return ""
                return re.sub(r'[^\w\s]', '', name).lower().replace(' ', '')

            normalized_anime_title = normalize_name(anime_title)
            self.logger.info(f"Normalized search title: '{normalized_anime_title}'")

            exact_matches = []
            normalized_matches = []
            substring_matches = []

            for local_id, anime_data in index_data.items():
                jp_name = anime_data.get('name', '')
                cn_name = anime_data.get('name_cn', '')

                normalized_jp_name = normalize_name(jp_name)
                normalized_cn_name = normalize_name(cn_name)

                if jp_name == anime_title or cn_name == anime_title:
                    self.logger.info(f"✓ Anime '{anime_title}' exactly matches existing anime in {index_path} with ID {local_id}")
                    self.logger.info(f"  Existing entry: JP='{jp_name}', CN='{cn_name}'")
                    exact_matches.append((local_id, jp_name, cn_name, 100))

                elif (normalized_jp_name and normalized_jp_name == normalized_anime_title) or \
                     (normalized_cn_name and normalized_cn_name == normalized_anime_title):
                    self.logger.info(f"✓ Anime '{anime_title}' matches existing anime after normalization in {index_path} with ID {local_id}")
                    self.logger.info(f"  Existing entry: JP='{jp_name}', CN='{cn_name}'")
                    self.logger.info(f"  Normalized: JP='{normalized_jp_name}', CN='{normalized_cn_name}'")
                    normalized_matches.append((local_id, jp_name, cn_name, 90))

                else:
                    min_length = 5

                    if normalized_anime_title and len(normalized_anime_title) >= min_length:
                        match_score = 0
                        match_type = ""

                        if normalized_jp_name and normalized_jp_name.startswith(normalized_anime_title) and len(normalized_anime_title) >= min_length:
                            score = 95 - (len(normalized_jp_name) - len(normalized_anime_title)) * 0.1
                            if score > match_score:
                                match_score = score
                                match_type = "JP starts with search (prefix match)"

                        if normalized_cn_name and normalized_cn_name.startswith(normalized_anime_title) and len(normalized_anime_title) >= min_length:
                            score = 95 - (len(normalized_cn_name) - len(normalized_anime_title)) * 0.1
                            if score > match_score:
                                match_score = score
                                match_type = "CN starts with search (prefix match)"

                        if normalized_jp_name and normalized_anime_title in normalized_jp_name and not normalized_jp_name.startswith(normalized_anime_title) and len(normalized_jp_name) >= min_length:
                            score = (len(normalized_anime_title) / len(normalized_jp_name)) * 80
                            if score > match_score:
                                match_score = score
                                match_type = "JP contains search"

                        if normalized_cn_name and normalized_anime_title in normalized_cn_name and not normalized_cn_name.startswith(normalized_anime_title) and len(normalized_cn_name) >= min_length:
                            score = (len(normalized_anime_title) / len(normalized_cn_name)) * 80
                            if score > match_score:
                                match_score = score
                                match_type = "CN contains search"

                        if normalized_jp_name and normalized_jp_name in normalized_anime_title and len(normalized_jp_name) >= min_length:
                            score = (len(normalized_jp_name) / len(normalized_anime_title)) * 70
                            if score > match_score:
                                match_score = score
                                match_type = "Search contains JP"

                        if normalized_cn_name and normalized_cn_name in normalized_anime_title and len(normalized_cn_name) >= min_length:
                            score = (len(normalized_cn_name) / len(normalized_anime_title)) * 70
                            if score > match_score:
                                match_score = score
                                match_type = "Search contains CN"

                        if match_score > 0:
                            self.logger.info(f"✓ Substring match ({match_type}) for '{anime_title}' with ID {local_id}, score: {match_score:.1f}")
                            self.logger.info(f"  Existing entry: JP='{jp_name}', CN='{cn_name}'")
                            substring_matches.append((local_id, jp_name, cn_name, match_score))

                if (jp_name and anime_title in jp_name) or (cn_name and anime_title in cn_name):
                    self.logger.info(f"  Near match found but not exact: ID={local_id}, JP='{jp_name}', CN='{cn_name}'")
                if (jp_name and jp_name in anime_title) or (cn_name and cn_name in anime_title):
                    self.logger.info(f"  Reverse near match found but not exact: ID={local_id}, JP='{jp_name}', CN='{cn_name}'")

            if exact_matches:
                best_match = exact_matches[0]
                return (True, best_match[0])
            elif normalized_matches:
                best_match = normalized_matches[0]
                return (True, best_match[0])
            elif substring_matches:
                best_match = max(substring_matches, key=lambda x: x[3])
                self.logger.info(f"Best substring match for '{anime_title}': ID={best_match[0]}, score: {best_match[3]:.1f}")
                return (True, best_match[0])

            self.logger.info(f"✗ Anime '{anime_title}' not found in {index_path}")
            return (False, None)
        except Exception as e:
            self.logger.error(f"Error checking {index_path}: {e}")
            return (False, None)

    def update_existing_anime(self, anime_info, local_id):
        self.logger.info(f"更新现有动漫: {anime_info['title']}，ID为 {local_id}")

        folder_path = self.base_dir / str(local_id)
        points_path = folder_path / "points.json"
        info_path = folder_path / "info.json"
        images_folder = folder_path / "images"

        if not folder_path.exists():
            self.logger.error(f"Folder for anime ID {local_id} does not exist at {folder_path}")
            return None

        existing_points = []
        try:
            if points_path.exists():
                with open(points_path, 'r', encoding='utf-8') as f:
                    points_data = json.load(f)
                    existing_points = points_data.get("points", [])
                    self.logger.info(f"从 {points_path} 加载了 {len(existing_points)} 个现有点位")
        except Exception as e:
            self.logger.error(f"Error loading existing points data: {e}")
            return None

        existing_info = {}
        try:
            if info_path.exists():
                with open(info_path, 'r', encoding='utf-8') as f:
                    existing_info = json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading existing info data: {e}")
            return None

        self.driver.get(anime_info['link'])
        time.sleep(8)

        try:
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
            )
        except:
            self.logger.error(f"Timeout waiting for anime page to load: {anime_info['link']}")
            return None

        anime_title = anime_info['title']

        self._scroll_detail_page()

        point_elements = self._find_point_elements()
        total = len(point_elements)
        self.logger.info(f"共 {total} 个点位，检查新增...")

        existing_coords = set()
        for point in existing_points:
            if "geo" in point and len(point["geo"]) == 2:
                lat = round(point["geo"][0], 5)
                lng = round(point["geo"][1], 5)
                existing_coords.add((lat, lng))

        threshold = 0.0001

        def is_new_coord(lat, lng):
            lat_r = round(lat, 5)
            lng_r = round(lng, 5)
            if (lat_r, lng_r) in existing_coords:
                return False
            for elat, elng in existing_coords:
                if abs(lat_r - elat) < threshold and abs(lng_r - elng) < threshold:
                    return False
            return True

        new_points = []
        for i, card in enumerate(point_elements):
            try:
                img_src, name, ep = self._extract_card_info(card)

                geo = [0, 0]
                try:
                    card.click()
                    time.sleep(2)

                    maps_link = self._get_google_maps_link_from_detail()
                    if maps_link:
                        lat, lng = self._resolve_and_extract_coords(maps_link)
                        if lat != 0 or lng != 0:
                            geo = [lat, lng]
                except Exception as e:
                    self.logger.warning(f"  点位 {i+1} 获取坐标失败: {e}")

                if geo[0] == 0 and geo[1] == 0:
                    continue

                if not is_new_coord(geo[0], geo[1]):
                    continue

                existing_coords.add((round(geo[0], 5), round(geo[1], 5)))

                img_url = ""
                if img_src:
                    img_idx = len(existing_points) + len(new_points) + 1
                    img_path = f"{images_folder}/{local_id}-{img_idx}.jpg"
                    if self.download_image(img_src, img_path):
                        img_url = f"https://image.xinu.ink/pic/data/{local_id}/images/{local_id}-{img_idx}.jpg"

                point_data = {
                    "id": f"{local_id}-{len(existing_points) + len(new_points) + 1}",
                    "name": name,
                    "image": img_url,
                    "ep": ep,
                    "geo": geo
                }
                new_points.append(point_data)
                self.logger.info(f"  新点位: {name[:25]} ({geo[0]:.4f}, {geo[1]:.4f})")

            except Exception as e:
                self.logger.warning(f"  提取点位 {i+1} 失败: {e}")

        self.logger.info(f"找到 {len(new_points)} 个新点位")

        if not new_points:
            self.logger.info("此动漫未找到新点位")
            return None

        combined_points = existing_points + new_points
        self.logger.info(f"合并了 {len(existing_points)} 个现有点位和 {len(new_points)} 个新点位")

        with open(points_path, 'w', encoding='utf-8') as f:
            json.dump({"points": combined_points}, f, ensure_ascii=False, indent=2)

        updated_info = existing_info.copy()
        updated_info["pointsLength"] = len(combined_points)
        updated_info["cn"] = anime_title

        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(updated_info, f, ensure_ascii=False, indent=2)

        anime_data = {
            "name": anime_title,
            "name_cn": anime_title,
            "cover": existing_info.get("cover", ""),
            "theme_color": existing_info.get("theme_color", "#7f6a95"),
            "points": combined_points
        }

        return {
            "local_id": local_id,
            "anime_data": anime_data,
            "new_points_count": len(new_points)
        }

    def update_index_json(self, anime_data_list, update_mode=False):
        index_path = self.base_dir / "index.json"

        if os.path.exists(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
        else:
            index_data = {}

        new_entries = 0
        updated_entries = 0

        for anime_data in anime_data_list:
            local_id = str(anime_data["local_id"])
            is_update = local_id in index_data and update_mode

            formatted_points = []
            for point in anime_data["anime_data"]["points"]:
                if "id" not in point or point["id"].startswith(local_id):
                    timestamp = int(time.time() * 1000)
                    random_chars = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=8))
                    point_id = f"{random_chars}"
                else:
                    point_id = point["id"]

                formatted_point = {
                    "id": point_id,
                    "name": point["name"],
                    "image": point["image"],
                    "ep": point["ep"],
                    "geo": point["geo"]
                }

                if "cn" in point:
                    formatted_point["cn"] = point["cn"]
                if "s" in point:
                    formatted_point["s"] = point["s"]

                formatted_points.append(formatted_point)

            if is_update:
                self.logger.info(f"Updating existing entry for anime ID {local_id} in index.json")
                index_data[local_id]["points"] = formatted_points
                index_data[local_id]["name"] = anime_data["anime_data"]["name"]
                index_data[local_id]["name_cn"] = anime_data["anime_data"]["name_cn"]
                if anime_data["anime_data"]["cover"]:
                    index_data[local_id]["cover"] = anime_data["anime_data"]["cover"]
                if anime_data["anime_data"].get("theme_color"):
                    index_data[local_id]["theme_color"] = anime_data["anime_data"]["theme_color"]
                updated_entries += 1
            else:
                index_data[local_id] = {
                    "name": anime_data["anime_data"]["name"],
                    "name_cn": anime_data["anime_data"]["name_cn"],
                    "cover": anime_data["anime_data"]["cover"],
                    "theme_color": anime_data["anime_data"].get("theme_color", "#7f6a95"),
                    "points": formatted_points,
                    "inform": f"https://image.xinu.ink/pic/data/{local_id}/points.json"
                }
                new_entries += 1

        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        root_index_path = Path("index.json")
        with open(root_index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        if update_mode:
            self.logger.info(f"Updated index.json files with {new_entries} new and {updated_entries} updated anime entries")
        else:
            self.logger.info(f"Updated index.json files with {len(anime_data_list)} new anime entries")

    def get_anime_list_with_manual_control(self):
        self.logger.info("Fetching anime list from recently updated page...")
        self.driver.get(self.recently_updated_url)

        primary_selector = "div.container__poster"
        fallback_selectors = [
            "a[href*='/maps/anime/']",
            "div.poster__inner",
            "h3.poster__title",
        ]

        found = False
        for selector in [primary_selector] + fallback_selectors:
            try:
                WebDriverWait(self.driver, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                found = True
                break
            except TimeoutException:
                continue

        if not found:
            self.logger.error("Could not find anime list elements. The website structure might have changed.")
            with open("page_source.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            return []

        print("\nManual scrolling mode activated.")
        print("Instructions:")
        print("1. Type 'scroll' to scroll down and load more content")
        print("2. Type 'done' when you've loaded all anime")
        print("3. Type 'extract' to extract the current anime list without further scrolling")

        while True:
            command = input("\nEnter command (scroll/done/extract): ").strip().lower()

            if command == "scroll":
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)

                new_height = self.driver.execute_script("return document.body.scrollHeight")
                print(f"Scrolled to {new_height}px")

                if STOP_ANIME_TITLE in self.driver.page_source:
                    print(f"Found stop anime: {STOP_ANIME_TITLE}")

                anime_items = self.driver.find_elements(By.CSS_SELECTOR, "div.container__poster")
                print(f"Currently visible anime items: {len(anime_items)}")

            elif command == "done" or command == "extract":
                break
            else:
                print("Invalid command. Please try again.")

        anime_list = []
        anime_items = self.driver.find_elements(By.CSS_SELECTOR, "div.container__poster")
        self.logger.info(f"Found {len(anime_items)} anime items")

        for i, item in enumerate(anime_items, 1):
            try:
                title = ""
                link = ""

                try:
                    link_elem = item.find_element(By.CSS_SELECTOR, "a[href*='/maps/anime/']")
                    link = link_elem.get_attribute("href") or ""
                    title = link_elem.get_attribute("title") or ""
                except:
                    pass

                if not title:
                    try:
                        title = item.find_element(By.CSS_SELECTOR, "h3.poster__title").text.strip()
                    except:
                        try:
                            title = item.find_element(By.CSS_SELECTOR, "h3").text.strip()
                        except:
                            pass

                if not link:
                    try:
                        a_elem = item.find_element(By.TAG_NAME, "a")
                        link = a_elem.get_attribute("href") or ""
                    except:
                        pass

                if link and not link.startswith("http"):
                    link = f"https://www.animepilgrimage.com{link}"

                if not title and link:
                    parts = link.rstrip("/").split("/")
                    title = parts[-1].replace("-", " ").title() if parts else "Unknown"

                if title and link:
                    anime_list.append({"title": title, "link": link})
                    print(f"{i}. {title}")
            except Exception as e:
                print(f"Error extracting anime item {i}: {e}")

        return anime_list

    def run(self, auto_mode=False, max_anime=5, wait_time=1800, max_wait_attempts=3):
        try:
            if not auto_mode:
                if self.is_process_running():
                    self.logger.warning("Another instance of the anime pilgrimage scraper is already running")
                    return False

                wait_attempts = 0
                while self.is_monthly_updater_running() and wait_attempts < max_wait_attempts:
                    wait_attempts += 1
                    self.logger.warning(f"Monthly updater is running. Waiting {wait_time/60} minutes (attempt {wait_attempts}/{max_wait_attempts})")
                    time.sleep(wait_time)

                    if wait_attempts == max_wait_attempts:
                        self.logger.warning("Maximum wait attempts reached. Delaying for 12 hours.")
                        time.sleep(43200)

                        if self.is_monthly_updater_running():
                            self.logger.error("Monthly updater is still running after 12 hours. Exiting.")
                            return False

                if not self.create_lock_file():
                    self.logger.error("Failed to create lock file. Exiting.")
                    return False

            self.logger.info("Starting anime pilgrimage scraper")

            try:
                self.logger.info("Running extract_apiid.py to refresh apiid.json")
                import extract_apiid
                extract_apiid.extract_apiid(base_dir='pic/data')
                self.logger.info("Successfully refreshed apiid.json")
            except Exception as e:
                self.logger.error(f"Error refreshing apiid.json: {e}")

            try:
                if auto_mode:
                    self.logger.info("Running in automatic mode")
                    anime_list = self.get_anime_list()
                else:
                    print("\nChoose how to get the anime list:")
                    print("1. Automatic scrolling (may not get all anime)")
                    print("2. Manual control (recommended for getting all anime)")
                    mode = input("Enter your choice (1/2): ").strip()

                    if mode == "2":
                        anime_list = self.get_anime_list_with_manual_control()
                    else:
                        anime_list = self.get_anime_list()

                if not anime_list:
                    self.logger.warning("No anime found. Exiting.")
                    return False

                if auto_mode:
                    start_idx = 1
                    end_idx = min(max_anime, len(anime_list))
                    self.logger.info(f"Auto mode: Scraping anime {start_idx} to {end_idx} out of {len(anime_list)}")
                else:
                    start_idx = int(input("\nEnter the starting anime number to scrape: "))
                    end_idx = int(input("Enter the ending anime number to scrape: "))

                    if start_idx < 1 or end_idx > len(anime_list) or start_idx > end_idx:
                        print("Invalid range. Exiting.")
                        return False

                if auto_mode:
                    local_folder_id = self.get_next_available_local_id()
                    self.logger.info(f"Auto mode: Using local folder ID {local_folder_id}")
                else:
                    local_folder_id = int(input("Enter the starting local folder ID: "))

                if not auto_mode:
                    print("Automatic mode enabled for point extraction. Points will be extracted without manual intervention.")

                anime_data_list = []
                updated_anime = []
                new_anime = []

                for i in range(start_idx - 1, end_idx):
                    anime_info = anime_list[i]
                    self.logger.info(f"[{i+1}/{end_idx}] 检查动漫: {anime_info['title']}")

                    exists, existing_id = self.is_anime_already_in_database(anime_info['title'])
                    if exists:
                        self.logger.info(f"动漫 '{anime_info['title']}' 已存在，ID为 {existing_id}，检查更新")
                        updated_data = self.update_existing_anime(anime_info, existing_id)
                        if updated_data:
                            new_points_count = updated_data.get('new_points_count', 0)
                            self.logger.info(f"更新动漫 '{anime_info['title']}'，添加了 {new_points_count} 个新点位")
                            anime_data_list.append(updated_data)

                            latest_point = None
                            if updated_data['anime_data']['points'] and len(updated_data['anime_data']['points']) > 0:
                                latest_point = updated_data['anime_data']['points'][-1]

                            updated_anime.append({
                                'name': anime_info['title'],
                                'id': existing_id,
                                'new_points': new_points_count,
                                'latest_point': latest_point
                            })

                            self.logger.info("保存更新到 index.json...")
                            self.update_index_json([updated_data], update_mode=True)
                            self.logger.info("更新已保存。")
                        else:
                            self.logger.info(f"动漫 '{anime_info['title']}' 未找到更新")
                        continue

                    self.logger.info(f"抓取动漫: {anime_info['title']}")
                    anime_data = self.scrape_anime(anime_info, local_folder_id, False)

                    if anime_data:
                        anime_data_list.append(anime_data)

                        new_anime.append({
                            'name': anime_info['title'],
                            'id': local_folder_id,
                            'points': anime_data['anime_data']['points']
                        })

                        self.logger.info("Saving progress to index.json...")
                        self.update_index_json([anime_data])
                        self.logger.info("Progress saved.")

                    local_folder_id += 1

                if not anime_data_list:
                    self.logger.warning("未收集到动漫数据。未找到新动漫或更新。")
                    return 2
                else:
                    self.logger.info(f"成功抓取了 {len(anime_data_list)} 部动漫。")

                    if auto_mode:
                        result_data = {
                            'updated_anime': updated_anime,
                            'new_anime': new_anime
                        }
                        self.logger.info(f"返回详细更新信息: {len(updated_anime)} 部更新动漫, {len(new_anime)} 部新动漫")
                        return result_data
                    else:
                        self.logger.info("抓取成功完成！")
                        return True

            finally:
                if not auto_mode:
                    self.remove_lock_file()
                self.driver.quit()

        except Exception as e:
            self.logger.error(f"Error running scraper: {e}")
            if not auto_mode:
                self.remove_lock_file()
            return False


def main():
    parser = argparse.ArgumentParser(description='Anime Pilgrimage Scraper')
    parser.add_argument('--auto', action='store_true', help='Run in automatic mode without user interaction')
    parser.add_argument('--max-anime', type=int, default=5, help='Maximum number of anime to scrape in auto mode')
    parser.add_argument('--wait-time', type=int, default=1800, help='Time to wait in seconds if another process is running')
    parser.add_argument('--max-wait-attempts', type=int, default=3, help='Maximum number of times to wait before giving up')
    parser.add_argument('--headless', action='store_true', default=True, help='Run Chrome in headless mode')
    parser.add_argument('--base-dir', type=str, default=BASE_DIR, help='Base directory for anime data')

    args = parser.parse_args()

    scraper = AnimePilgrimageScraper(
        base_dir=args.base_dir,
        headless=args.headless,
        auto_mode=args.auto
    )

    success = scraper.run(
        auto_mode=args.auto,
        max_anime=args.max_anime,
        wait_time=args.wait_time,
        max_wait_attempts=args.max_wait_attempts
    )

    if success is True or success == 2:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()

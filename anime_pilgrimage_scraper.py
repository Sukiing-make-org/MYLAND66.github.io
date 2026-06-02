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
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from PIL import Image
import colorsys

LOCK_FILE = "anime_pilgrimage_scraper.lock"
BASE_DIR = "pic/data"

STOP_ANIME_TITLE = "劇場版 ソードアート・オンライン オーディナル・スケール"


class AnimePilgrimageScraper:
    def __init__(self, base_dir=BASE_DIR, headless=True, auto_mode=True):
        self.logger = self.setup_logging()

        self.base_url = "https://www.animepilgrimage.com/ja"
        self.recently_updated_url = f"{self.base_url}/maps/recently-updated"
        self.headers = {
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

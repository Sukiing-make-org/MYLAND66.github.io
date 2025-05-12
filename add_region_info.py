#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import requests
import logging
import argparse
import urllib.parse
import threading
import queue
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('region_info_updater.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

class RegionInfoUpdater:
    def __init__(self, base_dir='pic/data', bark_url=None, rate_limit=1, max_workers=5, max_api_failures=10, max_runtime_hours=3):
        """Initialize the RegionInfoUpdater

        Args:
            base_dir: Base directory containing anime data
            bark_url: URL for Bark notifications
            rate_limit: Minimum seconds between API requests to avoid rate limiting
            max_workers: Maximum number of worker threads for concurrent processing
            max_api_failures: Maximum number of consecutive API failures before assuming API limit reached
            max_runtime_hours: Maximum runtime in hours before saving progress and stopping
        """
        self.base_dir = Path(base_dir)
        self.bark_url = bark_url
        self.rate_limit = rate_limit
        self.max_workers = max_workers
        self.max_api_failures = max_api_failures
        self.max_runtime_hours = max_runtime_hours
        self.start_time = datetime.now()
        self.time_limit = self.start_time + timedelta(hours=max_runtime_hours)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'AnimeRegionUpdater/1.0 (anime-pilgrimage-database; contact@example.com)',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://github.com/'
        })
        self.request_lock = threading.Lock()  # Lock for thread-safe API requests
        self.last_request_time = 0
        self.region_cache = {}  # Cache to avoid duplicate requests for the same coordinates
        self.cache_lock = threading.Lock()  # Lock for thread-safe cache access
        self.api_failure_count = 0  # Counter for consecutive API failures
        self.api_failure_lock = threading.Lock()  # Lock for thread-safe failure count updates
        self.api_limit_reached = False  # Flag to indicate if API limit has been reached
        self.time_limit_reached = False  # Flag to indicate if time limit has been reached

    def load_index_json(self):
        """Load the index.json file

        Returns:
            dict: The index data
        """
        index_path = self.base_dir / "index.json"
        if not index_path.exists():
            logger.error(f"索引文件不存在: {index_path}")
            return {}

        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载索引文件失败: {e}")
            return {}

    def save_index_json(self, index_data):
        """Save the index.json file

        Args:
            index_data: The index data to save
        """
        index_path = self.base_dir / "index.json"
        try:
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, ensure_ascii=False, indent=2)

            # Also save to root directory
            root_index_path = Path("index.json")
            with open(root_index_path, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, ensure_ascii=False, indent=2)

            logger.info("索引文件已保存")
        except Exception as e:
            logger.error(f"保存索引文件失败: {e}")

    def check_time_limit(self):
        """Check if the time limit has been reached

        Returns:
            bool: True if time limit has been reached, False otherwise
        """
        current_time = datetime.now()
        if current_time >= self.time_limit:
            if not self.time_limit_reached:
                logger.warning(f"已达到最大运行时间 {self.max_runtime_hours} 小时，将保存当前进度并停止")
                self.time_limit_reached = True
            return True
        return False

    def get_region_from_coordinates(self, lat, lon):
        """Get region information from coordinates using OpenStreetMap API

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            str: Region name if found, None otherwise
            None with api_limit_reached=True if API limit is reached
        """
        # Check if API limit or time limit has been reached
        if self.api_limit_reached or self.check_time_limit():
            return None

        # Check cache first (thread-safe)
        cache_key = f"{lat},{lon}"
        with self.cache_lock:
            if cache_key in self.region_cache:
                # Reset failure count on successful cache hit
                with self.api_failure_lock:
                    self.api_failure_count = 0
                return self.region_cache[cache_key]

        # Rate limiting (thread-safe)
        with self.request_lock:
            current_time = time.time()
            time_since_last_request = current_time - self.last_request_time
            if time_since_last_request < self.rate_limit:
                time.sleep(self.rate_limit - time_since_last_request)
            self.last_request_time = time.time()

        url = f"https://nominatim.openstreetmap.org/reverse?format=geocodejson&lat={lat}&lon={lon}&addressdetails=1&accept-language=zh&zoom=8&limit=1"

        # Retry mechanism
        max_retries = 3
        retry_delay = 2  # Initial delay in seconds

        for retry in range(max_retries):
            try:
                response = self.session.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()

                # Reset failure count on successful API call
                with self.api_failure_lock:
                    self.api_failure_count = 0

                # Extract city name from the response
                if 'features' in data and len(data['features']) > 0:
                    properties = data['features'][0].get('properties', {})
                    geocoding = properties.get('geocoding', {})

                    # Try to get the city name from different fields
                    city = None

                    # First try the 'name' field which is often the city
                    if 'name' in geocoding:
                        city = geocoding['name']

                    # If not found, try to get from admin levels
                    if not city and 'admin' in geocoding:
                        admin = geocoding['admin']
                        # Try different admin levels, prioritizing city-level admin
                        for level in ['level4', 'level6', 'level8', 'level5', 'level7', 'level3']:
                            if level in admin and admin[level]:
                                city = admin[level]
                                break

                    # If still not found, use the label
                    if not city and 'label' in geocoding:
                        # Extract the first part of the label which is often the city
                        label_parts = geocoding['label'].split(',')
                        if label_parts:
                            city = label_parts[0].strip()

                    if city:
                        # Cache the result (thread-safe)
                        with self.cache_lock:
                            self.region_cache[cache_key] = city
                        return city

                logger.warning(f"无法从坐标 {lat}, {lon} 获取地区信息")
                return None

            except requests.exceptions.RequestException as e:
                # Increment failure count
                with self.api_failure_lock:
                    self.api_failure_count += 1
                    current_failures = self.api_failure_count

                # Check if we've reached the failure threshold
                if current_failures >= self.max_api_failures:
                    logger.error(f"连续 {current_failures} 次API请求失败，可能已达到API限制，暂停处理")
                    self.api_limit_reached = True
                    return None

                if retry < max_retries - 1:
                    logger.warning(f"获取坐标 {lat}, {lon} 的地区信息失败，正在重试 ({retry+1}/{max_retries}): {e}")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error(f"获取坐标 {lat}, {lon} 的地区信息失败，已达到最大重试次数: {e}")
                    return None

            except Exception as e:
                logger.error(f"获取坐标 {lat}, {lon} 的地区信息时出错: {e}")
                # Increment failure count for any error
                with self.api_failure_lock:
                    self.api_failure_count += 1
                    current_failures = self.api_failure_count

                # Check if we've reached the failure threshold
                if current_failures >= self.max_api_failures:
                    logger.error(f"连续 {current_failures} 次API请求失败，可能已达到API限制，暂停处理")
                    self.api_limit_reached = True
                return None

    def update_anime_region_info(self, local_id, anime_data):
        """Update region information for an anime

        Args:
            local_id: Local ID of the anime
            anime_data: Anime data from index.json

        Returns:
            list: List of regions found for this anime
        """
        logger.info(f"处理动漫 {local_id}: {anime_data.get('name', '')}")

        # Check if the anime already has region information
        if 'region' in anime_data and anime_data['region']:
            logger.info(f"动漫 {local_id} 已有地区信息: {anime_data['region']}")
            return anime_data['region']

        # Get points data
        points_data = anime_data.get('points', [])
        if not points_data:
            logger.warning(f"动漫 {local_id} 没有巡礼点数据")
            return []

        # Handle different points data formats
        points = []
        if isinstance(points_data, dict) and 'points' in points_data:
            # Format: {"points": [{...}, {...}]}
            points = points_data['points']
        elif isinstance(points_data, list):
            # Format: [{...}, {...}]
            points = points_data

        if not points:
            logger.warning(f"动漫 {local_id} 没有有效的巡礼点数据")
            return []

        # Process each point to get region information
        regions = []
        region_count = defaultdict(int)

        for point in points:
            # Handle different point formats
            if isinstance(point, dict):
                geo = point.get('geo')
                if not geo or len(geo) < 2:
                    continue
                lat, lon = geo
            else:
                # Skip if point is not a dictionary
                logger.warning(f"跳过无效的巡礼点数据: {point}")
                continue

            region = self.get_region_from_coordinates(lat, lon)

            if region:
                region_count[region] += 1
                if region not in regions:
                    regions.append(region)

        if not regions:
            logger.warning(f"动漫 {local_id} 未找到任何地区信息")
            return []

        # Sort regions by frequency
        sorted_regions = sorted(regions, key=lambda r: region_count[r], reverse=True)

        # Update anime data with region information
        anime_data['region'] = sorted_regions

        # Update info.json if it exists
        info_path = self.base_dir / str(local_id) / "info.json"
        if info_path.exists():
            try:
                with open(info_path, 'r', encoding='utf-8') as f:
                    info_data = json.load(f)

                # Add region information to info.json
                if 'region' not in info_data:
                    info_data['region'] = sorted_regions

                with open(info_path, 'w', encoding='utf-8') as f:
                    json.dump(info_data, f, ensure_ascii=False, indent=2)

                logger.info(f"已更新动漫 {local_id} 的 info.json 文件")
            except Exception as e:
                logger.error(f"更新动漫 {local_id} 的 info.json 文件时出错: {e}")

        logger.info(f"动漫 {local_id} 的地区信息: {sorted_regions}")
        return sorted_regions

    def process_anime(self, local_id, anime_data, force=False, is_from_index=True):
        """Process a single anime to update its region information

        Args:
            local_id: Local ID of the anime
            anime_data: Anime data
            force: Whether to force update
            is_from_index: Whether the anime data is from index.json

        Returns:
            tuple: (local_id, anime_data, regions, success)
        """
        try:
            # If force is True, remove existing region data to force update
            if force and 'region' in anime_data:
                logger.info(f"强制更新动漫 {local_id} 的地区信息")
                anime_data.pop('region')

            # Update region info
            regions = self.update_anime_region_info(local_id, anime_data)

            if regions:
                return (local_id, anime_data, regions, True)
            else:
                return (local_id, anime_data, [], False)
        except Exception as e:
            logger.error(f"处理动漫 {local_id} 时出错: {e}")
            return (local_id, anime_data, [], False)

    def process_folder(self, folder, index_data, force=False):
        """Process a folder that is not in index.json

        Args:
            folder: Folder name
            index_data: Current index data
            force: Whether to force update

        Returns:
            tuple: (folder, temp_anime_data, regions, success)
        """
        try:
            logger.info(f"发现未在索引中的动漫文件夹: {folder}")

            # Check if the folder has info.json and points.json
            info_path = self.base_dir / folder / "info.json"
            points_path = self.base_dir / folder / "points.json"

            if info_path.exists() and points_path.exists():
                # Load info.json
                with open(info_path, 'r', encoding='utf-8') as f:
                    info_data = json.load(f)

                # Check if info.json already has region data
                if force or 'region' not in info_data or not info_data['region']:
                    # Load points.json
                    with open(points_path, 'r', encoding='utf-8') as f:
                        points_data = json.load(f)

                    # Create temporary anime data for processing
                    temp_anime_data = {
                        "name": info_data.get('title', ''),
                        "name_cn": info_data.get('cn', ''),
                        "points": points_data
                    }

                    # Update region info
                    regions = self.update_anime_region_info(folder, temp_anime_data)

                    if regions:
                        # Add this anime to index.json
                        logger.info(f"将动漫 {folder} 添加到索引中")
                        if 'region' not in temp_anime_data:
                            temp_anime_data['region'] = regions

                        return (folder, temp_anime_data, regions, True)

            return (folder, None, [], False)
        except Exception as e:
            logger.error(f"处理动漫文件夹 {folder} 时出错: {e}")
            return (folder, None, [], False)

    def update_all_anime_regions(self, force=False):
        """Update region information for all anime without region data

        Args:
            force: If True, update all anime regardless of whether they already have region data

        Returns:
            dict: Summary of updates
        """
        index_data = self.load_index_json()
        if not index_data:
            return {"error": "无法加载索引文件", "updated_count": 0}

        updated_count = 0
        updated_anime = []
        api_limit_reached = False

        # Collect anime to process from index.json
        anime_to_process = []
        for local_id, anime_data in index_data.items():
            if force or 'region' not in anime_data or not anime_data['region']:
                anime_to_process.append((local_id, anime_data))

        # Process anime from index.json concurrently
        results = []
        if anime_to_process:
            logger.info(f"使用 {self.max_workers} 个线程并发处理 {len(anime_to_process)} 个动漫")
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for local_id, anime_data in anime_to_process:
                    future = executor.submit(self.process_anime, local_id, anime_data, force, True)
                    futures.append(future)

                for future in as_completed(futures):
                    # Check if API limit or time limit has been reached
                    if self.api_limit_reached or self.time_limit_reached:
                        # Cancel remaining futures if possible
                        for f in futures:
                            if not f.done():
                                f.cancel()
                        api_limit_reached = self.api_limit_reached
                        time_limit_reached = self.time_limit_reached
                        if self.api_limit_reached:
                            logger.warning("API限制已达到，取消剩余处理任务")
                        if self.time_limit_reached:
                            logger.warning(f"已达到最大运行时间 {self.max_runtime_hours} 小时，取消剩余处理任务")
                        break

                    results.append(future.result())

        # Process results from index.json
        for local_id, anime_data, regions, success in results:
            if success and regions:
                updated_count += 1
                updated_anime.append({
                    "id": local_id,
                    "name": anime_data.get('name', ''),
                    "regions": regions
                })
                # Update the anime data in index.json
                index_data[local_id] = anime_data

        # If neither API limit nor time limit reached, check for anime folders that are not in index.json
        if not api_limit_reached and not self.api_limit_reached and not self.time_limit_reached:
            try:
                folders = [f.name for f in self.base_dir.glob('*') if f.is_dir() and f.name.isdigit()]
                folders_to_process = [folder for folder in folders if folder not in index_data]

                # Process folders concurrently
                folder_results = []
                if folders_to_process:
                    logger.info(f"使用 {self.max_workers} 个线程并发处理 {len(folders_to_process)} 个未索引的动漫文件夹")
                    with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                        futures = []
                        for folder in folders_to_process:
                            future = executor.submit(self.process_folder, folder, index_data, force)
                            futures.append(future)

                        for future in as_completed(futures):
                            # Check if API limit or time limit has been reached
                            if self.api_limit_reached or self.time_limit_reached:
                                # Cancel remaining futures if possible
                                for f in futures:
                                    if not f.done():
                                        f.cancel()
                                api_limit_reached = self.api_limit_reached
                                time_limit_reached = self.time_limit_reached
                                if self.api_limit_reached:
                                    logger.warning("API限制已达到，取消剩余处理任务")
                                if self.time_limit_reached:
                                    logger.warning(f"已达到最大运行时间 {self.max_runtime_hours} 小时，取消剩余处理任务")
                                break

                            folder_results.append(future.result())

                # Process results from folders
                for folder, temp_anime_data, regions, success in folder_results:
                    if success and regions:
                        updated_count += 1
                        updated_anime.append({
                            "id": folder,
                            "name": temp_anime_data.get('name', ''),
                            "regions": regions
                        })
                        # Add this anime to index.json
                        index_data[folder] = temp_anime_data
            except Exception as e:
                logger.error(f"扫描动漫文件夹时出错: {e}")

        # Always save the index.json file to preserve progress, even if API limit or time limit was reached
        if updated_count > 0:
            self.save_index_json(index_data)
            logger.info(f"已更新 {updated_count} 个动漫的地区信息")

            if api_limit_reached or self.api_limit_reached:
                logger.warning("由于API限制，处理被中断，但已保存当前进度")
            if self.time_limit_reached:
                logger.warning(f"由于达到最大运行时间 {self.max_runtime_hours} 小时，处理被中断，但已保存当前进度")
        else:
            if api_limit_reached or self.api_limit_reached:
                logger.warning("由于API限制，无法处理任何动漫，请稍后再试")
            elif self.time_limit_reached:
                logger.warning(f"由于达到最大运行时间 {self.max_runtime_hours} 小时，无法处理任何动漫")
            else:
                logger.info("没有需要更新地区信息的动漫")

        return {
            "updated_count": updated_count,
            "updated_anime": updated_anime,
            "api_limit_reached": api_limit_reached or self.api_limit_reached,
            "time_limit_reached": self.time_limit_reached
        }

    def send_bark_notification(self, title, content):
        """Send a notification via Bark

        Args:
            title: Notification title
            content: Notification content
        """
        if not self.bark_url:
            logger.info("未配置Bark URL，跳过通知")
            return

        try:
            # URL encode the title and content
            encoded_title = urllib.parse.quote(title)
            encoded_content = urllib.parse.quote(content)

            # Add emoji to make the notification more readable
            notification_url = f"{self.bark_url}/{encoded_title}/{encoded_content}?icon=https://image.xinu.ink/pic/data/images/icon.jpg"

            response = self.session.get(notification_url)
            response.raise_for_status()
            logger.info("Bark通知已发送")
        except Exception as e:
            logger.error(f"发送Bark通知失败: {e}")

def main():
    parser = argparse.ArgumentParser(description='更新动漫巡礼点的地区信息')
    parser.add_argument('--base-dir', default='pic/data', help='动漫数据的基础目录')
    parser.add_argument('--bark-url', default=None, help='Bark通知的URL')
    parser.add_argument('--rate-limit', type=float, default=1.0, help='API请求之间的最小间隔（秒）')
    parser.add_argument('--max-workers', type=int, default=5, help='并发处理的最大线程数')
    parser.add_argument('--max-api-failures', type=int, default=10, help='判断API限制的连续失败次数')
    parser.add_argument('--max-runtime-hours', type=float, default=3.0, help='最大运行时间（小时）')
    parser.add_argument('--force', action='store_true', help='强制更新所有动漫的地区信息')

    args = parser.parse_args()

    updater = RegionInfoUpdater(
        base_dir=args.base_dir,
        bark_url=args.bark_url,
        rate_limit=args.rate_limit,
        max_workers=args.max_workers,
        max_api_failures=args.max_api_failures,
        max_runtime_hours=args.max_runtime_hours
    )

    start_time = datetime.now()
    logger.info(f"开始更新动漫地区信息，时间: {start_time}")

    result = updater.update_all_anime_regions(force=args.force)

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    logger.info(f"更新完成，耗时: {duration:.2f}秒")

    # Prepare notification content
    if result["updated_count"] > 0:
        if result.get("api_limit_reached", False) and result.get("time_limit_reached", False):
            title = f"⚠️ 动漫地区信息部分更新"
            content = f"已更新{result['updated_count']}个动漫的地区信息，但由于API限制和时间限制已达到，处理被中断\n\n"
        elif result.get("api_limit_reached", False):
            title = f"⚠️ 动漫地区信息部分更新"
            content = f"已更新{result['updated_count']}个动漫的地区信息，但API限制已达到，处理被中断\n\n"
        elif result.get("time_limit_reached", False):
            title = f"⚠️ 动漫地区信息部分更新"
            content = f"已更新{result['updated_count']}个动漫的地区信息，但已达到最大运行时间 {args.max_runtime_hours} 小时，处理被中断\n\n"
        else:
            title = f"🌍 动漫地区信息更新"
            content = f"已更新{result['updated_count']}个动漫的地区信息\n\n"

        # Add details of updated anime (limit to 10 for readability)
        for i, anime in enumerate(result["updated_anime"][:10]):
            content += f"{i+1}. {anime['name']}: {', '.join(anime['regions'])}\n"

        if len(result["updated_anime"]) > 10:
            content += f"...等共{len(result['updated_anime'])}个动漫"
    else:
        if result.get("api_limit_reached", False) and result.get("time_limit_reached", False):
            title = "⚠️ 动漫地区信息更新失败"
            content = "由于API限制和时间限制已达到，无法处理任何动漫，请稍后再试"
        elif result.get("api_limit_reached", False):
            title = "⚠️ 动漫地区信息更新失败"
            content = "API限制已达到，无法处理任何动漫，请稍后再试"
        elif result.get("time_limit_reached", False):
            title = "⚠️ 动漫地区信息更新中断"
            content = f"已达到最大运行时间 {args.max_runtime_hours} 小时，无法处理任何动漫"
        else:
            title = "🌍 动漫地区信息检查"
            content = "所有动漫已有地区信息，无需更新"

    # Send notification
    updater.send_bark_notification(title, content)

if __name__ == "__main__":
    main()

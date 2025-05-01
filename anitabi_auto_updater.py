import os
import json
import time
import requests
import logging
import datetime
import traceback
from urllib.parse import urlparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys

# Lock file path for process coordination
LOCK_FILE = "anitabi_updater.lock"

class AnitabiAutoUpdater:
    def __init__(self, base_dir='pic/data', concurrency=100, aggressive_matching=False):
        self.base_dir = Path(base_dir)
        self.api_base = 'https://api.anitabi.cn'
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.valid_ids = []
        self.new_anime_count = 0
        self.new_anime_ids = []
        self.updated_anime_count = 0
        self.updated_anime_ids = []
        self.local_id_range = (0, 0)
        self.concurrency = concurrency

        # Set matching options
        self.aggressive_matching = aggressive_matching

        # Set up logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler("anitabi_updater.log"),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger("AnitabiUpdater")

        if aggressive_matching:
            self.logger.info("已启用激进匹配模式")

    def ensure_dir(self, path):
        os.makedirs(path, exist_ok=True)
        return path

    def get_next_local_id(self):
        """Find the next available local ID by checking the highest folder number in pic/data"""
        try:
            folders = [int(f.name) for f in self.base_dir.glob('*') if f.is_dir() and f.name.isdigit()]
            if not folders:
                return 1
            return max(folders) + 1
        except Exception as e:
            self.logger.error(f"Error finding next local ID: {e}")
            return 5901  # Default fallback value

    def check_id(self, subject_id):
        """Check if an API ID has valid data"""
        url = f'{self.api_base}/bangumi/{subject_id}/points/detail'
        retry_count = 0
        retry_delay = 1
        max_retries = 3

        while retry_count < max_retries:
            try:
                response = self.session.get(url, params={'haveImage': 'true'}, timeout=10)
                response.raise_for_status()

                # Try to parse the JSON response
                try:
                    data = response.json()
                    if data and len(data) > 0:
                        # Check if this API ID has valid anime info with names
                        anime_info = self.get_bangumi_lite(subject_id)
                        if anime_info:
                            # Get name fields (after mapping in get_bangumi_lite)
                            japanese_name = anime_info.get('name', '')
                            chinese_name = anime_info.get('name_cn', '')

                            if japanese_name or chinese_name:
                                self.logger.info(f'✅ API ID {subject_id} 有效，名称: {japanese_name} / {chinese_name}')
                                return subject_id
                            else:
                                self.logger.warning(f'⚠️ API ID {subject_id} 有数据点但没有有效名称，跳过')
                                return None
                        else:
                            self.logger.warning(f'⚠️ API ID {subject_id} 有数据点但获取动画信息失败，跳过')
                            return None
                    else:
                        self.logger.debug(f'❌ API ID {subject_id} 无效（没有数据）')
                        return None
                except json.JSONDecodeError as je:
                    self.logger.error(f'Failed to parse JSON response for API ID {subject_id}: {je}')
                    retry_count += 1
                    if retry_count >= max_retries:
                        return None
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 10)
                    continue

            except requests.exceptions.RequestException as e:
                retry_count += 1
                if retry_count >= max_retries:
                    self.logger.debug(f'❌ API ID {subject_id} 无效（请求失败）')
                    return None
                self.logger.debug(f'检查 API ID {subject_id} 失败，重试 {retry_count}，错误: {e}')
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 10)

    def scan_api_ids(self, start_id, end_id):
        """Scan a range of API IDs to find valid ones"""
        self.logger.info(f'开始扫描 API ID 范围：{start_id} 到 {end_id}')
        self.logger.info(f'使用 {self.concurrency} 个并发线程进行扫描')

        # Use thread pool for concurrent checking
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            # Submit all tasks
            future_to_id = {executor.submit(self.check_id, id): id
                           for id in range(start_id, end_id + 1)}

            # Collect results
            for future in as_completed(future_to_id):
                result = future.result()
                if result is not None:
                    self.valid_ids.append(result)

        # Sort and save results
        self.valid_ids.sort()

        # Print result report
        self.logger.info(f'扫描完成！找到 {len(self.valid_ids)} 个有效的 API ID')

        # Log some sample valid IDs for debugging
        if self.valid_ids:
            sample_size = min(5, len(self.valid_ids))
            sample_ids = self.valid_ids[:sample_size]
            self.logger.info(f'有效 API ID 示例: {sample_ids}')

        # Save results to file
        result_file = 'valid_api_ids.json'
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump({
                'total_checked': end_id - start_id + 1,
                'total_valid': len(self.valid_ids),
                'valid_ids': self.valid_ids,
                'check_time': time.strftime('%Y-%m-%d %H:%M:%S')
            }, f, ensure_ascii=False, indent=2)
        self.logger.info(f'结果已保存到文件: {result_file}')
        return self.valid_ids

    def download_image(self, url, save_path):
        """Download an image from URL to save_path"""
        if os.path.exists(save_path):
            return

        retry_count = 0
        retry_delay = 1
        max_retries = 5

        while retry_count < max_retries:
            try:
                response = self.session.get(url)
                response.raise_for_status()
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                time.sleep(0.5)  # Avoid too frequent requests
                return
            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    self.logger.error(f'Failed to download image after max retries: {url}, error: {e}')
                    return
                self.logger.debug(f'Failed to download image, retry {retry_count}: {url}, error: {e}')
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    def get_bangumi_lite(self, subject_id):
        """Get basic anime information"""
        url = f'{self.api_base}/bangumi/{subject_id}/lite'
        retry_count = 0
        retry_delay = 1
        max_retries = 5

        while retry_count < max_retries:
            try:
                self.logger.debug(f'Requesting anime info from: {url}')
                response = self.session.get(url)
                response.raise_for_status()

                # Log the raw response text for debugging
                self.logger.debug(f'Raw response text: {response.text[:200]}...')

                # Parse the JSON response
                try:
                    data = response.json()

                    # Validate the response data
                    if not isinstance(data, dict):
                        self.logger.warning(f'API response is not a dictionary: {type(data)}')
                        return None

                    # Check for required fields - handle both field naming conventions
                    # Some APIs return 'name'/'name_cn', others return 'title'/'cn'
                    has_name = 'name' in data or 'title' in data
                    has_name_cn = 'name_cn' in data or 'cn' in data

                    # Map fields if needed
                    if 'title' in data and 'name' not in data:
                        data['name'] = data['title']
                    if 'cn' in data and 'name_cn' not in data:
                        data['name_cn'] = data['cn']
                    # 处理主题色字段 - 只为新番剧添加theme_color字段
                    # 注意：这里只是映射字段，实际上是否使用这个值还取决于save_bangumi和update_existing_anime方法
                    # update_existing_anime方法不会使用这个值，所以已有番剧的主题色不会被修改
                    if 'color' in data and 'theme_color' not in data:
                        data['theme_color'] = data['color']

                    # Log the raw data and mapped fields for debugging
                    self.logger.debug(f'Raw API data: {data}')
                    self.logger.debug(f'Mapped name fields: name="{data.get("name", "")}" name_cn="{data.get("name_cn", "")}"')

                    if not has_name and not has_name_cn:
                        self.logger.warning(f'API response missing both name fields: {data}')

                    return data
                except json.JSONDecodeError as je:
                    self.logger.error(f'Failed to parse JSON response: {je}, response text: {response.text[:200]}...')
                    retry_count += 1
                    if retry_count >= max_retries:
                        return None
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)
                    continue

            except requests.exceptions.RequestException as e:
                retry_count += 1
                if retry_count >= max_retries:
                    self.logger.error(f'Failed to get anime info after max retries: {url}, error: {e}')
                    return None
                self.logger.debug(f'Failed to get anime info, retry {retry_count}: {url}, error: {e}')
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    def get_anime_info(self, api_id):
        """Get anime information for checking duplicates"""
        # Get the anime info from the API
        anime_info = self.get_bangumi_lite(api_id)

        # Log the raw response for debugging
        if anime_info:
            self.logger.debug(f"Raw API response for API ID {api_id}: {anime_info}")

            # Check if name fields are present (after mapping in get_bangumi_lite)
            japanese_name = anime_info.get('name', '')
            chinese_name = anime_info.get('name_cn', '')

            if not japanese_name:
                self.logger.warning(f"API ID {api_id} 响应缺少日文名称字段或为空")
            if not chinese_name:
                self.logger.warning(f"API ID {api_id} 响应缺少中文名称字段或为空")

            # If both name fields are empty, log a more detailed warning
            if not japanese_name and not chinese_name:
                self.logger.warning(f"API ID {api_id} 名称字段为空。完整响应: {anime_info}")
            else:
                self.logger.info(f"API ID {api_id} 的名称: '{japanese_name}' / '{chinese_name}'")
        else:
            self.logger.warning(f"获取 API ID {api_id} 的动画信息失败")

        return anime_info

    def get_bangumi_points(self, subject_id):
        """Get anime landmark points"""
        url = f'{self.api_base}/bangumi/{subject_id}/points/detail'
        retry_count = 0
        retry_delay = 1
        max_retries = 5

        while retry_count < max_retries:
            try:
                response = self.session.get(url, params={'haveImage': 'true'})
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                retry_count += 1
                if retry_count >= max_retries:
                    self.logger.error(f'Failed to get landmark info after max retries: {url}, error: {e}')
                    return None
                self.logger.debug(f'Failed to get landmark info, retry {retry_count}: {url}, error: {e}')
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    def save_bangumi(self, subject_id, local_id, bangumi_data=None):
        """Save anime data to local directory

        Args:
            subject_id: The API ID of the anime
            local_id: The local ID to use for the anime
            bangumi_data: Optional pre-fetched anime info to avoid an extra API call

        Returns:
            bool: True if the anime was saved successfully, False otherwise
        """
        # First check if there's landmark data
        points_data = self.get_bangumi_points(subject_id)
        if not points_data or len(points_data) == 0:
            return False

        # Get basic anime info if not provided
        if not bangumi_data:
            bangumi_data = self.get_bangumi_lite(subject_id)
            if not bangumi_data:
                return False

        # Create anime directory using local ID as folder name
        bangumi_dir = self.ensure_dir(self.base_dir / str(local_id))
        images_dir = self.ensure_dir(bangumi_dir / 'images')

        # Add local ID info
        bangumi_data['local_id'] = local_id

        # Save anime info
        with open(bangumi_dir / 'info.json', 'w', encoding='utf-8') as f:
            json.dump(bangumi_data, f, ensure_ascii=False, indent=2)

        # Download cover image
        if bangumi_data.get('cover'):
            original_url = bangumi_data['cover'].split('?')[0]  # Get original size
            file_name = os.path.basename(urlparse(original_url).path)
            cover_url = f'https://image.xinu.ink/pic/data/{local_id}/images/{file_name}'
            bangumi_data['cover'] = cover_url  # Update cover URL in info.json
            self.download_image(original_url, images_dir / file_name)

            # Re-save updated bangumi_data
            with open(bangumi_dir / 'info.json', 'w', encoding='utf-8') as f:
                json.dump(bangumi_data, f, ensure_ascii=False, indent=2)

        # Get and save landmark details
        points_data = self.get_bangumi_points(subject_id)
        if points_data:
            # Update image URLs for each landmark
            for point in points_data:
                if point.get('image'):
                    original_url = point['image'].split('?')[0]  # Get original size
                    file_name = os.path.basename(urlparse(original_url).path)
                    image_url = f'https://image.xinu.ink/pic/data/{local_id}/images/{file_name}'
                    point['image'] = image_url  # Update image URL in points.json
                    self.download_image(original_url, images_dir / file_name)

            with open(bangumi_dir / 'points.json', 'w', encoding='utf-8') as f:
                json.dump(points_data, f, ensure_ascii=False, indent=2)

        # 注意：不在这里调用generate_index()，而是在批量处理完所有动画后统一更新index.json
        # 这样可以避免重复更新index.json文件，提高效率
        return True

    def ensure_index_exists(self):
        """Make sure index.json exists, create it if it doesn't"""
        index_file = self.base_dir / 'index.json'
        if not index_file.exists():
            self.logger.info(f"index.json not found, creating it")
            # Create an empty index file
            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            self.logger.info(f"Created empty index.json file")
        return index_file

    def generate_index(self):
        """Generate index file for all anime in data directory"""
        # Ensure index.json exists
        self.ensure_index_exists()

        # Load existing index.json if it exists
        index_file = self.base_dir / 'index.json'
        if os.path.exists(index_file):
            try:
                with open(index_file, 'r', encoding='utf-8') as f:
                    index = json.load(f)
                self.logger.info(f"Loaded existing index.json with {len(index)} entries")
            except Exception as e:
                self.logger.error(f"Error loading existing index.json: {e}")
                index = {}
        else:
            index = {}

        # Make a copy of the original index for reference
        original_index = index.copy()

        # Track the number of updated entries
        updated_entries = 0
        processed_folders = 0

        # Traverse all anime folders in data directory
        for bangumi_dir in sorted(self.base_dir.glob('*')):
            if not bangumi_dir.is_dir() or bangumi_dir.name == 'index.json':
                continue

            local_id = bangumi_dir.name
            info_file = bangumi_dir / 'info.json'
            points_file = bangumi_dir / 'points.json'

            if not info_file.exists() or not points_file.exists():
                continue

            processed_folders += 1

            try:
                # Read anime info
                with open(info_file, 'r', encoding='utf-8') as f:
                    info = json.load(f)

                # Read landmark info
                with open(points_file, 'r', encoding='utf-8') as f:
                    points_data = json.load(f)
                    # 处理不同的格式（数组或带points属性的对象）
                    if isinstance(points_data, list):
                        points = points_data
                    elif isinstance(points_data, dict) and 'points' in points_data:
                        points = points_data['points']
                    else:
                        self.logger.warning(f"文件夹 {local_id} 的 points.json 格式不正确，使用原始数据")
                        points = points_data

                # Update cover image URL
                cover_url = info.get('cover', '')
                if cover_url:
                    file_name = os.path.basename(urlparse(cover_url).path)
                    cover_url = f'https://image.xinu.ink/pic/data/{local_id}/images/{file_name}'

                # Get name fields, handling both name/name_cn and title/cn fields
                anime_name = info.get('name', '') or info.get('title', '')
                anime_name_cn = info.get('name_cn', '') or info.get('cn', '')

                # Skip entries with empty names if this is a new entry
                if not anime_name and not anime_name_cn and local_id not in index:
                    self.logger.warning(f"Skipping folder {local_id} because it has empty name fields")
                    continue

                # If entry already exists in index, preserve existing values for fields that are empty in info.json
                if local_id in original_index:
                    existing_entry = original_index[local_id]

                    # Preserve existing name if new one is empty
                    if not anime_name and existing_entry.get('name'):
                        anime_name = existing_entry['name']

                    # Preserve existing name_cn if new one is empty
                    if not anime_name_cn and existing_entry.get('name_cn'):
                        anime_name_cn = existing_entry['name_cn']

                    # Preserve existing cover if new one is empty
                    if not cover_url and existing_entry.get('cover'):
                        cover_url = existing_entry['cover']

                    # Preserve existing theme_color
                    theme_color = info.get('theme_color', '')
                    if not theme_color and existing_entry.get('theme_color'):
                        theme_color = existing_entry['theme_color']
                else:
                    # For new entries, use a default theme_color if none is provided
                    theme_color = info.get('theme_color', '#7f6a95')  # Default theme color

                # Create or update entry in index
                index[local_id] = {
                    'name': anime_name,
                    'name_cn': anime_name_cn,
                    'cover': cover_url,
                    'theme_color': theme_color,
                    'points': points,
                    'inform': f'https://image.xinu.ink/pic/data/{local_id}/points.json'
                }
                updated_entries += 1

                # Log progress periodically
                if processed_folders % 100 == 0:
                    self.logger.info(f"Processed {processed_folders} folders so far...")
            except Exception as e:
                self.logger.error(f"Error processing folder {local_id}: {e}")
                # If there's an error but the entry already exists in the index, preserve it
                if local_id in original_index:
                    self.logger.info(f"Preserving existing entry for {local_id} due to processing error")
                    index[local_id] = original_index[local_id]
                continue

        # Check for empty entries and fix them
        empty_entries = []
        for local_id, entry in index.items():
            if not entry.get('name') and not entry.get('name_cn'):
                empty_entries.append(local_id)

        if empty_entries:
            self.logger.warning(f"Found {len(empty_entries)} entries with empty name fields")
            # Remove empty entries that don't have corresponding folders
            for local_id in empty_entries:
                folder_path = self.base_dir / local_id
                if not folder_path.is_dir():
                    self.logger.warning(f"Removing entry {local_id} because it has empty name fields and no folder")
                    index.pop(local_id, None)
                else:
                    self.logger.warning(f"Entry {local_id} has empty name fields but folder exists, keeping it")

        # Save index file in data directory
        with open(index_file, 'w', encoding='utf-8') as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        # Also save a copy to the root directory
        root_index_file = Path('index.json')
        with open(root_index_file, 'w', encoding='utf-8') as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        self.logger.info(f'Index files generated: {index_file} and {root_index_file}')
        self.logger.info(f'Processed {processed_folders} folders, updated {updated_entries} entries in index.json')

    def load_index_data(self):
        """Load index.json data from both data directory and root directory

        Returns:
            dict: Combined index data from both index.json files
        """
        combined_index = {}

        # Load index.json from data directory
        try:
            index_path = os.path.join(self.base_dir, 'index.json')
            if os.path.exists(index_path):
                with open(index_path, 'r', encoding='utf-8') as f:
                    data_index = json.load(f)
                    self.logger.info(f"Loaded {len(data_index)} entries from data directory index.json")

                    # Log the structure of the first entry
                    if data_index:
                        first_key = next(iter(data_index))
                        first_entry = data_index[first_key]
                        self.logger.info(f"First entry structure: {json.dumps(first_entry, ensure_ascii=False)[:500]}...")

                        # Check if the entry has name/name_cn fields
                        if 'name' in first_entry or 'name_cn' in first_entry or 'title' in first_entry or 'cn' in first_entry:
                            self.logger.info("Entry has name/name_cn/title/cn fields")
                        else:
                            self.logger.warning("Entry does not have name/name_cn/title/cn fields")

                            # Log all keys in the first entry
                            self.logger.warning(f"Keys in first entry: {list(first_entry.keys())}")

                            # If the entry is a nested structure, check for name fields in it
                            for key, value in first_entry.items():
                                if isinstance(value, dict):
                                    self.logger.info(f"Checking nested structure in key '{key}'")
                                    if 'name' in value or 'name_cn' in value or 'title' in value or 'cn' in value:
                                        self.logger.info(f"Found name fields in nested structure under key '{key}'")
                                    else:
                                        self.logger.warning(f"No name fields found in nested structure under key '{key}'")
                                        self.logger.warning(f"Keys in nested structure: {list(value.keys())}")

                    combined_index.update(data_index)
        except Exception as e:
            self.logger.error(f"Error loading data directory index.json: {e}")

        # Load index.json from root directory
        try:
            root_index_path = 'index.json'
            if os.path.exists(root_index_path):
                with open(root_index_path, 'r', encoding='utf-8') as f:
                    root_index = json.load(f)
                    self.logger.info(f"Loaded {len(root_index)} entries from root directory index.json")

                    # Log the structure of the first entry
                    if root_index:
                        first_key = next(iter(root_index))
                        first_entry = root_index[first_key]
                        self.logger.info(f"First entry structure from root index: {json.dumps(first_entry, ensure_ascii=False)[:500]}...")

                        # Check if the entry has name/name_cn fields
                        if 'name' in first_entry or 'name_cn' in first_entry or 'title' in first_entry or 'cn' in first_entry:
                            self.logger.info("Root entry has name/name_cn/title/cn fields")
                        else:
                            self.logger.warning("Root entry does not have name/name_cn/title/cn fields")

                            # Log all keys in the first entry
                            self.logger.warning(f"Keys in first root entry: {list(first_entry.keys())}")

                            # If the entry is a nested structure, check for name fields in it
                            for key, value in first_entry.items():
                                if isinstance(value, dict):
                                    self.logger.info(f"Checking nested structure in root entry key '{key}'")
                                    if 'name' in value or 'name_cn' in value or 'title' in value or 'cn' in value:
                                        self.logger.info(f"Found name fields in nested structure under root entry key '{key}'")
                                    else:
                                        self.logger.warning(f"No name fields found in nested structure under root entry key '{key}'")
                                        self.logger.warning(f"Keys in nested structure: {list(value.keys())}")

                    combined_index.update(root_index)
        except Exception as e:
            self.logger.error(f"Error loading root directory index.json: {e}")

        self.logger.info(f"Combined index.json contains {len(combined_index)} unique entries")
        return combined_index



    def is_anime_already_in_database(self, api_id, anime_info=None):
        """Check if an anime is already in the database by checking index.json

        Args:
            api_id: The API ID of the anime to check
            anime_info: Optional pre-fetched anime info to avoid an extra API call

        Returns:
            bool: True if the anime is already in the database, False otherwise
        """
        # Get the anime name from the API if not provided
        if not anime_info:
            anime_info = self.get_anime_info(api_id)
            if not anime_info:
                return False  # Can't check without anime info

        # Get anime names, handling both name/name_cn and title/cn fields
        anime_name = anime_info.get('name', '') or anime_info.get('title', '')
        anime_name_cn = anime_info.get('name_cn', '') or anime_info.get('cn', '')

        if not anime_name and not anime_name_cn:
            return False  # Can't check without a name

        # Use the most direct approach: read the file as text and search for the names
        return self._check_anime_in_index_direct(anime_name, anime_name_cn)

    def _check_anime_in_index_direct(self, anime_name, anime_name_cn):
        """检查动画是否存在于index.json中

        Args:
            anime_name: 动画的日文名称
            anime_name_cn: 动画的中文名称

        Returns:
            bool: 如果动画存在于index.json中则返回True，否则返回False
        """
        # 如果名称为空，无法检查
        if not anime_name and not anime_name_cn:
            self.logger.warning("无法检查动画是否存在：日文名和中文名都为空")
            return False

        # 记录要搜索的名称
        self.logger.info(f"检查动画 '{anime_name}' / '{anime_name_cn}' 是否已存在于数据库中")

        # 要检查的文件列表
        index_files = [
            os.path.join(self.base_dir, 'index.json'),
            'index.json'
        ]

        # 预处理搜索名称
        def normalize_name(name):
            if not name:
                return ""
            # 移除特殊字符、空格和标点符号
            import re
            # 移除所有非字母数字的字符（保留中日文字符）
            name = re.sub(r'[^\w\s\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf]', '', name)
            # 移除多余空格并转为小写
            return re.sub(r'\s+', '', name).lower()

        normalized_anime_name = normalize_name(anime_name)
        normalized_anime_name_cn = normalize_name(anime_name_cn)

        # 对每个文件进行检查
        for index_path in index_files:
            if not os.path.exists(index_path):
                continue

            try:
                # 读取并解析JSON文件
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)

                self.logger.info(f"正在检查 {index_path} 中的 {len(index_data)} 个条目")

                # 检查每个条目
                for local_id, anime_data in index_data.items():
                    # 获取动画名称（处理不同的字段名）
                    db_name = anime_data.get('name', '') or anime_data.get('title', '')
                    db_name_cn = anime_data.get('name_cn', '') or anime_data.get('cn', '')

                    # 精确匹配检查
                    if (anime_name and db_name and anime_name == db_name) or \
                       (anime_name_cn and db_name_cn and anime_name_cn == db_name_cn):
                        self.logger.info(f"在 {index_path} 中找到精确匹配：'{db_name}' / '{db_name_cn}'")
                        self.logger.info(f"动画 '{anime_name}' / '{anime_name_cn}' 已存在于 {index_path} 中，ID为 {local_id}")
                        return True

                    # 标准化数据库中的名称
                    normalized_db_name = normalize_name(db_name)
                    normalized_db_name_cn = normalize_name(db_name_cn)

                    # 标准化匹配检查
                    if (normalized_anime_name and normalized_db_name and normalized_anime_name == normalized_db_name) or \
                       (normalized_anime_name_cn and normalized_db_name_cn and normalized_anime_name_cn == normalized_db_name_cn):
                        self.logger.info(f"在 {index_path} 中找到标准化匹配：'{db_name}' / '{db_name_cn}'")
                        self.logger.info(f"动画 '{anime_name}' / '{anime_name_cn}' 已存在于 {index_path} 中，ID为 {local_id}")
                        return True

                    # 如果启用了激进匹配模式，尝试部分匹配
                    if self.aggressive_matching:
                        # 检查一个名称是否包含另一个名称（双向检查）
                        if (normalized_anime_name and normalized_db_name and
                            (normalized_anime_name in normalized_db_name or normalized_db_name in normalized_anime_name)):
                            self.logger.info(f"在 {index_path} 中找到部分匹配：'{db_name}'")
                            self.logger.info(f"动画 '{anime_name}' 已存在于 {index_path} 中，ID为 {local_id}")
                            return True

                        if (normalized_anime_name_cn and normalized_db_name_cn and
                            (normalized_anime_name_cn in normalized_db_name_cn or normalized_db_name_cn in normalized_anime_name_cn)):
                            self.logger.info(f"在 {index_path} 中找到部分匹配：'{db_name_cn}'")
                            self.logger.info(f"动画 '{anime_name_cn}' 已存在于 {index_path} 中，ID为 {local_id}")
                            return True

            except Exception as e:
                self.logger.error(f"检查 {index_path} 时出错: {e}")
                # 打印更详细的错误信息
                import traceback
                self.logger.error(traceback.format_exc())

        # 如果到这里，说明在所有文件中都没有找到匹配
        self.logger.info(f"未在任何 index.json 文件中找到动画 '{anime_name}' / '{anime_name_cn}'")
        return False

    def _check_index_json_files(self):
        """检查 index.json 文件是否存在，以及它的大小"""
        index_files = [
            os.path.join(self.base_dir, 'index.json'),
            'index.json'
        ]

        for index_path in index_files:
            if os.path.exists(index_path):
                file_size = os.path.getsize(index_path)
                self.logger.warning(f"找到 index.json 文件: {index_path}, 大小: {file_size} 字节")

                # 读取文件的前100个字符，看看它的格式
                try:
                    with open(index_path, 'r', encoding='utf-8') as f:
                        content = f.read(100)
                    self.logger.warning(f"index.json 文件的前100个字符: {content}")

                    # 尝试解析JSON并检查结构
                    try:
                        with open(index_path, 'r', encoding='utf-8') as f:
                            index_data = json.load(f)
                        self.logger.warning(f"成功解析 {index_path}，包含 {len(index_data)} 个条目")

                        # 检查前3个条目的结构
                        if index_data:
                            sample_size = min(3, len(index_data))
                            sample_keys = list(index_data.keys())[:sample_size]
                            self.logger.warning(f"检查 {sample_size} 个样本条目的结构:")
                            for key in sample_keys:
                                anime_data = index_data[key]
                                name = anime_data.get('name', '') or anime_data.get('title', '')
                                name_cn = anime_data.get('name_cn', '') or anime_data.get('cn', '')
                                self.logger.warning(f"条目 {key}: 名称='{name}', 中文名='{name_cn}'")
                    except json.JSONDecodeError as e:
                        self.logger.error(f"解析 {index_path} 为JSON时出错: {e}")
                except Exception as e:
                    self.logger.error(f"读取 {index_path} 时出错: {e}")
            else:
                self.logger.warning(f"未找到 index.json 文件: {index_path}")

    @staticmethod
    def create_lock_file():
        """Create a lock file to indicate that the updater is running"""
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
        """Check if another instance of the updater is running"""
        return os.path.exists(LOCK_FILE)

    @staticmethod
    def is_daily_updater_running():
        """Check if the daily updater is running by looking for its lock file"""
        return os.path.exists("anime_pilgrimage_scraper.lock")

    def ensure_apiid_json_exists(self):
        """Make sure apiid.json exists, create it if it doesn't"""
        if not os.path.exists('apiid.json'):
            self.logger.info(f"apiid.json not found, creating it")
            # Create an empty apiid.json file
            with open('apiid.json', 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            self.logger.info(f"Created empty apiid.json file")
        return True

    def update_apiid_json(self, local_id, api_id):
        """Add new entry to apiid.json"""
        try:
            # Ensure apiid.json exists
            self.ensure_apiid_json_exists()

            # Read current data
            with open('apiid.json', 'r', encoding='utf-8') as f:
                apiid_data = json.load(f)

            # Add new entry
            apiid_data[str(local_id)] = api_id

            # Save updated file
            with open('apiid.json', 'w', encoding='utf-8') as f:
                json.dump(apiid_data, f, ensure_ascii=False, indent=2)

            self.logger.info(f"Updated apiid.json with new entry: {local_id} -> {api_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error updating apiid.json: {e}")
            return False

    def run_auto_update(self, wait_time=1800, max_wait_attempts=3, start_api_id=None, end_api_id=2000000):
        """Run the automated update process

        Args:
            wait_time: Time to wait in seconds if another process is running (default: 30 minutes)
            max_wait_attempts: Maximum number of times to wait before giving up
            start_api_id: Starting API ID to scan (default: None, will use the last ID in apiid.json + 1)
            end_api_id: Ending API ID to scan (default: 3000000)
        """
        # 检查 index.json 文件
        self._check_index_json_files()

        # Check if another instance is running
        if self.is_process_running():
            self.logger.warning("Another instance of the monthly updater is already running")
            return {
                "new_anime_count": 0,
                "new_anime_ids": [],
                "updated_anime_count": 0,
                "updated_anime_ids": [],
                "local_id_range": (0, 0)
            }

        # Check if daily updater is running
        wait_attempts = 0
        while self.is_daily_updater_running() and wait_attempts < max_wait_attempts:
            wait_attempts += 1
            self.logger.warning(f"Daily updater is running. Waiting {wait_time/60} minutes (attempt {wait_attempts}/{max_wait_attempts})")
            time.sleep(wait_time)  # Wait for the specified time

            # If we've waited the maximum number of times, delay for 12 hours
            if wait_attempts == max_wait_attempts:
                self.logger.warning("Maximum wait attempts reached. Delaying for 12 hours.")
                time.sleep(43200)  # 12 hours in seconds

                # Check one more time
                if self.is_daily_updater_running():
                    self.logger.error("Daily updater is still running after 12 hours. Exiting.")
                    return {
                        "new_anime_count": 0,
                        "new_anime_ids": [],
                        "updated_anime_count": 0,
                        "updated_anime_ids": [],
                        "local_id_range": (0, 0)
                    }

        # Create lock file
        if not self.create_lock_file():
            self.logger.error("Failed to create lock file. Exiting.")
            return {
                "new_anime_count": 0,
                "new_anime_ids": [],
                "updated_anime_count": 0,
                "updated_anime_ids": [],
                "local_id_range": (0, 0)
            }

        try:
            # Make sure index.json exists
            self.ensure_index_exists()

            # Step 1: Find the next available local ID
            next_local_id = self.get_next_local_id()
            self.logger.info(f"Next available local anime ID: {next_local_id}")
            start_local_id = next_local_id

            # Step 2: Scan for valid API IDs
            # Ensure apiid.json exists
            self.ensure_apiid_json_exists()

            # 始终使用命令行参数中指定的起始ID
            # 如果没有提供，则使用默认值100000
            if start_api_id is None:
                start_api_id = 100000

            self.logger.info(f"Starting API ID scan from {start_api_id} to {end_api_id}")
            valid_ids = self.scan_api_ids(start_api_id, end_api_id)

            # Step 3: Pre-filter valid IDs by checking for duplicates in index.json
            self.logger.info(f"Found {len(valid_ids)} valid API IDs, checking for duplicates in index.json")

            # Load index.json data once to avoid repeated file operations
            index_data = self.load_index_data()

            # Log a sample of index.json entries for debugging
            sample_size = min(5, len(index_data))
            sample_entries = list(index_data.items())[:sample_size]
            self.logger.info(f"index.json 条目示例 (显示 {sample_size} 个，共 {len(index_data)} 个)")
            for local_id, anime_data in sample_entries:
                self.logger.info(f"  ID {local_id}: '{anime_data}'")

            # Create a list to store filtered API IDs (those not in index.json)
            filtered_ids = []

            # Check each API ID against index.json
            for i, api_id in enumerate(valid_ids, 1):
                # Get anime info
                anime_info = self.get_anime_info(api_id)
                if not anime_info:
                    self.logger.info(f"[{i}/{len(valid_ids)}] 跳过 API ID {api_id} 因为无法获取动画信息")
                    continue

                anime_name = anime_info.get('name', '')
                anime_name_cn = anime_info.get('name_cn', '')

                # Skip anime with empty names
                if not anime_name and not anime_name_cn:
                    self.logger.warning(f"[{i}/{len(valid_ids)}] 跳过 API ID {api_id} 因为它没有名称信息。这是意外的，因为 API 应该始终返回名称。")
                    # Log additional information about the API response
                    self.logger.warning(f"API ID {api_id} 的响应: {anime_info}")
                    continue

                # 这里不需要额外的日志，因为 _check_anime_in_index_direct 方法已经有了详细的日志

                # For debugging, log a sample of index.json entries
                if i == 1:  # Only do this for the first anime to avoid log spam
                    # Count entries with empty names
                    empty_name_count = 0
                    for _, anime_data in index_data.items():
                        if not anime_data.get('name') and not anime_data.get('name_cn') and not anime_data.get('title') and not anime_data.get('cn'):
                            empty_name_count += 1

                    if empty_name_count > 0:
                        self.logger.warning(f"在 index.json 中发现 {empty_name_count} 个没有名称的条目")

                    # Get a sample of non-empty entries
                    non_empty_entries = [(local_id, data) for local_id, data in index_data.items()
                                        if data.get('name') or data.get('name_cn') or data.get('title') or data.get('cn')]

                    sample_size = min(5, len(non_empty_entries))
                    if non_empty_entries:
                        sample_entries = non_empty_entries[:sample_size]
                        self.logger.info(f"index.json 中有名称的条目示例 (显示 {sample_size} 个，共 {len(non_empty_entries)} 个)")
                        for local_id, anime_data in sample_entries:
                            self.logger.info(f"  ID {local_id}: '{anime_data.get('name', '') or anime_data.get('title', '')}' / '{anime_data.get('name_cn', '') or anime_data.get('cn', '')}' ")
                    else:
                        self.logger.warning("index.json 中没有找到有名称的条目！")

                    # Dump the first 3 entries of index.json to see their structure
                    sample_size = min(3, len(index_data))
                    sample_keys = list(index_data.keys())[:sample_size]
                    self.logger.info(f"输出 index.json 的前 {sample_size} 个条目:")
                    for key in sample_keys:
                        self.logger.info(f"条目 {key}: {json.dumps(index_data[key], ensure_ascii=False)}")

                # Check if anime already exists in index.json using direct text search
                exists_in_index = self._check_anime_in_index_direct(anime_name, anime_name_cn)

                # If found, check if there are updates to existing anime
                if exists_in_index:
                    self.logger.info(f"[{i}/{len(valid_ids)}] 动画 '{anime_name}' / '{anime_name_cn}' (API ID: {api_id}) 已存在于 index.json 中，检查是否有更新")

                    # Find the local ID for this API ID
                    local_id = self.find_anime_by_api_id(api_id)
                    if local_id:
                        # Try to update the existing anime
                        update_success = self.update_existing_anime(api_id, local_id)
                        if update_success:
                            self.logger.info(f"[{i}/{len(valid_ids)}] 成功更新动画 '{anime_name}' / '{anime_name_cn}' (API ID: {api_id}, 本地 ID: {local_id}) 的巡礼点")
                            self.updated_anime_count += 1
                            self.updated_anime_ids.append(api_id)
                        else:
                            self.logger.info(f"[{i}/{len(valid_ids)}] 动画 '{anime_name}' / '{anime_name_cn}' (API ID: {api_id}) 没有新的巡礼点或更新失败")
                    else:
                        self.logger.warning(f"[{i}/{len(valid_ids)}] 动画 '{anime_name}' / '{anime_name_cn}' (API ID: {api_id}) 在 index.json 中存在但在 apiid.json 中找不到对应的本地ID")

                    # Skip adding this anime as a new one
                    continue

                # If we get here, the anime is not in index.json, so add it to filtered_ids
                self.logger.info(f"[{i}/{len(valid_ids)}] 添加动画 '{anime_name}' / '{anime_name_cn}' (API ID: {api_id}) 到处理列表（匹配失败，未在数据库中找到）")
                filtered_ids.append((api_id, anime_info))

                # Sleep briefly to avoid API rate limits
                time.sleep(0.5)

            # Step 4: Process filtered IDs and save anime data
            self.logger.info(f"找到 {len(filtered_ids)} 部新动画需要添加到数据库")

            for i, (api_id, anime_info) in enumerate(filtered_ids, 1):
                anime_name = anime_info.get('name', '')
                anime_name_cn = anime_info.get('name_cn', '')

                # Double-check that we have a name (just in case)
                if not anime_name and not anime_name_cn:
                    self.logger.warning(f"[{i}/{len(filtered_ids)}] 跳过 API ID {api_id} 因为它没有名称信息。这是意外的，因为 API 应该始终返回名称。")
                    # Log additional information about the API response
                    self.logger.warning(f"API ID {api_id} 的响应: {anime_info}")
                    continue

                self.logger.info(f"[{i}/{len(filtered_ids)}] 处理动画 '{anime_name}' / '{anime_name_cn}' (API ID: {api_id})")

                success = self.save_bangumi(api_id, next_local_id, anime_info)

                if success:
                    self.logger.info(f"[{i}/{len(filtered_ids)}] 成功保存动画 '{anime_name}' / '{anime_name_cn}' (API ID: {api_id}, 本地 ID: {next_local_id})")
                    self.update_apiid_json(next_local_id, api_id)
                    self.new_anime_count += 1
                    self.new_anime_ids.append(api_id)
                    next_local_id += 1
                else:
                    self.logger.info(f"[{i}/{len(filtered_ids)}] 保存动画 '{anime_name}' / '{anime_name_cn}' (API ID: {api_id}) 失败")

                # Sleep to avoid API rate limits
                time.sleep(1)

            self.local_id_range = (start_local_id, next_local_id - 1)

            # 生成完整的日志信息
            update_summary = f"更新完成！共扫描了 {len(valid_ids)} 个 API ID"
            if self.new_anime_count > 0:
                update_summary += f", 添加了 {self.new_anime_count} 部新动画"
            if self.updated_anime_count > 0:
                update_summary += f", 更新了 {self.updated_anime_count} 部已有动画的巡礼点"

            self.logger.info(update_summary)

            # Generate final index
            self.generate_index()
            return {
                "new_anime_count": self.new_anime_count,
                "new_anime_ids": self.new_anime_ids,
                "updated_anime_count": self.updated_anime_count,
                "updated_anime_ids": self.updated_anime_ids,
                "local_id_range": self.local_id_range,
                "scanned_count": len(valid_ids)
            }
        finally:
            # Always remove the lock file when done
            self.remove_lock_file()
            self.logger.info("锁文件已移除")

    def find_anime_by_api_id(self, api_id):
        """Find local ID for an anime by its API ID using apiid.json

        Args:
            api_id: The API ID to look for

        Returns:
            str: The local ID if found, None otherwise
        """
        try:
            # Ensure apiid.json exists
            self.ensure_apiid_json_exists()

            # Read current data
            with open('apiid.json', 'r', encoding='utf-8') as f:
                apiid_data = json.load(f)

            # Look for the API ID in the values
            for local_id, stored_api_id in apiid_data.items():
                if int(stored_api_id) == int(api_id):
                    return local_id

            return None
        except Exception as e:
            self.logger.error(f"查找API ID {api_id}对应的本地ID时出错: {e}")
            return None

    def compare_points_data(self, existing_points, new_points):
        """Compare existing points with new points to find new ones

        Args:
            existing_points: List of existing points from points.json
            new_points: List of new points from the API

        Returns:
            list: List of new points that don't exist in the existing points
        """
        # Create a set of existing point IDs for faster lookup
        existing_ids = set()
        for point in existing_points:
            if isinstance(point, dict) and 'id' in point:
                existing_ids.add(point['id'])

        # Find points in new_points that don't exist in existing_points
        new_points_to_add = []
        for point in new_points:
            if isinstance(point, dict) and 'id' in point and point['id'] not in existing_ids:
                new_points_to_add.append(point)

        return new_points_to_add

    def update_existing_anime(self, api_id, local_id):
        """Update an existing anime with new pilgrimage points

        Args:
            api_id: The API ID of the anime
            local_id: The local ID of the existing anime

        Returns:
            bool: True if the anime was updated successfully, False otherwise
        """
        self.logger.info(f"正在检查动画 API ID {api_id} (本地ID: {local_id}) 是否有新的巡礼点")

        # Get the folder path for the anime
        anime_folder = self.base_dir / str(local_id)
        if not anime_folder.exists():
            self.logger.error(f"动画文件夹 {anime_folder} 不存在")
            return False

        # Load existing points.json
        points_file = anime_folder / 'points.json'
        if not points_file.exists():
            self.logger.error(f"动画 {local_id} 的 points.json 文件不存在")
            return False

        try:
            # Load existing points
            with open(points_file, 'r', encoding='utf-8') as f:
                try:
                    existing_data = json.load(f)
                    # Handle both array and object with 'points' property formats
                    if isinstance(existing_data, list):
                        existing_points = existing_data
                    elif isinstance(existing_data, dict) and 'points' in existing_data:
                        existing_points = existing_data['points']
                    else:
                        self.logger.error(f"动画 {local_id} 的 points.json 格式不正确")
                        return False
                except json.JSONDecodeError:
                    self.logger.error(f"解析动画 {local_id} 的 points.json 文件失败")
                    return False

            # Get new points from the API
            new_points_data = self.get_bangumi_points(api_id)
            if not new_points_data:
                self.logger.info(f"无法从API获取动画 {api_id} 的巡礼点数据")
                return False

            # Compare points to find new ones
            new_points = self.compare_points_data(existing_points, new_points_data)
            if not new_points:
                self.logger.info(f"动画 API ID {api_id} (本地ID: {local_id}) 没有新的巡礼点")
                return False

            self.logger.info(f"动画 API ID {api_id} (本地ID: {local_id}) 有 {len(new_points)} 个新的巡礼点")

            # Ensure images directory exists
            images_dir = self.ensure_dir(anime_folder / 'images')

            # Download images for new points
            for point in new_points:
                if point.get('image'):
                    original_url = point['image'].split('?')[0]  # Get original size
                    file_name = os.path.basename(urlparse(original_url).path)
                    image_url = f'https://image.xinu.ink/pic/data/{local_id}/images/{file_name}'
                    point['image'] = image_url  # Update image URL
                    self.download_image(original_url, images_dir / file_name)

            # Combine existing and new points
            combined_points = existing_points + new_points
            self.logger.info(f"合并后共有 {len(combined_points)} 个巡礼点")

            # Save updated points.json
            # Preserve the original format (array or object with 'points' property)
            if isinstance(existing_data, list):
                with open(points_file, 'w', encoding='utf-8') as f:
                    json.dump(combined_points, f, ensure_ascii=False, indent=2)
            else:  # It's a dict with 'points' property
                existing_data['points'] = combined_points
                with open(points_file, 'w', encoding='utf-8') as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=2)

            # Update info.json if it exists
            info_file = anime_folder / 'info.json'
            if info_file.exists():
                try:
                    with open(info_file, 'r', encoding='utf-8') as f:
                        info_data = json.load(f)

                    # Update pointsLength if it exists
                    if 'pointsLength' in info_data:
                        info_data['pointsLength'] = len(combined_points)

                    with open(info_file, 'w', encoding='utf-8') as f:
                        json.dump(info_data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    self.logger.error(f"更新动画 {local_id} 的 info.json 文件失败: {e}")
                    # Continue anyway as this is not critical

            # 注意：不在这里调用generate_index()，而是在批量处理完所有动画后统一更新index.json
            # 这样可以避免重复更新index.json文件，提高效率

            return True

        except Exception as e:
            self.logger.error(f"更新动画 {local_id} 的巡礼点时出错: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    def send_bark_notification(self, bark_url, update_info):
        """Send notification via Bark"""
        if not update_info["new_anime_count"] and not update_info.get("updated_anime_count", 0):
            message = "🔍 本次扫描未发现新的动漫或更新。"
            if 'scanned_count' in update_info:
                message += f"\n🔢 共扫描了 {update_info['scanned_count']} 个API ID，但都已存在于数据库中且没有更新。"
        else:
            message = ""
            if update_info["new_anime_count"] > 0:
                message += f"🌟 成功添加 {update_info['new_anime_count']} 部新动漫。\n"
                message += f"💾 新增API ID: {', '.join(map(str, update_info['new_anime_ids']))}\n"
                message += f"📁 本地ID范围: {update_info['local_id_range'][0]} 至 {update_info['local_id_range'][1]}\n"

            if update_info.get("updated_anime_count", 0) > 0:
                message += f"🔄 成功更新 {update_info['updated_anime_count']} 部已有动漫的巡礼点。\n"
                message += f"💾 更新API ID: {', '.join(map(str, update_info['updated_anime_ids']))}\n"

            if 'scanned_count' in update_info:
                message += f"🔢 共扫描了 {update_info['scanned_count']} 个API ID。"

        title = "📅 动漫巡礼月度更新完成"
        full_url = f"{bark_url}/{title}/{message}"

        try:
            response = requests.get(full_url)
            response.raise_for_status()
            self.logger.info("成功发送 Bark 通知")
            return True
        except Exception as e:
            self.logger.error(f"发送 Bark 通知失败: {e}")
            return False

if __name__ == '__main__':
    try:
        # Get command line arguments
        bark_url = "https://api.day.app/FXxtHPEhbvdzxrgRpBW7E"
        wait_time = 1800  # Default: 30 minutes
        max_wait_attempts = 3  # Default: 3 attempts
        start_api_id = 100000  # Default start API ID
        end_api_id = 2000000  # Default end API ID
        aggressive_matching = False  # Default: disabled

        # Parse command line arguments
        if len(sys.argv) > 1:
            bark_url = sys.argv[1]
        if len(sys.argv) > 2:
            wait_time = int(sys.argv[2])
        if len(sys.argv) > 3:
            max_wait_attempts = int(sys.argv[3])
        if len(sys.argv) > 4:
            start_api_id = int(sys.argv[4])
        if len(sys.argv) > 5:
            end_api_id = int(sys.argv[5])
        if len(sys.argv) > 6:
            aggressive_matching = sys.argv[6].lower() in ['true', '1', 'yes', 'y']

        print(f"Using API ID range: {start_api_id} to {end_api_id}")
        if aggressive_matching:
            print("Aggressive name matching enabled")

        # Initialize the updater
        updater = AnitabiAutoUpdater(concurrency=100, aggressive_matching=aggressive_matching)

        # Run the update process
        update_info = updater.run_auto_update(wait_time=wait_time, max_wait_attempts=max_wait_attempts,
                                            start_api_id=start_api_id, end_api_id=end_api_id)

        # Send notification
        updater.send_bark_notification(bark_url, update_info)

        print("Update process completed successfully")

    except Exception as e:
        # Log the error
        logging.error(f"An error occurred during the update process: {e}")
        logging.error(traceback.format_exc())

        # Try to send a notification about the error
        try:
            error_message = f"🚨 更新过程中发生错误: {str(e)}"
            error_title = "📅 动漫巡礼月度更新失败"
            full_url = f"{bark_url}/{error_title}/{error_message}"
            requests.get(full_url)
        except Exception as notify_error:
            logging.error(f"Failed to send error notification: {notify_error}")

        # Re-raise the exception
        raise

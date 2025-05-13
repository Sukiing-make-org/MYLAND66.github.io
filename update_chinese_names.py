#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import requests
import logging
import argparse
import urllib.parse
from pathlib import Path
import sys

# Base directory for anime data
BASE_DIR = "pic/data"
# Default Bark notification URL
DEFAULT_BARK_URL = "https://api.day.app/FXxtHPEhbvdzxrgRpBW7E"

def setup_logging():
    """Set up logging configuration"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('chinese_name_updater.log', encoding='utf-8')
        ]
    )
    return logging.getLogger('chinese_name_updater')

def get_chinese_name_from_bangumi(japanese_name, logger):
    """Get Chinese name for an anime from Bangumi API

    Args:
        japanese_name: The Japanese name of the anime
        logger: Logger instance

    Returns:
        str: Chinese name if found, otherwise the original Japanese name
    """
    try:
        logger.info(f"正在从Bangumi API获取动漫《{japanese_name}》的中文名")

        # URL encode the Japanese name for the API request
        encoded_name = urllib.parse.quote(japanese_name)
        url = f"https://api.bgm.tv/search/subject/{encoded_name}?type=1&responseGroup=small"

        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
        }

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()

            # Check if we got results
            if data.get('results', 0) > 0 and len(data.get('list', [])) > 0:
                # Get the first result with a non-empty Chinese name
                for item in data['list']:
                    if item.get('name_cn') and item.get('name_cn').strip():
                        chinese_name = item['name_cn']
                        logger.info(f"找到中文名: {chinese_name}")
                        return chinese_name

                # If no item has a Chinese name, return the original name
                logger.info(f"Bangumi API结果中没有找到中文名")
                return japanese_name
            else:
                logger.info(f"Bangumi API中没有找到动漫《{japanese_name}》的结果")
                return japanese_name
        else:
            logger.warning(f"从Bangumi API获取数据失败: {response.status_code}")
            return japanese_name
    except Exception as e:
        logger.error(f"从Bangumi API获取中文名时出错: {e}")
        return japanese_name

def update_chinese_names(args):
    """更新动漫中文名，仅处理以下两种情况的番剧：
    1. 中日名数组完全一致的番剧（name 和 name_cn 相同）
    2. 中文数组为空的番剧（name_cn 为空）
    其他情况的番剧将被跳过，不做任何修改。

    Args:
        args: Command line arguments

    Returns:
        dict: Summary of changes
    """
    logger = setup_logging()
    logger.info("开始更新动漫中文名")

    base_dir = Path(args.base_dir)
    index_path = base_dir / "index.json"
    root_index_path = Path("index.json")

    # Check if index.json exists
    if not index_path.exists():
        logger.error(f"索引文件不存在: {index_path}")
        return {"updated_count": 0, "updated_anime": []}

    # Load index.json
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            index_data = json.load(f)
    except Exception as e:
        logger.error(f"读取索引文件时出错: {e}")
        return {"updated_count": 0, "updated_anime": []}

    # Track changes
    updated_count = 0
    updated_anime = []

    # Process each anime in index.json
    for local_id, anime in index_data.items():
        # 获取日文名和中文名
        japanese_name = anime.get('name', '')
        chinese_name_array = anime.get('name_cn', '')

        # 检查是否需要更新中文名
        needs_update = False

        # 情况1: 中文数组为空
        if not chinese_name_array:
            needs_update = True
            logger.info(f"动漫ID {local_id}: 中文名数组为空，需要更新")
        # 情况2: 中日名数组完全一致
        elif japanese_name == chinese_name_array:
            needs_update = True
            logger.info(f"动漫ID {local_id}: 中日名数组完全一致，需要更新")
        else:
            logger.info(f"动漫ID {local_id}: 中日名不同，无需更新")
            continue

        if needs_update:
            if not japanese_name:
                logger.warning(f"动漫ID {local_id} 没有日文名，跳过")
                continue

            logger.info(f"处理动漫ID {local_id}: {japanese_name}")

            # Get Chinese name from Bangumi API
            chinese_name = get_chinese_name_from_bangumi(japanese_name, logger)

            # Skip if Chinese name is the same as Japanese name
            if chinese_name == japanese_name:
                logger.info(f"动漫ID {local_id} 没有找到不同的中文名，保持原样")
                continue

            # Update index.json
            index_data[local_id]['name_cn'] = chinese_name
            logger.info(f"更新索引中的中文名: {chinese_name}")

            # Update info.json if it exists
            info_path = base_dir / local_id / "info.json"
            if info_path.exists():
                try:
                    with open(info_path, 'r', encoding='utf-8') as f:
                        info_data = json.load(f)

                    # Update the Chinese name in info.json
                    # Check if the file uses 'name_cn' or 'cn' for Chinese name
                    if 'name_cn' in info_data:
                        info_data['name_cn'] = chinese_name
                    elif 'cn' in info_data:
                        info_data['cn'] = chinese_name
                    else:
                        # If neither field exists, add 'cn'
                        info_data['cn'] = chinese_name

                    # Save updated info.json
                    with open(info_path, 'w', encoding='utf-8') as f:
                        json.dump(info_data, f, ensure_ascii=False, indent=2)

                    logger.info(f"更新info.json中的中文名: {info_path}")
                except Exception as e:
                    logger.error(f"更新info.json时出错: {e}")
                    # Continue with next anime even if this one fails
                    continue

            # Track this update
            updated_count += 1
            updated_anime.append({
                "local_id": local_id,
                "japanese_name": japanese_name,
                "chinese_name": chinese_name
            })

            # Add a small delay to avoid API rate limiting
            time.sleep(1)

    # Save updated index.json if any changes were made
    if updated_count > 0:
        # Save to data directory
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        # Also save to root directory
        with open(root_index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        logger.info(f"已更新索引文件: {index_path} 和 {root_index_path}")

    logger.info(f"中文名更新完成，共更新了 {updated_count} 个动漫")
    return {"updated_count": updated_count, "updated_anime": updated_anime}

def send_bark_notification(bark_url, title, message):
    """Send notification via Bark"""
    full_url = f"{bark_url}/{title}/{message}"

    try:
        response = requests.get(full_url)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"发送Bark通知失败: {e}")
        return False

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='动漫中文名更新工具 - 仅处理中日名相同或中文名为空的番剧')
    parser.add_argument('--base-dir', type=str, default=BASE_DIR, help='动漫数据的基础目录')
    parser.add_argument('--bark-url', type=str, default=DEFAULT_BARK_URL, help='Bark通知URL')

    args = parser.parse_args()

    # Update Chinese names
    result = update_chinese_names(args)

    # Send notification
    if result["updated_count"] > 0:
        title = "🔤 动漫中文名更新"
        message = f"✅ 成功更新了 {result['updated_count']} 个中日名相同或中文名为空的动漫"

        # Add some examples if available (limit to 3)
        if result["updated_anime"]:
            examples = result["updated_anime"][:3]
            message += "\n\n例如:"
            for anime in examples:
                message += f"\n• {anime['japanese_name']} → {anime['chinese_name']}"

            if len(result["updated_anime"]) > 3:
                message += f"\n...等共 {result['updated_count']} 个"
    else:
        title = "🔤 动漫中文名检查"
        message = "✓ 没有发现中日名相同或中文名为空的动漫，无需更新"

    send_bark_notification(args.bark_url, title, message)

if __name__ == "__main__":
    main()

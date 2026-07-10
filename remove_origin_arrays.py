#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import shutil
import logging
import argparse
import urllib.parse
import requests
from pathlib import Path
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # 将日志级别设置为DEBUG，以便查看更多信息
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('remove_origin_arrays.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

def remove_origin_arrays(bark_url=None):
    """Remove 'origin' and 'originURL' arrays from index.json

    Args:
        bark_url: URL for Bark notifications

    Returns:
        dict: Summary of the operation
    """
    root_index_path = Path("index.json")

    if not root_index_path.exists():
        logger.warning("根目录 index.json 不存在")
        return {"removed_count": 0}

    # 备份原始文件为 index-origin.json
    backup_path = Path("index-origin.json")
    shutil.copy2(root_index_path, backup_path)
    logger.info(f"已备份根目录索引文件到: {backup_path}")

    with open(root_index_path, 'r', encoding='utf-8') as f:
        index_data = json.load(f)
    logger.info(f"已加载根目录索引文件，包含 {len(index_data)} 个条目")

    removed_count = 0

    for local_id, anime_data in index_data.items():
        if 'points' not in anime_data or not isinstance(anime_data['points'], list):
            continue

        new_points = []
        for point in anime_data['points']:
            new_point = {k: v for k, v in point.items() if k not in ('origin', 'originURL', 'originLink')}

            if len(new_point) < len(point):
                removed_count += 1
                logger.debug(f"已从动漫 {local_id} 的点位 {point.get('id', '未知')} 中移除 origin/originURL/originLink")

            new_points.append(new_point)

        anime_data['points'] = new_points

    if removed_count > 0:
        with open(root_index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
        logger.info(f"已更新根目录索引文件，移除了 {removed_count} 个 origin/originURL/originLink 字段")
    else:
        logger.info("根目录索引文件中没有找到需要移除的 origin/originURL/originLink 字段")

    if bark_url:
        send_bark_notification(bark_url, removed_count)

    return {"removed_count": removed_count}

def send_bark_notification(bark_url, removed_count):
    """Send a notification via Bark

    Args:
        bark_url: Bark notification URL
        removed_count: Number of points with removed arrays
    """
    try:
        # Prepare notification content
        if removed_count > 0:
            title = "🧹 索引文件清理"
            content = f"已从 {removed_count} 个点位中移除 origin/originURL/originLink 数组"
        else:
            title = "🧹 索引文件检查"
            content = "没有找到需要移除的 origin/originURL/originLink 数组"

        # URL encode the title and content
        encoded_title = urllib.parse.quote(title)
        encoded_content = urllib.parse.quote(content)

        # Add emoji to make the notification more readable
        notification_url = f"{bark_url}/{encoded_title}/{encoded_content}?icon=https://image.xinu.ink/pic/data/images/icon.jpg"

        response = requests.get(notification_url)
        response.raise_for_status()
        logger.info("Bark通知已发送")
    except Exception as e:
        logger.error(f"发送Bark通知失败: {e}")

def main():
    parser = argparse.ArgumentParser(description='从index.json中移除origin和originURL数组')
    parser.add_argument('--bark-url', default=None, help='Bark通知的URL')

    args = parser.parse_args()

    start_time = datetime.now()
    logger.info(f"开始移除origin和originURL数组，时间: {start_time}")

    result = remove_origin_arrays(bark_url=args.bark_url)

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    logger.info(f"处理完成，耗时: {duration:.2f}秒")

    if "error" in result:
        logger.error(f"处理过程中出错: {result['error']}")
    else:
        logger.info(f"成功从 {result['removed_count']} 个点位中移除了origin和originURL数组")

if __name__ == "__main__":
    main()

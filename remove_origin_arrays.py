#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
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

def remove_origin_arrays(base_dir='pic/data', bark_url=None):
    """Remove 'origin' and 'originURL' arrays from index.json

    Args:
        base_dir: Base directory containing the index.json file
        bark_url: URL for Bark notifications

    Returns:
        dict: Summary of the operation
    """
    # Paths to index.json files
    data_index_path = Path(base_dir) / "index.json"
    root_index_path = Path("index.json")

    total_removed_count = 0
    processed_files = []

    # 处理根目录的index.json文件
    if root_index_path.exists():
        try:
            with open(root_index_path, 'r', encoding='utf-8') as f:
                root_index_data = json.load(f)
            logger.info(f"已加载根目录索引文件，包含 {len(root_index_data)} 个条目")

            # 处理根目录索引文件中的每个动漫条目
            root_removed_count = 0

            # 遍历每个动漫条目
            for local_id, anime_data in root_index_data.items():
                # 检查每个点位是否包含origin或originURL数组
                if 'points' in anime_data and isinstance(anime_data['points'], list):
                    # 获取点位数量
                    points_count = len(anime_data['points'])
                    logger.debug(f"动漫 {local_id} 有 {points_count} 个点位")

                    # 创建一个新的点位列表，不包含origin和originURL字段
                    new_points = []

                    for point in anime_data['points']:
                        origin_removed = False
                        origin_url_removed = False

                        # 创建点位的副本，不包含origin、originURL和originLink字段
                        new_point = {}
                        logger.debug(f"点位键: {list(point.keys())}")
                        origin_link_removed = False

                        for key, value in point.items():
                            if key != 'origin' and key != 'originURL' and key != 'originLink':
                                new_point[key] = value
                            elif key == 'origin':
                                origin_removed = True
                                logger.debug(f"从动漫 {local_id} 的点位 {point.get('id', '未知')} 中移除 origin 字段")
                            elif key == 'originURL':
                                origin_url_removed = True
                                logger.debug(f"从动漫 {local_id} 的点位 {point.get('id', '未知')} 中移除 originURL 字段")
                            elif key == 'originLink':
                                origin_link_removed = True
                                logger.debug(f"从动漫 {local_id} 的点位 {point.get('id', '未知')} 中移除 originLink 字段")

                        # 添加到新的点位列表
                        new_points.append(new_point)

                        # 如果移除了任一数组，计数加1
                        if origin_removed or origin_url_removed or origin_link_removed:
                            root_removed_count += 1
                            logger.info(f"已从动漫 {local_id} 的点位 {point.get('id', '未知')} 中移除 origin/originURL/originLink 数组")

                    # 用新的点位列表替换原来的点位列表
                    anime_data['points'] = new_points

            # 如果有任何更改，保存更新后的文件
            if root_removed_count > 0:
                with open(root_index_path, 'w', encoding='utf-8') as f:
                    json.dump(root_index_data, f, ensure_ascii=False, indent=2)
                logger.info(f"已更新根目录索引文件: {root_index_path}，移除了 {root_removed_count} 个 origin/originURL 数组")
                total_removed_count += root_removed_count
                processed_files.append(str(root_index_path))
            else:
                logger.info("根目录索引文件中没有找到需要移除的 origin/originURL 数组")

        except Exception as e:
            logger.error(f"处理根目录索引文件失败: {e}")

    # 处理数据目录的index.json文件
    if data_index_path.exists():
        try:
            with open(data_index_path, 'r', encoding='utf-8') as f:
                data_index_data = json.load(f)
            logger.info(f"已加载数据目录索引文件，包含 {len(data_index_data)} 个条目")

            # 处理数据目录索引文件中的每个动漫条目
            data_removed_count = 0

            # 遍历每个动漫条目
            for local_id, anime_data in data_index_data.items():
                # 检查每个点位是否包含origin或originURL数组
                if 'points' in anime_data and isinstance(anime_data['points'], list):
                    # 获取点位数量
                    points_count = len(anime_data['points'])
                    logger.debug(f"动漫 {local_id} 有 {points_count} 个点位")

                    # 创建一个新的点位列表，不包含origin和originURL字段
                    new_points = []

                    for point in anime_data['points']:
                        origin_removed = False
                        origin_url_removed = False

                        # 创建点位的副本，不包含origin、originURL和originLink字段
                        new_point = {}
                        logger.debug(f"点位键: {list(point.keys())}")
                        origin_link_removed = False

                        for key, value in point.items():
                            if key != 'origin' and key != 'originURL' and key != 'originLink':
                                new_point[key] = value
                            elif key == 'origin':
                                origin_removed = True
                                logger.debug(f"从动漫 {local_id} 的点位 {point.get('id', '未知')} 中移除 origin 字段")
                            elif key == 'originURL':
                                origin_url_removed = True
                                logger.debug(f"从动漫 {local_id} 的点位 {point.get('id', '未知')} 中移除 originURL 字段")
                            elif key == 'originLink':
                                origin_link_removed = True
                                logger.debug(f"从动漫 {local_id} 的点位 {point.get('id', '未知')} 中移除 originLink 字段")

                        # 添加到新的点位列表
                        new_points.append(new_point)

                        # 如果移除了任一数组，计数加1
                        if origin_removed or origin_url_removed or origin_link_removed:
                            data_removed_count += 1
                            logger.info(f"已从动漫 {local_id} 的点位 {point.get('id', '未知')} 中移除 origin/originURL/originLink 数组")

                    # 用新的点位列表替换原来的点位列表
                    anime_data['points'] = new_points

            # 如果有任何更改，保存更新后的文件
            if data_removed_count > 0:
                with open(data_index_path, 'w', encoding='utf-8') as f:
                    json.dump(data_index_data, f, ensure_ascii=False, indent=2)
                logger.info(f"已更新数据目录索引文件: {data_index_path}，移除了 {data_removed_count} 个 origin/originURL 数组")
                total_removed_count += data_removed_count
                processed_files.append(str(data_index_path))
            else:
                logger.info("数据目录索引文件中没有找到需要移除的 origin/originURL 数组")

        except Exception as e:
            logger.error(f"处理数据目录索引文件失败: {e}")

    # 总结处理结果
    if total_removed_count > 0:
        logger.info(f"总共从 {', '.join(processed_files)} 中移除了 {total_removed_count} 个 origin/originURL 数组")
    else:
        logger.info("没有找到需要移除的 origin/originURL 数组")

    # Send notification
    if bark_url:
        send_bark_notification(bark_url, total_removed_count)

    return {"removed_count": total_removed_count}

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
    parser.add_argument('--base-dir', default='pic/data', help='动漫数据的基础目录')
    parser.add_argument('--bark-url', default=None, help='Bark通知的URL')

    args = parser.parse_args()

    start_time = datetime.now()
    logger.info(f"开始移除origin和originURL数组，时间: {start_time}")

    result = remove_origin_arrays(base_dir=args.base_dir, bark_url=args.bark_url)

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    logger.info(f"处理完成，耗时: {duration:.2f}秒")

    if "error" in result:
        logger.error(f"处理过程中出错: {result['error']}")
    else:
        logger.info(f"成功从 {result['removed_count']} 个点位中移除了origin和originURL数组")

if __name__ == "__main__":
    main()

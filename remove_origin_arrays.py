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
    level=logging.INFO,
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
    
    # Check if index.json exists in data directory
    if not data_index_path.exists():
        logger.error(f"索引文件不存在: {data_index_path}")
        return {"error": "索引文件不存在", "removed_count": 0}
    
    # Load index.json from data directory
    try:
        with open(data_index_path, 'r', encoding='utf-8') as f:
            index_data = json.load(f)
        logger.info(f"已加载索引文件，包含 {len(index_data)} 个条目")
    except Exception as e:
        logger.error(f"加载索引文件失败: {e}")
        return {"error": f"加载索引文件失败: {e}", "removed_count": 0}
    
    # Track the number of entries with removed arrays
    removed_count = 0
    
    # Process each anime entry
    for local_id, anime_data in index_data.items():
        origin_removed = False
        origin_url_removed = False
        
        # Remove 'origin' array if it exists
        if 'origin' in anime_data:
            del anime_data['origin']
            origin_removed = True
            
        # Remove 'originURL' array if it exists
        if 'originURL' in anime_data:
            del anime_data['originURL']
            origin_url_removed = True
            
        # Count if either array was removed
        if origin_removed or origin_url_removed:
            removed_count += 1
            logger.info(f"已从动漫 {local_id} 中移除 origin/originURL 数组")
    
    # Save updated index.json if any changes were made
    if removed_count > 0:
        # Save to data directory
        with open(data_index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
        
        # Also save to root directory if it exists
        if root_index_path.exists():
            with open(root_index_path, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, ensure_ascii=False, indent=2)
            
        logger.info(f"已更新索引文件: {data_index_path} 和 {root_index_path}")
    else:
        logger.info("没有找到需要移除的 origin/originURL 数组")
    
    # Send notification
    if bark_url:
        send_bark_notification(bark_url, removed_count)
    
    return {"removed_count": removed_count}

def send_bark_notification(bark_url, removed_count):
    """Send a notification via Bark
    
    Args:
        bark_url: Bark notification URL
        removed_count: Number of entries with removed arrays
    """
    try:
        # Prepare notification content
        if removed_count > 0:
            title = "🧹 索引文件清理"
            content = f"已从 {removed_count} 个动漫条目中移除 origin/originURL 数组"
        else:
            title = "🧹 索引文件检查"
            content = "没有找到需要移除的 origin/originURL 数组"
        
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
        logger.info(f"成功从 {result['removed_count']} 个动漫条目中移除了origin和originURL数组")

if __name__ == "__main__":
    main()

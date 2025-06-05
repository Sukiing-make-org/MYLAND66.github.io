#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import requests
import logging
import argparse
import sys
import datetime
from pathlib import Path
from anime_pilgrimage_scraper import AnimePilgrimageScraper, LOCK_FILE

# Lock file path for the monthly updater
MONTHLY_UPDATER_LOCK = "anitabi_updater.lock"
# Base directory for anime data
BASE_DIR = "pic/data"
# Bark notification URL
DEFAULT_BARK_URL = "https://api.day.app/FXxtHPEhbvdzxrgRpBW7E"

def setup_logging():
    """设置日志配置"""
    logger = logging.getLogger("动漫巡礼更新器")
    logger.setLevel(logging.INFO)

    # 创建处理器
    c_handler = logging.StreamHandler()
    f_handler = logging.FileHandler("anime_pilgrimage_daily.log", encoding='utf-8')
    c_handler.setLevel(logging.INFO)
    f_handler.setLevel(logging.INFO)

    # 创建格式化器并添加到处理器
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    c_handler.setFormatter(formatter)
    f_handler.setFormatter(formatter)

    # 将处理器添加到日志记录器
    logger.addHandler(c_handler)
    logger.addHandler(f_handler)

    return logger

def is_monthly_updater_running():
    """检查月度更新器是否正在运行"""
    return os.path.exists(MONTHLY_UPDATER_LOCK)

def is_daily_updater_running():
    """检查每日更新器是否正在运行"""
    return os.path.exists(LOCK_FILE)

def create_lock_file():
    """创建锁文件以指示更新器正在运行"""
    try:
        with open(LOCK_FILE, 'w') as f:
            f.write(str(datetime.datetime.now()))
        return True
    except Exception as e:
        logging.error(f"创建锁文件时出错: {e}")
        return False

def remove_lock_file():
    """删除锁文件"""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
        return True
    except Exception as e:
        logging.error(f"删除锁文件时出错: {e}")
        return False

def send_bark_notification(bark_url, title, message, url=None):
    """通过Bark发送通知

    Args:
        bark_url: Bark API URL
        title: 通知标题
        message: 通知消息
        url: 可选的URL，点击通知时打开
    """
    # 构建URL
    if url:
        full_url = f"{bark_url}/{title}/{message}?url={url}"
    else:
        full_url = f"{bark_url}/{title}/{message}"

    try:
        response = requests.get(full_url)
        response.raise_for_status()
        logging.info("Bark通知发送成功")
        return True
    except Exception as e:
        logging.error(f"发送Bark通知失败: {e}")
        return False

def run_daily_updater(args):
    """运行每日动漫巡礼更新器"""
    logger = setup_logging()
    logger.info("开始运行每日动漫巡礼更新器")

    # 检查是否有其他实例正在运行
    if is_daily_updater_running():
        logger.warning("每日更新器的另一个实例已在运行")
        return False

    # 检查月度更新器是否正在运行
    wait_attempts = 0
    while is_monthly_updater_running() and wait_attempts < args.max_wait_attempts:
        wait_attempts += 1
        logger.warning(f"月度更新器正在运行。等待 {args.wait_time/60} 分钟 (尝试 {wait_attempts}/{args.max_wait_attempts})")
        time.sleep(args.wait_time)  # 等待指定时间

        # 如果已经等待了最大次数，延迟12小时
        if wait_attempts == args.max_wait_attempts:
            logger.warning("达到最大等待次数。延迟12小时。")
            time.sleep(43200)  # 12小时（秒）

            # 再检查一次
            if is_monthly_updater_running():
                logger.error("月度更新器在12小时后仍在运行。退出。")
                return False

    # 运行 extract_apiid.py 来刷新 apiid.json
    try:
        logger.info("运行 extract_apiid.py 来刷新 apiid.json")
        import extract_apiid
        extract_apiid.extract_apiid(base_dir='pic/data')
        logger.info("成功刷新 apiid.json")
    except Exception as e:
        logger.error(f"刷新 apiid.json 时出错: {e}")
        # 继续执行，因为这不是关键步骤

    # 首先初始化爬虫（暂不创建锁文件）
    scraper = AnimePilgrimageScraper(
        base_dir=args.base_dir,
        headless=True,
        auto_mode=True
    )

    # 现在创建锁文件
    if not create_lock_file():
        logger.error("创建锁文件失败。退出。")
        return False

    try:
        # 首先尝试获取动漫列表以诊断任何问题
        try:
            logger.info("在运行完整爬虫之前测试动漫列表检索")
            anime_list = scraper.get_anime_list()
            if anime_list:
                logger.info(f"测试运行中成功检索到 {len(anime_list)} 部动漫")
                for i, anime in enumerate(anime_list[:5], 1):
                    logger.info(f"测试动漫 {i}: {anime['title']}")
            else:
                logger.warning("测试检索中未找到动漫。无论如何都会尝试完整运行。")
        except Exception as e:
            logger.error(f"测试动漫列表检索时出错: {e}")
            # 无论如何都继续尝试完整运行

        # 运行完整爬虫
        result = scraper.run(
            auto_mode=True,
            max_anime=args.max_anime,
            wait_time=args.wait_time,
            max_wait_attempts=args.max_wait_attempts
        )

        # 从爬虫获取详细结果
        # result 可能的值:
        # - True = 成功更新
        # - 2 = 成功但未找到新数据
        # - dict = 成功并包含详细更新信息
        # - False = 发生错误

        if isinstance(result, dict):
            # 我们有详细的更新信息
            updated_anime = result.get('updated_anime', [])
            new_anime = result.get('new_anime', [])

            # 创建详细消息
            details = []

            if new_anime:
                new_anime_names = [f"《{anime['name']}》({len(anime['points'])}个点位)" for anime in new_anime[:3]]
                if len(new_anime) > 3:
                    new_anime_names.append(f"等{len(new_anime)-3}部作品")
                details.append(f"🆕 新增动漫: {', '.join(new_anime_names)}")

            if updated_anime:
                updated_anime_names = [f"《{anime['name']}》(+{anime['new_points']}个点位)" for anime in updated_anime[:3]]
                if len(updated_anime) > 3:
                    updated_anime_names.append(f"等{len(updated_anime)-3}部作品")
                details.append(f"🔄 更新动漫: {', '.join(updated_anime_names)}")

            # 发送包含详细信息的通知
            title = "✅ 动漫巡礼每日更新成功"
            message = "\n".join(details) if details else "已检查最近更新的动漫，成功添加新番剧或更新已有番剧的巡礼点数据。"

            # 如果有坐标，添加Google Maps URL
            map_url = None
            if updated_anime and 'latest_point' in updated_anime[0] and 'geo' in updated_anime[0]['latest_point']:
                lat, lng = updated_anime[0]['latest_point']['geo']
                map_url = f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}"

            send_bark_notification(args.bark_url, title, message, map_url)
            logger.info("每日更新成功完成，包含详细数据")
            return True

        elif result is True:
            # 发送关于成功更新新数据的通知
            title = "✅ 动漫巡礼每日更新成功"
            message = f"已检查最近更新的动漫，成功添加新番剧或更新已有番剧的巡礼点数据。"
            send_bark_notification(args.bark_url, title, message)
            logger.info("每日更新成功完成，包含新数据")
            return True

        elif result == 2:
            # 发送关于成功检查但无新数据的通知
            title = "ℹ️ 动漫巡礼每日检查完成"
            message = f"已检查最近更新的动漫，未发现新番剧或新巡礼点数据。"
            send_bark_notification(args.bark_url, title, message)
            logger.info("每日更新成功完成，但未找到新数据")
            return True  # 仍然向GitHub Actions返回成功

        else:
            # 发送关于失败的通知
            title = "⚠️ 动漫巡礼每日更新失败"
            message = "更新动漫巡礼数据失败。请查看日志了解详情。"
            send_bark_notification(args.bark_url, title, message)
            logger.error("每日更新失败")
            return False

    except Exception as e:
        logger.error(f"运行每日更新器时出错: {e}")
        # 保存任何可用的页面源代码用于调试
        try:
            with open("daily_updater_error_page.html", "w", encoding="utf-8") as f:
                f.write(scraper.driver.page_source)
            logger.info("已保存页面源代码到 daily_updater_error_page.html 用于调试")
        except Exception as page_error:
            logger.error(f"无法保存页面源代码: {page_error}")

        # 发送关于错误的通知
        title = "🚨 动漫巡礼每日更新错误"
        message = f"⛔ 每日更新过程中出现错误: {str(e)[:100]}..."
        send_bark_notification(args.bark_url, title, message)
        return False

    finally:
        # 完成时始终删除锁文件
        remove_lock_file()

def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='动漫巡礼每日更新器')
    parser.add_argument('--max-anime', type=int, default=50, help='检查更新的最大动漫数量')
    parser.add_argument('--wait-time', type=int, default=1800, help='如果其他进程正在运行时等待的时间（秒）（默认：30分钟）')
    parser.add_argument('--max-wait-attempts', type=int, default=3, help='放弃前的最大等待次数')
    parser.add_argument('--base-dir', type=str, default=BASE_DIR, help='动漫数据的基础目录')
    parser.add_argument('--bark-url', type=str, default=DEFAULT_BARK_URL, help='Bark通知URL')

    args = parser.parse_args()

    # 运行更新器
    success = run_daily_updater(args)

    # 返回适当的退出代码
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

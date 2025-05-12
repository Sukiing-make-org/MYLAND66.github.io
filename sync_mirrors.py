#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import argparse
import logging
import subprocess
import shutil
import tempfile
import time
import requests
from pathlib import Path
import sys
import hashlib
import re

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("mirror_sync.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def send_bark_notification(bark_url, title, message):
    """Send notification via Bark"""
    if not bark_url:
        logger.warning("No Bark URL provided, skipping notification")
        return False

    full_url = f"{bark_url}/{title}/{message}"

    try:
        response = requests.get(full_url)
        response.raise_for_status()
        logger.info("Bark notification sent successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to send Bark notification: {e}")
        return False

def run_command(command, cwd=None):
    """Run a shell command and return the output"""
    logger.info(f"Running command: {command}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            text=True,
            capture_output=True,
            cwd=cwd
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with error: {e}")
        logger.error(f"Error output: {e.stderr}")
        raise

def clone_repository(repo_url, target_dir, branch="main"):
    """Clone a repository to a target directory"""
    logger.info(f"Cloning repository {repo_url} to {target_dir}")
    try:
        run_command(f"git clone {repo_url} {target_dir} --branch {branch} --single-branch --depth 1")
        return True
    except Exception as e:
        logger.error(f"Failed to clone repository: {e}")
        return False

def get_file_hash(file_path):
    """Calculate MD5 hash of a file"""
    if not os.path.exists(file_path):
        return None

    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def find_changed_files(source_dir, target_dir):
    """Find files that are new or modified in the source directory compared to the target directory"""
    changed_files = []
    new_folders = []

    # Walk through the source directory
    for root, dirs, files in os.walk(source_dir):
        # Get the relative path from the source directory
        rel_path = os.path.relpath(root, source_dir)
        target_path = os.path.join(target_dir, rel_path) if rel_path != "." else target_dir

        # Check if the directory exists in the target
        if not os.path.exists(target_path):
            new_folders.append((rel_path, root))
            # All files in this directory are new
            for file in files:
                source_file = os.path.join(root, file)
                rel_file_path = os.path.join(rel_path, file) if rel_path != "." else file
                changed_files.append((rel_file_path, source_file))
            continue

        # Check each file
        for file in files:
            source_file = os.path.join(root, file)
            target_file = os.path.join(target_path, file)
            rel_file_path = os.path.join(rel_path, file) if rel_path != "." else file

            # If the file doesn't exist in the target or has a different hash, it's changed
            if not os.path.exists(target_file) or get_file_hash(source_file) != get_file_hash(target_file):
                changed_files.append((rel_file_path, source_file))

    return changed_files, new_folders

def get_new_anime_folders(source_dir, target_dir):
    """Find new anime folders by comparing source and target directories"""
    source_data_dir = os.path.join(source_dir, "pic", "data")
    target_data_dir = os.path.join(target_dir, "pic", "data")

    if not os.path.exists(source_data_dir):
        logger.warning(f"Source data directory does not exist: {source_data_dir}")
        return []

    if not os.path.exists(target_data_dir):
        # If target data directory doesn't exist, all folders are new
        return [f for f in os.listdir(source_data_dir)
                if os.path.isdir(os.path.join(source_data_dir, f)) and f.isdigit()]

    # Get all anime folders in source and target
    source_folders = set(f for f in os.listdir(source_data_dir)
                        if os.path.isdir(os.path.join(source_data_dir, f)) and f.isdigit())
    target_folders = set(f for f in os.listdir(target_data_dir)
                        if os.path.isdir(os.path.join(target_data_dir, f)) and f.isdigit())

    # New folders are in source but not in target
    new_folders = source_folders - target_folders
    return sorted(list(new_folders))

def is_folder_in_range(folder_id, folder_ranges):
    """Check if a folder ID is within the specified ranges

    Args:
        folder_id: Folder ID to check
        folder_ranges: List of tuples (start, end) representing folder ID ranges
                      If end is None, it means all IDs from start onwards

    Returns:
        True if the folder ID is within any of the ranges, False otherwise
    """
    for start, end in folder_ranges:
        if end is None:  # Range with no upper bound
            if folder_id >= start:
                return True
        elif start <= folder_id <= end:
            return True
    return False

def sync_repository(source_dir, repo_url, github_token, bark_url=None, branch="main", load_balance=False, load_balance_index=None, folder_ranges=None):
    """Sync changes to a mirror repository

    Args:
        source_dir: Source directory containing the anime data
        repo_url: URL of the mirror repository
        github_token: GitHub token for authentication
        bark_url: Bark notification URL
        branch: Branch to push to
        load_balance: Whether to use load balancing mode
        load_balance_index: Index of this repository in the load balancing group (0-based)
        folder_ranges: List of tuples (start, end) representing folder ID ranges for this repository
                      If end is None, it means all IDs from start onwards
    """
    logger.info(f"Starting sync to mirror repository: {repo_url}")
    logger.info(f"Load balancing: {'Enabled' if load_balance else 'Disabled'}")
    if load_balance:
        logger.info(f"Load balance index: {load_balance_index}")
        if folder_ranges:
            ranges_str = ", ".join([f"{start}-{end if end is not None else 'onwards'}" for start, end in folder_ranges])
            logger.info(f"Folder ranges: {ranges_str}")

    # Create a temporary directory for the mirror repository
    with tempfile.TemporaryDirectory() as temp_dir:
        # Construct the repository URL with authentication
        auth_repo_url = repo_url
        if github_token and "https://" in repo_url:
            auth_repo_url = repo_url.replace("https://", f"https://{github_token}@")

        # Clone the mirror repository
        if not clone_repository(auth_repo_url, temp_dir, branch):
            logger.error(f"Failed to clone mirror repository: {repo_url}")
            return False

        # Find new anime folders
        new_anime_folders = get_new_anime_folders(source_dir, temp_dir)
        logger.info(f"Found {len(new_anime_folders)} new anime folders")

        # In load balancing mode, filter new folders based on folder ranges
        if load_balance and folder_ranges:
            # Filter new folders based on folder ranges
            filtered_folders = []
            for folder in new_anime_folders:
                try:
                    folder_id = int(folder)
                    if is_folder_in_range(folder_id, folder_ranges):
                        filtered_folders.append(folder)
                    else:
                        logger.info(f"Folder {folder} is outside the range for this repository, skipping")
                except ValueError:
                    logger.warning(f"Invalid folder name: {folder}, skipping")

            logger.info(f"After filtering by range, {len(filtered_folders)} new folders will be synced to this repository")
            new_anime_folders = filtered_folders

        # Find changed files in existing folders
        source_data_dir = os.path.join(source_dir, "pic", "data")
        target_data_dir = os.path.join(temp_dir, "pic", "data")

        # Ensure target data directory exists
        os.makedirs(target_data_dir, exist_ok=True)

        # Copy new anime folders
        for folder in new_anime_folders:
            source_folder = os.path.join(source_data_dir, folder)
            target_folder = os.path.join(target_data_dir, folder)

            logger.info(f"Copying new anime folder: {folder}")
            shutil.copytree(source_folder, target_folder)

        # Find changed files in existing folders (excluding new folders)
        existing_folders = [f for f in os.listdir(source_data_dir)
                          if os.path.isdir(os.path.join(source_data_dir, f)) and f.isdigit() and f not in new_anime_folders]

        changed_files_count = 0
        for folder in existing_folders:
            source_folder = os.path.join(source_data_dir, folder)
            target_folder = os.path.join(target_data_dir, folder)

            # Skip if the folder doesn't exist in the target (should be handled by new_anime_folders)
            if not os.path.exists(target_folder):
                continue

            # Find changed files in this folder
            folder_changed_files, folder_new_dirs = find_changed_files(source_folder, target_folder)

            # Create new directories
            for rel_dir, source_dir_path in folder_new_dirs:
                target_dir_path = os.path.join(target_folder, rel_dir)
                os.makedirs(target_dir_path, exist_ok=True)

            # Copy changed files
            for rel_file, source_file in folder_changed_files:
                target_file = os.path.join(target_folder, rel_file)
                os.makedirs(os.path.dirname(target_file), exist_ok=True)
                shutil.copy2(source_file, target_file)
                changed_files_count += 1

        logger.info(f"Copied {changed_files_count} changed files in existing folders")

        # Copy index.json to the root directory
        logger.info("Copying index.json to root directory")
        source_index = os.path.join(source_dir, "index.json")
        target_index = os.path.join(temp_dir, "index.json")
        if os.path.exists(source_index):
            shutil.copy2(source_index, target_index)

        # Copy apiid.json to the root directory
        logger.info("Copying apiid.json to root directory")
        source_apiid = os.path.join(source_dir, "apiid.json")
        target_apiid = os.path.join(temp_dir, "apiid.json")
        if os.path.exists(source_apiid):
            shutil.copy2(source_apiid, target_apiid)

        # Check if there are any changes
        git_status = run_command("git status --porcelain", cwd=temp_dir)
        if not git_status:
            logger.info("No changes to commit")
            return True

        # Configure Git
        run_command('git config --local user.email "action@github.com"', cwd=temp_dir)
        run_command('git config --local user.name "GitHub Action"', cwd=temp_dir)

        # Commit and push changes
        logger.info("Committing and pushing changes")
        current_date = time.strftime("%Y-%m-%d")

        # Create a detailed commit message
        if load_balance:
            commit_message = f"Sync anime data: {current_date} (Load balanced repo {load_balance_index+1})"
            if new_anime_folders:
                commit_message += f"\n\nNew anime folders: {', '.join(new_anime_folders)}"
            if changed_files_count > 0:
                commit_message += f"\n\nUpdated {changed_files_count} files in existing folders"
        else:
            commit_message = f"Sync anime data: {current_date}"
            if new_anime_folders:
                commit_message += f"\n\nNew anime folders: {', '.join(new_anime_folders)}"
            if changed_files_count > 0:
                commit_message += f"\n\nUpdated {changed_files_count} files in existing folders"

        try:
            run_command("git add -A", cwd=temp_dir)
            run_command(f'git commit -m "{commit_message}"', cwd=temp_dir)
            run_command(f"git push", cwd=temp_dir)
            logger.info("Successfully pushed changes to mirror repository")

            # Send notification
            if bark_url:
                title = "🔄 动漫数据同步"
                message = f"✅ 成功同步数据到镜像仓库！\n仓库: {repo_url}"
                if load_balance:
                    message += f"\n负载均衡: 仓库 {load_balance_index+1}"
                if new_anime_folders:
                    message += f"\n新增番剧: {len(new_anime_folders)} 个"
                if changed_files_count > 0:
                    message += f"\n更新文件: {changed_files_count} 个"
                message += f"\n日期: {current_date}"
                send_bark_notification(bark_url, title, message)

            return True
        except Exception as e:
            logger.error(f"Failed to push changes: {e}")

            # Send notification about failure
            if bark_url:
                title = "⚠️ 动漫数据同步失败"
                message = f"❌ 同步到镜像仓库失败！\n仓库: {repo_url}"
                if load_balance:
                    message += f"\n负载均衡: 仓库 {load_balance_index+1}"
                message += f"\n错误: {str(e)[:100]}..."
                send_bark_notification(bark_url, title, message)

            return False

def main():
    parser = argparse.ArgumentParser(description='Sync anime data to mirror repositories')
    parser.add_argument('--source-dir', type=str, default='.',
                        help='Source directory containing the anime data (default: current directory)')
    parser.add_argument('--mirrors', type=str, required=True,
                        help='Comma-separated list of mirror repository URLs')
    parser.add_argument('--github-token', type=str, required=True,
                        help='GitHub token for authentication')
    parser.add_argument('--bark-url', type=str, default=None,
                        help='Bark notification URL')
    parser.add_argument('--branch', type=str, default='main',
                        help='Branch to push to (default: main)')
    parser.add_argument('--load-balance', action='store_true',
                        help='Enable load balancing mode')
    parser.add_argument('--load-balance-repos', type=str, default=None,
                        help='Comma-separated list of load balancing repository URLs')
    parser.add_argument('--folder-ranges', type=str, default=None,
                        help='JSON string defining folder ranges for load balancing repositories')

    args = parser.parse_args()

    # Split the mirror URLs
    mirror_urls = [url.strip() for url in args.mirrors.split(',')]

    # Handle load balancing repositories
    load_balance_urls = []
    folder_ranges = None

    if args.load_balance and args.load_balance_repos:
        load_balance_urls = [url.strip() for url in args.load_balance_repos.split(',')]
        logger.info(f"Load balancing enabled with {len(load_balance_urls)} repositories")

        # Parse folder ranges if provided
        if args.folder_ranges:
            try:
                folder_ranges = json.loads(args.folder_ranges)
                logger.info(f"Loaded folder ranges: {folder_ranges}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse folder ranges JSON: {e}")
                logger.info("Using default folder ranges")

        # Use default folder ranges if not provided or parsing failed
        if not folder_ranges:
            # Default ranges based on your specification
            folder_ranges = [
                [[1, 700]],           # cdn1: 1-700
                [[701, 1009]],        # cdn2: 701-1009
                [[1010, 5258]],       # cdn3: 1010-5258
                [[5259, None]]        # cdn4: 5259-onwards
            ]
            logger.info(f"Using default folder ranges: {folder_ranges}")

    # Sync to each mirror repository
    success_count = 0
    total_repos = len(mirror_urls) + len(load_balance_urls)

    # Sync to regular mirror repositories
    for url in mirror_urls:
        if sync_repository(args.source_dir, url, args.github_token, args.bark_url, args.branch):
            success_count += 1

    # Sync to load balancing repositories
    for i, url in enumerate(load_balance_urls):
        # Get folder ranges for this repository
        repo_folder_ranges = folder_ranges[i] if folder_ranges and i < len(folder_ranges) else None

        if sync_repository(args.source_dir, url, args.github_token, args.bark_url, args.branch,
                         load_balance=True, load_balance_index=i, folder_ranges=repo_folder_ranges):
            success_count += 1

    # Send final notification
    if args.bark_url:
        title = "🔄 动漫数据同步完成"
        message = f"✅ 成功同步到 {success_count}/{total_repos} 个镜像仓库"
        if args.load_balance and load_balance_urls:
            message += f"\n其中负载均衡仓库: {len(load_balance_urls)} 个"
        send_bark_notification(args.bark_url, title, message)

    logger.info(f"Sync completed: {success_count}/{total_repos} repositories synced successfully")

    # Return success if all repositories were synced successfully
    return success_count == total_repos

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)

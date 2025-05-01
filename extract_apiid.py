#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import glob
from collections import OrderedDict

def extract_apiid(base_dir='.'):
    """
    Traverse through all folders, extract folder numbers and IDs from info.json files,
    and compile them into apiid.json sorted by folder number.

    Args:
        base_dir: Base directory containing the anime folders (default: current directory)
    """
    # Dictionary to store folder numbers and their corresponding IDs
    apiid_data = {}

    # Get all directories in the specified path
    folders = [f for f in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, f)) and f.isdigit()]

    print(f"Found {len(folders)} folders to process in {base_dir}")

    # Process each folder
    for i, folder in enumerate(folders):
        if i % 100 == 0:
            print(f"Processing folder {i+1}/{len(folders)} ({folder})...")

        # Path to info.json in the current folder
        info_path = os.path.join(base_dir, folder, 'info.json')

        # Check if info.json exists
        if os.path.exists(info_path):
            try:
                # Read info.json
                with open(info_path, 'r', encoding='utf-8') as f:
                    info_data = json.load(f)

                # Extract ID
                if 'id' in info_data:
                    # Add folder number and ID to the dictionary
                    apiid_data[folder] = info_data['id']
            except Exception as e:
                print(f"Error processing {info_path}: {e}")

    # Sort the dictionary by folder number (as integers)
    sorted_apiid_data = OrderedDict()
    for folder in sorted(apiid_data.keys(), key=int):
        sorted_apiid_data[folder] = apiid_data[folder]

    # Save the compiled data to apiid.json in the base directory
    output_path = os.path.join(base_dir, 'apiid.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(sorted_apiid_data, f, ensure_ascii=False, indent=2)

    print(f"Successfully processed {len(apiid_data)} folders")
    print(f"Data saved to {output_path} (sorted by folder number)")

if __name__ == "__main__":
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Extract API IDs from info.json files')
    parser.add_argument('--base-dir', type=str, default='.',
                        help='Base directory containing the anime folders (default: current directory)')
    args = parser.parse_args()

    # Run the extraction
    extract_apiid(base_dir=args.base_dir)

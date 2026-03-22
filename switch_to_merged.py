#!/usr/bin/env python3
"""
Switch Mihomo Party to use the merged config (boostnet + 狗狗加速)
This properly updates the profile.yaml and triggers config reload.
"""

import os
import yaml
import shutil

CONFIG_DIR = os.path.expanduser("~/Library/Application Support/mihomo-party")
PROFILE_FILE = os.path.join(CONFIG_DIR, "profile.yaml")
WORK_CONFIG = os.path.join(CONFIG_DIR, "work", "config.yaml")

def switch_to_merged():
    # Read current profile
    with open(PROFILE_FILE, 'r') as f:
        profile = yaml.safe_load(f)
    
    print(f"Current profile: {profile.get('current', 'unknown')}")
    
    # Check if merged profile exists
    items = profile.get('items', [])
    merged_item = None
    for item in items:
        if item.get('id') == 'merged':
            merged_item = item
            break
    
    if not merged_item:
        print("Error: 'merged' profile not found in profile.yaml")
        print("Available profiles:")
        for item in items:
            print(f"  - {item.get('id')}: {item.get('name', 'unnamed')}")
        return False
    
    # Backup current work config
    if os.path.exists(WORK_CONFIG):
        backup_path = WORK_CONFIG + ".backup"
        shutil.copy2(WORK_CONFIG, backup_path)
        print(f"Backup created: {backup_path}")
    
    # Copy merged config to work directory
    merged_profile_path = os.path.join(CONFIG_DIR, "profiles", "merged.yaml")
    if not os.path.exists(merged_profile_path):
        print(f"Error: Merged config not found at {merged_profile_path}")
        return False
    
    shutil.copy2(merged_profile_path, WORK_CONFIG)
    print(f"Copied merged config to: {WORK_CONFIG}")
    
    # Update current profile
    profile['current'] = 'merged'
    
    # Write updated profile
    with open(PROFILE_FILE, 'w') as f:
        yaml.dump(profile, f, allow_unicode=True, sort_keys=False)
    
    print(f"\n✅ Switched to merged profile (狗狗加速 + BoostNet)")
    print(f"   Profile ID: merged")
    print(f"   Total nodes: ~90 (49 + 42)")
    print(f"\n⚠️  IMPORTANT: You need to restart Mihomo Party to apply the changes!")
    print(f"   1. Quit Mihomo Party (Cmd+Q)")
    print(f"   2. Reopen Mihomo Party")
    print(f"   3. Check Dashboard - should show ~90 nodes")
    
    return True

if __name__ == "__main__":
    switch_to_merged()

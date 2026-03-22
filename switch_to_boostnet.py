#!/usr/bin/env python3
"""
Switch Mihomo Party to use the merged config (boostnet + 狗狗加速)
"""

import shutil
import os

# Paths
CONFIG_DIR = os.path.expanduser("~/Library/Application Support/mihomo-party")
MERGED_CONFIG = os.path.join(CONFIG_DIR, "profiles", "19d14d141da.yaml")
ACTIVE_CONFIG = os.path.join(CONFIG_DIR, "work", "config.yaml")

def switch_config():
    if not os.path.exists(MERGED_CONFIG):
        print(f"Error: Merged config not found at {MERGED_CONFIG}")
        return False
    
    # Backup current config
    if os.path.exists(ACTIVE_CONFIG):
        backup_path = ACTIVE_CONFIG + ".backup"
        shutil.copy2(ACTIVE_CONFIG, backup_path)
        print(f"Backup created: {backup_path}")
    
    # Copy merged config to active
    shutil.copy2(MERGED_CONFIG, ACTIVE_CONFIG)
    print(f"Switched to merged config: {MERGED_CONFIG} -> {ACTIVE_CONFIG}")
    
    print("\n=== Important ===")
    print("Please RESTART Clash Party to apply the new config!")
    print("After restart, you should see ~90 nodes (狗狗加速 + BoostNet)")
    return True

if __name__ == "__main__":
    switch_config()

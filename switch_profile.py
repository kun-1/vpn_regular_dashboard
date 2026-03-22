#!/usr/bin/env python3
"""
Switch Clash Party to use the profile with both subscriptions
"""

import os
import json

CONFIG_DIR = os.path.expanduser("~/Library/Application Support/mihomo-party")
PROFILE_FILE = os.path.join(CONFIG_DIR, "profile.yaml")

# Read current profile
with open(PROFILE_FILE, 'r') as f:
    profile = f.read()

print("Current profile.yaml:")
print(profile[:500])
print("...")

# The profile we want to use
TARGET_PROFILE = "19d14d141da"  # This has both 狗狗加速 and BoostNet

# Update profile to use the merged config
new_profile = f"""id: {TARGET_PROFILE}
mode: rule
"""

with open(PROFILE_FILE, 'w') as f:
    f.write(new_profile)

print(f"\nSwitched to profile: {TARGET_PROFILE}")
print("\n=== Important ===")
print("Please RESTART Clash Party completely (Cmd+Q then reopen)")
print("After restart, Dashboard should show ~90 nodes")

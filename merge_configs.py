#!/usr/bin/env python3
"""
Merge 狗狗加速 and BoostNet configs into one
"""

import yaml
import os

CONFIG_DIR = os.path.expanduser("~/Library/Application Support/mihomo-party/profiles")

# Read both configs
with open(f"{CONFIG_DIR}/19c9983bec2.yaml", 'r') as f:
    config1 = yaml.safe_load(f)

with open(f"{CONFIG_DIR}/19d14d141da.yaml", 'r') as f:
    config2 = yaml.safe_load(f)

# Merge proxies
proxies1 = config1.get('proxies', [])
proxies2 = config2.get('proxies', [])

# Remove duplicates (by name)
all_proxies = {p['name']: p for p in proxies1}
for p in proxies2:
    if p['name'] not in all_proxies:
        all_proxies[p['name']] = p

merged_proxies = list(all_proxies.values())

print(f"Config 1 (狗狗加速): {len(proxies1)} proxies")
print(f"Config 2 (BoostNet): {len(proxies2)} proxies")
print(f"Merged (unique): {len(merged_proxies)} proxies")

# Create merged config
merged_config = config1.copy()
merged_config['proxies'] = merged_proxies

# Update proxy groups to include all nodes
proxy_names = [p['name'] for p in merged_proxies if p['name'] not in ['DIRECT', 'REJECT', 'COMPATIBLE', 'PASS']]

# Create a unified selector
merged_config['proxy-groups'] = [
    {
        'name': '🚀 统一选择',
        'type': 'url-test',
        'proxies': proxy_names,
        'url': 'http://www.gstatic.com/generate_204',
        'interval': 300
    },
    {
        'name': 'GLOBAL',
        'type': 'select',
        'proxies': ['🚀 统一选择'] + proxy_names
    }
]

# Save merged config
output_file = f"{CONFIG_DIR}/merged.yaml"
with open(output_file, 'w') as f:
    yaml.dump(merged_config, f, allow_unicode=True, sort_keys=False)

print(f"\nMerged config saved to: {output_file}")
print(f"\nTo use:")
print(f"1. In Clash Party, switch to 'merged' profile")
print(f"2. Or replace 19c9983bec2.yaml with merged.yaml")

#!/usr/bin/env python3
"""
Merge 狗狗加速 and BoostNet configs - MINIMAL VERSION
4 proxy groups:
- ♻️ 全部自动 (87 nodes)
- ♻️自动选择 (41 nodes, 狗狗加速原始)
- 🌐 手动选择
- GLOBAL
"""

import yaml
import os
import copy

CONFIG_DIR = os.path.expanduser("~/Library/Application Support/mihomo-party/profiles")

def load_config(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def save_config(path, config):
    with open(path, 'w') as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)

def merge_configs():
    # Load both configs
    config_gougou = load_config(f"{CONFIG_DIR}/19c9983bec2.yaml")
    config_boost = load_config(f"{CONFIG_DIR}/19d14d141da.yaml")

    # Extract proxies
    proxies_gougou = {p['name']: p for p in config_gougou.get('proxies', [])}
    proxies_boost = {p['name']: p for p in config_boost.get('proxies', [])}

    print(f"狗狗加速: {len(proxies_gougou)} proxies")
    print(f"BoostNet: {len(proxies_boost)} proxies")

    # Merge proxies (rename duplicates)
    all_proxies = copy.deepcopy(proxies_gougou)
    for name, proxy in proxies_boost.items():
        if name in all_proxies:
            new_name = f"[BoostNet] {name}"
            proxy = copy.deepcopy(proxy)
            proxy['name'] = new_name
            all_proxies[new_name] = proxy
        else:
            all_proxies[name] = proxy

    proxy_list = list(all_proxies.values())
    proxy_names = list(all_proxies.keys())
    gougou_names = list(proxies_gougou.keys())
    print(f"Merged total: {len(proxy_list)} unique proxies")

    # Create merged config
    merged = copy.deepcopy(config_gougou)
    merged['proxies'] = proxy_list

    # 4 proxy groups
    merged['proxy-groups'] = [
        {
            'name': '♻️ 全部自动',
            'type': 'url-test',
            'proxies': proxy_names,
            'url': 'http://www.gstatic.com/generate_204',
            'interval': 300
        },
        {
            'name': '♻️自动选择',
            'type': 'url-test',
            'proxies': gougou_names,
            'url': 'http://1.1.1.1',
            'interval': 600
        },
        {
            'name': '🌐 手动选择',
            'type': 'select',
            'proxies': ['♻️ 全部自动', '♻️自动选择'] + proxy_names
        },
        {
            'name': 'GLOBAL',
            'type': 'select',
            'proxies': ['♻️ 全部自动', '♻️自动选择', '🌐 手动选择'] + proxy_names[:20]
        }
    ]

    # Use 狗狗加速's controller settings
    merged['external-controller'] = '127.0.0.1:9097'
    merged['mixed-port'] = 7897

    # Save
    output_file = f"{CONFIG_DIR}/merged.yaml"
    save_config(output_file, merged)

    print(f"\n✅ Config saved: {output_file}")
    print(f"\nProxy groups (4 total):")
    for g in merged['proxy-groups']:
        print(f"  - {g['name']}: {g['type']} ({len(g.get('proxies', []))} items)")

    print(f"\nNext steps:")
    print(f"1. cp {output_file} ~/Library/Application\\ Support/mihomo-party/work/config.yaml")
    print(f"2. Restart Mihomo Party")
    print(f"3. Dashboard will auto-detect '♻️ 全部自动' (largest URLTest group)")

if __name__ == "__main__":
    merge_configs()

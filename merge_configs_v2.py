#!/usr/bin/env python3
"""
Merge 狗狗加速 and BoostNet configs properly
Preserves original proxy-groups structure while merging proxies
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
    config_gougou = load_config(f"{CONFIG_DIR}/19c9983bec2.yaml")  # 狗狗加速
    config_boost = load_config(f"{CONFIG_DIR}/19d14d141da.yaml")   # BoostNet

    # Extract proxies
    proxies_gougou = {p['name']: p for p in config_gougou.get('proxies', [])}
    proxies_boost = {p['name']: p for p in config_boost.get('proxies', [])}

    print(f"狗狗加速: {len(proxies_gougou)} proxies")
    print(f"BoostNet: {len(proxies_boost)} proxies")

    # Merge proxies (BoostNet takes precedence on name conflict)
    all_proxies = copy.deepcopy(proxies_gougou)
    for name, proxy in proxies_boost.items():
        if name in all_proxies:
            # Rename conflicting nodes with prefix
            new_name = f"[BoostNet] {name}"
            proxy = copy.deepcopy(proxy)
            proxy['name'] = new_name
            all_proxies[new_name] = proxy
            print(f"  Renamed duplicate: {name} -> {new_name}")
        else:
            all_proxies[name] = proxy

    proxy_list = list(all_proxies.values())
    proxy_names = list(all_proxies.keys())
    print(f"\nMerged total: {len(proxy_list)} unique proxies")

    # Create merged config based on 狗狗加速 (more complex structure)
    merged = copy.deepcopy(config_gougou)
    merged['proxies'] = proxy_list

    # Update proxy-groups to include all nodes
    # First, collect all original group names from both configs
    groups_gougou = {g['name']: g for g in config_gougou.get('proxy-groups', [])}
    groups_boost = {g['name']: g for g in config_boost.get('proxy-groups', [])}

    # Create a unified "全部节点" selector with all proxies
    all_nodes_group = {
        'name': '♻️ 全部节点',
        'type': 'url-test',
        'proxies': proxy_names,
        'url': 'http://www.gstatic.com/generate_204',
        'interval': 300
    }

    # Create subscription-specific groups
    gougou_names = list(proxies_gougou.keys())
    boost_names = list(proxies_boost.keys())
    # Also add renamed boost nodes
    boost_names_renamed = [n for n in proxy_names if n.startswith('[BoostNet]')]

    gougou_group = {
        'name': '🐕 狗狗加速',
        'type': 'url-test',
        'proxies': gougou_names,
        'url': 'http://www.gstatic.com/generate_204',
        'interval': 300
    }

    boost_group = {
        'name': '🚀 BoostNet',
        'type': 'url-test',
        'proxies': boost_names + boost_names_renamed,
        'url': 'http://www.gstatic.com/generate_204',
        'interval': 300
    }

    # Build new proxy-groups
    new_groups = []

    # 1. 全部节点 (merged all)
    new_groups.append(all_nodes_group)

    # 2. 分订阅选择器
    new_groups.append(gougou_group)
    new_groups.append(boost_group)

    # 3. 保留原始 狗狗加速的分组结构，但更新节点列表
    for name, group in groups_gougou.items():
        if name in ['♻️ 自动选择', '🔯 故障转移', '狗狗加速.com', 'AnyTLS', 'Tuic', '🔥ChatGPT', 'GLOBAL']:
            new_group = copy.deepcopy(group)
            # Update proxies list to include all nodes (filter to existing ones)
            if 'proxies' in new_group:
                # Keep the group structure but ensure all referenced proxies exist
                filtered_proxies = []
                for p in new_group['proxies']:
                    if p in all_proxies or p in ['DIRECT', 'REJECT', '♻️ 全部节点', '🐕 狗狗加速', '🚀 BoostNet']:
                        filtered_proxies.append(p)
                new_group['proxies'] = filtered_proxies
            new_groups.append(new_group)

    # 4. Add BoostNet's original selector if it exists
    if 'BoostNet' in groups_boost:
        boost_original = copy.deepcopy(groups_boost['BoostNet'])
        # Update to use renamed nodes
        if 'proxies' in boost_original:
            updated_proxies = []
            for p in boost_original['proxies']:
                if p in all_proxies:
                    updated_proxies.append(p)
                elif f"[BoostNet] {p}" in all_proxies:
                    updated_proxies.append(f"[BoostNet] {p}")
            boost_original['proxies'] = updated_proxies
        new_groups.append(boost_original)

    # Ensure GLOBAL group exists and includes all selectors
    global_exists = False
    for g in new_groups:
        if g['name'] == 'GLOBAL':
            global_exists = True
            # Add our new groups to GLOBAL
            global_proxies = g.get('proxies', [])
            for new_g in ['♻️ 全部节点', '🐕 狗狗加速', '🚀 BoostNet']:
                if new_g not in global_proxies:
                    global_proxies.insert(0, new_g)
            g['proxies'] = global_proxies
            break

    if not global_exists:
        new_groups.append({
            'name': 'GLOBAL',
            'type': 'select',
            'proxies': ['♻️ 全部节点', '🐕 狗狗加速', '🚀 BoostNet'] + proxy_names[:50]
        })

    merged['proxy-groups'] = new_groups

    # Use 狗狗加速's controller settings (9097)
    merged['external-controller'] = '127.0.0.1:9097'
    merged['mixed-port'] = 7897

    # Save
    output_file = f"{CONFIG_DIR}/merged.yaml"
    save_config(output_file, merged)

    print(f"\n✅ Merged config saved to: {output_file}")
    print(f"\nProxy groups:")
    for g in new_groups:
        print(f"  - {g['name']}: {g['type']} ({len(g.get('proxies', []))} proxies)")

    print(f"\nTo use:")
    print(f"1. Run: python3 switch_to_merged.py")
    print(f"2. Restart Mihomo Party")
    print(f"3. Dashboard should detect ~{len(proxy_list)} nodes")

if __name__ == "__main__":
    merge_configs()

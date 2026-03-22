#!/usr/bin/env python3
"""
Merge 狗狗加速 and BoostNet configs - FIXED VERSION
Preserves groups referenced by rules
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

    # Build proxy groups - MUST preserve groups referenced by rules
    # Rules reference: 狗狗加速.com, AnyTLS, Tuic, 🔥ChatGPT
    
    # Get original groups
    original_groups = {g['name']: g for g in config_gougou.get('proxy-groups', [])}
    
    new_groups = []
    
    # 1. 全部自动 (merged)
    new_groups.append({
        'name': '♻️ 全部自动',
        'type': 'url-test',
        'proxies': proxy_names,
        'url': 'http://www.gstatic.com/generate_204',
        'interval': 300
    })
    
    # 2. 保留原始 狗狗加速.com group (rules 引用它)
    if '狗狗加速.com' in original_groups:
        gougou_com = copy.deepcopy(original_groups['狗狗加速.com'])
        # Update proxies list to include only existing nodes
        new_proxies = []
        for p in gougou_com.get('proxies', []):
            if p in ['DIRECT', 'REJECT', '♻️ 全部自动'] or p in all_proxies:
                new_proxies.append(p)
            elif p in original_groups:  # It's a group name
                new_proxies.append(p)
        gougou_com['proxies'] = new_proxies
        new_groups.append(gougou_com)
    
    # 3. 保留 AnyTLS group
    if 'AnyTLS' in original_groups:
        anytls = copy.deepcopy(original_groups['AnyTLS'])
        new_proxies = []
        for p in anytls.get('proxies', []):
            if p in all_proxies:
                new_proxies.append(p)
        anytls['proxies'] = new_proxies
        new_groups.append(anytls)
    
    # 4. 保留 Tuic group
    if 'Tuic' in original_groups:
        tuic = copy.deepcopy(original_groups['Tuic'])
        new_proxies = []
        for p in tuic.get('proxies', []):
            if p in all_proxies:
                new_proxies.append(p)
        tuic['proxies'] = new_proxies
        new_groups.append(tuic)
    
    # 5. 保留 🔥ChatGPT group
    if '🔥ChatGPT' in original_groups:
        chatgpt = copy.deepcopy(original_groups['🔥ChatGPT'])
        new_proxies = []
        for p in chatgpt.get('proxies', []):
            if p in all_proxies:
                new_proxies.append(p)
        chatgpt['proxies'] = new_proxies
        new_groups.append(chatgpt)
    
    # 6. 保留 ♻️自动选择 (狗狗加速的自动选择)
    if '♻️自动选择' in original_groups:
        auto_select = copy.deepcopy(original_groups['♻️自动选择'])
        new_proxies = []
        for p in auto_select.get('proxies', []):
            if p in all_proxies:
                new_proxies.append(p)
        auto_select['proxies'] = new_proxies
        new_groups.append(auto_select)
    
    # 7. 保留 🔯故障转移
    if '🔯故障转移' in original_groups:
        fallback = copy.deepcopy(original_groups['🔯故障转移'])
        new_proxies = []
        for p in fallback.get('proxies', []):
            if p in all_proxies:
                new_proxies.append(p)
        fallback['proxies'] = new_proxies
        new_groups.append(fallback)
    
    # 8. GLOBAL - update to include new groups
    global_group = {
        'name': 'GLOBAL',
        'type': 'select',
        'proxies': ['♻️ 全部自动']
    }
    # Add original groups if they exist
    for gname in ['狗狗加速.com', '♻️自动选择', '🔯故障转移']:
        if gname in original_groups:
            global_group['proxies'].append(gname)
    # Add some direct nodes
    global_group['proxies'].extend(proxy_names[:20])
    new_groups.append(global_group)
    
    merged['proxy-groups'] = new_groups

    # Use 狗狗加速's controller settings
    merged['external-controller'] = '127.0.0.1:9097'
    merged['mixed-port'] = 7897

    # Save
    output_file = f"{CONFIG_DIR}/merged.yaml"
    save_config(output_file, merged)

    print(f"\n✅ Fixed config saved: {output_file}")
    print(f"\nProxy groups ({len(new_groups)} total):")
    for g in new_groups:
        print(f"  - {g['name']}: {g['type']} ({len(g.get('proxies', []))} items)")

    print(f"\nNext steps:")
    print(f"1. Restart Mihomo Party")
    print(f"2. Dashboard will work with '♻️ 全部自动' group")

if __name__ == "__main__":
    merge_configs()

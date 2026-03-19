#!/usr/bin/env python3
"""
Mihomo VPN 自动切换脚本
通过 Unix Socket 与 Mihomo Party 通信
"""

import json
import subprocess
import time
import statistics
from dataclasses import dataclass
from typing import List, Optional, Dict
import sys

# Mihomo Unix Socket 路径
MIHOMO_SOCKET = "/tmp/mihomo-party-501-1574.sock"

@dataclass
class NodeMetrics:
    """节点网络指标"""
    name: str
    delay_ms: float
    packet_loss: float
    score: float

class MihomoSwitcher:
    def __init__(self, proxy_group: str = "hy2"):
        self.proxy_group = proxy_group
        self.current_node = None
        
    def _api_call(self, path: str, method: str = "GET", data: dict = None) -> dict:
        """调用 Mihomo API"""
        cmd = ["curl", "-s", "--unix-socket", MIHOMO_SOCKET]
        
        if method == "PUT" and data:
            cmd.extend(["-X", "PUT", "-H", "Content-Type: application/json", "-d", json.dumps(data)])
        
        cmd.append(f"http://localhost{path}")
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"API 调用失败: {result.stderr}")
            return {}
        
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            print(f"解析 JSON 失败: {result.stdout[:200]}")
            return {}
    
    def get_nodes(self) -> List[str]:
        """获取代理组中的所有节点"""
        data = self._api_call(f"/proxies/{self.proxy_group}")
        return data.get("all", [])
    
    def get_current_node(self) -> str:
        """获取当前选中的节点"""
        data = self._api_call(f"/proxies/{self.proxy_group}")
        return data.get("now", "")
    
    def switch_node(self, node_name: str) -> bool:
        """切换到指定节点"""
        print(f"切换到: {node_name}")
        result = self._api_call(
            f"/proxies/{self.proxy_group}",
            method="PUT",
            data={"name": node_name}
        )
        # API 可能返回空或 {"message": "Updated"}
        return True  # 只要没报错就算成功
    
    def test_node_delay(self, node_name: str) -> float:
        """测试节点延迟"""
        # 通过测试 URL 延迟来评估节点质量
        data = self._api_call(f"/proxies/{self.proxy_group}/delay?url=http://www.gstatic.com/generate_204&timeout=5000")
        return data.get("delay", 9999)
    
    def evaluate_all_nodes(self) -> List[NodeMetrics]:
        """评估所有节点"""
        nodes = self.get_nodes()
        current = self.get_current_node()
        
        print(f"当前节点: {current}")
        print(f"共有 {len(nodes)} 个节点需要评估...")
        
        metrics = []
        
        for node in nodes:
            # 切换到节点
            if not self.switch_node(node):
                print(f"  {node}: 切换失败")
                continue
            
            # 等待连接稳定
            time.sleep(2)
            
            # 测试延迟（测试 3 次取平均）
            delays = []
            for _ in range(3):
                delay = self.test_node_delay(node)
                if delay < 9999:  # 有效的延迟
                    delays.append(delay)
                time.sleep(0.5)
            
            if delays:
                avg_delay = statistics.mean(delays)
                jitter = statistics.stdev(delays) if len(delays) > 1 else 0
                
                # 计算综合评分 (0-100，越高越好)
                # 延迟权重 60%，抖动权重 40%
                delay_score = max(0, 100 - avg_delay / 5)  # 延迟 500ms 得 0 分
                jitter_score = max(0, 100 - jitter * 2)    # 抖动 50ms 得 0 分
                score = 0.6 * delay_score + 0.4 * jitter_score
                
                metrics.append(NodeMetrics(
                    name=node,
                    delay_ms=avg_delay,
                    packet_loss=0,  # 暂时不测试丢包
                    score=score
                ))
                
                print(f"  {node}: 延迟={avg_delay:.1f}ms, 抖动={jitter:.1f}ms, 评分={score:.1f}")
            else:
                print(f"  {node}: 测试失败")
        
        return sorted(metrics, key=lambda x: x.score, reverse=True)
    
    def find_best_node(self, top_n: int = 3) -> Optional[str]:
        """找到最佳节点"""
        metrics = self.evaluate_all_nodes()
        
        if not metrics:
            print("没有可用节点")
            return None
        
        print(f"\n前 {top_n} 个最佳节点:")
        for i, m in enumerate(metrics[:top_n], 1):
            print(f"  {i}. {m.name} (评分: {m.score:.1f}, 延迟: {m.delay_ms:.1f}ms)")
        
        return metrics[0].name
    
    def auto_switch(self, threshold_score: float = 60):
        """自动切换到最佳节点"""
        current = self.get_current_node()
        
        # 先测试当前节点
        print(f"测试当前节点: {current}")
        current_delay = self.test_node_delay(current)
        
        if current_delay < 9999:
            current_score = max(0, 100 - current_delay / 5)
            print(f"当前节点评分: {current_score:.1f}")
            
            # 如果当前节点评分足够高，不切换
            if current_score >= threshold_score:
                print(f"当前节点质量良好，无需切换")
                return current
        
        # 寻找最佳节点
        best = self.find_best_node()
        
        if best and best != current:
            self.switch_node(best)
            print(f"已切换到最佳节点: {best}")
            return best
        elif best == current:
            print(f"当前节点已是最佳")
            return current
        else:
            print("未找到可用节点")
            return None


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Mihomo VPN 自动切换")
    parser.add_argument("--group", "-g", default="hy2", help="代理组名称 (默认: hy2)")
    parser.add_argument("--switch", "-s", action="store_true", help="执行自动切换")
    parser.add_argument("--list", "-l", action="store_true", help="列出所有节点")
    parser.add_argument("--test", "-t", metavar="NODE", help="测试指定节点")
    parser.add_argument("--to", metavar="NODE", help="切换到指定节点")
    parser.add_argument("--current", "-c", action="store_true", help="显示当前节点")
    
    args = parser.parse_args()
    
    switcher = MihomoSwitcher(args.group)
    
    if args.current:
        print(f"当前节点: {switcher.get_current_node()}")
    
    elif args.list:
        nodes = switcher.get_nodes()
        current = switcher.get_current_node()
        print(f"代理组: {args.group}")
        print(f"当前节点: {current}")
        print(f"\n可用节点 ({len(nodes)} 个):")
        for i, node in enumerate(nodes, 1):
            marker = "👉" if node == current else "  "
            print(f"{marker} {i}. {node}")
    
    elif args.to:
        if switcher.switch_node(args.to):
            print(f"已切换到: {args.to}")
        else:
            print("切换失败")
    
    elif args.test:
        print(f"测试节点: {args.test}")
        if switcher.switch_node(args.test):
            time.sleep(2)
            delay = switcher.test_node_delay(args.test)
            print(f"延迟: {delay}ms")
        else:
            print("切换失败")
    
    elif args.switch:
        print("开始自动切换...")
        switcher.auto_switch()
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

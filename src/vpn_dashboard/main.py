#!/usr/bin/env python3
"""
VPN Dashboard - Real-time VPN node monitoring and auto-switching
"""

import asyncio
import json
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="VPN Dashboard")
app.mount("/static", StaticFiles(directory="src/vpn_dashboard/static"), name="static")
templates = Jinja2Templates(directory="src/vpn_dashboard/templates")


@dataclass
class NodeMetrics:
    """Complete node metrics"""
    name: str
    delay_ms: float
    packet_loss: float
    jitter_ms: float
    bandwidth_mbps: Optional[float]
    stability_score: float
    alive: bool
    overall_score: float
    
    @property
    def status(self) -> str:
        if not self.alive or self.delay_ms >= 9999:
            return "bad"
        if self.overall_score >= 80:
            return "good"
        elif self.overall_score >= 60:
            return "warning"
        return "bad"


class NetworkTester:
    """Network testing with all metrics"""
    
    @staticmethod
    def test_bandwidth() -> Optional[float]:
        """Test bandwidth using speedtest-cli if available"""
        try:
            # Try to use speedtest-cli
            result = subprocess.run(
                ["speedtest-cli", "--simple", "--timeout", "10"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                # Parse output like: "Download: 123.45 Mbit/s"
                for line in result.stdout.split('\n'):
                    if 'Download:' in line:
                        speed_str = line.split(':')[1].strip().split()[0]
                        return float(speed_str)
        except:
            pass
        return None
    
    @staticmethod
    def ping_test(target: str = "8.8.8.8", count: int = 3) -> tuple:
        """Run ping test and return (delay_ms, packet_loss, jitter_ms)"""
        try:
            result = subprocess.run(
                ["ping", "-c", str(count), "-i", "0.2", target],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode != 0:
                return (9999, 100, 0)
            
            # Parse ping output
            lines = result.stdout.split('\n')
            delays = []
            
            for line in lines:
                if 'time=' in line:
                    try:
                        time_str = line.split('time=')[1].split()[0]
                        delays.append(float(time_str))
                    except:
                        pass
            
            if not delays:
                return (9999, 100, 0)
            
            avg_delay = sum(delays) / len(delays)
            packet_loss = (count - len(delays)) / count * 100
            
            # Calculate jitter (std deviation)
            if len(delays) > 1:
                mean = avg_delay
                variance = sum((d - mean) ** 2 for d in delays) / (len(delays) - 1)
                jitter = variance ** 0.5
            else:
                jitter = 0
            
            return (avg_delay, packet_loss, jitter)
            
        except subprocess.TimeoutExpired:
            return (9999, 100, 0)
        except Exception:
            return (9999, 100, 0)


class MihomoAPI:
    """Mihomo API wrapper"""
    BASE_URL = "http://127.0.0.1:9090"
    
    @classmethod
    def get_proxy_group(cls, group_name: str = "🚀 节点选择") -> dict:
        """Get proxy group details"""
        try:
            resp = requests.get(
                f"{cls.BASE_URL}/proxies/{requests.utils.quote(group_name)}",
                timeout=5
            )
            return resp.json() if resp.status_code == 200 else {}
        except:
            return {}
    
    @classmethod
    def get_all_proxies(cls) -> dict:
        """Get all proxies"""
        try:
            resp = requests.get(f"{cls.BASE_URL}/proxies", timeout=5)
            return resp.json().get("proxies", {}) if resp.status_code == 200 else {}
        except:
            return {}
    
    @classmethod
    def switch_node(cls, node_name: str, group_name: str = "🚀 节点选择") -> bool:
        """Switch to specified node"""
        try:
            resp = requests.put(
                f"{cls.BASE_URL}/proxies/{requests.utils.quote(group_name)}",
                json={"name": node_name},
                timeout=5
            )
            return resp.status_code == 204
        except:
            return False
    
    @classmethod
    def test_delay(cls, group_name: str = "🚀 节点选择", timeout: int = 5) -> float:
        """Test delay for current node"""
        try:
            resp = requests.get(
                f"{cls.BASE_URL}/proxies/{requests.utils.quote(group_name)}/delay",
                params={"timeout": timeout * 1000, "url": "http://www.gstatic.com/generate_204"},
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json().get("delay", 9999)
        except:
            pass
        return 9999
    
    @classmethod
    def test_node_delay(cls, node_name: str, timeout: int = 5) -> float:
        """Test delay for specific node"""
        try:
            resp = requests.get(
                f"{cls.BASE_URL}/proxies/{requests.utils.quote(node_name)}/delay",
                params={"timeout": timeout * 1000, "url": "http://www.gstatic.com/generate_204"},
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json().get("delay", 9999)
        except:
            pass
        return 9999


class VPNSwitcher:
    """Main VPN switching logic"""
    
    def __init__(self):
        self.proxy_group = "🚀 节点选择"
        self.node_metrics: dict[str, NodeMetrics] = {}
        self.latency_history: deque = deque(maxlen=60)
        self.current_node: str = ""
        self.auto_switch_enabled: bool = True
        self.last_switch_time: float = 0
        self.switch_cooldown: int = 30
        self._evaluating: bool = False
    
    def get_current_node(self) -> str:
        """Get currently selected node"""
        group = MihomoAPI.get_proxy_group(self.proxy_group)
        return group.get("now", "")
    
    def evaluate_node(self, node_name: str) -> NodeMetrics:
        """Evaluate a single node comprehensively"""
        # Test delay via API
        delay = MihomoAPI.test_node_delay(node_name)
        
        # Run ping test for detailed metrics
        ping_delay, packet_loss, jitter = NetworkTester.ping_test()
        
        # Use the worse of the two delays
        final_delay = max(delay, ping_delay)
        alive = final_delay < 9999
        
        # Test bandwidth (only for current node to save time)
        bandwidth = None
        if node_name == self.current_node:
            bandwidth = NetworkTester.test_bandwidth()
        
        # Calculate scores
        delay_score = max(0, 100 - final_delay / 5) if alive else 0
        loss_score = max(0, 100 - packet_loss * 10)
        jitter_score = max(0, 100 - jitter / 2)
        bandwidth_score = min(100, bandwidth * 2) if bandwidth else 50
        stability_score = 100 if alive else 0
        
        # Weighted overall score
        overall_score = (
            0.30 * delay_score +
            0.25 * loss_score +
            0.15 * jitter_score +
            0.20 * bandwidth_score +
            0.10 * stability_score
        )
        
        return NodeMetrics(
            name=node_name,
            delay_ms=final_delay,
            packet_loss=packet_loss,
            jitter_ms=jitter,
            bandwidth_mbps=bandwidth,
            stability_score=stability_score,
            alive=alive,
            overall_score=overall_score
        )
    
    async def evaluate_all_nodes(self):
        """Evaluate all nodes in parallel for speed"""
        if self._evaluating:
            return
        
        self._evaluating = True
        self.current_node = self.get_current_node()
        
        group = MihomoAPI.get_proxy_group(self.proxy_group)
        nodes = [n for n in group.get("all", []) if n not in ["REJECT", "DIRECT"]]
        
        # Evaluate nodes in parallel with semaphore to limit concurrency
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent tests
        
        async def evaluate_with_limit(node_name: str):
            async with semaphore:
                # Use thread pool for blocking operations
                loop = asyncio.get_event_loop()
                metrics = await loop.run_in_executor(None, self.evaluate_node, node_name)
                self.node_metrics[node_name] = metrics
                return node_name
        
        # Run all evaluations in parallel
        await asyncio.gather(*[evaluate_with_limit(n) for n in nodes])
        
        self._evaluating = False
    
    def should_switch(self) -> tuple[bool, str]:
        """Determine if we should switch to a better node"""
        if not self.auto_switch_enabled:
            return (False, "")
        
        if time.time() - self.last_switch_time < self.switch_cooldown:
            return (False, "")
        
        if not self.node_metrics:
            return (False, "")
        
        # Find best node
        best_node = max(self.node_metrics.items(), key=lambda x: x[1].overall_score)
        best_name, best_metrics = best_node
        
        current = self.node_metrics.get(self.current_node)
        if not current:
            # Current node not evaluated, switch to best if score is good enough
            if best_metrics.overall_score > 60:
                return (True, best_name)
            return (False, "")
        
        # Only switch if significantly better (score diff > 10)
        if best_metrics.overall_score - current.overall_score > 10:
            return (True, best_name)
        
        return (False, "")
    
    def switch_to_node(self, node_name: str) -> bool:
        """Switch to specified node"""
        if MihomoAPI.switch_node(node_name, self.proxy_group):
            self.current_node = node_name
            self.last_switch_time = time.time()
            return True
        return False


# Global switcher instance
switcher = VPNSwitcher()


@app.get("/")
async def index():
    return templates.TemplateResponse("index.html", {"request": {}})


@app.get("/api/current-node")
async def current_node():
    """Get current node info"""
    node_name = switcher.get_current_node()
    
    # Get cached metrics if available
    cached = switcher.node_metrics.get(node_name)
    
    if cached:
        # Use cached metrics
        delay = cached.delay_ms
        packet_loss = cached.packet_loss
        jitter = cached.jitter_ms
        bandwidth = cached.bandwidth_mbps
        score = cached.overall_score
        status = cached.status
    else:
        # Node not evaluated yet, test now
        delay = MihomoAPI.test_delay(switcher.proxy_group)
        switcher.latency_history.append({"time": time.time(), "delay": delay})
        packet_loss = 0
        jitter = 0
        bandwidth = None
        score = 0
        status = "evaluating"
    
    return {
        "node": node_name,
        "delay": delay if delay < 9999 else 0,
        "packet_loss": packet_loss,
        "jitter": jitter,
        "bandwidth": bandwidth,
        "score": score,
        "status": status,
        "auto_mode": switcher.auto_switch_enabled,
        "evaluated": cached is not None
    }


@app.get("/api/nodes")
async def nodes():
    """Return all nodes as cards"""
    current = switcher.get_current_node()
    nodes_data = list(switcher.node_metrics.values())
    
    # Sort by score
    nodes_data.sort(key=lambda x: x.overall_score, reverse=True)
    
    if not nodes_data:
        return """<div class="node-card loading" style="grid-column: 1 / -1; text-align: center; padding: 2rem;"><div style="font-size: 2rem; margin-bottom: 1rem;">⏳</div><div>正在评估所有节点...</div><div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">首次启动需要约 1-2 分钟</div></div>"""
    
    html = ''
    
    for node in nodes_data:
        is_current = node.name == current
        status_class = node.status
        status_text = {"good": "优秀", "warning": "一般", "bad": "较差"}.get(node.status, "未知")
        bw_display = f"{node.bandwidth_mbps:.0f}" if node.bandwidth_mbps else "--"
        
        html += f'''
        <div class="node-card {'current' if is_current else ''} {status_class}">
            <div class="card-header">
                <span class="node-status-dot {status_class}"></span>
                <span class="node-name">{node.name}</span>
                {'<span class="current-badge">当前</span>' if is_current else ''}
            </div>
            <div class="card-metrics">
                <div class="metric-row">
                    <div class="metric-box">
                        <span class="metric-value {status_class}">{node.delay_ms:.0f}<span class="metric-unit">ms</span></span>
                        <span class="metric-label">延迟</span>
                    </div>
                    <div class="metric-box">
                        <span class="metric-value {'bad' if node.packet_loss > 5 else 'good'}">{node.packet_loss:.1f}<span class="metric-unit">%</span></span>
                        <span class="metric-label">丢包</span>
                    </div>
                </div>
                <div class="metric-row">
                    <div class="metric-box">
                        <span class="metric-value {'bad' if node.jitter_ms > 50 else 'good'}">{node.jitter_ms:.1f}<span class="metric-unit">ms</span></span>
                        <span class="metric-label">抖动</span>
                    </div>
                    <div class="metric-box score">
                        <span class="metric-value">{node.overall_score:.0f}</span>
                        <span class="metric-label">评分</span>
                    </div>
                </div>
            </div>
            <div class="card-footer">
                <span class="status-text {status_class}">{status_text}</span>
            </div>
        </div>
        '''
    
    return HTMLResponse(content=html)


@app.get("/api/toggle-auto")
async def toggle_auto():
    switcher.auto_switch_enabled = not switcher.auto_switch_enabled
    return {"enabled": switcher.auto_switch_enabled}


@app.on_event("startup")
async def startup_event():
    """Start background tasks"""
    # Start node evaluation
    asyncio.create_task(switcher.evaluate_all_nodes())
    
    # Start auto-switching loop (every 10s)
    async def auto_switch_loop():
        await asyncio.sleep(5)  # Wait for initial evaluation
        while True:
            should_switch, best_node = switcher.should_switch()
            if should_switch:
                print(f"[AutoSwitch] Switching to {best_node}")
                switcher.switch_to_node(best_node)
            await asyncio.sleep(10)
    
    asyncio.create_task(auto_switch_loop())
    
    # Start periodic re-evaluation loop (every 60s)
    async def reevaluate_loop():
        await asyncio.sleep(60)  # First re-eval after 60s
        while True:
            print("[Re-eval] Starting periodic node re-evaluation...")
            await switcher.evaluate_all_nodes()
            print(f"[Re-eval] Completed. Evaluated {len(switcher.node_metrics)} nodes")
            await asyncio.sleep(60)
    
    asyncio.create_task(reevaluate_loop())


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()

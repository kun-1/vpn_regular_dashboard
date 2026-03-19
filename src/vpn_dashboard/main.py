#!/usr/bin/env python3
"""
VPN Dashboard - Grafana-style monitoring with card layout
Auto-switching mode enabled
"""

import json
import subprocess
import time
import statistics
import asyncio
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Configuration
MIHOMO_SOCKET = "/tmp/mihomo-party-501-1574.sock"
DATA_DIR = Path(__file__).parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

app = FastAPI(title="VPN Dashboard")

# Static files and templates
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@dataclass
class NodeMetrics:
    """Complete node metrics"""
    name: str
    delay_ms: float
    packet_loss: float
    jitter_ms: float
    stability_score: float
    alive: bool
    overall_score: float
    
    @property
    def status(self) -> str:
        if self.delay_ms > 200 or self.packet_loss > 5 or self.jitter_ms > 50:
            return "bad"
        elif self.delay_ms > 100 or self.packet_loss > 2 or self.jitter_ms > 30:
            return "warning"
        return "good"


class MihomoAPI:
    """Mihomo API client via Unix Socket"""
    
    @staticmethod
    def _call(path: str, method: str = "GET", data: dict = None) -> dict:
        cmd = ["curl", "-s", "--unix-socket", MIHOMO_SOCKET]
        if method == "PUT" and data:
            cmd.extend(["-X", "PUT", "-H", "Content-Type: application/json", "-d", json.dumps(data)])
        cmd.append(f"http://localhost{path}")
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {}
        try:
            return json.loads(result.stdout) if result.stdout else {}
        except:
            return {}
    
    @classmethod
    def get_proxy(cls, name: str) -> dict:
        return cls._call(f"/proxies/{name}")
    
    @classmethod
    def switch_proxy(cls, group: str, node: str) -> bool:
        cls._call(f"/proxies/{group}", method="PUT", data={"name": node})
        return True
    
    @classmethod
    def test_delay(cls, group: str, timeout: int = 5000) -> int:
        result = cls._call(f"/proxies/{group}/delay?timeout={timeout}")
        return result.get("delay", 9999)


class NetworkTester:
    """Network testing with all metrics"""
    
    @staticmethod
    def ping_test(target: str = "8.8.8.8", count: int = 10) -> tuple:
        try:
            result = subprocess.run(
                ["ping", "-c", str(count), "-i", "0.2", target],
                capture_output=True, text=True, timeout=30
            )
            
            times = []
            for line in result.stdout.split('\n'):
                if 'time=' in line:
                    try:
                        time_str = line.split('time=')[1].split(' ')[0]
                        times.append(float(time_str))
                    except:
                        pass
            
            packet_loss = (1 - len(times) / count) * 100
            if not times:
                return 9999, 100, 0
            
            avg_delay = statistics.mean(times)
            jitter = statistics.stdev(times) if len(times) > 1 else 0
            
            return avg_delay, packet_loss, jitter
        except:
            return 9999, 100, 0


class VPNSwitcher:
    """VPN monitoring and auto-switching"""
    
    def __init__(self, proxy_group: str = "hy2"):
        self.proxy_group = proxy_group
        self.node_metrics: Dict[str, NodeMetrics] = {}
        self.latency_history: deque = deque(maxlen=300)
        self.current_node: Optional[str] = None
        self.auto_switch_enabled = True
        self.evaluation_in_progress = False
        
        # Thresholds
        self.THRESHOLDS = {
            "delay_ms": 200,
            "packet_loss_pct": 5,
            "jitter_ms": 50,
            "score_diff": 20,
        }
    
    def get_current_node(self) -> Optional[str]:
        data = MihomoAPI.get_proxy(self.proxy_group)
        self.current_node = data.get("now")
        return self.current_node
    
    def get_all_nodes(self) -> List[str]:
        data = MihomoAPI.get_proxy(self.proxy_group)
        return data.get("all", [])
    
    def evaluate_node(self, node_name: str) -> Optional[NodeMetrics]:
        """Comprehensive evaluation of a single node"""
        # Switch to node
        MihomoAPI.switch_proxy(self.proxy_group, node_name)
        time.sleep(1.5)
        
        # Run ping test
        delay, loss, jitter = NetworkTester.ping_test(count=10)
        
        # Get Mihomo's measurement
        mihomo_delay = MihomoAPI.test_delay(self.proxy_group)
        final_delay = min(delay, mihomo_delay) if mihomo_delay < 9999 else delay
        
        # Calculate scores
        stability = max(0, 100 - loss * 10)
        delay_score = max(0, 100 - final_delay / 5)
        loss_score = max(0, 100 - loss * 20)
        jitter_score = max(0, 100 - jitter * 2)
        overall = 0.4 * delay_score + 0.3 * loss_score + 0.2 * jitter_score + 0.1 * stability
        
        return NodeMetrics(
            name=node_name,
            delay_ms=final_delay,
            packet_loss=loss,
            jitter_ms=jitter,
            stability_score=stability,
            alive=loss < 50,
            overall_score=overall
        )
    
    def evaluate_all_nodes(self) -> List[NodeMetrics]:
        """Evaluate all nodes"""
        if self.evaluation_in_progress:
            return list(self.node_metrics.values())
        
        self.evaluation_in_progress = True
        nodes = self.get_all_nodes()
        results = []
        
        print(f"Evaluating {len(nodes)} nodes...")
        for node_name in nodes:
            try:
                metrics = self.evaluate_node(node_name)
                if metrics:
                    self.node_metrics[node_name] = metrics
                    results.append(metrics)
                    print(f"  {node_name}: {metrics.delay_ms:.0f}ms, score={metrics.overall_score:.0f}")
            except Exception as e:
                print(f"  {node_name}: failed - {e}")
        
        # Sort by score
        results.sort(key=lambda x: x.overall_score, reverse=True)
        self.evaluation_in_progress = False
        return results
    
    def should_switch(self, current: NodeMetrics, best: NodeMetrics) -> tuple:
        """Determine if switch needed"""
        if current.delay_ms > self.THRESHOLDS["delay_ms"]:
            return True, f"延迟过高 ({current.delay_ms:.0f}ms)"
        if current.packet_loss > self.THRESHOLDS["packet_loss_pct"]:
            return True, f"丢包过高 ({current.packet_loss:.1f}%)"
        if current.jitter_ms > self.THRESHOLDS["jitter_ms"]:
            return True, f"抖动过高 ({current.jitter_ms:.1f}ms)"
        if best.overall_score > current.overall_score + self.THRESHOLDS["score_diff"]:
            return True, f"发现更好节点 (+{best.overall_score - current.overall_score:.0f}分)"
        return False, ""
    
    def auto_switch(self) -> Optional[str]:
        """Auto-switch to best node if needed"""
        if not self.auto_switch_enabled or self.evaluation_in_progress:
            return None
        
        current_name = self.get_current_node()
        if not current_name or not self.node_metrics:
            return None
        
        current = self.node_metrics.get(current_name)
        best = max(self.node_metrics.values(), key=lambda x: x.overall_score)
        
        if not current:
            return None
        
        should_switch, reason = self.should_switch(current, best)
        if should_switch and best.name != current_name:
            print(f"Auto-switch: {current_name} -> {best.name}: {reason}")
            MihomoAPI.switch_proxy(self.proxy_group, best.name)
            self.current_node = best.name
            return best.name
        
        return None
    
    def start_auto_mode(self):
        """Start auto-switching mode in background"""
        def run_auto():
            # Initial evaluation
            self.evaluate_all_nodes()
            
            while self.auto_switch_enabled:
                try:
                    # Full evaluation every 5 minutes
                    self.evaluate_all_nodes()
                    # Check for switch
                    self.auto_switch()
                    time.sleep(300)  # 5 minutes
                except Exception as e:
                    print(f"Auto mode error: {e}")
                    time.sleep(60)
        
        thread = threading.Thread(target=run_auto, daemon=True)
        thread.start()
        print("Auto-switching mode started")


# Global switcher
switcher = VPNSwitcher()


@app.on_event("startup")
async def startup():
    """Start auto mode on startup"""
    switcher.start_auto_mode()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/current-node")
async def current_node():
    node_name = switcher.get_current_node()
    
    # Get cached metrics if available
    cached = switcher.node_metrics.get(node_name)
    
    if cached:
        # Use cached metrics
        delay = cached.delay_ms
        packet_loss = cached.packet_loss
        jitter = cached.jitter_ms
        score = cached.overall_score
        status = cached.status
    else:
        # Node not evaluated yet, test now
        delay = MihomoAPI.test_delay(switcher.proxy_group)
        switcher.latency_history.append({"time": time.time(), "delay": delay})
        packet_loss = 0
        jitter = 0
        score = 0
        status = "evaluating"
    
    return {
        "node": node_name,
        "delay": delay if delay < 9999 else 0,
        "packet_loss": packet_loss,
        "jitter": jitter,
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
        return """<div class="cards-grid"><div class="node-card loading" style="grid-column: 1 / -1; text-align: center; padding: 2rem;"><div style="font-size: 2rem; margin-bottom: 1rem;">⏳</div><div>正在评估所有节点...</div><div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">首次启动需要约 1-2 分钟</div></div></div>"""
    
    html = '<div class="cards-grid">'
    
    for node in nodes_data:
        is_current = node.name == current
        status_class = node.status
        status_text = {"good": "优秀", "warning": "一般", "bad": "较差"}.get(node.status, "未知")
        
        html += f'''
        <div class="node-card {'current' if is_current else ''} {status_class}">
            <div class="card-header">
                <span class="node-status-dot {status_class}"></span>
                <span class="node-name">{node.name}</span>
                {'<span class="current-badge">当前</span>' if is_current else ''}
            </div>
            <div class="card-metrics">
                <div class="metric-box">
                    <span class="metric-value {status_class}">{node.delay_ms:.0f}</span>
                    <span class="metric-unit">ms</span>
                    <span class="metric-label">延迟</span>
                </div>
                <div class="metric-box">
                    <span class="metric-value {'bad' if node.packet_loss > 5 else 'good'}">{node.packet_loss:.1f}</span>
                    <span class="metric-unit">%</span>
                    <span class="metric-label">丢包</span>
                </div>
                <div class="metric-box">
                    <span class="metric-value {'bad' if node.jitter_ms > 50 else 'good'}">{node.jitter_ms:.1f}</span>
                    <span class="metric-unit">ms</span>
                    <span class="metric-label">抖动</span>
                </div>
                <div class="metric-box score">
                    <span class="metric-value">{node.overall_score:.0f}</span>
                    <span class="metric-label">评分</span>
                </div>
            </div>
            <div class="card-footer">
                <span class="status-text {status_class}">{status_text}</span>
            </div>
        </div>
        '''
    
    html += '</div>'
    return HTMLResponse(content=html)


@app.get("/api/stream")
async def stream():
    async def event_generator():
        while True:
            delay = MihomoAPI.test_delay(switcher.proxy_group)
            switcher.latency_history.append({"time": time.time(), "delay": delay})
            
            data = {
                "latency_history": list(switcher.latency_history)[-60:],
                "current_delay": delay,
                "current_node": switcher.current_node,
                "node_count": len(switcher.node_metrics),
                "timestamp": time.time()
            }
            
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(1)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@app.get("/api/toggle-auto")
async def toggle_auto():
    switcher.auto_switch_enabled = not switcher.auto_switch_enabled
    return {"enabled": switcher.auto_switch_enabled}


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()

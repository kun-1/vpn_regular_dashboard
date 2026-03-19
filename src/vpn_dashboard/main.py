#!/usr/bin/env python3
"""
VPN Dashboard - Grafana-style monitoring for Mihomo
Complete evaluation system with all metrics
"""

import json
import subprocess
import time
import statistics
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Configuration
MIHOMO_SOCKET = "/tmp/mihomo-party-501-1574.sock"
DATA_DIR = Path(__file__).parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
HISTORY_FILE = DATA_DIR / "vpn_history.jsonl"
METRICS_FILE = DATA_DIR / "node_metrics.json"

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
    bandwidth_mbps: Optional[float]
    stability_score: float
    alive: bool
    history: List[Dict]
    last_tested: float
    
    @property
    def overall_score(self) -> float:
        """Calculate overall score (0-100)"""
        # Weights from toread document
        delay_score = max(0, 100 - self.delay_ms / 5)  # 40% weight
        loss_score = max(0, 100 - self.packet_loss * 20)  # 30% weight
        jitter_score = max(0, 100 - self.jitter_ms * 2)  # 20% weight
        stability_weight = self.stability_score * 0.1  # 10% weight
        
        return (
            0.4 * delay_score +
            0.3 * loss_score +
            0.2 * jitter_score +
            0.1 * stability_weight
        )
    
    @property
    def status(self) -> str:
        """Get status based on thresholds"""
        if self.delay_ms > 200 or self.packet_loss > 5 or self.jitter_ms > 50:
            return "bad"
        elif self.delay_ms > 100 or self.packet_loss > 2 or self.jitter_ms > 30:
            return "warning"
        return "good"


class MihomoAPI:
    """Mihomo API client via Unix Socket"""
    
    @staticmethod
    def _call(path: str, method: str = "GET", data: dict = None) -> dict:
        """Call Mihomo API via curl"""
        cmd = ["curl", "-s", "--unix-socket", MIHOMO_SOCKET]
        
        if method == "PUT" and data:
            cmd.extend(["-X", "PUT", "-H", "Content-Type: application/json", "-d", json.dumps(data)])
        
        cmd.append(f"http://localhost{path}")
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return {}
        
        try:
            return json.loads(result.stdout) if result.stdout else {}
        except json.JSONDecodeError:
            return {}
    
    @classmethod
    def get_proxy(cls, name: str) -> dict:
        return cls._call(f"/proxies/{name}")
    
    @classmethod
    def switch_proxy(cls, group: str, node: str) -> bool:
        cls._call(f"/proxies/{group}", method="PUT", data={"name": node})
        return True
    
    @classmethod
    def test_delay(cls, group: str, url: str = "http://www.gstatic.com/generate_204", timeout: int = 5000) -> int:
        result = cls._call(f"/proxies/{group}/delay?url={url}&timeout={timeout}")
        return result.get("delay", 9999)


class NetworkTester:
    """Advanced network testing with all metrics"""
    
    @staticmethod
    def ping_test(target: str = "8.8.8.8", count: int = 10) -> Tuple[float, float, float]:
        """
        Test latency, packet loss, and jitter
        Returns: (avg_delay_ms, packet_loss_pct, jitter_ms)
        """
        try:
            result = subprocess.run(
                ["ping", "-c", str(count), "-i", "0.2", target],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            lines = result.stdout.split('\n')
            times = []
            
            for line in lines:
                if 'time=' in line:
                    try:
                        time_str = line.split('time=')[1].split(' ')[0]
                        times.append(float(time_str))
                    except:
                        pass
            
            # Calculate metrics
            packet_loss = (1 - len(times) / count) * 100
            
            if not times:
                return 9999, 100, 0
            
            avg_delay = statistics.mean(times)
            jitter = statistics.stdev(times) if len(times) > 1 else 0
            
            return avg_delay, packet_loss, jitter
            
        except Exception as e:
            print(f"Ping test failed: {e}")
            return 9999, 100, 0
    
    @staticmethod
    def test_node_comprehensive(node_name: str, proxy_group: str = "hy2") -> Optional[NodeMetrics]:
        """Comprehensive test of a node with all metrics"""
        # Switch to node
        MihomoAPI.switch_proxy(proxy_group, node_name)
        time.sleep(2)  # Wait for connection
        
        # Run ping test
        delay, loss, jitter = NetworkTester.ping_test(count=20)
        
        # Get Mihomo's own delay measurement
        mihomo_delay = MihomoAPI.test_delay(proxy_group)
        
        # Use the better measurement
        final_delay = min(delay, mihomo_delay) if mihomo_delay < 9999 else delay
        
        # Calculate stability (based on packet loss)
        stability = max(0, 100 - loss * 10)
        
        return NodeMetrics(
            name=node_name,
            delay_ms=final_delay,
            packet_loss=loss,
            jitter_ms=jitter,
            bandwidth_mbps=None,  # Would need speedtest
            stability_score=stability,
            alive=loss < 50,
            history=[],
            last_tested=time.time()
        )


class VPNSwitcher:
    """VPN monitoring and auto-switching"""
    
    # Thresholds from toread document
    THRESHOLDS = {
        "delay_ms": 200,
        "packet_loss_pct": 5,
        "jitter_ms": 50,
        "reconnect_count": 3,
        "evaluation_interval": 600,  # 10 minutes
    }
    
    def __init__(self, proxy_group: str = "hy2"):
        self.proxy_group = proxy_group
        self.latency_history: deque = deque(maxlen=300)
        self.node_metrics_cache: Dict[str, NodeMetrics] = {}
        self.reconnect_events: deque = deque(maxlen=100)
        self.last_evaluation = 0
        self.current_node: Optional[str] = None
        
        # Load cached metrics
        self._load_metrics()
    
    def _load_metrics(self):
        """Load cached node metrics"""
        if METRICS_FILE.exists():
            try:
                data = json.loads(METRICS_FILE.read_text())
                for name, m in data.items():
                    self.node_metrics_cache[name] = NodeMetrics(**m)
            except:
                pass
    
    def _save_metrics(self):
        """Save node metrics to cache"""
        data = {name: asdict(m) for name, m in self.node_metrics_cache.items()}
        METRICS_FILE.write_text(json.dumps(data, default=str))
    
    def get_current_node(self) -> Optional[str]:
        """Get current active node"""
        data = MihomoAPI.get_proxy(self.proxy_group)
        self.current_node = data.get("now")
        return self.current_node
    
    def get_all_nodes(self) -> List[str]:
        """Get all available nodes"""
        data = MihomoAPI.get_proxy(self.proxy_group)
        return data.get("all", [])
    
    def evaluate_all_nodes(self, force: bool = False) -> List[NodeMetrics]:
        """Evaluate all nodes with comprehensive metrics"""
        nodes = self.get_all_nodes()
        current = self.get_current_node()
        
        print(f"Evaluating {len(nodes)} nodes...")
        results = []
        
        for node_name in nodes:
            # Check if we have recent cached metrics
            cached = self.node_metrics_cache.get(node_name)
            if cached and not force and (time.time() - cached.last_tested) < 300:
                results.append(cached)
                continue
            
            # Test node
            metrics = NetworkTester.test_node_comprehensive(node_name, self.proxy_group)
            if metrics:
                self.node_metrics_cache[node_name] = metrics
                results.append(metrics)
                print(f"  {node_name}: delay={metrics.delay_ms:.1f}ms, loss={metrics.packet_loss:.1f}%, jitter={metrics.jitter_ms:.1f}ms, score={metrics.overall_score:.1f}")
        
        # Save updated metrics
        self._save_metrics()
        
        # Sort by overall score
        results.sort(key=lambda x: x.overall_score, reverse=True)
        return results
    
    def should_switch(self, current: NodeMetrics, best: NodeMetrics) -> Tuple[bool, str]:
        """Determine if we should switch to a better node"""
        # Check if current node is below thresholds
        if current.delay_ms > self.THRESHOLDS["delay_ms"]:
            return True, f"延迟过高 ({current.delay_ms:.0f}ms > {self.THRESHOLDS['delay_ms']}ms)"
        
        if current.packet_loss > self.THRESHOLDS["packet_loss_pct"]:
            return True, f"丢包率过高 ({current.packet_loss:.1f}% > {self.THRESHOLDS['packet_loss_pct']}%)"
        
        if current.jitter_ms > self.THRESHOLDS["jitter_ms"]:
            return True, f"抖动过高 ({current.jitter_ms:.1f}ms > {self.THRESHOLDS['jitter_ms']}ms)"
        
        # Check if best node is significantly better
        if best.overall_score > current.overall_score + 20:
            return True, f"发现明显更好的节点 (评分: {best.overall_score:.1f} vs {current.overall_score:.1f})"
        
        return False, ""
    
    def auto_switch(self) -> Optional[str]:
        """Automatically switch to best node if needed"""
        current_name = self.get_current_node()
        if not current_name:
            return None
        
        # Evaluate all nodes
        all_nodes = self.evaluate_all_nodes()
        if not all_nodes:
            return None
        
        # Find current and best nodes
        current = next((n for n in all_nodes if n.name == current_name), None)
        best = all_nodes[0]
        
        if not current:
            return None
        
        # Check if we should switch
        should_switch, reason = self.should_switch(current, best)
        
        if should_switch and best.name != current_name:
            print(f"Switching from {current_name} to {best.name}: {reason}")
            MihomoAPI.switch_proxy(self.proxy_group, best.name)
            self.log_event("switch", {
                "from": current_name,
                "to": best.name,
                "reason": reason,
                "from_score": current.overall_score,
                "to_score": best.overall_score
            })
            return best.name
        
        return None
    
    def test_current_delay(self) -> int:
        """Test current node delay"""
        return MihomoAPI.test_delay(self.proxy_group)
    
    def record_metrics(self, delay: int):
        """Record latency to history"""
        self.latency_history.append({
            "time": time.time(),
            "delay": delay
        })
    
    def get_latency_history(self) -> List[int]:
        """Get latency history for chart"""
        return [h["delay"] for h in self.latency_history if h["delay"] < 9999]
    
    def log_event(self, event_type: str, details: dict):
        """Log event to file"""
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            **details
        }
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(event) + "\n")
    
    def get_recent_logs(self, n: int = 20) -> List[dict]:
        """Get recent log entries"""
        if not HISTORY_FILE.exists():
            return []
        
        lines = HISTORY_FILE.read_text().strip().split("\n")
        logs = []
        for line in lines[-n:]:
            try:
                logs.append(json.loads(line))
            except:
                pass
        return logs[::-1]


# Global switcher instance
switcher = VPNSwitcher()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main dashboard page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/current-node")
async def current_node():
    """Get current node with full metrics"""
    node_name = switcher.get_current_node()
    delay = switcher.test_current_delay()
    switcher.record_metrics(delay)
    
    # Get cached metrics if available
    cached = switcher.node_metrics_cache.get(node_name)
    
    # Determine status
    if delay < 100:
        status = "good"
        status_text = "优秀"
    elif delay < 200:
        status = "good"
        status_text = "良好"
    elif delay < 300:
        status = "warning"
        status_text = "一般"
    else:
        status = "bad"
        status_text = "较差"
    
    return {
        "node": node_name,
        "delay": delay if delay < 9999 else None,
        "packet_loss": cached.packet_loss if cached else 0,
        "jitter": cached.jitter_ms if cached else 0,
        "score": cached.overall_score if cached else 0,
        "status": status,
        "status_text": status_text
    }


@app.get("/api/nodes")
async def nodes():
    """Get all nodes with comprehensive metrics"""
    current = switcher.get_current_node()
    
    # Use cached metrics
    nodes_data = list(switcher.node_metrics_cache.values())
    
    # If no cached data, get basic info from Mihomo
    if not nodes_data:
        proxy_data = MihomoAPI.get_proxy(switcher.proxy_group)
        for name in proxy_data.get("all", [])[:50]:  # Limit to 50
            extra = proxy_data.get("extra", {}).get(name, {})
            history = extra.get("history", [])
            delay = history[-1].get("delay", 9999) if history else 9999
            
            nodes_data.append(NodeMetrics(
                name=name,
                delay_ms=delay,
                packet_loss=0,
                jitter_ms=0,
                bandwidth_mbps=None,
                stability_score=100,
                alive=extra.get("alive", False),
                history=history[-5:] if history else [],
                last_tested=0
            ))
    
    # Sort by score
    nodes_data.sort(key=lambda x: x.overall_score, reverse=True)
    
    html = ""
    for node in nodes_data:
        is_current = node.name == current
        
        # Status indicator
        status_emoji = {"good": "🟢", "warning": "🟡", "bad": "🔴"}.get(node.status, "⚪")
        
        # Delay bar
        bar_width = min(100, (node.delay_ms / 500) * 100) if node.delay_ms > 0 else 0
        
        row_class = "current" if is_current else ""
        
        html += f"""
        <tr class="{row_class}">
            <td>{status_emoji}</td>
            <td>{node.name}</td>
            <td>
                <div class="delay-bar">
                    <span class="delay-value">{node.delay_ms:.0f}ms</span>
                    <div class="delay-visual">
                        <div class="delay-fill {node.status}" style="width: {bar_width}%"></div>
                    </div>
                </div>
            </td>
            <td>{node.packet_loss:.1f}%</td>
            <td>{node.jitter_ms:.1f}ms</td>
            <td>{node.overall_score:.0f}</td>
        </tr>
        """
    
    return html


@app.get("/api/evaluate")
async def evaluate_nodes():
    """Trigger full evaluation of all nodes"""
    asyncio.create_task(asyncio.to_thread(switcher.evaluate_all_nodes, force=True))
    return {"status": "started"}


@app.get("/api/auto-switch")
async def trigger_auto_switch():
    """Trigger auto-switch if needed"""
    result = await asyncio.to_thread(switcher.auto_switch)
    return {"switched_to": result}


@app.get("/api/logs")
async def logs():
    """Get recent logs"""
    logs_data = switcher.get_recent_logs(20)
    
    html = ""
    for log in logs_data:
        time_str = log.get("timestamp", "")[11:19]
        event_type = log.get("type", "unknown")
        
        if event_type == "switch":
            event_class = "switch"
            text = f"🔄 {log.get('from', '?')} → {log.get('to', '?')}: {log.get('reason', '')}"
        elif event_type == "test":
            event_class = ""
            text = f"📊 {log.get('node', '?')}: 评分 {log.get('score', 0):.1f}"
        else:
            event_class = ""
            text = str(log)
        
        html += f"""
        <div class="log-entry">
            <span class="log-time">{time_str}</span>
            <span class="log-event {event_class}">{text}</span>
        </div>
        """
    
    return html


@app.get("/api/stream")
async def stream():
    """SSE endpoint for real-time updates"""
    async def event_generator():
        while True:
            delay = switcher.test_current_delay()
            switcher.record_metrics(delay)
            
            # Auto-switch check every 60 seconds
            if len(switcher.latency_history) % 60 == 0:
                switcher.auto_switch()
            
            data = {
                "latency_history": list(switcher.latency_history)[-60:],
                "current_delay": delay,
                "current_node": switcher.current_node,
                "timestamp": time.time()
            }
            
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(1)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


def main():
    """Run the server"""
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()

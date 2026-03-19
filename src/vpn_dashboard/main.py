#!/usr/bin/env python3
"""
VPN Dashboard - Grafana-style monitoring for Mihomo
"""

import json
import subprocess
import time
import statistics
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio

# Configuration
MIHOMO_SOCKET = "/tmp/mihomo-party-501-1574.sock"
DATA_DIR = Path(__file__).parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
HISTORY_FILE = DATA_DIR / "vpn_history.jsonl"

app = FastAPI(title="VPN Dashboard")

# Static files and templates
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@dataclass
class NodeMetrics:
    name: str
    delay_ms: float
    alive: bool
    history: List[Dict]


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
    def get_proxies(cls) -> dict:
        return cls._call("/proxies")
    
    @classmethod
    def get_proxy(cls, name: str) -> dict:
        return cls._call(f"/proxies/{name}")
    
    @classmethod
    def switch_proxy(cls, group: str, node: str) -> bool:
        result = cls._call(f"/proxies/{group}", method="PUT", data={"name": node})
        return True  # API returns empty on success
    
    @classmethod
    def test_delay(cls, group: str, url: str = "http://www.gstatic.com/generate_204", timeout: int = 5000) -> int:
        result = cls._call(f"/proxies/{group}/delay?url={url}&timeout={timeout}")
        return result.get("delay", 9999)


class VPNSwitcher:
    """VPN auto-switching logic"""
    
    def __init__(self, proxy_group: str = "hy2"):
        self.proxy_group = proxy_group
        self.latency_history: List[Dict] = []
        self.max_history = 300  # 5 minutes at 1 sample/sec
    
    def get_current_node(self) -> Optional[str]:
        """Get current active node"""
        data = MihomoAPI.get_proxy(self.proxy_group)
        return data.get("now")
    
    def get_all_nodes(self) -> List[str]:
        """Get all available nodes"""
        data = MihomoAPI.get_proxy(self.proxy_group)
        return data.get("all", [])
    
    def get_node_details(self) -> List[NodeMetrics]:
        """Get detailed info for all nodes"""
        data = MihomoAPI.get_proxy(self.proxy_group)
        nodes = []
        
        for name in data.get("all", [])[:20]:  # Limit to 20 for performance
            node_data = data.get("extra", {}).get(name, {})
            history = node_data.get("history", [])
            
            # Get latest delay
            delay = 9999
            if history:
                latest = history[-1]
                delay = latest.get("delay", 9999)
            
            nodes.append(NodeMetrics(
                name=name,
                delay_ms=delay,
                alive=node_data.get("alive", False),
                history=history[-5:] if history else []
            ))
        
        # Sort by delay
        nodes.sort(key=lambda x: x.delay_ms if x.delay_ms > 0 else 9999)
        return nodes
    
    def test_current_delay(self) -> int:
        """Test current node delay"""
        return MihomoAPI.test_delay(self.proxy_group)
    
    def record_metrics(self, delay: int):
        """Record latency to history"""
        self.latency_history.append({
            "time": time.time(),
            "delay": delay
        })
        # Keep only recent history
        if len(self.latency_history) > self.max_history:
            self.latency_history = self.latency_history[-self.max_history:]
    
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
        return logs[::-1]  # Reverse to show newest first


# Global switcher instance
switcher = VPNSwitcher()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main dashboard page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/current-node")
async def current_node():
    """Get current node info"""
    node = switcher.get_current_node()
    delay = switcher.test_current_delay()
    switcher.record_metrics(delay)
    
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
        "node": node,
        "delay": delay if delay < 9999 else None,
        "status": status,
        "status_text": status_text
    }


@app.get("/api/nodes")
async def nodes():
    """Get all nodes with metrics"""
    current = switcher.get_current_node()
    nodes_data = switcher.get_node_details()
    
    html = ""
    for node in nodes_data:
        is_current = node.name == current
        
        # Determine delay color
        if node.delay_ms < 100:
            delay_class = "good"
        elif node.delay_ms < 200:
            delay_class = "warning"
        else:
            delay_class = "bad"
        
        # Calculate delay bar width (max 500ms)
        bar_width = min(100, (node.delay_ms / 500) * 100) if node.delay_ms > 0 else 0
        
        row_class = "current" if is_current else ""
        status_dot = "🟢" if node.alive else "🔴"
        
        html += f"""
        <tr class="{row_class}">
            <td>{status_dot}</td>
            <td>{node.name}</td>
            <td>
                <div class="delay-bar">
                    <span class="delay-value">{node.delay_ms}ms</span>
                    <div class="delay-visual">
                        <div class="delay-fill {delay_class}" style="width: {bar_width}%"></div>
                    </div>
                </div>
            </td>
            <td>{"📈" if len(node.history) > 1 and node.history[-1]["delay"] < node.history[0]["delay"] else "📉"}</td>
        </tr>
        """
    
    return html


@app.get("/api/logs")
async def logs():
    """Get recent logs"""
    logs_data = switcher.get_recent_logs(10)
    
    html = ""
    for log in logs_data:
        time_str = log.get("timestamp", "")[11:19]  # Extract HH:MM:SS
        event_type = log.get("type", "unknown")
        
        if event_type == "switch":
            event_class = "switch"
            text = f"自动切换到 {log.get('to', 'unknown')} ({log.get('delay', 0)}ms)"
        elif event_type == "test":
            event_class = ""
            text = f"延迟测试: {log.get('node', 'unknown')} = {log.get('delay', 0)}ms"
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
            
            data = {
                "latency_history": switcher.get_latency_history()[-60:],  # Last 60 points
                "current_delay": delay,
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

#!/usr/bin/env python3
"""
VPN Dashboard - Real-time VPN node monitoring and auto-switching
Fixed: IP detection, location display, verified switching, improved scoring
"""

import asyncio
import json
import subprocess
import time
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="VPN Dashboard")
app.mount("/static", StaticFiles(directory="src/vpn_dashboard/static"), name="static")
templates = Jinja2Templates(directory="src/vpn_dashboard/templates")


@dataclass
class IPInfo:
    """IP address with geolocation info"""
    ip: str
    country: str
    country_code: str
    region: str
    city: str
    isp: str
    lat: float = 0.0
    lon: float = 0.0
    
    @property
    def location_str(self) -> str:
        if self.city and self.country:
            return f"{self.city}, {self.country}"
        return self.country or "Unknown"
    
    @property
    def flag(self) -> str:
        """Convert country code to emoji flag"""
        if len(self.country_code) == 2:
            return "".join(chr(ord(c) + 127397) for c in self.country_code.upper())
        return "🌐"


@dataclass
class NodeMetrics:
    """Complete node metrics with IP info"""
    name: str
    delay_ms: float
    packet_loss: float
    jitter_ms: float
    bandwidth_mbps: Optional[float]
    stability_score: float
    alive: bool
    overall_score: float
    ip_info: Optional[IPInfo] = None
    history: List[Dict] = field(default_factory=list)
    
    @property
    def status(self) -> str:
        if not self.alive or self.delay_ms >= 9999:
            return "bad"
        if self.overall_score >= 80:
            return "good"
        elif self.overall_score >= 60:
            return "warning"
        return "bad"


class IPDetector:
    """Detect real exit IP and geolocation"""
    
    IP_APIS = [
        "https://ipapi.co/json/",
        "https://ipwho.is/",
        "https://api.ip.sb/geoip",
    ]
    
    @classmethod
    def get_current_ip(cls, proxy_url: Optional[str] = None) -> Optional[IPInfo]:
        """Get current exit IP with geolocation"""
        for api_url in cls.IP_APIS:
            try:
                proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
                resp = requests.get(api_url, proxies=proxies, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    
                    # Handle different API formats
                    if "ip" in data:
                        return IPInfo(
                            ip=data.get("ip", ""),
                            country=data.get("country_name", data.get("country", "")),
                            country_code=data.get("country_code", data.get("countryCode", "")),
                            region=data.get("region", ""),
                            city=data.get("city", ""),
                            isp=data.get("org", data.get("isp", "")),
                            lat=float(data.get("latitude", 0) or 0),
                            lon=float(data.get("longitude", 0) or 0),
                        )
            except Exception as e:
                print(f"[IPDetector] {api_url} failed: {e}")
                continue
        return None
    
    @classmethod
    def test_node_ip(cls, node_name: str, base_url: str = "http://127.0.0.1:9090") -> Optional[IPInfo]:
        """Test IP for a specific node by temporarily switching to it"""
        try:
            # Get current node to restore later
            current_resp = requests.get(f"{base_url}/proxies/🚀%20节点选择", timeout=5)
            current_node = current_resp.json().get("now", "") if current_resp.status_code == 200 else ""
            
            # Switch to target node
            switch_resp = requests.put(
                f"{base_url}/proxies/🚀%20节点选择",
                json={"name": node_name},
                timeout=5
            )
            
            if switch_resp.status_code != 204:
                return None
            
            # Wait for connection to establish
            time.sleep(2)
            
            # Get IP through the proxy
            proxy_url = f"http://127.0.0.1:7890"  # Default Mihomo mixed port
            ip_info = cls.get_current_ip(proxy_url)
            
            # Restore original node
            if current_node:
                requests.put(
                    f"{base_url}/proxies/🚀%20节点选择",
                    json={"name": current_node},
                    timeout=5
                )
            
            return ip_info
            
        except Exception as e:
            print(f"[IPDetector] Test node {node_name} failed: {e}")
            return None


class NetworkTester:
    """Network testing with all metrics"""
    
    @staticmethod
    def test_bandwidth() -> Optional[float]:
        """Test bandwidth using speedtest-cli if available"""
        try:
            result = subprocess.run(
                ["speedtest-cli", "--simple", "--timeout", "10"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'Download:' in line:
                        speed_str = line.split(':')[1].strip().split()[0]
                        return float(speed_str)
        except:
            pass
        return None
    
    @staticmethod
    def ping_test(target: str = "8.8.8.8", count: int = 5) -> tuple:
        """Run ping test and return (delay_ms, packet_loss, jitter_ms)"""
        try:
            result = subprocess.run(
                ["ping", "-c", str(count), "-i", "0.2", target],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode != 0:
                return (9999, 100, 0)
            
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
    """Mihomo API wrapper - supports both TCP and Unix socket"""
    BASE_URL = "http://127.0.0.1:9090"
    SOCKET_PATH = "/tmp/mihomo-party-501-1574.sock"
    PROXY_PORT = "7890"
    _use_socket: Optional[bool] = None
    
    @classmethod
    def _use_unix_socket(cls) -> bool:
        """Detect if we should use Unix socket"""
        if cls._use_socket is None:
            import socket
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2)
                sock.connect(cls.SOCKET_PATH)
                sock.close()
                cls._use_socket = True
                print(f"[MihomoAPI] Using Unix socket: {cls.SOCKET_PATH}")
            except Exception as e:
                cls._use_socket = False
                print(f"[MihomoAPI] Using TCP: {cls.BASE_URL} (socket failed: {e})")
        return cls._use_socket
    
    @classmethod
    def _request(cls, method: str, path: str, **kwargs) -> Optional[requests.Response]:
        """Make request using appropriate transport"""
        
        if cls._use_unix_socket():
            # Use Unix socket via requests_unixsocket
            try:
                import requests_unixsocket
                # Encode socket path for URL
                encoded_path = cls.SOCKET_PATH.replace('/', '%2F')
                url = f"http+unix://{encoded_path}{path}"
                
                session = requests_unixsocket.Session()
                return session.request(method, url, **kwargs)
            except ImportError:
                print("[MihomoAPI] requests-unixsocket not installed, falling back to raw socket")
                return cls._raw_socket_request(method, path, **kwargs)
            except Exception as e:
                print(f"[MihomoAPI] Unix socket request failed: {e}")
                return None
        else:
            # Use regular HTTP
            url = f"{cls.BASE_URL}{path}"
            try:
                return requests.request(method, url, **kwargs)
            except Exception as e:
                print(f"[MihomoAPI] HTTP request failed: {e}")
                return None
    
    @classmethod
    def _raw_socket_request(cls, method: str, path: str, **kwargs) -> Optional[requests.Response]:
        """Fallback raw socket implementation"""
        import socket
        from urllib.parse import urlencode, parse_qs, urlparse
        import json
        
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(kwargs.get('timeout', 5))
            sock.connect(cls.SOCKET_PATH)
            
            # Build path with query params
            full_path = path
            if 'params' in kwargs:
                full_path += '?' + urlencode(kwargs['params'])
            
            # Build body
            body = b''
            if 'json' in kwargs:
                body = json.dumps(kwargs['json']).encode()
            
            # Build HTTP request
            lines = [f"{method} {full_path} HTTP/1.1", "Host: localhost", "Connection: close"]
            if body:
                lines.append("Content-Type: application/json")
                lines.append(f"Content-Length: {len(body)}")
            lines.append("")
            
            request = "\r\n".join(lines).encode()
            if body:
                request += body
            
            sock.sendall(request)
            
            # Read response
            response_data = b""
            sock.settimeout(kwargs.get('timeout', 5))
            while True:
                try:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    response_data += chunk
                except socket.timeout:
                    break
            
            sock.close()
            
            # Parse response
            header_end = response_data.find(b"\r\n\r\n")
            if header_end == -1:
                return None
            
            body = response_data[header_end + 4:]
            headers = response_data[:header_end].decode('utf-8', errors='ignore')
            status_line = headers.split("\r\n")[0]
            status_code = int(status_line.split()[1])
            
            # Create mock response
            class MockResponse:
                def __init__(self, status_code, body):
                    self.status_code = status_code
                    self._body = body
                
                def json(self):
                    return json.loads(self._body.decode('utf-8', errors='ignore'))
            
            return MockResponse(status_code, body)
            
        except Exception as e:
            print(f"[MihomoAPI] Raw socket request failed: {e}")
            return None
    
    @classmethod
    def get_proxy_group(cls, group_name: str = "🚀 节点选择") -> dict:
        """Get proxy group details"""
        try:
            from urllib.parse import quote
            resp = cls._request("GET", f"/proxies/{quote(group_name, safe='')}", timeout=5)
            if resp and resp.status_code == 200:
                return resp.json()
        except Exception as e:
            print(f"[MihomoAPI] get_proxy_group failed: {e}")
        return {}
    
    @classmethod
    def get_all_proxies(cls) -> dict:
        """Get all proxies"""
        try:
            resp = cls._request("GET", "/proxies", timeout=5)
            if resp and resp.status_code == 200:
                return resp.json().get("proxies", {})
        except Exception as e:
            print(f"[MihomoAPI] get_all_proxies failed: {e}")
        return {}
    
    @classmethod
    def switch_node(cls, node_name: str, group_name: str = "🚀 节点选择") -> bool:
        """Switch to specified node"""
        try:
            from urllib.parse import quote
            resp = cls._request(
                "PUT", 
                f"/proxies/{quote(group_name, safe='')}",
                json={"name": node_name},
                timeout=5
            )
            return resp is not None and resp.status_code == 204
        except Exception as e:
            print(f"[MihomoAPI] switch_node failed: {e}")
            return False
    
    @classmethod
    def test_delay(cls, group_name: str = "🚀 节点选择", timeout: int = 5) -> float:
        """Test delay for current node"""
        try:
            from urllib.parse import quote
            resp = cls._request(
                "GET",
                f"/proxies/{quote(group_name, safe='')}/delay",
                params={"timeout": timeout * 1000, "url": "http://www.gstatic.com/generate_204"},
                timeout=10
            )
            if resp and resp.status_code == 200:
                return resp.json().get("delay", 9999)
        except Exception as e:
            print(f"[MihomoAPI] test_delay failed: {e}")
        return 9999
    
    @classmethod
    def test_node_delay(cls, node_name: str, timeout: int = 5) -> float:
        """Test delay for specific node"""
        try:
            from urllib.parse import quote
            resp = cls._request(
                "GET",
                f"/proxies/{quote(node_name, safe='')}/delay",
                params={"timeout": timeout * 1000, "url": "http://www.gstatic.com/generate_204"},
                timeout=10
            )
            if resp and resp.status_code == 200:
                return resp.json().get("delay", 9999)
        except Exception as e:
            print(f"[MihomoAPI] test_node_delay failed: {e}")
        return 9999
    
    @classmethod
    def get_proxy_port(cls) -> str:
        """Get proxy port from configs"""
        try:
            resp = cls._request("GET", "/configs", timeout=5)
            if resp and resp.status_code == 200:
                config = resp.json()
                return str(config.get("mixed-port", config.get("port", "7890")))
        except Exception as e:
            print(f"[MihomoAPI] get_proxy_port failed: {e}")
        return "7890"


class VPNSwitcher:
    """Main VPN switching logic with IP verification"""
    
    def __init__(self):
        self.proxy_group = "🚀 节点选择"
        self.node_metrics: dict[str, NodeMetrics] = {}
        self.latency_history: deque = deque(maxlen=100)
        self.current_node: str = ""
        self.current_ip_info: Optional[IPInfo] = None
        self.auto_switch_enabled: bool = True
        self.last_switch_time: float = 0
        self.switch_cooldown: int = 30
        self._evaluating: bool = False
        self._switch_verifying: bool = False
        self.proxy_port: str = "7890"
        self.node_ip_cache: dict[str, IPInfo] = {}  # Cache node IPs
        
    async def initialize(self):
        """Initialize proxy port"""
        self.proxy_port = MihomoAPI.get_proxy_port()
        print(f"[Init] Proxy port: {self.proxy_port}")
    
    def get_current_node(self) -> str:
        """Get currently selected node"""
        group = MihomoAPI.get_proxy_group(self.proxy_group)
        return group.get("now", "")
    
    def get_current_ip(self) -> Optional[IPInfo]:
        """Get current exit IP"""
        proxy_url = f"http://127.0.0.1:{self.proxy_port}"
        return IPDetector.get_current_ip(proxy_url)
    
    def evaluate_node(self, node_name: str, test_ip: bool = False) -> NodeMetrics:
        """Evaluate a single node comprehensively"""
        # Test delay via API
        delay = MihomoAPI.test_node_delay(node_name)
        
        # Run ping test
        ping_delay, packet_loss, jitter = NetworkTester.ping_test()
        
        # Use the worse of the two delays
        final_delay = max(delay, ping_delay)
        alive = final_delay < 9999
        
        # Get cached IP info or test new
        ip_info = self.node_ip_cache.get(node_name)
        if test_ip and alive:
            # Only test IP for current node or on demand
            new_ip = IPDetector.test_node_ip(node_name)
            if new_ip:
                ip_info = new_ip
                self.node_ip_cache[node_name] = new_ip
        
        # Calculate scores - IMPROVED ALGORITHM
        # Higher weight on delay (user experience)
        delay_score = max(0, 100 - final_delay / 3) if alive else 0  # Was /5, now /3
        loss_score = max(0, 100 - packet_loss * 20)  # Was *10, stricter
        jitter_score = max(0, 100 - jitter)  # Was /2, now direct
        
        # Stability score based on history
        stability_score = self._calculate_stability(node_name, alive)
        
        # NEW: Geographic score (prefer certain regions)
        geo_score = self._calculate_geo_score(ip_info)
        
        # Weighted overall score - IMPROVED
        overall_score = (
            0.40 * delay_score +      # Was 0.30, delay most important
            0.20 * loss_score +       # Was 0.25
            0.15 * jitter_score +     # Was 0.15
            0.15 * stability_score +  # NEW: stability history
            0.10 * geo_score          # NEW: geography preference
        )
        
        # Get history for this node
        history = self._get_node_history(node_name)
        
        return NodeMetrics(
            name=node_name,
            delay_ms=final_delay,
            packet_loss=packet_loss,
            jitter_ms=jitter,
            bandwidth_mbps=None,  # Remove bandwidth test (slow and unreliable)
            stability_score=stability_score,
            alive=alive,
            overall_score=overall_score,
            ip_info=ip_info,
            history=history
        )
    
    def _calculate_stability(self, node_name: str, current_alive: bool) -> float:
        """Calculate stability score based on historical performance"""
        if node_name not in self.node_metrics:
            return 100.0 if current_alive else 0.0
        
        old_metrics = self.node_metrics[node_name]
        history = old_metrics.history[-10:]  # Last 10 checks
        
        if not history:
            return 100.0 if current_alive else 0.0
        
        alive_count = sum(1 for h in history if h.get("alive", False))
        stability = (alive_count / len(history)) * 100
        
        # Boost if currently alive
        if current_alive:
            stability = min(100, stability + 10)
        
        return stability
    
    def _calculate_geo_score(self, ip_info: Optional[IPInfo]) -> float:
        """Calculate geographic preference score"""
        if not ip_info:
            return 50.0  # Neutral if unknown
        
        # Prefer certain countries (customize as needed)
        preferred = ["SG", "JP", "KR", "US", "DE", "GB", "NL"]
        if ip_info.country_code in preferred:
            return 100.0
        
        # Penalize high-latency regions slightly
        return 70.0
    
    def _get_node_history(self, node_name: str) -> List[Dict]:
        """Get history for a node"""
        if node_name in self.node_metrics:
            return self.node_metrics[node_name].history
        return []
    
    def _update_history(self, node_name: str, metrics: NodeMetrics):
        """Update history for a node"""
        history = self._get_node_history(node_name)
        history.append({
            "time": time.time(),
            "delay": metrics.delay_ms,
            "alive": metrics.alive,
            "score": metrics.overall_score
        })
        # Keep last 20 records
        if len(history) > 20:
            history = history[-20:]
        metrics.history = history
    
    async def evaluate_all_nodes(self):
        """Evaluate all nodes in parallel"""
        if self._evaluating:
            return
        
        self._evaluating = True
        self.current_node = self.get_current_node()
        
        group = MihomoAPI.get_proxy_group(self.proxy_group)
        nodes = [n for n in group.get("all", []) if n not in ["REJECT", "DIRECT"]]
        
        # Evaluate in parallel with semaphore
        semaphore = asyncio.Semaphore(3)  # Reduced from 5 to be gentler
        
        async def evaluate_with_limit(node_name: str):
            async with semaphore:
                loop = asyncio.get_event_loop()
                # Only test IP for a few nodes to save time
                test_ip = node_name == self.current_node or len(self.node_ip_cache) < 5
                metrics = await loop.run_in_executor(None, self.evaluate_node, node_name, test_ip)
                self._update_history(node_name, metrics)
                self.node_metrics[node_name] = metrics
                return node_name
        
        await asyncio.gather(*[evaluate_with_limit(n) for n in nodes])
        
        # Update current IP
        self.current_ip_info = self.get_current_ip()
        
        self._evaluating = False
    
    def should_switch(self) -> tuple[bool, str, str]:
        """Determine if we should switch to a better node"""
        if not self.auto_switch_enabled:
            return (False, "", "auto mode disabled")
        
        if time.time() - self.last_switch_time < self.switch_cooldown:
            remaining = int(self.switch_cooldown - (time.time() - self.last_switch_time))
            return (False, "", f"cooldown: {remaining}s remaining")
        
        if not self.node_metrics:
            return (False, "", "no metrics available")
        
        # Find best node
        alive_nodes = [(n, m) for n, m in self.node_metrics.items() if m.alive]
        if not alive_nodes:
            return (False, "", "no alive nodes")
        
        best_node = max(alive_nodes, key=lambda x: x[1].overall_score)
        best_name, best_metrics = best_node
        
        current = self.node_metrics.get(self.current_node)
        if not current:
            if best_metrics.overall_score > 50:
                return (True, best_name, "current node not evaluated")
            return (False, "", "current node not evaluated, best score too low")
        
        # Switch if significantly better (score diff > 15, was 10)
        score_diff = best_metrics.overall_score - current.overall_score
        if score_diff > 15:
            return (True, best_name, f"score diff: {score_diff:.1f}")
        
        # Or if current is bad
        if not current.alive and best_metrics.alive:
            return (True, best_name, "current dead, switching to alive")
        
        return (False, "", f"no significant improvement (diff: {score_diff:.1f})")
    
    def switch_to_node(self, node_name: str) -> tuple[bool, str]:
        """Switch to specified node with verification"""
        if self._switch_verifying:
            return (False, "switch already in progress")
        
        self._switch_verifying = True
        
        try:
            # Step 1: Get current IP before switch
            old_ip = self.get_current_ip()
            old_ip_str = old_ip.ip if old_ip else "unknown"
            
            # Step 2: Call API to switch
            if not MihomoAPI.switch_node(node_name, self.proxy_group):
                return (False, "API switch failed")
            
            # Step 3: Wait for connection
            time.sleep(3)
            
            # Step 4: Verify new IP
            new_ip = self.get_current_ip()
            
            if not new_ip:
                return (False, "could not detect new IP")
            
            # Step 5: Check if IP actually changed (or is different from before)
            if old_ip and new_ip.ip == old_ip_str:
                # IP didn't change - might be same region or switch failed
                print(f"[Switch] Warning: IP unchanged ({new_ip.ip}), but node switched to {node_name}")
            
            # Step 6: Update state
            self.current_node = node_name
            self.current_ip_info = new_ip
            self.last_switch_time = time.time()
            
            # Cache the IP for this node
            self.node_ip_cache[node_name] = new_ip
            
            # Add to latency history
            self.latency_history.append({
                "time": time.time(),
                "delay": self.node_metrics.get(node_name, NodeMetrics(
                    name=node_name, delay_ms=0, packet_loss=0, jitter_ms=0,
                    bandwidth_mbps=None, stability_score=0, alive=True, overall_score=0
                )).delay_ms,
                "node": node_name,
                "ip": new_ip.ip
            })
            
            return (True, f"switched to {node_name}, IP: {new_ip.ip} ({new_ip.location_str})")
            
        finally:
            self._switch_verifying = False
    
    def manual_switch(self, node_name: str) -> tuple[bool, str]:
        """Manual switch with bypass of some checks"""
        return self.switch_to_node(node_name)


# Global switcher instance
switcher = VPNSwitcher()


@app.get("/")
async def index():
    return templates.TemplateResponse("index.html", {"request": {}})


@app.get("/api/current")
async def current():
    """Get current node and IP info"""
    node_name = switcher.get_current_node()
    
    # Get fresh IP info
    ip_info = switcher.get_current_ip()
    if ip_info:
        switcher.current_ip_info = ip_info
    else:
        ip_info = switcher.current_ip_info
    
    # Get cached metrics
    cached = switcher.node_metrics.get(node_name)
    
    if cached:
        delay = cached.delay_ms
        packet_loss = cached.packet_loss
        jitter = cached.jitter_ms
        score = cached.overall_score
        status = cached.status
    else:
        delay = MihomoAPI.test_delay(switcher.proxy_group)
        packet_loss = 0
        jitter = 0
        score = 0
        status = "evaluating"
    
    return {
        "node": node_name,
        "ip_info": {
            "ip": ip_info.ip if ip_info else "--",
            "location": ip_info.location_str if ip_info else "Unknown",
            "country_code": ip_info.country_code if ip_info else "",
            "flag": ip_info.flag if ip_info else "🌐",
            "isp": ip_info.isp if ip_info else ""
        } if ip_info else None,
        "metrics": {
            "delay": delay if delay < 9999 else 0,
            "packet_loss": packet_loss,
            "jitter": jitter,
            "score": score,
            "status": status
        },
        "auto_mode": switcher.auto_switch_enabled,
        "evaluated": cached is not None,
        "last_switch": datetime.fromtimestamp(switcher.last_switch_time).strftime("%H:%M:%S") if switcher.last_switch_time else "never"
    }


@app.get("/api/nodes")
async def nodes():
    """Return all nodes as JSON"""
    current = switcher.get_current_node()
    nodes_data = list(switcher.node_metrics.values())
    
    # Sort by score
    nodes_data.sort(key=lambda x: x.overall_score, reverse=True)
    
    result = []
    for node in nodes_data:
        result.append({
            "name": node.name,
            "is_current": node.name == current,
            "delay_ms": node.delay_ms,
            "packet_loss": node.packet_loss,
            "jitter_ms": node.jitter_ms,
            "score": node.overall_score,
            "status": node.status,
            "alive": node.alive,
            "ip_info": {
                "ip": node.ip_info.ip if node.ip_info else None,
                "location": node.ip_info.location_str if node.ip_info else "Unknown",
                "flag": node.ip_info.flag if node.ip_info else "🌐",
                "country_code": node.ip_info.country_code if node.ip_info else ""
            } if node.ip_info else None
        })
    
    return result


@app.post("/api/switch/{node_name}")
async def switch_node(node_name: str):
    """Manual switch to a node"""
    success, message = switcher.manual_switch(node_name)
    if success:
        return {"success": True, "message": message}
    else:
        raise HTTPException(status_code=400, detail=message)


@app.get("/api/toggle-auto")
async def toggle_auto():
    switcher.auto_switch_enabled = not switcher.auto_switch_enabled
    return {"enabled": switcher.auto_switch_enabled}


@app.get("/api/history")
async def get_history():
    """Get latency history for chart"""
    return list(switcher.latency_history)


@app.on_event("startup")
async def startup_event():
    """Start background tasks"""
    await switcher.initialize()
    
    # Start node evaluation
    asyncio.create_task(switcher.evaluate_all_nodes())
    
    # Start auto-switching loop
    async def auto_switch_loop():
        await asyncio.sleep(5)
        while True:
            should_switch, best_node, reason = switcher.should_switch()
            if should_switch:
                print(f"[AutoSwitch] Reason: {reason}")
                success, message = switcher.switch_to_node(best_node)
                print(f"[AutoSwitch] Result: {message}")
            await asyncio.sleep(10)
    
    asyncio.create_task(auto_switch_loop())
    
    # Start periodic re-evaluation
    async def reevaluate_loop():
        await asyncio.sleep(60)
        while True:
            print("[Re-eval] Starting periodic node re-evaluation...")
            await switcher.evaluate_all_nodes()
            print(f"[Re-eval] Completed. Evaluated {len(switcher.node_metrics)} nodes")
            await asyncio.sleep(60)
    
    asyncio.create_task(reevaluate_loop())
    
    # Start IP refresh loop
    async def ip_refresh_loop():
        await asyncio.sleep(30)
        while True:
            fresh_ip = switcher.get_current_ip()
            if fresh_ip:
                switcher.current_ip_info = fresh_ip
                print(f"[IP Refresh] Current: {fresh_ip.ip} ({fresh_ip.location_str})")
            await asyncio.sleep(30)
    
    asyncio.create_task(ip_refresh_loop())


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()

"""
Tunnel Manager Module

Core module for managing multiple DNSTT tunnels and SSH sessions.
"""

import os
import subprocess
import time
import logging
import threading
import signal
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import psutil

from health_checker import HealthChecker
from xui_client import XUIClient

logger = logging.getLogger(__name__)


class TunnelState(Enum):
    """Tunnel state enumeration."""
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class DNSTTTunnel:
    """Represents a DNSTT tunnel instance."""
    tunnel_id: int
    local_port: int
    process: Optional[subprocess.Popen] = None
    pid: Optional[int] = None
    state: TunnelState = TunnelState.STOPPED
    restart_count: int = 0
    last_check: float = 0
    
    def is_alive(self) -> bool:
        """Check if DNSTT process is still running."""
        if self.pid is None:
            return False
        try:
            return psutil.pid_exists(self.pid)
        except Exception:
            return False


@dataclass
class SSHTunnel:
    """Represents an SSH tunnel instance."""
    tunnel_id: int  # DNSTT tunnel this SSH belongs to
    ssh_id: int  # SSH session ID within the DNSTT tunnel
    socks5_port: int
    process: Optional[subprocess.Popen] = None
    pid: Optional[int] = None
    state: TunnelState = TunnelState.STOPPED
    restart_count: int = 0
    last_check: float = 0
    xui_outbound_id: Optional[str] = None
    
    def is_alive(self) -> bool:
        """Check if SSH process is still running."""
        if self.pid is None:
            return False
        try:
            return psutil.pid_exists(self.pid)
        except Exception:
            return False


class TunnelManager:
    """Manages multiple DNSTT tunnels and SSH sessions."""
    
    def __init__(self, config: Dict):
        """
        Initialize tunnel manager.
        
        Args:
            config: Configuration dictionary from config.yaml
        """
        self.config = config
        self.running = False
        self._shutdown_event = threading.Event()
        
        # DNSTT configuration
        dnstt_config = config.get("dnstt", {})
        self.dnstt_path = os.path.expanduser(dnstt_config.get("path", "/usr/local/bin/dnstt-client"))
        self.dnstt_remote_ip = dnstt_config.get("remote_ip")
        self.dnstt_port = dnstt_config.get("port", 53)
        self.dnstt_domain = dnstt_config.get("domain")
        self.dnstt_pubkey = dnstt_config.get("pubkey", "")
        
        # SSH configuration
        ssh_config = config.get("ssh", {})
        self.ssh_user = ssh_config.get("user", "tunnel")
        self.ssh_key_path = os.path.expanduser(ssh_config.get("key_path", "~/.ssh/dnstt_key"))
        self.ssh_server = ssh_config.get("server", "127.0.0.1")
        
        # Tunnel configuration
        tunnels_config = config.get("tunnels", {})
        self.dnstt_count = tunnels_config.get("dnstt_count", 3)
        self.ssh_per_dnstt = tunnels_config.get("ssh_per_dnstt", 10)
        self.dnstt_start_port = tunnels_config.get("dnstt_start_port", 1080)
        self.socks_start_port = tunnels_config.get("socks_start_port", 9090)
        self.socks_ports_per_tunnel = tunnels_config.get("socks_ports_per_tunnel", 100)
        
        # Health check configuration
        health_config = config.get("health_check", {})
        self.health_interval = health_config.get("interval", 60)
        self.health_timeout = health_config.get("timeout", 5)
        self.health_retry_count = health_config.get("retry_count", 3)
        
        # Restart configuration
        restart_config = config.get("restart", {})
        self.max_retries = restart_config.get("max_retries", 5)
        self.backoff_seconds = restart_config.get("backoff_seconds", 10)
        
        # 3x-ui configuration
        xui_config = config.get("xui", {})
        self.xui_client = XUIClient(
            api_url=xui_config.get("api_url", "http://127.0.0.1:2053"),
            username=xui_config.get("username", "admin"),
            password=xui_config.get("password", "")
        )
        
        # Initialize health checker
        self.health_checker = HealthChecker(timeout=self.health_timeout)
        
        # Track tunnels
        self.dnstt_tunnels: Dict[int, DNSTTTunnel] = {}
        self.ssh_tunnels: Dict[Tuple[int, int], SSHTunnel] = {}  # Key: (tunnel_id, ssh_id)
        
        # Thread locks
        self._lock = threading.Lock()
        
    def _expand_path(self, path: str) -> str:
        """Expand user path and environment variables."""
        return os.path.expanduser(os.path.expandvars(path))
    
    def start_dnstt_tunnel(self, tunnel_id: int, local_port: int) -> bool:
        """
        Start a DNSTT tunnel.
        
        Args:
            tunnel_id: Unique tunnel ID
            local_port: Local port for the tunnel
            
        Returns:
            True if started successfully, False otherwise
        """
        try:
            logger.info(f"Starting DNSTT tunnel {tunnel_id} on port {local_port}")
            
            # Build DNSTT command
            cmd = [
                self.dnstt_path,
                "-udp", f"{self.dnstt_remote_ip}:{self.dnstt_port}",
                "-pubkey", self.dnstt_pubkey,
                self.dnstt_domain,
                f"127.0.0.1:{local_port}"
            ]
            
            # Start DNSTT process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid  # Create new process group
            )
            
            # Wait a bit for tunnel to establish
            time.sleep(2)
            
            # Check if process is still running and port is listening
            if process.poll() is not None:
                logger.error(f"DNSTT tunnel {tunnel_id} process exited immediately")
                return False
            
            if not self.health_checker.check_dnstt_port("127.0.0.1", local_port):
                logger.warning(f"DNSTT tunnel {tunnel_id} port {local_port} not listening yet, waiting...")
                # Wait a bit more
                for _ in range(5):
                    time.sleep(1)
                    if self.health_checker.check_dnstt_port("127.0.0.1", local_port):
                        break
                else:
                    logger.error(f"DNSTT tunnel {tunnel_id} port {local_port} still not listening")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return False
            
            # Update tunnel state
            with self._lock:
                if tunnel_id in self.dnstt_tunnels:
                    tunnel = self.dnstt_tunnels[tunnel_id]
                    tunnel.process = process
                    tunnel.pid = process.pid
                    tunnel.state = TunnelState.RUNNING
                    tunnel.last_check = time.time()
            
            logger.info(f"DNSTT tunnel {tunnel_id} started successfully (PID: {process.pid})")
            return True
            
        except Exception as e:
            logger.error(f"Error starting DNSTT tunnel {tunnel_id}: {e}")
            return False
    
    def stop_dnstt_tunnel(self, tunnel_id: int) -> bool:
        """
        Stop a DNSTT tunnel and all its SSH sessions.
        
        Args:
            tunnel_id: Tunnel ID to stop
            
        Returns:
            True if stopped successfully, False otherwise
        """
        with self._lock:
            if tunnel_id not in self.dnstt_tunnels:
                return True  # Already stopped
            
            tunnel = self.dnstt_tunnels[tunnel_id]
            tunnel.state = TunnelState.STOPPING
        
        logger.info(f"Stopping DNSTT tunnel {tunnel_id}")
        
        # Stop all SSH sessions for this tunnel
        ssh_tunnels_to_stop = [
            ssh for (tid, sid), ssh in self.ssh_tunnels.items()
            if tid == tunnel_id
        ]
        
        for ssh_tunnel in ssh_tunnels_to_stop:
            self.stop_ssh_tunnel(tunnel_id, ssh_tunnel.ssh_id)
        
        # Stop DNSTT process
        if tunnel.process and tunnel.pid:
            try:
                if psutil.pid_exists(tunnel.pid):
                    try:
                        pgid = os.getpgid(tunnel.pid)
                        os.killpg(pgid, signal.SIGTERM)
                        try:
                            tunnel.process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            if psutil.pid_exists(tunnel.pid):
                                os.killpg(pgid, signal.SIGKILL)
                                tunnel.process.wait()
                    except ProcessLookupError:
                        pass  # Process already dead
            except ProcessLookupError:
                pass  # Process already dead
            except Exception as e:
                logger.error(f"Error stopping DNSTT tunnel {tunnel_id}: {e}")
        
        with self._lock:
            tunnel.state = TunnelState.STOPPED
            tunnel.process = None
            tunnel.pid = None
        
        logger.info(f"DNSTT tunnel {tunnel_id} stopped")
        return True
    
    def start_ssh_tunnel(self, tunnel_id: int, ssh_id: int, dnstt_port: int, socks5_port: int) -> bool:
        """
        Start an SSH tunnel through a DNSTT tunnel.
        
        Args:
            tunnel_id: Parent DNSTT tunnel ID
            ssh_id: SSH session ID within the tunnel
            dnstt_port: DNSTT tunnel local port
            socks5_port: SOCKS5 proxy port
            
        Returns:
            True if started successfully, False otherwise
        """
        try:
            logger.info(f"Starting SSH tunnel {ssh_id} through DNSTT tunnel {tunnel_id} -> SOCKS5:{socks5_port}")
            
            # Build SSH command
            cmd = [
                "ssh",
                "-i", self.ssh_key_path,
                "-N",  # No remote command execution
                f"{self.ssh_user}@{self.ssh_server}",
                "-p", str(dnstt_port),
                "-D", str(socks5_port),  # Dynamic port forwarding (SOCKS5)
                "-o", "ServerAliveInterval=60",
                "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes",
                "-o", "UserKnownHostsFile=/dev/null"
            ]
            
            # Start SSH process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid
            )
            
            # Wait for SSH to establish connection
            time.sleep(2)
            
            # Check if process is still running
            if process.poll() is not None:
                stderr = process.stderr.read().decode() if process.stderr else ""
                logger.error(f"SSH tunnel {tunnel_id}:{ssh_id} process exited: {stderr}")
                return False
            
            # Check if SOCKS5 port is listening
            if not self.health_checker.is_port_listening("127.0.0.1", socks5_port):
                logger.warning(f"SSH tunnel {tunnel_id}:{ssh_id} SOCKS5 port {socks5_port} not listening yet")
                for _ in range(5):
                    time.sleep(1)
                    if self.health_checker.is_port_listening("127.0.0.1", socks5_port):
                        break
                else:
                    logger.error(f"SSH tunnel {tunnel_id}:{ssh_id} SOCKS5 port still not listening")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return False
            
            # Register in 3x-ui
            outbound_id = self.xui_client.add_socks5_outbound(
                host="127.0.0.1",
                port=socks5_port,
                remark=f"DNSTT-{tunnel_id}-SSH-{ssh_id}"
            )
            
            if outbound_id:
                # Reload Xray
                self.xui_client.reload_xray()
            
            # Update tunnel state
            with self._lock:
                key = (tunnel_id, ssh_id)
                if key in self.ssh_tunnels:
                    ssh_tunnel = self.ssh_tunnels[key]
                    ssh_tunnel.process = process
                    ssh_tunnel.pid = process.pid
                    ssh_tunnel.state = TunnelState.RUNNING
                    ssh_tunnel.last_check = time.time()
                    ssh_tunnel.xui_outbound_id = outbound_id
            
            logger.info(f"SSH tunnel {tunnel_id}:{ssh_id} started successfully (PID: {process.pid}, Outbound: {outbound_id})")
            return True
            
        except Exception as e:
            logger.error(f"Error starting SSH tunnel {tunnel_id}:{ssh_id}: {e}")
            return False
    
    def stop_ssh_tunnel(self, tunnel_id: int, ssh_id: int) -> bool:
        """
        Stop an SSH tunnel.
        
        Args:
            tunnel_id: Parent DNSTT tunnel ID
            ssh_id: SSH session ID
            
        Returns:
            True if stopped successfully, False otherwise
        """
        key = (tunnel_id, ssh_id)
        
        with self._lock:
            if key not in self.ssh_tunnels:
                return True  # Already stopped
            
            ssh_tunnel = self.ssh_tunnels[key]
            ssh_tunnel.state = TunnelState.STOPPING
        
        logger.info(f"Stopping SSH tunnel {tunnel_id}:{ssh_id}")
        
        # Remove from 3x-ui
        if ssh_tunnel.xui_outbound_id:
            self.xui_client.remove_outbound(ssh_tunnel.xui_outbound_id)
            self.xui_client.reload_xray()
        
        # Stop SSH process
        if ssh_tunnel.process and ssh_tunnel.pid:
            try:
                if psutil.pid_exists(ssh_tunnel.pid):
                    try:
                        pgid = os.getpgid(ssh_tunnel.pid)
                        os.killpg(pgid, signal.SIGTERM)
                        try:
                            ssh_tunnel.process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            if psutil.pid_exists(ssh_tunnel.pid):
                                os.killpg(pgid, signal.SIGKILL)
                                ssh_tunnel.process.wait()
                    except ProcessLookupError:
                        pass  # Process already dead
            except ProcessLookupError:
                pass  # Process already dead
            except Exception as e:
                logger.error(f"Error stopping SSH tunnel {tunnel_id}:{ssh_id}: {e}")
        
        with self._lock:
            ssh_tunnel.state = TunnelState.STOPPED
            ssh_tunnel.process = None
            ssh_tunnel.pid = None
            ssh_tunnel.xui_outbound_id = None
        
        logger.info(f"SSH tunnel {tunnel_id}:{ssh_id} stopped")
        return True
    
    def initialize_tunnels(self):
        """Initialize all DNSTT tunnels and SSH sessions."""
        logger.info(f"Initializing {self.dnstt_count} DNSTT tunnels with {self.ssh_per_dnstt} SSH sessions each")
        
        # Initialize DNSTT tunnels
        for tunnel_id in range(self.dnstt_count):
            local_port = self.dnstt_start_port + tunnel_id
            tunnel = DNSTTTunnel(
                tunnel_id=tunnel_id,
                local_port=local_port,
                state=TunnelState.STARTING
            )
            self.dnstt_tunnels[tunnel_id] = tunnel
        
        # Initialize SSH tunnels
        for tunnel_id in range(self.dnstt_count):
            base_socks_port = self.socks_start_port + (tunnel_id * self.socks_ports_per_tunnel)
            for ssh_id in range(self.ssh_per_dnstt):
                socks5_port = base_socks_port + ssh_id
                key = (tunnel_id, ssh_id)
                ssh_tunnel = SSHTunnel(
                    tunnel_id=tunnel_id,
                    ssh_id=ssh_id,
                    socks5_port=socks5_port,
                    state=TunnelState.STOPPED
                )
                self.ssh_tunnels[key] = ssh_tunnel
        
        # Start DNSTT tunnels
        for tunnel_id in range(self.dnstt_count):
            tunnel = self.dnstt_tunnels[tunnel_id]
            if not self.start_dnstt_tunnel(tunnel_id, tunnel.local_port):
                logger.error(f"Failed to start DNSTT tunnel {tunnel_id}")
                continue
            
            # Wait a bit before starting SSH sessions
            time.sleep(1)
            
            # Start SSH sessions for this DNSTT tunnel
            for ssh_id in range(self.ssh_per_dnstt):
                key = (tunnel_id, ssh_id)
                ssh_tunnel = self.ssh_tunnels[key]
                if not self.start_ssh_tunnel(tunnel_id, ssh_id, tunnel.local_port, ssh_tunnel.socks5_port):
                    logger.error(f"Failed to start SSH tunnel {tunnel_id}:{ssh_id}")
                    # Continue with next SSH session
                time.sleep(0.5)  # Small delay between SSH starts
        
        logger.info("Tunnel initialization complete")
    
    def monitor_loop(self):
        """Main monitoring loop."""
        logger.info("Starting monitoring loop")
        
        while not self._shutdown_event.is_set():
            try:
                # Check DNSTT tunnels
                for tunnel_id, tunnel in list(self.dnstt_tunnels.items()):
                    if tunnel.state != TunnelState.RUNNING:
                        continue
                    
                    # Check if process is alive and port is listening
                    is_alive = tunnel.is_alive()
                    port_listening = self.health_checker.check_dnstt_port("127.0.0.1", tunnel.local_port)
                    
                    if not is_alive or not port_listening:
                        logger.warning(f"DNSTT tunnel {tunnel_id} failed (alive: {is_alive}, port: {port_listening})")
                        tunnel.state = TunnelState.FAILED
                        tunnel.restart_count += 1
                        
                        if tunnel.restart_count <= self.max_retries:
                            logger.info(f"Restarting DNSTT tunnel {tunnel_id} (attempt {tunnel.restart_count})")
                            self.stop_dnstt_tunnel(tunnel_id)
                            time.sleep(self.backoff_seconds * tunnel.restart_count)
                            if self.start_dnstt_tunnel(tunnel_id, tunnel.local_port):
                                # Restart all SSH sessions for this tunnel
                                base_socks_port = self.socks_start_port + (tunnel_id * self.socks_ports_per_tunnel)
                                for ssh_id in range(self.ssh_per_dnstt):
                                    socks5_port = base_socks_port + ssh_id
                                    key = (tunnel_id, ssh_id)
                                    if key in self.ssh_tunnels:
                                        ssh_tunnel = self.ssh_tunnels[key]
                                        ssh_tunnel.state = TunnelState.STOPPED
                                        self.start_ssh_tunnel(tunnel_id, ssh_id, tunnel.local_port, socks5_port)
                                    time.sleep(0.5)
                                tunnel.restart_count = 0  # Reset on success
                        else:
                            logger.error(f"DNSTT tunnel {tunnel_id} exceeded max retries, stopping")
                            self.stop_dnstt_tunnel(tunnel_id)
                    
                    tunnel.last_check = time.time()
                
                # Check SSH tunnels
                for (tunnel_id, ssh_id), ssh_tunnel in list(self.ssh_tunnels.items()):
                    # Skip if parent DNSTT tunnel is not running
                    if tunnel_id not in self.dnstt_tunnels:
                        continue
                    parent_tunnel = self.dnstt_tunnels[tunnel_id]
                    if parent_tunnel.state != TunnelState.RUNNING:
                        continue
                    
                    if ssh_tunnel.state != TunnelState.RUNNING:
                        continue
                    
                    # Check if process is alive and SOCKS5 is working
                    is_alive = ssh_tunnel.is_alive()
                    socks5_healthy = self.health_checker.check_tunnel_health(
                        "127.0.0.1", ssh_tunnel.socks5_port
                    )
                    
                    if not is_alive or not socks5_healthy:
                        logger.warning(f"SSH tunnel {tunnel_id}:{ssh_id} failed (alive: {is_alive}, healthy: {socks5_healthy})")
                        ssh_tunnel.state = TunnelState.FAILED
                        ssh_tunnel.restart_count += 1
                        
                        if ssh_tunnel.restart_count <= self.max_retries:
                            logger.info(f"Restarting SSH tunnel {tunnel_id}:{ssh_id} (attempt {ssh_tunnel.restart_count})")
                            self.stop_ssh_tunnel(tunnel_id, ssh_id)
                            time.sleep(self.backoff_seconds * ssh_tunnel.restart_count)
                            if self.start_ssh_tunnel(
                                tunnel_id, ssh_id, 
                                parent_tunnel.local_port, 
                                ssh_tunnel.socks5_port
                            ):
                                ssh_tunnel.restart_count = 0  # Reset on success
                        else:
                            logger.error(f"SSH tunnel {tunnel_id}:{ssh_id} exceeded max retries, stopping")
                            self.stop_ssh_tunnel(tunnel_id, ssh_id)
                    
                    ssh_tunnel.last_check = time.time()
                
                # Sleep until next check
                self._shutdown_event.wait(self.health_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)
                time.sleep(5)
        
        logger.info("Monitoring loop stopped")
    
    def start(self):
        """Start the tunnel manager."""
        if self.running:
            logger.warning("Tunnel manager is already running")
            return
        
        logger.info("Starting tunnel manager")
        self.running = True
        self._shutdown_event.clear()
        
        # Initialize tunnels
        self.initialize_tunnels()
        
        # Start monitoring in background thread
        monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        monitor_thread.start()
        
        logger.info("Tunnel manager started")
    
    def stop(self):
        """Stop the tunnel manager and all tunnels."""
        if not self.running:
            return
        
        logger.info("Stopping tunnel manager")
        self.running = False
        self._shutdown_event.set()
        
        # Stop all tunnels
        for tunnel_id in list(self.dnstt_tunnels.keys()):
            self.stop_dnstt_tunnel(tunnel_id)
        
        logger.info("Tunnel manager stopped")

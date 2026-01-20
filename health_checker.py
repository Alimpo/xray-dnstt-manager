"""
SOCKS5 Health Checker Module

Tests SOCKS5 proxy connectivity and validates ports are listening.
"""

import socket
import time
import logging
from typing import Optional
import socks
import requests

logger = logging.getLogger(__name__)


class HealthChecker:
    """Health checker for SOCKS5 proxies."""
    
    def __init__(self, timeout: int = 5):
        """
        Initialize health checker.
        
        Args:
            timeout: Connection timeout in seconds
        """
        self.timeout = timeout
    
    def is_port_listening(self, host: str, port: int) -> bool:
        """
        Check if a port is listening.
        
        Args:
            host: Host address (usually 127.0.0.1)
            port: Port number
            
        Returns:
            True if port is listening, False otherwise
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"Port check failed for {host}:{port}: {e}")
            return False
    
    def test_socks5_connectivity(
        self, 
        host: str = "127.0.0.1", 
        port: int = 9090,
        test_url: str = "http://www.google.com"
    ) -> bool:
        """
        Test SOCKS5 proxy connectivity.
        
        Args:
            host: SOCKS5 proxy host
            port: SOCKS5 proxy port
            test_url: URL to test connectivity through proxy
            
        Returns:
            True if proxy is working, False otherwise
        """
        try:
            # Create SOCKS5 proxy configuration
            proxies = {
                'http': f'socks5://{host}:{port}',
                'https': f'socks5://{host}:{port}'
            }
            
            # Try to make a request through the proxy
            response = requests.get(
                test_url,
                proxies=proxies,
                timeout=self.timeout,
                allow_redirects=False
            )
            
            # Any response (even errors) means proxy is working
            return True
            
        except requests.exceptions.ProxyError:
            logger.debug(f"SOCKS5 proxy {host}:{port} proxy error")
            return False
        except requests.exceptions.ConnectionError:
            logger.debug(f"SOCKS5 proxy {host}:{port} connection error")
            return False
        except requests.exceptions.Timeout:
            logger.debug(f"SOCKS5 proxy {host}:{port} timeout")
            return False
        except Exception as e:
            logger.debug(f"SOCKS5 proxy {host}:{port} test failed: {e}")
            return False
    
    def check_tunnel_health(
        self,
        host: str = "127.0.0.1",
        socks5_port: int = 9090,
        test_url: str = "http://www.google.com"
    ) -> bool:
        """
        Comprehensive health check: port listening + SOCKS5 connectivity.
        
        Args:
            host: SOCKS5 proxy host
            socks5_port: SOCKS5 proxy port
            test_url: URL to test connectivity through proxy
            
        Returns:
            True if tunnel is healthy, False otherwise
        """
        # First check if port is listening
        if not self.is_port_listening(host, socks5_port):
            logger.debug(f"Port {socks5_port} not listening on {host}")
            return False
        
        # Then check SOCKS5 connectivity
        if not self.test_socks5_connectivity(host, socks5_port, test_url):
            logger.debug(f"SOCKS5 proxy {host}:{socks5_port} connectivity test failed")
            return False
        
        return True
    
    def check_dnstt_port(self, host: str = "127.0.0.1", port: int = 1080) -> bool:
        """
        Check if DNSTT tunnel port is listening.
        
        Args:
            host: Host address
            port: DNSTT tunnel port
            
        Returns:
            True if port is listening, False otherwise
        """
        return self.is_port_listening(host, port)

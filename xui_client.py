"""
3x-ui API Client Module

Handles authentication, adding/removing outbounds, and reloading Xray configuration.
"""

import requests
import logging
from typing import Optional, Dict, Any, List
import time

logger = logging.getLogger(__name__)


class XUIClient:
    """Client for interacting with 3x-ui API."""
    
    def __init__(self, api_url: str, username: str, password: str):
        """
        Initialize 3x-ui API client.
        
        Args:
            api_url: Base URL of 3x-ui API (e.g., http://127.0.0.1:2053)
            username: 3x-ui username
            password: 3x-ui password
        """
        self.api_url = api_url.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.auth_token: Optional[str] = None
        self._authenticated = False
    
    def _get_url(self, endpoint: str) -> str:
        """Build full API URL."""
        return f"{self.api_url}{endpoint}"
    
    def login(self) -> bool:
        """
        Authenticate with 3x-ui API.
        
        Returns:
            True if authentication successful, False otherwise
        """
        try:
            login_url = self._get_url("/login")
            payload = {
                "username": self.username,
                "password": self.password
            }
            
            response = self.session.post(login_url, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success") or "token" in data or "session" in response.cookies:
                    # Different 3x-ui versions use different auth methods
                    if "token" in data:
                        self.auth_token = data["token"]
                        self.session.headers.update({"Authorization": f"Bearer {self.auth_token}"})
                    # Session cookies are handled automatically by requests.Session
                    self._authenticated = True
                    logger.info("Successfully authenticated with 3x-ui API")
                    return True
                else:
                    logger.error(f"Authentication failed: {data}")
                    return False
            else:
                logger.error(f"Login failed with status {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False
    
    def ensure_authenticated(self) -> bool:
        """Ensure we're authenticated, login if needed."""
        if not self._authenticated:
            return self.login()
        return True
    
    def add_socks5_outbound(
        self,
        host: str = "127.0.0.1",
        port: int = 9090,
        remark: str = "SSH-Tunnel",
        retry_count: int = 3
    ) -> Optional[str]:
        """
        Add a SOCKS5 outbound to 3x-ui.
        
        Args:
            host: SOCKS5 proxy host
            port: SOCKS5 proxy port
            remark: Remark/label for this outbound
            retry_count: Number of retries on failure
            
        Returns:
            Outbound ID if successful, None otherwise
        """
        if not self.ensure_authenticated():
            logger.error("Cannot add outbound: not authenticated")
            return None
        
        for attempt in range(retry_count):
            try:
                # Different 3x-ui versions may use different endpoints
                # Try common endpoints
                endpoints = [
                    "/xui/API/outbounds/add",
                    "/xui/API/inbounds/add",  # Some versions use inbounds for both
                    "/API/outbounds/add"
                ]
                
                for endpoint in endpoints:
                    url = self._get_url(endpoint)
                    
                    # Xray outbound configuration for SOCKS5
                    outbound_config = {
                        "protocol": "socks",
                        "settings": {
                            "servers": [{
                                "address": host,
                                "port": port
                            }]
                        },
                        "streamSettings": {
                            "network": "tcp"
                        },
                        "remark": f"{remark}-{host}:{port}"
                    }
                    
                    payload = {
                        "outbound": outbound_config,
                        "remark": f"{remark}-{host}:{port}"
                    }
                    
                    # Try different payload formats based on 3x-ui version
                    response = self.session.post(url, json=payload, timeout=10)
                    
                    if response.status_code in [200, 201]:
                        data = response.json()
                        outbound_id = data.get("id") or data.get("obj", {}).get("id") or str(port)
                        logger.info(f"Successfully added SOCKS5 outbound {host}:{port} with ID {outbound_id}")
                        return outbound_id
                    elif response.status_code == 401:
                        # Re-authenticate and retry
                        self._authenticated = False
                        if not self.ensure_authenticated():
                            break
                        continue
                
                logger.warning(f"Failed to add outbound after trying all endpoints (attempt {attempt + 1}/{retry_count})")
                if attempt < retry_count - 1:
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Error adding outbound (attempt {attempt + 1}/{retry_count}): {e}")
                if attempt < retry_count - 1:
                    time.sleep(1)
        
        logger.error(f"Failed to add SOCKS5 outbound {host}:{port} after {retry_count} attempts")
        return None
    
    def remove_outbound(self, outbound_id: str, retry_count: int = 3) -> bool:
        """
        Remove an outbound from 3x-ui.
        
        Args:
            outbound_id: ID of the outbound to remove
            retry_count: Number of retries on failure
            
        Returns:
            True if successful, False otherwise
        """
        if not self.ensure_authenticated():
            logger.error("Cannot remove outbound: not authenticated")
            return False
        
        for attempt in range(retry_count):
            try:
                # Try common endpoints
                endpoints = [
                    f"/xui/API/outbounds/{outbound_id}",
                    f"/xui/API/inbounds/{outbound_id}",
                    f"/API/outbounds/{outbound_id}"
                ]
                
                for endpoint in endpoints:
                    url = self._get_url(endpoint)
                    response = self.session.delete(url, timeout=10)
                    
                    if response.status_code in [200, 204]:
                        logger.info(f"Successfully removed outbound {outbound_id}")
                        return True
                    elif response.status_code == 404:
                        logger.warning(f"Outbound {outbound_id} not found (may already be removed)")
                        return True  # Consider it success if already gone
                    elif response.status_code == 401:
                        self._authenticated = False
                        if not self.ensure_authenticated():
                            break
                        continue
                
                if attempt < retry_count - 1:
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Error removing outbound (attempt {attempt + 1}/{retry_count}): {e}")
                if attempt < retry_count - 1:
                    time.sleep(1)
        
        logger.error(f"Failed to remove outbound {outbound_id} after {retry_count} attempts")
        return False
    
    def reload_xray(self, retry_count: int = 3) -> bool:
        """
        Reload Xray configuration.
        
        Args:
            retry_count: Number of retries on failure
            
        Returns:
            True if successful, False otherwise
        """
        if not self.ensure_authenticated():
            logger.error("Cannot reload Xray: not authenticated")
            return False
        
        for attempt in range(retry_count):
            try:
                endpoints = [
                    "/xui/API/setting/updateXrayConfig",
                    "/xui/API/setting/reload",
                    "/API/reload"
                ]
                
                for endpoint in endpoints:
                    url = self._get_url(endpoint)
                    response = self.session.post(url, timeout=10)
                    
                    if response.status_code in [200, 204]:
                        logger.info("Successfully reloaded Xray configuration")
                        return True
                    elif response.status_code == 401:
                        self._authenticated = False
                        if not self.ensure_authenticated():
                            break
                        continue
                
                if attempt < retry_count - 1:
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Error reloading Xray (attempt {attempt + 1}/{retry_count}): {e}")
                if attempt < retry_count - 1:
                    time.sleep(1)
        
        logger.error(f"Failed to reload Xray after {retry_count} attempts")
        return False
    
    def list_outbounds(self) -> Optional[List[Dict[str, Any]]]:
        """
        List all outbounds.
        
        Returns:
            List of outbounds or None on error
        """
        if not self.ensure_authenticated():
            logger.error("Cannot list outbounds: not authenticated")
            return None
        
        try:
            endpoints = [
                "/xui/API/outbounds",
                "/xui/API/inbounds",
                "/API/outbounds"
            ]
            
            for endpoint in endpoints:
                url = self._get_url(endpoint)
                response = self.session.get(url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    outbounds = data.get("obj") or data.get("data") or data.get("outbounds") or []
                    return outbounds if isinstance(outbounds, list) else []
                    
        except Exception as e:
            logger.error(f"Error listing outbounds: {e}")
        
        return None

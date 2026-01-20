#!/usr/bin/env python3
"""
DNSTT Tunnel Manager - Main Entry Point

Manages multiple DNSTT tunnels and SSH sessions with automatic health monitoring
and 3x-ui integration.
"""

import os
import sys
import signal
import logging
import yaml
from pathlib import Path
from logging.handlers import RotatingFileHandler

from tunnel_manager import TunnelManager


# Global manager instance for signal handling
manager: TunnelManager = None


def setup_logging(config: dict):
    """
    Setup logging configuration.
    
    Args:
        config: Configuration dictionary
    """
    log_config = config.get("logging", {})
    log_level = log_config.get("level", "INFO").upper()
    log_file = log_config.get("file", "logs/tunnel_manager.log")
    max_bytes = log_config.get("max_bytes", 10485760)  # 10MB
    backup_count = log_config.get("backup_count", 5)
    
    # Create logs directory if it doesn't exist
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation
    if log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count
        )
        file_handler.setLevel(getattr(logging, log_level, logging.INFO))
        file_format = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        root_logger.addHandler(file_handler)
    
    logging.info("Logging configured")


def load_config(config_path: str = "config.yaml") -> dict:
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Configuration dictionary
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid
    """
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    if not config:
        raise ValueError(f"Configuration file is empty: {config_path}")
    
    logging.info(f"Configuration loaded from {config_path}")
    return config


def signal_handler(signum, frame):
    """
    Handle shutdown signals gracefully.
    
    Args:
        signum: Signal number
        frame: Current stack frame
    """
    logging.info(f"Received signal {signum}, shutting down...")
    if manager:
        manager.stop()
    sys.exit(0)


def main():
    """Main entry point."""
    global manager
    
    # Default config path
    config_path = "config.yaml"
    
    # Allow config path from command line
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
    
    try:
        # Load configuration
        config = load_config(config_path)
        
        # Setup logging
        setup_logging(config)
        
        logging.info("=" * 60)
        logging.info("DNSTT Tunnel Manager Starting")
        logging.info("=" * 60)
        
        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Create and start tunnel manager
        manager = TunnelManager(config)
        manager.start()
        
        # Keep main thread alive
        try:
            while manager.running:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received")
        finally:
            manager.stop()
        
        logging.info("DNSTT Tunnel Manager stopped")
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing configuration file: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

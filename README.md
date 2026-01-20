# DNSTT Tunnel Manager

A Python-based service for managing multiple DNSTT tunnels and SSH sessions with automatic health monitoring, failure recovery, and 3x-ui/Xray integration for bypassing internet filtering.

## Features

- **Multiple DNSTT Tunnels**: Configure and manage multiple DNSTT tunnels simultaneously
- **Multiple SSH Sessions**: Create multiple SSH sessions per DNSTT tunnel for scalability
- **Automatic Health Monitoring**: Continuously monitors tunnel health via SOCKS5 connectivity tests
- **Auto-Restart on Failure**: Automatically restarts failed tunnels with exponential backoff
- **3x-ui Integration**: Automatically registers SOCKS5 proxies as outbounds in 3x-ui/Xray
- **Load Balancing**: Xray load balances traffic across all active SSH sessions
- **Comprehensive Logging**: Detailed logging with rotation
- **Systemd Service**: Runs as a daemon with automatic restart

## Architecture

```
Iranian Server
├── Tunnel Manager (Python Service)
│   ├── DNSTT Tunnel 1 (127.0.0.1:1080)
│   │   ├── SSH Session 1 → SOCKS5:9090
│   │   ├── SSH Session 2 → SOCKS5:9091
│   │   └── SSH Session N → SOCKS5:909N
│   ├── DNSTT Tunnel 2 (127.0.0.1:1081)
│   │   ├── SSH Session 1 → SOCKS5:9100
│   │   └── SSH Session N → SOCKS5:910N
│   └── DNSTT Tunnel M (127.0.0.1:108M)
│       └── SSH Session N → SOCKS5:9MNN
└── 3x-ui (Xray)
    └── Auto-configured SOCKS5 outbounds (load balanced)
```

## Prerequisites

- Python 3.7+
- DNSTT client binary installed
- SSH client installed
- 3x-ui/Xray installed and running
- SSH key configured for tunnel authentication

## Installation

1. **Clone or download this repository**

```bash
cd /opt
git clone <repository-url> dnstt
cd dnstt
```

2. **Install Python dependencies**

```bash
pip3 install -r requirements.txt
```

3. **Configure the service**

Edit `config.yaml` with your settings:

```yaml
dnstt:
  path: "/usr/local/bin/dnstt-client"
  remote_ip: "1.2.3.4"  # Outside server IP
  port: 53
  domain: "example.com"
  pubkey: "your-dnstt-public-key"

ssh:
  user: "tunnel"
  key_path: "~/.ssh/dnstt_key"
  server: "127.0.0.1"
  port: 1080

tunnels:
  dnstt_count: 3  # Number of DNSTT tunnels
  ssh_per_dnstt: 10  # SSH sessions per tunnel
  dnstt_start_port: 1080
  socks_start_port: 9090
  socks_ports_per_tunnel: 100

xui:
  api_url: "http://127.0.0.1:2053"
  username: "admin"
  password: "your-password"
```

4. **Install as systemd service** (optional)

```bash
sudo cp dnstt-tunnel-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable dnstt-tunnel-manager
sudo systemctl start dnstt-tunnel-manager
```

5. **Or run manually**

```bash
python3 main.py config.yaml
```

## Configuration

### DNSTT Settings

- `path`: Path to DNSTT client binary
- `remote_ip`: IP address of the outside server running DNSTT server
- `port`: UDP port for DNSTT (usually 53)
- `domain`: Domain name used for DNS tunneling
- `pubkey`: DNSTT public key

### SSH Settings

- `user`: SSH username on the outside server
- `key_path`: Path to SSH private key
- `server`: SSH server address (usually 127.0.0.1 when going through DNSTT)
- `port`: SSH server port (usually matches DNSTT local port)

### Tunnel Settings

- `dnstt_count`: Number of DNSTT tunnels to create
- `ssh_per_dnstt`: Number of SSH sessions per DNSTT tunnel
- `dnstt_start_port`: Starting port for DNSTT tunnels (e.g., 1080 → 1080, 1081, 1082...)
- `socks_start_port`: Starting port for SOCKS5 proxies (e.g., 9090)
- `socks_ports_per_tunnel`: Port range size per DNSTT tunnel (e.g., 100 → tunnel 1: 9090-9089, tunnel 2: 9100-9199)

### 3x-ui Settings

- `api_url`: 3x-ui API endpoint (usually http://127.0.0.1:2053)
- `username`: 3x-ui admin username
- `password`: 3x-ui admin password

### Health Check Settings

- `interval`: Health check interval in seconds (default: 60)
- `timeout`: Connection timeout in seconds (default: 5)
- `retry_count`: Number of retries before marking as failed (default: 3)

### Restart Settings

- `max_retries`: Maximum restart attempts before giving up (default: 5)
- `backoff_seconds`: Base delay between restart attempts (default: 10)

## Usage

### Start the Service

```bash
# Using systemd
sudo systemctl start dnstt-tunnel-manager

# Or manually
python3 main.py config.yaml
```

### Check Status

```bash
# Systemd
sudo systemctl status dnstt-tunnel-manager

# View logs
sudo journalctl -u dnstt-tunnel-manager -f
# Or
tail -f logs/tunnel_manager.log
```

### Stop the Service

```bash
# Systemd
sudo systemctl stop dnstt-tunnel-manager

# Or Ctrl+C if running manually
```

## How It Works

1. **Initialization**: 
   - Starts specified number of DNSTT tunnels on sequential local ports
   - For each DNSTT tunnel, starts specified number of SSH sessions
   - Each SSH session creates a SOCKS5 proxy on a unique port
   - Registers each SOCKS5 proxy as an outbound in 3x-ui

2. **Monitoring**:
   - Periodically checks if DNSTT processes are running
   - Tests if DNSTT ports are listening
   - Tests SOCKS5 proxy connectivity for each SSH session
   - Detects failures and triggers automatic restarts

3. **Failure Recovery**:
   - If a DNSTT tunnel fails: restarts the tunnel, then restarts all its SSH sessions
   - If an SSH session fails: only restarts that specific SSH session
   - Uses exponential backoff to avoid rapid restart loops

4. **3x-ui Integration**:
   - Automatically adds new SOCKS5 proxies as outbounds when tunnels start
   - Removes outbounds when tunnels fail permanently
   - Reloads Xray configuration after changes

## Port Allocation Example

With configuration:
- `dnstt_count: 3`
- `ssh_per_dnstt: 10`
- `dnstt_start_port: 1080`
- `socks_start_port: 9090`
- `socks_ports_per_tunnel: 100`

Result:
- DNSTT Tunnel 1: port 1080 → SSH SOCKS5 ports 9090-9099
- DNSTT Tunnel 2: port 1081 → SSH SOCKS5 ports 9100-9109
- DNSTT Tunnel 3: port 1082 → SSH SOCKS5 ports 9200-9209
- Total: 30 SOCKS5 proxies registered in Xray

## Troubleshooting

### Tunnels not starting

1. Check DNSTT client binary path is correct
2. Verify SSH key permissions: `chmod 600 ~/.ssh/dnstt_key`
3. Check if ports are already in use: `netstat -tulpn | grep <port>`
4. Review logs: `tail -f logs/tunnel_manager.log`

### 3x-ui API errors

1. Verify API URL and credentials in `config.yaml`
2. Check 3x-ui is running: `systemctl status 3x-ui`
3. Test API manually: `curl http://127.0.0.1:2053/login`

### SSH connection failures

1. Verify SSH key is authorized on outside server
2. Check DNSTT tunnel is working: `nc -zv 127.0.0.1 1080`
3. Test SSH manually through DNSTT tunnel
4. Check SSH logs in tunnel manager output

### High CPU/Memory Usage

- Reduce `dnstt_count` or `ssh_per_dnstt` in config
- Increase `health_check.interval` to check less frequently
- Check for zombie processes: `ps aux | grep defunct`

## Security Considerations

- Run the service as a dedicated user (not root) if possible
- Keep SSH keys secure with proper permissions
- Use strong passwords for 3x-ui
- Regularly update DNSTT and SSH components
- Monitor logs for suspicious activity

## License

[Your License Here]

## Contributing

[Your Contributing Guidelines Here]

## Support

[Your Support Information Here]
# xray-dnstt-manager

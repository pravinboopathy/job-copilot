#!/bin/bash
set -e

# Start WireGuard if config is mounted
if [ -f /etc/wireguard/wg0.conf ]; then
    echo "Starting WireGuard VPN..."
    wg-quick up wg0
    sleep 2
    EXTERNAL_IP=$(curl -s --max-time 10 ifconfig.me || echo "FAILED")
    echo "External IP: $EXTERNAL_IP"
else
    echo "Warning: No WireGuard config found at /etc/wireguard/wg0.conf — running without VPN"
fi

# Run the CLI with any arguments passed to the container
echo "Running: python -m src.cli $*"
python -m src.cli "$@"

# Disconnect VPN
if [ -f /etc/wireguard/wg0.conf ]; then
    wg-quick down wg0 2>/dev/null || true
fi

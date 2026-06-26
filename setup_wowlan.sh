#!/bin/bash
set -e

# Install iw if missing
if ! command -v iw &>/dev/null; then
    apt install -y iw
fi

# Detect phy name (iw dev outputs lines like "phy#0")
PHY=$(iw dev | awk '/^phy#/{gsub("#",""); print; exit}')
if [ -z "$PHY" ]; then
    echo "ERROR: Could not detect WiFi phy. Is WiFi connected?"
    exit 1
fi
echo "Using $PHY"

# Enable WoWLAN now
iw $PHY wowlan enable any
echo "WoWLAN enabled on $PHY"

# Create systemd service to re-enable after every boot/resume
tee /etc/systemd/system/wowlan.service << EOF
[Unit]
Description=Enable WoWLAN on boot and resume
After=network.target

[Service]
Type=oneshot
ExecStart=/sbin/iw $PHY wowlan enable any

[Install]
WantedBy=multi-user.target sleep.target
EOF

systemctl daemon-reload
systemctl enable wowlan.service
systemctl start wowlan.service

echo "Done. WoWLAN is active and will persist across reboots and resumes."
iw $PHY wowlan show

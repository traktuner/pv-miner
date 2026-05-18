#!/usr/bin/env bash
#
# install-proxmox.sh — create a minimal Alpine LXC container running pv-miner.
# Run on any Proxmox PVE host as root. Everything is auto-detected by default.
#
# Quick start:
#   bash <(curl -fsSL https://raw.githubusercontent.com/traktuner/pv-miner/master/install-proxmox.sh)
#
# Customise via env vars:
#   CT_HOSTNAME=pv-miner IP=192.168.1.60/24 GATEWAY=192.168.1.1 \
#     bash <(curl -fsSL https://raw.githubusercontent.com/traktuner/pv-miner/master/install-proxmox.sh)
#

set -euo pipefail
unset HOSTNAME

# ── Defaults ─────────────────────────────────────────────────────────────────
CTID=${CTID:-}
CT_HOSTNAME=${CT_HOSTNAME:-pv-miner}
RAM_MB=${RAM_MB:-128}
CORES=${CORES:-1}
DISK_GB=${DISK_GB:-1}
STORAGE=${STORAGE:-}
TEMPLATE_STORAGE=${TEMPLATE_STORAGE:-}
BRIDGE=${BRIDGE:-}
IP=${IP:-dhcp}
GATEWAY=${GATEWAY:-}
ALPINE_VER=${ALPINE_VER:-3.23}
ONBOOT=${ONBOOT:-1}
UNPRIVILEGED=${UNPRIVILEGED:-1}
WEB_PORT=${WEB_PORT:-8080}
TIMEZONE=${TIMEZONE:-$(cat /etc/timezone 2>/dev/null || echo Europe/Vienna)}

REPO=${REPO:-traktuner/pv-miner}
BRANCH=${BRANCH:-master}
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

# ── Sanity ────────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]]         || { echo "✗ Run as root on the Proxmox host"; exit 1; }
command -v pct   >/dev/null || { echo "✗ pct not found — not a Proxmox host?";  exit 1; }
command -v pvesh >/dev/null || { echo "✗ pvesh not found — not a Proxmox host?"; exit 1; }
command -v pvesm >/dev/null || { echo "✗ pvesm not found — not a Proxmox host?"; exit 1; }

# ── Storage auto-detect ───────────────────────────────────────────────────────
pick_storage() {
  pvesm status -content "$1" 2>/dev/null \
    | awk 'NR>1 && $3=="active" {print $1; exit}'
}
if [[ -z "$STORAGE" ]]; then
  STORAGE=$(pick_storage rootdir)
  [[ -n "$STORAGE" ]] || { echo "✗ No container-capable storage found. Set STORAGE=<name>."; pvesm status; exit 1; }
fi
if [[ -z "$TEMPLATE_STORAGE" ]]; then
  TEMPLATE_STORAGE=$(pick_storage vztmpl)
  [[ -n "$TEMPLATE_STORAGE" ]] || { echo "✗ No template-capable storage found. Set TEMPLATE_STORAGE=<name>."; exit 1; }
fi

# ── Bridge auto-detect ────────────────────────────────────────────────────────
if [[ -z "$BRIDGE" ]]; then
  BRIDGE=$(ip -br link show type bridge 2>/dev/null | awk 'NR==1 {print $1}')
  [[ -n "$BRIDGE" ]] || { echo "✗ No network bridge found. Set BRIDGE=<name>."; exit 1; }
fi

# ── Container ID ──────────────────────────────────────────────────────────────
[[ -n "$CTID" ]] || CTID=$(pvesh get /cluster/nextid)
pct status "$CTID" >/dev/null 2>&1 && { echo "✗ CTID $CTID already in use. Set CTID=<id>."; exit 1; }

echo "→ Creating CTID $CTID ($CT_HOSTNAME)"
echo "  storage: $STORAGE  (template: $TEMPLATE_STORAGE)"
echo "  resources: ${RAM_MB} MB RAM · ${CORES} core · ${DISK_GB} GB disk"
echo "  network: bridge=$BRIDGE · ip=$IP"
echo "  timezone: $TIMEZONE"
echo ""

# ── Alpine template ───────────────────────────────────────────────────────────
echo "→ Resolving Alpine $ALPINE_VER template…"
pveam update >/dev/null 2>&1 || true
TEMPLATE_FILE=$(pveam available --section system 2>/dev/null \
  | awk -v v="$ALPINE_VER" '$2 ~ "alpine-"v && $2 ~ "default" {print $2}' \
  | sort -V | tail -1)
[[ -n "$TEMPLATE_FILE" ]] || { echo "✗ Alpine $ALPINE_VER template not found"; exit 1; }

if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE_FILE"; then
  echo "  Downloading $TEMPLATE_FILE…"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE_FILE"
else
  echo "  $TEMPLATE_FILE already cached"
fi

# ── Create container ──────────────────────────────────────────────────────────
NET_ARGS="name=eth0,bridge=${BRIDGE},ip=${IP}"
[[ "$IP" == "dhcp" ]] || { [[ -n "$GATEWAY" ]] || { echo "✗ Static IP set but GATEWAY is empty"; exit 1; }; NET_ARGS="${NET_ARGS},gw=${GATEWAY}"; }

echo "→ Creating LXC…"
pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE_FILE}" \
  --hostname     "$CT_HOSTNAME" \
  --memory       "$RAM_MB" \
  --swap         0 \
  --cores        "$CORES" \
  --rootfs       "${STORAGE}:${DISK_GB}" \
  --net0         "$NET_ARGS" \
  --unprivileged "$UNPRIVILEGED" \
  --features     keyctl=1,nesting=1 \
  --onboot       "$ONBOOT" \
  --tags         "pv-miner" \
  --description  "pv-miner — PV surplus controlled mining. Web UI on :${WEB_PORT}." \
  >/dev/null

echo "→ Starting container…"
pct start "$CTID"

# Wait for network
for i in $(seq 1 30); do
  pct exec "$CTID" -- sh -c 'wget -q --spider https://pypi.org 2>/dev/null' && break
  sleep 1
done

# ── Install app ───────────────────────────────────────────────────────────────
echo "→ Installing Python and dependencies (~60 s)…"
pct exec "$CTID" -- sh << SETUP
set -e
apk add --no-cache python3 py3-pip tzdata >/dev/null
mkdir -p /opt/pv-miner /data /var/log
if [ -f "/usr/share/zoneinfo/${TIMEZONE}" ]; then
  cp "/usr/share/zoneinfo/${TIMEZONE}" /etc/localtime
  echo "${TIMEZONE}" > /etc/timezone
else
  echo "WARNING: timezone ${TIMEZONE} not found, keeping Alpine default" >&2
fi
python3 -m venv /opt/pv-miner/venv
/opt/pv-miner/venv/bin/pip install --quiet 'flask>=3.0.0' 'requests>=2.31.0'
touch /var/log/pv-miner.log
SETUP

echo "→ Downloading app from GitHub…"
pct exec "$CTID" -- sh -c "wget -qO /opt/pv-miner/pv_miner.py '${RAW}/pv_miner.py'"

# ── Default config ────────────────────────────────────────────────────────────
# Written once; survives updates. User configures IPs via web UI.
if ! pct exec "$CTID" -- sh -c 'test -s /data/config.json 2>/dev/null'; then
pct exec "$CTID" -- sh -c "cat > /data/config.json << 'JSON'
{
  \"fronius\": { \"host\": \"\", \"pv2_host\": \"\", \"poll_interval_seconds\": 30 },
  \"miner\":   { \"host\": \"\", \"api_key\": \"\", \"expected_power_watt\": 2800 },
  \"control\": { \"battery_full_soc\": 100, \"battery_charge_target_watt\": 2000,
                \"grid_buffer_watt\": 200, \"grid_import_tolerance_watt\": 300,
                \"akku_entlade_sperre_watt\": 100, \"start_stable_minutes\": 5,
                \"stop_stable_minutes\": 3 },
  \"modes\":   { \"manual_override\": \"auto\" },
  \"logging\": { \"level\": \"INFO\", \"file\": \"/var/log/pv-miner.log\", \"max_bytes\": 10485760, \"backup_count\": 3 }
}
JSON"
fi

# ── OpenRC service ────────────────────────────────────────────────────────────
echo "→ Setting up service…"
pct exec "$CTID" -- sh << SVC
cat > /etc/init.d/pv-miner << 'EOF'
#!/sbin/openrc-run
name="pv-miner"
description="pv-miner PV surplus controlled mining"
command="/opt/pv-miner/venv/bin/python"
command_args="/opt/pv-miner/pv_miner.py"
command_background="yes"
pidfile="/run/pv-miner.pid"
output_log="/var/log/pv-miner.log"
error_log="/var/log/pv-miner.log"
directory="/data"
export CONFIG_PATH="/data/config.json"
export WEB_PORT="${WEB_PORT}"
export TZ="${TIMEZONE}"
export UPDATE_URL="${RAW}/pv_miner.py"
depend() { need net localmount; after firewall; }
EOF
chmod +x /etc/init.d/pv-miner
rc-update add pv-miner default >/dev/null
rc-service pv-miner start >/dev/null
SVC

# ── Update script ─────────────────────────────────────────────────────────────
pct exec "$CTID" -- sh << UPDSCRIPT
cat > /usr/local/bin/pv-miner-update << 'EOF'
#!/bin/sh
set -eu
URL="${RAW}/pv_miner.py"
NEW=/opt/pv-miner/pv_miner.py.new
BIN=/opt/pv-miner/pv_miner.py
PREVIOUS=/opt/pv-miner/pv_miner.py.previous
echo "→ Fetching \$URL"
wget --header='Cache-Control: no-cache' -qO "\$NEW" "\$URL?_=\$(date +%s)"
python3 -c "import ast; ast.parse(open('\$NEW').read())" || {
  echo "✗ Downloaded file is not valid Python"; rm -f "\$NEW"; exit 1
}
if [ -f "\$BIN" ] && [ "\$(sha256sum "\$NEW" | awk '{print \$1}')" = "\$(sha256sum "\$BIN" | awk '{print \$1}')" ]; then
  rm -f "\$NEW"
  echo "✓ Already current"
  exit 0
fi
EXPECTED_HASH=\$(sha256sum "\$NEW" | awk '{print \$1}')
cp "\$BIN" "\$PREVIOUS"
mv "\$NEW" "\$BIN"
rc-service pv-miner restart >/dev/null
for i in \$(seq 1 30); do
  BODY=\$(wget -qO- "http://localhost:${WEB_PORT}/api/version?_=\$(date +%s)" 2>/dev/null || true)
  echo "\$BODY" | grep -q "\$EXPECTED_HASH" && { echo "✓ Update OK"; exit 0; }
  sleep 1
done
echo "✗ Updated service did not verify — rolling back"
cp "\$PREVIOUS" "\$BIN"
rc-service pv-miner restart >/dev/null
exit 1
EOF
chmod +x /usr/local/bin/pv-miner-update
UPDSCRIPT

# ── Console banner ────────────────────────────────────────────────────────────
pct exec "$CTID" -- sh << 'BANNER'
cat > /usr/local/bin/pv-miner-banner << 'EOF'
#!/bin/sh
IP=$(ip -4 -o addr show eth0 2>/dev/null | awk '{split($4,a,"/"); print a[1]; exit}')
[ -z "$IP" ] && IP='(no network yet)'
ALPINE=$(cat /etc/alpine-release 2>/dev/null || echo '?')
{
  printf '\n'
  printf '   \033[1;36mpv-miner\033[0m   ·   Alpine %s\n' "$ALPINE"
  printf '   ────────────────────────────────────────\n'
  printf '\n'
  printf '   Web UI:   \033[1;32mhttp://%s:8080\033[0m\n' "$IP"
  printf '\n'
  printf '   Logs:     tail -f /var/log/pv-miner.log\n'
  printf '   Service:  rc-service pv-miner status|restart\n'
  printf '   Update:   pv-miner-update\n'
  printf '\n'
} > /etc/issue
EOF
chmod +x /usr/local/bin/pv-miner-banner
mkdir -p /etc/local.d
ln -sf /usr/local/bin/pv-miner-banner /etc/local.d/zz-banner.start
rc-update add local default >/dev/null 2>&1 || true
/usr/local/bin/pv-miner-banner
BANNER

# ── Done ──────────────────────────────────────────────────────────────────────
sleep 2
IP_ADDR=$(pct exec "$CTID" -- ip -4 -o addr show eth0 2>/dev/null \
  | awk '{split($4,a,"/"); print a[1]}' || true)

cat << EOF

✓ pv-miner is up.

  CTID:     $CTID
  Hostname: $CT_HOSTNAME
  IP:       ${IP_ADDR:-<DHCP pending — check: pct exec $CTID -- ip a>}
  Web UI:   http://${IP_ADDR:-<container-ip>}:${WEB_PORT}

Next steps:
  1. Open Web UI → set Fronius IP and Miner IP
  2. Set Braiins OS root password (leave empty if none)
  3. Check "Miner benötigt" (default: 2800 W)
  4. Leave Auto enabled — the battery has priority

Operations:
  Logs:     pct exec $CTID -- tail -f /var/log/pv-miner.log
  Restart:  pct exec $CTID -- rc-service pv-miner restart
  Shell:    pct enter $CTID
  Update:   pct exec $CTID -- pv-miner-update

Backup:
  pct exec $CTID -- tar -C /data -czf - . > pv-miner-backup.tgz

EOF

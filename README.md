# pv-miner

Controls an Antminer S19j Pro (Braiins OS) based on PV surplus and battery SOC from a Fronius GEN24 Plus + BYD HVS system. Runs as a minimal Alpine LXC container on Proxmox — no Home Assistant, no Docker.

Power target is set continuously via the Braiins OS REST API. A built-in web UI handles all configuration and provides live status.

## One-line install

Run on the **Proxmox PVE host shell** (not inside a VM):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/traktuner/pv-miner/main/install-proxmox.sh)
```

That's it. The script:

1. Downloads the Alpine 3.23 LXC template (~3 MB, cached).
2. Creates an unprivileged container: **128 MB RAM · 1 core · 1 GB disk · no swap**.
3. Installs Python 3 + Flask inside the container.
4. Downloads `pv_miner.py` directly from this repo.
5. Sets up an OpenRC service that auto-starts on container boot.
6. Prints the IP and Web UI URL.

```
✓ pv-miner is up.

  CTID:     104
  Hostname: pv-miner
  IP:       192.168.1.60
  Web UI:   http://192.168.1.60:8080
```

## First-time setup

Open the Web UI and fill in:

- **Fronius IP** — the GEN24 Plus (the one with the battery, not the Symo)
- **Miner IP** — the Antminer running Braiins OS
- **API Key** — optional; generate under Braiins OS → Settings → API Access
- **max_power_watt** — check your autotuned range in the Braiins OS web interface first

## Control logic

```
1. SOC < soc_minimum (15%)
   → pause or minimum power (configurable)
   → resumes when SOC ≥ soc_minimum + soc_hysterese

2. SOC ≥ soc_freigabe (95%)
   → full power (battery full, surplus must go somewhere)

3. Otherwise
   available = |P_Grid| − netz_puffer_watt   (only when exporting to grid)
   available < min_power_watt  → pause or minimum power (configurable)
   otherwise                   → set power_target = clamp(available, min, max)
```

Flapping is suppressed by two independent hysteresis layers: start/stop requires `hysterese_zyklen` consecutive confirmations; power adjustments are skipped when the delta is below `hysterese_watt`.

## Braiins OS modes

In the web UI under **Braiins OS Betriebsmodi** you can choose what happens in two scenarios:

| Scenario | Option A | Option B |
|---|---|---|
| Low PV surplus | **Pause** (miner off, saves energy) | **Minimalbetrieb** (~500 W, miner always runs) |
| Low battery SOC | **Pause** (protect battery) | **Minimalbetrieb** (~500 W) |

In addition, the **Override** buttons let you force any state immediately:

| Button | Effect |
|---|---|
| Auto | Follow the automatic control logic |
| Pause | Force pause |
| Minimalbetrieb | Force ~500 W regardless of surplus |
| Vollbetrieb | Force max power regardless of surplus |

## Customising the install

All defaults can be overridden via env vars:

```bash
CTID=200 \
CT_HOSTNAME=pv-miner \
IP=192.168.1.60/24 \
GATEWAY=192.168.1.1 \
RAM_MB=128 \
DISK_GB=1 \
STORAGE=local-zfs \
  bash <(curl -fsSL https://raw.githubusercontent.com/traktuner/pv-miner/main/install-proxmox.sh)
```

| Variable | Default | Notes |
|---|---|---|
| `CTID` | next free via `pvesh get /cluster/nextid` | |
| `CT_HOSTNAME` | `pv-miner` | |
| `RAM_MB` | `128` | idles at ~30 MB |
| `DISK_GB` | `1` | Alpine + Python venv ≈ 200 MB |
| `STORAGE` | first active rootdir-capable | `local-lvm`, `local-zfs`, … |
| `TEMPLATE_STORAGE` | first active vztmpl-capable | |
| `BRIDGE` | first PVE bridge (`vmbr0`) | |
| `IP` | `dhcp` | or `192.168.1.60/24` |
| `GATEWAY` | — | required if `IP` is static |
| `WEB_PORT` | `8080` | |
| `REPO` | `traktuner/pv-miner` | override to test a fork |
| `BRANCH` | `main` | override to test a branch |

## Common operations

```bash
# Live logs
pct exec <CTID> -- tail -f /var/log/pv-miner.log

# Restart service
pct exec <CTID> -- rc-service pv-miner restart

# Shell into container
pct enter <CTID>

# Update to latest
pct exec <CTID> -- pv-miner-update
```

## Updating

The installer bakes an update command into the container:

```bash
pct exec <CTID> -- pv-miner-update
```

It downloads the latest `pv_miner.py` from GitHub, validates it's syntactically correct Python, swaps it atomically, restarts the service, and health-checks the web UI within 15 seconds. If the service doesn't come up it automatically rolls back to the previous version. `/data/config.json` is never touched.

## Backup / restore

Config lives in `/data/config.json` inside the container.

```bash
# Backup
pct exec <CTID> -- tar -C /data -czf - . > pv-miner-backup.tgz

# Restore
cat pv-miner-backup.tgz | pct exec <CTID> -- tar -C /data -xzf -
pct exec <CTID> -- rc-service pv-miner restart
```

## Infrastructure

| Component | Model |
|---|---|
| Hybrid inverter (with battery) | Fronius GEN24 Plus |
| String inverter | Fronius Symo GEN24 8.2 |
| Battery | BYD HVS 25.6 kWh |
| Miner | Antminer S19j Pro, Braiins OS |
| Proxmox host | Dell PowerEdge R640 (slpppve01) |

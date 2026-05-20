# pv-miner

Controls an Antminer S19j Pro (Braiins OS) based on PV production and battery data from a Fronius GEN24 Plus + BYD HVS system. Runs as a minimal Alpine LXC container on Proxmox or as a small Docker container — no Home Assistant required.

**Modes:** Akku-Auto pauses/resumes the miner so the battery keeps priority. PV-Sommer 24h mines continuously, uses a Braiins OS hashrate target during the day, and uses a lower power target in the evening/night. pv-miner never changes autotuning mode or fan settings.

## One-line install

Run on the **Proxmox PVE host shell** (not inside a VM):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/traktuner/pv-miner/master/install-proxmox.sh)
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

Open the Web UI, switch to **Einstellungen**, and fill in:

- **Fronius IP** — the GEN24 Plus (the hybrid with the battery). Supplies grid, battery, SOC and its own PV.
- **2. Wechselrichter IP** — optional; a second inverter (e.g. a Symo) that is *not* linked to the hybrid. Its PV is invisible to the hybrid's local API, so pv-miner queries it separately and adds it. Leave empty with a single inverter.
- **Miner IP** — the Antminer running Braiins OS
- **Braiins OS password** — the password of the `root` login; leave empty if none is set
- **Miner benötigt** — expected miner draw while starting/ramping, minimum `2500 W`, default `2800 W`
- **Akku-Ladeziel** — minimum charge power the battery should still get before the miner starts, default `2000 W`
- **Akku gilt als voll ab** — SOC threshold where the battery reserve drops away, default `100%`
- **Sicherheitspuffer** — extra PV margin, default `200 W`
- **Start erst nach stabiler Sonne** — start delay after a pause, default `5 min`
- **Stop erst nach Lastspitze** — delay before pausing on sustained battery discharge or grid import, default `3 min`
- **PV-Sommer 24h** — optional mode: default `110 TH/s` above `4000 W` PV, `1200 W` power target below `2000 W` PV, switching after `5 min` stable PV

## Docker

GitHub Actions publishes a multi-arch image to GHCR on every push to `master`:

```bash
docker run -d \
  --name pv-miner \
  --restart unless-stopped \
  -p 8080:8080 \
  -v pv-miner-data:/data \
  ghcr.io/traktuner/pv-miner:latest
```

The container listens on port `8080` and stores its config in `/data/config.json`; its file log goes to `/data/pv-miner.log`. Put Traefik/OIDC in front of it if you expose it beyond your trusted network.

Docker updates are done by pulling a new image and recreating the container. The Web UI update button is only for the Proxmox LXC install because it uses OpenRC inside the appliance.

## Control logic

There are two clear automatic modes.

**Akku-Auto**

```
house_without_miner = abs(P_Load) - current_miner_power

if battery is not full:
  start_required_pv = house_without_miner + battery_charge_target + expected_miner_power + buffer

if battery is full:
  start_required_pv = house_without_miner + expected_miner_power + buffer

if miner is stopped and P_PV >= start_required_pv for start_stable_minutes:
  run

if miner is running and battery is not discharging and grid import stays below tolerance:
  run

if battery discharge or grid import stays too high for stop_stable_minutes:
  pause
```

Starting is deliberately conservative: after a pause, the start condition must stay true for `start_stable_minutes` (default 5 minutes) before pv-miner starts the miner again. Stopping is deliberately less nervous: short heat-pump or household load spikes are tolerated, and pv-miner pauses only if battery discharge or grid import remains too high for `stop_stable_minutes` (default 3 minutes). Every pause/resume is verified — pv-miner polls the miner afterwards and reports in the web UI whether the command was actually confirmed.

**PV-Sommer 24h**

```
if P_PV >= day_pv_threshold for switch_stable_minutes:
  resume miner
  set hashrate target to high_hashrate_th

if P_PV <= night_pv_threshold for switch_stable_minutes:
  resume miner
  set power target to low_power_watt

if Fronius is temporarily unavailable:
  keep mining and hold the last known summer target
```

Summer target writes are idempotent: pv-miner reads the current Braiins OS target first. If the target type changes, it explicitly switches Braiins OS via `PUT /performance/mode` and verifies the active mode before reporting success. If only the value changes within the same type, it sends `PUT /performance/hashrate-target` or `PUT /performance/power-target` and verifies the resulting target.

The **Live** page shows the current decision, the calculated start threshold or active summer target, house load without miner, battery charge target, miner estimate and buffer. Device IPs and tuning values live on the **Einstellungen** page to keep the dashboard compact.

## API assumptions

- Fronius: `GET /solar_api/v1/GetPowerFlowRealtimeData.fcgi`; `P_Grid < 0` means grid export, `P_Akku > 0` means battery discharge, and `P_Akku < 0` means battery charging. SOC is read from the first inverter entry that contains `SOC`; if none is present, the miner is paused for safety. If a second inverter is configured, its `Site.P_PV` is added and the house load is recomputed from the whole-house balance `P_Load = -(P_Grid + P_Akku + P_PV)`.
- Braiins OS: Public API (REST) at `/api/v1`. pv-miner logs in via `POST /api/v1/auth/login` as `root`; the returned token is sent in the `authorization` header (no "Bearer" prefix, auto-refreshed). It uses `PUT /api/v1/actions/pause` and `PUT /api/v1/actions/resume`, reads `GET /api/v1/miner/details` (`status`: 2 = mining, 3 = paused, 1 = idle), `GET /api/v1/miner/stats` (`power_stats.approximated_consumption.watt`), reads performance targets from `GET /api/v1/performance/mode` / `GET /api/v1/performance/tuner-state`, switches summer target type with `PUT /api/v1/performance/mode`, and writes same-type target changes with `PUT /api/v1/performance/hashrate-target` / `PUT /api/v1/performance/power-target`. Autotuning mode and fans are never written.

## Override buttons

The **Override** buttons force a state immediately, bypassing the automatic logic until you switch back to Auto:

| Button | Effect |
|---|---|
| Auto | Battery-first automatic control |
| Pause erzwingen | Force pause |
| Start erzwingen | Force the miner to run, regardless of surplus |

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
  bash <(curl -fsSL https://raw.githubusercontent.com/traktuner/pv-miner/master/install-proxmox.sh)
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
| `TIMEZONE` | host `/etc/timezone` or `Europe/Vienna` | container local time |
| `REPO` | `traktuner/pv-miner` | override to test a fork |
| `BRANCH` | `master` | override to test a branch |

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

It downloads the latest `pv_miner.py` from GitHub, validates it's syntactically correct Python, swaps it atomically, restarts the service, and verifies within 30 seconds that the running web service reports the expected code hash via `/api/version`. If the updated service doesn't verify it automatically rolls back to the previous version. `/data/config.json` is never touched.

## Backup / restore

Config lives in `/data/config.json` inside the container.

```bash
# Backup
pct exec <CTID> -- tar -C /data -czf - . > pv-miner-backup.tgz

# Restore
cat pv-miner-backup.tgz | pct exec <CTID> -- tar -C /data -xzf -
pct exec <CTID> -- rc-service pv-miner restart
```

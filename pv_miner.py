#!/usr/bin/env python3
"""pv-miner — web-controlled PV surplus mining daemon."""

import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request
import requests as _http

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/config.json")
WEB_PORT    = int(os.environ.get("WEB_PORT", "8080"))

DEFAULT_CONFIG: dict = {
    "fronius": {
        "host": "",
        "poll_interval_seconds": 30,
    },
    "miner": {
        "host": "",
        "api_key": "",
        "min_power_watt": 500,
        "max_power_watt": 3400,
    },
    "control": {
        "soc_minimum":      15,
        "soc_hysterese":    5,
        "soc_freigabe":     95,
        "netz_puffer_watt": 200,
        "hysterese_watt":   300,
        "hysterese_zyklen": 2,
    },
    "modes": {
        # "pause" | "minimum_power"
        "low_surplus_action": "pause",
        "soc_low_action":     "pause",
        # "auto" | "pause" | "minimum" | "maximum"
        "manual_override":    "auto",
    },
    "logging": {
        "level":        "INFO",
        "file":         "/var/log/pv-miner.log",
        "max_bytes":    10485760,
        "backup_count": 3,
    },
}

# ---------------------------------------------------------------------------
# Embedded single-page UI
# ---------------------------------------------------------------------------

HTML_PAGE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>pv-miner</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}
header{background:#161b22;border-bottom:1px solid #30363d;padding:14px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:1rem;font-weight:600;letter-spacing:.02em}
.badge{padding:4px 12px;border-radius:12px;font-size:.78rem;font-weight:700}
.badge.mining {background:#1a4731;color:#3fb950}
.badge.minimum{background:#3d2e1a;color:#e3b341}
.badge.maximum{background:#1c2c4c;color:#58a6ff}
.badge.paused {background:#3d1a1a;color:#f85149}
.badge.unknown{background:#21262d;color:#8b949e}
main{max-width:960px;margin:0 auto;padding:24px}
section{margin-bottom:32px}
h2{font-size:.78rem;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
.card .lbl{font-size:.72rem;color:#8b949e;margin-bottom:4px}
.card .val{font-size:1.35rem;font-weight:700;font-variant-numeric:tabular-nums}
.ov-row{display:flex;gap:8px;flex-wrap:wrap}
.ov-row button{padding:7px 18px;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#e6edf3;cursor:pointer;font-size:.88rem;transition:background .12s}
.ov-row button:hover{background:#30363d}
.ov-row button.active{border-color:#388bfd;background:#1c2c4c;color:#79c0ff;font-weight:600}
.box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px}
.fsec{margin-bottom:22px}
.fsec h3{font-size:.78rem;color:#8b949e;margin-bottom:10px;padding-bottom:7px;border-bottom:1px solid #21262d}
.fg{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px}
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:.76rem;color:#8b949e}
.field input,.field select{background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:7px 10px;font-size:.88rem}
.field input:focus,.field select:focus{outline:none;border-color:#388bfd}
.save-row{margin-top:18px;display:flex;align-items:center;gap:12px}
.btn-save{padding:7px 22px;background:#238636;border:none;border-radius:6px;color:#fff;font-size:.88rem;font-weight:600;cursor:pointer}
.btn-save:hover{background:#2ea043}
.ok{color:#3fb950;font-size:.83rem}.err{color:#f85149;font-size:.83rem}
.ts{font-size:.7rem;color:#484f58;margin-left:10px}
</style>
</head>
<body>
<header>
  <h1>&#9935; pv-miner</h1>
  <span id="badge" class="badge unknown">&#8212;</span>
</header>
<main>

<section>
  <h2>Status <span id="ts" class="ts"></span></h2>
  <div class="cards">
    <div class="card"><div class="lbl">Batterie SOC</div><div class="val" id="v-soc">&#8212;</div></div>
    <div class="card"><div class="lbl">PV &#220;berschuss</div><div class="val" id="v-surplus">&#8212;</div></div>
    <div class="card"><div class="lbl">P_Grid</div><div class="val" id="v-pgrid">&#8212;</div></div>
    <div class="card"><div class="lbl">P_PV</div><div class="val" id="v-ppv">&#8212;</div></div>
    <div class="card"><div class="lbl">Batterie</div><div class="val" id="v-pakku">&#8212;</div></div>
    <div class="card"><div class="lbl">Miner Power</div><div class="val" id="v-power">&#8212;</div></div>
  </div>
</section>

<section>
  <h2>Override</h2>
  <div class="ov-row">
    <button id="ov-auto"    onclick="setOv('auto')">Auto (PV-gesteuert)</button>
    <button id="ov-pause"   onclick="setOv('pause')">Pause</button>
    <button id="ov-minimum" onclick="setOv('minimum')">Minimalbetrieb (~500 W)</button>
    <button id="ov-maximum" onclick="setOv('maximum')">Vollbetrieb (Max W)</button>
  </div>
</section>

<section>
  <h2>Konfiguration</h2>
  <div class="box">

    <div class="fsec">
      <h3>Fronius GEN24 Plus</h3>
      <div class="fg">
        <div class="field"><label>IP-Adresse</label><input id="f-fh" placeholder="192.168.1.xxx"></div>
        <div class="field"><label>Poll-Intervall (s)</label><input id="f-pi" type="number" min="10" max="300"></div>
      </div>
    </div>

    <div class="fsec">
      <h3>Antminer (Braiins OS)</h3>
      <div class="fg">
        <div class="field"><label>IP-Adresse</label><input id="f-mh" placeholder="192.168.1.xxx"></div>
        <div class="field"><label>API Key (leer = kein Auth)</label><input id="f-ak" type="password" placeholder="optional"></div>
        <div class="field"><label>Min Power (W)</label><input id="f-mn" type="number" min="100" max="2000"></div>
        <div class="field"><label>Max Power (W)</label><input id="f-mx" type="number" min="500" max="4000"></div>
      </div>
    </div>

    <div class="fsec">
      <h3>Regelparameter</h3>
      <div class="fg">
        <div class="field"><label>SOC Minimum (%)</label><input id="f-sm" type="number" min="0" max="100"></div>
        <div class="field"><label>SOC Hysterese (%)</label><input id="f-sh" type="number" min="0" max="30"></div>
        <div class="field"><label>SOC Freigabe (%)</label><input id="f-sf" type="number" min="0" max="100"></div>
        <div class="field"><label>Netz-Puffer (W)</label><input id="f-np" type="number" min="0" max="2000"></div>
        <div class="field"><label>Hysterese Delta (W)</label><input id="f-hw" type="number" min="0" max="1000"></div>
        <div class="field"><label>Hysterese Zyklen</label><input id="f-hz" type="number" min="1" max="10"></div>
      </div>
    </div>

    <div class="fsec">
      <h3>Braiins OS Betriebsmodi</h3>
      <div class="fg">
        <div class="field">
          <label>Bei wenig PV-&#220;berschuss</label>
          <select id="f-ls">
            <option value="pause">Pausieren (Miner aus)</option>
            <option value="minimum_power">Minimalbetrieb (~500 W, l&#228;uft immer)</option>
          </select>
        </div>
        <div class="field">
          <label>Bei niedrigem Batterie-SOC</label>
          <select id="f-sl">
            <option value="pause">Pausieren (Batterie sch&#252;tzen)</option>
            <option value="minimum_power">Minimalbetrieb (~500 W)</option>
          </select>
        </div>
      </div>
    </div>

    <div class="save-row">
      <button class="btn-save" onclick="saveCfg()">Speichern</button>
      <span id="smsg"></span>
    </div>
  </div>
</section>

<section>
  <h2>System</h2>
  <div class="ov-row" style="align-items:center">
    <button id="btn-update" onclick="doUpdate()" style="border-color:#30363d">Update (GitHub master)</button>
    <span id="umsg"></span>
  </div>
</section>

</main>
<script>
async function fetchStatus(){
  try{
    const d=await(await fetch('/api/status')).json();
    const fw=v=>v!=null?Math.round(v)+' W':'&#8212;';
    document.getElementById('v-soc').textContent=d.soc!=null?d.soc.toFixed(1)+'%':'&#8212;';
    document.getElementById('v-surplus').innerHTML=fw(d.surplus_w);
    document.getElementById('v-pgrid').innerHTML=fw(d.p_grid);
    document.getElementById('v-ppv').innerHTML=fw(d.p_pv);
    document.getElementById('v-pakku').innerHTML=fw(d.p_akku);
    document.getElementById('v-power').innerHTML=fw(d.miner_power_w);
    const st=d.display_state||'unknown';
    const b=document.getElementById('badge');
    b.className='badge '+st;
    const L={mining:'&#9935; Mining',minimum:'&#8595; Minimal',maximum:'&#8593; Vollbetrieb',paused:'&#9646; Pausiert',unknown:'&#8212;'};
    b.innerHTML=L[st]||st;
    ['auto','pause','minimum','maximum'].forEach(m=>{
      document.getElementById('ov-'+m).classList.toggle('active',m===(d.manual_override||'auto'));
    });
    document.getElementById('ts').textContent='aktualisiert '+new Date().toLocaleTimeString('de-AT');
  }catch(e){}
}
async function fetchCfg(){
  try{
    const d=await(await fetch('/api/config')).json();
    document.getElementById('f-fh').value=d.fronius?.host||'';
    document.getElementById('f-pi').value=d.fronius?.poll_interval_seconds??30;
    document.getElementById('f-mh').value=d.miner?.host||'';
    document.getElementById('f-ak').value=d.miner?.api_key||'';
    document.getElementById('f-mn').value=d.miner?.min_power_watt??500;
    document.getElementById('f-mx').value=d.miner?.max_power_watt??3400;
    document.getElementById('f-sm').value=d.control?.soc_minimum??15;
    document.getElementById('f-sh').value=d.control?.soc_hysterese??5;
    document.getElementById('f-sf').value=d.control?.soc_freigabe??95;
    document.getElementById('f-np').value=d.control?.netz_puffer_watt??200;
    document.getElementById('f-hw').value=d.control?.hysterese_watt??300;
    document.getElementById('f-hz').value=d.control?.hysterese_zyklen??2;
    document.getElementById('f-ls').value=d.modes?.low_surplus_action||'pause';
    document.getElementById('f-sl').value=d.modes?.soc_low_action||'pause';
  }catch(e){}
}
async function saveCfg(){
  const msg=document.getElementById('smsg');
  const cfg={
    fronius:{host:document.getElementById('f-fh').value.trim(),poll_interval_seconds:+document.getElementById('f-pi').value},
    miner:{host:document.getElementById('f-mh').value.trim(),api_key:document.getElementById('f-ak').value.trim(),min_power_watt:+document.getElementById('f-mn').value,max_power_watt:+document.getElementById('f-mx').value},
    control:{soc_minimum:+document.getElementById('f-sm').value,soc_hysterese:+document.getElementById('f-sh').value,soc_freigabe:+document.getElementById('f-sf').value,netz_puffer_watt:+document.getElementById('f-np').value,hysterese_watt:+document.getElementById('f-hw').value,hysterese_zyklen:+document.getElementById('f-hz').value},
    modes:{low_surplus_action:document.getElementById('f-ls').value,soc_low_action:document.getElementById('f-sl').value}
  };
  try{
    const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
    if(r.ok){msg.className='ok';msg.innerHTML='&#10003; Gespeichert';}
    else{const e=await r.json();msg.className='err';msg.innerHTML='&#10007; '+(e.error||'Fehler');}
  }catch(e){msg.className='err';msg.innerHTML='&#10007; Netzwerkfehler';}
  setTimeout(()=>msg.textContent='',4000);
}
async function setOv(mode){
  try{await fetch('/api/override',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});}catch(e){}
  fetchStatus();
}
async function doUpdate(){
  const btn=document.getElementById('btn-update');
  const msg=document.getElementById('umsg');
  btn.disabled=true;
  msg.className='';msg.textContent='⏳ Update wird heruntergeladen…';
  try{
    const r=await fetch('/api/update',{method:'POST'});
    if(!r.ok){const e=await r.json();msg.className='err';msg.textContent='✗ '+(e.error||'Fehler');btn.disabled=false;return;}
  }catch(e){msg.className='err';msg.textContent='✗ Netzwerkfehler';btn.disabled=false;return;}
  msg.textContent='⏳ Service wird neu gestartet…';
  // Poll until the service is back (up to 30 s)
  let tries=0;
  const poll=setInterval(async()=>{
    tries++;
    try{
      await fetch('/api/status');
      clearInterval(poll);
      msg.className='ok';msg.textContent='✓ Update erfolgreich — Seite wird neu geladen…';
      setTimeout(()=>location.reload(),1500);
    }catch(e){
      if(tries>=30){clearInterval(poll);msg.className='err';msg.textContent='✗ Service antwortet nicht — prüfe Logs';btn.disabled=false;}
    }
  },1000);
}
fetchStatus();fetchCfg();
setInterval(fetchStatus,10000);
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Config manager
# ---------------------------------------------------------------------------

class ConfigManager:
    def __init__(self, path: str):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._cfg  = self._load()

    def _load(self) -> dict:
        if not self._path.exists():
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
            self._write(cfg)
            return cfg
        try:
            with self._path.open() as f:
                loaded = json.load(f)
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
            for section in cfg:
                if section in loaded and isinstance(cfg[section], dict):
                    cfg[section].update(loaded[section])
                elif section in loaded:
                    cfg[section] = loaded[section]
            return cfg
        except Exception as exc:
            logging.getLogger("config").error("Load failed: %s — using defaults", exc)
            return json.loads(json.dumps(DEFAULT_CONFIG))

    def _write(self, cfg: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(cfg, f, indent=2)
        tmp.replace(self._path)

    def get(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._cfg))

    def update(self, patch: dict) -> None:
        with self._lock:
            for section, values in patch.items():
                if section in self._cfg and isinstance(self._cfg[section], dict):
                    self._cfg[section].update(values)
                else:
                    self._cfg[section] = values
            self._write(self._cfg)
        logging.getLogger("config").info("Config saved")

    def set_override(self, mode: str) -> None:
        with self._lock:
            self._cfg.setdefault("modes", {})["manual_override"] = mode
            self._write(self._cfg)


# ---------------------------------------------------------------------------
# Fronius API
# ---------------------------------------------------------------------------

class FroniusAPI:
    def __init__(self, cfg: ConfigManager, timeout: int = 10):
        self._cfg     = cfg
        self._timeout = timeout

    def get_powerflow(self) -> dict | None:
        host = self._cfg.get()["fronius"]["host"]
        if not host:
            return None
        url = f"http://{host}/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
        try:
            r = _http.get(url, timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
            site      = data["Body"]["Data"]["Site"]
            inverters = data["Body"]["Data"].get("Inverters", {})
            soc = float(inverters.get("1", {}).get("SOC") or 100.0)
            return {
                "p_grid": float(site.get("P_Grid") or 0.0),
                "p_pv":   float(site.get("P_PV")   or 0.0),
                "p_akku": float(site.get("P_Akku")  or 0.0),
                "soc":    soc,
            }
        except Exception as exc:
            logging.getLogger("api").warning("Fronius: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Braiins OS API
# ---------------------------------------------------------------------------

class BraiinsAPI:
    def __init__(self, cfg: ConfigManager, timeout: int = 10):
        self._cfg     = cfg
        self._timeout = timeout

    def _base(self) -> str:
        return f"http://{self._cfg.get()['miner']['host']}/api/v1"

    def _hdrs(self) -> dict:
        h = {"Content-Type": "application/json"}
        key = self._cfg.get()["miner"].get("api_key", "")
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def set_power_target(self, watt: int) -> bool:
        try:
            _http.put(
                f"{self._base()}/miner/power-target",
                json={"power_target": {"watt": watt}, "save_action": "SAVE_ACTION_SAVE"},
                headers=self._hdrs(), timeout=self._timeout,
            ).raise_for_status()
            logging.getLogger("api").debug("set_power_target(%dW) OK", watt)
            return True
        except Exception as exc:
            logging.getLogger("api").warning("set_power_target: %s", exc)
            return False

    def pause(self) -> bool:
        try:
            _http.post(f"{self._base()}/miner/pause", json={},
                       headers=self._hdrs(), timeout=self._timeout).raise_for_status()
            logging.getLogger("api").debug("pause OK")
            return True
        except Exception as exc:
            logging.getLogger("api").warning("pause: %s", exc)
            return False

    def resume(self) -> bool:
        try:
            _http.post(f"{self._base()}/miner/resume", json={},
                       headers=self._hdrs(), timeout=self._timeout).raise_for_status()
            logging.getLogger("api").debug("resume OK")
            return True
        except Exception as exc:
            logging.getLogger("api").warning("resume: %s", exc)
            return False

    def get_status(self) -> dict | None:
        try:
            r = _http.get(f"{self._base()}/miner/stats",
                          headers=self._hdrs(), timeout=self._timeout)
            r.raise_for_status()
            d  = r.json()
            ps = d.get("power_stats", {})
            return {
                "power_watt":   int(ps.get("watt_current", 0)),
                "paused":       d.get("miner_status", "") in ("PAUSED", "paused"),
                "hashrate_ths": float(d.get("hashrate", 0)),
            }
        except Exception as exc:
            logging.getLogger("api").warning("get_status: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Shared state (web UI ↔ control loop)
# ---------------------------------------------------------------------------

class StateStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._d: dict = {
            "soc": None, "p_grid": None, "p_pv": None, "p_akku": None,
            "surplus_w": None, "miner_power_w": None,
            "display_state": "unknown", "manual_override": "auto",
        }

    def update(self, **kw) -> None:
        with self._lock:
            self._d.update(kw)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._d)


# ---------------------------------------------------------------------------
# Power controller
# ---------------------------------------------------------------------------

class PowerController:
    def __init__(self, cfg: ConfigManager, fronius: FroniusAPI,
                 braiins: BraiinsAPI, state: StateStore):
        self._cfg     = cfg
        self._fronius = fronius
        self._braiins = braiins
        self._state   = state
        self._log     = logging.getLogger("cycle")

        self._cur_action = "pause"
        self._cur_target = 0
        self._pend_action: str | None = None
        self._pend_target = 0
        self._pend_count  = 0
        self._soc_blocked = False
        self._fronius_err = 0
        self._braiins_err = 0

    def _decide(self, pf: dict, cfg: dict) -> tuple[str, int]:
        modes    = cfg.get("modes", {})
        ctrl     = cfg["control"]
        miner    = cfg["miner"]
        override = modes.get("manual_override", "auto")
        low_act  = modes.get("low_surplus_action", "pause")
        soc_act  = modes.get("soc_low_action",     "pause")
        min_w    = miner["min_power_watt"]
        max_w    = miner["max_power_watt"]

        if override == "pause":   return ("pause", 0)
        if override == "minimum": return ("mine",  min_w)
        if override == "maximum": return ("mine",  max_w)

        soc        = pf["soc"]
        ueberschuss = abs(pf["p_grid"]) if pf["p_grid"] < 0 else 0.0
        soc_resume  = ctrl["soc_minimum"] + ctrl["soc_hysterese"]

        if self._soc_blocked:
            if soc < soc_resume:
                return ("mine", min_w) if soc_act == "minimum_power" else ("pause", 0)
            self._soc_blocked = False

        if soc < ctrl["soc_minimum"]:
            self._soc_blocked = True
            return ("mine", min_w) if soc_act == "minimum_power" else ("pause", 0)

        if soc >= ctrl["soc_freigabe"]:
            return ("mine", max_w)

        verfuegbar = ueberschuss - ctrl["netz_puffer_watt"]
        if verfuegbar < min_w:
            return ("mine", min_w) if low_act == "minimum_power" else ("pause", 0)

        return ("mine", max(min_w, min(int(verfuegbar), max_w)))

    def _hysterese(self, action: str, target: int, cfg: dict) -> tuple[str, int]:
        hyst_w  = cfg["control"]["hysterese_watt"]
        hyst_cy = cfg["control"]["hysterese_zyklen"]

        if action == self._cur_action:
            if action == "pause":
                return ("pause", 0)
            if abs(target - self._cur_target) <= hyst_w:
                return (self._cur_action, self._cur_target)
            self._pend_action = None
            self._pend_count  = 0
            return (action, target)

        if self._pend_action == action:
            self._pend_count  += 1
            self._pend_target  = target
        else:
            self._pend_action  = action
            self._pend_target  = target
            self._pend_count   = 1

        if self._pend_count >= hyst_cy:
            self._pend_action = None
            self._pend_count  = 0
            return (action, self._pend_target)
        return (self._cur_action, self._cur_target)

    def _display(self, action: str, target: int, cfg: dict) -> str:
        if action == "pause":
            return "paused"
        if target <= cfg["miner"]["min_power_watt"]:
            return "minimum"
        if target >= cfg["miner"]["max_power_watt"]:
            return "maximum"
        return "mining"

    def _apply(self, action: str, target: int) -> None:
        if action == "pause":
            if self._cur_action != "pause":
                if self._braiins.pause():
                    self._cur_action  = "pause"
                    self._cur_target  = 0
                    self._braiins_err = 0
                else:
                    self._braiins_err += 1
        else:
            if self._cur_action == "pause":
                if not self._braiins.resume():
                    self._braiins_err += 1
                    return
            if self._braiins.set_power_target(target):
                self._cur_action  = "mine"
                self._cur_target  = target
                self._braiins_err = 0
            else:
                self._braiins_err += 1
        if self._braiins_err >= 5:
            self._log.critical("Braiins: %d consecutive failures", self._braiins_err)

    def run_cycle(self) -> None:
        cfg = self._cfg.get()
        pf  = self._fronius.get_powerflow()

        if pf is None:
            self._fronius_err += 1
            self._log.warning("Fronius unreachable (streak: %d)", self._fronius_err)
            if self._fronius_err >= 3 and self._cur_action != "pause":
                self._log.warning("Fronius 3x unreachable → pausing miner (safety)")
                self._apply("pause", 0)
            return

        self._fronius_err = 0
        soc        = pf["soc"]
        ueberschuss = abs(pf["p_grid"]) if pf["p_grid"] < 0 else 0.0

        self._log.debug("Fronius: p_grid=%.0fW p_pv=%.0fW p_akku=%.0fW soc=%.1f%%",
                        pf["p_grid"], pf["p_pv"], pf["p_akku"], soc)

        desired_a, desired_t = self._decide(pf, cfg)
        action, target       = self._hysterese(desired_a, desired_t, cfg)
        display              = self._display(action, target, cfg)

        miner_st = self._braiins.get_status()
        self._state.update(
            soc=soc, p_grid=pf["p_grid"], p_pv=pf["p_pv"], p_akku=pf["p_akku"],
            surplus_w=ueberschuss,
            miner_power_w=miner_st["power_watt"] if miner_st else None,
            display_state=display,
            manual_override=cfg.get("modes", {}).get("manual_override", "auto"),
        )

        no_change = (action == self._cur_action and
                     (action == "pause" or target == self._cur_target))
        if no_change:
            self._log.info("[cycle] SOC=%.0f%% surplus=%.0fW → keine Änderung",
                           soc, ueberschuss)
            return

        if action == "pause":
            self._log.info("[cycle] SOC=%.0f%% surplus=%.0fW → PAUSE", soc, ueberschuss)
        elif display == "minimum":
            self._log.info("[cycle] SOC=%.0f%% surplus=%.0fW → MINIMAL %dW", soc, ueberschuss, target)
        elif display == "maximum":
            self._log.info("[cycle] SOC=%.0f%% → MAX_POWER %dW (SOC Freigabe)", soc, target)
        else:
            self._log.info("[cycle] SOC=%.0f%% surplus=%.0fW → target=%dW", soc, ueberschuss, target)

        self._apply(action, target)


# ---------------------------------------------------------------------------
# Flask web app
# ---------------------------------------------------------------------------

def create_app(cfg_manager: ConfigManager, state: StateStore) -> Flask:
    app = Flask(__name__)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @app.route("/")
    def index():
        return Response(HTML_PAGE, mimetype="text/html")

    @app.route("/api/status")
    def api_status():
        return jsonify(state.snapshot())

    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        safe = cfg_manager.get()
        if safe.get("miner", {}).get("api_key"):
            safe["miner"]["api_key"] = "••••••••"
        return jsonify(safe)

    @app.route("/api/config", methods=["POST"])
    def api_config_post():
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400
        if not data.get("fronius", {}).get("host") or not data.get("miner", {}).get("host"):
            return jsonify({"error": "fronius.host und miner.host dürfen nicht leer sein"}), 400
        if data.get("miner", {}).get("api_key", "").startswith("••"):
            data.setdefault("miner", {})["api_key"] = cfg_manager.get()["miner"].get("api_key", "")
        cfg_manager.update(data)
        return jsonify({"ok": True})

    @app.route("/api/update", methods=["POST"])
    def api_update():
        update_bin = "/usr/local/bin/pv-miner-update"
        if not Path(update_bin).exists():
            return jsonify({"error": "pv-miner-update not found (only available in the LXC appliance)"}), 400

        def _run():
            time.sleep(2)  # let the HTTP response reach the browser first
            try:
                subprocess.run([update_bin], timeout=60)
            except Exception as exc:
                logging.getLogger("main").error("Update failed: %s", exc)

        threading.Thread(target=_run, daemon=True, name="updater").start()
        logging.getLogger("main").info("Update triggered via web UI")
        return jsonify({"ok": True})

    @app.route("/api/override", methods=["POST"])
    def api_override():
        data = request.get_json(silent=True) or {}
        mode = data.get("mode", "auto")
        if mode not in ("auto", "pause", "minimum", "maximum"):
            return jsonify({"error": "Invalid mode"}), 400
        cfg_manager.set_override(mode)
        state.update(manual_override=mode)
        logging.getLogger("cycle").info("Override: %s", mode)
        return jsonify({"ok": True})

    return app


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(cfg: dict) -> None:
    lc      = cfg["logging"]
    level   = getattr(logging, lc.get("level", "INFO").upper(), logging.INFO)
    fmt     = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    root    = logging.getLogger()
    root.setLevel(level)
    try:
        fh = logging.handlers.RotatingFileHandler(
            lc.get("file", "/var/log/pv-miner.log"),
            maxBytes=lc.get("max_bytes", 10 * 1024 * 1024),
            backupCount=lc.get("backup_count", 3),
        )
        fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        root.addHandler(fh)
    except OSError as exc:
        print(f"WARNING: cannot open log file: {exc}", file=sys.stderr)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(sh)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cfg_manager = ConfigManager(CONFIG_PATH)
    setup_logging(cfg_manager.get())
    log = logging.getLogger("main")
    log.info("pv-miner starting (web UI on :%d)", WEB_PORT)

    state   = StateStore()
    fronius = FroniusAPI(cfg_manager)
    braiins = BraiinsAPI(cfg_manager)
    ctrl    = PowerController(cfg_manager, fronius, braiins, state)
    app     = create_app(cfg_manager, state)

    shutdown = threading.Event()

    def _sig(_s, _f):
        log.info("Signal received — shutting down")
        shutdown.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    def _loop():
        while not shutdown.is_set():
            try:
                ctrl.run_cycle()
            except Exception as exc:
                log.exception("run_cycle error: %s", exc)
            shutdown.wait(timeout=cfg_manager.get()["fronius"].get("poll_interval_seconds", 30))
        log.info("Pausing miner before exit")
        braiins.pause()

    threading.Thread(target=_loop, daemon=True, name="control").start()

    from werkzeug.serving import make_server
    srv = make_server("0.0.0.0", WEB_PORT, app, threaded=True)
    srv.timeout = 1
    while not shutdown.is_set():
        srv.handle_request()

    shutdown.set()
    log.info("pv-miner stopped")


if __name__ == "__main__":
    main()

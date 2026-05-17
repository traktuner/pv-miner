#!/usr/bin/env python3
"""pv-miner — web-controlled PV surplus mining daemon."""

import json
import ast
import hashlib
import logging
import logging.handlers
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from flask import Flask, Response, jsonify, request
import requests as _http

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/config.json")
WEB_PORT    = int(os.environ.get("WEB_PORT", "8080"))
UPDATE_URL  = os.environ.get(
    "UPDATE_URL",
    "https://raw.githubusercontent.com/traktuner/pv-miner/master/pv_miner.py",
)

DEFAULT_CONFIG: dict = {
    "fronius": {
        "host": "",
        "pv2_host": "",
        "poll_interval_seconds": 30,
    },
    "miner": {
        "host": "",
        "api_key": "",
        "expected_power_watt": 2800,
    },
    "control": {
        "battery_full_soc": 100,
        "battery_charge_limit_watt": 11300,
        "grid_buffer_watt": 200,
        "akku_entlade_sperre_watt": 100,
        "start_stable_minutes": 5,
    },
    "modes": {
        # "auto" | "pause" | "run"
        "manual_override": "auto",
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
:root{--bg:#0b1020;--panel:#111827;--panel2:#162033;--line:#263244;--text:#eef4ff;--muted:#8ea0b8;--green:#35d07f;--amber:#f4bd50;--red:#ff6370;--blue:#58a6ff;--cyan:#3ddbd9}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:linear-gradient(180deg,#09111f 0%,#0d1324 55%,#0a0f1c 100%);color:var(--text);min-height:100vh}button,input{font:inherit}header{height:64px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:rgba(12,18,32,.84);backdrop-filter:blur(12px);position:sticky;top:0;z-index:2}h1{font-size:1.05rem;font-weight:760;letter-spacing:.01em}.head-left{display:flex;align-items:center;gap:16px}.tabs{display:flex;gap:6px}.tabs button{border:1px solid var(--line);background:#141d2e;color:var(--muted);border-radius:7px;padding:7px 10px;cursor:pointer;font-size:.82rem;font-weight:760}.tabs button.active{background:var(--blue);border-color:var(--blue);color:#07111f}.view{display:none}.view.active{display:block}.badge{padding:7px 12px;border-radius:999px;font-size:.78rem;font-weight:800;border:1px solid var(--line)}.badge.mining{color:#08150f;background:var(--green);border-color:var(--green)}.badge.paused{color:#22080b;background:var(--red);border-color:var(--red)}.badge.unknown{color:var(--muted);background:#151c2b}main{max-width:1180px;margin:0 auto;padding:22px}.hero{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(300px,.65fr);gap:16px;margin-bottom:18px}.decision{background:linear-gradient(135deg,#15243a,#101827);border:1px solid var(--line);border-radius:10px;padding:20px;min-height:190px;display:flex;flex-direction:column;justify-content:space-between}.decision .eyebrow{font-size:.76rem;color:var(--muted);text-transform:uppercase;font-weight:750;letter-spacing:.07em}.decision h2{font-size:1.55rem;line-height:1.15;margin:8px 0 10px}.decision p{color:#c8d5e8;font-size:.95rem;line-height:1.45}.threshold{background:#101827;border:1px solid var(--line);border-radius:10px;padding:16px}.threshold .big{font-size:2rem;font-weight:850;font-variant-numeric:tabular-nums}.threshold .sub{color:var(--muted);font-size:.8rem;margin-top:4px}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:18px}.card{background:rgba(17,24,39,.95);border:1px solid var(--line);border-radius:8px;padding:14px;min-height:88px}.card .lbl{color:var(--muted);font-size:.76rem;font-weight:680;margin-bottom:7px}.card .val{font-size:1.35rem;font-weight:820;font-variant-numeric:tabular-nums}.card.good .val{color:var(--green)}.card.warn .val{color:var(--amber)}.card.bad .val{color:var(--red)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}section{background:rgba(17,24,39,.92);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}section h3{font-size:.82rem;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:14px}.ov-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.ov-row button,.btn-save{border:1px solid var(--line);border-radius:7px;background:#182236;color:var(--text);padding:9px 14px;cursor:pointer;font-size:.9rem;font-weight:700}.ov-row button:hover,.btn-save:hover{background:#202d45}.ov-row button.active{background:var(--blue);border-color:var(--blue);color:#07111f}.btn-save{background:#1f8f55;border-color:#2ac06e}.fg{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.field{display:flex;flex-direction:column;gap:6px}.field label{font-size:.8rem;font-weight:720;color:#c9d6e8}.field input{background:#0c1322;border:1px solid var(--line);border-radius:7px;color:var(--text);padding:9px 10px}.field input:focus{outline:none;border-color:var(--blue)}.hint{font-size:.76rem;color:var(--muted);line-height:1.4}.hint em{font-style:normal;color:#e8f1ff;font-weight:800}.ok{color:var(--green);font-size:.85rem}.err{color:var(--red);font-size:.85rem}.ts{color:var(--muted);font-size:.76rem;margin-left:10px}.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}.flow div{background:#0d1526;border:1px solid var(--line);border-radius:8px;padding:10px}.flow span{display:block;color:var(--muted);font-size:.72rem;margin-bottom:4px}.flow b{font-size:1rem;font-variant-numeric:tabular-nums}@media(max-width:850px){.hero,.grid{grid-template-columns:1fr}.cards{grid-template-columns:repeat(2,1fr)}.fg{grid-template-columns:1fr}}@media(max-width:520px){main{padding:12px}header{padding:0 14px}.head-left{gap:10px}.tabs button{padding:6px 8px}.cards{grid-template-columns:1fr}.flow{grid-template-columns:1fr 1fr}.decision h2{font-size:1.25rem}}
</style>
</head>
<body>
<header><div class="head-left"><h1>pv-miner</h1><nav class="tabs"><button id="tab-dashboard" class="active" onclick="showView('dashboard')">Live</button><button id="tab-settings" onclick="showView('settings')">Einstellungen</button></nav></div><span id="badge" class="badge unknown">—</span></header>
<main>
<div id="view-dashboard" class="view active">
  <div class="hero">
    <div class="decision">
      <div>
        <div class="eyebrow">Automatikentscheidung <span id="ts" class="ts"></span></div>
        <h2 id="decision-title">Warte auf Daten</h2>
        <p id="decision-reason">Fronius und Miner werden abgefragt.</p>
      </div>
      <div class="flow">
        <div><span>Haus ohne Miner</span><b id="v-house">—</b></div>
        <div><span>Akku-Reserve</span><b id="v-batt-reserve">—</b></div>
        <div><span>Miner benötigt</span><b id="v-miner-need">—</b></div>
        <div><span>Puffer</span><b id="v-buffer">—</b></div>
      </div>
    </div>
    <div class="threshold">
      <div class="hint">Mining erlaubt ab</div>
      <div class="big" id="v-required">—</div>
      <div class="sub" id="v-required-sub">PV-Produktion, damit Akku Vorrang behält.</div>
    </div>
  </div>

  <div class="cards">
    <div class="card" id="c-soc"><div class="lbl">Akku</div><div class="val" id="v-soc">—</div></div>
    <div class="card"><div class="lbl">PV Produktion</div><div class="val" id="v-ppv">—</div></div>
    <div class="card" id="c-grid"><div class="lbl" id="l-pgrid">Netz</div><div class="val" id="v-pgrid">—</div></div>
    <div class="card" id="c-batt"><div class="lbl" id="l-pakku">Batterie</div><div class="val" id="v-pakku">—</div></div>
    <div class="card"><div class="lbl">Haus gesamt</div><div class="val" id="v-pload">—</div></div>
    <div class="card"><div class="lbl">Miner aktuell</div><div class="val" id="v-power">—</div></div>
    <div class="card"><div class="lbl">Verfügbar</div><div class="val" id="v-verfuegbar">—</div></div>
    <div class="card"><div class="lbl">Nächste Prüfung</div><div class="val" id="v-next">—</div></div>
  </div>

  <div class="grid">
    <section>
      <h3>Steuerung</h3>
      <div class="ov-row">
        <button id="ov-auto" onclick="setOv('auto')">Auto</button>
        <button id="ov-pause" onclick="setOv('pause')">Pause</button>
        <button id="ov-run" onclick="setOv('run')">Start erzwingen</button>
      </div>
      <div class="hint" style="margin-top:10px">Auto: Akku hat Vorrang. Pause und Start erzwingen überschreiben die Automatik bis du wieder Auto aktivierst.</div>
      <div id="cmdmsg" class="hint" style="margin-top:8px"></div>
    </section>

    <section>
      <h3>System</h3>
      <div class="ov-row"><button id="btn-update" onclick="doUpdate()">Update prüfen</button><span id="umsg"></span></div>
      <div class="hint" style="margin-top:10px">Updates werden nur installiert, wenn GitHub eine andere Version liefert.</div>
    </section>
  </div>
</div>

<div id="view-settings" class="view">
  <section>
    <h3>Setup</h3>
    <div class="fg">
      <div class="field"><label>Fronius GEN24 Plus — IP</label><input id="f-fh" placeholder="172.16.40.17"><div class="hint">Hybrid-Wechselrichter mit Batterie.</div></div>
      <div class="field"><label>2. Wechselrichter — IP optional</label><input id="f-fh2" placeholder="leer lassen"><div class="hint">Nur wenn dessen PV nicht im Hybrid-P_PV enthalten ist.</div></div>
      <div class="field"><label>Antminer — IP</label><input id="f-mh" placeholder="172.16.40.x"><div class="hint">Braiins OS REST API.</div></div>
      <div class="field"><label>Braiins OS Passwort root</label><input id="f-ak" type="password" placeholder="leer = kein Passwort"><div class="hint">Nur für Login/API.</div></div>
    </div>
  </section>

  <section>
    <h3>Akku zuerst</h3>
    <div class="fg">
      <div class="field"><label>Miner benötigt (W)</label><input id="f-mneed" type="number" min="2500" max="10000" step="50" oninput="updateConfigHints()"><div class="hint" id="h-mneed">Mindestens 2500 W. Wird genutzt, wenn der Miner noch aus ist oder langsam hochfährt.</div></div>
      <div class="field"><label>Max. Akku-Ladeleistung (W)</label><input id="f-bcl" type="number" min="0" max="30000" step="100" oninput="updateConfigHints()"><div class="hint" id="h-bcl">Default 11300 W. Solange der Akku nicht voll ist, reserviert pv-miner diese Leistung für den Akku.</div></div>
      <div class="field"><label>Akku gilt als voll ab (%)</label><input id="f-full" type="number" min="90" max="100" step="0.1" oninput="updateConfigHints()"><div class="hint" id="h-full">Bei vollem Akku fällt die 11,3-kW-Reserve weg.</div></div>
      <div class="field"><label>Sicherheitspuffer (W)</label><input id="f-buffer" type="number" min="0" max="5000" step="50" oninput="updateConfigHints()"><div class="hint" id="h-buffer">Zusätzlicher Abstand, damit nicht aus dem Netz oder Akku gezogen wird.</div></div>
      <div class="field"><label>Akku-Entlade-Sperre (W)</label><input id="f-abs" type="number" min="0" max="2000" step="50" oninput="updateConfigHints()"><div class="hint" id="h-abs">Wenn P_Akku darüber liegt, wird pausiert.</div></div>
      <div class="field"><label>Start erst nach stabiler Sonne (Minuten)</label><input id="f-startmin" type="number" min="1" max="60"><div class="hint">Nach einer Pause startet der Miner erst wieder, wenn die Startbedingung so lange stabil erfüllt ist.</div></div>
      <div class="field"><label>Abfrage-Intervall (Sekunden)</label><input id="f-pi" type="number" min="10" max="300"><div class="hint">Wie oft Fronius und Miner abgefragt werden.</div></div>
    </div>
    <div class="ov-row" style="margin-top:16px"><button class="btn-save" onclick="saveCfg()">Speichern</button><span id="smsg"></span></div>
  </section>
</div>
</main>
<script>
function el(id){return document.getElementById(id)}
function showView(name){
  ['dashboard','settings'].forEach(v=>{
    el('view-'+v).classList.toggle('active',v===name);
    el('tab-'+v).classList.toggle('active',v===name);
  });
}
function n(id,def){const x=+el(id)?.value;return Number.isFinite(x)?x:def}
function fw(v){return v==null?'—':Math.round(v)+' W'}
function absw(v){return v==null?'—':Math.round(Math.abs(v))+' W'}
function kw(v){return v==null?'—':(v/1000).toFixed(1)+' kW'}
function cls(card,kind){card.className='card '+(kind||'')}
function updateConfigHints(){
  const m=n('f-mneed',2800), b=n('f-bcl',11300), full=n('f-full',100), buf=n('f-buffer',200), abs=n('f-abs',100);
  el('h-mneed').innerHTML=`pv-miner rechnet mit mindestens <em>${Math.max(2500,m)} W</em>. Sobald der Miner real mehr zieht, wird der höhere Wert genutzt.`;
  el('h-bcl').innerHTML=`Akku bekommt bis <em>${(b/1000).toFixed(1)} kW</em> Vorrang, solange er nicht voll ist.`;
  el('h-full').innerHTML=`Ab <em>${full}% SOC</em> gilt der Akku als voll.`;
  el('h-buffer').innerHTML=`Zusätzlich <em>${buf} W</em> Reserve gegen Netzbezug/Akkuentladung.`;
  el('h-abs').innerHTML=`Bei Akku-Entladung über <em>${abs} W</em> wird die Automatik pausiert.`;
}
async function fetchStatus(){
  try{
    const d=await(await fetch('/api/status',{cache:'no-store'})).json();
    el('v-soc').textContent=d.soc!=null?d.soc.toFixed(1)+'%':'—';
    el('v-ppv').textContent=fw(d.p_pv); el('v-pload').textContent=absw(d.p_load); el('v-power').textContent=fw(d.miner_power_w);
    el('l-pgrid').textContent=d.p_grid==null?'Netz':(d.p_grid<0?'Netz Einspeisung':(d.p_grid>0?'Netz Bezug':'Netz neutral')); el('v-pgrid').textContent=absw(d.p_grid);
    el('l-pakku').textContent=d.p_akku==null?'Batterie':(d.p_akku>0?'Batterie entlädt':(d.p_akku<0?'Batterie lädt':'Batterie neutral')); el('v-pakku').textContent=absw(d.p_akku);
    el('v-house').textContent=fw(d.house_without_miner_w); el('v-batt-reserve').textContent=fw(d.battery_reserve_w); el('v-miner-need').textContent=fw(d.miner_needed_w); el('v-buffer').textContent=fw(d.grid_buffer_watt);
    el('v-required').textContent=kw(d.required_pv_w); el('v-required-sub').textContent=d.soc!=null&&d.soc>=d.battery_full_soc?'Akku voll: nur Haus + Miner + Puffer nötig.':'Akku lädt zuerst: Haus + Akku-Ladelimit + Miner + Puffer.';
    el('v-verfuegbar').textContent=fw(d.available_w); el('v-next').textContent=(d.poll_interval_seconds||30)+' s';
    const st=d.display_state||'unknown'; const b=el('badge'); b.className='badge '+st; b.textContent=st==='mining'?'Mining':(st==='paused'?'Pausiert':'—');
    el('decision-title').textContent=d.decision_title||'Warte auf Daten'; el('decision-reason').textContent=d.decision_reason||'';
    cls(el('c-soc'),d.soc==null?'':(d.soc>=d.battery_full_soc?'good':'warn')); cls(el('c-grid'),d.p_grid==null?'':(d.p_grid>50?'bad':(d.p_grid<-50?'good':''))); cls(el('c-batt'),d.p_akku==null?'':(d.p_akku>100?'bad':(d.p_akku<0?'good':'')));
    ['auto','pause','run'].forEach(m=>el('ov-'+m).classList.toggle('active',m===(d.manual_override||'auto')));
    if(d.command_state==='ok'){el('cmdmsg').className='hint';el('cmdmsg').textContent=d.command_msg||'Befehl bestätigt';}
    else if(d.command_state){el('cmdmsg').className='hint warn';el('cmdmsg').textContent=d.command_msg||'Befehl nicht bestätigt';}
    else el('cmdmsg').textContent='';
    el('ts').textContent='aktualisiert '+new Date().toLocaleTimeString('de-AT');
  }catch(e){}
}
async function fetchCfg(){
  try{const d=await(await fetch('/api/config',{cache:'no-store'})).json();
    el('f-fh').value=d.fronius?.host||''; el('f-fh2').value=d.fronius?.pv2_host||''; el('f-pi').value=d.fronius?.poll_interval_seconds??30;
    el('f-mh').value=d.miner?.host||''; el('f-ak').value=d.miner?.api_key||''; el('f-mneed').value=Math.max(2500,d.miner?.expected_power_watt??2800);
    el('f-bcl').value=d.control?.battery_charge_limit_watt??11300; el('f-full').value=d.control?.battery_full_soc??100; el('f-buffer').value=d.control?.grid_buffer_watt??200; el('f-abs').value=d.control?.akku_entlade_sperre_watt??100; el('f-startmin').value=d.control?.start_stable_minutes??5;
    updateConfigHints();
  }catch(e){}
}
async function saveCfg(){
  const msg=el('smsg');
  const cfg={fronius:{host:el('f-fh').value.trim(),pv2_host:el('f-fh2').value.trim(),poll_interval_seconds:n('f-pi',30)},miner:{host:el('f-mh').value.trim(),api_key:el('f-ak').value.trim(),expected_power_watt:Math.max(2500,n('f-mneed',2800))},control:{battery_charge_limit_watt:n('f-bcl',11300),battery_full_soc:n('f-full',100),grid_buffer_watt:n('f-buffer',200),akku_entlade_sperre_watt:n('f-abs',100),start_stable_minutes:n('f-startmin',5)}};
  try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)}); if(r.ok){msg.className='ok';msg.textContent='Gespeichert';}else{const e=await r.json();msg.className='err';msg.textContent=e.error||'Fehler';}}
  catch(e){msg.className='err';msg.textContent='Netzwerkfehler';}
  setTimeout(()=>msg.textContent='',4000);
}
async function setOv(mode){await fetch('/api/override',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})}).catch(()=>{});fetchStatus();}
async function doUpdate(){
  const btn=el('btn-update'), msg=el('umsg'); btn.disabled=true; msg.className=''; msg.textContent='Prüfe Update...';
  try{const r=await fetch('/api/update',{method:'POST'}); const result=await r.json(); if(!r.ok){msg.className='err';msg.textContent=result.error||'Update fehlgeschlagen';btn.disabled=false;return;} if(result.updated===false){msg.className='ok';msg.textContent='Bereits aktuell.';btn.disabled=false;return;}}
  catch(e){msg.className='err';msg.textContent='Netzwerkfehler';btn.disabled=false;return;}
  msg.textContent='Update installiert, Service startet neu...'; let stable=0,tries=0,started=Date.now();
  const poll=setInterval(async()=>{tries++;try{const r=await fetch('/api/status?u='+Date.now(),{cache:'no-store'});if(!r.ok)throw new Error();await r.json();stable++;if(Date.now()-started<6000||stable<2)return;clearInterval(poll);msg.className='ok';msg.textContent='Update erfolgreich.';setTimeout(()=>location.reload(),1200);}catch(e){stable=0;if(tries>=60){clearInterval(poll);msg.className='err';msg.textContent='Service antwortet nicht. Logs prüfen.';btn.disabled=false;}}},1000);
}
fetchStatus();fetchCfg();setInterval(fetchStatus,10000);
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

    @staticmethod
    def _first_soc(inverters: dict) -> float | None:
        for inv in inverters.values():
            if not isinstance(inv, dict) or "SOC" not in inv:
                continue
            try:
                return float(inv["SOC"])
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _base(host: str) -> str:
        host = host.strip()
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        parsed = urlparse(host)
        netloc = parsed.netloc or parsed.path
        scheme = parsed.scheme or "http"
        return f"{scheme}://{netloc}"

    def _fetch(self, host: str) -> dict:
        """Fetch the PowerFlow Body.Data dict from one inverter."""
        url = f"{self._base(host)}/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
        r = _http.get(url, timeout=self._timeout)
        r.raise_for_status()
        return r.json()["Body"]["Data"]

    def get_powerflow(self) -> dict | None:
        cfg = self._cfg.get()["fronius"]
        host = cfg["host"]
        if not host:
            return None
        try:
            data      = self._fetch(host)
            site      = data["Site"]
            inverters = data.get("Inverters", {})
            soc = self._first_soc(inverters)
            if soc is None:
                raise ValueError("Fronius response has no battery SOC")
            p_grid = float(site.get("P_Grid") or 0.0)
            p_akku = float(site.get("P_Akku") or 0.0)
            p_pv   = float(site.get("P_PV")   or 0.0)
            p_load = float(site.get("P_Load") or 0.0)

            # Second inverter (e.g. a Symo that is NOT linked to the hybrid):
            # its production is invisible to the hybrid's local API, so query
            # it separately, add its PV and recompute the house load from the
            # whole-house balance  P_Load = -(P_Grid + P_Akku + P_PV).
            pv2_host = (cfg.get("pv2_host") or "").strip()
            if pv2_host and pv2_host != host.strip():
                try:
                    site2 = self._fetch(pv2_host)["Site"]
                    p_pv += float(site2.get("P_PV") or 0.0)
                    p_load = -(p_grid + p_akku + p_pv)
                except Exception as exc:
                    logging.getLogger("api").warning(
                        "Fronius 2. Wechselrichter (%s): %s — nutze nur Haupt-PV",
                        pv2_host, exc)

            return {
                "p_grid": p_grid,
                "p_pv":   p_pv,
                "p_akku": p_akku,
                "p_load": p_load,
                "soc":    soc,
            }
        except Exception as exc:
            logging.getLogger("api").warning("Fronius: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Braiins OS API  (Public REST API — token auth)
# ---------------------------------------------------------------------------

class BraiinsAPI:
    """Braiins OS Public API client (REST, ``/api/v1``).

    Auth
    ----
    ``POST /api/v1/auth/login`` with ``{"username","password"}`` returns
    ``{"token", "timeout_s"}``. The token is sent in the ``authorization``
    header **without** a "Bearer" prefix. It is refreshed before expiry and
    on any 401.

    The ``api_key`` config field holds the password of the ``root`` account.
    Leave it empty if no password is set (factory default).

    Scope
    -----
    pv-miner uses ONLY pause / resume. It never sets a power target, hashrate
    target, autotuning or fan settings. The miner keeps whatever is configured
    in Braiins OS itself.

    Endpoints used:
      PUT  /api/v1/actions/pause    — pause mining (miner status → 3)
      PUT  /api/v1/actions/resume   — resume mining (miner status → 2)
      GET  /api/v1/miner/details    — { "status": 1|2|3, ... }
      GET  /api/v1/miner/stats      — power_stats.approximated_consumption.watt

    Miner status values: 2 = mining, 3 = paused, 1 = idle (bosminer up,
    not mining).
    """

    _STATUS_MINING = 2

    def __init__(self, cfg: ConfigManager, timeout: int = 10):
        self._cfg     = cfg
        self._timeout = timeout
        self._log     = logging.getLogger("api")
        self._token: str = ""
        self._token_expiry: float = 0.0

    # ── helpers ───────────────────────────────────────────────────────────────

    def _base(self) -> str:
        host = self._cfg.get()["miner"]["host"].strip()
        if not host:
            return ""
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        parsed = urlparse(host)
        return f"{parsed.scheme or 'http'}://{parsed.netloc or parsed.path}/api/v1"

    def _login(self) -> bool:
        base = self._base()
        if not base:
            return False
        password = self._cfg.get()["miner"].get("api_key", "") or ""
        try:
            r = _http.post(f"{base}/auth/login",
                           json={"username": "root", "password": password},
                           timeout=self._timeout)
            r.raise_for_status()
            d = r.json()
            self._token = d["token"]
            ttl = int(d.get("timeout_s", 3600))
            self._token_expiry = time.time() + max(60, ttl - 100)
            self._log.info("Braiins login OK")
            return True
        except Exception as exc:
            self._log.warning("Braiins login failed: %s", exc)
            self._token = ""
            self._token_expiry = 0.0
            return False

    def _request(self, method: str, path: str, json: dict | None = None,
                 _retry: bool = True):
        """Authenticated request. Returns the Response or None on failure."""
        base = self._base()
        if not base:
            return None
        if not self._token or time.time() >= self._token_expiry:
            if not self._login():
                return None
        try:
            r = _http.request(method, f"{base}{path}",
                              headers={"authorization": self._token},
                              json=json, timeout=self._timeout)
            if r.status_code == 401 and _retry:
                self._log.info("Braiins token expired — re-logging in")
                self._token_expiry = 0.0
                if not self._login():
                    return None
                return self._request(method, path, json=json, _retry=False)
            r.raise_for_status()
            return r
        except Exception as exc:
            self._log.warning("Braiins %s %s: %s", method, path, exc)
            return None

    @staticmethod
    def _ok(resp) -> bool:
        """An action succeeded if it returned HTTP 2xx and no error body."""
        if resp is None:
            return False
        try:
            body = resp.json()
        except Exception:
            return True  # empty / non-JSON body on 2xx is fine
        return not (isinstance(body, dict) and body.get("error"))

    # ── public API ────────────────────────────────────────────────────────────

    def pause(self) -> bool:
        r = self._request("PUT", "/actions/pause")
        ok = self._ok(r)
        if ok:
            self._log.debug("pause OK")
        else:
            self._log.warning("pause failed: %s", r.text if r is not None else "no response")
        return ok

    def resume(self) -> bool:
        r = self._request("PUT", "/actions/resume")
        ok = self._ok(r)
        if ok:
            self._log.debug("resume OK")
        else:
            self._log.warning("resume failed: %s", r.text if r is not None else "no response")
        return ok

    def get_status(self) -> dict | None:
        """Return {power_watt, paused} or None.

        ``paused`` is True whenever the miner is not actively mining
        (status != 2). ``power_watt`` is the real draw while mining, 0 while
        paused (the stale post-pause reading would otherwise distort the
        surplus calculation).
        """
        rd = self._request("GET", "/miner/details")
        if rd is None:
            return None
        try:
            status = rd.json().get("status")
        except Exception as exc:
            self._log.warning("Braiins miner/details parse: %s", exc)
            return None

        if status != self._STATUS_MINING:
            return {"power_watt": 0, "paused": True}

        watt = 0
        rs = self._request("GET", "/miner/stats")
        if rs is not None:
            try:
                ps = rs.json().get("power_stats") or {}
                watt = int((ps.get("approximated_consumption") or {}).get("watt") or 0)
            except Exception:
                watt = 0
        return {"power_watt": watt, "paused": False}


# ---------------------------------------------------------------------------
# Shared state (web UI ↔ control loop)
# ---------------------------------------------------------------------------

class StateStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._d: dict = {
            "soc": None, "p_grid": None, "p_pv": None, "p_akku": None,
            "p_load": None, "house_without_miner_w": None,
            "battery_reserve_w": None, "miner_needed_w": None,
            "grid_buffer_watt": None, "required_pv_w": None,
            "available_w": None, "verfuegbar_w": None,
            "miner_power_w": None, "battery_full_soc": None,
            "start_wait_remaining_s": None,
            "poll_interval_seconds": None,
            "display_state": "unknown", "manual_override": "auto",
            "decision_title": "Warte auf Daten", "decision_reason": "",
            "command_state": None, "command_msg": None,
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
        self._cur_action: str | None = None
        self._start_since: float | None = None
        self._fronius_err = 0
        self._braiins_err = 0

    @staticmethod
    def _display(action: str | None) -> str:
        if action not in ("run", "pause"):
            return "unknown"
        return "mining" if action == "run" else "paused"

    @staticmethod
    def _decision_numbers(pf: dict, cfg: dict, miner_w_now: int) -> dict:
        configured_miner_need = max(2500, int(cfg["miner"].get("expected_power_watt", 2800)))
        miner_need = max(configured_miner_need, max(0, miner_w_now))
        full_soc = float(cfg["control"].get("battery_full_soc", 100))
        battery_limit = int(cfg["control"].get("battery_charge_limit_watt", 11300))
        buffer_w = int(cfg["control"].get("grid_buffer_watt", 200))
        house_without_miner = max(0.0, abs(pf.get("p_load", 0.0)) - max(0, miner_w_now))
        battery_reserve = 0 if pf["soc"] >= full_soc else battery_limit
        required_pv = house_without_miner + battery_reserve + miner_need + buffer_w
        available = pf["p_pv"] - required_pv
        return {
            "house_without_miner_w": house_without_miner,
            "battery_reserve_w": battery_reserve,
            "miner_needed_w": miner_need,
            "grid_buffer_watt": buffer_w,
            "required_pv_w": required_pv,
            "available_w": available,
            "battery_full_soc": full_soc,
        }

    def _decide(self, pf: dict, cfg: dict, miner_w_now: int) -> tuple[str, dict, str, str]:
        override = cfg.get("modes", {}).get("manual_override", "auto")
        nums = self._decision_numbers(pf, cfg, miner_w_now)
        discharge_limit = int(cfg["control"].get("akku_entlade_sperre_watt", 100))

        if override == "pause":
            return "pause", nums, "Pause erzwungen", "Die Automatik ist pausiert."
        if override == "run":
            return "run", nums, "Start erzwungen", "Der Miner wird unabhängig von PV/Akku gestartet."

        if pf["p_akku"] > discharge_limit:
            return "pause", nums, "Akku entlädt", (
                f"Akku entlädt mit {pf['p_akku']:.0f} W. Miner pausiert."
            )

        if pf["p_pv"] >= nums["required_pv_w"]:
            if nums["battery_reserve_w"] > 0:
                return "run", nums, "Mining erlaubt", "PV reicht für Haus, volle Akku-Ladeleistung, Miner und Puffer."
            return "run", nums, "Mining erlaubt", "Akku ist voll; PV reicht für Haus, Miner und Puffer."

        missing = nums["required_pv_w"] - pf["p_pv"]
        if nums["battery_reserve_w"] > 0:
            return "pause", nums, "Akku lädt zuerst", (
                f"Es fehlen {missing:.0f} W, damit der Akku mit voller Leistung laden kann und der Miner zusätzlich läuft."
            )
        return "pause", nums, "Zu wenig PV", f"Es fehlen {missing:.0f} W für Haus, Miner und Puffer."

    def _auto_gate(self, desired: str, cfg: dict) -> str:
        """Pause immediately; start only after stable sun for N minutes."""
        if desired == "pause":
            self._start_since = None
            return "pause"
        if self._cur_action == "run":
            self._start_since = None
            return "run"

        wait_s = max(0, float(cfg["control"].get("start_stable_minutes", 5))) * 60
        now = time.monotonic()
        if self._start_since is None:
            self._start_since = now
        if now - self._start_since >= wait_s:
            self._start_since = None
            return "run"
        return "pause"

    def _verify(self, action: str) -> bool:
        want_paused = action == "pause"
        for _ in range(4):
            time.sleep(3)
            st = self._braiins.get_status()
            if st is not None and st["paused"] == want_paused:
                return True
        return False

    def _apply(self, action: str) -> None:
        if self._cur_action is not None and action == self._cur_action:
            return
        issued = self._braiins.pause() if action == "pause" else self._braiins.resume()
        verb = "Pause" if action == "pause" else "Start"
        if not issued:
            self._braiins_err += 1
            self._state.update(command_state="failed", command_msg=f"{verb}-Befehl wurde vom Miner nicht angenommen")
            return
        if self._verify(action):
            self._cur_action = action
            self._braiins_err = 0
            self._state.update(command_state="ok", display_state=self._display(action), command_msg=f"{verb} vom Miner bestätigt")
        else:
            self._braiins_err += 1
            self._state.update(command_state="unconfirmed", command_msg=f"{verb} gesendet, aber vom Miner nicht bestätigt")

    def run_cycle(self) -> None:
        cfg = self._cfg.get()
        override = cfg.get("modes", {}).get("manual_override", "auto")
        poll_interval = cfg.get("fronius", {}).get("poll_interval_seconds", 30)
        miner_host = cfg.get("miner", {}).get("host")
        pf = self._fronius.get_powerflow()
        miner_st = self._braiins.get_status() if miner_host else None
        miner_w_now = miner_st["power_watt"] if miner_st else 0
        if miner_st is not None:
            self._cur_action = "pause" if miner_st["paused"] else "run"

        if pf is None:
            self._fronius_err += 1
            self._log.warning("Fronius unreachable (streak: %d)", self._fronius_err)
            if self._fronius_err >= 3 and miner_host and self._cur_action != "pause" and override == "auto":
                self._apply("pause")
            self._state.update(
                miner_power_w=miner_w_now if miner_st else None,
                display_state=self._display(self._cur_action) if miner_host else "unknown",
                manual_override=override,
                poll_interval_seconds=poll_interval,
                decision_title="Fronius nicht erreichbar",
                decision_reason="Ohne Wechselrichterdaten wird im Auto-Modus sicherheitshalber pausiert.",
            )
            return

        self._fronius_err = 0
        nums = self._decision_numbers(pf, cfg, miner_w_now)

        if not miner_host:
            self._state.update(
                soc=pf["soc"], p_grid=pf["p_grid"], p_pv=pf["p_pv"], p_akku=pf["p_akku"], p_load=pf.get("p_load"),
                miner_power_w=None, verfuegbar_w=max(0, nums["available_w"]), manual_override=override,
                display_state="unknown", poll_interval_seconds=poll_interval,
                decision_title="Antminer fehlt", decision_reason="Fronius wird angezeigt; zum Schalten fehlt noch die Antminer-IP.",
                **nums,
            )
            return

        desired, nums, title, reason = self._decide(pf, cfg, miner_w_now)
        action = desired if override != "auto" else self._auto_gate(desired, cfg)
        wait_remaining = None
        if override == "auto" and desired == "run" and action == "pause" and self._start_since is not None:
            wait_s = max(0, float(cfg["control"].get("start_stable_minutes", 5))) * 60
            wait_remaining = max(0, int(wait_s - (time.monotonic() - self._start_since)))
            title = "Warte auf stabile Sonne"
            reason = f"Startbedingung erfüllt. Miner startet in {wait_remaining // 60}:{wait_remaining % 60:02d}, wenn genug PV stabil bleibt."

        self._state.update(
            soc=pf["soc"], p_grid=pf["p_grid"], p_pv=pf["p_pv"], p_akku=pf["p_akku"], p_load=pf.get("p_load"),
            miner_power_w=miner_w_now if miner_st else None,
            verfuegbar_w=max(0, nums["available_w"]),
            display_state=self._display(action), manual_override=override,
            poll_interval_seconds=poll_interval, decision_title=title, decision_reason=reason,
            start_wait_remaining_s=wait_remaining,
            **nums,
        )

        if self._cur_action is not None and action == self._cur_action:
            self._log.info("[cycle] SOC=%.1f%% PV=%.0fW required=%.0fW → no change (%s)",
                           pf["soc"], pf["p_pv"], nums["required_pv_w"], self._cur_action)
            return

        self._log.info("[cycle] SOC=%.1f%% PV=%.0fW required=%.0fW → %s",
                       pf["soc"], pf["p_pv"], nums["required_pv_w"], action.upper())
        self._apply(action)

# ---------------------------------------------------------------------------
# Flask web app
# ---------------------------------------------------------------------------

def validate_config_patch(data: dict) -> str | None:
    ctrl = data.get("control", {})
    miner = data.get("miner", {})

    try:
        if not (2500 <= int(miner.get("expected_power_watt", 2800)) <= 10000):
            return "Miner benötigt muss zwischen 2500 und 10000 W liegen"
        if not (0 <= int(ctrl.get("battery_charge_limit_watt", 11300)) <= 30000):
            return "Max. Akku-Ladeleistung muss zwischen 0 und 30000 W liegen"
        if not (90 <= float(ctrl.get("battery_full_soc", 100)) <= 100):
            return "Akku voll ab muss zwischen 90 und 100% liegen"
        if not (0 <= int(ctrl.get("grid_buffer_watt", 200)) <= 5000):
            return "Sicherheitspuffer muss zwischen 0 und 5000 W liegen"
        if not (0 <= int(ctrl.get("akku_entlade_sperre_watt", 100)) <= 2000):
            return "Akku-Entlade-Sperre muss zwischen 0 und 2000 W liegen"
        if not (1 <= int(ctrl.get("start_stable_minutes", 5)) <= 60):
            return "Start-Wartezeit muss zwischen 1 und 60 Minuten liegen"
    except (TypeError, ValueError):
        return "Numerische Konfigurationswerte sind ungültig"

    return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_busted_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["_"] = str(int(time.time()))
    return urlunparse(parsed._replace(query=urlencode(query)))


def _download_update() -> tuple[bytes | None, str | None, str]:
    try:
        r = _http.get(
            _cache_busted_url(UPDATE_URL),
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            timeout=15,
        )
        r.raise_for_status()
        ast.parse(r.content.decode("utf-8"))
        return r.content, _sha256_bytes(r.content), ""
    except Exception as exc:
        return None, None, str(exc)


def _write_update(remote: bytes) -> tuple[str, Path]:
    bin_path = Path(__file__).resolve()
    new_path = bin_path.with_suffix(bin_path.suffix + ".new")
    previous_path = bin_path.with_suffix(bin_path.suffix + ".previous")
    new_path.write_bytes(remote)
    shutil.copy2(bin_path, previous_path)
    os.replace(new_path, bin_path)
    return _sha256_bytes(remote), previous_path


def _start_detached_restart(expected_hash: str, previous_path: Path) -> None:
    script = Path("/tmp/pv-miner-web-update.sh")
    script.write_text(f"""#!/bin/sh
set -eu
EXPECTED_HASH='{expected_hash}'
BIN='{Path(__file__).resolve()}'
PREVIOUS='{previous_path}'
PORT='{WEB_PORT}'
LOG='/tmp/pv-miner-web-update.log'
{{
  echo "Restarting pv-miner for update $EXPECTED_HASH"
  sleep 1
  rc-service pv-miner restart || true
  for i in $(seq 1 30); do
    BODY=$(wget -qO- "http://127.0.0.1:$PORT/api/version?u=$(date +%s)" 2>/dev/null || true)
    echo "$BODY" | grep -q "$EXPECTED_HASH" && {{ echo "Update verified"; exit 0; }}
    sleep 1
  done
  echo "Updated service did not verify; rolling back"
  cp "$PREVIOUS" "$BIN"
  rc-service pv-miner restart || true
  exit 1
}} >> "$LOG" 2>&1
""")
    script.chmod(0o755)
    subprocess.Popen(
        [str(script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def create_app(cfg_manager: ConfigManager, state: StateStore) -> Flask:
    app = Flask(__name__)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @app.after_request
    def _no_cache(resp):
        # The whole UI is generated dynamically and changes on every update —
        # never let the browser serve a stale copy of the page or the APIs.
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"]        = "no-cache"
        resp.headers["Expires"]       = "0"
        return resp

    @app.route("/")
    def index():
        return Response(HTML_PAGE, mimetype="text/html")

    @app.route("/api/status")
    def api_status():
        return jsonify(state.snapshot())

    @app.route("/api/version")
    def api_version():
        path = Path(__file__).resolve()
        return jsonify({
            "file": str(path),
            "sha256": _sha256_file(path),
            "pid": os.getpid(),
            "update_url": UPDATE_URL,
        })

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
        if not data.get("fronius", {}).get("host"):
            return jsonify({"error": "Fronius GEN24 Plus — IP darf nicht leer sein"}), 400
        if data.get("miner", {}).get("api_key", "").startswith("••"):
            data.setdefault("miner", {})["api_key"] = cfg_manager.get()["miner"].get("api_key", "")
        error = validate_config_patch(data)
        if error:
            return jsonify({"error": error}), 400
        cfg_manager.update(data)
        return jsonify({"ok": True})

    @app.route("/api/update", methods=["POST"])
    def api_update():
        remote, remote_hash, error = _download_update()
        if error:
            return jsonify({"error": f"Update-Prüfung fehlgeschlagen: {error}"}), 502
        assert remote is not None and remote_hash is not None
        local_hash = _sha256_file(Path(__file__))
        if remote_hash == local_hash:
            logging.getLogger("main").info("Update requested, already current")
            return jsonify({"ok": True, "updated": False})

        try:
            expected_hash, previous_path = _write_update(remote)
            _start_detached_restart(expected_hash, previous_path)
        except Exception as exc:
            logging.getLogger("main").exception("Update install failed")
            return jsonify({"error": f"Update konnte nicht installiert werden: {exc}"}), 500

        logging.getLogger("main").info("Update installed via web UI: %s", expected_hash)
        return jsonify({"ok": True, "updated": True, "sha256": expected_hash})

    @app.route("/api/override", methods=["POST"])
    def api_override():
        data = request.get_json(silent=True) or {}
        mode = data.get("mode", "auto")
        if mode not in ("auto", "pause", "run"):
            return jsonify({"error": "Invalid mode"}), 400
        cfg_manager.set_override(mode)
        state.update(manual_override=mode, command_state=None, command_msg=None)
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
        # Leave the miner exactly as it is on shutdown — a service restart or
        # update must not disturb mining. The miner keeps its own state.
        log.info("Control loop stopped — miner left untouched")

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

#!/usr/bin/env python3
"""pv-miner — web-controlled PV surplus mining daemon."""

import json
import ast
import hashlib
import logging
import logging.handlers
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse, parse_qsl, urlencode

from flask import Flask, Response, jsonify, request
import requests as _http

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/config.json")
WEB_PORT    = int(os.environ.get("WEB_PORT", "8080"))
LOG_FILE    = os.environ.get("LOG_FILE", "/var/log/pv-miner.log")
UPDATE_URL  = os.environ.get(
    "UPDATE_URL",
    "https://raw.githubusercontent.com/traktuner/pv-miner/master/pv_miner.py",
)
RUN_MODES = ("auto", "pause", "fixed_hashrate", "fixed_power", "off")

DEFAULT_CONFIG: dict = {
    "fronius": {
        "host": "",
        "pv2_host": "",
        "poll_interval_seconds": 30,
    },
    "miner": {
        "host": "",
        "api_key": "",
    },
    "control": {
        "enable_start_soc": False,
        "start_soc_percent": 80,
        "enable_start_battery_charge": False,
        "start_battery_charge_watt": 2000,
        "enable_pause_soc": False,
        "pause_soc_percent": 30,
        "enable_pause_battery_discharge": False,
        "pause_battery_discharge_watt": 300,
        "enable_pause_grid_import": False,
        "pause_grid_import_watt": 300,
        "start_stable_minutes": 5,
        "stop_stable_minutes": 0,
    },
    "summer": {
        "day_pv_threshold_watt": 4000,
        "night_pv_threshold_watt": 2000,
        "high_hashrate_th": 110,
        "low_power_watt": 945,
        "switch_stable_minutes": 5,
    },
    "mode": {
        # Legacy configs may still contain older profile names.
        "active": "auto",
    },
    "modes": {
        # See RUN_MODES.
        "manual_override": "auto",
        "pending_override": None,
        "pending_apply_at": None,
    },
    "logging": {
        "level":        "INFO",
        "file":         LOG_FILE,
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
*{box-sizing:border-box;margin:0;padding:0}body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:linear-gradient(180deg,#09111f 0%,#0d1324 55%,#0a0f1c 100%);color:var(--text);min-height:100vh}button,input{font:inherit}header{height:64px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:rgba(12,18,32,.84);backdrop-filter:blur(12px);position:sticky;top:0;z-index:2}h1{font-size:1.05rem;font-weight:760;letter-spacing:.01em}.head-left{display:flex;align-items:center;gap:16px}.tabs{display:flex;gap:6px}.tabs button{border:1px solid var(--line);background:#141d2e;color:var(--muted);border-radius:7px;padding:7px 10px;cursor:pointer;font-size:.82rem;font-weight:760}.tabs button.active{background:var(--blue);border-color:var(--blue);color:#07111f}.view{display:none}.view.active{display:block}.badge{padding:7px 12px;border-radius:999px;font-size:.78rem;font-weight:800;border:1px solid var(--line)}.badge.mining{color:#08150f;background:var(--green);border-color:var(--green)}.badge.paused{color:#22080b;background:var(--red);border-color:var(--red)}.badge.unknown{color:var(--muted);background:#151c2b}.badge.off{color:#07111f;background:var(--amber);border-color:var(--amber)}main{max-width:1180px;margin:0 auto;padding:22px}.hero{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(300px,.65fr);gap:16px;margin-bottom:18px}.decision{background:linear-gradient(135deg,#15243a,#101827);border:1px solid var(--line);border-radius:10px;padding:20px;min-height:190px;display:flex;flex-direction:column;justify-content:space-between}.decision .eyebrow{font-size:.76rem;color:var(--muted);text-transform:uppercase;font-weight:750;letter-spacing:.07em}.decision h2{font-size:1.55rem;line-height:1.15;margin:8px 0 10px}.decision p{color:#c8d5e8;font-size:.95rem;line-height:1.45}.threshold{background:#101827;border:1px solid var(--line);border-radius:10px;padding:16px}.threshold .big{font-size:2rem;font-weight:850;font-variant-numeric:tabular-nums}.threshold .sub{color:var(--muted);font-size:.8rem;margin-top:4px}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:18px}.card{background:rgba(17,24,39,.95);border:1px solid var(--line);border-radius:8px;padding:14px;min-height:88px}.card .lbl{color:var(--muted);font-size:.76rem;font-weight:680;margin-bottom:7px}.card .val{font-size:1.35rem;font-weight:820;font-variant-numeric:tabular-nums}.card.good .val{color:var(--green)}.card.warn .val{color:var(--amber)}.card.bad .val{color:var(--red)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}section{background:rgba(17,24,39,.92);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}section h3{font-size:.82rem;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:14px}.ov-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.ov-row button,.btn-save{border:1px solid var(--line);border-radius:7px;background:#182236;color:var(--text);padding:9px 14px;cursor:pointer;font-size:.9rem;font-weight:700}.ov-row button:hover,.btn-save:hover{background:#202d45}.ov-row button.active{background:var(--blue);border-color:var(--blue);color:#07111f}.btn-save{background:#1f8f55;border-color:#2ac06e}.fg{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.field{display:flex;flex-direction:column;gap:6px}.field label{font-size:.8rem;font-weight:720;color:#c9d6e8}.field input:not([type=checkbox]){background:#0c1322;border:1px solid var(--line);border-radius:7px;color:var(--text);padding:9px 10px}.field input[type=checkbox]{width:1rem;height:1rem;margin-right:7px;vertical-align:-2px}.field input:focus{outline:none;border-color:var(--blue)}.hint{font-size:.76rem;color:var(--muted);line-height:1.4}.hint em{font-style:normal;color:#e8f1ff;font-weight:800}.ok{color:var(--green);font-size:.85rem}.err{color:var(--red);font-size:.85rem}.ts{color:var(--muted);font-size:.76rem;margin-left:10px}.fixed-targets{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:12px}.fixed-targets .field{display:none}.fixed-targets .field.active{display:flex}.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}.flow div{background:#0d1526;border:1px solid var(--line);border-radius:8px;padding:10px}.flow span{display:block;color:var(--muted);font-size:.72rem;margin-bottom:4px}.flow b{font-size:1rem;font-variant-numeric:tabular-nums}@media(max-width:850px){.hero,.grid{grid-template-columns:1fr}.cards{grid-template-columns:repeat(2,1fr)}.fg,.fixed-targets{grid-template-columns:1fr}}@media(max-width:520px){main{padding:12px}header{padding:0 14px}.head-left{gap:10px}.tabs button{padding:6px 8px}.cards{grid-template-columns:1fr}.flow{grid-template-columns:1fr 1fr}.decision h2{font-size:1.25rem}}
.field input.invalid{border-color:var(--red);box-shadow:0 0 0 1px var(--red)}
.field.rule{border:1px solid var(--line);border-radius:9px;padding:13px;background:#0d1524;transition:border-color .2s,background .2s}.field.rule.enabled{border-color:#2b7c59;background:#10241d}.field.rule>label{display:flex;align-items:center}.field.rule input:disabled{opacity:.48;cursor:not-allowed}.notice{border-left:3px solid var(--blue);background:#0d1829;border-radius:6px;padding:11px 13px;color:#b9c9dd;font-size:.8rem;line-height:1.45;margin-bottom:14px}.btn-save:disabled,.ov-row button:disabled{opacity:.55;cursor:wait}.ts.err{color:var(--red)}
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
        <div><span id="l-batt-reserve">Akku-Ladeziel</span><b id="v-batt-reserve">—</b></div>
        <div><span id="l-miner-need">Miner benötigt</span><b id="v-miner-need">—</b></div>
        <div><span>Profilwechsel</span><b id="v-buffer">—</b></div>
      </div>
    </div>
    <div class="threshold">
      <div class="hint" id="l-required">Aktives Ziel</div>
      <div class="big" id="v-required">—</div>
      <div class="sub" id="v-required-sub">Die Automatik wählt das Ziel anhand des verfügbaren PV-Überschusses.</div>
    </div>
  </div>

  <div class="cards">
    <div class="card" id="c-soc"><div class="lbl">Akku</div><div class="val" id="v-soc">—</div></div>
    <div class="card"><div class="lbl">PV Produktion</div><div class="val" id="v-ppv">—</div></div>
    <div class="card" id="c-grid"><div class="lbl" id="l-pgrid">Netz</div><div class="val" id="v-pgrid">—</div></div>
    <div class="card" id="c-batt"><div class="lbl" id="l-pakku">Batterie</div><div class="val" id="v-pakku">—</div></div>
    <div class="card"><div class="lbl">Haus gesamt</div><div class="val" id="v-pload">—</div></div>
    <div class="card"><div class="lbl">Miner aktuell</div><div class="val" id="v-power">—</div></div>
    <div class="card"><div class="lbl" id="l-verfuegbar">Verfügbar</div><div class="val" id="v-verfuegbar">—</div></div>
    <div class="card"><div class="lbl">Abfrage-Intervall</div><div class="val" id="v-next">—</div></div>
  </div>

  <div class="grid">
    <section>
      <h3>Aktiver Laufmodus</h3>
      <div class="ov-row">
        <button id="run-auto" onclick="setRunMode('auto')">Auto</button>
        <button id="run-pause" onclick="setRunMode('pause')">Pause</button>
        <button id="run-fixed_hashrate" onclick="setRunMode('fixed_hashrate')">Fix Hashrate</button>
        <button id="run-fixed_power" onclick="setRunMode('fixed_power')">Fix Watt</button>
        <button id="run-off" onclick="setRunMode('off')">Off</button>
      </div>
      <div class="hint" style="margin-top:10px">Auto wechselt zwischen High- und Eco-Ziel und beachtet die aktivierten Akku-/Netzregeln. Pause stoppt. Fix Hashrate nutzt das High-Ziel, Fix Watt das Eco-Ziel. Off deaktiviert alle Braiins-API-Zugriffe und lässt den Miner unangetastet.</div>
      <div class="hint" style="margin-top:10px"><b id="auto-preview">Auto würde: —</b></div>
      <div id="auto-preview-reason" class="hint" style="margin-top:4px"></div>
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

  <section id="settings-auto">
    <h3>Automatik</h3>
    <div class="fg">
      <div class="field"><label>High ab PV-Überschuss (W)</label><input id="f-daypv" type="number" min="0" max="30000" step="100" required oninput="updateConfigHints()"><div class="hint" id="h-daypv">Überschuss nach Hausverbrauch; ab diesem Wert wird auf das High-Ziel gewechselt.</div></div>
      <div class="field"><label>Eco unter PV-Überschuss (W)</label><input id="f-nightpv" type="number" min="0" max="30000" step="100" required oninput="updateConfigHints()"><div class="hint" id="h-nightpv">Unter diesem Überschuss wird auf das Eco-Ziel gewechselt.</div></div>
      <div class="field"><label>High Hashrate-Ziel (TH/s)</label><input id="f-highth" type="number" min="1" max="200" step="0.1" required oninput="updateConfigHints()"><div class="hint" id="h-highth">Leistungsziel bei großem PV-Überschuss.</div></div>
      <div class="field"><label>Eco Power-Ziel (W)</label><input id="f-loww" type="number" min="945" max="7000" step="1" required oninput="updateConfigHints()"><div class="hint" id="h-loww">Sparsames Ziel bei wenig oder keiner PV-Leistung.</div></div>
      <div class="field"><label>Wechsel erst nach stabil (Minuten)</label><input id="f-switchmin" type="number" min="1" max="120" step="1" required oninput="updateConfigHints()"><div class="hint" id="h-switchmin">PV muss so lange stabil über/unter der Schwelle bleiben.</div></div>
      <div class="field"><label>Abfrage-Intervall (Sekunden)</label><input id="f-pi" type="number" min="10" max="300" step="1" required><div class="hint">Wie oft Fronius und Miner abgefragt werden.</div></div>
    </div>
  </section>

  <section>
    <h3>Optionale Akku- und Netzregeln</h3>
    <div class="notice">Aktivierte Schutzregeln verhindern einen Start und können laufendes Mining pausieren. Bei der Pause-Verzögerung bedeutet <b>0 Minuten: sofort</b>; ein höherer Wert toleriert kurze Lastspitzen.</div>
    <div class="fg">
      <div class="field rule"><label><input id="f-en-start-soc" type="checkbox" onchange="updateConfigHints()"> Start erst ab Akku-SOC</label><input id="f-start-soc" type="number" min="0" max="100" step="0.1" required oninput="updateConfigHints()"><div class="hint" id="h-start-soc">Wenn aktiv, startet Auto erst ab diesem Akku-Stand.</div></div>
      <div class="field rule"><label><input id="f-en-start-charge" type="checkbox" onchange="updateConfigHints()"> Start erst bei Akku-Ladung</label><input id="f-start-charge" type="number" min="0" max="30000" step="100" required oninput="updateConfigHints()"><div class="hint" id="h-start-charge">Wenn aktiv, startet Auto erst, wenn der Akku mindestens so stark lädt.</div></div>
      <div class="field rule"><label><input id="f-en-pause-soc" type="checkbox" onchange="updateConfigHints()"> Akku-Reserve schützen unter SOC</label><input id="f-pause-soc" type="number" min="0" max="100" step="0.1" required oninput="updateConfigHints()"><div class="hint" id="h-pause-soc">Unter der Reserve darf nur mit gedecktem Eco-Ziel gemined werden.</div></div>
      <div class="field rule"><label><input id="f-en-pause-discharge" type="checkbox" onchange="updateConfigHints()"> Pause bei Akku-Entladung</label><input id="f-pause-discharge" type="number" min="0" max="10000" step="50" required oninput="updateConfigHints()"><div class="hint" id="h-pause-discharge">Wenn aktiv, pausiert Auto bei stärkerer Akku-Entladung.</div></div>
      <div class="field rule"><label><input id="f-en-pause-grid" type="checkbox" onchange="updateConfigHints()"> Pause bei Netzbezug</label><input id="f-pause-grid" type="number" min="0" max="10000" step="50" required oninput="updateConfigHints()"><div class="hint" id="h-pause-grid">Wenn aktiv, pausiert Auto bei zu hohem Netzbezug.</div></div>
      <div class="field"><label>Start erst nach stabiler Lage (Minuten)</label><input id="f-startmin" type="number" min="1" max="60" step="1" required><div class="hint">Gilt nur, wenn Auto gerade nicht läuft und die Start-Regeln erfüllt werden.</div></div>
      <div class="field"><label>Pause-Verzögerung für Watt-Regeln (Minuten)</label><input id="f-stopmin" type="number" min="0" max="60" step="1" required oninput="updateConfigHints()"><div class="hint" id="h-stopmin">0 = sofort; gilt für Akku-Entladung und Netzbezug.</div></div>
    </div>
    <div class="ov-row" style="margin-top:16px"><button id="btn-save" class="btn-save" onclick="saveCfg()">Speichern</button><span id="smsg" aria-live="polite"></span></div>
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
function n(id,def){
  const raw=el(id)?.value?.trim();
  if(raw==null||raw==='') return def;
  const x=Number(raw);
  return Number.isFinite(x)?x:def;
}
const numericSettings=[
  {id:'f-daypv',key:'dayPv',label:'High ab PV-Überschuss',min:0,max:30000,integer:true},
  {id:'f-nightpv',key:'nightPv',label:'Eco unter PV-Überschuss',min:0,max:30000,integer:true},
  {id:'f-highth',key:'highTh',label:'High Hashrate-Ziel',min:1,max:200},
  {id:'f-loww',key:'lowW',label:'Eco Power-Ziel',min:945,max:7000,integer:true,clampMin:true},
  {id:'f-switchmin',key:'switchMin',label:'Wechselzeit',min:1,max:120,integer:true},
  {id:'f-pi',key:'pollInterval',label:'Abfrage-Intervall',min:10,max:300,integer:true},
  {id:'f-start-soc',key:'startSoc',label:'Start-Akku-SOC',min:0,max:100},
  {id:'f-start-charge',key:'startCharge',label:'Start-Akkuladung',min:0,max:30000,integer:true},
  {id:'f-pause-soc',key:'pauseSoc',label:'Akku-Reserve',min:0,max:100},
  {id:'f-pause-discharge',key:'pauseDischarge',label:'Akku-Entladung',min:0,max:10000,integer:true},
  {id:'f-pause-grid',key:'pauseGrid',label:'Netzbezug',min:0,max:10000,integer:true},
  {id:'f-startmin',key:'startMin',label:'Start-Wartezeit',min:1,max:60,integer:true},
  {id:'f-stopmin',key:'stopMin',label:'Pause-Verzögerung',min:0,max:60,integer:true},
];
const ruleInputs=[
  ['f-en-start-soc','f-start-soc'],['f-en-start-charge','f-start-charge'],
  ['f-en-pause-soc','f-pause-soc'],['f-en-pause-discharge','f-pause-discharge'],
  ['f-en-pause-grid','f-pause-grid'],
];
function clearNumericError(input){
  input.classList.remove('invalid');
  input.removeAttribute('aria-invalid');
}
function invalidNumeric(input,message){
  input.classList.add('invalid');
  input.setAttribute('aria-invalid','true');
  return {input,message};
}
function validatedNumericSettings(){
  const values={};
  for(const spec of numericSettings){
    const input=el(spec.id);
    clearNumericError(input);
    const raw=input.value.trim();
    if(raw==='') return invalidNumeric(input,`${spec.label}: Bitte eine Zahl eingeben.`);
    let value=Number(raw);
    if(!Number.isFinite(value)) return invalidNumeric(input,`${spec.label}: Bitte eine gültige Zahl eingeben.`);
    if(spec.clampMin&&value<spec.min){
      value=spec.min;
      input.value=String(value);
    }
    if(value<spec.min||value>spec.max){
      return invalidNumeric(input,`${spec.label}: Erlaubt sind ${spec.min} bis ${spec.max}.`);
    }
    if(spec.integer&&!Number.isInteger(value)){
      return invalidNumeric(input,`${spec.label}: Bitte eine ganze Zahl eingeben.`);
    }
    values[spec.key]=value;
  }
  if(values.nightPv>=values.dayPv){
    return invalidNumeric(el('f-nightpv'),'Eco unter PV-Überschuss muss kleiner als High sein.');
  }
  if(el('f-en-start-soc').checked&&el('f-en-pause-soc').checked&&values.startSoc<=values.pauseSoc){
    return invalidNumeric(el('f-start-soc'),'Start-Akku-SOC muss höher als die Akku-Reserve sein.');
  }
  return {values};
}
function fw(v){return v==null?'—':Math.round(v)+' W'}
function absw(v){return v==null?'—':Math.round(Math.abs(v))+' W'}
function kw(v){return v==null?'—':(v/1000).toFixed(1)+' kW'}
function th(v){return v==null?'—':Number(v).toFixed(1)+' TH/s'}
function targetValue(d, desired){
  if(d.summer_target_kind==='power') return fw(desired?d.desired_power_target_w:d.power_target_w);
  return th(desired?d.desired_hashrate_target_th:d.hashrate_target_th);
}
function cls(card,kind){card.className='card '+(kind||'')}
let activeMode='auto';
let decisionTimer=null;
let modeSwitchTimer=null;
function runKey(active,override){
  return (override&&override!=='auto')?override:'auto';
}
function setRunUi(mode){
  const current=mode||'auto';
  ['auto','pause','fixed_hashrate','fixed_power','off'].forEach(m=>el('run-'+m)?.classList.toggle('active',m===current));
}
async function sendRunMode(mode){
  const body={mode};
  if(mode==='auto'){body.active_mode='auto';body.override='auto';}
  else{body.override=mode;}
  try{
    const r=await fetch('/api/run-mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok){
      const e=await r.json().catch(()=>({error:'Befehl nicht angenommen'}));
      el('cmdmsg').className='hint warn';
      el('cmdmsg').textContent=e.error||'Befehl nicht angenommen';
    }else{
      const d=await r.json();
      setRunUi(d.pending_override||d.manual_override||mode);
    }
  }catch(e){
    el('cmdmsg').className='hint warn';
    el('cmdmsg').textContent='Netzwerkfehler';
  }finally{ fetchStatus(); }
}
function fmtTimer(seconds){
  const s=Math.max(0,Math.ceil(seconds));
  return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
}
function setDecisionReason(d){
  const reason=d.decision_reason||'';
  const timers=[
    {value:d.summer_switch_remaining_s, re:/Wechsel in \\d+:\\d{2}/, text:s=>`Wechsel in ${fmtTimer(s)}`},
    {value:d.start_wait_remaining_s, re:/Miner startet in \\d+:\\d{2}/, text:s=>`Miner startet in ${fmtTimer(s)}`},
    {value:d.stop_wait_remaining_s, re:/Auto pausiert erst in \\d+:\\d{2}/, text:s=>`Auto pausiert erst in ${fmtTimer(s)}`},
  ];
  decisionTimer=null;
  for(const t of timers){
    if(Number.isFinite(+t.value) && t.re.test(reason)){
      decisionTimer={base:reason, started:Date.now(), remaining:+t.value, re:t.re, text:t.text};
      break;
    }
  }
  if(!decisionTimer){
    el('decision-reason').textContent=reason;
    return;
  }
  renderDecisionReason();
}
function renderDecisionReason(){
  if(!decisionTimer){return;}
  const left=Math.max(0,decisionTimer.remaining-((Date.now()-decisionTimer.started)/1000));
  el('decision-reason').textContent=decisionTimer.base.replace(decisionTimer.re,decisionTimer.text(left));
}
function modeLabel(mode){
  return {auto:'Auto',pause:'Pause',fixed_hashrate:'Fix Hashrate',fixed_power:'Fix Watt',off:'Off'}[mode]||mode;
}
function setModeSwitchStatus(d){
  if(d.pending_override && Number.isFinite(+d.mode_switch_remaining_s)){
    modeSwitchTimer={mode:d.pending_override,started:Date.now(),remaining:+d.mode_switch_remaining_s,refreshRequested:false};
    renderModeSwitchStatus();
    return true;
  }
  modeSwitchTimer=null;
  return false;
}
function renderModeSwitchStatus(){
  if(!modeSwitchTimer) return;
  const left=Math.max(0,modeSwitchTimer.remaining-((Date.now()-modeSwitchTimer.started)/1000));
  el('cmdmsg').className='hint';
  el('cmdmsg').textContent=left>0
    ? `Wechsel zu ${modeLabel(modeSwitchTimer.mode)} in ${fmtTimer(left)}.`
    : `${modeLabel(modeSwitchTimer.mode)} wird angewendet...`;
  if(left<=0 && !modeSwitchTimer.refreshRequested){
    modeSwitchTimer.refreshRequested=true;
    setTimeout(fetchStatus,250);
  }
}
function updateConfigHints(){
  const ssoc=n('f-start-soc',80), scharge=n('f-start-charge',2000), psoc=n('f-pause-soc',30), pdis=n('f-pause-discharge',300), pgrid=n('f-pause-grid',300);
  const day=n('f-daypv',4000), night=n('f-nightpv',2000), hi=n('f-highth',110), loww=Math.max(945,n('f-loww',945)), sw=n('f-switchmin',5), pauseMin=n('f-stopmin',0);
  for(const [toggleId,inputId] of ruleInputs){
    const enabled=el(toggleId).checked;
    el(inputId).disabled=!enabled;
    el(inputId).closest('.rule')?.classList.toggle('enabled',enabled);
  }
  el('h-start-soc').innerHTML=`${el('f-en-start-soc').checked?'Aktiv':'Aus'}: Start erst ab <em>${ssoc}%</em> Akku.`;
  el('h-start-charge').innerHTML=`${el('f-en-start-charge').checked?'Aktiv':'Aus'}: Start erst, wenn der Akku mindestens <em>${scharge} W</em> lädt.`;
  el('h-pause-soc').innerHTML=`${el('f-en-pause-soc').checked?'Aktiv':'Aus'}: Unter <em>${psoc}%</em> Akku nur minen, wenn PV das Eco-Ziel deckt.`;
  const pauseText=pauseMin===0?'sofort':`nach <em>${pauseMin} Minuten</em>`;
  el('h-pause-discharge').innerHTML=`${el('f-en-pause-discharge').checked?'Aktiv':'Aus'}: Pause ${pauseText} bei Akku-Entladung ab <em>${pdis} W</em>.`;
  el('h-pause-grid').innerHTML=`${el('f-en-pause-grid').checked?'Aktiv':'Aus'}: Pause ${pauseText} bei Netzbezug ab <em>${pgrid} W</em>.`;
  el('h-daypv').innerHTML=`Ab <em>${(day/1000).toFixed(1)} kW</em> Überschuss nach Hauslast wird auf High gewechselt.`;
  el('h-nightpv').innerHTML=`Unter <em>${(night/1000).toFixed(1)} kW</em> Überschuss nach Hauslast wird auf Eco gewechselt.`;
  el('h-highth').innerHTML=`High-Ziel: <em>${hi.toFixed(1)} TH/s</em>.`;
  el('h-loww').innerHTML=`Eco-Ziel: <em>${loww} W</em>. Unter 945 W wird automatisch auf 945 W gesetzt.`;
  el('h-switchmin').innerHTML=`Wechsel erst nach <em>${sw} Minuten</em> stabiler PV.`;
  el('h-stopmin').innerHTML=pauseMin===0?'Akku-Entladung und Netzbezug pausieren <em>sofort</em>.':`Akku-Entladung und Netzbezug müssen <em>${pauseMin} Minuten</em> anhalten.`;
}
async function fetchStatus(){
  try{
    const d=await(await fetch('/api/status',{cache:'no-store'})).json();
    el('v-soc').textContent=d.soc!=null?d.soc.toFixed(1)+'%':'—';
    el('v-ppv').textContent=fw(d.p_pv); el('v-pload').textContent=absw(d.p_load); el('v-power').textContent=fw(d.miner_power_w);
    el('l-pgrid').textContent=d.p_grid==null?'Netz':(d.p_grid<0?'Netz Einspeisung':(d.p_grid>0?'Netz Bezug':'Netz neutral')); el('v-pgrid').textContent=absw(d.p_grid);
    el('l-pakku').textContent=d.p_akku==null?'Batterie':(d.p_akku>0?'Batterie entlädt':(d.p_akku<0?'Batterie lädt':'Batterie neutral')); el('v-pakku').textContent=absw(d.p_akku);
    const fixed=d.manual_override==='fixed_hashrate'||d.manual_override==='fixed_power';
    const off=d.manual_override==='off';
    el('v-house').textContent=fw(d.house_without_miner_w); el('v-batt-reserve').textContent=off?'Aus':(fixed?'Fix':(d.summer_profile==='day'?'High':'Eco')); el('v-miner-need').textContent=targetValue(d,false); el('v-buffer').textContent=d.summer_switch_remaining_s!=null?fmtTimer(d.summer_switch_remaining_s):'—';
    el('l-batt-reserve').textContent=off?'Braiins API':(fixed?'Override':'Leistungsprofil');
    el('l-miner-need').textContent=off?'Miner-Status':(d.summer_target_kind==='power'?'Power Target aktuell':'Hashrate Target aktuell');
    el('l-required').textContent=off?'Miner-Steuerung':(fixed?'Fix Ziel':(d.summer_target_kind==='power'?'Power Target':'Hashrate Target'));
    el('v-required').textContent=off?'Off':targetValue(d,true);
    el('v-required-sub').textContent=off?'Keine Lese- oder Schreibzugriffe auf Braiins OS.':(fixed?'Fix-Modus übersteuert die Automatik bis du wieder Auto aktivierst.':'Auto nutzt PV-Überschuss nach Hauslast; unter Akku-Reserve nur mit gedecktem Ziel.');
    el('l-verfuegbar').textContent='PV für Miner'; el('v-verfuegbar').textContent=fw(d.available_w); el('v-next').textContent=(d.poll_interval_seconds||30)+' s';
    const st=d.display_state||'unknown'; const b=el('badge'); b.className='badge '+st; b.textContent=st==='mining'?'Mining':(st==='paused'?'Pausiert':(st==='off'?'Off':'—'));
    el('decision-title').textContent=d.decision_title||'Warte auf Daten'; setDecisionReason(d);
    el('auto-preview').textContent=d.auto_preview_title?`Auto würde: ${d.auto_preview_title}`:'Auto würde: —';
    el('auto-preview-reason').textContent=d.auto_preview_reason||'';
    cls(el('c-soc'),d.soc==null?'':(d.soc>=80?'good':'warn')); cls(el('c-grid'),d.p_grid==null?'':(d.p_grid>50?'bad':(d.p_grid<-50?'good':''))); cls(el('c-batt'),d.p_akku==null?'':(d.p_akku>100?'bad':(d.p_akku<0?'good':'')));
    activeMode='auto';
    setRunUi(d.pending_override||runKey(d.active_mode,d.manual_override));
    if(!setModeSwitchStatus(d)){
      if(d.command_state==='ok'){el('cmdmsg').className='hint';el('cmdmsg').textContent=d.command_msg||'Befehl bestätigt';}
      else if(d.command_state){el('cmdmsg').className='hint warn';el('cmdmsg').textContent=d.command_msg||'Befehl nicht bestätigt';}
      else el('cmdmsg').textContent='';
    }
    el('ts').className='ts';
    el('ts').textContent='aktualisiert '+new Date().toLocaleTimeString('de-AT');
  }catch(e){
    el('ts').className='ts err';
    el('ts').textContent='Verbindung unterbrochen';
  }
}
async function fetchCfg(){
  try{const d=await(await fetch('/api/config',{cache:'no-store'})).json();
    activeMode='auto';
    el('f-fh').value=d.fronius?.host||''; el('f-fh2').value=d.fronius?.pv2_host||''; el('f-pi').value=d.fronius?.poll_interval_seconds??30;
    el('f-mh').value=d.miner?.host||''; el('f-ak').value=d.miner?.api_key||'';
    el('f-en-start-soc').checked=!!d.control?.enable_start_soc; el('f-start-soc').value=d.control?.start_soc_percent??80;
    el('f-en-start-charge').checked=!!d.control?.enable_start_battery_charge; el('f-start-charge').value=d.control?.start_battery_charge_watt??2000;
    el('f-en-pause-soc').checked=!!d.control?.enable_pause_soc; el('f-pause-soc').value=d.control?.pause_soc_percent??30;
    el('f-en-pause-discharge').checked=!!d.control?.enable_pause_battery_discharge; el('f-pause-discharge').value=d.control?.pause_battery_discharge_watt??300;
    el('f-en-pause-grid').checked=!!d.control?.enable_pause_grid_import; el('f-pause-grid').value=d.control?.pause_grid_import_watt??300;
    el('f-startmin').value=d.control?.start_stable_minutes??5; el('f-stopmin').value=d.control?.stop_stable_minutes??0;
    el('f-daypv').value=d.summer?.day_pv_threshold_watt??4000; el('f-nightpv').value=d.summer?.night_pv_threshold_watt??2000; el('f-highth').value=d.summer?.high_hashrate_th??110; el('f-loww').value=Math.max(945,d.summer?.low_power_watt??945); el('f-switchmin').value=d.summer?.switch_stable_minutes??5;
    updateConfigHints();
  }catch(e){
    el('smsg').className='err';
    el('smsg').textContent='Einstellungen konnten nicht geladen werden.';
  }
}
async function saveCfg(){
  const msg=el('smsg'), btn=el('btn-save');
  const checked=validatedNumericSettings();
  if(!checked.values){
    msg.className='err';
    msg.textContent=checked.message;
    checked.input.focus();
    return;
  }
  btn.disabled=true;
  btn.textContent='Speichere...';
  const v=checked.values;
  const cfg={mode:{active:'auto'},fronius:{host:el('f-fh').value.trim(),pv2_host:el('f-fh2').value.trim(),poll_interval_seconds:v.pollInterval},miner:{host:el('f-mh').value.trim(),api_key:el('f-ak').value.trim()},control:{enable_start_soc:el('f-en-start-soc').checked,start_soc_percent:v.startSoc,enable_start_battery_charge:el('f-en-start-charge').checked,start_battery_charge_watt:v.startCharge,enable_pause_soc:el('f-en-pause-soc').checked,pause_soc_percent:v.pauseSoc,enable_pause_battery_discharge:el('f-en-pause-discharge').checked,pause_battery_discharge_watt:v.pauseDischarge,enable_pause_grid_import:el('f-en-pause-grid').checked,pause_grid_import_watt:v.pauseGrid,start_stable_minutes:v.startMin,stop_stable_minutes:v.stopMin},summer:{day_pv_threshold_watt:v.dayPv,night_pv_threshold_watt:v.nightPv,high_hashrate_th:v.highTh,low_power_watt:v.lowW,switch_stable_minutes:v.switchMin}};
  try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)}); if(r.ok){msg.className='ok';msg.textContent='Gespeichert und wird jetzt angewendet.';fetchStatus();}else{const e=await r.json();msg.className='err';msg.textContent=e.error||'Fehler';}}
  catch(e){msg.className='err';msg.textContent='Netzwerkfehler';}
  finally{
    btn.disabled=false;
    btn.textContent='Speichern';
  }
  setTimeout(()=>{el('smsg').textContent='';},5000);
}
async function setRunMode(mode){
  setRunUi(mode);
  el('cmdmsg').textContent='';
  await sendRunMode(mode);
}
async function doUpdate(){
  const btn=el('btn-update'), msg=el('umsg'); btn.disabled=true; msg.className=''; msg.textContent='Prüfe Update...';
  try{const r=await fetch('/api/update',{method:'POST'}); const result=await r.json(); if(!r.ok){msg.className='err';msg.textContent=result.error||'Update fehlgeschlagen';btn.disabled=false;return;} if(result.updated===false){msg.className='ok';msg.textContent='Bereits aktuell.';btn.disabled=false;return;}}
  catch(e){msg.className='err';msg.textContent='Netzwerkfehler';btn.disabled=false;return;}
  msg.textContent='Update installiert, Service startet neu...'; let stable=0,tries=0,started=Date.now();
  const poll=setInterval(async()=>{tries++;try{const r=await fetch('/api/status?u='+Date.now(),{cache:'no-store'});if(!r.ok)throw new Error();await r.json();stable++;if(Date.now()-started<6000||stable<2)return;clearInterval(poll);msg.className='ok';msg.textContent='Update erfolgreich.';setTimeout(()=>location.reload(),1200);}catch(e){stable=0;if(tries>=60){clearInterval(poll);msg.className='err';msg.textContent='Service antwortet nicht. Logs prüfen.';btn.disabled=false;}}},1000);
}
document.addEventListener('input',event=>{
  if(event.target.matches('input[type=number]')) clearNumericError(event.target);
});
fetchStatus();fetchCfg();setInterval(fetchStatus,10000);setInterval(()=>{renderDecisionReason();renderModeSwitchStatus();},1000);
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
            normalize_config_patch(cfg)
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
            normalize_config_patch(cfg)
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

    def queue_run_mode(self, override: str, delay_s: int = 10) -> tuple[str, float | None]:
        with self._lock:
            modes = self._cfg.setdefault("modes", {})
            current = modes.get("manual_override", "auto")
            if override == current:
                modes["pending_override"] = None
                modes["pending_apply_at"] = None
                self._write(self._cfg)
                return current, None
            apply_at = time.time() + max(0, delay_s)
            modes["pending_override"] = override
            modes["pending_apply_at"] = apply_at
            self._write(self._cfg)
            return current, apply_at

    def apply_pending_run_mode(self) -> bool:
        with self._lock:
            modes = self._cfg.setdefault("modes", {})
            pending = modes.get("pending_override")
            apply_at = modes.get("pending_apply_at")
            if pending not in RUN_MODES or apply_at is None:
                return False
            try:
                due = float(apply_at)
            except (TypeError, ValueError):
                modes["pending_override"] = None
                modes["pending_apply_at"] = None
                self._write(self._cfg)
                return False
            if time.time() < due:
                return False
            previous = modes.get("manual_override", "auto")
            modes["manual_override"] = pending
            modes["resume_auto_now"] = previous == "pause" and pending == "auto"
            modes["sync_summer_profile_now"] = previous != "auto" and pending == "auto"
            modes["pending_override"] = None
            modes["pending_apply_at"] = None
            self._write(self._cfg)
            return True


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
                soc = float(inv["SOC"])
            except (TypeError, ValueError):
                continue
            if math.isfinite(soc) and 0 <= soc <= 100:
                return soc
        return None

    @staticmethod
    def _site_number(site: dict, key: str) -> float:
        raw = site.get(key)
        if raw is None:
            raise ValueError(f"Fronius response has no {key}")
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"Fronius response has invalid {key}")
        return value

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
                raise ValueError("Fronius response has no valid battery SOC")
            p_grid = self._site_number(site, "P_Grid")
            p_akku = self._site_number(site, "P_Akku")
            p_pv   = self._site_number(site, "P_PV")
            p_load = self._site_number(site, "P_Load")

            # Second inverter (e.g. a Symo that is NOT linked to the hybrid):
            # its production is invisible to the hybrid's local API, so query
            # it separately, add its PV and recompute the house load from the
            # whole-house balance  P_Load = -(P_Grid + P_Akku + P_PV).
            pv2_host = (cfg.get("pv2_host") or "").strip()
            if pv2_host and pv2_host != host.strip():
                try:
                    site2 = self._fetch(pv2_host)["Site"]
                    p_pv += self._site_number(site2, "P_PV")
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
    pv-miner uses pause / resume plus hashrate and power targets. It never
    changes fan settings.

    Endpoints used:
      PUT  /api/v1/actions/pause    — pause mining (miner status → 3)
      PUT  /api/v1/actions/resume   — resume mining (miner status → 2)
      PUT  /api/v1/performance/hashrate-target
      PUT  /api/v1/performance/power-target
      GET  /api/v1/performance/mode
      GET  /api/v1/performance/tuner-state
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

    @staticmethod
    def _find_terahash(value) -> float | None:
        if isinstance(value, dict):
            if "terahash_per_second" in value:
                try:
                    return float(value["terahash_per_second"])
                except (TypeError, ValueError):
                    return None
            for nested in value.values():
                found = BraiinsAPI._find_terahash(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = BraiinsAPI._find_terahash(nested)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _find_power_target_watt(value) -> int | None:
        if not isinstance(value, dict):
            if isinstance(value, list):
                for nested in value:
                    found = BraiinsAPI._find_power_target_watt(nested)
                    if found is not None:
                        return found
            return None

        if "watt" in value:
            try:
                return int(value["watt"])
            except (TypeError, ValueError):
                return None
        for key in ("current_target", "target", "power_target"):
            nested = value.get(key)
            if isinstance(nested, dict) and "watt" in nested:
                try:
                    return int(nested["watt"])
                except (TypeError, ValueError):
                    return None
        for nested in value.values():
            found = BraiinsAPI._find_power_target_watt(nested)
            if found is not None:
                return found
        return None

    @staticmethod
    def _find_target_kind(value) -> str | None:
        if isinstance(value, dict):
            for key, nested in value.items():
                key_l = str(key).lower()
                if key_l in ("powertarget", "powertargetmodestate"):
                    return "power"
                if key_l in ("hashratetarget", "hashratetargetmodestate"):
                    return "hashrate"
                found = BraiinsAPI._find_target_kind(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = BraiinsAPI._find_target_kind(nested)
                if found is not None:
                    return found
        return None

    @classmethod
    def _target_state_from_json(cls, value) -> dict:
        return {
            "target_kind": cls._find_target_kind(value),
            "hashrate_target_th": cls._find_terahash(value),
            "power_target_w": cls._find_power_target_watt(value),
        }

    @staticmethod
    def _target_payload(kind: str, target: float | int) -> dict:
        if kind == "power":
            return {
                "tunermode": {
                    "target": {
                        "powertarget": {
                            "power_target": {"watt": int(target)}
                        }
                    }
                }
            }
        return {
            "tunermode": {
                "target": {
                    "hashratetarget": {
                        "hashrate_target": {"terahash_per_second": float(target)}
                    }
                }
            }
        }

    @staticmethod
    def _target_matches(state: dict | None, kind: str, target: float | int) -> bool:
        if not state or state.get("target_kind") != kind:
            return False
        if kind == "power":
            current = state.get("power_target_w")
            return current is not None and abs(int(current) - int(target)) < 10
        current = state.get("hashrate_target_th")
        return current is not None and abs(float(current) - float(target)) < 0.1

    def get_hashrate_target(self) -> float | None:
        for path in ("/performance/mode", "/performance/tuner-state"):
            r = self._request("GET", path)
            if r is None:
                continue
            try:
                found = self._find_terahash(r.json())
                if found is not None:
                    return found
            except Exception as exc:
                self._log.warning("Braiins %s parse: %s", path, exc)
        return None

    def get_performance_target_state(self) -> dict:
        for path in ("/performance/mode", "/performance/tuner-state"):
            r = self._request("GET", path)
            if r is None:
                continue
            try:
                state = self._target_state_from_json(r.json())
                if state.get("target_kind") is not None:
                    return state
            except Exception as exc:
                self._log.warning("Braiins %s target parse: %s", path, exc)
        return {"target_kind": None, "hashrate_target_th": None, "power_target_w": None}

    def get_power_target(self) -> int | None:
        for path in ("/performance/mode", "/performance/tuner-state"):
            r = self._request("GET", path)
            if r is None:
                continue
            try:
                found = self._find_power_target_watt(r.json())
                if found is not None:
                    return found
            except Exception as exc:
                self._log.warning("Braiins %s power parse: %s", path, exc)
        return None

    def set_hashrate_target(self, terahash: float) -> bool:
        r = self._request(
            "PUT",
            "/performance/hashrate-target",
            json={"terahash_per_second": float(terahash)},
        )
        ok = self._ok(r)
        if ok:
            self._log.info("hashrate target set to %.1f TH/s", terahash)
            try:
                accepted = self._find_terahash(r.json())
                if accepted is not None and abs(float(accepted) - float(terahash)) >= 0.1:
                    self._log.warning("Braiins returned hashrate target %.1f TH/s after requesting %.1f TH/s", accepted, terahash)
            except Exception:
                pass
        else:
            self._log.warning("set_hashrate_target failed: %s", r.text if r is not None else "no response")
        return ok

    def set_power_target(self, watt: int) -> bool:
        r = self._request(
            "PUT",
            "/performance/power-target",
            json={"watt": int(watt)},
        )
        ok = self._ok(r)
        if ok:
            self._log.info("power target set to %d W", watt)
            try:
                accepted = self._find_power_target_watt(r.json())
                if accepted is not None and abs(int(accepted) - int(watt)) >= 10:
                    self._log.warning("Braiins returned power target %d W after requesting %d W", accepted, watt)
            except Exception:
                pass
        else:
            self._log.warning("set_power_target failed: %s", r.text if r is not None else "no response")
        return ok

    def set_performance_mode_target(self, kind: str, target: float | int) -> bool:
        r = self._request("PUT", "/performance/mode", json=self._target_payload(kind, target))
        ok = self._ok(r)
        if ok:
            self._log.info("performance mode set to %s target %s", kind, target)
        else:
            self._log.warning("set_performance_mode_target failed: %s", r.text if r is not None else "no response")
        return ok

    def verify_target(self, kind: str, target: float | int) -> bool:
        return self.wait_for_target(kind, target) is not None

    def wait_for_target(self, kind: str, target: float | int, attempts: int = 6, delay_s: float = 2.0) -> dict | None:
        for attempt in range(attempts):
            state = self.get_performance_target_state()
            if self._target_matches(state, kind, target):
                return state
            if attempt + 1 < attempts:
                time.sleep(delay_s)
        return None

    def verify_target_kind(self, kind: str) -> bool:
        for _ in range(6):
            time.sleep(2)
            if self.get_performance_target_state().get("target_kind") == kind:
                return True
        return False

    def get_status(self) -> dict | None:
        """Return {power_watt, paused, hashrate_target_th, power_target_w} or None.

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

        target_state = self.get_performance_target_state()

        if status != self._STATUS_MINING:
            return {
                "power_watt": 0,
                "paused": True,
                **target_state,
            }

        watt = 0
        rs = self._request("GET", "/miner/stats")
        if rs is not None:
            try:
                ps = rs.json().get("power_stats") or {}
                watt = int((ps.get("approximated_consumption") or {}).get("watt") or 0)
            except Exception:
                watt = 0
        return {
            "power_watt": watt,
            "paused": False,
            **target_state,
        }


# ---------------------------------------------------------------------------
# Shared state (web UI ↔ control loop)
# ---------------------------------------------------------------------------

class StateStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._d: dict = {
            "soc": None, "p_grid": None, "p_pv": None, "p_akku": None,
            "p_load": None, "house_without_miner_w": None,
            "battery_charge_now_w": None, "available_w": None,
            "miner_power_w": None,
            "hashrate_target_th": None, "desired_hashrate_target_th": None,
            "power_target_w": None, "desired_power_target_w": None,
            "active_mode": "auto", "summer_profile": None,
            "summer_target_kind": None,
            "start_wait_remaining_s": None, "stop_wait_remaining_s": None,
            "summer_switch_remaining_s": None,
            "poll_interval_seconds": None,
            "display_state": "unknown", "manual_override": "auto",
            "pending_override": None, "mode_switch_remaining_s": None,
            "decision_title": "Warte auf Daten", "decision_reason": "",
            "auto_preview_action": None, "auto_preview_title": None,
            "auto_preview_reason": None,
            "command_state": None, "command_msg": None,
        }

    def update(self, **kw) -> None:
        with self._lock:
            self._d.update(kw)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._d)


@dataclass
class DesiredState:
    action: str
    title: str
    reason: str
    nums: dict
    hashrate_target_th: float | None = None
    power_target_w: int | None = None
    profile: str | None = None
    immediate_pause: bool = False

    @property
    def target_kind(self) -> str | None:
        if self.power_target_w is not None:
            return "power"
        if self.hashrate_target_th is not None:
            return "hashrate"
        return None


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
        self._stop_since: float | None = None
        self._summer_profile: str | None = None
        self._summer_switch_since: float | None = None
        self._target_transition_resume_until: float | None = None
        self._fronius_err = 0
        self._braiins_err = 0

    @staticmethod
    def _display(action: str | None) -> str:
        if action not in ("run", "pause"):
            return "unknown"
        return "mining" if action == "run" else "paused"

    @staticmethod
    def _decision_numbers(pf: dict, cfg: dict, miner_w_now: int) -> dict:
        house_without_miner = max(0.0, abs(pf.get("p_load", 0.0)) - max(0, miner_w_now))
        battery_charge_now = max(0.0, -pf.get("p_akku", 0.0))
        available = pf["p_pv"] - house_without_miner
        return {
            "house_without_miner_w": house_without_miner,
            "battery_charge_now_w": battery_charge_now,
            "available_w": available,
        }

    @staticmethod
    def _enabled(ctrl: dict, key: str) -> bool:
        return bool(ctrl.get(key))

    @staticmethod
    def _rule_list_text(rules: list[str]) -> str:
        if not rules:
            return ""
        if len(rules) == 1:
            return rules[0]
        return ", ".join(rules[:-1]) + " und " + rules[-1]

    def _start_rule_failures(self, pf: dict, cfg: dict) -> list[str]:
        ctrl = cfg.get("control", {})
        failures: list[str] = []
        if self._enabled(ctrl, "enable_start_soc"):
            limit = float(ctrl.get("start_soc_percent", 80))
            if pf["soc"] < limit:
                failures.append(f"Akku-SOC {pf['soc']:.1f}% unter {limit:g}%")
        if self._enabled(ctrl, "enable_start_battery_charge"):
            limit = int(ctrl.get("start_battery_charge_watt", 2000))
            charge = max(0.0, -pf.get("p_akku", 0.0))
            if charge < limit:
                failures.append(f"Akku lädt nur mit {charge:.0f} W statt {limit} W")
        return failures

    def _battery_reserve_failure(self, pf: dict, cfg: dict) -> str | None:
        ctrl = cfg.get("control", {})
        if not self._enabled(ctrl, "enable_pause_soc"):
            return None
        limit = float(ctrl.get("pause_soc_percent", 30))
        if pf["soc"] < limit:
            return f"Akku-SOC {pf['soc']:.1f}% unter {limit:g}%"
        return None

    def _pause_rule_failures(self, pf: dict, cfg: dict) -> list[str]:
        ctrl = cfg.get("control", {})
        failures: list[str] = []
        if self._enabled(ctrl, "enable_pause_battery_discharge"):
            limit = int(ctrl.get("pause_battery_discharge_watt", 300))
            if pf["p_akku"] >= limit:
                failures.append(f"Akku entlädt mit {pf['p_akku']:.0f} W ab Grenzwert {limit} W")
        if self._enabled(ctrl, "enable_pause_grid_import"):
            limit = int(ctrl.get("pause_grid_import_watt", 300))
            if pf["p_grid"] >= limit:
                failures.append(f"Netzbezug {pf['p_grid']:.0f} W ab Grenzwert {limit} W")
        return failures

    def _decide_auto(self, pf: dict, cfg: dict, miner_w_now: int) -> DesiredState:
        base = self._decide_summer(pf, cfg, miner_w_now)
        nums = base.nums
        reserve_failure = self._battery_reserve_failure(pf, cfg)
        pause_failures = self._pause_rule_failures(pf, cfg)

        # Watt-based protections always win. Previously they were ignored while
        # the miner was paused and whenever the SOC-reserve branch was active.
        if pause_failures:
            pause_minutes = float(cfg.get("control", {}).get("stop_stable_minutes", 0))
            immediate = pause_minutes <= 0
            suffix = "Miner wird sofort pausiert." if immediate else "Automatik pausiert, wenn der Zustand anhält."
            return DesiredState(
                "pause", "Schutzregel aktiv",
                f"{self._rule_list_text(pause_failures)}. {suffix}", nums,
                hashrate_target_th=base.hashrate_target_th,
                power_target_w=base.power_target_w,
                profile=base.profile,
                immediate_pause=immediate,
            )

        if reserve_failure:
            low_w = int(self._summer_target_for_profile(cfg, "night")[1])
            available = float(nums.get("available_w") or 0)
            battery_discharge = max(0.0, float(pf.get("p_akku") or 0))
            if available >= low_w and (self._cur_action != "run" or battery_discharge <= 0):
                day_w = int(cfg.get("summer", {}).get("day_pv_threshold_watt", 4000))
                if self._cur_action != "run":
                    start_failures = self._start_rule_failures(pf, cfg)
                    if start_failures:
                        return DesiredState(
                            "pause",
                            "Start wartet",
                            f"{self._rule_list_text(start_failures)}. Akku-Reserve ist geschützt; Startregel muss zuerst erfüllt sein.",
                            nums,
                            power_target_w=low_w,
                            profile="night",
                        )
                if base.profile == "day" and available >= day_w:
                    return DesiredState(
                        "run",
                        "Akku-Reserve geschützt",
                        "Akku ist unter Reserve, aber der PV-Überschuss deckt das High-Ziel. Mining läuft mit Hashrate-Ziel.",
                        nums,
                        hashrate_target_th=base.hashrate_target_th,
                        profile="day",
                    )
                high_wait = ""
                switch_remaining = self._summer_switch_remaining(cfg)
                if available >= day_w and switch_remaining is not None:
                    high_wait = (
                        f" High ist vorbereitet; Wechsel in {switch_remaining // 60}:"
                        f"{switch_remaining % 60:02d}, wenn der PV-Überschuss stabil bleibt."
                    )
                return DesiredState(
                    "run",
                    "Akku-Reserve geschützt",
                    f"Akku ist unter Reserve, aber PV nach Hauslast deckt das Eco-Ziel {low_w} W. "
                    f"Mining läuft mit Power-Ziel.{high_wait}",
                    nums,
                    power_target_w=low_w,
                    profile="night",
                )
            return DesiredState(
                "pause",
                "Akku-Schutz aktiv",
                f"{reserve_failure}. PV deckt das Eco-Ziel nicht ohne Akku; Miner bleibt aus.",
                nums,
                hashrate_target_th=base.hashrate_target_th,
                power_target_w=base.power_target_w,
                profile=base.profile,
                immediate_pause=True,
            )

        if self._cur_action == "run":
            return base

        start_failures = self._start_rule_failures(pf, cfg)
        if start_failures:
            return DesiredState(
                "pause",
                "Start wartet",
                f"{self._rule_list_text(start_failures)}. Startbedingungen müssen stabil erfüllt sein.",
                nums,
                hashrate_target_th=base.hashrate_target_th,
                power_target_w=base.power_target_w,
                profile=base.profile,
            )
        return base

    def _peek_auto_gate(self, desired: str, cfg: dict, immediate_pause: bool = False) -> str:
        """Return what Auto would do now without mutating timers."""
        now = time.monotonic()
        if desired == "pause":
            if immediate_pause:
                return "pause"
            if self._cur_action != "run":
                return "pause"
            wait_s = max(0, float(cfg["control"].get("stop_stable_minutes", 0))) * 60
            if self._stop_since is not None and now - self._stop_since >= wait_s:
                return "pause"
            return "run"

        if self._cur_action == "run":
            return "run"

        wait_s = max(0, float(cfg["control"].get("start_stable_minutes", 5))) * 60
        if self._start_since is not None and now - self._start_since >= wait_s:
            return "run"
        return "pause"

    @staticmethod
    def _preview_title(action: str) -> str:
        return "Mining aktiv" if action == "run" else "Pausieren"

    def _auto_preview_for_fixed(self, pf: dict | None, cfg: dict, miner_w_now: int) -> tuple[str, str, str]:
        if pf is None:
            return "pause", "Pausieren", "Ohne Wechselrichterdaten würde Auto sicherheitshalber pausieren."

        saved_profile = self._summer_profile
        saved_since = self._summer_switch_since
        try:
            auto_desired_state = self._decide_auto(pf, cfg, miner_w_now)
        finally:
            self._summer_profile = saved_profile
            self._summer_switch_since = saved_since

        auto_action = self._peek_auto_gate(auto_desired_state.action, cfg, auto_desired_state.immediate_pause)
        if auto_desired_state.action == "run" and auto_action == "pause":
            return auto_action, "Start wartet", f"{auto_desired_state.reason} Auto würde erst nach stabiler Lage starten."
        if auto_desired_state.action == "pause" and auto_action == "run":
            return auto_action, "Mining bleibt aktiv", "Auto würde vorerst weiterlaufen und erst pausieren, wenn der Zustand länger anhält."
        return auto_action, self._preview_title(auto_action), auto_desired_state.reason

    @staticmethod
    def _mode(cfg: dict) -> str:
        mode = cfg.get("mode", {}).get("active", "auto")
        return "auto" if mode in ("auto", "battery_auto", "summer_24h") else "auto"

    @staticmethod
    def _override(cfg: dict) -> str:
        override = cfg.get("modes", {}).get("manual_override", "auto")
        return override if override in RUN_MODES else "auto"

    def _fixed_override_state(self, cfg: dict, nums: dict) -> DesiredState | None:
        override = self._override(cfg)
        summer = cfg.get("summer", {})
        if override == "pause":
            return DesiredState("pause", "Pause", "Die Automatik ist pausiert.", nums)
        if override == "fixed_hashrate":
            target = max(1.0, min(200.0, float(summer.get("high_hashrate_th", 110))))
            return DesiredState(
                "run",
                "Fix Hashrate",
                f"Automatik ist aus. Der Miner läuft dauerhaft mit dem High-Ziel: {target:.1f} TH/s.",
                nums,
                hashrate_target_th=target,
                profile="fixed",
            )
        if override == "fixed_power":
            target = max(945, min(7000, int(summer.get("low_power_watt", 945))))
            return DesiredState(
                "run",
                "Fix Watt",
                f"Automatik ist aus. Der Miner läuft dauerhaft mit dem Eco-Ziel: {target} W.",
                nums,
                power_target_w=target,
                profile="fixed",
            )
        return None

    def _summer_target_for_profile(self, cfg: dict, profile: str) -> tuple[str, float | int]:
        summer = cfg.get("summer", {})
        high_th = float(summer.get("high_hashrate_th", 110))
        low_w = max(945, int(summer.get("low_power_watt", 945)))
        return ("hashrate", high_th) if profile == "day" else ("power", low_w)

    def _summer_switch_remaining(self, cfg: dict) -> int | None:
        if self._summer_switch_since is None:
            return None
        wait_s = max(0, float(cfg.get("summer", {}).get("switch_stable_minutes", 5))) * 60
        return max(0, int(wait_s - (time.monotonic() - self._summer_switch_since)))

    @staticmethod
    def _desired_with_target(action: str, title: str, reason: str, nums: dict,
                             target: tuple[str, float | int], profile: str) -> DesiredState:
        kind, value = target
        if kind == "power":
            return DesiredState(action, title, reason, nums, power_target_w=int(value), profile=profile)
        return DesiredState(action, title, reason, nums, hashrate_target_th=float(value), profile=profile)

    def _summer_profile_from_available(self, available: float, cfg: dict) -> str:
        summer = cfg.get("summer", {})
        day_pv = int(summer.get("day_pv_threshold_watt", 4000))
        night_pv = int(summer.get("night_pv_threshold_watt", 2000))
        if available >= day_pv:
            return "day"
        if available <= night_pv:
            return "night"
        return self._summer_profile if self._summer_profile in ("day", "night") else "night"

    def _decide_summer(self, pf: dict | None, cfg: dict, miner_w_now: int) -> DesiredState:
        nums = self._decision_numbers(
            pf or {"p_load": 0, "soc": 0, "p_pv": 0, "p_akku": 0, "p_grid": 0},
            cfg,
            miner_w_now,
        )
        summer = cfg.get("summer", {})
        day_pv = int(summer.get("day_pv_threshold_watt", 4000))
        night_pv = int(summer.get("night_pv_threshold_watt", 2000))
        wait_s = max(0, float(summer.get("switch_stable_minutes", 5))) * 60
        now = time.monotonic()
        available = nums["available_w"]

        if pf is None:
            if self._summer_profile not in ("day", "night"):
                self._summer_profile = "night"
            target = self._summer_target_for_profile(cfg, self._summer_profile)
            target_text = f"{target[1]:.1f} TH/s" if target[0] == "hashrate" else f"{int(target[1])} W"
            profile_name = "High" if self._summer_profile == "day" else "Eco"
            return self._desired_with_target(
                "run",
                "Automatik hält Zustand",
                f"Fronius ist nicht erreichbar. Auto hält {profile_name} mit {target_text}.",
                nums,
                target,
                self._summer_profile,
            )

        if self._summer_profile not in ("day", "night"):
            self._summer_profile = self._summer_profile_from_available(available, cfg)
            target = self._summer_target_for_profile(cfg, self._summer_profile)
            target_text = f"{target[1]:.1f} TH/s" if target[0] == "hashrate" else f"{int(target[1])} W"
            return self._desired_with_target(
                "run",
                f"Automatik {'High' if self._summer_profile == 'day' else 'Eco'}",
                f"PV-Profil aus aktuellem Überschuss nach Hauslast gesetzt: {target_text}.",
                nums,
                target,
                self._summer_profile,
            )

        desired_profile = self._summer_profile
        if self._summer_profile == "night" and available >= day_pv:
            desired_profile = "day"
        elif self._summer_profile == "day" and available <= night_pv:
            desired_profile = "night"

        if desired_profile != self._summer_profile:
            if self._summer_switch_since is None:
                self._summer_switch_since = now
            if now - self._summer_switch_since >= wait_s:
                self._summer_profile = desired_profile
                self._summer_switch_since = None
        else:
            self._summer_switch_since = None

        target = self._summer_target_for_profile(cfg, self._summer_profile)
        if desired_profile != self._summer_profile:
            target_name = "High" if desired_profile == "day" else "Eco"
            remain = max(0, int(wait_s - (now - (self._summer_switch_since or now))))
            return self._desired_with_target(
                "run",
                "Automatik wartet",
                f"{target_name} ist vorbereitet. Wechsel in {remain // 60}:{remain % 60:02d}, wenn PV stabil bleibt.",
                nums,
                target,
                self._summer_profile,
            )

        profile_name = "High" if self._summer_profile == "day" else "Eco"
        target_text = f"{target[1]:.1f} TH/s" if target[0] == "hashrate" else f"{int(target[1])} W"
        return self._desired_with_target(
            "run",
            f"Automatik {profile_name}",
            f"Miner läuft dauerhaft mit {target_text}. Umschaltung erfolgt per PV-Hysterese.",
            nums,
            target,
            self._summer_profile,
        )

    def _auto_gate(self, desired: str, cfg: dict, force_start: bool = False,
                   immediate_pause: bool = False) -> str:
        """Start slowly and stop only after sustained bad conditions."""
        now = time.monotonic()
        if desired == "pause":
            self._start_since = None
            if immediate_pause:
                self._stop_since = None
                return "pause"
            if self._cur_action != "run":
                self._stop_since = None
                return "pause"
            wait_s = max(0, float(cfg["control"].get("stop_stable_minutes", 0))) * 60
            if self._stop_since is None:
                self._stop_since = now
            if now - self._stop_since >= wait_s:
                self._stop_since = None
                return "pause"
            return "run"

        self._stop_since = None
        if self._cur_action == "run":
            self._start_since = None
            return "run"
        if force_start:
            self._start_since = None
            return "run"

        wait_s = max(0, float(cfg["control"].get("start_stable_minutes", 5))) * 60
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

    def _apply(self, action: str) -> bool:
        if self._cur_action is not None and action == self._cur_action:
            return True
        issued = self._braiins.pause() if action == "pause" else self._braiins.resume()
        verb = "Pause" if action == "pause" else "Start"
        if not issued:
            self._braiins_err += 1
            self._state.update(command_state="failed", command_msg=f"{verb}-Befehl wurde vom Miner nicht angenommen")
            return False
        if self._verify(action):
            self._cur_action = action
            self._braiins_err = 0
            self._state.update(command_state="ok", display_state=self._display(action), command_msg=f"{verb} vom Miner bestätigt")
            return True
        else:
            self._braiins_err += 1
            self._state.update(command_state="unconfirmed", command_msg=f"{verb} gesendet, aber vom Miner nicht bestätigt")
            return False

    def _apply_desired(self, desired: DesiredState, current_hashrate_th: float | None,
                       current_power_target_w: int | None, current_target_kind: str | None) -> None:
        before_err = self._braiins_err
        action_ok = self._apply(desired.action)
        if desired.action != "run" or (desired.hashrate_target_th is None and desired.power_target_w is None):
            return
        if not action_ok:
            return

        desired_kind = desired.target_kind
        desired_value = desired.power_target_w if desired_kind == "power" else desired.hashrate_target_th
        if desired_kind is None or desired_value is None:
            return

        current_state = {
            "target_kind": current_target_kind,
            "hashrate_target_th": current_hashrate_th,
            "power_target_w": current_power_target_w,
        }
        if BraiinsAPI._target_matches(current_state, desired_kind, desired_value):
            return

        if current_target_kind != desired_kind:
            mode_changed = self._braiins.set_performance_mode_target(desired_kind, desired_value)
            if mode_changed and self._cur_action == "run":
                # Braiins may briefly report idle/paused while applying a new
                # tuning mode. This must not turn a live target transition into
                # an Auto start-delay cycle.
                self._target_transition_resume_until = time.monotonic() + 300
            if not (mode_changed and self._braiins.verify_target_kind(desired_kind)):
                self._braiins_err = max(self._braiins_err, before_err) + 1
                self._state.update(command_state="failed", command_msg="Braiins Zielmodus wurde nicht bestätigt")
                return
            confirmed = self._braiins.wait_for_target(desired_kind, desired_value, attempts=3, delay_s=2)
            if confirmed is not None:
                self._braiins_err = 0
                msg = (
                    f"Power Target {desired.power_target_w} W aktiv"
                    if desired.power_target_w is not None
                    else f"Hashrate-Ziel {desired.hashrate_target_th:.1f} TH/s aktiv"
                )
                self._state.update(command_state="ok", command_msg=msg)
                return

        if desired.power_target_w is not None:
            for _ in range(2):
                if self._braiins.set_power_target(desired.power_target_w):
                    confirmed = self._braiins.wait_for_target("power", desired.power_target_w, attempts=4, delay_s=2)
                    if confirmed is not None:
                        self._braiins_err = 0
                        self._state.update(command_state="ok", command_msg=f"Power Target {desired.power_target_w} W aktiv")
                        return
            self._braiins_err = max(self._braiins_err, before_err) + 1
            actual = self._braiins.get_performance_target_state()
            actual_w = actual.get("power_target_w")
            msg = (
                f"Power Target wurde nicht bestätigt; Miner meldet {actual_w} W"
                if actual.get("target_kind") == "power" and actual_w is not None
                else "Power Target wurde vom Miner nicht bestätigt"
            )
            self._state.update(command_state="failed", command_msg=msg)
            return

        for _ in range(2):
            if self._braiins.set_hashrate_target(desired.hashrate_target_th):
                confirmed = self._braiins.wait_for_target("hashrate", desired.hashrate_target_th, attempts=4, delay_s=2)
                if confirmed is not None:
                    self._braiins_err = 0
                    msg = f"Hashrate-Ziel {desired.hashrate_target_th:.1f} TH/s aktiv"
                    self._state.update(command_state="ok", command_msg=msg)
                    return
        self._braiins_err = max(self._braiins_err, before_err) + 1
        actual = self._braiins.get_performance_target_state()
        actual_th = actual.get("hashrate_target_th")
        msg = (
            f"Hashrate-Ziel wurde nicht bestätigt; Miner meldet {actual_th:.1f} TH/s"
            if actual.get("target_kind") == "hashrate" and actual_th is not None
            else "Hashrate-Ziel wurde vom Miner nicht bestätigt"
        )
        self._state.update(command_state="failed", command_msg=msg)

    def _publish_off_state(self, pf: dict | None, active_mode: str, poll_interval: int,
                           pending_override: str | None,
                           mode_switch_remaining: int | None) -> None:
        """Publish live Fronius data without touching the Braiins API."""
        self._cur_action = None
        self._start_since = None
        self._stop_since = None
        self._summer_profile = None
        self._summer_switch_since = None
        self._target_transition_resume_until = None

        if pf is None:
            self._fronius_err += 1
            self._log.warning("Fronius unreachable while Braiins control is off (streak: %d)",
                              self._fronius_err)
        else:
            self._fronius_err = 0

        self._state.update(
            soc=pf.get("soc") if pf else None,
            p_grid=pf.get("p_grid") if pf else None,
            p_pv=pf.get("p_pv") if pf else None,
            p_akku=pf.get("p_akku") if pf else None,
            p_load=pf.get("p_load") if pf else None,
            house_without_miner_w=None,
            battery_charge_now_w=max(0.0, -float(pf.get("p_akku") or 0)) if pf else None,
            available_w=None,
            miner_power_w=None,
            hashrate_target_th=None,
            power_target_w=None,
            desired_hashrate_target_th=None,
            desired_power_target_w=None,
            display_state="off",
            manual_override="off",
            pending_override=pending_override,
            mode_switch_remaining_s=mode_switch_remaining,
            active_mode=active_mode,
            summer_profile=None,
            summer_target_kind=None,
            poll_interval_seconds=poll_interval,
            decision_title="Off – Miner-Steuerung aus",
            decision_reason=(
                "Braiins OS wird weder abgefragt noch gesteuert. "
                "Der Miner bleibt unverändert und kann direkt im Braiins GUI bedient werden."
            ),
            start_wait_remaining_s=None,
            stop_wait_remaining_s=None,
            summer_switch_remaining_s=None,
            auto_preview_action=None,
            auto_preview_title="Nicht berechnet",
            auto_preview_reason="Off fragt den Miner nicht ab und berechnet deshalb keine Automatikentscheidung.",
            command_state=None,
            command_msg=None,
        )
        self._log.info("[cycle] override=off -> Braiins API skipped")

    def run_cycle(self) -> None:
        self._cfg.apply_pending_run_mode()
        cfg = self._cfg.get()
        active_mode = self._mode(cfg)
        modes = cfg.get("modes", {})
        override = self._override(cfg)
        pending_override = modes.get("pending_override")
        pending_apply_at = modes.get("pending_apply_at")
        mode_switch_remaining = None
        if pending_override in RUN_MODES and pending_apply_at is not None:
            try:
                mode_switch_remaining = max(0, math.ceil(float(pending_apply_at) - time.time()))
            except (TypeError, ValueError):
                pending_override = None
        poll_interval = cfg.get("fronius", {}).get("poll_interval_seconds", 30)
        miner_host = cfg.get("miner", {}).get("host")
        pf = self._fronius.get_powerflow()
        if override == "off":
            self._publish_off_state(
                pf,
                active_mode,
                poll_interval,
                pending_override,
                mode_switch_remaining,
            )
            return
        miner_st = self._braiins.get_status() if miner_host else None
        miner_w_now = miner_st["power_watt"] if miner_st else 0
        current_hashrate_th = miner_st.get("hashrate_target_th") if miner_st else None
        current_power_target_w = miner_st.get("power_target_w") if miner_st else None
        current_target_kind = miner_st.get("target_kind") if miner_st else None
        if miner_st is not None:
            self._cur_action = "pause" if miner_st["paused"] else "run"

        if pf is None:
            self._fronius_err += 1
            self._log.warning("Fronius unreachable (streak: %d)", self._fronius_err)
            fixed_state = self._fixed_override_state(cfg, self._decision_numbers(
                {"p_load": 0, "soc": 0, "p_pv": 0, "p_akku": 0, "p_grid": 0},
                cfg,
                miner_w_now,
            ))
            if fixed_state is not None and miner_host:
                auto_action, auto_title, auto_reason = self._auto_preview_for_fixed(None, cfg, miner_w_now)
                self._state.update(
                    miner_power_w=miner_w_now if miner_st else None,
                    hashrate_target_th=current_hashrate_th,
                    power_target_w=current_power_target_w,
                    desired_hashrate_target_th=fixed_state.hashrate_target_th,
                    desired_power_target_w=fixed_state.power_target_w,
                    display_state=self._display(self._cur_action) if miner_st else "unknown",
                    manual_override=override,
                    pending_override=pending_override,
                    mode_switch_remaining_s=mode_switch_remaining,
                    active_mode=active_mode,
                    summer_profile=fixed_state.profile,
                    summer_target_kind=fixed_state.target_kind,
                    start_wait_remaining_s=None,
                    stop_wait_remaining_s=None,
                    summer_switch_remaining_s=None,
                    poll_interval_seconds=poll_interval,
                    decision_title=fixed_state.title,
                    decision_reason=fixed_state.reason,
                    auto_preview_action=auto_action,
                    auto_preview_title=auto_title,
                    auto_preview_reason=auto_reason,
                    **fixed_state.nums,
                )
                self._apply_desired(fixed_state, current_hashrate_th, current_power_target_w, current_target_kind)
                return
            if self._fronius_err >= 3 and miner_host and self._cur_action != "pause" and override == "auto":
                self._apply("pause")
            self._state.update(
                miner_power_w=miner_w_now if miner_st else None,
                hashrate_target_th=current_hashrate_th,
                power_target_w=current_power_target_w,
                display_state=self._display(self._cur_action) if miner_st else "unknown",
                manual_override=override,
                pending_override=pending_override,
                mode_switch_remaining_s=mode_switch_remaining,
                active_mode=active_mode,
                start_wait_remaining_s=None,
                stop_wait_remaining_s=None,
                summer_switch_remaining_s=None,
                poll_interval_seconds=poll_interval,
                decision_title="Fronius nicht erreichbar",
                decision_reason=(
                    "Wechselrichterdaten fehlen dreimal in Folge; Auto pausiert jetzt sicherheitshalber."
                    if self._fronius_err >= 3 else
                    f"Wechselrichterdaten fehlen ({self._fronius_err}/3). Auto hält vorerst den beobachteten Miner-Zustand."
                ),
                auto_preview_action="pause",
                auto_preview_title="Pausieren",
                auto_preview_reason="Ohne Wechselrichterdaten würde Auto sicherheitshalber pausieren.",
            )
            return

        self._fronius_err = 0
        nums = self._decision_numbers(pf, cfg, miner_w_now)
        if override == "auto" and cfg.get("modes", {}).get("sync_summer_profile_now"):
            self._summer_profile = self._summer_profile_from_available(nums["available_w"], cfg)
            self._summer_switch_since = None
            self._cfg.update({"modes": {"sync_summer_profile_now": False}})

        if not miner_host:
            desired_state = self._decide_auto(pf, cfg, 0)
            self._state.update(
                soc=pf["soc"], p_grid=pf["p_grid"], p_pv=pf["p_pv"], p_akku=pf["p_akku"], p_load=pf.get("p_load"),
                miner_power_w=None, hashrate_target_th=None, power_target_w=None,
                desired_hashrate_target_th=desired_state.hashrate_target_th,
                desired_power_target_w=desired_state.power_target_w,
                manual_override=override,
                pending_override=pending_override,
                mode_switch_remaining_s=mode_switch_remaining,
                active_mode=active_mode,
                display_state="unknown", poll_interval_seconds=poll_interval,
                summer_profile=desired_state.profile,
                summer_target_kind=desired_state.target_kind,
                start_wait_remaining_s=None, stop_wait_remaining_s=None,
                summer_switch_remaining_s=self._summer_switch_remaining(cfg),
                decision_title="Antminer fehlt", decision_reason="Fronius wird angezeigt; zum Schalten fehlt noch die Antminer-IP.",
                auto_preview_action=None, auto_preview_title=None,
                auto_preview_reason="Auto kann ohne Antminer-IP noch nicht schalten.",
                **nums,
            )
            return

        fixed_state = self._fixed_override_state(cfg, nums)
        if fixed_state is not None:
            auto_action, auto_title, auto_reason = self._auto_preview_for_fixed(pf, cfg, miner_w_now)
            self._state.update(
                soc=pf["soc"], p_grid=pf["p_grid"], p_pv=pf["p_pv"], p_akku=pf["p_akku"], p_load=pf.get("p_load"),
                miner_power_w=miner_w_now if miner_st else None,
                hashrate_target_th=current_hashrate_th,
                power_target_w=current_power_target_w,
                desired_hashrate_target_th=fixed_state.hashrate_target_th,
                desired_power_target_w=fixed_state.power_target_w,
                display_state=self._display(self._cur_action) if miner_st else "unknown", manual_override=override,
                pending_override=pending_override,
                mode_switch_remaining_s=mode_switch_remaining,
                active_mode=active_mode, summer_profile=fixed_state.profile,
                summer_target_kind=fixed_state.target_kind,
                start_wait_remaining_s=None,
                stop_wait_remaining_s=None,
                summer_switch_remaining_s=None,
                poll_interval_seconds=poll_interval,
                decision_title=fixed_state.title,
                decision_reason=fixed_state.reason,
                auto_preview_action=auto_action,
                auto_preview_title=auto_title,
                auto_preview_reason=auto_reason,
                **fixed_state.nums,
            )
            self._log.info("[cycle] override=%s target=%s/%s → %s",
                           override, fixed_state.target_kind,
                           fixed_state.hashrate_target_th or fixed_state.power_target_w,
                           fixed_state.action.upper())
            self._apply_desired(fixed_state, current_hashrate_th, current_power_target_w, current_target_kind)
            return

        auto_desired_state = self._decide_auto(pf, cfg, miner_w_now)
        auto_action = self._peek_auto_gate(auto_desired_state.action, cfg, auto_desired_state.immediate_pause)
        if auto_desired_state.action == "run" and auto_action == "pause":
            auto_preview_title = "Start wartet"
            auto_preview_reason = f"{auto_desired_state.reason} Beim Umschalten auf Auto startet der Miner erst nach stabiler Lage."
        elif auto_desired_state.action == "pause" and auto_desired_state.immediate_pause:
            auto_preview_title = "Pausieren"
            auto_preview_reason = auto_desired_state.reason
        elif auto_desired_state.action == "pause" and auto_action == "run":
            auto_preview_title = "Mining bleibt aktiv"
            auto_preview_reason = "Auto würde vorerst weiterlaufen und erst pausieren, wenn der Zustand länger anhält."
        else:
            auto_preview_title = self._preview_title(auto_action)
            auto_preview_reason = auto_desired_state.reason

        force_auto_start = override == "auto" and bool(cfg.get("modes", {}).get("resume_auto_now"))
        transition_resume = (
            override == "auto"
            and self._target_transition_resume_until is not None
            and time.monotonic() <= self._target_transition_resume_until
            and self._cur_action == "pause"
            and auto_desired_state.action == "run"
        )
        if self._target_transition_resume_until is not None and time.monotonic() > self._target_transition_resume_until:
            self._target_transition_resume_until = None
        action = self._auto_gate(
            auto_desired_state.action,
            cfg,
            force_auto_start or transition_resume,
            auto_desired_state.immediate_pause,
        )
        if force_auto_start:
            self._cfg.update({"modes": {"resume_auto_now": False}})
        desired_state = DesiredState(
            action,
            auto_desired_state.title,
            auto_desired_state.reason,
            auto_desired_state.nums,
            hashrate_target_th=auto_desired_state.hashrate_target_th,
            power_target_w=auto_desired_state.power_target_w,
            profile=auto_desired_state.profile,
            immediate_pause=auto_desired_state.immediate_pause,
        )
        nums = desired_state.nums
        title = desired_state.title
        reason = desired_state.reason
        wait_remaining = None
        stop_wait_remaining = None
        if force_auto_start and auto_desired_state.action == "run" and action == "run":
            title = "Auto aktiviert"
            reason = "Auto hat die aktuelle Startbedingung sofort angewendet."
        if transition_resume and action == "run":
            title = "Zielwechsel wird fortgesetzt"
            reason = "Braiins hat den Miner beim Zielwechsel kurz angehalten. Auto setzt Mining ohne Startwartezeit fort."
        if override == "auto" and auto_desired_state.action == "run" and action == "pause" and self._start_since is not None:
            wait_s = max(0, float(cfg["control"].get("start_stable_minutes", 5))) * 60
            wait_remaining = max(0, int(wait_s - (time.monotonic() - self._start_since)))
            title = "Warte auf stabilen Start"
            reason = f"{auto_desired_state.reason} Miner startet in {wait_remaining // 60}:{wait_remaining % 60:02d}, wenn alle Bedingungen stabil bleiben."
        if override == "auto" and auto_desired_state.action == "pause" and action == "pause" and auto_desired_state.immediate_pause:
            title = auto_desired_state.title
            reason = auto_desired_state.reason
        elif override == "auto" and auto_desired_state.action == "pause" and action == "run" and self._stop_since is not None:
            wait_s = max(0, float(cfg["control"].get("stop_stable_minutes", 0))) * 60
            stop_wait_remaining = max(0, int(wait_s - (time.monotonic() - self._stop_since)))
            title = "Watt-Spitze wird toleriert"
            reason = f"Miner läuft weiter. Auto pausiert erst in {stop_wait_remaining // 60}:{stop_wait_remaining % 60:02d}, wenn der Zustand anhält."

        self._state.update(
            soc=pf["soc"], p_grid=pf["p_grid"], p_pv=pf["p_pv"], p_akku=pf["p_akku"], p_load=pf.get("p_load"),
            miner_power_w=miner_w_now if miner_st else None,
            hashrate_target_th=current_hashrate_th,
            power_target_w=current_power_target_w,
            desired_hashrate_target_th=desired_state.hashrate_target_th,
            desired_power_target_w=desired_state.power_target_w,
            display_state=self._display(self._cur_action) if miner_st else "unknown", manual_override=override,
            pending_override=pending_override,
            mode_switch_remaining_s=mode_switch_remaining,
            active_mode=active_mode, summer_profile=desired_state.profile,
            summer_target_kind=desired_state.target_kind,
            poll_interval_seconds=poll_interval, decision_title=title, decision_reason=reason,
            start_wait_remaining_s=wait_remaining, stop_wait_remaining_s=stop_wait_remaining,
            summer_switch_remaining_s=self._summer_switch_remaining(cfg),
            auto_preview_action=auto_action, auto_preview_title=auto_preview_title,
            auto_preview_reason=auto_preview_reason,
            **nums,
        )

        if self._cur_action is not None and action == self._cur_action:
            self._log.info("[cycle] SOC=%.1f%% PV=%.0fW profile=%s target=%s/%s → no action change (%s)",
                           pf["soc"], pf["p_pv"], desired_state.profile, desired_state.target_kind,
                           desired_state.hashrate_target_th or desired_state.power_target_w, self._cur_action)
        else:
            self._log.info("[cycle] SOC=%.1f%% PV=%.0fW profile=%s target=%s/%s → %s",
                           pf["soc"], pf["p_pv"], desired_state.profile, desired_state.target_kind,
                           desired_state.hashrate_target_th or desired_state.power_target_w, action.upper())
        self._apply_desired(desired_state, current_hashrate_th, current_power_target_w, current_target_kind)
        if transition_resume and self._cur_action == "run":
            self._target_transition_resume_until = None

# ---------------------------------------------------------------------------
# Flask web app
# ---------------------------------------------------------------------------

def validate_config_patch(data: dict) -> str | None:
    ctrl = data.get("control", {})
    fronius = data.get("fronius", {})
    miner = data.get("miner", {})
    mode = data.get("mode", {})
    summer = data.get("summer", {})
    modes = data.get("modes", {})

    def bounded(section: dict, key: str, default: int | float, label: str,
                minimum: int | float, maximum: int | float,
                integer: bool = False) -> int | float:
        raw = section.get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{label} muss eine Zahl sein")
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"{label} muss eine endliche Zahl sein")
        if integer and not value.is_integer():
            raise ValueError(f"{label} muss eine ganze Zahl sein")
        if not minimum <= value <= maximum:
            raise ValueError(f"{label} muss zwischen {minimum} und {maximum} liegen")
        return int(value) if integer else value

    try:
        for section, key, label in (
            (fronius, "host", "Fronius-IP"),
            (fronius, "pv2_host", "IP des zweiten Wechselrichters"),
            (miner, "host", "Antminer-IP"),
            (miner, "api_key", "Braiins-Passwort"),
        ):
            if key in section and not isinstance(section[key], str):
                raise ValueError(f"{label} muss Text sein")
        for key in (
            "enable_start_soc",
            "enable_start_battery_charge",
            "enable_pause_soc",
            "enable_pause_battery_discharge",
            "enable_pause_grid_import",
        ):
            if key in ctrl and not isinstance(ctrl[key], bool):
                raise ValueError(f"{key} muss true oder false sein")
        if mode.get("active", "auto") not in ("auto", "battery_auto", "summer_24h"):
            return "Betriebsmodus ist ungültig"
        if modes and modes.get("manual_override", "auto") not in RUN_MODES:
            return "Steuerungsmodus ist ungültig"

        bounded(fronius, "poll_interval_seconds", 30, "Abfrage-Intervall", 10, 300, integer=True)
        start_soc = bounded(ctrl, "start_soc_percent", 80, "Start-SOC", 0, 100)
        bounded(ctrl, "start_battery_charge_watt", 2000, "Start-Akkuladung", 0, 30000, integer=True)
        pause_soc = bounded(ctrl, "pause_soc_percent", 30, "Akku-Reserve", 0, 100)
        if (
            bool(ctrl.get("enable_start_soc"))
            and bool(ctrl.get("enable_pause_soc"))
            and start_soc <= pause_soc
        ):
            return "Start-SOC muss höher sein als Pause-SOC, sonst kann Auto direkt wieder stoppen"
        bounded(ctrl, "pause_battery_discharge_watt", 300, "Akku-Entladung", 0, 10000, integer=True)
        bounded(ctrl, "pause_grid_import_watt", 300, "Netzbezug", 0, 10000, integer=True)
        bounded(ctrl, "start_stable_minutes", 5, "Start-Wartezeit", 1, 60, integer=True)
        bounded(ctrl, "stop_stable_minutes", 0, "Pause-Verzögerung", 0, 60, integer=True)
        high_pv = bounded(summer, "day_pv_threshold_watt", 4000, "High-PV-Überschuss", 0, 30000, integer=True)
        eco_pv = bounded(summer, "night_pv_threshold_watt", 2000, "Eco-PV-Überschuss", 0, 30000, integer=True)
        if eco_pv >= high_pv:
            return "High-PV-Überschuss muss höher sein als Eco-PV-Überschuss"
        bounded(summer, "high_hashrate_th", 110, "High Hashrate-Ziel", 1, 200)
        bounded(summer, "low_power_watt", 945, "Eco Power-Ziel", 945, 7000, integer=True)
        bounded(summer, "switch_stable_minutes", 5, "Automatik-Wechselzeit", 1, 120, integer=True)
    except ValueError as exc:
        return str(exc)
    except TypeError:
        return "Konfigurationswerte sind ungültig"

    return None


def normalize_config_patch(data: dict, repair_invalid: bool = True) -> None:
    mode = data.setdefault("mode", {})
    if mode.get("active") in ("battery_auto", "summer_24h", None):
        mode["active"] = "auto"
    ctrl = data.setdefault("control", {})
    legacy_map = {
        "start_soc_percent": ("battery_full_soc", 80),
        "start_battery_charge_watt": ("battery_charge_target_watt", 2000),
        "pause_battery_discharge_watt": ("akku_entlade_sperre_watt", 300),
        "pause_grid_import_watt": ("grid_import_tolerance_watt", 300),
    }
    for new_key, (old_key, default) in legacy_map.items():
        if new_key not in ctrl:
            ctrl[new_key] = ctrl.get(old_key, default)
    for key in (
        "enable_start_soc",
        "enable_start_battery_charge",
        "enable_pause_soc",
        "enable_pause_battery_discharge",
        "enable_pause_grid_import",
    ):
        if repair_invalid:
            ctrl[key] = bool(ctrl.get(key, False))
        else:
            ctrl.setdefault(key, False)
    summer = data.setdefault("summer", {})
    try:
        summer["low_power_watt"] = max(945, int(summer.get("low_power_watt", 945)))
    except (TypeError, ValueError):
        if repair_invalid:
            summer["low_power_watt"] = 945
    modes = data.setdefault("modes", {})
    if modes.get("manual_override") == "run":
        modes["manual_override"] = "auto"
    if modes.get("pending_override") is not None and modes.get("pending_override") not in RUN_MODES:
        modes["pending_override"] = None
        modes["pending_apply_at"] = None
    modes.pop("fixed_hashrate_th", None)
    modes.pop("fixed_power_watt", None)
    modes.pop("sync_summer_profile_now", None)
    data.setdefault("miner", {}).pop("expected_power_watt", None)


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


def _update_request(url: str) -> tuple[str, dict[str, str]]:
    headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.netloc == "raw.githubusercontent.com" and len(parts) >= 4:
        owner, repo, ref, *file_parts = parts
        api_path = quote("/".join(file_parts), safe="/")
        api_url = (
            f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
            f"/contents/{api_path}?{urlencode({'ref': ref})}"
        )
        headers["Accept"] = "application/vnd.github.raw+json"
        return api_url, headers
    return _cache_busted_url(url), headers


def _download_update() -> tuple[bytes | None, str | None, str]:
    try:
        url, headers = _update_request(UPDATE_URL)
        r = _http.get(
            url,
            headers=headers,
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


def create_app(cfg_manager: ConfigManager, state: StateStore,
               control_wake: threading.Event | None = None) -> Flask:
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
        if not isinstance(data, dict) or not data:
            return jsonify({"error": "Invalid JSON"}), 400
        for section in ("fronius", "miner", "control", "summer", "mode", "modes"):
            if section in data and not isinstance(data[section], dict):
                return jsonify({"error": f"{section} muss ein Objekt sein"}), 400
        fronius_host = data.get("fronius", {}).get("host")
        if not isinstance(fronius_host, str) or not fronius_host.strip():
            return jsonify({"error": "Fronius GEN24 Plus — IP darf nicht leer sein"}), 400
        api_key = data.get("miner", {}).get("api_key", "")
        if isinstance(api_key, str) and api_key.startswith("••"):
            data.setdefault("miner", {})["api_key"] = cfg_manager.get()["miner"].get("api_key", "")
        normalize_config_patch(data, repair_invalid=False)
        error = validate_config_patch(data)
        if error:
            return jsonify({"error": error}), 400
        cfg_manager.update(data)
        if control_wake is not None:
            control_wake.set()
        return jsonify({"ok": True})

    @app.route("/api/update", methods=["POST"])
    def api_update():
        if shutil.which("rc-service") is None:
            return jsonify({
                "error": "Web-Update ist nur in der LXC-Installation verfügbar. Docker-Container bitte per neuem Image aktualisieren."
            }), 400

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

    @app.route("/api/run-mode", methods=["POST"])
    def api_run_mode():
        data = request.get_json(silent=True) or {}
        override = data.get("override", data.get("mode", "auto"))
        active_mode = data.get("active_mode")
        if override not in RUN_MODES:
            return jsonify({"error": "Invalid mode"}), 400
        if active_mode in ("battery_auto", "summer_24h"):
            active_mode = "auto"
        if active_mode is not None and active_mode != "auto":
            return jsonify({"error": "Invalid active mode"}), 400

        current, apply_at = cfg_manager.queue_run_mode(override)
        pending = override if apply_at is not None else None
        state.update(
            pending_override=pending,
            mode_switch_remaining_s=10 if pending else None,
            command_state=None,
            command_msg=None,
        )
        if control_wake is not None:
            control_wake.set()
        logging.getLogger("cycle").info("Run mode queued: current=%s requested=%s apply_at=%s",
                                        current, override, apply_at)
        return jsonify({
            "ok": True,
            "manual_override": current,
            "pending_override": pending,
            "apply_at": apply_at,
        })

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
    shutdown = threading.Event()
    control_wake = threading.Event()
    app     = create_app(cfg_manager, state, control_wake)

    def _sig(_s, _f):
        log.info("Signal received — shutting down")
        shutdown.set()
        control_wake.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    def _loop():
        while not shutdown.is_set():
            try:
                ctrl.run_cycle()
            except Exception as exc:
                log.exception("run_cycle error: %s", exc)
            cfg = cfg_manager.get()
            timeout = float(cfg["fronius"].get("poll_interval_seconds", 30))
            pending_at = cfg.get("modes", {}).get("pending_apply_at")
            if pending_at is not None:
                try:
                    timeout = min(timeout, max(0.0, float(pending_at) - time.time()))
                except (TypeError, ValueError):
                    pass
            control_wake.wait(timeout=timeout)
            control_wake.clear()
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

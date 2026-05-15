#!/usr/bin/env python3
"""pv-miner — web-controlled PV surplus mining daemon."""

import json
import hashlib
import logging
import logging.handlers
import os
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

# Rough power estimate for the start decision: an S19j Pro on Braiins OS runs
# at roughly 30-33 J/TH, so power(W) ≈ hashrate(TH/s) × this factor. Slightly
# high on purpose → the miner starts a touch later, never importing from grid.
WATT_PER_TH = 33

DEFAULT_CONFIG: dict = {
    "fronius": {
        "host": "",
        "poll_interval_seconds": 30,
    },
    "miner": {
        "host": "",
        "api_key": "",
        # The hashrate target lives on the miner itself (Braiins OS). pv-miner
        # reads it from there and can set it there on request — it is NOT
        # stored in this file.
    },
    "control": {
        "soc_minimum":      15,
        "soc_hysterese":    5,
        "soc_freigabe":     95,
        "soc_start_mining": 0,       # 0 = immer erlaubt; z.B. 100 = erst wenn Akku voll
        "netz_puffer_watt": 200,
        "akku_entlade_sperre_watt": 100,
        "pv_schwelle_watt": 12000,   # nur für Modus "battery_first"
        "hysterese_zyklen": 2,
    },
    "modes": {
        # "grid"           — nur minen wenn P_Grid negativ (Einspeisung); Akku lädt voll zuerst
        # "pv_and_battery" — minen sobald PV > Hausverbrauch; Akku + Miner teilen Überschuss
        # "battery_first"  — Akku hat Vorrang; minen erst wenn PV-Produktion > pv_schwelle_watt
        "surplus_source":  "grid",
        # "auto" | "pause" | "run"
        "manual_override": "auto",
    },
    "time_rule": {
        "enabled":       False,
        "start":         "18:00",
        "end":           "07:00",
        "soc_threshold": 50,
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
.fg{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
.fg-wide{display:grid;grid-template-columns:1fr;gap:14px}
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:.76rem;color:#8b949e}
.field input,.field select{background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:7px 10px;font-size:.88rem}
.field input:focus,.field select:focus{outline:none;border-color:#388bfd}
.hint{font-size:.72rem;color:#6e7681;line-height:1.45;margin-top:2px}
.hint em{font-style:normal;color:#cdd9e5;font-weight:600}
.hint.warn em{color:#e3b341}
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
    <div class="card"><div class="lbl">Verf&#252;gbar f. Miner</div><div class="val" id="v-verfuegbar">&#8212;</div></div>
    <div class="card"><div class="lbl" id="l-pgrid">Netz</div><div class="val" id="v-pgrid">&#8212;</div></div>
    <div class="card"><div class="lbl">PV Produktion</div><div class="val" id="v-ppv">&#8212;</div></div>
    <div class="card"><div class="lbl">Hausverbrauch</div><div class="val" id="v-pload">&#8212;</div></div>
    <div class="card"><div class="lbl" id="l-pakku">Batterie</div><div class="val" id="v-pakku">&#8212;</div></div>
    <div class="card"><div class="lbl">Miner Verbrauch</div><div class="val" id="v-power">&#8212;</div></div>
    <div class="card"><div class="lbl">Ziel-Hashrate</div><div class="val" id="v-hr">&#8212;</div></div>
  </div>
</section>

<section>
  <h2>Override</h2>
  <div class="ov-row">
    <button id="ov-auto"  onclick="setOv('auto')">Auto (PV-gesteuert)</button>
    <button id="ov-pause" onclick="setOv('pause')">Pause erzwingen</button>
    <button id="ov-run"   onclick="setOv('run')">Laufen lassen</button>
  </div>
  <div class="hint" style="margin-top:8px">Auto folgt der PV-/SOC-Regelung. <em>Pause</em> und <em>Laufen lassen</em> &#252;berschreiben die Automatik bis du wieder auf Auto stellst.</div>
  <div id="cmdmsg" class="hint" style="margin-top:6px"></div>
</section>

<section>
  <h2>Ziel-Hashrate</h2>
  <div class="ov-row" style="align-items:center">
    <input id="f-hr" type="number" min="10" max="200" step="1"
           style="width:120px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:7px 10px;font-size:.88rem">
    <span style="color:#8b949e;font-size:.88rem">TH/s</span>
    <button onclick="setHashrate()">An den Miner senden</button>
    <span id="hrmsg"></span>
  </div>
  <div class="hint" id="h-hr">Wird direkt im Braiins OS Tuner gesetzt. L&#252;fter und alle anderen Einstellungen bleiben unber&#252;hrt.</div>
</section>

<section>
  <h2>Konfiguration</h2>
  <div class="box">

    <div class="fsec">
      <h3>Ger&#228;te</h3>
      <div class="fg">
        <div class="field">
          <label>Fronius GEN24 Plus — IP</label>
          <input id="f-fh" placeholder="192.168.1.xxx">
          <div class="hint">IP-Adresse des Hybrid-Wechselrichters <em>mit Batterie</em> (nicht der Symo).</div>
        </div>
        <div class="field">
          <label>Antminer — IP</label>
          <input id="f-mh" placeholder="192.168.1.xxx">
          <div class="hint">IP-Adresse des Antminers mit Braiins OS.</div>
        </div>
        <div class="field">
          <label>Braiins OS Passwort (root)</label>
          <input id="f-ak" type="password" placeholder="leer = kein Passwort">
          <div class="hint">Passwort des <em>root</em>-Logins in Braiins OS. Leer lassen wenn kein Passwort gesetzt ist (Werkseinstellung).</div>
        </div>
        <div class="field">
          <label>Abfrage-Intervall (Sekunden)</label>
          <input id="f-pi" type="number" min="10" max="300" oninput="updateHints()">
          <div class="hint" id="h-pi">Alle 30 Sekunden wird der Fronius abgefragt und der Miner nachgeregelt.</div>
        </div>
      </div>
    </div>

    <div class="fsec">
      <h3>Batterie-Schwellwerte</h3>
      <div class="fg">
        <div class="field">
          <label>SOC Schutzgrenze (%)</label>
          <input id="f-sm" type="number" min="0" max="100" oninput="updateHints()">
          <div class="hint" id="h-sm">F&#228;llt der Akku unter diesen Wert, wird der Miner sofort gestoppt.</div>
        </div>
        <div class="field">
          <label>SOC Wiederstart-Hysterese (%)</label>
          <input id="f-sh" type="number" min="0" max="30" oninput="updateHints()">
          <div class="hint" id="h-sh">Nach einem SOC-Stopp startet der Miner erst wieder wenn der Akku wieder weiter geladen ist &#8212; verhindert schnelles Ein/Ausschalten.</div>
        </div>
        <div class="field">
          <label>SOC Volllast-Freigabe (%)</label>
          <input id="f-sf" type="number" min="0" max="100" oninput="updateHints()">
          <div class="hint" id="h-sf">Ab diesem SOC l&#228;uft der Miner auf voller Leistung &#8212; egal wie viel PV gerade produziert wird. Der Akku ist voll, der Strom muss weg.</div>
        </div>
        <div class="field">
          <label>SOC: Mining erlaubt ab (%, 0 = immer)</label>
          <input id="f-sstart" type="number" min="0" max="100" oninput="updateHints()">
          <div class="hint" id="h-sstart">Auf 0 lassen wenn der Miner sofort starten soll sobald PV-&#220;berschuss da ist. Auf 100 setzen um erst zu minen wenn der Akku vollst&#228;ndig geladen ist.</div>
        </div>
      </div>
    </div>

    <div class="fsec">
      <h3>Regelverhalten</h3>
      <div class="fg">
        <div class="field">
          <label>Netz-Sicherheitspuffer (W)</label>
          <input id="f-np" type="number" min="0" max="2000" oninput="updateHints()">
          <div class="hint" id="h-np">Dieser Puffer wird vom berechneten &#220;berschuss abgezogen damit kein Strom vom Netz bezogen wird. Empfehlung: 150&#8211;300 W.</div>
        </div>
        <div class="field">
          <label>Akku-Entlade-Sperre (W)</label>
          <input id="f-abs" type="number" min="0" max="2000" oninput="updateHints()">
          <div class="hint" id="h-abs">Wenn der Fronius meldet, dass der Akku deutlich entl&#228;dt, wird im Auto-Modus nicht gestartet.</div>
        </div>
        <div class="field">
          <label>Hysterese-Zyklen (Start/Stopp)</label>
          <input id="f-hz" type="number" min="1" max="10" oninput="updateHints()">
          <div class="hint" id="h-hz">Start und Stopp werden erst ausgef&#252;hrt wenn die Bedingung so viele Messungen in Folge erf&#252;llt ist &#8212; verhindert Fehlentscheidungen durch kurze Wolken.</div>
        </div>
      </div>
    </div>

    <div class="fsec">
      <h3>Betriebsmodi</h3>
      <div class="fg-wide">
        <div class="field">
          <label>Woher kommt der &#220;berschuss f&#252;r den Miner?</label>
          <select id="f-ss" onchange="updateHints()">
            <option value="grid">Netz-Einspeisung &#8212; Akku l&#228;dt komplett zuerst</option>
            <option value="pv_and_battery">PV minus Hausverbrauch &#8212; Miner und Akku teilen gleichzeitig</option>
            <option value="battery_first">Akku hat Vorrang &#8212; minen ab fester PV-Schwelle</option>
          </select>
          <div class="hint" id="h-ss"></div>
        </div>
        <div class="field">
          <label>PV-Schwelle f&#252;r &#8222;Akku hat Vorrang&#8220; (W)</label>
          <input id="f-pvs" type="number" min="0" max="100000" step="100" oninput="updateHints()">
          <div class="hint" id="h-pvs"></div>
        </div>
      </div>
    </div>

    <div class="fsec">
      <h3>Zeitfenster-Schutz</h3>
      <div class="fg">
        <div class="field">
          <label>Regel aktiv</label>
          <select id="f-te" onchange="updateHints()">
            <option value="false">Aus</option>
            <option value="true">Ein</option>
          </select>
          <div class="hint" id="h-te">Optionaler Schutz f&#252;r ein frei w&#228;hlbares Zeitfenster, wenn der Akku nicht weit genug geladen ist.</div>
        </div>
        <div class="field">
          <label>Von Uhrzeit</label>
          <input id="f-ts" type="time" oninput="updateHints()">
          <div class="hint">Beginn des Zeitfensters nach lokaler Container-Zeit. Voreinstellung ist nur ein Beispiel.</div>
        </div>
        <div class="field">
          <label>Bis Uhrzeit</label>
          <input id="f-tend" type="time" oninput="updateHints()">
          <div class="hint">Ende des Zeitfensters. Ein Fenster &#252;ber Mitternacht ist erlaubt.</div>
        </div>
        <div class="field">
          <label>Miner pausieren wenn SOC bei oder unter (%)</label>
          <input id="f-tso" type="number" min="0" max="100" oninput="updateHints()">
          <div class="hint" id="h-tso"></div>
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
function hint(id,html,warn){
  const el=document.getElementById(id);
  if(!el)return;
  el.innerHTML=html;
  el.className='hint'+(warn?' warn':'');
}
function v(id,def){return +document.getElementById(id)?.value||def;}
function s(id){return document.getElementById(id)?.value||'';}
let hrInit=false;

function updateHints(){
  const sm=v('f-sm',15),sh=v('f-sh',5),sf=v('f-sf',95),ss2=v('f-sstart',0);
  const np=v('f-np',200),absw=v('f-abs',100),hz=v('f-hz',2),pi=v('f-pi',30);
  const pvs=v('f-pvs',12000);
  const ss=s('f-ss');
  const te=s('f-te')==='true',ts=s('f-ts')||'18:00',tend=s('f-tend')||'07:00',tso=v('f-tso',50);

  hint('h-pi',`Alle <em>${pi} Sekunden</em> wird der Wechselrichter abgefragt und entschieden ob der Miner laufen darf.`);
  hint('h-sm',`Fällt der Akku unter <em>${sm}%</em>, wird der Miner sofort pausiert — egal wie viel PV vorhanden ist.`);
  hint('h-sh',`Nach einem SOC-Stopp startet der Miner erst wieder bei <em>${sm+sh}%</em> (${sm}% + ${sh}% Hysterese). Verhindert schnelles Ein-/Ausschalten am Schwellwert.`);
  hint('h-sf',`Ab <em>${sf}% SOC</em> läuft der Miner auch ohne PV-Überschuss — der Akku ist faktisch voll und der Strom muss irgendwo hin.`);

  if(ss2===0){
    hint('h-sstart','Mining ist erlaubt sobald PV-Überschuss vorhanden ist und der SOC über der Schutzgrenze liegt. Normaler Betrieb.');
  } else if(ss2>=100){
    hint('h-sstart','<em>Batterie zuerst:</em> Der Miner startet erst wenn der Akku auf 100% geladen ist. Tagsüber lädt der Akku durch, danach wird der Überschuss zum Minen verwendet.');
  } else {
    hint('h-sstart',`Der Miner startet erst wenn der Akku <em>${ss2}%</em> erreicht hat. Darunter lädt der Akku zuerst.`);
  }

  hint('h-np',`<em>${np} W</em> Sicherheitspuffer — es muss ${np} W mehr Überschuss da sein als der Miner zieht, bevor gestartet wird. Größerer Wert = sicherer kein Netzbezug.`);
  hint('h-abs',`Wenn der Akku mit mehr als <em>${absw} W</em> entlädt, wertet pv-miner das als Defizit und startet im Auto-Modus nicht neu.`);

  const delaySec=hz*pi;
  hint('h-hz',`Start und Stopp werden erst ausgeführt wenn die Bedingung <em>${hz} Messungen hintereinander</em> erfüllt ist (= ${delaySec} Sekunden). Eine kurze Wolke löst damit keinen Stopp aus.`);

  if(ss==='grid'){
    hint('h-ss','<em>Akku lädt komplett zuerst.</em> Der Miner startet erst wenn echt Strom ins Netz eingespeist wird — also wenn der Akku voll ist oder keine Ladung mehr aufnimmt.<br><br>Beispiel: 5 kW PV &#8226; 2 kW Haus &#8226; Akku lädt 3 kW &#8594; 0 W Einspeisung &#8594; Miner aus, bis der Akku voll ist.');
  } else if(ss==='pv_and_battery'){
    hint('h-ss','<em>Miner und Akku teilen sich die Sonne.</em> Der Miner startet sobald die PV-Produktion den Hausverbrauch plus seinen eigenen Bedarf deckt — egal ob der Akku noch lädt.<br><br>Beispiel: 5 kW PV &#8226; 2 kW Haus &#8594; 3 kW frei &#8594; Miner startet, der Akku lädt mit dem Rest. Vorteil: Miner startet früher am Morgen.');
  } else {
    hint('h-ss',`<em>Akku hat Vorrang.</em> Der Akku darf mit voller Leistung laden. Der Miner startet sobald die <em>PV-Produktion über ${(pvs/1000).toFixed(1)} kW</em> liegt — dann ist auch bei voll ladendem Akku genug Sonne für den Miner da.<br><br>Beispiel: Akku lädt mit bis zu 11 kW. Erst wenn die PV-Anlage über ${(pvs/1000).toFixed(1)} kW liefert wird gemint. Darunter bekommt der Akku die ganze Sonne. Schwelle rechts einstellbar.`);
  }
  hint('h-pvs', ss==='battery_first'
    ? `Der Miner läuft nur wenn die PV-Anlage mehr als <em>${pvs} W</em> (${(pvs/1000).toFixed(1)} kW) produziert. Tipp: etwas über die max. Akku-Ladeleistung legen (z.B. Akku 11 kW &#8594; Schwelle 12000 W).`
    : 'Wird nur im Modus &#8222;Akku hat Vorrang&#8220; verwendet.');

  if(te){
    hint('h-te',`Aktiv: Zwischen <em>${ts}</em> und <em>${tend}</em> wird der Miner pausiert wenn der Akku bei ${tso}% oder weniger liegt.`);
    hint('h-tso',`Im Zeitfenster wird pausiert sobald der SOC <em>${tso}% oder weniger</em> beträgt.`);
  } else {
    hint('h-te','Aus: Es gilt nur die normale PV-/SOC-Regelung.');
    hint('h-tso','Wird nur verwendet wenn die Zeitfenster-Regel aktiv ist.');
  }
}

async function fetchStatus(){
  try{
    const d=await(await fetch('/api/status',{cache:'no-store'})).json();
    const fw=v=>v!=null?Math.round(v)+' W':'—';
    const absw=v=>v!=null?Math.round(Math.abs(v))+' W':'—';
    document.getElementById('v-soc').textContent=d.soc!=null?d.soc.toFixed(1)+'%':'—';
    document.getElementById('v-verfuegbar').textContent=fw(d.verfuegbar_w);
    document.getElementById('l-pgrid').textContent=d.p_grid==null?'Netz':(d.p_grid<0?'Netz Einspeisung':(d.p_grid>0?'Netz Bezug':'Netz'));
    document.getElementById('v-pgrid').textContent=absw(d.p_grid);
    document.getElementById('v-ppv').textContent=fw(d.p_pv);
    document.getElementById('v-pload').textContent=d.p_load!=null?Math.round(Math.abs(d.p_load))+' W':'—';
    document.getElementById('l-pakku').textContent=d.p_akku==null?'Batterie':(d.p_akku>0?'Batterie entlädt':(d.p_akku<0?'Batterie lädt':'Batterie'));
    document.getElementById('v-pakku').textContent=absw(d.p_akku);
    document.getElementById('v-power').textContent=fw(d.miner_power_w);
    const hr=d.hashrate_target_th;
    document.getElementById('v-hr').textContent=hr!=null?(+hr).toFixed(0)+' TH/s':'—';
    const hrInput=document.getElementById('f-hr');
    if(hr!=null && !hrInit && document.activeElement!==hrInput){hrInput.value=Math.round(hr);hrInit=true;}
    hint('h-hr',(hr!=null?`Aktuell am Miner: <em>${(+hr).toFixed(0)} TH/s</em>. `:'')
      +'Der Wert wird direkt im Braiins OS Tuner gesetzt (Modus Hashrate-Target). L&#252;fter und alle anderen Einstellungen bleiben unber&#252;hrt.');
    const st=d.display_state||'unknown';
    const b=document.getElementById('badge');
    b.className='badge '+st;
    const L={mining:'&#9935; Mining',paused:'&#9646; Pausiert',unknown:'&#8212;'};
    b.innerHTML=L[st]||st;
    ['auto','pause','run'].forEach(m=>{
      document.getElementById('ov-'+m).classList.toggle('active',m===(d.manual_override||'auto'));
    });
    const cm=document.getElementById('cmdmsg');
    if(d.command_state==='ok'){cm.className='hint';cm.style.marginTop='6px';cm.innerHTML='&#10003; '+(d.command_msg||'Letzter Schaltbefehl bestätigt');}
    else if(d.command_state==='unconfirmed'||d.command_state==='failed'){cm.className='hint warn';cm.style.marginTop='6px';cm.innerHTML='&#9888;&#65039; <em>'+(d.command_msg||'Letzter Schaltbefehl nicht bestätigt')+'</em>';}
    else{cm.innerHTML='';}
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
    document.getElementById('f-sm').value=d.control?.soc_minimum??15;
    document.getElementById('f-sh').value=d.control?.soc_hysterese??5;
    document.getElementById('f-sf').value=d.control?.soc_freigabe??95;
    document.getElementById('f-sstart').value=d.control?.soc_start_mining??0;
    document.getElementById('f-np').value=d.control?.netz_puffer_watt??200;
    document.getElementById('f-abs').value=d.control?.akku_entlade_sperre_watt??100;
    document.getElementById('f-hz').value=d.control?.hysterese_zyklen??2;
    document.getElementById('f-pvs').value=d.control?.pv_schwelle_watt??12000;
    document.getElementById('f-ss').value=d.modes?.surplus_source||'grid';
    document.getElementById('f-te').value=String(!!d.time_rule?.enabled);
    document.getElementById('f-ts').value=d.time_rule?.start||'18:00';
    document.getElementById('f-tend').value=d.time_rule?.end||'07:00';
    document.getElementById('f-tso').value=d.time_rule?.soc_threshold??50;
    updateHints();
  }catch(e){}
}
async function saveCfg(){
  const msg=document.getElementById('smsg');
  const cfg={
    fronius:{host:document.getElementById('f-fh').value.trim(),poll_interval_seconds:+document.getElementById('f-pi').value},
    miner:{host:document.getElementById('f-mh').value.trim(),api_key:document.getElementById('f-ak').value.trim()},
    control:{soc_minimum:+document.getElementById('f-sm').value,soc_hysterese:+document.getElementById('f-sh').value,soc_freigabe:+document.getElementById('f-sf').value,soc_start_mining:+document.getElementById('f-sstart').value,netz_puffer_watt:+document.getElementById('f-np').value,akku_entlade_sperre_watt:+document.getElementById('f-abs').value,pv_schwelle_watt:+document.getElementById('f-pvs').value,hysterese_zyklen:+document.getElementById('f-hz').value},
    modes:{surplus_source:document.getElementById('f-ss').value},
    time_rule:{enabled:document.getElementById('f-te').value==='true',start:document.getElementById('f-ts').value,end:document.getElementById('f-tend').value,soc_threshold:+document.getElementById('f-tso').value}
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
async function setHashrate(){
  const msg=document.getElementById('hrmsg');
  const th=+document.getElementById('f-hr').value;
  if(!(th>=10&&th<=200)){msg.className='err';msg.textContent='Wert zwischen 10 und 200 TH/s';return;}
  msg.className='';msg.textContent='Wird gesendet...';
  try{
    const r=await fetch('/api/hashrate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({terahash_per_second:th})});
    const e=await r.json();
    if(r.ok){msg.className='ok';msg.innerHTML='&#10003; Gesetzt: '+(e.terahash_per_second??th)+' TH/s';}
    else{msg.className='err';msg.innerHTML='&#10007; '+(e.error||'Fehler');}
  }catch(e){msg.className='err';msg.innerHTML='&#10007; Netzwerkfehler';}
  fetchStatus();
  setTimeout(()=>msg.textContent='',5000);
}
async function doUpdate(){
  const btn=document.getElementById('btn-update');
  const msg=document.getElementById('umsg');
  btn.disabled=true;
  msg.className='';msg.textContent='Update wird heruntergeladen...';
  try{
    const r=await fetch('/api/update',{method:'POST'});
    const result=await r.json();
    if(!r.ok){msg.className='err';msg.textContent='Fehler: '+(result.error||'Update fehlgeschlagen');btn.disabled=false;return;}
    if(result.updated===false){
      msg.className='ok';msg.textContent='Bereits aktuell. Kein Neustart nötig.';
      btn.disabled=false;
      return;
    }
  }catch(e){msg.className='err';msg.textContent='Netzwerkfehler';btn.disabled=false;return;}
  msg.textContent='Service wird neu gestartet...';
  let tries=0;
  let stable=0;
  const started=Date.now();
  const poll=setInterval(async()=>{
    tries++;
    try{
      const r=await fetch('/api/status?update_poll='+Date.now(),{cache:'no-store'});
      if(!r.ok)throw new Error('not ready');
      await r.json();
      stable++;
      if(Date.now()-started<6000 || stable<2)return;
      clearInterval(poll);
      msg.className='ok';msg.textContent='Update erfolgreich. Seite wird neu geladen...';
      setTimeout(()=>location.reload(),2500);
    }catch(e){
      stable=0;
      if(tries>=60){clearInterval(poll);msg.className='err';msg.textContent='Service antwortet nicht. Bitte Logs prüfen.';btn.disabled=false;}
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

    def get_powerflow(self) -> dict | None:
        host = self._cfg.get()["fronius"]["host"]
        if not host:
            return None
        url = f"{self._base(host)}/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
        try:
            r = _http.get(url, timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
            site      = data["Body"]["Data"]["Site"]
            inverters = data["Body"]["Data"].get("Inverters", {})
            soc = self._first_soc(inverters)
            if soc is None:
                raise ValueError("Fronius response has no battery SOC")
            return {
                "p_grid": float(site.get("P_Grid") or 0.0),
                "p_pv":   float(site.get("P_PV")   or 0.0),
                "p_akku": float(site.get("P_Akku")  or 0.0),
                "p_load": float(site.get("P_Load")  or 0.0),
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
    target, autotuning or fan settings — the miner keeps whatever is configured
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

    def get_hashrate_target(self) -> float | None:
        """Read the hashrate target (TH/s) configured in Braiins OS."""
        r = self._request("GET", "/performance/mode")
        if r is None:
            return None
        try:
            th = (r.json().get("tunermode", {})
                  .get("target", {})
                  .get("hashratetarget", {})
                  .get("hashrate_target", {})
                  .get("terahash_per_second"))
            return float(th) if th is not None else None
        except Exception:
            return None

    def set_hashrate_target(self, terahash: float) -> bool:
        """Set the hashrate target (TH/s) in Braiins OS.

        This is the ONLY value pv-miner ever writes to the miner, and only
        when the user explicitly changes it in the web UI. Fans, autotuning
        mode and everything else stay untouched.
        """
        r = self._request("PUT", "/performance/hashrate-target",
                           json={"terahash_per_second": float(terahash)})
        ok = self._ok(r)
        if ok:
            self._log.info("hashrate target set to %.1f TH/s", terahash)
        else:
            self._log.warning("set_hashrate_target failed: %s",
                              r.text if r is not None else "no response")
        return ok

    def get_status(self) -> dict | None:
        """Return {power_watt, paused, hashrate_target_th} or None.

        ``paused`` is True whenever the miner is not actively mining
        (status != 2). ``power_watt`` is the real draw while mining, 0 while
        paused (the stale post-pause reading would otherwise distort the
        surplus calculation). ``hashrate_target_th`` is the target read live
        from the miner (may be None if unavailable).
        """
        rd = self._request("GET", "/miner/details")
        if rd is None:
            return None
        try:
            status = rd.json().get("status")
        except Exception as exc:
            self._log.warning("Braiins miner/details parse: %s", exc)
            return None

        hashrate_th = self.get_hashrate_target()

        if status != self._STATUS_MINING:
            return {"power_watt": 0, "paused": True,
                    "hashrate_target_th": hashrate_th}

        watt = 0
        rs = self._request("GET", "/miner/stats")
        if rs is not None:
            try:
                ps = rs.json().get("power_stats") or {}
                watt = int((ps.get("approximated_consumption") or {}).get("watt") or 0)
            except Exception:
                watt = 0
        return {"power_watt": watt, "paused": False,
                "hashrate_target_th": hashrate_th}


# ---------------------------------------------------------------------------
# Shared state (web UI ↔ control loop)
# ---------------------------------------------------------------------------

class StateStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._d: dict = {
            "soc": None, "p_grid": None, "p_pv": None, "p_akku": None,
            "p_load": None, "verfuegbar_w": None, "miner_power_w": None,
            "display_state": "unknown", "manual_override": "auto",
            "hashrate_target_th": None,
            "command_state": None,   # "ok" | "failed" | "unconfirmed" | None
            "command_msg": None,
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

        # The controller only ever holds two states: "pause" or "run".
        self._cur_action = "pause"
        self._pend_action: str | None = None
        self._pend_count  = 0
        self._soc_blocked = False
        self._fronius_err = 0
        self._braiins_err = 0

    @staticmethod
    def _minute_of_day(value: str) -> int | None:
        try:
            hour, minute = value.split(":", 1)
            h = int(hour)
            m = int(minute)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h * 60 + m
        except (AttributeError, ValueError):
            pass
        return None

    def _time_rule_active(self, cfg: dict, soc: float) -> bool:
        rule = cfg.get("time_rule", {})
        if not rule.get("enabled"):
            return False
        if soc > float(rule.get("soc_threshold", 0)):
            return False

        start = self._minute_of_day(rule.get("start", "18:00"))
        end = self._minute_of_day(rule.get("end", "07:00"))
        if start is None or end is None:
            self._log.warning("Invalid time_rule start/end: %r", rule)
            return False
        lt = time.localtime()
        now = lt.tm_hour * 60 + lt.tm_min
        if start == end:
            return True
        if start < end:
            return start <= now < end
        return now >= start or now < end

    def _decide(self, pf: dict, cfg: dict, cur_miner_w: int,
                hashrate_th: float | None) -> tuple[str, float]:
        """Return (action, verfuegbar_w).

        action is "pause" or "run". pv-miner only switches the miner on/off,
        the miner regulates its own consumption via its hashrate target.

        verfuegbar_w is the PV surplus "as if the miner were off" (the miner's
        current draw is added back so the calculation stays self-consistent).
        For the "battery_first" mode it is P_PV minus the configured PV
        threshold instead.
        """
        modes    = cfg.get("modes", {})
        ctrl     = cfg["control"]
        override = modes.get("manual_override", "auto")
        surplus_source = modes.get("surplus_source", "grid")
        puffer = ctrl["netz_puffer_watt"]

        # Estimated miner draw, derived from its hashrate target. Only used as
        # the START threshold while the miner is off; while it runs the real
        # consumption is used instead.
        need_w = round((hashrate_th or 96) * WATT_PER_TH)

        # ── Verfügbarer Überschuss + roher Überschuss-Wunsch ─────────────────
        if surplus_source == "battery_first":
            # Akku hat Vorrang: rein nach PV-Produktion. Der Akku lädt mit
            # voller Leistung; erst wenn die PV mehr als die Schwelle liefert
            # ist genug für Akku UND Miner da.
            pv_schwelle = ctrl.get("pv_schwelle_watt", 12000)
            verfuegbar  = pf["p_pv"] - pv_schwelle
            surplus_run = pf["p_pv"] >= pv_schwelle
        elif surplus_source == "pv_and_battery":
            # PV − reiner Hausverbrauch − Puffer.
            # abs(p_load) enthält den Miner bereits → cur_miner_w abziehen.
            p_load_house = abs(pf.get("p_load", 0.0)) - cur_miner_w
            verfuegbar   = pf["p_pv"] - p_load_house - puffer
            threshold    = cur_miner_w if (self._cur_action == "run" and cur_miner_w > 0) else need_w
            surplus_run  = verfuegbar >= threshold
        else:
            # grid: nur Netz-Einspeisung. Der laufende Miner-Zug wird
            # zurückaddiert (er frisst sonst die sichtbare Einspeisung auf).
            grid_export = abs(pf["p_grid"]) if pf["p_grid"] < 0 else 0.0
            verfuegbar  = grid_export + cur_miner_w - puffer
            threshold   = cur_miner_w if (self._cur_action == "run" and cur_miner_w > 0) else need_w
            surplus_run = verfuegbar >= threshold

        if override == "pause": return ("pause", verfuegbar)
        if override == "run":   return ("run",   verfuegbar)

        soc        = pf["soc"]
        soc_resume = ctrl["soc_minimum"] + ctrl["soc_hysterese"]

        # ── SOC protection (absolute minimum) ───────────────────────────────
        if self._soc_blocked:
            if soc < soc_resume:
                return ("pause", verfuegbar)
            self._soc_blocked = False

        if soc < ctrl["soc_minimum"]:
            self._soc_blocked = True
            return ("pause", verfuegbar)

        # ── Optional time-window battery guard ──────────────────────────────
        if self._time_rule_active(cfg, soc):
            return ("pause", verfuegbar)

        # ── SOC start threshold ("erst minen wenn Akku weit genug geladen") ─
        soc_start = ctrl.get("soc_start_mining", 0)
        if soc_start > 0 and soc < soc_start:
            return ("pause", verfuegbar)

        # ── SOC freigabe: Akku (fast) voll → laufen lassen, egal wie viel PV ─
        if soc >= ctrl["soc_freigabe"]:
            entlade_sperre = ctrl.get("akku_entlade_sperre_watt", 100)
            if pf["p_grid"] < -puffer or pf["p_akku"] <= entlade_sperre:
                return ("run", verfuegbar)

        return ("run" if surplus_run else "pause", verfuegbar)

    def _hysterese(self, action: str, cfg: dict) -> str:
        """Confirm a start/stop only after N consecutive cycles agree."""
        hyst_cy = cfg["control"]["hysterese_zyklen"]

        if action == self._cur_action:
            self._pend_action = None
            self._pend_count  = 0
            return self._cur_action

        if self._pend_action == action:
            self._pend_count += 1
        else:
            self._pend_action = action
            self._pend_count  = 1

        if self._pend_count >= hyst_cy:
            self._pend_action = None
            self._pend_count  = 0
            return action
        return self._cur_action

    @staticmethod
    def _display(action: str) -> str:
        return "mining" if action == "run" else "paused"

    def _verify(self, action: str) -> bool:
        """Poll the miner until it actually reached the requested state.

        Returns True once confirmed, False if it never confirms in time.
        """
        want_paused = (action == "pause")
        for _ in range(4):
            time.sleep(3)
            st = self._braiins.get_status()
            if st is not None and st["paused"] == want_paused:
                return True
        return False

    def _apply(self, action: str) -> None:
        """Issue pause/resume and verify the miner actually executed it."""
        if action == self._cur_action:
            return

        issued = self._braiins.pause() if action == "pause" else self._braiins.resume()
        verb   = "Pause" if action == "pause" else "Start"

        if not issued:
            self._braiins_err += 1
            self._state.update(command_state="failed",
                               command_msg=f"{verb}-Befehl wurde vom Miner nicht angenommen")
            self._log.warning("%s command rejected by miner", action)
            if self._braiins_err >= 5:
                self._log.critical("Braiins: %d consecutive failures", self._braiins_err)
            return

        # Command accepted — now confirm the miner really switched.
        if self._verify(action):
            self._cur_action  = action
            self._braiins_err = 0
            self._state.update(command_state="ok",
                               display_state=self._display(action),
                               command_msg=f"{verb} ausgeführt — vom Miner bestätigt")
            self._log.info("%s confirmed by miner", action)
        else:
            self._braiins_err += 1
            self._state.update(command_state="unconfirmed",
                               command_msg=f"{verb} gesendet, aber der Miner hat den Zustand "
                                           f"nicht bestätigt — Logs prüfen")
            self._log.warning("%s issued but miner did not confirm within timeout", action)
            if self._braiins_err >= 5:
                self._log.critical("Braiins: %d consecutive failures", self._braiins_err)

    def run_cycle(self) -> None:
        cfg        = self._cfg.get()
        override   = cfg.get("modes", {}).get("manual_override", "auto")
        miner_host = cfg.get("miner", {}).get("host")
        pf         = self._fronius.get_powerflow()

        # Query the miner first — gives actual consumption, run state and the
        # hashrate target currently configured on the miner.
        miner_st    = self._braiins.get_status() if miner_host else None
        cur_miner_w = miner_st["power_watt"] if miner_st else 0
        hashrate_th = miner_st.get("hashrate_target_th") if miner_st else None
        if miner_st is not None:
            # Sync our notion of state with reality (catches manual changes
            # made directly in Braiins OS).
            self._cur_action = "pause" if miner_st["paused"] else "run"

        # ── Fronius unreachable ─────────────────────────────────────────────
        if pf is None:
            self._fronius_err += 1
            self._log.warning("Fronius unreachable (streak: %d)", self._fronius_err)
            if override != "auto" and miner_host:
                self._apply("pause" if override == "pause" else "run")
            elif self._fronius_err >= 3 and self._cur_action != "pause":
                self._log.warning("Fronius 3x unreachable → pausing miner (safety)")
                self._apply("pause")
            self._state.update(
                verfuegbar_w=None,
                miner_power_w=miner_st["power_watt"] if miner_st else None,
                hashrate_target_th=hashrate_th,
                display_state=self._display(self._cur_action) if miner_host else "unknown",
                manual_override=override,
            )
            return

        self._fronius_err = 0
        soc = pf["soc"]
        self._log.debug("Fronius: p_grid=%.0fW p_pv=%.0fW p_akku=%.0fW p_load=%.0fW soc=%.1f%%",
                        pf["p_grid"], pf["p_pv"], pf["p_akku"], pf.get("p_load", 0), soc)

        if not miner_host:
            self._state.update(
                soc=soc, p_grid=pf["p_grid"], p_pv=pf["p_pv"],
                p_akku=pf["p_akku"], p_load=pf.get("p_load"),
                verfuegbar_w=None, miner_power_w=None, hashrate_target_th=None,
                display_state="unknown", manual_override=override,
            )
            self._log.info("[cycle] Fronius OK, Antminer IP noch nicht konfiguriert")
            return

        desired, verfuegbar = self._decide(pf, cfg, cur_miner_w, hashrate_th)
        # Manual overrides apply immediately — no start/stop hysteresis.
        action = desired if override != "auto" else self._hysterese(desired, cfg)

        self._state.update(
            soc=soc, p_grid=pf["p_grid"], p_pv=pf["p_pv"],
            p_akku=pf["p_akku"], p_load=pf.get("p_load"),
            verfuegbar_w=max(0.0, verfuegbar),
            miner_power_w=miner_st["power_watt"] if miner_st else None,
            hashrate_target_th=hashrate_th,
            display_state=self._display(action),
            manual_override=override,
        )

        if action == self._cur_action:
            self._log.info("[cycle] SOC=%.0f%% verfügbar=%.0fW → keine Änderung (%s)",
                           soc, verfuegbar, self._cur_action)
            return

        self._log.info("[cycle] SOC=%.0f%% verfügbar=%.0fW → %s",
                        soc, verfuegbar, "START" if action == "run" else "PAUSE")
        self._apply(action)


# ---------------------------------------------------------------------------
# Flask web app
# ---------------------------------------------------------------------------

def _is_time(value: str) -> bool:
    return PowerController._minute_of_day(value) is not None


def validate_config_patch(data: dict) -> str | None:
    ctrl = data.get("control", {})
    modes = data.get("modes", {})
    time_rule = data.get("time_rule", {})

    try:
        if not (0 <= int(ctrl.get("soc_minimum", 0)) <= 100):
            return "SOC Schutzgrenze muss zwischen 0 und 100 liegen"
        if not (0 <= int(ctrl.get("soc_hysterese", 0)) <= 50):
            return "SOC Hysterese muss zwischen 0 und 50 liegen"
        if not (0 <= int(ctrl.get("netz_puffer_watt", 0)) <= 5000):
            return "Netz-Puffer muss zwischen 0 und 5000 W liegen"
        if not (0 <= int(ctrl.get("akku_entlade_sperre_watt", 100)) <= 2000):
            return "Akku-Entlade-Sperre muss zwischen 0 und 2000 W liegen"
        if not (0 <= int(ctrl.get("pv_schwelle_watt", 0)) <= 100000):
            return "PV-Schwelle muss zwischen 0 und 100000 W liegen"
        for key in ("soc_freigabe", "soc_start_mining"):
            if not (0 <= int(ctrl.get(key, 0)) <= 100):
                return f"{key} muss zwischen 0 und 100 liegen"
        if int(ctrl.get("hysterese_zyklen", 1)) < 1:
            return "Hysterese-Zyklen müssen mindestens 1 sein"
    except (TypeError, ValueError):
        return "Numerische Konfigurationswerte sind ungültig"

    if modes.get("surplus_source", "grid") not in ("grid", "pv_and_battery", "battery_first"):
        return "Ungültige Überschuss-Quelle"

    if time_rule:
        if not _is_time(time_rule.get("start", "")) or not _is_time(time_rule.get("end", "")):
            return "Zeitfenster-Uhrzeiten müssen gültig sein"
        try:
            soc_threshold = int(time_rule.get("soc_threshold", 0))
        except (TypeError, ValueError):
            return "Zeitfenster-SOC ist ungültig"
        if not (0 <= soc_threshold <= 100):
            return "Zeitfenster-SOC muss zwischen 0 und 100 liegen"

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


def update_available() -> tuple[bool, str]:
    try:
        r = _http.get(
            _cache_busted_url(UPDATE_URL),
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            timeout=15,
        )
        r.raise_for_status()
        remote_hash = _sha256_bytes(r.content)
        local_hash = _sha256_file(Path(__file__))
        return remote_hash != local_hash, ""
    except Exception as exc:
        return False, str(exc)


def create_app(cfg_manager: ConfigManager, state: StateStore,
                braiins: "BraiinsAPI") -> Flask:
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
        update_bin = "/usr/local/bin/pv-miner-update"
        if not Path(update_bin).exists():
            return jsonify({"error": "pv-miner-update not found (only available in the LXC appliance)"}), 400
        available, error = update_available()
        if error:
            return jsonify({"error": f"Update-Prüfung fehlgeschlagen: {error}"}), 502
        if not available:
            logging.getLogger("main").info("Update requested, already current")
            return jsonify({"ok": True, "updated": False})

        def _run():
            time.sleep(2)  # let the HTTP response reach the browser first
            try:
                subprocess.run([update_bin], timeout=60)
            except Exception as exc:
                logging.getLogger("main").error("Update failed: %s", exc)

        threading.Thread(target=_run, daemon=True, name="updater").start()
        logging.getLogger("main").info("Update triggered via web UI")
        return jsonify({"ok": True, "updated": True})

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

    @app.route("/api/hashrate", methods=["POST"])
    def api_hashrate():
        data = request.get_json(silent=True) or {}
        try:
            th = float(data.get("terahash_per_second"))
        except (TypeError, ValueError):
            return jsonify({"error": "Ziel-Hashrate ist ungültig"}), 400
        if not (10 <= th <= 200):
            return jsonify({"error": "Ziel-Hashrate muss zwischen 10 und 200 TH/s liegen"}), 400
        if not cfg_manager.get().get("miner", {}).get("host"):
            return jsonify({"error": "Antminer-IP ist nicht konfiguriert"}), 400
        if not braiins.set_hashrate_target(th):
            return jsonify({"error": "Miner hat die Ziel-Hashrate nicht angenommen"}), 502
        # Reflect the new value immediately for the UI
        applied = braiins.get_hashrate_target()
        state.update(hashrate_target_th=applied if applied is not None else th)
        return jsonify({"ok": True, "terahash_per_second": applied if applied is not None else th})

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
    app     = create_app(cfg_manager, state, braiins)

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

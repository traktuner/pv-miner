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
from urllib.parse import urlparse

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
        "soc_start_mining": 0,       # 0 = immer erlaubt; z.B. 100 = erst wenn Akku voll
        "netz_puffer_watt": 200,
        "hysterese_watt":   300,
        "hysterese_zyklen": 2,
    },
    "modes": {
        # "grid"           — nur minen wenn P_Grid negativ (Einspeisung); Akku lädt zuerst
        # "pv_and_battery" — minen sobald PV > Hausverbrauch; Akku + Miner teilen Überschuss
        "surplus_source":     "grid",
        # "pause" | "minimum_power"
        "low_surplus_action": "pause",
        "soc_low_action":     "pause",
        # "auto" | "pause" | "minimum" | "maximum"
        "manual_override":    "auto",
    },
    "time_rule": {
        "enabled":       False,
        "start":         "18:00",
        "end":           "07:00",
        "soc_threshold": 50,
        # "pause" | "minimum_power"
        "action":        "pause",
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
    <div class="card"><div class="lbl">Netz (&#8722;=Einspeisung)</div><div class="val" id="v-pgrid">&#8212;</div></div>
    <div class="card"><div class="lbl">PV Produktion</div><div class="val" id="v-ppv">&#8212;</div></div>
    <div class="card"><div class="lbl">Hausverbrauch</div><div class="val" id="v-pload">&#8212;</div></div>
    <div class="card"><div class="lbl">Batterie (&#8722;=l&#228;dt)</div><div class="val" id="v-pakku">&#8212;</div></div>
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
          <label>Braiins API Key (leer = kein Auth)</label>
          <input id="f-ak" type="password" placeholder="optional">
          <div class="hint">Unter Braiins OS &#8594; Settings &#8594; API Access generieren. Leer lassen wenn kein Auth konfiguriert.</div>
        </div>
        <div class="field">
          <label>Abfrage-Intervall (Sekunden)</label>
          <input id="f-pi" type="number" min="10" max="300" oninput="updateHints()">
          <div class="hint" id="h-pi">Alle 30 Sekunden wird der Fronius abgefragt und der Miner nachgeregelt.</div>
        </div>
      </div>
    </div>

    <div class="fsec">
      <h3>Leistungsgrenzen</h3>
      <div class="fg">
        <div class="field">
          <label>Minimale Miner-Leistung (W)</label>
          <input id="f-mn" type="number" min="100" max="2000" oninput="updateHints()">
          <div class="hint" id="h-mn">Unter diesem Wert macht Mining keinen Sinn &#8212; der Miner wird stattdessen pausiert.</div>
        </div>
        <div class="field">
          <label>Maximale Miner-Leistung (W)</label>
          <input id="f-mx" type="number" min="500" max="4000" oninput="updateHints()">
          <div class="hint" id="h-mx">Oberes Limit. Sollte etwas unter dem Hardware-Maximum laut Braiins Autotuning liegen.</div>
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
          <label>Mindest&#228;nderung Power-Target (W)</label>
          <input id="f-hw" type="number" min="0" max="1000" oninput="updateHints()">
          <div class="hint" id="h-hw">Kleine PV-Schwankungen werden ignoriert. Erst wenn die berechnete Leistung um mehr als diesen Wert abweicht wird der Miner nachgeregelt.</div>
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
            <option value="grid">Nur was ins Netz eingespeist wird (Akku l&#228;dt immer zuerst)</option>
            <option value="pv_and_battery">PV-Produktion minus Hausverbrauch (Miner und Akku teilen gleichzeitig)</option>
          </select>
          <div class="hint" id="h-ss"></div>
        </div>
        <div class="field">
          <label>Was soll passieren wenn kein ausreichender &#220;berschuss vorhanden ist?</label>
          <select id="f-ls" onchange="updateHints()">
            <option value="pause">Miner pausieren (komplett aus)</option>
            <option value="minimum_power">Miner l&#228;uft weiter auf Minimalbetrieb</option>
          </select>
          <div class="hint" id="h-ls"></div>
        </div>
        <div class="field">
          <label>Was soll passieren wenn der Akku unter die Schutzgrenze f&#228;llt?</label>
          <select id="f-sl" onchange="updateHints()">
            <option value="pause">Miner pausieren (Akku sch&#252;tzen)</option>
            <option value="minimum_power">Miner l&#228;uft weiter auf Minimalbetrieb</option>
          </select>
          <div class="hint" id="h-sl"></div>
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
          <label>Wenn SOC bei oder unter (%)</label>
          <input id="f-tso" type="number" min="0" max="100" oninput="updateHints()">
          <div class="hint" id="h-tso"></div>
        </div>
        <div class="field">
          <label>Dann</label>
          <select id="f-ta" onchange="updateHints()">
            <option value="pause">Miner pausieren</option>
            <option value="minimum_power">Auf Minimalbetrieb reduzieren</option>
          </select>
          <div class="hint" id="h-ta"></div>
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

function updateHints(){
  const sm=v('f-sm',15),sh=v('f-sh',5),sf=v('f-sf',95),ss2=v('f-sstart',0);
  const np=v('f-np',200),hw=v('f-hw',300),hz=v('f-hz',2),pi=v('f-pi',30);
  const mn=v('f-mn',500),mx=v('f-mx',3400);
  const ss=s('f-ss'),ls=s('f-ls'),sl=s('f-sl');
  const te=s('f-te')==='true',ts=s('f-ts')||'18:00',tend=s('f-tend')||'07:00',tso=v('f-tso',50),ta=s('f-ta');

  hint('h-pi',`Alle <em>${pi} Sekunden</em> wird der Wechselrichter abgefragt und der Miner bei Bedarf nachgeregelt.`);
  hint('h-mn',`Unter <em>${mn} W</em> lohnt sich Mining nicht mehr &#8212; bei weniger Überschuss wird pausiert (oder Minimalbetrieb aktiviert, je nach Einstellung unten).`);
  hint('h-mx',`Der Miner wird maximal auf <em>${mx} W</em> gesetzt. Diesen Wert aus dem Braiins OS Autotuning-Ergebnis ablesen.`);
  hint('h-sm',`Fällt der Akku unter <em>${sm}%</em>, wird der Miner sofort gestoppt &#8212; egal wie viel PV vorhanden ist.`);
  hint('h-sh',`Nach einem SOC-Stopp startet der Miner erst wieder bei <em>${sm+sh}%</em> (${sm}% + ${sh}% Hysterese). Verhindert schnelles Ein-/Ausschalten wenn der Akku genau an der Grenze ist.`);
  hint('h-sf',`Ab <em>${sf}% SOC</em> läuft der Miner auf voller Leistung (<em>${mx} W</em>) &#8212; egal wie viel PV gerade produziert wird. Der Akku ist voll und der Strom muss irgendwo hin.`);

  if(ss2===0){
    hint('h-sstart','Mining ist erlaubt sobald PV-Überschuss vorhanden ist und der SOC über der Schutzgrenze liegt. Normaler Betrieb.');
  } else if(ss2>=100){
    hint('h-sstart','<em>Batterie zuerst:</em> Der Miner startet erst wenn der Akku auf 100% geladen ist. Tagsüber lädt der Akku durch, abends/nachts wird dann mit dem gespeicherten Strom gemint.');
  } else {
    hint('h-sstart',`Der Miner startet erst wenn der Akku <em>${ss2}%</em> erreicht hat. Darunter lädt der Akku zuerst. Erst danach wird der Überschuss für Mining verwendet.`);
  }

  hint('h-np',`<em>${np} W</em> Sicherheitspuffer &#8212; der Miner bekommt immer ${np} W weniger als berechnet damit kein Strom vom Netz bezogen wird. Kleiner Wert = mehr Mining, größerer Wert = sicherer kein Netzbezug.`);
  hint('h-hw',`Kleine Schwankungen werden ignoriert: Erst wenn die berechnete Zielleistung um mehr als <em>${hw} W</em> abweicht wird der Miner tatsächlich nachgeregelt. Verhindert ständiges Regulieren bei bewölktem Himmel.`);

  const delaySec=hz*pi;
  hint('h-hz',`Start und Stopp werden erst ausgeführt wenn die Bedingung <em>${hz} Messungen hintereinander</em> erfüllt ist (= ${delaySec} Sekunden). Eine kurze Wolke die nach ${pi}s wieder weg ist löst damit keinen Stopp aus.`);

  if(ss==='grid'){
    hint('h-ss','<em>Akku hat immer Vorrang.</em> Der Miner bekommt nur was tatsächlich ins Netz eingespeist wird &#8212; erst wenn der Akku voll ist (oder keine Kapazität mehr aufnimmt) steigt der Netz-Überschuss und der Miner startet.<br><br>Beispiel: 5 kW PV &#8226; 2 kW Haus &#8226; Akku lädt 2 kW &#8594; nur 1 kW Einspeisung &#8594; Miner läuft mit ~'+(Math.max(0,1000-np))+' W.');
  } else {
    const avail=Math.max(0,5000-2000-np);
    hint('h-ss','<em>Miner und Akku teilen gleichzeitig.</em> Der Miner startet sobald PV mehr produziert als der Haushalt verbraucht &#8212; egal ob der Akku noch leer ist. Was der Miner nicht braucht, lädt den Akku.<br><br>Beispiel: 5 kW PV &#8226; 2 kW Haus &#8594; <em>'+avail+' W für den Miner.</em> Der Akku lädt mit dem Rest (oder gar nicht, falls der Miner alles zieht). Vorteil: Miner startet früher am Morgen. Nachteil: Akku lädt langsamer.');
  }

  if(ls==='pause'){
    hint('h-ls','Wenn der Überschuss wegfällt (Wolke, Abend): <em>Miner wird komplett gestoppt.</em> Sobald wieder genug PV da ist, startet er wieder. Energiesparend &#8212; kein unnötiger Verbrauch.');
  } else {
    hint('h-ls','Wenn der Überschuss zu gering ist: <em>Miner läuft trotzdem weiter mit '+mn+' W.</em> Der fehlende Strom wird aus dem Akku oder dem Netz bezogen. Sinnvoll wenn der Miner warm bleiben soll oder nachts mit Batteriestrom gemint werden soll.',true);
  }

  if(sl==='pause'){
    hint('h-sl','Wenn der Akku unter '+sm+'% fällt: <em>Miner wird gestoppt</em> um den Akku zu schonen. Empfohlene Einstellung &#8212; schützt die Batterie vor Tiefentladung.');
  } else {
    hint('h-sl','Wenn der Akku unter '+sm+'% fällt: <em>Miner läuft trotzdem weiter mit '+mn+' W.</em> &#9888;&#65039; Der Akku wird dabei weiter entladen. Nur sinnvoll wenn du eine tiefe Entladung bewusst akzeptierst.',true);
  }

  if(te){
    hint('h-te',`Aktiv: Zwischen <em>${ts}</em> und <em>${tend}</em> greift diese Regel, wenn der Akku zu niedrig ist.`);
    hint('h-tso',`Im Zeitfenster greift die Regel bei <em>${tso}% SOC oder weniger</em>.`);
    hint('h-ta',ta==='pause'
      ? 'Bei niedrigem SOC im Zeitfenster: <em>Miner wird pausiert.</em>'
      : `Bei niedrigem SOC im Zeitfenster: <em>Miner wird auf ${mn} W reduziert.</em>`, ta!=='pause');
  } else {
    hint('h-te','Aus: Es gilt nur die normale PV-/SOC-Regelung.');
    hint('h-tso','Wird nur verwendet, wenn die Zeitfenster-Regel aktiv ist.');
    hint('h-ta','Wird nur verwendet, wenn die Zeitfenster-Regel aktiv ist.');
  }
}

async function fetchStatus(){
  try{
    const d=await(await fetch('/api/status')).json();
    const fw=v=>v!=null?Math.round(v)+' W':'&#8212;';
    document.getElementById('v-soc').textContent=d.soc!=null?d.soc.toFixed(1)+'%':'&#8212;';
    document.getElementById('v-verfuegbar').innerHTML=fw(d.verfuegbar_w);
    document.getElementById('v-pgrid').innerHTML=fw(d.p_grid);
    document.getElementById('v-ppv').innerHTML=fw(d.p_pv);
    document.getElementById('v-pload').innerHTML=d.p_load!=null?Math.round(Math.abs(d.p_load))+' W':'&#8212;';
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
    document.getElementById('f-sstart').value=d.control?.soc_start_mining??0;
    document.getElementById('f-np').value=d.control?.netz_puffer_watt??200;
    document.getElementById('f-hw').value=d.control?.hysterese_watt??300;
    document.getElementById('f-hz').value=d.control?.hysterese_zyklen??2;
    document.getElementById('f-ss').value=d.modes?.surplus_source||'grid';
    document.getElementById('f-ls').value=d.modes?.low_surplus_action||'pause';
    document.getElementById('f-sl').value=d.modes?.soc_low_action||'pause';
    document.getElementById('f-te').value=String(!!d.time_rule?.enabled);
    document.getElementById('f-ts').value=d.time_rule?.start||'18:00';
    document.getElementById('f-tend').value=d.time_rule?.end||'07:00';
    document.getElementById('f-tso').value=d.time_rule?.soc_threshold??50;
    document.getElementById('f-ta').value=d.time_rule?.action||'pause';
    updateHints();
  }catch(e){}
}
async function saveCfg(){
  const msg=document.getElementById('smsg');
  const cfg={
    fronius:{host:document.getElementById('f-fh').value.trim(),poll_interval_seconds:+document.getElementById('f-pi').value},
    miner:{host:document.getElementById('f-mh').value.trim(),api_key:document.getElementById('f-ak').value.trim(),min_power_watt:+document.getElementById('f-mn').value,max_power_watt:+document.getElementById('f-mx').value},
    control:{soc_minimum:+document.getElementById('f-sm').value,soc_hysterese:+document.getElementById('f-sh').value,soc_freigabe:+document.getElementById('f-sf').value,soc_start_mining:+document.getElementById('f-sstart').value,netz_puffer_watt:+document.getElementById('f-np').value,hysterese_watt:+document.getElementById('f-hw').value,hysterese_zyklen:+document.getElementById('f-hz').value},
    modes:{surplus_source:document.getElementById('f-ss').value,low_surplus_action:document.getElementById('f-ls').value,soc_low_action:document.getElementById('f-sl').value},
    time_rule:{enabled:document.getElementById('f-te').value==='true',start:document.getElementById('f-ts').value,end:document.getElementById('f-tend').value,soc_threshold:+document.getElementById('f-tso').value,action:document.getElementById('f-ta').value}
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
  msg.className='';msg.textContent='&#9203; Update wird heruntergeladen…';
  try{
    const r=await fetch('/api/update',{method:'POST'});
    if(!r.ok){const e=await r.json();msg.className='err';msg.textContent='&#10007; '+(e.error||'Fehler');btn.disabled=false;return;}
  }catch(e){msg.className='err';msg.textContent='&#10007; Netzwerkfehler';btn.disabled=false;return;}
  msg.textContent='&#9203; Service wird neu gestartet…';
  let tries=0;
  const poll=setInterval(async()=>{
    tries++;
    try{
      await fetch('/api/status');
      clearInterval(poll);
      msg.className='ok';msg.textContent='&#10003; Update erfolgreich — Seite wird neu geladen…';
      setTimeout(()=>location.reload(),1500);
    }catch(e){
      if(tries>=30){clearInterval(poll);msg.className='err';msg.textContent='&#10007; Service antwortet nicht — prüfe Logs';btn.disabled=false;}
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
# Braiins OS API
# ---------------------------------------------------------------------------

class BraiinsAPI:
    def __init__(self, cfg: ConfigManager, timeout: int = 10):
        self._cfg     = cfg
        self._timeout = timeout

    def _base(self) -> str:
        host = self._cfg.get()["miner"]["host"].strip()
        if not host:
            return ""
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        parsed = urlparse(host)
        netloc = parsed.netloc or parsed.path
        scheme = parsed.scheme or "http"
        return f"{scheme}://{netloc}/api/v1"

    def _hdrs(self) -> dict:
        h = {"Content-Type": "application/json"}
        key = self._cfg.get()["miner"].get("api_key", "")
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def set_power_target(self, watt: int) -> bool:
        try:
            _http.put(
                f"{self._base()}/performance/power-target",
                json={"watt": watt},
                headers=self._hdrs(), timeout=self._timeout,
            ).raise_for_status()
            logging.getLogger("api").debug("set_power_target(%dW) OK", watt)
            return True
        except Exception as exc:
            logging.getLogger("api").warning("set_power_target: %s", exc)
            return False

    def pause(self) -> bool:
        try:
            _http.put(f"{self._base()}/actions/pause",
                      headers=self._hdrs(), timeout=self._timeout).raise_for_status()
            logging.getLogger("api").debug("pause OK")
            return True
        except Exception as exc:
            logging.getLogger("api").warning("pause: %s", exc)
            return False

    def resume(self) -> bool:
        try:
            _http.put(f"{self._base()}/actions/resume",
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
            ps = d.get("power_stats", {}) or {}
            ms = d.get("miner_stats", {}) or {}
            real = ms.get("real_hashrate", {}) or {}
            last_hashrate = real.get("last_5s") or real.get("last_15s") or {}
            power = (ps.get("approximated_consumption") or {}).get("watt", 0)
            return {
                "power_watt":   int(power or 0),
                "paused":       False,
                "hashrate_ths": float(last_hashrate.get("gigahash_per_second", 0.0)) / 1000.0,
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
            "p_load": None, "verfuegbar_w": None, "miner_power_w": None,
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

    def _decide(self, pf: dict, cfg: dict) -> tuple[str, int, float]:
        """Return (action, target_watt, verfuegbar_w)."""
        modes    = cfg.get("modes", {})
        ctrl     = cfg["control"]
        miner    = cfg["miner"]
        override = modes.get("manual_override", "auto")
        low_act  = modes.get("low_surplus_action", "pause")
        soc_act  = modes.get("soc_low_action",     "pause")
        surplus_source = modes.get("surplus_source", "grid")
        min_w    = miner["min_power_watt"]
        max_w    = miner["max_power_watt"]

        if override == "pause":   return ("pause", 0, 0.0)
        if override == "minimum": return ("mine",  min_w, float(min_w))
        if override == "maximum": return ("mine",  max_w, float(max_w))

        soc        = pf["soc"]
        soc_resume = ctrl["soc_minimum"] + ctrl["soc_hysterese"]

        # ── SOC protection (absolute minimum) ───────────────────────────────
        if self._soc_blocked:
            if soc < soc_resume:
                return ("mine", min_w, 0.0) if soc_act == "minimum_power" else ("pause", 0, 0.0)
            self._soc_blocked = False

        if soc < ctrl["soc_minimum"]:
            self._soc_blocked = True
            return ("mine", min_w, 0.0) if soc_act == "minimum_power" else ("pause", 0, 0.0)

        # ── Optional time-window battery guard ──────────────────────────────
        if self._time_rule_active(cfg, soc):
            action = cfg.get("time_rule", {}).get("action", "pause")
            return ("mine", min_w, 0.0) if action == "minimum_power" else ("pause", 0, 0.0)

        # ── SOC start threshold (Case 2: "erst minen wenn Akku voll") ───────
        soc_start = ctrl.get("soc_start_mining", 0)
        if soc_start > 0 and soc < soc_start:
            return ("mine", min_w, 0.0) if low_act == "minimum_power" else ("pause", 0, 0.0)

        # ── SOC freigabe: Akku voll → volle Power ───────────────────────────
        if soc >= ctrl["soc_freigabe"]:
            return ("mine", max_w, float(max_w))

        # ── Verfügbare Leistung berechnen ────────────────────────────────────
        puffer = ctrl["netz_puffer_watt"]

        if surplus_source == "pv_and_battery":
            # Case 1: PV − Hausverbrauch − Puffer + aktueller Miner-Zug
            # = was der Miner haben kann ohne Netzbezug zu erzeugen
            # (abs(p_load) enthält den Miner bereits → aufaddieren für Netto-Hausverbrauch)
            p_load_house = abs(pf.get("p_load", 0.0)) - self._cur_target
            verfuegbar = pf["p_pv"] - p_load_house - puffer
        else:
            # Default: nur Netz-Einspeisung (Akku lädt zuerst)
            verfuegbar = (abs(pf["p_grid"]) if pf["p_grid"] < 0 else 0.0) - puffer

        if verfuegbar < min_w:
            return ("mine", min_w, verfuegbar) if low_act == "minimum_power" else ("pause", 0, verfuegbar)

        target = max(min_w, min(int(verfuegbar), max_w))
        return ("mine", target, verfuegbar)

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
        soc = pf["soc"]

        self._log.debug("Fronius: p_grid=%.0fW p_pv=%.0fW p_akku=%.0fW p_load=%.0fW soc=%.1f%%",
                        pf["p_grid"], pf["p_pv"], pf["p_akku"], pf.get("p_load", 0), soc)

        desired_a, desired_t, verfuegbar = self._decide(pf, cfg)
        action, target                   = self._hysterese(desired_a, desired_t, cfg)
        display                          = self._display(action, target, cfg)

        miner_st = self._braiins.get_status()
        self._state.update(
            soc=soc, p_grid=pf["p_grid"], p_pv=pf["p_pv"],
            p_akku=pf["p_akku"], p_load=pf.get("p_load"),
            verfuegbar_w=max(0.0, verfuegbar),
            miner_power_w=miner_st["power_watt"] if miner_st else None,
            display_state=display,
            manual_override=cfg.get("modes", {}).get("manual_override", "auto"),
        )

        no_change = (action == self._cur_action and
                     (action == "pause" or target == self._cur_target))
        if no_change:
            self._log.info("[cycle] SOC=%.0f%% verfügbar=%.0fW → keine Änderung",
                           soc, verfuegbar)
            return

        if action == "pause":
            self._log.info("[cycle] SOC=%.0f%% verfügbar=%.0fW → PAUSE", soc, verfuegbar)
        elif display == "minimum":
            self._log.info("[cycle] SOC=%.0f%% verfügbar=%.0fW → MINIMAL %dW", soc, verfuegbar, target)
        elif display == "maximum":
            self._log.info("[cycle] SOC=%.0f%% → MAX_POWER %dW (SOC Freigabe)", soc, target)
        else:
            self._log.info("[cycle] SOC=%.0f%% verfügbar=%.0fW → target=%dW", soc, verfuegbar, target)

        self._apply(action, target)


# ---------------------------------------------------------------------------
# Flask web app
# ---------------------------------------------------------------------------

def _is_time(value: str) -> bool:
    return PowerController._minute_of_day(value) is not None


def validate_config_patch(data: dict) -> str | None:
    miner = data.get("miner", {})
    ctrl = data.get("control", {})
    modes = data.get("modes", {})
    time_rule = data.get("time_rule", {})

    try:
        min_w = int(miner.get("min_power_watt", 0))
        max_w = int(miner.get("max_power_watt", 0))
        if not (100 <= min_w <= max_w <= 10000):
            return "Miner-Leistungsgrenzen sind ungültig"
        if not (0 <= int(ctrl.get("soc_minimum", 0)) <= 100):
            return "SOC Schutzgrenze muss zwischen 0 und 100 liegen"
        if not (0 <= int(ctrl.get("soc_hysterese", 0)) <= 50):
            return "SOC Hysterese muss zwischen 0 und 50 liegen"
        for key in ("soc_freigabe", "soc_start_mining"):
            if not (0 <= int(ctrl.get(key, 0)) <= 100):
                return f"{key} muss zwischen 0 und 100 liegen"
        if int(ctrl.get("hysterese_zyklen", 1)) < 1:
            return "Hysterese-Zyklen müssen mindestens 1 sein"
    except (TypeError, ValueError):
        return "Numerische Konfigurationswerte sind ungültig"

    if modes.get("surplus_source", "grid") not in ("grid", "pv_and_battery"):
        return "Ungültige Überschuss-Quelle"
    if modes.get("low_surplus_action", "pause") not in ("pause", "minimum_power"):
        return "Ungültige Aktion bei wenig Überschuss"
    if modes.get("soc_low_action", "pause") not in ("pause", "minimum_power"):
        return "Ungültige Aktion bei niedrigem SOC"

    if time_rule:
        if time_rule.get("action", "pause") not in ("pause", "minimum_power"):
            return "Ungültige Zeitfenster-Aktion"
        if not _is_time(time_rule.get("start", "")) or not _is_time(time_rule.get("end", "")):
            return "Zeitfenster-Uhrzeiten müssen gültig sein"
        try:
            soc_threshold = int(time_rule.get("soc_threshold", 0))
        except (TypeError, ValueError):
            return "Zeitfenster-SOC ist ungültig"
        if not (0 <= soc_threshold <= 100):
            return "Zeitfenster-SOC muss zwischen 0 und 100 liegen"

    return None


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

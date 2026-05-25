from __future__ import annotations

import json as _json
import os
import pathlib
import threading
import time as _time
import math as _math
from datetime import datetime
from typing import Any, Optional

from flask import Flask, jsonify, render_template_string, request

from .client import EpluconClient
from .errors import EpluconError
from .models import Zone
from .schedule import compute_con_duration_minutes_from_next_start, split_minutes_to_hours_minutes
from .settings import env_or_config, load_config_dict

# ---------------------------------------------------------------------------
# Zone settings persistence (per-zone default duration etc.)
# ---------------------------------------------------------------------------

_SETTINGS_PATH = pathlib.Path(__file__).resolve().parent.parent / "zone_settings.json"


def _load_zone_settings() -> dict[str, Any]:
    try:
        with open(_SETTINGS_PATH, "r") as f:
            return _json.load(f)
    except Exception:
        return {"zone_defaults": {}}


def _save_zone_settings(data: dict[str, Any]) -> None:
    with open(_SETTINGS_PATH, "w") as f:
        _json.dump(data, f, indent=2)


def _get_zone_default_minutes(zone_api_id: int) -> int:
    settings = _load_zone_settings()
    return int(settings.get("zone_defaults", {}).get(str(zone_api_id), {}).get("default_minutes", 240))


def _set_zone_default_minutes(zone_api_id: int, minutes: int) -> None:
    settings = _load_zone_settings()
    zd = settings.setdefault("zone_defaults", {})
    zd.setdefault(str(zone_api_id), {})["default_minutes"] = int(minutes)
    _save_zone_settings(settings)


# ---------------------------------------------------------------------------
# Pending writes
# ---------------------------------------------------------------------------

_pending_lock = threading.Lock()
_pending_writes: dict[int, dict[str, Any]] = {}

WRITE_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""
<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Eplucon Thermostaat</title>
<style>
*{box-sizing:border-box}
:root{--bg:#eef4ef;--card:#fff;--line:#d7e2dc;--text:#173126;--muted:#5d7369;
--ok:#0f7a56;--ok2:#0b5e42;--warn:#9a4f1c;--err-bg:#fdecea;--err-ln:#f2c3bb;
--ok-bg:#e6f6ee;--ok-ln:#b8e2cd;--chip:#edf4f0;--heat:#e25822;--cool:#2f80c3}
body{margin:0;font-family:"Segoe UI",Helvetica,Arial,sans-serif;color:var(--text);
  background:radial-gradient(900px 300px at 50% -15%,#d6ece1 0%,transparent 65%),var(--bg)}
.wrap{max-width:1080px;margin:0 auto;padding:14px}
h1{margin:0;font-size:clamp(18px,4.5vw,24px)}
.zones{display:grid;gap:12px;grid-template-columns:1fr}
@media(min-width:720px){.zones{grid-template-columns:repeat(auto-fill,minmax(340px,1fr))}}

.zone{border:1px solid var(--line);background:var(--card);border-radius:14px;
  padding:12px;box-shadow:0 3px 8px rgba(16,44,34,.06)}
.z-top{display:flex;gap:8px;justify-content:space-between;align-items:center;margin-bottom:6px}
.z-name{margin:0;font-size:18px}
.z-mode{font-size:11px;padding:3px 7px;border-radius:999px;background:var(--chip);
  border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.z-status{border-radius:8px;padding:6px 10px;margin-bottom:6px;font-size:13px;display:none}
.z-status.pending{display:block;background:#fef9e7;border:1px solid #f0d96c;color:#7a6200}
.z-status.done{display:block;background:var(--ok-bg);border:1px solid var(--ok-ln);color:#0a5e3e}
.z-status.fail{display:block;background:var(--err-bg);border:1px solid var(--err-ln);color:#6f221a}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.spinner{display:inline-block;animation:pulse 1.2s ease-in-out infinite}

.z-stats{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px}
.stat{background:#f8fbf9;border:1px solid var(--line);border-radius:10px;padding:8px;text-align:center}
.stat .lbl{display:block;color:var(--muted);font-size:11px;margin-bottom:2px}
.stat .val{font-size:22px;font-weight:700}
.stat .icon{font-size:20px}
.icon-heat{color:var(--heat)}
.icon-cool{color:var(--cool)}
.icon-off{color:var(--muted)}

.z-ctrl{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px}
.btn{border:none;border-radius:10px;min-height:42px;color:#fff;background:var(--ok);
  padding:0 10px;cursor:pointer;font:inherit}
.btn:hover{background:var(--ok2)}
.btn.minor{background:#2f8f6f}
.btn.warn{background:var(--warn)}
.btn.sm{min-height:34px;font-size:13px}
.btn:disabled{opacity:.5;cursor:not-allowed}
.input,.select{font:inherit;border-radius:10px;border:1px solid var(--line);
  min-height:42px;width:100%;padding:0 10px;background:#fff;color:var(--text)}

.z-dur{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px}
.z-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:6px}
.z-foot{font-size:12px;color:var(--muted);border-top:1px dashed var(--line);padding-top:6px;
  display:flex;gap:8px;justify-content:space-between;align-items:center}

/* Schedule modal */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:100;
  justify-content:center;align-items:flex-start;padding:30px 10px;overflow-y:auto}
.modal-bg.open{display:flex}
.modal{background:#fff;border-radius:14px;max-width:600px;width:100%;padding:16px;
  max-height:90vh;overflow-y:auto}
.modal h2{margin:0 0 12px}
.modal .close{float:right;background:none;border:none;font-size:22px;cursor:pointer;padding:4px 8px}
.sched-profile{border:1px solid var(--line);border-radius:10px;padding:10px;margin-bottom:10px}
.sched-profile h3{margin:0 0 8px;font-size:14px}
.sched-days{display:flex;gap:4px;margin-bottom:8px}
.sched-days label{display:flex;align-items:center;gap:2px;font-size:12px;
  padding:4px 6px;border:1px solid var(--line);border-radius:6px;cursor:pointer}
.sched-days input:checked+span{font-weight:700;color:var(--ok)}
.sched-interval{display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:6px;align-items:center;margin-bottom:4px}
.sched-interval input{font:inherit;border:1px solid var(--line);border-radius:6px;padding:4px 6px;width:100%}
.sched-interval .del{background:none;border:none;color:var(--warn);cursor:pointer;font-size:16px;padding:4px}
.setback-row{display:grid;grid-template-columns:auto 1fr;gap:8px;align-items:center;margin-top:6px}
.setback-row label{font-size:12px;color:var(--muted)}
.setback-row input{width:70px}
</style>
</head>
<body>
<div class="wrap">
<h1 style="margin-bottom:14px">Eplucon Thermostaat</h1>
<div class="zones" id="zones"></div>
</div>

<!-- Schedule modal (one, reused) -->
<div class="modal-bg" id="sched-modal-bg">
  <div class="modal" id="sched-modal">
    <button class="close" onclick="closeScheduleModal()">&times;</button>
    <h2 id="sched-title">Schema</h2>
    <div id="sched-body">Laden...</div>
    <div style="margin-top:12px;display:flex;gap:8px">
      <button class="btn" onclick="saveSchedule(true)">Opslaan + activeren</button>
      <button class="btn minor" onclick="saveSchedule(false)">Alleen opslaan</button>
      <button class="btn warn" onclick="closeScheduleModal()">Annuleren</button>
    </div>
  </div>
</div>

<script>
var DEBOUNCE_MS = 2000;
var zones = [];            // current zone data from server
var zoneDefaults = {};     // zone_api_id -> {default_minutes}
var debounceTimers = {};   // zone_api_id -> timer
var pendingTemps = {};     // zone_api_id -> target temp (accumulated from +/- clicks)
var polling = {};
var currentSchedZone = null;
var currentSchedForms = null;
var currentSchedIndex = 0;

// ---- Init ----
function init() {
  loadZones();
  setInterval(loadZones, 30000); // refresh every 30s
}

function loadZones() {
  fetch('/api/zones').then(r=>r.json()).then(data => {
    zones = data.zones || [];
    zoneDefaults = data.zone_defaults || {};
    renderZones();
    checkPending();
  }).catch(()=>{});
}

// ---- Render ----
function renderZones() {
  var c = document.getElementById('zones');
  var html = '';
  zones.forEach(z => {
    var relay = (z.relay_state === 'on');
    var algo = z.algorithm || 'heating';
    var iconHtml = '';
    if (relay) {
      if (algo === 'cooling') iconHtml = '<span class="icon-cool" title="Koelen">&#x1F9CA;</span>';
      else iconHtml = '<span class="icon-heat" title="Verwarmen">&#x1F525;</span>';
    } else {
      iconHtml = '<span class="icon-off" title="Uit">&mdash;</span>';
    }

    var defMin = (zoneDefaults[z.zone_api_id] || {}).default_minutes || 240;
    var defH = Math.floor(defMin / 60);
    var defM = defMin % 60;

    // Use pending temp if user is adjusting
    var displayTemp = (pendingTemps[z.zone_api_id] !== undefined) ? pendingTemps[z.zone_api_id] : z.set_temperature_c;

    html += '<div class="zone" data-zid="'+z.zone_api_id+'">' +
      '<div class="z-top">' +
        '<h2 class="z-name">'+esc(z.name)+'</h2>' +
        '<div class="z-mode" data-field="mode">'+esc(z.mode)+'</div>' +
      '</div>' +
      '<div class="z-status" id="status-'+z.zone_api_id+'"></div>' +
      '<div class="z-stats">' +
        '<div class="stat"><span class="lbl">Huidig</span><span class="val" data-field="current">'+
          fmt(z.current_temperature_c)+'&deg;</span></div>' +
        '<div class="stat"><span class="lbl">Set</span><span class="val" data-field="set">'+
          fmt(displayTemp)+'&deg;</span></div>' +
        '<div class="stat"><span class="lbl">Status</span><span class="val icon">'+iconHtml+'</span></div>' +
      '</div>' +
      '<div class="z-ctrl">' +
        '<button class="btn minor" onclick="adj('+z.zone_api_id+',-0.5)">-0.5</button>' +
        '<input class="input" type="number" step="0.1" id="temp-'+z.zone_api_id+'" value="'+fmt(displayTemp)+'">' +
        '<button class="btn minor" onclick="adj('+z.zone_api_id+',0.5)">+0.5</button>' +
      '</div>' +
      '<div class="z-dur">' +
        '<select class="select" id="durmode-'+z.zone_api_id+'">' +
          '<option value="fixed">Duur: vast</option>' +
          '<option value="con">Duur: con</option>' +
        '</select>' +
        '<input class="input" type="number" step="1" min="0" max="23" id="hours-'+z.zone_api_id+'" placeholder="uren ('+defH+')">' +
        '<input class="input" type="number" step="1" min="0" max="59" id="mins-'+z.zone_api_id+'" placeholder="min ('+(defM<10?'0':'')+defM+')">' +
      '</div>' +
      '<div class="z-actions">' +
        '<button class="btn" onclick="applyTemp('+z.zone_api_id+')">Toepassen</button>' +
        '<button class="btn warn" onclick="applyConstant('+z.zone_api_id+')">Vast (geen timer)</button>' +
      '</div>' +
      '<div class="z-foot">' +
        '<span>Schema: <a href="#" onclick="openSchedule('+z.zone_api_id+');return false">bewerken</a></span>' +
        '<span>Default: <a href="#" onclick="editDefault('+z.zone_api_id+');return false">'+defH+'u'+(defM<10?'0':'')+defM+'</a></span>' +
      '</div>' +
    '</div>';
  });
  c.innerHTML = html;
}

function fmt(v) { return v !== null && v !== undefined ? parseFloat(v).toFixed(1) : '?'; }
function esc(s) { var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// ---- Debounced temperature adjustment ----
function adj(zid, delta) {
  var inp = document.getElementById('temp-'+zid);
  var cur = parseFloat(inp.value) || 0;
  var nv = Math.round((cur + delta) * 10) / 10;
  inp.value = nv.toFixed(1);
  pendingTemps[zid] = nv;

  // Update displayed set temp immediately
  var card = document.querySelector('[data-zid="'+zid+'"]');
  if (card) {
    var setEl = card.querySelector('[data-field="set"]');
    if (setEl) setEl.innerHTML = nv.toFixed(1) + '&deg;';
  }

  // Debounce: reset timer, send after 2s of inactivity
  if (debounceTimers[zid]) clearTimeout(debounceTimers[zid]);
  debounceTimers[zid] = setTimeout(function() {
    sendTemp(zid, nv);
    delete pendingTemps[zid];
    delete debounceTimers[zid];
  }, DEBOUNCE_MS);
}

// ---- Send temperature ----
function sendTemp(zid, temp) {
  var durmode = document.getElementById('durmode-'+zid).value;
  var hours = document.getElementById('hours-'+zid).value;
  var mins = document.getElementById('mins-'+zid).value;
  var body = {zone_api_id: zid, temp: temp, duration_mode: durmode};
  if (hours) body.hours = parseInt(hours);
  if (mins) body.minutes = parseInt(mins);
  postAction('/api/set_temp', body, zid);
}

function applyTemp(zid) {
  var temp = parseFloat(document.getElementById('temp-'+zid).value);
  if (isNaN(temp)) return;
  // Cancel any pending debounce
  if (debounceTimers[zid]) { clearTimeout(debounceTimers[zid]); delete debounceTimers[zid]; }
  delete pendingTemps[zid];
  sendTemp(zid, temp);
}

function applyConstant(zid) {
  var temp = parseFloat(document.getElementById('temp-'+zid).value);
  if (isNaN(temp)) return;
  if (debounceTimers[zid]) { clearTimeout(debounceTimers[zid]); delete debounceTimers[zid]; }
  delete pendingTemps[zid];
  postAction('/api/set_temp', {zone_api_id: zid, temp: temp, duration_mode: 'constant'}, zid);
}

function postAction(url, body, zid) {
  showStatus(zid, 'pending', 'Verstuurd...');
  fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
    .then(r=>r.json()).then(d => {
      if (d.ok) startPolling(zid);
      else showStatus(zid, 'fail', d.error || 'Fout');
    }).catch(e => showStatus(zid, 'fail', ''+e));
}

// ---- Status + polling ----
function showStatus(zid, status, msg) {
  var el = document.getElementById('status-'+zid);
  if (!el) return;
  el.className = 'z-status';
  if (status==='pending') { el.classList.add('pending'); el.innerHTML='<span class="spinner">&#9696;</span> '+esc(msg); }
  else if (status==='done') { el.classList.add('done'); el.textContent=msg; setTimeout(()=>{el.style.display='none'},8000); }
  else if (status==='fail') { el.classList.add('fail'); el.textContent=msg; }
  else el.style.display='none';
}

function checkPending() {
  fetch('/api/pending').then(r=>r.json()).then(data => {
    var zs = data.zones||{};
    for (var zid in zs) {
      showStatus(zid, zs[zid].status, zs[zid].message);
      if (zs[zid].status==='pending') startPolling(parseInt(zid));
    }
  }).catch(()=>{});
}

function startPolling(zid) { if (polling[zid]) return; polling[zid]=true; pollZone(zid); }
function pollZone(zid) {
  fetch('/api/pending/'+zid).then(r=>r.json()).then(info => {
    showStatus(zid, info.status, info.message);
    if (info.status==='pending') setTimeout(()=>pollZone(zid), 3000);
    else { polling[zid]=false; loadZones(); }
  }).catch(()=>{polling[zid]=false;});
}

// ---- Default duration editor ----
function editDefault(zid) {
  var z = zones.find(x=>x.zone_api_id===zid);
  var cur = (zoneDefaults[zid]||{}).default_minutes || 240;
  var input = prompt('Default duur voor '+(z?z.name:'zone')+' (in minuten):', cur);
  if (input === null) return;
  var mins = parseInt(input);
  if (isNaN(mins) || mins < 1) return;
  fetch('/api/zone_settings', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({zone_api_id:zid, default_minutes:mins})})
    .then(()=>loadZones()).catch(()=>{});
}

// ---- Schedule modal ----
function openSchedule(zid) {
  currentSchedZone = zid;
  currentSchedIndex = 0; // reset to first tab (local)
  var z = zones.find(x=>x.zone_api_id===zid);
  document.getElementById('sched-title').textContent = 'Schema: '+(z?z.name:'');
  document.getElementById('sched-body').innerHTML = 'Laden...';
  document.getElementById('sched-modal-bg').classList.add('open');
  fetch('/api/programs/'+zid).then(r=>r.json()).then(data => {
    currentSchedForms = data.programs || [];
    // Sort: local (-17) first, then global (0..4)
    currentSchedForms.sort((a,b) => a.index - b.index);
    renderScheduleModal();
  }).catch(e => { document.getElementById('sched-body').textContent = 'Fout: '+e; });
}

function closeScheduleModal() {
  document.getElementById('sched-modal-bg').classList.remove('open');
  currentSchedZone = null;
  currentSchedForms = null;
}

function renderScheduleModal() {
  if (!currentSchedForms || !currentSchedForms.length) {
    document.getElementById('sched-body').textContent = 'Geen programma\'s gevonden.';
    return;
  }

  // Find the active schedule for this zone
  var z = zones.find(x=>x.zone_api_id===currentSchedZone);
  var activeIdx = z ? z.schedule_index : -1;

  // Build tabs: "Lokaal" for -17, "Gedeeld 1" etc for 0+
  var html = '<div style="display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap">';
  currentSchedForms.forEach((p,i) => {
    var label;
    if (p.index < 0) {
      label = 'Lokaal schema';
    } else {
      label = 'Gedeeld ' + (p.index + 1);
      if (p.schedule_name) label += ': ' + p.schedule_name.replace('Voor geselecteerde ruimte(s) ','');
    }
    // Mark active schedule
    var isActive = (p.index === activeIdx) || (p.index < 0 && activeIdx === -1);
    var style = (i===currentSchedIndex) ? 'background:var(--ok);color:#fff' : 'background:var(--chip)';
    html += '<button class="btn sm" style="'+style+'" onclick="switchSchedTab('+i+')">' +
      esc(label) + (isActive ? ' *' : '') + '</button>';
  });
  html += '</div>';

  var p = currentSchedForms[currentSchedIndex];
  if (!p) { document.getElementById('sched-body').innerHTML = html; return; }

  // Info text
  if (p.index < 0) {
    html += '<p style="font-size:12px;color:var(--muted);margin:0 0 10px">' +
      'Lokaal schema: geldt alleen voor deze zone.' +
      '</p>';
  } else {
    // Show which zones are linked
    var linkedZones = [];
    (p.inputs||[]).forEach(inp => {
      if (inp.name === 'setInZoneId[]') {
        var parts = inp.value.split('-');
        if (parts.length === 2) {
          var iid = parseInt(parts[0]);
          var match = zones.find(z => z.zone_internal_id === iid);
          if (match) linkedZones.push(match.name);
        }
      }
    });
    html += '<p style="font-size:12px;color:var(--muted);margin:0 0 10px">' +
      'Gedeeld schema: wijzigingen gelden voor alle gekoppelde zones.' +
      (linkedZones.length ? '<br>Gekoppeld aan: <strong>' + linkedZones.map(esc).join(', ') + '</strong>' : '') +
      '</p>';
  }

  // Activate button
  var isCurrentlyActive = (p.index === activeIdx) || (p.index < 0 && activeIdx === -1);
  if (!isCurrentlyActive) {
    html += '<button class="btn sm" style="margin-bottom:10px" onclick="activateProgram('+p.index+')">Dit schema activeren</button> ';
  } else {
    html += '<span style="font-size:12px;color:var(--ok);margin-bottom:10px;display:inline-block">Actief *</span> ';
  }

  var fields = {};
  (p.inputs||[]).forEach(inp => {
    if (!fields[inp.name]) fields[inp.name] = [];
    fields[inp.name].push(inp);
  });

  var dayNames = ['Ma','Di','Wo','Do','Vr','Za','Zo'];
  for (var pi=0; pi<2; pi++) {
    var prefix = 'p'+pi;
    var profileLabel = pi===0 ? 'Profiel 1 (bv. werkdagen)' : 'Profiel 2 (bv. weekend)';
    html += '<div class="sched-profile"><h3>'+profileLabel+'</h3>';
    html += '<div class="sched-days">';
    for (var d=0; d<7; d++) {
      var key = prefix+'Days['+d+']';
      var vals = fields[key]||[];
      var checked = vals.length>0 && vals[0].value==='1';
      html += '<label><input type="checkbox" data-sched-field="'+key+'"'+(checked?' checked':'')+
        '><span>'+dayNames[d]+'</span></label>';
    }
    html += '</div>';
    // Intervals
    var starts = (fields[prefix+'Intervals[start][]']||[]).map(x=>x.value);
    var ends = (fields[prefix+'Intervals[end][]']||[]).map(x=>x.value);
    var temps = (fields[prefix+'Intervals[temp][]']||[]).map(x=>x.value);
    var cnt = Math.max(starts.length, ends.length, temps.length);
    html += '<div id="intervals-'+pi+'">';
    if (cnt === 0) {
      html += '<p style="font-size:12px;color:var(--muted);margin:4px 0">Geen intervallen ingesteld, alleen setback-temperatuur actief.</p>';
    }
    for (var iv=0; iv<cnt; iv++) {
      html += schedIntervalRow(pi, starts[iv]||'', ends[iv]||'', temps[iv]||'');
    }
    html += '</div>';
    html += '<button class="btn sm" style="margin-top:4px" onclick="addInterval('+pi+')">+ Interval</button>';
    // Setback
    var sbKey = prefix+'SetbackTemp';
    var sbVal = (fields[sbKey]||[{value:''}])[0].value;
    html += '<div class="setback-row"><label>Setback temp (buiten intervallen):</label>' +
      '<input class="input" type="number" step="1" data-sched-field="'+sbKey+'" value="'+sbVal+'" style="width:70px"> C</div>';
    html += '</div>';
  }
  document.getElementById('sched-body').innerHTML = html;
}

function activateProgram(progIndex) {
  if (!currentSchedZone) return;
  showStatus(currentSchedZone, 'pending', 'Schema activeren...');
  fetch('/api/activate_program', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({zone_api_id: currentSchedZone, program_index: progIndex})})
    .then(r=>r.json()).then(d => {
      if (d.ok) { showStatus(currentSchedZone,'done','Schema geactiveerd'); closeScheduleModal(); startPolling(currentSchedZone); }
      else showStatus(currentSchedZone,'fail', d.error||'Fout');
    }).catch(e => showStatus(currentSchedZone,'fail',''+e));
}

function schedIntervalRow(pi, start, end, temp) {
  return '<div class="sched-interval">' +
    '<input type="time" data-interval="'+pi+'" data-iv-field="start" value="'+(start||'')+'">' +
    '<input type="time" data-interval="'+pi+'" data-iv-field="end" value="'+(end||'')+'">' +
    '<input type="number" step="1" data-interval="'+pi+'" data-iv-field="temp" value="'+(temp||'')+'" placeholder="C">' +
    '<button class="del" onclick="this.parentElement.remove()" title="Verwijder">&times;</button>' +
  '</div>';
}

function addInterval(pi) {
  var cont = document.getElementById('intervals-'+pi);
  cont.insertAdjacentHTML('beforeend', schedIntervalRow(pi, '', '', ''));
}

function switchSchedTab(i) { currentSchedIndex = i; renderScheduleModal(); }

function saveSchedule(activate) {
  if (!currentSchedForms || !currentSchedZone) {
    console.warn('saveSchedule: no forms or zone');
    return;
  }
  var p = currentSchedForms[currentSchedIndex];
  if (!p) {
    console.warn('saveSchedule: no program at index', currentSchedIndex);
    return;
  }

  // Capture zone id before modal closes
  var zid = currentSchedZone;

  // Collect form data
  var overrides = {};

  // Days
  for (var pi=0; pi<2; pi++) {
    for (var d=0; d<7; d++) {
      var key = 'p'+pi+'Days['+d+']';
      var cb = document.querySelector('[data-sched-field="'+key+'"]');
      overrides[key] = cb && cb.checked ? '1' : '0';
    }
    // Setback
    var sbKey = 'p'+pi+'SetbackTemp';
    var sbEl = document.querySelector('[data-sched-field="'+sbKey+'"]');
    if (sbEl) overrides[sbKey] = sbEl.value;

    // Intervals - always send arrays (empty = delete all intervals)
    // Also generate index[] field (sequential 0,1,2...) required by portal
    var rows = document.querySelectorAll('[data-interval="'+pi+'"]');
    var fields = {};
    rows.forEach(el => {
      var f = el.getAttribute('data-iv-field');
      if (!fields[f]) fields[f] = [];
      fields[f].push(el.value);
    });
    var intervalCount = (fields.start || []).length;
    var indices = [];
    for (var idx=0; idx<intervalCount; idx++) indices.push(''+idx);
    overrides['p'+pi+'Intervals[index][]'] = indices;
    overrides['p'+pi+'Intervals[start][]'] = fields.start || [];
    overrides['p'+pi+'Intervals[end][]'] = fields.end || [];
    overrides['p'+pi+'Intervals[temp][]'] = fields.temp || [];
  }

  // Close modal first so status bar is visible on the zone card
  closeScheduleModal();

  // Show status on the zone card (now visible)
  showStatus(zid, 'pending', activate ? 'Schema opslaan + activeren...' : 'Schema opslaan...');

  console.log('saveSchedule: posting for zone', zid, 'program', p.index);
  fetch('/api/save_schedule', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({zone_api_id: zid, program_index: p.index, overrides: overrides, activate: activate})})
    .then(r=>r.json()).then(d => {
      console.log('saveSchedule response:', d);
      if (d.ok) { startPolling(zid); }
      else showStatus(zid, 'fail', d.error||'Fout');
    }).catch(e => {
      console.error('saveSchedule error:', e);
      showStatus(zid, 'fail', ''+e);
    });
}

init();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Flask app + API
# ---------------------------------------------------------------------------

def _create_client() -> EpluconClient:
    config, _ = load_config_dict(os.getenv("EPLUCON_CONFIG"))
    username = env_or_config("EPLUCON_USERNAME", config, "auth", "username", default=None, cast=str)
    password = env_or_config("EPLUCON_PASSWORD", config, "auth", "password", default=None, cast=str)
    api_key = env_or_config("EPLUCON_API_KEY", config, "auth", "api_key", default=None, cast=str)
    if not username or not password or not api_key:
        raise RuntimeError("Ontbrekende auth. Vul [auth] in eplucon.toml.")
    return EpluconClient(
        username=username, password=password, api_key=api_key,
        base_url=env_or_config("EPLUCON_BASE_URL", config, "connection", "base_url",
                               default="https://portaal.eplucon.nl", cast=str),
        request_timeout=env_or_config("EPLUCON_REQUEST_TIMEOUT", config, "connection", "request_timeout", default=40, cast=int),
        settle_seconds=env_or_config("EPLUCON_SETTLE_SECONDS", config, "connection", "settle_seconds", default=15, cast=int),
        poll_interval_seconds=env_or_config("EPLUCON_POLL_INTERVAL_SECONDS", config, "connection", "poll_interval_seconds", default=5, cast=int),
        verify_tls=not env_or_config("EPLUCON_INSECURE", config, "connection", "insecure", default=False, cast=bool),
    )


app = Flask(__name__)


def _client() -> EpluconClient:
    c = app.config.get("EPLUCON_CLIENT")
    if c is None:
        c = _create_client()
        app.config["EPLUCON_CLIENT"] = c
    return c


def _zone_to_dict(z: Zone) -> dict:
    raw = z.raw_data or {}
    flags = raw.get("zone", {}).get("flags", {})
    return {
        "zone_api_id": z.zone_api_id,
        "zone_internal_id": z.zone_internal_id,
        "mode_id": z.mode_id,
        "name": z.name,
        "mode": z.mode,
        "set_temperature_c": z.set_temperature_c,
        "current_temperature_c": z.current_temperature_c,
        "schedule_index": z.schedule_index,
        "const_temp_time": z.const_temp_time,
        "module_name": z.module_name,
        "relay_state": flags.get("relayState", "off"),
        "algorithm": flags.get("algorithm", "heating"),
    }


@app.get("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.get("/api/zones")
def api_zones():
    try:
        zones = _client().get_zones()
        settings = _load_zone_settings()
        zd = settings.get("zone_defaults", {})
        return jsonify({
            "zones": [_zone_to_dict(z) for z in zones],
            "zone_defaults": {int(k): v for k, v in zd.items()},
        })
    except Exception as e:
        return jsonify({"zones": [], "zone_defaults": {}, "error": str(e)})


@app.post("/api/set_temp")
def api_set_temp():
    try:
        data = request.get_json(force=True)
        zid = int(data["zone_api_id"])
        temp = float(data["temp"])
        dur_mode = data.get("duration_mode", "fixed")
        client = _client()
        zone = client.get_zone(zone_api_id=zid)

        if dur_mode == "constant":
            desc = f"{zone.name} vast op {temp:.1f} C"
            _fire_write(client, zone, "constant", temp=temp, description=desc)
        elif dur_mode == "con":
            mins = client.plan_con_duration_minutes(zone, default_minutes=_get_zone_default_minutes(zid), max_minutes=480)
            h, m = split_minutes_to_hours_minutes(mins)
            desc = f"{zone.name} naar {temp:.1f} C voor {h}u{m:02d} (con)"
            _fire_write(client, zone, "time_limit", temp=temp, total_minutes=mins, description=desc)
        else:
            hours = data.get("hours")
            minutes = data.get("minutes")
            if hours is None and minutes is None:
                total = _get_zone_default_minutes(zid)
            else:
                total = int((hours or 0)) * 60 + int((minutes or 0))
                if total <= 0:
                    total = _get_zone_default_minutes(zid)
            # Cap at next schedule start so we don't overshoot the schema
            total = _cap_at_next_schedule(client, zone, total)
            h, m = split_minutes_to_hours_minutes(total)
            desc = f"{zone.name} naar {temp:.1f} C voor {h}u{m:02d}"
            _fire_write(client, zone, "time_limit", temp=temp, total_minutes=total, description=desc)

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/zone_settings")
def api_zone_settings():
    try:
        data = request.get_json(force=True)
        zid = int(data["zone_api_id"])
        mins = int(data["default_minutes"])
        _set_zone_default_minutes(zid, mins)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/activate_program")
def api_activate_program():
    try:
        data = request.get_json(force=True)
        zid = int(data["zone_api_id"])
        idx = int(data["program_index"])
        client = _client()
        zone = client.get_zone(zone_api_id=zid)

        with _pending_lock:
            _pending_writes[zid] = {"status": "pending", "message": "Schema activeren...", "ts": _time.time()}

        def _do():
            with WRITE_LOCK:
                try:
                    client.activate_program(zone, idx, wait=False)
                    with _pending_lock:
                        _pending_writes[zid] = {"status": "ok", "message": "Schema geactiveerd", "ts": _time.time()}
                except Exception as exc:
                    with _pending_lock:
                        _pending_writes[zid] = {"status": "error", "message": f"Fout: {exc}", "ts": _time.time()}

        threading.Thread(target=_do, daemon=True).start()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/programs/<int:zone_api_id>")
def api_programs(zone_api_id: int):
    try:
        client = _client()
        zone = client.get_zone(zone_api_id=zone_api_id)
        forms = client.get_program_forms(zone)
        result = []
        for f in forms:
            result.append({
                "index": f.index,
                "schedule_id": f.schedule_id,
                "schedule_name": f.schedule_name,
                "mode_id": f.mode_id,
                "zone_id": f.zone_id,
                "inputs": [{"name": i.name, "value": i.value, "type": i.input_type, "checked": i.checked} for i in f.inputs],
            })
        return jsonify({"programs": result})
    except Exception as e:
        return jsonify({"programs": [], "error": str(e)})


@app.post("/api/save_schedule")
def api_save_schedule():
    try:
        data = request.get_json(force=True)
        zid = int(data["zone_api_id"])
        idx = int(data["program_index"])
        overrides = data.get("overrides", {})
        activate = data.get("activate", True)
        client = _client()
        zone = client.get_zone(zone_api_id=zid)

        with _pending_lock:
            _pending_writes[zid] = {"status": "pending", "message": "Schema opslaan + activeren...", "ts": _time.time()}

        def _do():
            if WRITE_LOCK.locked():
                with _pending_lock:
                    _pending_writes[zid] = {"status": "pending", "message": "Wachten op eerdere actie...", "ts": _time.time()}

            with WRITE_LOCK:
                try:
                    with _pending_lock:
                        _pending_writes[zid] = {"status": "pending", "message": "Schema opslaan...", "ts": _time.time()}

                    client.submit_program_form(zone, idx, overrides=overrides, wait=False)

                    if activate:
                        with _pending_lock:
                            _pending_writes[zid] = {"status": "pending", "message": "Schema activeren...", "ts": _time.time()}
                        # Wait for the save to settle before activating
                        client.wait_for_zone_idle(zid, timeout_seconds=60, poll_interval_seconds=3)
                        # Only do the mode-switch, don't re-submit the form
                        # (activate_program would re-submit with empty overrides, wiping our changes)
                        fresh_zone = client.get_zone(zone_api_id=zid)
                        if idx >= 0:
                            client._write_set_constant_temp(
                                zone=fresh_zone,
                                mode="globalSchedule",
                                constant_temp_deci=fresh_zone.set_temperature_deci,
                                active_schedule=idx,
                                hours=None,
                                minutes=None,
                            )
                        else:
                            client._write_set_constant_temp(
                                zone=fresh_zone,
                                mode="localSchedule",
                                constant_temp_deci=fresh_zone.set_temperature_deci,
                                active_schedule=idx,
                                hours=None,
                                minutes=None,
                            )

                    # Wait for portal to finish processing
                    with _pending_lock:
                        _pending_writes[zid] = {"status": "pending", "message": "Updaten van de parameters...", "ts": _time.time()}

                    idle = client.wait_for_zone_idle(zid, timeout_seconds=90, poll_interval_seconds=5)
                    msg = "Schema opgeslagen en geactiveerd" if activate else "Schema opgeslagen"
                    if not idle:
                        msg += " (timeout)"

                    with _pending_lock:
                        _pending_writes[zid] = {"status": "ok", "message": msg, "ts": _time.time()}

                except Exception as exc:
                    with _pending_lock:
                        _pending_writes[zid] = {"status": "error", "message": f"Fout: {exc}", "ts": _time.time()}

        threading.Thread(target=_do, daemon=True).start()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/pending")
def api_pending_all():
    result: dict[str, Any] = {}
    with _pending_lock:
        snapshot = dict(_pending_writes)
    for zid, info in snapshot.items():
        result[str(zid)] = {"status": info["status"], "message": info["message"]}
    return jsonify({"zones": result})


@app.get("/api/pending/<int:zone_api_id>")
def api_pending_zone(zone_api_id: int):
    client = _client()
    with _pending_lock:
        info = _pending_writes.get(zone_api_id)
    if info is None:
        return jsonify({"status": "none", "message": ""})
    entry: dict[str, Any] = {"status": info["status"], "message": info["message"]}
    if info["status"] in ("ok", "error"):
        try:
            z = client.get_zone(zone_api_id=zone_api_id)
            entry["set_temperature_c"] = z.set_temperature_c
            entry["current_temperature_c"] = z.current_temperature_c
            entry["mode"] = z.mode
        except Exception:
            pass
        if _time.time() - info["ts"] > 120:
            with _pending_lock:
                _pending_writes.pop(zone_api_id, None)
    return jsonify(entry)


def _cap_at_next_schedule(client: EpluconClient, zone: Zone, requested_minutes: int) -> int:
    """Cap the requested duration at the next schedule start time.

    If the next schedule start is sooner than the requested duration,
    shorten the duration to end at the schedule start.
    """
    try:
        next_start = client.get_next_schedule_start_for_zone(zone)
        if next_start is None:
            return requested_minutes
        now = datetime.now().astimezone()
        remaining_seconds = (next_start - now).total_seconds()
        if remaining_seconds <= 0:
            return requested_minutes
        remaining_minutes = int(_math.ceil(remaining_seconds / 60.0))
        return max(1, min(requested_minutes, remaining_minutes))
    except Exception:
        return requested_minutes


def _fire_write(client, zone, mode, *, temp, total_minutes=0, description=""):
    zid = zone.zone_api_id
    with _pending_lock:
        _pending_writes[zid] = {"status": "pending", "message": f"Verstuurd: {description}", "ts": _time.time()}

    def _do():
        if WRITE_LOCK.locked():
            with _pending_lock:
                _pending_writes[zid] = {"status": "pending", "message": f"Wachten op eerdere actie... ({description})", "ts": _time.time()}

        with WRITE_LOCK:
            try:
                with _pending_lock:
                    _pending_writes[zid] = {"status": "pending", "message": f"Verwerken: {description}", "ts": _time.time()}

                if mode == "constant":
                    client.set_constant_temperature(zone, temp, wait=False)
                elif mode == "time_limit":
                    client.set_temperature_for_minutes(zone, temp, total_minutes, wait=False)

                # Wait for portal to finish processing (same mechanism as Eplucon portal)
                with _pending_lock:
                    _pending_writes[zid] = {"status": "pending", "message": f"Updaten van de parameters...", "ts": _time.time()}

                idle = client.wait_for_zone_idle(zid, timeout_seconds=90, poll_interval_seconds=5)

                if idle:
                    with _pending_lock:
                        _pending_writes[zid] = {"status": "ok", "message": f"Verwerkt: {description}", "ts": _time.time()}
                else:
                    with _pending_lock:
                        _pending_writes[zid] = {"status": "ok", "message": f"Verstuurd (timeout): {description}", "ts": _time.time()}

            except Exception as exc:
                with _pending_lock:
                    _pending_writes[zid] = {"status": "error", "message": f"Fout: {exc}", "ts": _time.time()}

    threading.Thread(target=_do, daemon=True).start()


def main() -> None:
    config, _ = load_config_dict(os.getenv("EPLUCON_CONFIG"))
    host = env_or_config("EPLUCON_WEBAPP_HOST", config, "webapp", "host", default="0.0.0.0", cast=str)
    port = env_or_config("EPLUCON_WEBAPP_PORT", config, "webapp", "port", default=8080, cast=int)
    debug = env_or_config("EPLUCON_WEBAPP_DEBUG", config, "webapp", "debug", default=False, cast=bool)
    server = env_or_config("EPLUCON_WEBAPP_SERVER", config, "webapp", "server", default="auto", cast=str).strip().lower()

    if server in ("auto", "waitress") and not debug:
        try:
            from waitress import serve
            threads = env_or_config("EPLUCON_WEBAPP_THREADS", config, "webapp", "threads", default=6, cast=int)
            serve(app, host=host, port=port, threads=threads)
            return
        except ImportError:
            if server == "waitress":
                raise RuntimeError("Waitress niet gevonden.")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Network Tester (web version)

Run:   python network_tester.py
It starts a small server on your own PC and opens a "Network Tester" page in
your browser. Click GO on the page to see:
  - the Wi-Fi you are connected to (name, signal, band, link speed)
  - ping / jitter / packet loss (real ping command, like the SAS script)
  - download and upload speed
  - your internet provider and public IP

Only the Python standard library is used - nothing to install.
Press Ctrl+C in the terminal to stop it.
"""

import json
import platform
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

IS_WINDOWS = platform.system().lower() == "windows"
IS_MAC = platform.system().lower() == "darwin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0

HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-:]{0,252}$")


def run(cmd, timeout=8):
    """Run a command (no shell) and return its text output."""
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="replace",
        timeout=timeout, creationflags=NO_WINDOW,
    ).stdout


# ---------------------------------------------------------------- ping ----
def ping_once(target):
    """Send ONE ping and return its round-trip time (traffic generation +
    measurement, same idea as the SAS script)."""
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", "1000", target]
    else:
        cmd = ["ping", "-c", "1", target]
    try:
        output = run(cmd, timeout=5)
    except FileNotFoundError:
        return {"ok": False, "error": "The ping command was not found on this PC."}
    except subprocess.TimeoutExpired:
        return {"ok": False}
    match = re.search(r"time[=<]\s*([\d.]+)\s*ms", output)
    if match:
        return {"ok": True, "ms": float(match.group(1))}
    return {"ok": False}


# ---------------------------------------------------------------- wi-fi ---
def parse_netsh(text):
    """Turn `netsh wlan show interfaces` output into a list of dicts."""
    interfaces, current = [], {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        # A new "Name" line starts the next adapter (the header line
        # "There is 1 interface on the system:" must not count as one).
        if key == "Name" and "Name" in current:
            interfaces.append(current)
            current = {}
        current[key] = value
    if current:
        interfaces.append(current)
    return interfaces


def to_int(value):
    m = re.search(r"\d+", value or "")
    return int(m.group()) if m else None


def to_float(value):
    m = re.search(r"\d+(?:\.\d+)?", value or "")
    return float(m.group()) if m else None


def wifi_windows():
    text = run(["netsh", "wlan", "show", "interfaces"])
    if "location" in text.lower() and "ssid" not in text.lower():
        return {"supported": True, "connected": False, "needs_location": True}
    interfaces = parse_netsh(text)
    if not interfaces:
        return {"supported": True, "connected": False}
    active = next((i for i in interfaces if i.get("State", "").lower() == "connected"), None)
    if not active:
        return {"supported": True, "connected": False}
    signal = to_int(active.get("Signal"))
    return {
        "supported": True,
        "connected": True,
        "ssid": active.get("SSID") or "",
        "bssid": active.get("AP BSSID") or active.get("BSSID") or "",
        "signal": signal,
        "dbm": round(signal / 2 - 100) if signal is not None else None,
        "band": active.get("Band") or "",
        "channel": active.get("Channel") or "",
        "radio": active.get("Radio type") or "",
        "auth": active.get("Authentication") or "",
        "rx": to_float(active.get("Receive rate (Mbps)")),
        "tx": to_float(active.get("Transmit rate (Mbps)")),
        "adapter": active.get("Description") or active.get("Name") or "",
    }


def wifi_mac():
    airport = ("/System/Library/PrivateFrameworks/Apple80211.framework/"
               "Versions/Current/Resources/airport")
    try:
        text = run([airport, "-I"])
    except FileNotFoundError:
        return {"supported": False}
    fields = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    if not fields.get("SSID"):
        return {"supported": True, "connected": False}
    rssi = to_int(fields.get("agrCtlRSSI", "").replace("-", ""))
    dbm = -rssi if rssi is not None else None
    signal = max(0, min(100, 2 * (dbm + 100))) if dbm is not None else None
    return {
        "supported": True, "connected": True, "ssid": fields["SSID"],
        "bssid": fields.get("BSSID", ""), "signal": signal, "dbm": dbm,
        "band": "", "channel": fields.get("channel", ""), "radio": "",
        "auth": fields.get("link auth", ""), "rx": to_float(fields.get("lastTxRate")),
        "tx": to_float(fields.get("lastTxRate")), "adapter": "",
    }


def wifi_linux():
    try:
        text = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,CHAN,RATE,SECURITY", "dev", "wifi"])
    except FileNotFoundError:
        return {"supported": False}
    for line in text.splitlines():
        parts = re.split(r"(?<!\\):", line)
        if len(parts) >= 6 and parts[0].strip() == "*":
            signal = to_int(parts[2])
            rate = to_float(parts[4])
            return {
                "supported": True, "connected": True,
                "ssid": parts[1].replace("\\:", ":"), "bssid": "", "signal": signal,
                "dbm": round(signal / 2 - 100) if signal is not None else None,
                "band": "", "channel": parts[3], "radio": "", "auth": parts[5],
                "rx": rate, "tx": rate, "adapter": "",
            }
    return {"supported": True, "connected": False}


def wifi_info():
    try:
        if IS_WINDOWS:
            return wifi_windows()
        if IS_MAC:
            return wifi_mac()
        return wifi_linux()
    except Exception as exc:  # never crash the page because of Wi-Fi lookup
        return {"supported": False, "error": str(exc)}


# ------------------------------------------------------------------ web ---
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Network Tester</title>
<style>
  :root{
    --bg:#0b0f17; --panel:#121927; --panel2:#182234; --line:#243047;
    --text:#eaf0fa; --muted:#8a98b3; --down:#22d3ee; --up:#a78bfa;
    --good:#34d399; --warn:#fbbf24; --bad:#f87171;
  }
  @media (prefers-color-scheme: light){
    :root{ --bg:#f3f6fb; --panel:#ffffff; --panel2:#eef2f9; --line:#d9e1ee;
           --text:#111a2b; --muted:#5b6b87; --down:#0891b2; --up:#7c3aed;
           --good:#059669; --warn:#b45309; --bad:#dc2626; }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:"Segoe UI",system-ui,-apple-system,Arial,sans-serif;line-height:1.4}
  .wrap{max-width:1040px;margin:0 auto;padding:24px 16px 48px}
  header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:18px}
  h1{font-size:22px;margin:0;letter-spacing:.3px}
  .sub{color:var(--muted);font-size:13px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
  .hero{display:flex;gap:28px;align-items:center;flex-wrap:wrap}
  .gobox{position:relative;width:210px;height:210px;flex:none;margin:0 auto}
  .gobox svg{position:absolute;inset:0;transform:rotate(-90deg)}
  .track{fill:none;stroke:var(--panel2);stroke-width:8}
  .prog{fill:none;stroke:var(--down);stroke-width:8;stroke-linecap:round;
        stroke-dasharray:553;stroke-dashoffset:553;transition:stroke-dashoffset .35s ease}
  #go{position:absolute;left:25px;top:25px;width:160px;height:160px;border-radius:50%;
      border:2px solid var(--down);background:transparent;color:var(--text);
      font:600 34px "Segoe UI",system-ui,Arial,sans-serif;letter-spacing:1px;cursor:pointer;
      transition:background .15s, transform .1s}
  #go:hover:not(:disabled){background:color-mix(in srgb,var(--down) 14%,transparent)}
  #go:active:not(:disabled){transform:scale(.97)}
  #go:disabled{cursor:default;font-size:16px;font-weight:500;color:var(--muted);border-color:var(--line)}
  #go:focus-visible{outline:3px solid var(--up);outline-offset:3px}
  .main{flex:1;min-width:260px}
  .speeds{display:flex;gap:36px;flex-wrap:wrap;margin-bottom:10px}
  .speed label{display:block;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px}
  .speed .big{font-size:46px;font-weight:300;line-height:1.1;font-variant-numeric:tabular-nums}
  .speed.down label b{color:var(--down)} .speed.up label b{color:var(--up)}
  .mini{display:flex;gap:22px;flex-wrap:wrap;color:var(--muted);font-size:14px;margin-bottom:14px}
  .mini b{color:var(--text);font-weight:600;font-variant-numeric:tabular-nums}
  .steps{list-style:none;display:flex;gap:6px;flex-wrap:wrap;padding:0;margin:0 0 14px}
  .steps li{font-size:12px;padding:3px 10px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
  .steps li.on{border-color:var(--down);color:var(--text)}
  .steps li.done{border-color:var(--good);color:var(--good)}
  .steps li.fail{border-color:var(--bad);color:var(--bad)}
  .chips{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}
  .chip{background:var(--panel2);border-radius:10px;padding:8px 10px;font-size:13px}
  .chip span{display:block;color:var(--muted);font-size:12px}
  .chip b{font-size:14px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-top:16px}
  h2{font-size:13px;margin:0 0 12px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;font-weight:600}
  .row{display:flex;justify-content:space-between;gap:14px;padding:7px 0;border-top:1px solid var(--line);font-size:14px}
  .row:first-of-type{border-top:0}
  .row span{color:var(--muted);flex:none} .row b{font-weight:600;text-align:right;word-break:break-word}
  .bars{display:inline-flex;gap:2px;align-items:flex-end;height:14px;margin-right:8px;vertical-align:-1px}
  .bars i{width:4px;background:var(--line);border-radius:1px}
  .bars i:nth-child(1){height:4px}.bars i:nth-child(2){height:7px}
  .bars i:nth-child(3){height:10px}.bars i:nth-child(4){height:14px}
  .bars i.f{background:var(--good)}
  .note{margin:10px 0 0;font-size:13px;color:var(--warn)}
  canvas{width:100%;height:150px;display:block}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px;text-align:center}
  .stats div{background:var(--panel2);border-radius:8px;padding:6px 2px;font-size:12px;color:var(--muted)}
  .stats b{display:block;color:var(--text);font-size:15px;font-variant-numeric:tabular-nums}
  details{margin-top:16px}
  summary{cursor:pointer;color:var(--muted);font-size:14px}
  .set{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px}
  .set label{font-size:13px;color:var(--muted);display:flex;flex-direction:column;gap:4px}
  .set input{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:14px;width:190px}
  .foot{margin-top:22px;font-size:12px;color:var(--muted)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Network Tester</h1>
    <div class="sub">Runs on your own PC. Press Ctrl+C in the terminal to stop it.</div>
  </header>

  <section class="card hero">
    <div class="gobox">
      <svg viewBox="0 0 200 200" width="210" height="210" aria-hidden="true">
        <circle class="track" cx="100" cy="100" r="88"/>
        <circle class="prog" id="ring" cx="100" cy="100" r="88"/>
      </svg>
      <button id="go" type="button">GO</button>
    </div>

    <div class="main">
      <div class="speeds">
        <div class="speed down"><label><b>&#9660;</b> Download Mbps</label><div class="big" id="down">&mdash;</div></div>
        <div class="speed up"><label><b>&#9650;</b> Upload Mbps</label><div class="big" id="up">&mdash;</div></div>
      </div>
      <div class="mini">
        <span>Ping <b id="pingv">&mdash;</b> ms</span>
        <span>Jitter <b id="jitv">&mdash;</b> ms</span>
        <span>Packet loss <b id="lossv">&mdash;</b></span>
      </div>
      <ul class="steps" id="steps">
        <li data-s="wifi">Wi-Fi</li><li data-s="ping">Ping</li>
        <li data-s="down">Download</li><li data-s="up">Upload</li>
      </ul>
      <div class="chips" id="rate"></div>
    </div>
  </section>

  <div class="grid">
    <section class="card">
      <h2>Wi-Fi you are connected to</h2>
      <div id="wifi"><div class="row"><span>Status</span><b>Click GO to check</b></div></div>
      <p class="note" id="wifinote" hidden></p>
    </section>

    <section class="card">
      <h2>Internet connection</h2>
      <div id="net">
        <div class="row"><span>Provider (ISP)</span><b id="isp">&mdash;</b></div>
        <div class="row"><span>Public IP</span><b id="ip">&mdash;</b></div>
        <div class="row"><span>Location</span><b id="loc">&mdash;</b></div>
        <div class="row"><span>Test server</span><b id="srv">&mdash;</b></div>
      </div>
    </section>

    <section class="card">
      <h2>Latency per ping (ms)</h2>
      <canvas id="chart"></canvas>
      <div class="stats">
        <div>Min<b id="smin">&mdash;</b></div><div>Average<b id="savg">&mdash;</b></div>
        <div>Max<b id="smax">&mdash;</b></div><div>Sent / got<b id="scnt">&mdash;</b></div>
      </div>
    </section>
  </div>

  <details>
    <summary>Settings</summary>
    <div class="set">
      <label>Ping target<input id="target" value="google.com" spellcheck="false"></label>
      <label>Number of pings<input id="count" type="number" min="1" max="100" value="10"></label>
    </div>
  </details>
  <p class="foot">Speed results are a rough guide: they depend on the test server, other devices on your Wi-Fi, and what else your PC is doing.</p>
</div>

<script>
const $ = s => document.querySelector(s);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const mbps = (bytes, secs) => bytes * 8 / secs / 1e6;
const f1 = n => (n == null || isNaN(n)) ? "—" : n.toFixed(1);
const f2 = n => (n == null || isNaN(n)) ? "—" : n.toFixed(2);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let busy = false;
let R = {};                 // results of the current run
let pings = [];             // [{ms|null}]

function setRing(p){ $("#ring").style.strokeDashoffset = 553 * (1 - Math.max(0, Math.min(1, p))); }
function setStep(name, state){
  document.querySelectorAll("#steps li").forEach(li => {
    if (li.dataset.s === name) li.className = state;
  });
}
function row(label, valueHtml){ return `<div class="row"><span>${label}</span><b>${valueHtml}</b></div>`; }

/* ---------- Wi-Fi ---------- */
function renderWifi(w){
  const box = $("#wifi"), note = $("#wifinote");
  note.hidden = true;
  if (!w || w.supported === false){
    box.innerHTML = row("Status", "Wi-Fi details not available on this system");
    return;
  }
  if (w.needs_location){
    box.innerHTML = row("Status", "Windows is hiding the Wi-Fi name");
    note.textContent = "Turn on Location: Settings > Privacy & security > Location, then allow desktop apps, and click GO again.";
    note.hidden = false;
    return;
  }
  if (!w.connected){
    box.innerHTML = row("Status", "Not connected to Wi-Fi") + row("Connection", "Wired (Ethernet) or offline");
    return;
  }
  const s = w.signal;
  const lit = s == null ? 0 : s >= 75 ? 4 : s >= 50 ? 3 : s >= 25 ? 2 : 1;
  const label = s == null ? "" : s >= 75 ? "Excellent" : s >= 50 ? "Good" : s >= 25 ? "Weak" : "Very weak";
  const bars = `<span class="bars">${[1,2,3,4].map(i => `<i class="${i <= lit ? "f" : ""}"></i>`).join("")}</span>`;
  let html = row("Wi-Fi name", esc(w.ssid || "(hidden or unavailable)"));
  html += row("Signal", s == null ? "—" : `${bars}${s}% ${label}${w.dbm != null ? ` (${w.dbm} dBm)` : ""}`);
  const bc = [w.band, w.channel ? "channel " + w.channel : ""].filter(Boolean).join(", ");
  if (bc) html += row("Band / channel", esc(bc));
  if (w.radio) html += row("Wi-Fi standard", esc(w.radio));
  if (w.rx != null || w.tx != null) html += row("Link speed", `${f1(w.rx)} down / ${f1(w.tx)} up Mbps`);
  if (w.auth) html += row("Security", esc(w.auth));
  if (w.adapter) html += row("Adapter", esc(w.adapter));
  box.innerHTML = html;
}

async function doWifi(){
  setStep("wifi", "on");
  const w = await (await fetch("/api/wifi")).json();
  renderWifi(w);
  setStep("wifi", "done");
}

/* ---------- public IP / ISP ---------- */
async function doInternetInfo(){
  try{
    const m = await (await fetch("https://speed.cloudflare.com/meta", {cache:"no-store"})).json();
    $("#isp").textContent = m.asOrganization || "—";
    $("#ip").textContent = m.clientIp || "—";
    $("#loc").textContent = [m.city, m.region, m.country].filter(Boolean).join(", ") || "—";
    $("#srv").textContent = m.colo && (m.colo.city || m.colo.iata) ? `Cloudflare, ${m.colo.city || m.colo.iata}` : "Cloudflare";
    return;
  }catch(e){}
  try{
    const m = await (await fetch("https://ipinfo.io/json")).json();
    $("#isp").textContent = (m.org || "—").replace(/^AS\d+\s*/, "");
    $("#ip").textContent = m.ip || "—";
    $("#loc").textContent = [m.city, m.region, m.country].filter(Boolean).join(", ") || "—";
    $("#srv").textContent = "—";
  }catch(e){
    $("#isp").textContent = "Unavailable (offline or blocked)";
  }
}

/* ---------- ping ---------- */
function drawChart(){
  const c = $("#chart"), dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const g = c.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
  const cs = getComputedStyle(document.documentElement);
  const line = cs.getPropertyValue("--line").trim(), muted = cs.getPropertyValue("--muted").trim();
  const accent = cs.getPropertyValue("--down").trim(), bad = cs.getPropertyValue("--bad").trim();
  const vals = pings.map(p => p.ms).filter(v => v != null);
  const max = Math.max(10, ...vals) * 1.15;
  const L = 34, T = 8, B = 18, pw = w - L - 6, ph = h - T - B;
  g.font = "11px system-ui, sans-serif"; g.fillStyle = muted; g.strokeStyle = line; g.lineWidth = 1;
  for (let i = 0; i <= 3; i++){
    const y = T + ph * i / 3;
    g.beginPath(); g.moveTo(L, y); g.lineTo(L + pw, y); g.stroke();
    g.fillText(Math.round(max * (1 - i / 3)), 2, y + 4);
  }
  if (!pings.length) return;
  const n = Math.max(pings.length, 2);
  const X = i => L + pw * (n === 1 ? 0 : i / (n - 1));
  const Y = v => T + ph * (1 - v / max);
  g.strokeStyle = accent; g.lineWidth = 2; g.beginPath();
  let started = false;
  pings.forEach((p, i) => {
    if (p.ms == null) { started = false; return; }
    if (!started){ g.moveTo(X(i), Y(p.ms)); started = true; } else g.lineTo(X(i), Y(p.ms));
  });
  g.stroke();
  pings.forEach((p, i) => {
    g.fillStyle = p.ms == null ? bad : accent;
    g.beginPath(); g.arc(X(i), p.ms == null ? T + ph : Y(p.ms), 3, 0, 7); g.fill();
  });
}

function pingStats(){
  const v = pings.map(p => p.ms).filter(x => x != null);
  const lost = pings.length - v.length;
  let jit = null;
  if (v.length > 1){
    let s = 0; for (let i = 1; i < v.length; i++) s += Math.abs(v[i] - v[i-1]);
    jit = s / (v.length - 1);
  }
  return {
    sent: pings.length, got: v.length, lost,
    loss: pings.length ? lost / pings.length * 100 : null,
    min: v.length ? Math.min(...v) : null,
    avg: v.length ? v.reduce((a, b) => a + b, 0) / v.length : null,
    max: v.length ? Math.max(...v) : null, jit
  };
}

function showPing(){
  const s = pingStats();
  $("#pingv").textContent = s.avg == null ? "—" : Math.round(s.avg);
  $("#jitv").textContent = f1(s.jit);
  $("#lossv").textContent = s.loss == null ? "—" : s.loss.toFixed(0) + "%";
  $("#smin").textContent = f1(s.min); $("#savg").textContent = f1(s.avg);
  $("#smax").textContent = f1(s.max); $("#scnt").textContent = `${s.sent} / ${s.got}`;
  drawChart();
  return s;
}

async function doPing(){
  setStep("ping", "on");
  const target = $("#target").value.trim() || "google.com";
  const count = Math.max(1, Math.min(100, parseInt($("#count").value, 10) || 10));
  pings = [];
  for (let i = 0; i < count; i++){
    let r;
    try { r = await (await fetch("/api/ping?target=" + encodeURIComponent(target))).json(); }
    catch(e){ r = {ok:false, error:"The tester program is not running."}; }
    if (r.error){ setStep("ping", "fail"); throw new Error(r.error); }
    pings.push({ms: r.ok ? r.ms : null});
    showPing();
    setRing(0.1 + 0.25 * (i + 1) / count);
    await sleep(400);
  }
  R.ping = showPing();
  setStep("ping", R.ping.got ? "done" : "fail");
}

/* ---------- download / upload (Cloudflare speed test servers) ---------- */
async function doDown(){
  setStep("down", "on");
  const SECS = 8, STREAMS = 4;
  let bytes = 0, stop = false;
  const ctrl = new AbortController(), t0 = performance.now();
  const timer = setTimeout(() => { stop = true; ctrl.abort(); }, SECS * 1000);
  const tick = setInterval(() => {
    const s = (performance.now() - t0) / 1000;
    $("#down").textContent = f2(mbps(bytes, s));
    setRing(0.35 + 0.35 * Math.min(1, s / SECS));
  }, 200);
  try{
    await Promise.all(Array.from({length: STREAMS}, async () => {
      while (!stop){
        const r = await fetch("https://speed.cloudflare.com/__down?bytes=25000000&r=" + Math.random(),
                              {cache: "no-store", signal: ctrl.signal});
        const rd = r.body.getReader();
        for (;;){ const {done, value} = await rd.read(); if (done) break; bytes += value.length; }
      }
    }));
  }catch(e){ if (bytes === 0){ clearInterval(tick); clearTimeout(timer); setStep("down", "fail"); $("#down").textContent = "\u2014"; throw new Error("Download test could not reach the speed server"); } }
  clearInterval(tick); clearTimeout(timer);
  R.down = mbps(bytes, (performance.now() - t0) / 1000);
  $("#down").textContent = f2(R.down);
  setStep("down", "done");
}

function doUp(){
  setStep("up", "on");
  return new Promise((resolve, reject) => {
    const SECS = 7, STREAMS = 3;
    const buf = new Uint8Array(4 * 1024 * 1024);
    for (let i = 0; i < buf.length; i += 65536) crypto.getRandomValues(buf.subarray(i, i + 65536));
    const payload = new Blob([buf]);
    let sent = 0, stopped = false;
    const xhrs = new Set(), t0 = performance.now();
    const tick = setInterval(() => {
      const s = (performance.now() - t0) / 1000;
      $("#up").textContent = f2(mbps(sent, s));
      setRing(0.7 + 0.3 * Math.min(1, s / SECS));
    }, 200);
    const finish = () => {
      stopped = true; clearInterval(tick); xhrs.forEach(x => x.abort());
      if (sent === 0){ setStep("up", "fail"); $("#up").textContent = "\u2014"; return reject(new Error("Upload test could not reach the speed server.")); }
      R.up = mbps(sent, (performance.now() - t0) / 1000);
      $("#up").textContent = f2(R.up);
      setStep("up", "done"); resolve();
    };
    const launch = () => {
      if (stopped) return;
      const x = new XMLHttpRequest(); xhrs.add(x); let last = 0;
      x.open("POST", "https://speed.cloudflare.com/__up?r=" + Math.random());
      x.upload.onprogress = e => { sent += e.loaded - last; last = e.loaded; };
      x.onload = () => { xhrs.delete(x); launch(); };
      x.onerror = () => { xhrs.delete(x); if (sent === 0 && !xhrs.size) finish(); };
      x.send(payload);
    };
    for (let i = 0; i < STREAMS; i++) launch();
    setTimeout(finish, SECS * 1000);
  });
}

/* ---------- what can you do ---------- */
function rate(){
  const p = R.ping || {}, d = R.down, u = R.up;
  const out = [];
  const tag = (name, level, why) => out.push({name, level, why});
  const ok = (cond, a, b) => cond ? a : b;
  if (p.got){
    const g = p.avg < 50 && p.jit < 15 && p.loss === 0 ? "Great" : p.avg < 100 && p.loss < 2 ? "OK" : "Poor";
    tag("Gaming", g, `ping ${Math.round(p.avg)} ms`);
  } else tag("Gaming", "—", "no ping data");
  if (d != null) tag("Web browsing", d >= 5 ? "Great" : d >= 2 ? "OK" : "Poor", `${f1(d)} Mbps down`);
  else tag("Web browsing", "—", "no speed data");
  if (d != null) tag("HD / 4K video", d >= 25 ? "Great" : d >= 10 ? "OK" : "Poor", `${f1(d)} Mbps down`);
  else tag("HD / 4K video", "—", "no speed data");
  if (d != null && u != null && p.got){
    const v = d >= 3 && u >= 3 && p.jit < 30 && p.loss < 2 ? "Great" : u >= 1 && p.jit < 50 ? "OK" : "Poor";
    tag("Video calls", v, `${f1(u)} Mbps up`);
  } else tag("Video calls", "—", "needs ping + speed");
  const color = {Great: "var(--good)", OK: "var(--warn)", Poor: "var(--bad)"};
  $("#rate").innerHTML = out.map(o =>
    `<div class="chip"><span>${o.name}</span><b style="color:${color[o.level] || "var(--muted)"}">${o.level}</b> <span style="display:inline">${esc(o.why)}</span></div>`
  ).join("");
}

/* ---------- run everything ---------- */
async function runTest(){
  if (busy) return;
  busy = true; R = {}; pings = [];
  const go = $("#go");
  go.disabled = true; go.textContent = "Testing…";
  ["#down", "#up", "#pingv", "#jitv", "#lossv", "#smin", "#savg", "#smax", "#scnt"].forEach(s => $(s).textContent = "—");
  $("#rate").innerHTML = "";
  document.querySelectorAll("#steps li").forEach(li => li.className = "");
  setRing(0.02); drawChart();
  const errors = [];
  const attempt = async fn => { try { await fn(); } catch(e){ errors.push(e.message || String(e)); } };
  const info = doInternetInfo();
  await attempt(doWifi);
  setRing(0.1);
  await attempt(doPing);
  await attempt(doDown);
  await attempt(doUp);
  await info;
  setRing(1); rate();
  if (errors.length){
    const n = $("#wifinote");
    n.hidden = false;
    n.textContent = "Some steps could not finish: " + [...new Set(errors.map(e => e.replace(/\.$/, "")))].join(" | ") +
      ". Speed tests need internet access to speed.cloudflare.com (some school or office networks block it).";
  }
  go.disabled = false; go.textContent = "GO"; busy = false;
}

$("#go").addEventListener("click", runTest);
window.addEventListener("resize", drawChart);
drawChart();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "NetworkTester"

    def log_message(self, *args):
        pass  # keep the terminal quiet

    def send_body(self, code, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, obj):
        self.send_body(code, json.dumps(obj))

    def do_GET(self):
        # Only answer requests addressed to this PC (blocks DNS-rebinding tricks).
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return self.send_json(403, {"ok": False, "error": "forbidden"})

        url = urlparse(self.path)
        if url.path == "/":
            return self.send_body(200, PAGE, "text/html")
        if url.path == "/api/wifi":
            return self.send_json(200, wifi_info())
        if url.path == "/api/ping":
            target = (parse_qs(url.query).get("target") or [""])[0].strip()
            if not HOST_RE.match(target):
                return self.send_json(400, {"ok": False, "error": "Invalid ping target."})
            return self.send_json(200, ping_once(target))
        self.send_json(404, {"error": "not found"})


def main():
    server = None
    for port in range(8000, 8020):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        print("Could not start: ports 8000-8019 are all busy.")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print("Network Tester is running.")
    print(f"If your browser does not open, go to: {url}")
    print("Press Ctrl+C here to stop.")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

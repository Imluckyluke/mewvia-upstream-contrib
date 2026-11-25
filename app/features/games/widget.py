"""Interactive HTML5 Retro Arcade Telegram Widget for Tic-Tac-Toe (XO)."""

from __future__ import annotations

import aiohttp

from app.core.logging import get_logger

log = get_logger(__name__)

XO_WIDGET_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
  content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<style>
* {
  box-sizing: border-box;
  -webkit-tap-highlight-color: transparent;
  margin: 0;
  padding: 0;
}
body {
  background: #08080c;
  color: #00ff66;
  font-family: 'Courier New', Courier, monospace;
  padding: 10px 8px;
  text-align: center;
  user-select: none;
  -webkit-user-select: none;
  touch-action: manipulation;
  background-image: repeating-linear-gradient(
    0deg,
    rgba(0,0,0,0.5) 0px,
    rgba(0,0,0,0.5) 1px,
    transparent 1px,
    transparent 2px
  );
}
.arcade-cabinet {
  border: 3px solid #00f0ff;
  border-radius: 12px;
  padding: 10px 8px;
  background: #0e0e18;
  box-shadow: 0 0 16px rgba(0,240,255,0.35), inset 0 0 16px rgba(0,0,0,0.85);
  max-width: 290px;
  margin: 0 auto;
}
.marquee {
  background: #161626;
  border: 2px solid #ff0055;
  border-radius: 6px;
  padding: 6px 4px;
  margin-bottom: 8px;
  box-shadow: 0 0 10px rgba(255,0,85,0.3);
}
.title {
  font-size: 16px;
  font-weight: 900;
  color: #ffea00;
  text-shadow: 0 0 8px #ffea00, 2px 2px #ff0055;
  letter-spacing: 2px;
}
.sub {
  font-size: 9px;
  color: #00f0ff;
  margin-top: 2px;
  letter-spacing: 1px;
}
.led-panel {
  display: flex;
  justify-content: space-around;
  align-items: center;
  background: #000;
  border: 2px solid #222;
  padding: 5px 12px;
  margin-bottom: 8px;
  border-radius: 6px;
}
.led-box { display: flex; flex-direction: column; align-items: center; }
.led-label { font-size: 9px; color: #888; font-weight: bold; }
.led-val { font-size: 20px; font-weight: 900; font-family: monospace; }
.val-1p { color: #ff0055; text-shadow: 0 0 8px #ff0055; }
.val-cpu { color: #00ff66; text-shadow: 0 0 8px #00ff66; }
.vs-badge { font-size: 11px; font-weight: 900; color: #64748b; }

.diff-row {
  display: flex;
  gap: 6px;
  justify-content: center;
  margin-bottom: 10px;
}
.diff-btn {
  flex: 1;
  max-width: 82px;
  background: #141422;
  border: 2px solid #333;
  color: #888;
  font-family: inherit;
  font-size: 10px;
  font-weight: 900;
  padding: 6px 0;
  border-radius: 5px;
  cursor: pointer;
  touch-action: manipulation;
}
.diff-btn.active {
  background: #ffea00;
  color: #000;
  border-color: #fff;
  box-shadow: 0 0 10px rgba(255,234,0,0.6);
}
.grid {
  display: grid;
  grid-template-columns: repeat(3, 72px);
  grid-template-rows: repeat(3, 72px);
  gap: 8px;
  width: 232px;
  height: 232px;
  margin: 0 auto;
  justify-content: center;
}
.cell {
  width: 72px;
  height: 72px;
  min-width: 72px;
  min-height: 72px;
  max-width: 72px;
  max-height: 72px;
  background: #1a1a2a;
  border: 2px solid #3a3a4c;
  border-bottom: 4px solid #10101a;
  border-radius: 8px;
  color: #fff;
  font-family: inherit;
  font-size: 38px;
  font-weight: 900;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  touch-action: manipulation;
  box-shadow: inset 0 0 8px rgba(0,0,0,0.6);
  line-height: 1;
  padding: 0;
  overflow: hidden;
  outline: none;
}
.cell:active {
  background: #252538;
}
.cell.x {
  color: #ff0055;
  text-shadow: 0 0 12px #ff0055, 0 0 20px #ff0055;
  border-color: #ff0055;
}
.cell.o {
  color: #00ff66;
  text-shadow: 0 0 12px #00ff66, 0 0 20px #00ff66;
  border-color: #00ff66;
}
.cell.win {
  background: #ffea00 !important;
  color: #000 !important;
  border-color: #fff !important;
  text-shadow: none !important;
  animation: flash 0.3s infinite alternate;
}
@keyframes flash { from { opacity: 0.7; } to { opacity: 1; } }
.msg-screen {
  margin: 10px 0 8px 0;
  background: #000;
  border: 2px solid #222;
  border-radius: 5px;
  padding: 5px;
  font-size: 11px;
  font-weight: 900;
  color: #ffea00;
  text-shadow: 0 0 6px rgba(255,234,0,0.6);
  min-height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  letter-spacing: 0.5px;
  overflow: hidden;
}
.footer-btns {
  display: flex;
  gap: 8px;
  justify-content: center;
}
.act-btn {
  background: #00f0ff;
  border: none;
  border-bottom: 3px solid #008899;
  color: #000;
  font-family: inherit;
  font-size: 10px;
  font-weight: 900;
  padding: 7px 14px;
  border-radius: 5px;
  cursor: pointer;
  touch-action: manipulation;
  letter-spacing: 1px;
  box-shadow: 0 0 8px rgba(0,240,255,0.4);
}
.act-btn:active {
  transform: translateY(1px);
}
.btn-pink {
  background: #ff0055;
  color: #fff;
  border-bottom: 3px solid #88002d;
  box-shadow: 0 0 8px rgba(255,0,85,0.4);
}
</style>
</head>
<body>
<div class="arcade-cabinet">
  <div class="marquee">
    <div class="title">★ MEWVIA ARCADE ★</div>
    <div class="sub">INSERT COIN - 1P VS CPU</div>
  </div>

  <div class="led-panel">
    <div class="led-box">
      <span class="led-label">1P [X]</span>
      <span class="led-val val-1p" id="sX">00</span>
    </div>
    <div class="vs-badge">VS</div>
    <div class="led-box">
      <span class="led-label">CPU [O]</span>
      <span class="led-val val-cpu" id="sO">00</span>
    </div>
  </div>

  <div class="diff-row">
    <button class="diff-btn" id="d0" type="button" onclick="setD(0)">EASY</button>
    <button class="diff-btn active" id="d1" type="button" onclick="setD(1)">SMART</button>
    <button class="diff-btn" id="d2" type="button" onclick="setD(2)">BRUTAL</button>
  </div>

  <div class="grid">
    <button class="cell" id="c0" type="button" onclick="tap(0)"></button>
    <button class="cell" id="c1" type="button" onclick="tap(1)"></button>
    <button class="cell" id="c2" type="button" onclick="tap(2)"></button>
    <button class="cell" id="c3" type="button" onclick="tap(3)"></button>
    <button class="cell" id="c4" type="button" onclick="tap(4)"></button>
    <button class="cell" id="c5" type="button" onclick="tap(5)"></button>
    <button class="cell" id="c6" type="button" onclick="tap(6)"></button>
    <button class="cell" id="c7" type="button" onclick="tap(7)"></button>
    <button class="cell" id="c8" type="button" onclick="tap(8)"></button>
  </div>

  <div class="msg-screen" id="mBox">&gt;&gt; YOUR TURN: TAP TO MOVE &lt;&lt;</div>

  <div class="footer-btns">
    <button class="act-btn" type="button" onclick="restart()">[ NEW GAME ]</button>
    <button class="act-btn btn-pink" type="button" onclick="resetScore()">[ RESET SCORE ]</button>
  </div>
</div>

<script>
var b = ['', '', '', '', '', '', '', '', ''];
var pX = 0, pO = 0;
var over = false;
var dLevel = 1;

var W = [
  [0,1,2],[3,4,5],[6,7,8],
  [0,3,6],[1,4,7],[2,5,8],
  [0,4,8],[2,4,6]
];

function setMsg(t) {
  var el = document.getElementById('mBox');
  if (el) { el.textContent = t; }
}

function haptic(t) {
  try {
    if (window.TelegramWidget && TelegramWidget.HapticFeedback) {
      if (t === 'tap') TelegramWidget.HapticFeedback.impactOccurred('medium');
      if (t === 'win') TelegramWidget.HapticFeedback.notificationOccurred('success');
      if (t === 'loss') TelegramWidget.HapticFeedback.notificationOccurred('error');
    }
  } catch(e) {}
}

function setD(lvl) {
  dLevel = lvl;
  for (var i = 0; i < 3; i++) {
    var btn = document.getElementById('d' + i);
    if (btn) {
      btn.className = (i === lvl) ? 'diff-btn active' : 'diff-btn';
    }
  }
  haptic('tap');
  restart();
}

function tap(i) {
  if (b[i] !== '' || over) return;
  b[i] = 'X';
  var cell = document.getElementById('c' + i);
  if (cell) {
    cell.textContent = 'X';
    cell.className = 'cell x';
  }
  haptic('tap');

  var win = check('X');
  if (win) {
    pX++;
    var s = pX < 10 ? '0' + pX : '' + pX;
    document.getElementById('sX').textContent = s;
    setMsg('*** YOU WIN! HIGH SCORE! ***');
    flash(win);
    haptic('win');
    over = true;
    return;
  }
  if (!b.includes('')) {
    setMsg('== DRAW GAME! TRY AGAIN ==');
    over = true;
    return;
  }

  setMsg('.. CPU CALCULATING ..');
  over = true;
  setTimeout(cpu, 300);
}

function cpu() {
  var m = getMove();
  if (m !== -1) {
    b[m] = 'O';
    var cell = document.getElementById('c' + m);
    if (cell) {
      cell.textContent = 'O';
      cell.className = 'cell o';
    }
  }
  haptic('tap');
  over = false;

  var win = check('O');
  if (win) {
    pO++;
    var s = pO < 10 ? '0' + pO : '' + pO;
    document.getElementById('sO').textContent = s;
    setMsg('!!! CPU WINS! GAME OVER !!!');
    flash(win);
    haptic('loss');
    over = true;
    return;
  }
  if (!b.includes('')) {
    setMsg('== DRAW GAME! TRY AGAIN ==');
    over = true;
    return;
  }
  setMsg('&gt;&gt; YOUR TURN: TAP TO MOVE &lt;&lt;');
}

function getMove() {
  var free = [];
  for (var i = 0; i < 9; i++) {
    if (b[i] === '') free.push(i);
  }
  if (!free.length) return -1;

  if (dLevel === 0 && Math.random() < 0.65) {
    return free[Math.floor(Math.random() * free.length)];
  }

  for (var i = 0; i < free.length; i++) {
    b[free[i]] = 'O';
    if (check('O')) { b[free[i]] = ''; return free[i]; }
    b[free[i]] = '';
  }
  for (var i = 0; i < free.length; i++) {
    b[free[i]] = 'X';
    if (check('X')) { b[free[i]] = ''; return free[i]; }
    b[free[i]] = '';
  }

  if (dLevel === 1 && Math.random() < 0.2) {
    return free[Math.floor(Math.random() * free.length)];
  }

  if (free.includes(4)) return 4;
  var corners = [];
  [0, 2, 6, 8].forEach(function(c) { if (free.includes(c)) corners.push(c); });
  if (corners.length) return corners[Math.floor(Math.random() * corners.length)];
  return free[0];
}

function check(p) {
  for (var i = 0; i < W.length; i++) {
    var l = W[i];
    if (b[l[0]] === p && b[l[1]] === p && b[l[2]] === p) return l;
  }
  return null;
}

function flash(line) {
  for (var i = 0; i < line.length; i++) {
    var c = document.getElementById('c' + line[i]);
    if (c) c.className = 'cell win';
  }
}

function restart() {
  b = ['', '', '', '', '', '', '', '', ''];
  over = false;
  for (var i = 0; i < 9; i++) {
    var c = document.getElementById('c' + i);
    if (c) {
      c.textContent = '';
      c.className = 'cell';
    }
  }
  setMsg('&gt;&gt; YOUR TURN: TAP TO MOVE &lt;&lt;');
}

function resetScore() {
  haptic('tap');
  pX = 0; pO = 0;
  document.getElementById('sX').textContent = '00';
  document.getElementById('sO').textContent = '00';
  restart();
}
</script>
</body>
</html>"""


async def send_xo_widget(chat_id: int, bot_token: str, api_base_url: str = "") -> bool:
    """Send the interactive retro arcade Tic-Tac-Toe widget to the user.

    `api_base_url` mirrors the Bot's configured server (empty = public
    api.telegram.org) so deployments behind the local Bot API keep working
    even when Telegram's public endpoint is unreachable.
    """
    base = (api_base_url or "https://api.telegram.org").rstrip("/")
    url = f"{base}/bot{bot_token}/sendWidget"
    payload = {
        "chat_id": chat_id,
        "html": XO_WIDGET_HTML,
        "caption": "🕹️ <b>MEWVIA RETRO ARCADE</b>",
        "parse_mode": "HTML",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    msg_id = data.get("result", {}).get("message_id")
                    log.info("xo_widget_sent", chat_id=chat_id, msg_id=msg_id)
                    return True
                body = await resp.text()
                log.warning("xo_widget_failed", status=resp.status, body=body)
                return False
    except Exception as exc:
        log.error("xo_widget_error", error=str(exc))
        return False

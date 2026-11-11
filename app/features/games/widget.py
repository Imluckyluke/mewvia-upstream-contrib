"""Interactive HTML5 Retro Arcade Telegram Widget for Tic-Tac-Toe (XO)."""

from __future__ import annotations

import aiohttp

from app.core.logging import get_logger

log = get_logger(__name__)

XO_WIDGET_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; margin: 0; padding: 0; }
body {
  background: #08080a;
  color: #00ff66;
  font-family: 'Courier New', Courier, monospace;
  padding: 10px;
  text-align: center;
  user-select: none;
  background-image: repeating-linear-gradient(
    0deg,
    rgba(0,0,0,0.5) 0px,
    rgba(0,0,0,0.5) 1px,
    transparent 1px,
    transparent 2px
  );
}
.arcade-cabinet {
  border: 3px solid #ff0055;
  border-radius: 12px;
  padding: 10px;
  background: #0d0d14;
  box-shadow: 0 0 15px rgba(255,0,85,0.4), inset 0 0 15px rgba(0,0,0,0.8);
}
.marquee {
  background: #141422;
  border: 2px solid #00f0ff;
  border-radius: 6px;
  padding: 6px;
  margin-bottom: 10px;
  box-shadow: 0 0 10px rgba(0,240,255,0.3);
}
.title {
  font-size: 15px;
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
  background: #000;
  border: 1px solid #333;
  padding: 6px;
  margin-bottom: 10px;
  border-radius: 4px;
}
.led-box { display: flex; flex-direction: column; }
.led-label { font-size: 9px; color: #888; font-weight: bold; }
.led-val { font-size: 16px; font-weight: bold; }
.val-1p { color: #00ff66; text-shadow: 0 0 6px #00ff66; }
.val-cpu { color: #ff0055; text-shadow: 0 0 6px #ff0055; }
.val-diff { color: #ffea00; text-shadow: 0 0 6px #ffea00; cursor: pointer; touch-action: none; }
.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  width: 230px;
  height: 230px;
  margin: 0 auto;
}
.cell-btn {
  background: #1a1a26;
  border: 2px solid #444;
  border-bottom: 4px solid #111;
  border-radius: 8px;
  color: #fff;
  font-family: 'Courier New', Courier, monospace;
  font-size: 38px;
  font-weight: 900;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  touch-action: none;
  transition: all 0.08s;
  box-shadow: inset 0 0 8px rgba(0,0,0,0.6);
}
.cell-btn:active {
  border-bottom: 2px solid #111;
  transform: translateY(2px);
}
.cell-btn.mark-x {
  color: #00ff66;
  text-shadow: 0 0 12px #00ff66, 0 0 20px #00ff66;
  border-color: #00ff66;
}
.cell-btn.mark-o {
  color: #ff0055;
  text-shadow: 0 0 12px #ff0055, 0 0 20px #ff0055;
  border-color: #ff0055;
}
.cell-btn.win {
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
  border: 1px solid #222;
  border-radius: 4px;
  padding: 6px;
  font-size: 11px;
  font-weight: bold;
  color: #ffea00;
  text-shadow: 0 0 6px rgba(255,234,0,0.6);
  min-height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.footer-btns {
  display: flex;
  gap: 8px;
  justify-content: center;
}
.arcade-act-btn {
  background: #ff0055;
  border: none;
  border-bottom: 3px solid #88002d;
  color: #fff;
  font-family: 'Courier New', Courier, monospace;
  font-size: 10px;
  font-weight: bold;
  padding: 6px 12px;
  border-radius: 6px;
  cursor: pointer;
  touch-action: none;
  letter-spacing: 1px;
  box-shadow: 0 0 8px rgba(255,0,85,0.4);
}
.arcade-act-btn:active {
  border-bottom: 1px solid #88002d;
  transform: translateY(2px);
}
.btn-yellow {
  background: #ffea00;
  color: #000;
  border-bottom: 3px solid #b2a400;
  box-shadow: 0 0 8px rgba(255,234,0,0.4);
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
      <span class="led-label">1P (X)</span>
      <span class="led-val val-1p" id="sX">00</span>
    </div>
    <div class="led-box">
      <span class="led-label">DIFFICULTY</span>
      <button class="led-val val-diff" id="dBtn"
        style="background:none;border:none;font-family:inherit;">SMART</button>
    </div>
    <div class="led-box">
      <span class="led-label">CPU (O)</span>
      <span class="led-val val-cpu" id="sO">00</span>
    </div>
  </div>

  <div class="grid" id="g"></div>

  <div class="msg-screen" id="mBox">&gt;&gt; YOUR TURN: TAP TO MOVE &lt;&lt;</div>

  <div class="footer-btns">
    <button class="arcade-act-btn" id="rstBtn">[ RESET GAME ]</button>
    <button class="arcade-act-btn btn-yellow" id="clrBtn">[ CLEAR SCORE ]</button>
  </div>
</div>

<script>
let b = Array(9).fill('');
let pX = 0, pO = 0;
let over = false;
let dLevel = 1;
const dNames = ['EASY', 'SMART', 'BRUTAL'];

const W = [
  [0,1,2],[3,4,5],[6,7,8],
  [0,3,6],[1,4,7],[2,5,8],
  [0,4,8],[2,4,6]
];

function haptic(t) {
  try {
    if (window.TelegramWidget && TelegramWidget.HapticFeedback) {
      if (t === 'tap') TelegramWidget.HapticFeedback.impactOccurred('medium');
      if (t === 'win') TelegramWidget.HapticFeedback.notificationOccurred('success');
      if (t === 'loss') TelegramWidget.HapticFeedback.notificationOccurred('error');
    }
  } catch(e) {}
}

const grid = document.getElementById('g');
const btns = [];
for (let i = 0; i < 9; i++) {
  const btn = document.createElement('button');
  btn.className = 'cell-btn';
  btn.setAttribute('type', 'button');
  btn.addEventListener('click', function() { onMove(i); });
  grid.appendChild(btn);
  btns.push(btn);
}

document.getElementById('dBtn').addEventListener('click', function() {
  dLevel = (dLevel + 1) % 3;
  this.innerText = dNames[dLevel];
  haptic('tap');
  newRound();
});

document.getElementById('rstBtn').addEventListener('click', function() {
  haptic('tap');
  newRound();
});

document.getElementById('clrBtn').addEventListener('click', function() {
  haptic('tap');
  pX = 0; pO = 0;
  document.getElementById('sX').innerText = '00';
  document.getElementById('sO').innerText = '00';
  newRound();
});

function onMove(idx) {
  if (b[idx] !== '' || over) return;
  b[idx] = 'X';
  btns[idx].innerText = 'X';
  btns[idx].classList.add('mark-x');
  haptic('tap');

  const win = check('X');
  if (win) {
    pX++;
    document.getElementById('sX').innerText = pX < 10 ? '0' + pX : pX;
    setMsg('*** YOU WIN! HIGH SCORE! ***');
    flashWin(win);
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
  setTimeout(cpuMove, 300);
}

function cpuMove() {
  const m = getMove();
  if (m !== -1) {
    b[m] = 'O';
    btns[m].innerText = 'O';
    btns[m].classList.add('mark-o');
  }
  haptic('tap');
  over = false;

  const win = check('O');
  if (win) {
    pO++;
    document.getElementById('sO').innerText = pO < 10 ? '0' + pO : pO;
    setMsg('!!! CPU WINS! GAME OVER !!!');
    flashWin(win);
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
  const free = [];
  b.forEach(function(v, i) { if (v === '') free.push(i); });
  if (!free.length) return -1;

  if (dLevel === 0 && Math.random() < 0.6) {
    return free[Math.floor(Math.random() * free.length)];
  }

  for (let i = 0; i < free.length; i++) {
    b[free[i]] = 'O';
    if (check('O')) { b[free[i]] = ''; return free[i]; }
    b[free[i]] = '';
  }
  for (let i = 0; i < free.length; i++) {
    b[free[i]] = 'X';
    if (check('X')) { b[free[i]] = ''; return free[i]; }
    b[free[i]] = '';
  }

  if (dLevel === 1 && Math.random() < 0.2) {
    return free[Math.floor(Math.random() * free.length)];
  }

  if (free.includes(4)) return 4;
  const corners = [0,2,6,8].filter(function(c) { return free.includes(c); });
  if (corners.length) return corners[Math.floor(Math.random() * corners.length)];
  return free[0];
}

function check(p) {
  for (let i = 0; i < W.length; i++) {
    const l = W[i];
    if (b[l[0]] === p && b[l[1]] === p && b[l[2]] === p) return l;
  }
  return null;
}

function flashWin(line) {
  for (let i = 0; i < line.length; i++) {
    btns[line[i]].classList.add('win');
  }
}

function setMsg(t) {
  document.getElementById('mBox').innerHTML = t;
}

function newRound() {
  b = Array(9).fill('');
  over = false;
  for (let i = 0; i < 9; i++) {
    btns[i].innerText = '';
    btns[i].className = 'cell-btn';
  }
  setMsg('&gt;&gt; YOUR TURN: TAP TO MOVE &lt;&lt;');
}
</script>
</body>
</html>"""


async def send_xo_widget(chat_id: int, bot_token: str) -> bool:
    """Send the interactive retro arcade Tic-Tac-Toe widget to the user."""
    url = f"https://api.telegram.org/bot{bot_token}/sendWidget"
    payload = {
        "chat_id": chat_id,
        "html": XO_WIDGET_HTML,
        "caption": "🕹️ <b>MEWVIA RETRO ARCADE (دوز کلاسیک نوستالژیک)</b>\nروی دکمه‌ها لمس کنید!",
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

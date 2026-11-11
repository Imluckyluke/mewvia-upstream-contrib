"""Interactive HTML5 graphical Telegram Widget for Tic-Tac-Toe (XO)."""

from __future__ import annotations

import aiohttp

from app.core.logging import get_logger

log = get_logger(__name__)

XO_WIDGET_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body {
  margin: 0;
  padding: 14px 10px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  background: radial-gradient(circle at 50% 20%, #1e1b4b 0%, #0f172a 100%);
  color: #fff;
  text-align: center;
  user-select: none;
  overflow: hidden;
}
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding: 0 6px;
}
.title {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.5px;
  background: linear-gradient(135deg, #38bdf8, #818cf8, #c084fc);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.diff-badge {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 12px;
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.15);
  cursor: pointer;
  color: #38bdf8;
  font-weight: 600;
}
.scoreboard {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 12px;
  padding: 8px 12px;
  margin-bottom: 14px;
}
.player-box { display: flex; flex-direction: column; gap: 2px; }
.p-name { font-size: 11px; color: #94a3b8; font-weight: 600; }
.p-score { font-size: 18px; font-weight: 800; }
.p-x { color: #38bdf8; text-shadow: 0 0 12px rgba(56,189,248,0.6); }
.p-o { color: #f43f5e; text-shadow: 0 0 12px rgba(244,63,94,0.6); }
.vs { font-size: 12px; color: #64748b; font-weight: 700; padding: 0 8px; }
.board-wrap {
  position: relative;
  width: 250px;
  height: 250px;
  margin: 0 auto;
}
.board {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  grid-template-rows: repeat(3, 1fr);
  gap: 8px;
  width: 100%;
  height: 100%;
}
.cell {
  position: relative;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.07);
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: inset 0 0 15px rgba(0,0,0,0.2);
}
.cell:active { transform: scale(0.94); background: rgba(255,255,255,0.08); }
.cell.win {
  background: rgba(99,102,241,0.2) !important;
  border-color: #818cf8 !important;
  box-shadow: 0 0 20px rgba(129,140,248,0.5);
}
.svg-icon {
  width: 48px;
  height: 48px;
  filter: drop-shadow(0 0 6px currentColor);
  animation: popIn 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}
@keyframes popIn {
  0% { transform: scale(0.2); opacity: 0; }
  100% { transform: scale(1); opacity: 1; }
}
.msg {
  height: 24px;
  margin: 12px 0 8px 0;
  font-size: 13px;
  font-weight: 700;
  color: #cbd5e1;
  display: flex;
  align-items: center;
  justify-content: center;
}
.controls {
  display: flex;
  gap: 8px;
  justify-content: center;
  margin-top: 6px;
}
.btn {
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.12);
  color: #e2e8f0;
  padding: 6px 14px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn:active { transform: scale(0.95); background: rgba(255,255,255,0.15); }
.btn-primary {
  background: linear-gradient(135deg, #0284c7, #4f46e5);
  border: none;
  box-shadow: 0 0 15px rgba(79,70,229,0.4);
}
</style>
</head>
<body>
<div class="header">
  <div class="title">MEWVIA XO ⚡</div>
  <div class="diff-badge" id="diffBtn" onclick="toggleDiff()">سطح: هوشمند 🟡</div>
</div>

<div class="scoreboard">
  <div class="player-box">
    <span class="p-name">شما (X)</span>
    <span class="p-score p-x" id="scX">0</span>
  </div>
  <div class="vs">VS</div>
  <div class="player-box">
    <span class="p-name">میوویا (O)</span>
    <span class="p-score p-o" id="scO">0</span>
  </div>
</div>

<div class="board-wrap">
  <div class="board" id="grid"></div>
</div>

<div class="msg" id="statusMsg">نوبت شماست! یک خانه را انتخاب کن</div>

<div class="controls">
  <button class="btn btn-primary" onclick="resetGame(true)">🔄 بازی جدید</button>
  <button class="btn" onclick="resetScore()">صفر کردن رکورد</button>
</div>

<script>
const X_SVG = (
  '<svg class="svg-icon" style="color:#38bdf8" viewBox="0 0 24 24" ' +
  'fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round">' +
  '<line x1="18" y1="6" x2="6" y2="18"></line>' +
  '<line x1="6" y1="6" x2="18" y2="18"></line></svg>'
);
const O_SVG = (
  '<svg class="svg-icon" style="color:#f43f5e" viewBox="0 0 24 24" ' +
  'fill="none" stroke="currentColor" stroke-width="3">' +
  '<circle cx="12" cy="12" r="8"></circle></svg>'
);

let board = Array(9).fill('');
let scores = { x: 0, o: 0 };
let over = false;
let diff = 1;
const diffNames = ['سطح: ساده 🟢', 'سطح: هوشمند 🟡', 'سطح: غیرممکن 🔴'];

const WINS = [
  [0,1,2],[3,4,5],[6,7,8],
  [0,3,6],[1,4,7],[2,5,8],
  [0,4,8],[2,4,6]
];

function haptic(type) {
  try {
    if (window.TelegramWidget && TelegramWidget.HapticFeedback) {
      if (type === 'tap') TelegramWidget.HapticFeedback.impactOccurred('light');
      if (type === 'win') TelegramWidget.HapticFeedback.notificationOccurred('success');
      if (type === 'loss') TelegramWidget.HapticFeedback.notificationOccurred('error');
    }
  } catch(e) {}
}

function initGrid() {
  const g = document.getElementById('grid');
  g.innerHTML = '';
  for(let i=0; i<9; i++) {
    const d = document.createElement('div');
    d.className = 'cell';
    d.onclick = () => userMove(i);
    g.appendChild(d);
  }
}

function toggleDiff() {
  diff = (diff + 1) % 3;
  document.getElementById('diffBtn').innerText = diffNames[diff];
  haptic('tap');
  resetGame(false);
}

function userMove(i) {
  if (board[i] || over) return;
  board[i] = 'X';
  haptic('tap');
  updateView();

  const win = checkWin('X');
  if (win) {
    endGame('X', win);
    return;
  }
  if (!board.includes('')) {
    endGame('D');
    return;
  }

  document.getElementById('statusMsg').innerText = 'میوویا در حال محاسبه...';
  over = true;
  setTimeout(botTurn, 320);
}

function botTurn() {
  const m = getBestMove();
  if (m !== -1) board[m] = 'O';
  over = false;
  haptic('tap');
  updateView();

  const win = checkWin('O');
  if (win) {
    endGame('O', win);
    return;
  }
  if (!board.includes('')) {
    endGame('D');
    return;
  }
  document.getElementById('statusMsg').innerText = 'نوبت شماست (X)';
}

function getBestMove() {
  const empty = board.map((v, i) => v === '' ? i : null).filter(v => v !== null);
  if (!empty.length) return -1;

  if (diff === 0 && Math.random() < 0.6) return empty[Math.floor(Math.random()*empty.length)];

  for (let m of empty) {
    board[m] = 'O';
    if (checkWin('O')) { board[m] = ''; return m; }
    board[m] = '';
  }
  for (let m of empty) {
    board[m] = 'X';
    if (checkWin('X')) { board[m] = ''; return m; }
    board[m] = '';
  }

  if (diff === 1 && Math.random() < 0.25) return empty[Math.floor(Math.random()*empty.length)];

  if (empty.includes(4)) return 4;
  const corners = [0,2,6,8].filter(c => empty.includes(c));
  if (corners.length) return corners[Math.floor(Math.random()*corners.length)];

  return empty[0];
}

function checkWin(p) {
  for (let w of WINS) {
    if (board[w[0]] === p && board[w[1]] === p && board[w[2]] === p) return w;
  }
  return null;
}

function endGame(winner, line) {
  over = true;
  const msg = document.getElementById('statusMsg');
  if (winner === 'X') {
    scores.x++;
    msg.innerHTML = '<span style="color:#38bdf8">🎉 پیروز شدی! عالی بود!</span>';
    highlight(line);
    haptic('win');
  } else if (winner === 'O') {
    scores.o++;
    msg.innerHTML = '<span style="color:#f43f5e">🤖 میوویا برنده شد!</span>';
    highlight(line);
    haptic('loss');
  } else {
    msg.innerHTML = '<span style="color:#cbd5e1">🤝 مساوی شد! یک دست دیگه؟</span>';
  }
  document.getElementById('scX').innerText = scores.x;
  document.getElementById('scO').innerText = scores.o;
}

function highlight(line) {
  if (!line) return;
  const cells = document.querySelectorAll('.cell');
  line.forEach(idx => cells[idx].classList.add('win'));
}

function updateView() {
  const cells = document.querySelectorAll('.cell');
  board.forEach((val, i) => {
    if (val === 'X' && !cells[i].innerHTML) cells[i].innerHTML = X_SVG;
    if (val === 'O' && !cells[i].innerHTML) cells[i].innerHTML = O_SVG;
  });
}

function resetGame(full) {
  board = Array(9).fill('');
  over = false;
  document.getElementById('statusMsg').innerText = 'نوبت شماست! یک خانه را انتخاب کن';
  initGrid();
  haptic('tap');
}

function resetScore() {
  scores = { x: 0, o: 0 };
  document.getElementById('scX').innerText = 0;
  document.getElementById('scO').innerText = 0;
  resetGame(true);
}

initGrid();
</script>
</body>
</html>"""


async def send_xo_widget(chat_id: int, bot_token: str) -> bool:
    """Send the interactive graphical Tic-Tac-Toe widget to the user."""
    url = f"https://api.telegram.org/bot{bot_token}/sendWidget"
    payload = {
        "chat_id": chat_id,
        "html": XO_WIDGET_HTML,
        "caption": "🎮 <b>ویجت تعاملی بازی دوز میوویا (Mewvia XO)</b>\nروی خانه‌ها لمس کنید!",
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

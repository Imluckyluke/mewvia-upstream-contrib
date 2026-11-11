"""Tic-Tac-Toe (XO) game engine with minimax AI and rating math."""

from __future__ import annotations

import random
from typing import Literal

WINNING_COMBINATIONS: list[tuple[int, int, int]] = [
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),  # Rows
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),  # Cols
    (0, 4, 8),
    (2, 4, 6),  # Diagonals
]


def check_winner(board: str) -> tuple[str | None, tuple[int, int, int] | None]:
    """Check if there is a winner.

    Returns:
        (winner, line) where winner is 'X', 'O', 'draw' or None (ongoing).
    """
    for a, b, c in WINNING_COMBINATIONS:
        if board[a] != "_" and board[a] == board[b] == board[c]:
            return board[a], (a, b, c)

    if "_" not in board:
        return "draw", None

    return None, None


def available_moves(board: str) -> list[int]:
    """Return indices of empty cells."""
    return [i for i, c in enumerate(board) if c == "_"]


def _minimax(
    board: list[str],
    depth: int,
    is_maximizing: bool,
    alpha: int,
    beta: int,
) -> int:
    b_str = "".join(board)
    winner, _ = check_winner(b_str)
    if winner == "O":  # Bot
        return 10 - depth
    if winner == "X":  # Player
        return depth - 10
    if winner == "draw":
        return 0

    if is_maximizing:
        max_eval = -100
        for move in available_moves(b_str):
            board[move] = "O"
            ev = _minimax(board, depth + 1, False, alpha, beta)
            board[move] = "_"
            max_eval = max(max_eval, ev)
            alpha = max(alpha, ev)
            if beta <= alpha:
                break
        return max_eval
    else:
        min_eval = 100
        for move in available_moves(b_str):
            board[move] = "X"
            ev = _minimax(board, depth + 1, True, alpha, beta)
            board[move] = "_"
            min_eval = min(min_eval, ev)
            beta = min(beta, ev)
            if beta <= alpha:
                break
        return min_eval


def find_best_move(board: str, difficulty: str = "med") -> int:
    """Choose bot move according to selected difficulty level."""
    empty = available_moves(board)
    if not empty:
        return -1

    # First turn: if center is open, good probability to take it or corner
    if len(empty) == 9:
        return 4
    if len(empty) == 8 and 4 in empty:
        if random.random() < 0.7:
            return 4

    # 1. Easy mode: 50% random move
    if difficulty == "easy":
        if random.random() < 0.5:
            return random.choice(empty)

    # 2. Medium mode: checks immediate win, then immediate block, then 30% random or minimax
    if difficulty == "med":
        # Immediate win for bot?
        b_list = list(board)
        for m in empty:
            b_list[m] = "O"
            if check_winner("".join(b_list))[0] == "O":
                return m
            b_list[m] = "_"

        # Immediate block player?
        for m in empty:
            b_list[m] = "X"
            if check_winner("".join(b_list))[0] == "X":
                return m
            b_list[m] = "_"

        if random.random() < 0.30:
            return random.choice(empty)

    # 3. Hard / Unbeatable (or fallback for med): Full minimax
    best_score = -100
    best_move = empty[0]
    b_list = list(board)

    # Shuffle empty to add variety when multiple moves have equal score
    random_empty = list(empty)
    random.shuffle(random_empty)

    for m in random_empty:
        b_list[m] = "O"
        score = _minimax(b_list, depth=0, is_maximizing=False, alpha=-100, beta=100)
        b_list[m] = "_"
        if score > best_score:
            best_score = score
            best_move = m

    return best_move


def calculate_score_delta(
    outcome: Literal["win", "loss", "draw"],
    difficulty: str,
    current_score: int,
    current_streak: int = 0,
) -> int:
    """Calculate rating delta for a finished game."""
    if outcome == "win":
        base = {"easy": 15, "med": 25, "hard": 40}.get(difficulty, 25)
        # Streak bonus (e.g. 3-streak = +6 pts, capped at +10)
        streak_bonus = min(10, current_streak * 2)
        return base + streak_bonus

    if outcome == "draw":
        return {"easy": 2, "med": 5, "hard": 10}.get(difficulty, 5)

    if outcome == "loss":
        # Don't penalize players if score is at or below 1000
        if current_score <= 1000:
            return 0
        penalty = {"easy": 5, "med": 10, "hard": 15}.get(difficulty, 10)
        return -penalty

    return 0

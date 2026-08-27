"""Игровая логика (чистый ГСЧ)."""
from __future__ import annotations

import random
from typing import Optional

from config import ROULETTE_BLACK, ROULETTE_RED

# ==================== MINER (Сапёр наоборот) ====================
# Классика: ячейка = Мина (проигрыш) или Алмаз (множитель растёт).
# Рост ДЕТЕРМИНИРОВАННЫЙ: каждый алмаз умножает коэффициент на growth.
#   mult(n) = growth^n, с жёстким потолком cap.

MINE = "mine"
GEM = "gem"

MINER_MIN_SIZE = 3
MINER_MAX_SIZE = 30


def generate_miner_board(size: int, mines: int) -> list[str]:
    total = size * size
    mines = max(1, min(mines, total - 2))
    board = [MINE] * mines + [GEM] * (total - mines)
    random.shuffle(board)
    return board


def guarantee_first_safe(board: list[str], idx: int) -> list[str]:
    """Первый ход НИКОГДА не мина."""
    if board[idx] != MINE:
        return board
    for i, c in enumerate(board):
        if i != idx and c != MINE:
            board = list(board)
            board[i], board[idx] = board[idx], board[i]
            break
    return board


def miner_multiplier(opened: int, growth: float = 1.03, cap: float = 100.0) -> float:
    """
    ДЕТЕРМИНИРОВАННЫЙ рост: каждый открытый алмаз умножает коэффициент
    на фиксированный growth. Никакой зависимости от размера поля.

        mult(n) = growth ** n   (с потолком cap)

    growth=1.03:  1 алмаз → ×1.03, 2 → ×1.0609, 3 → ×1.0927,
                  10 → ×1.3439, 50 → ×4.38, 100 → ×19.2 (или cap).
    n=0 → ×1.0 (кэшаут без открытых клеток запрещён).
    """
    if opened <= 0:
        return 1.0
    return float(min(growth ** opened, cap))


# ==================== LADDER ====================
def play_ladder(target_step: int, steps: list[dict]) -> tuple[bool, int, list[tuple[int, bool]]]:
    results: list[tuple[int, bool]] = []
    for s in range(1, target_step + 1):
        chance = float(steps[s - 1]["chance"])
        ok = random.uniform(0, 100) < chance
        results.append((s, ok))
        if not ok:
            return False, s, results
    return True, 0, results


# ==================== HIGH / LOW ====================
def play_highlow(guess: str) -> tuple[bool, int]:
    roll = random.randint(1, 100)
    if roll == 50:
        return False, roll
    if guess == "high":
        return roll > 50, roll
    if guess == "low":
        return roll < 50, roll
    return False, roll


# ==================== BLACKJACK ====================
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]


def new_shoe(decks: int = 6) -> list[tuple[str, str]]:
    shoe = [(r, s) for _ in range(decks) for s in SUITS for r in RANKS]
    random.shuffle(shoe)
    return shoe


def hand_value(hand: list[tuple[str, str]]) -> int:
    total = 0
    aces = 0
    for r, _ in hand:
        if r == "A":
            aces += 1
            total += 11
        elif r in ("J", "Q", "K"):
            total += 10
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def format_hand(hand: list[tuple[str, str]], hide_second: bool = False) -> str:
    if hide_second and len(hand) >= 2:
        return f"{hand[0][0]}{hand[0][1]}  🂠"
    return "  ".join(f"{r}{s}" for r, s in hand)


# ==================== DICE ====================
def play_dice(target: Optional[int] = None) -> tuple[int, bool, float]:
    roll = random.randint(1, 6)
    if target is None:
        return roll, False, 0.0
    win = roll == target
    return roll, win, 5.5 if win else 0.0


# ==================== ROULETTE ====================
def play_roulette(bet_type: str, bet_value: Optional[int] = None) -> dict:
    roll = random.randint(0, 36)
    won = False
    coef = 0.0
    if bet_type == "number" and bet_value is not None:
        won = roll == bet_value
        coef = 35.0 if won else 0.0
    elif bet_type == "red":
        won = roll in ROULETTE_RED
        coef = 2.0 if won else 0.0
    elif bet_type == "black":
        won = roll in ROULETTE_BLACK
        coef = 2.0 if won else 0.0
    elif bet_type == "even":
        won = roll != 0 and roll % 2 == 0
        coef = 2.0 if won else 0.0
    elif bet_type == "odd":
        won = roll % 2 == 1
        coef = 2.0 if won else 0.0
    elif bet_type == "low":
        won = 1 <= roll <= 18
        coef = 2.0 if won else 0.0
    elif bet_type == "high":
        won = 19 <= roll <= 36
        coef = 2.0 if won else 0.0
    return {"roll": roll, "won": won, "coef": coef}


def roulette_color(roll: int) -> str:
    if roll == 0:
        return "🟢"
    return "🔴" if roll in ROULETTE_RED else "⚫"


# ==================== SLOTS ====================
def spin_slots(symbols: list[str], weights: list[int],
               payouts_3: dict, payouts_2: dict) -> tuple[list[str], float]:
    reels = random.choices(symbols, weights=weights, k=3)
    coef = 0.0
    if reels[0] == reels[1] == reels[2]:
        coef = float(payouts_3.get(reels[0], 0))
    elif reels[0] == reels[1] and reels[0] in payouts_2:
        coef = float(payouts_2[reels[0]])
    return reels, coef

"""tiny-chess-01: a chess engine that fits in 4,096 bytes, packed by makefun compress.

It plays the house gauntlet over the env's HTTP routes: POST /game serves the game
in play, POST /move plays a move, and the loop ends when the env answers "done".

Alpha-beta with iterative deepening, a transposition table, killer moves, a
quiescence search over captures and a check extension, all inside a fixed node
budget so a replay gives the same moves. Every move is checked against the env's
own legal_moves before it is sent.
"""
import json
import os
import sys
from urllib.request import Request, urlopen

ENV = os.environ["MAKEFUN_ENV_URL"]
PIECES = ".pnbrqk"
BUDGET = 30000

N_MOVES = ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2))
K_MOVES = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
R_MOVES = ((1, 0), (-1, 0), (0, 1), (0, -1))
B_MOVES = ((1, 1), (1, -1), (-1, 1), (-1, -1))
Q_MOVES = R_MOVES + B_MOVES
SLIDES = (None, None, None, B_MOVES, R_MOVES, Q_MOVES, None)

VALUE = (0, 100, 320, 330, 500, 900, 0)

class Budget(Exception):
    """The move's node budget is spent; the last whole depth stands."""


def post(path, body):
    """POST a JSON body to the env and return its JSON answer."""
    request = Request(ENV + path, json.dumps(body).encode(),
                      {"content-type": "application/json"})
    with urlopen(request) as response:
        return json.load(response)


def parse(fen):
    """The FEN's position as (board, side to move), board being 64 ints."""
    board = [0] * 64
    rank, file = 7, 0
    for char in fen.split()[0]:
        if char == "/":
            rank -= 1
            file = 0
        elif char < "9":
            file += int(char)
        else:
            board[rank * 8 + file] = PIECES.index(char.lower()) + (8 if char.islower() else 0)
            file += 1
    # The 65th cell holds the castling rights, so a board copy carries them.
    rights = 0
    for flag, bit in (("K", 1), ("Q", 2), ("k", 4), ("q", 8)):
        if flag in fen.split()[2]:
            rights |= bit
    board.append(rights)
    return board, 0 if fen.split()[1] == "w" else 1


def ep_square(fen):
    """The FEN's en passant target as a square index, or None."""
    field = fen.split()
    if len(field) < 4 or field[3] == "-":
        return None
    return "abcdefgh".index(field[3][0]) + 8 * (int(field[3][1]) - 1)


def moves(board, side, ep):
    """Pseudo-legal moves as (from, to, promotion piece type or 0)."""
    found = []
    add = found.append
    for square in range(64):
        piece = board[square]
        if not piece or piece >> 3 != side:
            continue
        kind = piece & 7
        rank, file = square >> 3, square & 7
        if kind == 1:
            step = 8 - 16 * side
            ahead = square + step
            if 0 <= ahead < 64 and not board[ahead]:
                if ahead >> 3 == 7 - 7 * side:
                    add((square, ahead, 5))
                    add((square, ahead, 4))
                    add((square, ahead, 3))
                    add((square, ahead, 2))
                else:
                    add((square, ahead, 0))
                    if rank == 1 + 5 * side and not board[square + 2 * step]:
                        add((square, 2 * step + square, 0))
            for side_step in (-1, 1):
                ahead = square + step + side_step
                if 0 <= ahead < 64 and abs((ahead & 7) - file) == 1:
                    target = board[ahead]
                    if (target and target >> 3 != side) or ahead == ep:
                        add((square, ahead, 0))
        elif kind == 2 or kind == 6:
            for rank_step, file_step in (N_MOVES if kind == 2 else K_MOVES):
                new_rank, new_file = rank + rank_step, file + file_step
                if 0 <= new_rank < 8 and 0 <= new_file < 8:
                    target = new_rank * 8 + new_file
                    if not board[target] or board[target] >> 3 != side:
                        add((square, target, 0))
            if kind == 6:
                found.extend(castles(board, side, square))
        else:
            for rank_step, file_step in SLIDES[kind]:
                new_rank, new_file = rank, file
                while True:
                    new_rank += rank_step
                    new_file += file_step
                    if not (0 <= new_rank < 8 and 0 <= new_file < 8):
                        break
                    target = new_rank * 8 + new_file
                    if board[target]:
                        if board[target] >> 3 != side:
                            add((square, target, 0))
                        break
                    add((square, target, 0))
    return found


def castles(board, side, square):
    """The castling moves available from this king square, if any."""
    home = 4 if side == 0 else 60
    if square != home or attacked(board, home, 1 - side):
        return []
    found = []
    rook = 8 * side + 4
    rights = board[64]
    for step, corner, between, bit in ((2, 3, (1, 2), 1 << (2 * side)),
                                       (-2, -4, (-1, -2, -3), 2 << (2 * side))):
        if not rights & bit or board[home + corner] != rook:
            continue
        path = [home + offset for offset in between]
        if any(board[s] for s in path):
            continue
        if any(attacked(board, s, 1 - side) for s in path[:2]):
            continue
        found.append((home, home + step, 0))
    return found


def make(board, move):
    """The board after the move."""
    source, target, promotion = move
    piece = board[source]
    after = board[:]
    kind = piece & 7
    side = piece >> 3
    # Drop the rights this move ends: the king's, a rook's from its corner, and
    # the opponent's on a corner this move captures on.
    rights = after[64]
    for square in (source, target):
        for cell, mask in ((4, 3), (7, 1), (0, 2), (60, 12), (63, 4), (56, 8)):
            if square == cell:
                rights &= ~mask
    after[64] = rights
    if kind == 1 and abs(target - source) % 8 == 1 and not board[target]:
        after[target + (8 if piece >> 3 == 0 else -8)] = 0
    elif kind == 6 and abs(target - source) == 2:
        corner = target + 1 if target > source else target - 2
        after[target - 1 if target > source else target + 1] = after[corner]
        after[corner] = 0
    after[target] = promotion if promotion else piece
    after[source] = 0
    return after


def king_square(board, side):
    """Where this side's king stands."""
    wanted = 8 * side + 6
    for square in range(64):
        if board[square] == wanted:
            return square
    return 0


def attacked(board, square, by_side):
    """Whether the by_side pieces attack this square."""
    rank, file = square >> 3, square & 7
    base = 8 * by_side
    for rank_step, file_step in N_MOVES:
        new_rank, new_file = rank + rank_step, file + file_step
        if 0 <= new_rank < 8 and 0 <= new_file < 8:
            if board[new_rank * 8 + new_file] == base + 2:
                return True
    for rank_step, file_step in K_MOVES:
        new_rank, new_file = rank + rank_step, file + file_step
        if 0 <= new_rank < 8 and 0 <= new_file < 8:
            if board[new_rank * 8 + new_file] == base + 6:
                return True
    pawn_rank = rank + (2 * by_side - 1)
    for file_step in (-1, 1):
        new_file = file + file_step
        if 0 <= pawn_rank < 8 and 0 <= new_file < 8:
            if board[pawn_rank * 8 + new_file] == base + 1:
                return True
    for rank_step, file_step in Q_MOVES:
        new_rank, new_file = rank, file
        while True:
            new_rank += rank_step
            new_file += file_step
            if not (0 <= new_rank < 8 and 0 <= new_file < 8):
                break
            piece = board[new_rank * 8 + new_file]
            if piece:
                if piece == base + 5:
                    return True
                if piece == base + (4 if rank_step == 0 or file_step == 0 else 3):
                    return True
                break
    return False


def evaluate(board):
    """Material and position, from white's point of view."""
    score = 0
    bishops = [0, 0]
    for square in range(64):
        piece = board[square]
        if not piece:
            continue
        kind = piece & 7
        side = piece >> 3
        rank, file = square >> 3, square & 7
        if side:
            rank, file = 7 - rank, 7 - file
        if kind == 6:
            # A king belongs on its back rank, and best of all in a corner.
            value = -18 * rank
            if rank == 0 and file in (2, 6):
                value += 12
        else:
            value = VALUE[kind]
            centre = 4 - abs(2 * file - 7) - abs(2 * rank - 7)
            if kind == 1:
                value += rank * 2 + centre // 2
            elif kind == 2 or kind == 3:
                value += centre
            elif kind == 4:
                value += 6 if rank == 6 else 0
            if kind == 3:
                bishops[side] += 1
        score += value if side == 0 else -value
    for side in (0, 1):
        if bishops[side] > 1:
            score += 30 if side == 0 else -30
    return score


nodes = 0
table = {}
killers = [[None, None] for _ in range(256)]


def quiesce(board, side, alpha, beta):
    """Search captures and promotions until the position is quiet."""
    global nodes
    nodes += 1
    if nodes > BUDGET:
        raise Budget
    raw = evaluate(board)
    score = raw if side == 0 else -raw
    if score >= beta:
        return score
    if score > alpha:
        alpha = score
    for move in moves(board, side, None):
        if not (move[2] or board[move[1]]):
            continue
        after = make(board, move)
        if attacked(after, king_square(after, side), 1 - side):
            continue
        score = -quiesce(after, 1 - side, -beta, -alpha)
        if score >= beta:
            return score
        if score > alpha:
            alpha = score
    return alpha


def search(board, side, ep, depth, alpha, beta, ply):
    """Alpha-beta, returning the score of the side to move."""
    global nodes
    nodes += 1
    if nodes > BUDGET:
        raise Budget
    if ply > 200:
        raise Budget
    in_check = attacked(board, king_square(board, side), 1 - side)
    if in_check and ply < 32:
        depth += 1
    if depth < 1:
        return quiesce(board, side, alpha, beta)

    key = (bytes(board), side)
    cached = table.get(key)
    if cached and cached[0] >= depth:
        if cached[1] == 0 or (cached[1] == 1 and cached[2] <= alpha) or (cached[1] == 2 and cached[2] >= beta):
            return cached[2]

    legal = []
    for move in moves(board, side, ep):
        after = make(board, move)
        if attacked(after, king_square(after, side), 1 - side):
            continue
        legal.append(move)
    if not legal:
        return -90000 + ply if in_check else 0

    first = killers[ply][0]
    legal.sort(key=lambda m: (m != first, -(VALUE[board[m[1]] & 7] if board[m[1]] else 0), m[0]))

    best = -99999
    for move in legal:
        after = make(board, move)
        gives_check = attacked(after, king_square(after, 1 - side), side)
        score = -search(after, 1 - side,
                        (move[0] + move[1]) >> 1 if board[move[0]] & 7 == 1 and abs(move[1] - move[0]) == 16 else None,
                        depth - 1 + (1 if gives_check else 0), -beta, -alpha, ply + 1)
        if score > best:
            best = score
        if score > alpha:
            alpha = score
        if alpha >= beta:
            if not (move[2] or board[move[1]]):
                killers[ply][1] = killers[ply][0]
                killers[ply][0] = move
            break

    if len(table) < 400000:
        table[key] = (depth, 0 if alpha < best < beta else (1 if best <= alpha else 2), best)
    return best if best > alpha else alpha


def choose(board, side, ep):
    """The best move found within the node budget."""
    global nodes
    legal = []
    for move in moves(board, side, ep):
        after = make(board, move)
        if not attacked(after, king_square(after, side), 1 - side):
            legal.append(move)
    if not legal:
        return None
    best = legal[0]
    for depth in range(1, 7):
        nodes = 0
        candidate = best
        score = -99999
        try:
            for move in legal:
                after = make(board, move)
                value = -search(after, 1 - side,
                                (move[0] + move[1]) >> 1 if board[move[0]] & 7 == 1 and abs(move[1] - move[0]) == 16 else None,
                                depth - 1, -99999, -score, 1)
                if value > score:
                    score = value
                    candidate = move
            best = candidate
        except Budget:
            break
    return best


def uci(move):
    """The env's spelling of a move."""
    text = "abcdefgh"[move[0] & 7] + str((move[0] >> 3) + 1)
    text += "abcdefgh"[move[1] & 7] + str((move[1] >> 3) + 1)
    return text + ("nbrq"[move[2] - 2] if move[2] else "")


def play():
    """Play every game the env serves, one after another."""
    game = post("/game", {})
    while game.get("status") != "done":
        while game.get("status") == "your_move":
            legal_text = game["legal_moves"]
            board, side = parse(game["fen"])
            move = choose(board, side, ep_square(game["fen"]))
            text = uci(move) if move else None
            if text not in legal_text:
                text = legal_text[0]
            game = post("/move", {"game": game["game"], "move": text})
        game = post("/game", {})


play()

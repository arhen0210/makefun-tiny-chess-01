# tiny-chess-01

A chess engine for the make.fun tiny-chess-01 entry. It plays the house gauntlet
over the env's HTTP routes (`POST /game`, `POST /move`) using alpha-beta search
with iterative deepening, a transposition table, killer moves, a quiescence
search over captures, and a check extension — all bounded by a fixed node
budget (30,000 nodes) so a replay produces the same moves. Every chosen move
is validated against the env's own `legal_moves` before it is sent, falling
back to the first legal move if anything ever disagrees. The readable,
commented source lives in `engine.py` (13,790 bytes).

The actual entry is `main.py`, which must fit in 4,096 bytes. It is produced
by packing `engine.py` with the makefun CLI:

    node /home/ubuntu/makefun2/tiny-chess-01/tiny-chess-01-kit/cli/makefun.mjs \
        compress engine.py --out main.py

This yields a 3,885-byte `main.py` that reproduces the engine exactly. After
any change to `engine.py`, rerun the compress command so `main.py` and the
source stay in sync; the engine's logic and thresholds should not be edited
otherwise.

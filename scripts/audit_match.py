#!/usr/bin/env python3
"""Independently replay a completed Fastchess match and verify opening pairs.

target/testing/venv/bin/python scripts/audit_match.py logs/sprt/RUN
Requires the python-chess dependency installed by setup_testing.py.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import chess.pgn
from sprt import dump


def audit(run):
    schedule = (run / 'openings.epd').read_text().splitlines()
    allowed = set(schedule)
    if len(allowed) != len(schedule):
        raise ValueError('The saved schedule contains duplicate positions')
    pairs = defaultdict(list)
    games = draws = plies = 0
    with (run / 'games.pgn').open() as stream:
        while True:
            game = chess.pgn.read_game(stream)
            if game is None:
                break
            if game.errors:
                raise ValueError(f'PGN parse/legality errors: {game.errors}')
            board = game.board()
            root = board.epd(en_passant='legal')
            if root not in allowed:
                raise ValueError(f'Game starts outside the saved opening schedule: {root}')
            white, black = game.headers['White'], game.headers['Black']
            if {white, black} != {'new', 'old'}:
                raise ValueError(f'Unexpected players: {white}, {black}')
            result = game.headers['Result']
            if result not in ('1-0', '0-1', '1/2-1/2'):
                raise ValueError(f'Incomplete game: {result}')
            for move in game.mainline_moves():
                if move not in board.legal_moves:
                    raise ValueError(f'Illegal move: {move}')
                board.push(move)
                plies += 1
            outcome = board.outcome(claim_draw=True)
            if outcome is None or outcome.result() != result:
                raise ValueError(f'Non-board result (forfeit/adjudication?) in game {games+1}: '
                                 f'{dict(game.headers)}')
            score_white = {'1-0': 1, '0-1': 0, '1/2-1/2': .5}[result]
            pairs[root].append((white, score_white if white == 'new' else 1-score_white))
            games += 1
            draws += result == '1/2-1/2'
    penta = [0] * 5
    for root, pair in pairs.items():
        if len(pair) != 2 or {white for white, _ in pair} != {'new', 'old'}:
            raise ValueError(f'Missing, repeated or incorrectly colour-swapped pair: {root}: {pair}')
        penta[int(2 * sum(score for _, score in pair))] += 1
    if not games:
        raise ValueError('No games to audit')
    report = dict(games=games, distinct_opening_pairs=len(pairs), pentanomial=penta,
                  draws=draws, draw_fraction=draws/games, mean_played_plies=plies/games,
                  verification='All PGNs replay legally, board outcomes agree, roots match schedule, exactly two colour-swapped games per root')
    dump(run / 'audit.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.run.resolve()), indent=2))

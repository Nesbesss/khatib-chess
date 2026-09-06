#!/usr/bin/env python3
"""Build pinned Fastchess and validate/deduplicate Stockfish's 8moves_v3 book."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'target/testing'
FASTCHESS_REV = '4e691463cee6a5c38b63525db57e7b7e66c2cbf7'
BOOK_REV = '65815ccdbc7727cd4f6aee252ba8f67fb740e92f'
BOOK_URL = f'https://raw.githubusercontent.com/official-stockfish/books/{BOOK_REV}/8moves_v3.pgn.zip'


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def prepare_book():
    import chess.pgn
    directory = ROOT / 'data/books'
    directory.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(BOOK_URL, timeout=60) as response:
        archive = response.read()
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        name, = [n for n in zipped.namelist() if n.endswith('.pgn')]
        pgn = io.StringIO(zipped.read(name).decode('utf-8-sig'))
    seen = set()
    positions = []
    total = 0
    while True:
        game = chess.pgn.read_game(pgn)
        if game is None:
            break
        if game.errors:
            raise ValueError(f'Invalid PGN game {total + 1}: {game.errors}')
        board = game.end().board()
        if not board.is_valid() or board.is_game_over() or game.end().ply() != 16:
            raise ValueError(f'Invalid opening at game {total + 1}')
        # Canonical legal EP rights; counters do not distinguish opening pairs.
        epd = board.epd(en_passant='legal')
        total += 1
        if epd not in seen:
            seen.add(epd)
            positions.append(epd)
    if len(positions) < 20000:
        raise ValueError(f'Only {len(positions)} unique opening positions')
    book = directory / '8moves_v3.unique.epd'
    book.write_text('\n'.join(positions) + '\n')
    metadata = dict(source=BOOK_URL, source_commit=BOOK_REV, source_lines=total,
                    unique_positions=len(positions), duplicate_positions=total-len(positions),
                    archive_sha256=hashlib.sha256(archive).hexdigest(),
                    epd_sha256=hashlib.sha256(book.read_bytes()).hexdigest(),
                    validation='python-chess 1.11.2: legal PGN, valid nonterminal boards, 16 plies',
                    license='CC0-1.0; https://github.com/official-stockfish/books/blob/' + BOOK_REV + '/LICENSE')
    (directory / '8moves_v3.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps(metadata, indent=2))


def main():
    if sys.argv[1:] == ['--book-only']:
        prepare_book()
        return
    DEST.mkdir(parents=True, exist_ok=True)
    source = DEST / 'fastchess-src'
    if not source.exists():
        run('git', 'clone', 'https://github.com/Disservin/fastchess.git', source)
    run('git', '-C', source, 'checkout', '--detach', FASTCHESS_REV)
    run('make', '-C', source, '-j4', 'CXX=clang++')
    shutil.copy2(source / 'fastchess', DEST / 'fastchess')
    (DEST / 'fastchess-revision.txt').write_text(FASTCHESS_REV + '\n')
    venv = DEST / 'venv'
    if not venv.exists():
        run(sys.executable, '-m', 'venv', venv)
    python = venv / 'bin/python'
    run(python, '-m', 'pip', 'install', 'chess==1.11.2')
    run(python, Path(__file__).resolve(), '--book-only')
    run(DEST / 'fastchess', '-version')


if __name__ == '__main__':
    main()

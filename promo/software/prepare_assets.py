"""Original promo UI assets, using real Khatib analysis and vector chess pieces."""
import json
import urllib.parse
import urllib.request
from pathlib import Path

import chess
import chess.svg
import cairosvg

P = Path(__file__).resolve().parent
A = P / 'assets'
A.mkdir(exist_ok=True)
fen = 'r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1'
url = 'http://127.0.0.1:8093/analyze?' + urllib.parse.urlencode({'fen': fen, 'ms': 5000})
raw = urllib.request.urlopen(url, timeout=30).read().decode()
(A / 'khatib-analysis.sse').write_text(raw)
events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: ')]
samples = [e for e in events if 'depth' in e and 'pv' in e]
best = samples[-1]
board = chess.Board(fen)
positions = [board.fen()]
san = []
for uci in best['pv'][:8]:
    move = chess.Move.from_uci(uci)
    if move not in board.legal_moves:
        break
    san.append(board.san(move))
    board.push(move)
    positions.append(board.fen())
(A / 'analysis.json').write_text(json.dumps({'fen': fen, 'sample': best, 'san': san, 'positions': positions}, indent=2))
for sym in 'PNBRQKpnbrqk':
    svg = chess.svg.piece(chess.Piece.from_symbol(sym), size=512)
    filename = ('white-' if sym.isupper() else 'black-') + sym.lower()
    (A / (filename + '.svg')).write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(A / (filename + '.png')), output_width=512, output_height=512)
print('Generated 12 vector piece textures; captured actual Khatib depth', best['depth'], 'PV', san)

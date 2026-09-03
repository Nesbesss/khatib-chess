// Position representation, FEN, make/unmake, attack queries.
use crate::attacks::*;
use crate::types::*;
use std::sync::OnceLock;

pub struct Zobrist {
    pub pieces: [[[u64; 64]; 6]; 2],
    pub castling: [u64; 16],
    pub ep_file: [u64; 8],
    pub side: u64,
}

fn build_zobrist() -> Zobrist {
    // Same xorshift as attacks.rs; deterministic keys across runs.
    let mut s = 0x123456789ABCDEFu64;
    let mut next = || {
        s ^= s >> 12; s ^= s << 25; s ^= s >> 27;
        s.wrapping_mul(0x2545F4914F6CDD1D)
    };
    let mut pieces = [[[0u64; 64]; 6]; 2];
    for c in 0..2 { for p in 0..6 { for sq in 0..64 {
        pieces[c][p][sq] = next();
    }}}
    let mut castling = [0u64; 16];
    for i in 0..16 { castling[i] = next(); }
    let mut ep_file = [0u64; 8];
    for i in 0..8 { ep_file[i] = next(); }
    let side = next();
    Zobrist { pieces, castling, ep_file, side }
}

static ZOBRIST: OnceLock<Zobrist> = OnceLock::new();
#[inline(always)]
pub fn zobrist() -> &'static Zobrist { ZOBRIST.get_or_init(build_zobrist) }

// State that make_move destroys and unmake_move must restore.
#[derive(Copy, Clone)]
pub struct Undo {
    pub captured: Option<Piece>,
    pub castling: u8,
    pub ep_square: Option<u8>,
    pub halfmove: u16,
    pub hash: u64,
}

#[derive(Clone)]
pub struct Board {
    // pieces[color][piece_kind]
    pub pieces: [[Bitboard; 6]; 2],
    pub occupied: [Bitboard; 2],
    pub all: Bitboard,
    // Redundant mailbox: make/unmake and SEE need square -> piece fast.
    pub mailbox: [Option<(Color, Piece)>; 64],
    pub side: Color,
    pub castling: u8,
    pub ep_square: Option<u8>,
    pub halfmove: u16,
    pub fullmove: u16,
    pub hash: u64,
}

pub const START_FEN: &str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

impl Board {
    pub fn empty() -> Board {
        Board {
            pieces: [[0; 6]; 2],
            occupied: [0; 2],
            all: 0,
            mailbox: [None; 64],
            side: Color::White,
            castling: 0,
            ep_square: None,
            halfmove: 0,
            fullmove: 1,
            hash: 0,
        }
    }

    pub fn startpos() -> Board { Board::from_fen(START_FEN).unwrap() }

    #[inline(always)]
    pub fn piece_at(&self, sq: u8) -> Option<(Color, Piece)> { self.mailbox[sq as usize] }

    #[inline(always)]
    pub fn king_square(&self, c: Color) -> u8 {
        self.pieces[c.idx()][Piece::King.idx()].trailing_zeros() as u8
    }

    #[inline(always)]
    fn put(&mut self, sq: u8, color: Color, piece: Piece) {
        let b = bb(sq);
        self.pieces[color.idx()][piece.idx()] |= b;
        self.occupied[color.idx()] |= b;
        self.all |= b;
        self.mailbox[sq as usize] = Some((color, piece));
        self.hash ^= zobrist().pieces[color.idx()][piece.idx()][sq as usize];
    }

    #[inline(always)]
    fn remove(&mut self, sq: u8, color: Color, piece: Piece) {
        let b = bb(sq);
        self.pieces[color.idx()][piece.idx()] &= !b;
        self.occupied[color.idx()] &= !b;
        self.all &= !b;
        self.mailbox[sq as usize] = None;
        self.hash ^= zobrist().pieces[color.idx()][piece.idx()][sq as usize];
    }

    #[inline(always)]
    fn move_piece(&mut self, from: u8, to: u8, color: Color, piece: Piece) {
        self.remove(from, color, piece);
        self.put(to, color, piece);
    }

    pub fn from_fen(fen: &str) -> Result<Board, String> {
        let parts: Vec<&str> = fen.split_whitespace().collect();
        if parts.len() < 4 {
            return Err(format!("FEN needs at least 4 fields, got {}", parts.len()));
        }
        let mut b = Board::empty();

        // Ranks come highest-first in FEN.
        let mut rank = 7i8;
        let mut file = 0i8;
        for c in parts[0].chars() {
            match c {
                '/' => {
                    if file != 8 { return Err(format!("rank {} has {} files", rank, file)); }
                    rank -= 1;
                    file = 0;
                    if rank < 0 { return Err("too many ranks".into()); }
                }
                '1'..='8' => file += c.to_digit(10).unwrap() as i8,
                _ => {
                    let (color, piece) = Piece::from_char(c)
                        .ok_or(format!("bad piece char '{}'", c))?;
                    if file > 7 || rank < 0 { return Err("piece outside board".into()); }
                    b.put(square(file as u8, rank as u8), color, piece);
                    file += 1;
                }
            }
        }
        if rank != 0 || file != 8 { return Err("incomplete board".into()); }

        b.side = match parts[1] {
            "w" => Color::White,
            "b" => Color::Black,
            s => return Err(format!("bad side '{}'", s)),
        };

        if parts[2] != "-" {
            for c in parts[2].chars() {
                b.castling |= match c {
                    'K' => CASTLE_WK, 'Q' => CASTLE_WQ,
                    'k' => CASTLE_BK, 'q' => CASTLE_BQ,
                    _ => return Err(format!("bad castling char '{}'", c)),
                };
            }
        }

        if parts[3] != "-" {
            b.ep_square = Some(parse_square(parts[3])
                .ok_or(format!("bad ep square '{}'", parts[3]))?);
        }

        b.halfmove = parts.get(4).and_then(|s| s.parse().ok()).unwrap_or(0);
        b.fullmove = parts.get(5).and_then(|s| s.parse().ok()).unwrap_or(1);

        b.hash ^= zobrist().castling[b.castling as usize];
        if let Some(ep) = b.ep_square { b.hash ^= zobrist().ep_file[file_of(ep) as usize]; }
        if b.side == Color::Black { b.hash ^= zobrist().side; }

        Ok(b)
    }

    pub fn to_fen(&self) -> String {
        let mut s = String::new();
        for rank in (0..8).rev() {
            let mut empty = 0;
            for file in 0..8 {
                match self.piece_at(square(file, rank)) {
                    Some((c, p)) => {
                        if empty > 0 { s.push_str(&empty.to_string()); empty = 0; }
                        s.push(p.to_char(c));
                    }
                    None => empty += 1,
                }
            }
            if empty > 0 { s.push_str(&empty.to_string()); }
            if rank > 0 { s.push('/'); }
        }
        s.push(' ');
        s.push(if self.side == Color::White { 'w' } else { 'b' });
        s.push(' ');
        if self.castling == 0 {
            s.push('-');
        } else {
            if self.castling & CASTLE_WK != 0 { s.push('K'); }
            if self.castling & CASTLE_WQ != 0 { s.push('Q'); }
            if self.castling & CASTLE_BK != 0 { s.push('k'); }
            if self.castling & CASTLE_BQ != 0 { s.push('q'); }
        }
        s.push(' ');
        match self.ep_square {
            Some(sq) => s.push_str(&square_name(sq)),
            None => s.push('-'),
        }
        format!("{} {} {}", s, self.halfmove, self.fullmove)
    }

    // Every piece of `by` that attacks `sq`, given occupancy `occ`.
    // Passing occ explicitly lets SEE re-query with pieces removed.
    pub fn attackers_to(&self, sq: u8, by: Color, occ: Bitboard) -> Bitboard {
        let p = &self.pieces[by.idx()];
        // Pawn attacks are symmetric: squares attacking sq are the squares
        // sq would attack as a pawn of the opposite color.
        (pawn_attacks(by.flip(), sq) & p[Piece::Pawn.idx()])
            | (knight_attacks(sq) & p[Piece::Knight.idx()])
            | (king_attacks(sq) & p[Piece::King.idx()])
            | (bishop_attacks(sq, occ) & (p[Piece::Bishop.idx()] | p[Piece::Queen.idx()]))
            | (rook_attacks(sq, occ) & (p[Piece::Rook.idx()] | p[Piece::Queen.idx()]))
    }

    #[inline(always)]
    pub fn is_attacked(&self, sq: u8, by: Color) -> bool {
        self.attackers_to(sq, by, self.all) != 0
    }

    #[inline(always)]
    pub fn in_check(&self, c: Color) -> bool {
        self.is_attacked(self.king_square(c), c.flip())
    }

    #[inline(always)]
    pub fn checkers(&self) -> Bitboard {
        self.attackers_to(self.king_square(self.side), self.side.flip(), self.all)
    }

    pub fn make_move(&mut self, m: Move) -> Undo {
        let z = zobrist();
        let undo = Undo {
            captured: None,
            castling: self.castling,
            ep_square: self.ep_square,
            halfmove: self.halfmove,
            hash: self.hash,
        };

        let us = self.side;
        let them = us.flip();
        let from = m.from();
        let to = m.to();
        let (_, piece) = self.mailbox[from as usize].expect("move from empty square");

        // Clear old ep/castling contributions; re-add updated ones at the end.
        if let Some(ep) = self.ep_square { self.hash ^= z.ep_file[file_of(ep) as usize]; }
        self.hash ^= z.castling[self.castling as usize];

        self.halfmove += 1;
        let mut captured = None;

        if m.is_ep() {
            // The captured pawn sits beside the destination, not on it.
            let cap_sq = if us == Color::White { to - 8 } else { to + 8 };
            self.remove(cap_sq, them, Piece::Pawn);
            captured = Some(Piece::Pawn);
            self.halfmove = 0;
        } else if m.is_capture() {
            let (_, cp) = self.mailbox[to as usize].expect("capture of empty square");
            self.remove(to, them, cp);
            captured = Some(cp);
            self.halfmove = 0;
        }

        if piece == Piece::Pawn { self.halfmove = 0; }

        if m.is_promotion() {
            self.remove(from, us, Piece::Pawn);
            self.put(to, us, m.promo_piece());
        } else {
            self.move_piece(from, to, us, piece);
        }

        if m.is_castle() {
            // King has moved; bring the rook around.
            let (rook_from, rook_to) = match (us, m.flag()) {
                (Color::White, FLAG_CASTLE_KING) => (H1, H1 - 2),
                (Color::White, _) => (A1, A1 + 3),
                (Color::Black, FLAG_CASTLE_KING) => (H8, H8 - 2),
                (Color::Black, _) => (A8, A8 + 3),
            };
            self.move_piece(rook_from, rook_to, us, Piece::Rook);
        }

        // Any king or rook movement, and any capture on a rook's home square,
        // can revoke castling rights.
        if piece == Piece::King {
            self.castling &= if us == Color::White { !(CASTLE_WK | CASTLE_WQ) }
                             else { !(CASTLE_BK | CASTLE_BQ) };
        }
        for sq in [from, to] {
            self.castling &= match sq {
                H1 => !CASTLE_WK, A1 => !CASTLE_WQ,
                H8 => !CASTLE_BK, A8 => !CASTLE_BQ,
                _ => !0,
            };
        }

        self.ep_square = if m.flag() == FLAG_DOUBLE_PAWN {
            Some(if us == Color::White { to - 8 } else { to + 8 })
        } else {
            None
        };

        self.hash ^= z.castling[self.castling as usize];
        if let Some(ep) = self.ep_square { self.hash ^= z.ep_file[file_of(ep) as usize]; }
        self.hash ^= z.side;

        if us == Color::Black { self.fullmove += 1; }
        self.side = them;

        Undo { captured, ..undo }
    }

    pub fn unmake_move(&mut self, m: Move, undo: Undo) {
        let us = self.side.flip(); // side that made the move
        let them = self.side;
        let from = m.from();
        let to = m.to();

        if us == Color::Black { self.fullmove -= 1; }
        self.side = us;

        if m.is_promotion() {
            self.remove(to, us, m.promo_piece());
            self.put(from, us, Piece::Pawn);
        } else {
            let (_, piece) = self.mailbox[to as usize].expect("unmake from empty square");
            self.move_piece(to, from, us, piece);
        }

        if m.is_castle() {
            let (rook_from, rook_to) = match (us, m.flag()) {
                (Color::White, FLAG_CASTLE_KING) => (H1, H1 - 2),
                (Color::White, _) => (A1, A1 + 3),
                (Color::Black, FLAG_CASTLE_KING) => (H8, H8 - 2),
                (Color::Black, _) => (A8, A8 + 3),
            };
            self.move_piece(rook_to, rook_from, us, Piece::Rook);
        }

        if let Some(cp) = undo.captured {
            let cap_sq = if m.is_ep() {
                if us == Color::White { to - 8 } else { to + 8 }
            } else { to };
            self.put(cap_sq, them, cp);
        }

        // Hash was saved wholesale; the put/remove calls above have been
        // scribbling on it, so restore it directly.
        self.castling = undo.castling;
        self.ep_square = undo.ep_square;
        self.halfmove = undo.halfmove;
        self.hash = undo.hash;
    }
}

// Fully legal move generation: pins and checks resolved up front, so every
// move emitted is legal without a make/unmake verification pass.
use crate::attacks::*;
use crate::board::Board;
use crate::types::*;

// Max legal moves in any reachable position is 218; 256 gives headroom.
pub const MAX_MOVES: usize = 256;

pub struct MoveList {
    pub moves: [Move; MAX_MOVES],
    pub len: usize,
}

impl MoveList {
    #[inline(always)]
    pub fn new() -> MoveList {
        MoveList { moves: [Move::NULL; MAX_MOVES], len: 0 }
    }
    #[inline(always)]
    pub fn push(&mut self, m: Move) {
        self.moves[self.len] = m;
        self.len += 1;
    }
    #[inline(always)]
    pub fn as_slice(&self) -> &[Move] { &self.moves[..self.len] }
}

impl std::ops::Index<usize> for MoveList {
    type Output = Move;
    #[inline(always)]
    fn index(&self, i: usize) -> &Move { &self.moves[i] }
}

#[derive(Copy, Clone, PartialEq)]
pub enum GenMode { All, Captures }

pub fn generate(board: &Board, mode: GenMode) -> MoveList {
    let mut list = MoveList::new();
    let us = board.side;
    let them = us.flip();
    let ksq = board.king_square(us);
    let occ = board.all;
    let own = board.occupied[us.idx()];
    // The enemy king is never a legal capture target. Standard perft suites
    // never expose this (no test position lets a piece capture a king), but
    // the search reaches such positions, and capturing the king silently
    // removed it from the board.
    let enemy = board.occupied[them.idx()] & !board.pieces[them.idx()][Piece::King.idx()];

    let checkers = board.attackers_to(ksq, them, occ);
    let num_checkers = checkers.count_ones();

    // Under double check only the king may move.
    if num_checkers >= 2 {
        gen_king(board, &mut list, ksq, us, mode);
        return list;
    }

    // Squares a non-king move may land on. Under single check we must capture
    // the checker or interpose on the check ray.
    let target = if num_checkers == 1 {
        let checker_sq = checkers.trailing_zeros() as u8;
        checkers | between(ksq, checker_sq)
    } else {
        !0u64
    };
    // Exclude the enemy king from every destination: it is never capturable,
    // and generating such a move silently removed the king from the board.
    // `enemy` already excludes it; `!own` does not.
    let no_king = !board.pieces[them.idx()][Piece::King.idx()];
    let target = match mode {
        GenMode::All => target & !own & no_king,
        GenMode::Captures => target & enemy,
    };

    // Pinned pieces may only move along the pin ray. Find them by looking for
    // enemy sliders that would attack the king if one blocker were removed.
    let mut pinned = 0u64;
    let bq = board.pieces[them.idx()][Piece::Bishop.idx()]
        | board.pieces[them.idx()][Piece::Queen.idx()];
    let rq = board.pieces[them.idx()][Piece::Rook.idx()]
        | board.pieces[them.idx()][Piece::Queen.idx()];
    let mut snipers = (bishop_attacks(ksq, enemy) & bq) | (rook_attacks(ksq, enemy) & rq);
    while snipers != 0 {
        let sniper_sq = snipers.trailing_zeros() as u8;
        snipers &= snipers - 1;
        let blockers = between(ksq, sniper_sq) & occ;
        // Exactly one blocker, and it's ours => pinned.
        if blockers.count_ones() == 1 && blockers & own != 0 {
            pinned |= blockers;
        }
    }

    gen_pawns(board, &mut list, us, target, pinned, ksq, mode);

    for piece in [Piece::Knight, Piece::Bishop, Piece::Rook, Piece::Queen] {
        let mut bbs = board.pieces[us.idx()][piece.idx()];
        while bbs != 0 {
            let from = bbs.trailing_zeros() as u8;
            bbs &= bbs - 1;
            let mut att = piece_attacks(piece, from, occ) & target;
            // A pinned piece stays on the king-pinner line. Knights can never
            // move while pinned, which falls out of this mask being empty.
            if pinned & bb(from) != 0 {
                att &= line(ksq, from);
            }
            while att != 0 {
                let to = att.trailing_zeros() as u8;
                att &= att - 1;
                let flag = if enemy & bb(to) != 0 { FLAG_CAPTURE } else { FLAG_QUIET };
                list.push(Move::new(from, to, flag));
            }
        }
    }

    gen_king(board, &mut list, ksq, us, mode);

    if mode == GenMode::All && num_checkers == 0 {
        gen_castles(board, &mut list, us, ksq);
    }

    list
}

fn gen_king(board: &Board, list: &mut MoveList, ksq: u8, us: Color, mode: GenMode) {
    let them = us.flip();
    let own = board.occupied[us.idx()];
    // The enemy king is never a legal capture target. Standard perft suites
    // never expose this (no test position lets a piece capture a king), but
    // the search reaches such positions, and capturing the king silently
    // removed it from the board.
    let enemy = board.occupied[them.idx()] & !board.pieces[them.idx()][Piece::King.idx()];
    let targets = match mode {
        GenMode::All => !own,
        GenMode::Captures => enemy,
    };
    // Remove the king before testing destinations: it must not shield itself
    // from a slider it is fleeing along the ray of.
    let occ_no_king = board.all & !bb(ksq);
    let mut att = king_attacks(ksq) & targets;
    while att != 0 {
        let to = att.trailing_zeros() as u8;
        att &= att - 1;
        if board.attackers_to(to, them, occ_no_king) != 0 { continue; }
        let flag = if enemy & bb(to) != 0 { FLAG_CAPTURE } else { FLAG_QUIET };
        list.push(Move::new(ksq, to, flag));
    }
}

fn gen_castles(board: &Board, list: &mut MoveList, us: Color, ksq: u8) {
    let them = us.flip();
    let (kside, qside) = match us {
        Color::White => (CASTLE_WK, CASTLE_WQ),
        Color::Black => (CASTLE_BK, CASTLE_BQ),
    };
    let back = if us == Color::White { 0 } else { 56 };

    // King side: f1/g1 empty, e1/f1/g1 not attacked.
    if board.castling & kside != 0 {
        let f = back + 5;
        let g = back + 6;
        if board.all & (bb(f) | bb(g)) == 0
            && !board.is_attacked(f, them)
            && !board.is_attacked(g, them)
        {
            list.push(Move::new(ksq, g, FLAG_CASTLE_KING));
        }
    }
    // Queen side: b1/c1/d1 empty, e1/d1/c1 not attacked (b1 may be attacked).
    if board.castling & qside != 0 {
        let b = back + 1;
        let c = back + 2;
        let d = back + 3;
        if board.all & (bb(b) | bb(c) | bb(d)) == 0
            && !board.is_attacked(c, them)
            && !board.is_attacked(d, them)
        {
            list.push(Move::new(ksq, c, FLAG_CASTLE_QUEEN));
        }
    }
}

fn gen_pawns(
    board: &Board, list: &mut MoveList, us: Color,
    target: Bitboard, pinned: Bitboard, ksq: u8, mode: GenMode,
) {
    let them = us.flip();
    let pawns = board.pieces[us.idx()][Piece::Pawn.idx()];
    // The enemy king is never a legal capture target. Standard perft suites
    // never expose this (no test position lets a piece capture a king), but
    // the search reaches such positions, and capturing the king silently
    // removed it from the board.
    let enemy = board.occupied[them.idx()] & !board.pieces[them.idx()][Piece::King.idx()];
    let occ = board.all;
    let (up, promo_rank, start_rank) = match us {
        Color::White => (8i8, 7u8, 1u8),
        Color::Black => (-8i8, 0u8, 6u8),
    };

    let mut p = pawns;
    while p != 0 {
        let from = p.trailing_zeros() as u8;
        p &= p - 1;
        let is_pinned = pinned & bb(from) != 0;
        let pin_line = if is_pinned { line(ksq, from) } else { !0u64 };

        // Pushes.
        if mode == GenMode::All {
            let one = (from as i8 + up) as u8;
            if occ & bb(one) == 0 {
                if bb(one) & target & pin_line != 0 {
                    push_pawn_move(list, from, one, rank_of(one) == promo_rank, false);
                }
                // Double push only from the start rank, over an empty square.
                if rank_of(from) == start_rank {
                    let two = (one as i8 + up) as u8;
                    if occ & bb(two) == 0 && bb(two) & target & pin_line != 0 {
                        list.push(Move::new(from, two, FLAG_DOUBLE_PAWN));
                    }
                }
            }
        }

        // Captures.
        let mut caps = pawn_attacks(us, from) & enemy & target & pin_line;
        while caps != 0 {
            let to = caps.trailing_zeros() as u8;
            caps &= caps - 1;
            push_pawn_move(list, from, to, rank_of(to) == promo_rank, true);
        }

        // En passant. The captured pawn and the capturing pawn both leave the
        // rank, which can expose a horizontal check — so verify by inspection.
        if let Some(ep) = board.ep_square {
            if pawn_attacks(us, from) & bb(ep) != 0 && bb(from) & pin_line != 0 {
                let cap_sq = if us == Color::White { ep - 8 } else { ep + 8 };
                let occ_after = (occ & !bb(from) & !bb(cap_sq)) | bb(ep);
                let rq = board.pieces[them.idx()][Piece::Rook.idx()]
                    | board.pieces[them.idx()][Piece::Queen.idx()];
                let bq = board.pieces[them.idx()][Piece::Bishop.idx()]
                    | board.pieces[them.idx()][Piece::Queen.idx()];
                let exposed = (rook_attacks(ksq, occ_after) & rq)
                    | (bishop_attacks(ksq, occ_after) & bq);
                // Under check, ep is only legal if it captures the checker
                // or blocks; target covers that, but the captured pawn sits
                // off the destination square so check it directly too.
                let resolves = target & (bb(ep) | bb(cap_sq)) != 0;
                if exposed == 0 && resolves {
                    list.push(Move::new(from, ep, FLAG_EP_CAPTURE));
                }
            }
        }
    }
}

#[inline(always)]
fn push_pawn_move(list: &mut MoveList, from: u8, to: u8, promo: bool, capture: bool) {
    if promo {
        let base = if capture { FLAG_PROMO_CAPTURE_N } else { FLAG_PROMO_N };
        for i in 0..4 {
            list.push(Move::new(from, to, base + i));
        }
    } else {
        list.push(Move::new(from, to, if capture { FLAG_CAPTURE } else { FLAG_QUIET }));
    }
}

// Handcrafted evaluation: material + piece-square tables, tapered between
// middlegame and endgame. Replaced by NNUE in stage 5; the interface stays.
use crate::board::Board;
use crate::types::*;

pub type Score = i32;
pub const MATE: Score = 30000;
pub const MATE_IN_MAX: Score = MATE - 1000;
pub const DRAW: Score = 0;

// Middlegame / endgame material values, in centipawns (PeSTO tuned set).
const MG_MATERIAL: [Score; 6] = [82, 337, 365, 477, 1025, 0];
const EG_MATERIAL: [Score; 6] = [94, 281, 297, 512, 936, 0];

// Phase weights: how much each piece keeps the game in the middlegame.
const PHASE_WEIGHT: [i32; 6] = [0, 1, 1, 2, 4, 0];
const TOTAL_PHASE: i32 = 24;

// Piece-square tables from White's perspective, A1 first, rank by rank.
const MG_PST: [[Score; 64]; 6] = [
    // Pawn
    [   0,   0,   0,   0,   0,   0,   0,   0,
      -35,  -1, -20, -23, -15,  24,  38, -22,
      -26,  -4,  -4, -10,   3,   3,  33, -12,
      -27,  -2,  -5,  12,  17,   6,  10, -25,
      -14,  13,   6,  21,  23,  12,  17, -23,
       -6,   7,  26,  31,  65,  56,  25, -20,
       98, 134,  61,  95,  68, 126,  34, -11,
        0,   0,   0,   0,   0,   0,   0,   0 ],
    // Knight
    [-105, -21, -58, -33, -17, -28, -19,  -23,
      -29, -53, -12,  -3,  -1,  18, -14,  -19,
      -23,  -9,  12,  10,  19,  17,  25,  -16,
      -13,   4,  16,  13,  28,  19,  21,   -8,
       -9,  17,  19,  53,  37,  69,  18,   22,
      -47,  60,  37,  65,  84, 129,  73,   44,
      -73, -41,  72,  36,  23,  62,   7,  -17,
     -167, -89, -34, -49,  61, -97, -15, -107 ],
    // Bishop
    [ -33,  -3, -14, -21, -13, -12, -39, -21,
        4,  15,  16,   0,   7,  21,  33,   1,
        0,  15,  15,  15,  14,  27,  18,  10,
       -6,  13,  13,  26,  34,  12,  10,   4,
       -4,   5,  19,  50,  37,  37,   7,  -2,
      -16,  37,  43,  40,  35,  50,  37,  -2,
      -26,  16, -18, -13,  30,  59,  18, -47,
      -29,   4, -82, -37, -25, -42,   7,  -8 ],
    // Rook
    [ -19, -13,   1,  17,  16,   7, -37, -26,
      -44, -16, -20,  -9,  -1,  11,  -6, -71,
      -45, -25, -16, -17,   3,   0,  -5, -33,
      -36, -26, -12,  -1,   9,  -7,   6, -23,
      -24, -11,   7,  26,  24,  35,  -8, -20,
       -5,  19,  26,  36,  17,  45,  61,  16,
       27,  32,  58,  62,  80,  67,  26,  44,
       32,  42,  32,  51,  63,   9,  31,  43 ],
    // Queen
    [  -1, -18,  -9,  10, -15, -25, -31, -50,
      -35,  -8,  11,   2,   8,  15,  -3,   1,
      -14,   2, -11,  -2,  -5,   2,  14,   5,
       -9, -26,  -9, -10,  -2,  -4,   3,  -3,
      -27, -27, -16, -16,  -1,  17,  -2,   1,
      -13, -17,   7,   8,  29,  56,  47,  57,
      -24, -39,  -5,   1, -16,  57,  28,  54,
      -28,   0,  29,  12,  59,  44,  43,  45 ],
    // King
    [ -15,  36,  12, -54,   8, -28,  24,  14,
        1,   7,  -8, -64, -43, -16,   9,   8,
      -14, -14, -22, -46, -44, -30, -15, -27,
      -49,  -1, -27, -39, -46, -44, -33, -51,
      -17, -20, -12, -27, -30, -25, -14, -36,
       -9,  24,   2, -16, -20,   6,  22, -22,
       29,  -1, -20,  -7,  -8,  -4, -38, -29,
      -65,  23,  16, -15, -56, -34,   2,  13 ],
];

const EG_PST: [[Score; 64]; 6] = [
    // Pawn
    [   0,   0,   0,   0,   0,   0,   0,   0,
       13,   8,   8,  10,  13,   0,   2,  -7,
        4,   7,  -6,   1,   0,  -5,  -1,  -8,
       13,   9,  -3,  -7,  -7,  -8,   3,  -1,
       32,  24,  13,   5,  -2,   4,  17,  17,
       94, 100,  85,  67,  56,  53,  82,  84,
      178, 173, 158, 134, 147, 132, 165, 187,
        0,   0,   0,   0,   0,   0,   0,   0 ],
    // Knight
    [ -29, -51, -23, -15, -22, -18, -50, -64,
      -42, -20, -10,  -5,  -2, -20, -23, -44,
      -23,  -3,  -1,  15,  10,  -3, -20, -22,
      -18,  -6,  16,  25,  16,  17,   4, -18,
      -17,   3,  22,  22,  22,  11,   8, -18,
      -24, -20,  10,   9,  -1,  -9, -19, -41,
      -25,  -8, -25,  -2,  -9, -25, -24, -52,
      -58, -38, -13, -28, -31, -27, -63, -99 ],
    // Bishop
    [ -23,  -9, -23,  -5,  -9, -16,  -5, -17,
      -14, -18,  -7,  -1,   4,  -9, -15, -27,
      -12,  -3,   8,  10,  13,   3,  -7, -15,
       -6,   3,  13,  19,   7,  10,  -3,  -9,
       -3,   9,  12,   9,  14,  10,   3,   2,
        2,  -8,   0,  -1,  -2,   6,   0,   4,
       -8,  -4,   7, -12,  -3, -13,  -4, -14,
      -14, -21, -11,  -8,  -7,  -9, -17, -24 ],
    // Rook
    [  -9,   2,   3,  -1,  -5, -13,   4, -20,
       -6,  -6,   0,   2,  -9,  -9, -11,  -3,
       -4,   0,  -5,  -1,  -7, -12,  -8, -16,
        3,   5,   8,   4,  -5,  -6,  -8, -11,
        4,   3,  13,   1,   2,   1,  -1,   2,
        7,   7,   7,   5,   4,  -3,  -5,  -3,
       11,  13,  13,  11,  -3,   3,   8,   3,
       13,  10,  18,  15,  12,  12,   8,   5 ],
    // Queen
    [ -33, -28, -22, -43,  -5, -32, -20, -41,
      -22, -23, -30, -16, -16, -23, -36, -32,
      -16, -27,  15,   6,   9,  17,  10,   5,
      -18,  28,  19,  47,  31,  34,  39,  23,
        3,  22,  24,  45,  57,  40,  57,  36,
      -20,   6,   9,  49,  47,  35,  19,   9,
      -17,  20,  32,  41,  58,  25,  30,   0,
       -9,  22,  22,  27,  27,  19,  10,  20 ],
    // King
    [ -53, -34, -21, -11, -28, -14, -24, -43,
      -27, -11,   4,  13,  14,   4,  -5, -17,
      -19,  -3,  11,  21,  23,  16,   7,  -9,
      -18,  -4,  21,  24,  27,  23,   9, -11,
       -8,  22,  24,  27,  26,  33,  26,   3,
       10,  17,  23,  15,  20,  45,  44,  13,
      -12,  17,  14,  17,  17,  38,  23,  11,
      -74, -35, -18, -18, -11,  15,   4, -17 ],
];

// Black reads the same tables with the rank mirrored.
#[inline(always)]
pub fn relative_sq(c: Color, sq: u8) -> usize {
    if c == Color::White { sq as usize } else { (sq ^ 56) as usize }
}

// When a network is loaded, it replaces the handcrafted eval entirely.
// Set once at startup; read without locking on the hot path.
static NET: std::sync::OnceLock<Option<Box<crate::nnue::Network>>> =
    std::sync::OnceLock::new();

pub fn load_network(path: &str) -> Result<(), String> {
    let net = crate::nnue::load(path).map_err(|e| e.to_string())?;
    NET.set(Some(net)).map_err(|_| "network already loaded".to_string())
}

#[inline(always)]
pub fn network() -> Option<&'static crate::nnue::Network> {
    NET.get().and_then(|o| o.as_deref())
}

pub fn evaluate(board: &Board) -> Score {
    if let Some(net) = network() {
        // ponytail: full refresh per call, no incremental accumulator yet.
        // Correct but ~10x slower than it should be; wire the accumulator
        // through make/unmake once the net is proven to gain Elo.
        let mut acc = crate::nnue::Accumulator::new(net);
        acc.refresh(net, board);
        return crate::nnue::evaluate(net, &acc, board.side);
    }
    evaluate_hce(board)
}

// Material that cannot force mate: K, K+N, K+B, K+NN vs bare king.
// Without this the engine trades into dead draws believing it is ahead.
pub fn is_insufficient_material(board: &Board) -> bool {
    let all_pawns = board.pieces[0][Piece::Pawn.idx()] | board.pieces[1][Piece::Pawn.idx()];
    let heavy = board.pieces[0][Piece::Rook.idx()] | board.pieces[1][Piece::Rook.idx()]
        | board.pieces[0][Piece::Queen.idx()] | board.pieces[1][Piece::Queen.idx()];
    if all_pawns | heavy != 0 { return false; }

    for c in [Color::White, Color::Black] {
        let n = board.pieces[c.idx()][Piece::Knight.idx()].count_ones();
        let b = board.pieces[c.idx()][Piece::Bishop.idx()].count_ones();
        // Two bishops, or bishop + knight, can mate.
        if b >= 2 || (b >= 1 && n >= 1) { return false; }
        // Three knights can mate (contrived but legal after promotion).
        if n >= 3 { return false; }
    }
    true
}

pub fn evaluate_hce(board: &Board) -> Score {
    if is_insufficient_material(board) { return DRAW; }

    let mut mg = [0i32; 2];
    let mut eg = [0i32; 2];
    let mut phase = 0i32;

    for color in [Color::White, Color::Black] {
        let ci = color.idx();
        for piece in [Piece::Pawn, Piece::Knight, Piece::Bishop,
                      Piece::Rook, Piece::Queen, Piece::King] {
            let pi = piece.idx();
            let mut b = board.pieces[ci][pi];
            while b != 0 {
                let sq = b.trailing_zeros() as u8;
                b &= b - 1;
                let rsq = relative_sq(color, sq);
                mg[ci] += MG_MATERIAL[pi] + MG_PST[pi][rsq];
                eg[ci] += EG_MATERIAL[pi] + EG_PST[pi][rsq];
                phase += PHASE_WEIGHT[pi];
            }
        }
    }

    let us = board.side.idx();
    let them = board.side.flip().idx();
    let mg_score = mg[us] - mg[them];
    let eg_score = eg[us] - eg[them];

    // Interpolate: full middlegame at phase 24, full endgame at 0.
    let phase = phase.min(TOTAL_PHASE);
    (mg_score * phase + eg_score * (TOTAL_PHASE - phase)) / TOTAL_PHASE
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::board::Board;

    #[test]
    fn startpos_is_balanced() {
        // Symmetric position: score must be 0 regardless of side to move.
        assert_eq!(evaluate(&Board::startpos()), 0);
    }

    #[test]
    fn eval_is_side_relative() {
        // Same position, opposite sides to move => negated scores.
        let w = Board::from_fen("4k3/8/8/8/8/8/8/QK6 w - - 0 1").unwrap();
        let b = Board::from_fen("4k3/8/8/8/8/8/8/QK6 b - - 0 1").unwrap();
        assert!(evaluate(&w) > 500, "white up a queen should be winning");
        assert_eq!(evaluate(&w), -evaluate(&b));
    }
}

#[cfg(test)]
mod draw_tests {
    use super::*;
    use crate::board::Board;

    #[test]
    fn insufficient_material_detected() {
        for fen in [
            "4k3/8/8/8/8/8/8/4K3 w - - 0 1",        // bare kings
            "4k3/8/8/8/8/8/8/3BK3 w - - 0 1",       // K+B vs K
            "4k3/8/8/8/8/8/8/3NK3 w - - 0 1",       // K+N vs K
            "4k3/8/8/8/8/8/8/2NNK3 w - - 0 1",      // K+NN vs K
            "3bk3/8/8/8/8/8/8/3BK3 w - - 0 1",      // K+B vs K+B
        ] {
            let b = Board::from_fen(fen).unwrap();
            assert!(is_insufficient_material(&b), "should be a draw: {}", fen);
            assert_eq!(evaluate_hce(&b), DRAW);
        }
    }

    #[test]
    fn sufficient_material_not_flagged() {
        for fen in [
            "4k3/8/8/8/8/8/8/3QK3 w - - 0 1",       // queen mates
            "4k3/8/8/8/8/8/8/3RK3 w - - 0 1",       // rook mates
            "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",      // pawn promotes
            "4k3/8/8/8/8/8/8/2BBK3 w - - 0 1",      // two bishops mate
            "4k3/8/8/8/8/8/8/2NBK3 w - - 0 1",      // bishop + knight mate
        ] {
            let b = Board::from_fen(fen).unwrap();
            assert!(!is_insufficient_material(&b), "should not be a draw: {}", fen);
        }
    }
}

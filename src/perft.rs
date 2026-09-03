// Perft: count leaf nodes at depth N. The move generator's correctness test.
use crate::board::Board;
use crate::movegen::{generate, GenMode};

pub fn perft(board: &mut Board, depth: u32) -> u64 {
    if depth == 0 { return 1; }
    let list = generate(board, GenMode::All);
    // Bulk counting: at depth 1 the move count is the answer.
    if depth == 1 { return list.len as u64; }
    let mut nodes = 0;
    for i in 0..list.len {
        let m = list[i];
        let undo = board.make_move(m);
        nodes += perft(board, depth - 1);
        board.unmake_move(m, undo);
    }
    nodes
}

// Per-move breakdown, for bisecting a mismatch against a reference engine.
pub fn perft_divide(board: &mut Board, depth: u32) -> Vec<(String, u64)> {
    let list = generate(board, GenMode::All);
    let mut out = Vec::new();
    for i in 0..list.len {
        let m = list[i];
        let undo = board.make_move(m);
        let n = if depth <= 1 { 1 } else { perft(board, depth - 1) };
        board.unmake_move(m, undo);
        out.push((m.to_uci(), n));
    }
    out.sort();
    out
}

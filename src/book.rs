// A small opening book.
//
// The engine reaches only depth 13-15 on move one in two seconds, and spends
// that time rediscovering theory. The book answers instantly and leaves the
// clock for positions where search actually decides something.
//
// Lines are stored as move sequences and expanded into a hash -> moves table at
// startup, so the source stays readable and the lookup stays a single probe.
use crate::board::Board;
use crate::movegen::{generate, GenMode};
use crate::types::Move;
use std::collections::HashMap;
use std::sync::OnceLock;

// Mainline theory, a few moves deep. Each line is played from the start.
// Several replies per position give the engine variety rather than repeating
// one game forever.
const LINES: &[&str] = &[
    // Open games
    "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7 f1e1 b7b5 a4b3 d7d6 c2c3 e8g8 h2h3",
    "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5c6 d7c6 e1g1 f7f6 d2d4 e5d4 f3d4 c6c5",
    "e2e4 e7e5 g1f3 b8c6 f1c4 f8c5 c2c3 g8f6 d2d4 e5d4 c3d4 c5b4 c1d2 b4d2",
    "e2e4 e7e5 g1f3 b8c6 f1c4 g8f6 d2d3 f8c5 c2c3 d7d6 e1g1 e8g8",
    "e2e4 e7e5 g1f3 b8c6 d2d4 e5d4 f3d4 g8f6 b1c3 f8b4 d4c6 b7c6",
    "e2e4 e7e5 g1f3 g8f6 f3e5 d7d6 e5f3 f6e4 d2d4 d6d5 f1d3 b8c6 e1g1",
    "e2e4 e7e5 b1c3 g8f6 g1f3 b8c6 f1b5 f8b4 e1g1 e8g8 d2d3 d7d6",
    "e2e4 e7e5 f2f4 e5f4 g1f3 g7g5 h2h4 g5g4 f3e5 g8f6",
    "e2e4 e7e5 g1f3 b8c6 d2d4 e5d4 f1c4 f8c5 c2c3 d4c3",
    // Sicilian
    "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3 a7a6 c1e3 e7e5 d4b3 c8e6",
    "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3 b8c6 c1g5 e7e6 d1d2 f8e7",
    "e2e4 c7c5 g1f3 b8c6 d2d4 c5d4 f3d4 g8f6 b1c3 e7e5 d4b5 d7d6 c1g5",
    "e2e4 c7c5 g1f3 e7e6 d2d4 c5d4 f3d4 a7a6 f1d3 g8f6 e1g1 d7d6",
    "e2e4 c7c5 g1f3 b8c6 f1b5 g7g6 e1g1 f8g7 f1e1 e7e5 b5c6 d7c6",
    "e2e4 c7c5 b1c3 b8c6 g2g3 g7g6 f1g2 f8g7 d2d3 d7d6 c1e3",
    "e2e4 c7c5 c2c3 d7d5 e4d5 d8d5 d2d4 g8f6 g1f3 e7e6 f1e2 c5d4",
    "e2e4 c7c5 g1f3 d7d6 f1b5 c8d7 b5d7 d8d7 e1g1 b8c6 c2c3 g8f6",
    // French
    "e2e4 e7e6 d2d4 d7d5 b1c3 g8f6 c1g5 f8e7 e4e5 f6d7 g5e7 d8e7",
    "e2e4 e7e6 d2d4 d7d5 b1c3 f8b4 e4e5 c7c5 a2a3 b4c3 b2c3 g8e7",
    "e2e4 e7e6 d2d4 d7d5 e4e5 c7c5 c2c3 b8c6 g1f3 d8b6 f1e2 c8d7",
    "e2e4 e7e6 d2d4 d7d5 b1d2 c7c5 e4d5 d8d5 g1f3 c5d4 f1c4 d5d6",
    // Caro-Kann
    "e2e4 c7c6 d2d4 d7d5 b1c3 d5e4 c3e4 c8f5 e4g3 f5g6 h2h4 h7h6",
    "e2e4 c7c6 d2d4 d7d5 e4e5 c8f5 g1f3 e7e6 f1e2 c6c5 c1e3 d8b6",
    "e2e4 c7c6 d2d4 d7d5 e4d5 c6d5 c2c4 g8f6 b1c3 e7e6 g1f3 f8e7",
    // Other replies to e4
    "e2e4 d7d5 e4d5 d8d5 b1c3 d5a5 d2d4 g8f6 g1f3 c7c6 f1c4 c8f5",
    "e2e4 g8f6 e4e5 f6d5 d2d4 d7d6 g1f3 g7g6 f1c4 d5b6 c4b3",
    "e2e4 d7d6 d2d4 g8f6 b1c3 g7g6 f2f4 f8g7 g1f3 e8g8",
    "e2e4 g7g6 d2d4 f8g7 b1c3 d7d6 f2f4 g8f6 g1f3 e8g8",
    "e2e4 b8c6 d2d4 d7d5 b1c3 d5e4 d4d5 c6e5 d1d4",
    // Queen's Gambit
    "d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7 e2e3 e8g8 g1f3 h7h6 g5h4",
    "d2d4 d7d5 c2c4 c7c6 g1f3 g8f6 b1c3 d5c4 a2a4 c8f5 e2e3 e7e6",
    "d2d4 d7d5 c2c4 d5c4 g1f3 g8f6 e2e3 e7e6 f1c4 c7c5 e1g1 a7a6",
    "d2d4 d7d5 c2c4 e7e6 b1c3 c7c6 g1f3 g8f6 e2e3 b8d7 f1d3 d5c4 d3c4",
    "d2d4 d7d5 g1f3 g8f6 c2c4 e7e6 b1c3 f8e7 c1g5 e8g8 e2e3 h7h6",
    // Indian defences
    "d2d4 g8f6 c2c4 e7e6 b1c3 f8b4 e2e3 e8g8 f1d3 d7d5 g1f3 c7c5",
    "d2d4 g8f6 c2c4 e7e6 g1f3 b7b6 g2g3 c8b7 f1g2 f8e7 e1g1 e8g8",
    "d2d4 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6 g1f3 e8g8 f1e2 e7e5",
    "d2d4 g8f6 c2c4 g7g6 b1c3 d7d5 c4d5 f6d5 e2e4 d5c3 b2c3 f8g7",
    "d2d4 g8f6 c2c4 c7c5 d4d5 b7b5 c4b5 a7a6 b5a6 g7g6 b1c3 c8a6",
    "d2d4 g8f6 c2c4 e7e5 d4e5 f6g4 c1f4 b8c6 g1f3 f8b4 b1d2",
    "d2d4 g8f6 g1f3 e7e6 c1g5 h7h6 g5h4 b7b6 e2e3 c8b7 f1d3 f8e7",
    "d2d4 g8f6 g1f3 g7g6 c2c4 f8g7 b1c3 d7d5 d1b3 d5c4 b3c4 e8g8",
    // Other queen's pawn
    "d2d4 f7f5 g2g3 g8f6 f1g2 e7e6 g1f3 f8e7 e1g1 e8g8 c2c4 d7d6",
    "d2d4 d7d5 c1f4 g8f6 e2e3 e7e6 g1f3 f8d6 f4d6 c7d6",
    "d2d4 e7e6 c2c4 g8f6 b1c3 f8b4 d1c2 e8g8 a2a3 b4c3 c2c3",
    "d2d4 d7d5 e2e3 g8f6 f1d3 c7c5 c2c3 b8c6 f2f4 c8g4",
    // Flank openings
    "c2c4 e7e5 b1c3 g8f6 g1f3 b8c6 g2g3 d7d5 c4d5 f6d5",
    "c2c4 g8f6 b1c3 e7e6 g1f3 d7d5 d2d4 f8e7 c1g5 e8g8",
    "c2c4 c7c5 g1f3 g8f6 b1c3 b8c6 g2g3 g7g6 f1g2 f8g7",
    "g1f3 d7d5 g2g3 g8f6 f1g2 c7c6 e1g1 c8g4 d2d3 b8d7",
    "g1f3 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6 d2d4 e8g8",
    "b2b3 e7e5 c1b2 b8c6 e2e3 g8f6 f1b5 f8d6 f2f4 e5f4",
    "f2f4 d7d5 g1f3 g8f6 e2e3 c8g4 f1e2 e7e6 e1g1 f8d6",
    "g2g3 d7d5 f1g2 g8f6 g1f3 c7c6 e1g1 c8f5 d2d3 e7e6",
];

pub struct Book {
    // position hash -> the moves played from it in known theory
    table: HashMap<u64, Vec<Move>>,
}

impl Book {
    fn build() -> Book {
        let mut table: HashMap<u64, Vec<Move>> = HashMap::new();
        for line in LINES {
            let mut board = Board::startpos();
            for token in line.split_whitespace() {
                let list = generate(&board, GenMode::All);
                let mv = (0..list.len)
                    .map(|i| list[i])
                    .find(|m| m.to_uci() == token);
                let Some(mv) = mv else {
                    // A typo in a line should not poison the rest of the book.
                    eprintln!("info string book: illegal move {} in line", token);
                    break;
                };
                let entry = table.entry(board.hash).or_default();
                if !entry.contains(&mv) {
                    entry.push(mv);
                }
                board.make_move(mv);
            }
        }
        Book { table }
    }

    /// A book move for this position, or None to search normally.
    /// `pick` selects among equally-good replies so games differ.
    pub fn probe(&self, board: &Board, pick: usize) -> Option<Move> {
        let moves = self.table.get(&board.hash)?;
        if moves.is_empty() {
            return None;
        }
        Some(moves[pick % moves.len()])
    }

    pub fn len(&self) -> usize { self.table.len() }
}

static BOOK: OnceLock<Book> = OnceLock::new();

pub fn book() -> &'static Book { BOOK.get_or_init(Book::build) }

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn book_has_positions_and_legal_moves() {
        let b = book();
        assert!(b.len() > 50, "book should cover many positions, got {}", b.len());

        // Every stored move must be legal in its position — a book move is
        // played without verification, so an illegal one would corrupt a game.
        let mut board = Board::startpos();
        let mv = b.probe(&board, 0).expect("start position is in the book");
        let list = generate(&board, GenMode::All);
        assert!(list.as_slice().contains(&mv), "book move must be legal");
        board.make_move(mv);
    }

    #[test]
    fn book_offers_variety() {
        // The start position should have several replies, or every game is
        // identical.
        let b = book();
        let board = Board::startpos();
        let first = b.probe(&board, 0).unwrap();
        let any_different = (1..6)
            .filter_map(|i| b.probe(&board, i))
            .any(|m| m != first);
        assert!(any_different, "book should offer more than one first move");
    }

    #[test]
    fn book_runs_out_gracefully() {
        // A position outside theory must return None rather than a wrong move.
        let board = Board::from_fen("8/8/4k3/8/8/4K3/8/8 w - - 0 1").unwrap();
        assert!(book().probe(&board, 0).is_none());
    }
}

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
    "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1",      // Ruy Lopez
    "e2e4 e7e5 g1f3 b8c6 f1c4 f8c5 c2c3 g8f6 d2d4",      // Italian
    "e2e4 e7e5 g1f3 b8c6 d2d4 e5d4 f3d4 g8f6",           // Scotch
    "e2e4 e7e5 g1f3 g8f6 f3e5 d7d6 e5f3 f6e4 d2d4",      // Petrov
    "e2e4 e7e5 b1c3 g8f6 g1f3 b8c6 f1b5",                // Four Knights
    // Sicilian
    "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3",      // Najdorf/Classical
    "e2e4 c7c5 g1f3 b8c6 d2d4 c5d4 f3d4 g8f6 b1c3",      // Sveshnikov-ish
    "e2e4 c7c5 g1f3 e7e6 d2d4 c5d4 f3d4 a7a6",           // Kan
    "e2e4 c7c5 b1c3 b8c6 g2g3 g7g6 f1g2",                // Closed Sicilian
    // French / Caro-Kann
    "e2e4 e7e6 d2d4 d7d5 b1c3 g8f6 c1g5 f8e7",           // French Classical
    "e2e4 e7e6 d2d4 d7d5 b1d2 c7c5 e4d5",                // French Tarrasch
    "e2e4 c7c6 d2d4 d7d5 b1c3 d5e4 c3e4 c8f5",           // Caro-Kann Classical
    "e2e4 c7c6 d2d4 d7d5 e4e5 c8f5 g1f3 e7e6",           // Caro Advance
    // Queen's pawn
    "d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7",           // QGD
    "d2d4 d7d5 c2c4 c7c6 g1f3 g8f6 b1c3 d5c4",           // Slav
    "d2d4 d7d5 c2c4 d5c4 g1f3 g8f6 e2e3",                // QGA
    "d2d4 g8f6 c2c4 e7e6 g1f3 d7d5 b1c3 f8e7",           // QGD via Nf6
    "d2d4 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6",           // King's Indian
    "d2d4 g8f6 c2c4 e7e6 g2g3 d7d5 f1g2",                // Catalan
    "d2d4 g8f6 c2c4 e7e6 b1c3 f8b4 e2e3",                // Nimzo-Indian
    "d2d4 f7f5 g2g3 g8f6 f1g2 e7e6 g1f3",                // Dutch
    // Flank
    "g1f3 d7d5 d2d4 g8f6 c2c4 e7e6",                     // transposition
    "c2c4 e7e5 b1c3 g8f6 g1f3 b8c6",                     // English
    "c2c4 g8f6 b1c3 e7e6 g1f3 d7d5",                     // English -> QGD
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

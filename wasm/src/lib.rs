// Kraken compiled to WebAssembly, so it runs in the visitor's browser.
//
// The engine's own modules are included directly rather than depended on as a
// crate: the binary crate has a main() and CLI plumbing that make no sense
// here, and the search needs a WASM-safe stopping condition (no threads, and
// std::time::Instant panics on this target).
use wasm_bindgen::prelude::*;

#[path = "../../src/types.rs"] mod types;
#[path = "../../src/attacks.rs"] mod attacks;
#[path = "../../src/board.rs"] mod board;
#[path = "../../src/movegen.rs"] mod movegen;
#[path = "../../src/eval.rs"] mod eval;
#[path = "../../src/nnue.rs"] mod nnue;
#[path = "../../src/book.rs"] mod book;
#[path = "../../src/search_wasm.rs"] mod search;

use board::Board;
use movegen::{generate, GenMode};
use types::Move;

#[wasm_bindgen]
pub struct Kraken {
    board: Board,
    searcher: search::Searcher,
    book_pick: usize,
    // The move list, kept so notation can be regenerated from the start.
    history: Vec<String>,
}

/// Standard algebraic notation for one move in a position.
fn san(board: &Board, mv: Move, legal: &movegen::MoveList) -> String {
    use types::{Color, Piece};
    let from = mv.from();
    let to = mv.to();
    let Some((_, piece)) = board.piece_at(from) else { return mv.to_uci() };

    if mv.is_castle() {
        return if types::file_of(to) == 6 { "O-O".into() } else { "O-O-O".into() };
    }

    let mut s = String::new();
    let captures = mv.is_capture() || mv.is_ep();

    if piece == Piece::Pawn {
        if captures {
            s.push((b'a' + types::file_of(from)) as char);
        }
    } else {
        s.push(piece.to_char(Color::White));
        // Disambiguate when another identical piece could also play here.
        let mut same_file = false;
        let mut same_rank = false;
        let mut ambiguous = false;
        for i in 0..legal.len {
            let other = legal[i];
            if other == mv || other.to() != to { continue; }
            if let Some((_, op)) = board.piece_at(other.from()) {
                if op != piece { continue; }
                ambiguous = true;
                if types::file_of(other.from()) == types::file_of(from) { same_file = true; }
                if types::rank_of(other.from()) == types::rank_of(from) { same_rank = true; }
            }
        }
        if ambiguous {
            if !same_file {
                s.push((b'a' + types::file_of(from)) as char);
            } else if !same_rank {
                s.push((b'1' + types::rank_of(from)) as char);
            } else {
                s.push((b'a' + types::file_of(from)) as char);
                s.push((b'1' + types::rank_of(from)) as char);
            }
        }
    }

    if captures { s.push('x'); }
    s.push_str(&types::square_name(to));

    if mv.is_promotion() {
        s.push('=');
        s.push(mv.promo_piece().to_char(Color::White));
    }

    // Check and mate suffixes need the resulting position.
    let mut after = board.clone();
    after.make_move(mv);
    if after.in_check(after.side) {
        s.push(if generate(&after, GenMode::All).len == 0 { '#' } else { '+' });
    }
    s
}

#[wasm_bindgen]
impl Kraken {
    #[wasm_bindgen(constructor)]
    pub fn new() -> Kraken {
        Kraken {
            board: Board::startpos(),
            searcher: search::Searcher::new(16),
            book_pick: 0,
            history: Vec::new(),
        }
    }

    /// Load the network from bytes fetched by the page.
    pub fn load_network(&mut self, bytes: &[u8]) -> bool {
        eval::load_network_bytes(bytes).is_ok()
    }

    /// Set the position from a move list, replayed from the start.
    pub fn set_position(&mut self, moves: &str) -> bool {
        self.board = Board::startpos();
        self.history.clear();
        if moves.trim().is_empty() {
            return true;
        }
        for token in moves.split_whitespace() {
            let list = generate(&self.board, GenMode::All);
            match (0..list.len).map(|i| list[i]).find(|m| m.to_uci() == token) {
                Some(m) => {
                    self.board.make_move(m);
                    self.history.push(token.to_string());
                }
                None => return false,
            }
        }
        true
    }

    /// Legal moves in the current position, space separated.
    pub fn legal_moves(&self) -> String {
        let list = generate(&self.board, GenMode::All);
        (0..list.len).map(|i| list[i].to_uci()).collect::<Vec<_>>().join(" ")
    }

    /// Terminal state: playing, white-wins, black-wins, or a draw reason.
    pub fn status(&self) -> String {
        let list = generate(&self.board, GenMode::All);
        if list.len == 0 {
            return if self.board.in_check(self.board.side) {
                if self.board.side == types::Color::White { "black-wins" } else { "white-wins" }
            } else { "draw-stalemate" }.into();
        }
        if self.board.halfmove >= 100 { return "draw-fifty".into(); }
        if eval::is_insufficient_material(&self.board) { return "draw-material".into(); }
        "playing".into()
    }

    /// Best move, searched to `depth`, or from the book when `use_book`.
    pub fn best_move(&mut self, depth: u32, use_book: bool) -> String {
        if use_book {
            if let Some(mv) = book::book().probe(&self.board, self.book_pick) {
                self.book_pick += 1;
                return mv.to_uci();
            }
        }
        let (mv, _) = self.searcher.search(&mut self.board, depth);
        if mv == Move::NULL { String::new() } else { mv.to_uci() }
    }

    /// Evaluation in centipawns from White's point of view.
    pub fn evaluate(&self) -> i32 {
        let v = eval::evaluate(&self.board);
        if self.board.side == types::Color::White { v } else { -v }
    }

    pub fn fen(&self) -> String { self.board.to_fen() }

    /// The move list in standard algebraic notation, space separated.
    ///
    /// Built here rather than in JavaScript because SAN needs legality and
    /// disambiguation — which piece can also reach that square — and the
    /// engine already knows the rules.
    pub fn san_history(&self) -> String {
        let mut board = Board::startpos();
        let mut out = Vec::new();
        for token in self.history.iter() {
            let list = generate(&board, GenMode::All);
            let Some(mv) = (0..list.len).map(|i| list[i])
                .find(|m| m.to_uci() == *token) else { break };
            out.push(san(&board, mv, &list));
            board.make_move(mv);
        }
        out.join(" ")
    }
}

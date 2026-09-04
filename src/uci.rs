// UCI protocol: the lingua franca for chess GUIs and match runners.
use crate::board::{Board, START_FEN};
use crate::movegen::{generate, GenMode};
use crate::search::{SearchLimits, Searcher, ThreadedSearcher, MAX_PLY};
use crate::types::*;
use std::io::{self, BufRead, Write};
use std::sync::atomic::Ordering;
use std::time::Duration;

pub fn run() {
    let mut board = Board::startpos();
    let mut searcher = ThreadedSearcher::new(64, 1);
    let mut use_book = true;
    // Rotates through equally-good book replies so games are not identical.
    let mut book_pick: usize = (std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as usize).unwrap_or(0)) % 997;
    let stdin = io::stdin();

    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let tokens: Vec<&str> = line.split_whitespace().collect();
        let Some(&cmd) = tokens.first() else { continue };

        match cmd {
            "uci" => {
                println!("id name Kraken 1.0");
                println!("id author nesbes");
                println!("option name Hash type spin default 64 min 1 max 4096");
                println!("option name Threads type spin default 1 min 1 max 64");
                println!("option name OwnBook type check default true");
                println!("uciok");
            }
            "isready" => println!("readyok"),
            "ucinewgame" => {
                board = Board::startpos();
                searcher.clear();
            }
            "setoption" => {
                // setoption name Hash value 256
                if let Some(i) = tokens.iter().position(|&t| t == "name") {
                    let value = tokens.iter().position(|&t| t == "value")
                        .and_then(|v| tokens.get(v + 1))
                        .and_then(|s| s.parse::<usize>().ok());
                    match (tokens.get(i + 1), value) {
                        (Some(&"Hash"), Some(mb)) => {
                            let n = searcher.threads;
                            searcher = ThreadedSearcher::new(mb, n);
                        }
                        (Some(&"Threads"), Some(n)) => searcher.set_threads(n),
                        (Some(&"OwnBook"), _) => {
                            use_book = tokens.last() == Some(&"true");
                        }
                        _ => {}
                    }
                }
            }
            "position" => set_position(&mut board, &mut searcher, &tokens),
            "go" => {
                if use_book {
                    if let Some(mv) = crate::book::book().probe(&board, book_pick) {
                        book_pick += 1;
                        println!("info string book move");
                        println!("bestmove {}", mv.to_uci());
                        io::stdout().flush().ok();
                        continue;
                    }
                }
                let limits = parse_go(&tokens, board.side);
                let (best, _) = searcher.search(&board, limits, true);
                println!("bestmove {}", best.to_uci());
            }
            "stop" => searcher.stop.store(true, Ordering::Relaxed),
            "quit" => break,
            // Non-standard helpers.
            "d" | "print" => println!("{}\n{}", render(&board), board.to_fen()),
            "eval" => println!("{}", crate::eval::evaluate(&board)),
            // Non-standard: list legal moves, so a GUI does not need its own
            // rules implementation.
            "legal" => {
                let list = generate(&board, GenMode::All);
                let moves: Vec<String> = (0..list.len)
                    .map(|i| list[i].to_uci()).collect();
                println!("legal {}", moves.join(" "));
            }
            // Non-standard: report terminal state so a match runner can
            // detect checkmate and draws without reimplementing the rules.
            "status" => {
                let list = generate(&board, GenMode::All);
                let s = if list.len == 0 {
                    if board.in_check(board.side) {
                        if board.side == Color::White { "black-wins" } else { "white-wins" }
                    } else { "draw-stalemate" }
                } else if board.halfmove >= 100 {
                    "draw-fifty"
                } else if crate::eval::is_insufficient_material(&board) {
                    "draw-material"
                } else {
                    "playing"
                };
                println!("{}", s);
            }
            _ => {}
        }
        io::stdout().flush().ok();
    }
}

fn set_position(board: &mut Board, searcher: &mut ThreadedSearcher, tokens: &[&str]) {
    let mut i = 1;
    let fen = if tokens.get(i) == Some(&"startpos") {
        i += 1;
        START_FEN.to_string()
    } else if tokens.get(i) == Some(&"fen") {
        i += 1;
        let start = i;
        while i < tokens.len() && tokens[i] != "moves" { i += 1; }
        tokens[start..i].join(" ")
    } else {
        return;
    };

    let Ok(b) = Board::from_fen(&fen) else {
        eprintln!("info string bad fen: {}", fen);
        return;
    };
    *board = b;
    searcher.repetitions.clear();
    searcher.repetitions.push(board.hash);

    if tokens.get(i) == Some(&"moves") {
        for &mv_str in &tokens[i + 1..] {
            match find_move(board, mv_str) {
                Some(m) => {
                    board.make_move(m);
                    searcher.repetitions.push(board.hash);
                }
                None => {
                    eprintln!("info string illegal move: {}", mv_str);
                    break;
                }
            }
        }
    }
}

// Match a UCI move string against the legal move list. Doing it this way
// means we never have to infer flags (castle vs quiet, ep vs capture).
fn find_move(board: &Board, s: &str) -> Option<Move> {
    let list = generate(board, GenMode::All);
    (0..list.len).map(|i| list[i]).find(|m| m.to_uci() == s)
}

fn parse_go(tokens: &[&str], side: Color) -> SearchLimits {
    let mut limits = SearchLimits::default();
    let get = |key: &str| -> Option<u64> {
        tokens.iter().position(|&t| t == key)
            .and_then(|i| tokens.get(i + 1))
            .and_then(|s| s.parse().ok())
    };

    if let Some(d) = get("depth") { limits.depth = d as u32; }
    if let Some(n) = get("nodes") { limits.nodes = Some(n); }
    if let Some(ms) = get("movetime") { limits.movetime = Some(Duration::from_millis(ms)); }

    // Clock-based: budget a fraction of remaining time plus most of the increment.
    let (time, inc) = match side {
        Color::White => (get("wtime"), get("winc")),
        Color::Black => (get("btime"), get("binc")),
    };
    if let Some(t) = time {
        let inc = inc.unwrap_or(0);
        let moves_to_go = get("movestogo").unwrap_or(30).max(1);
        // Reserve an overhead margin so we never flag on the move being
        // computed; scale it with the clock so blitz stays safe.
        let overhead = (t / 50).clamp(20, 300);
        let usable = t.saturating_sub(overhead);
        // Soft target: the share of the clock this move deserves.
        let soft = (usable / moves_to_go + inc * 3 / 4).max(5);
        // Hard cap: never spend more than a fifth of what's left on one move.
        let hard = (usable / 4).max(soft).min(usable);
        limits.soft_time = Some(Duration::from_millis(soft));
        limits.movetime = Some(Duration::from_millis(hard));
    }
    if tokens.contains(&"infinite") {
        limits.movetime = None;
        limits.soft_time = None;
        limits.depth = MAX_PLY as u32;
    }
    limits
}

fn render(board: &Board) -> String {
    let mut s = String::new();
    for rank in (0..8).rev() {
        s.push_str(&format!("{} ", rank + 1));
        for file in 0..8 {
            let c = match board.piece_at(square(file, rank)) {
                Some((col, p)) => p.to_char(col),
                None => '.',
            };
            s.push(c);
            s.push(' ');
        }
        s.push('\n');
    }
    s.push_str("  a b c d e f g h");
    s
}

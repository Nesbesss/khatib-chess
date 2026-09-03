// UCI protocol: the lingua franca for chess GUIs and match runners.
use crate::board::{Board, START_FEN};
use crate::movegen::{generate, GenMode};
use crate::search::{SearchLimits, Searcher, MAX_PLY};
use crate::types::*;
use std::io::{self, BufRead, Write};
use std::sync::atomic::Ordering;
use std::time::Duration;

pub fn run() {
    let mut board = Board::startpos();
    let mut searcher = Searcher::new(64);
    let stdin = io::stdin();

    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let tokens: Vec<&str> = line.split_whitespace().collect();
        let Some(&cmd) = tokens.first() else { continue };

        match cmd {
            "uci" => {
                println!("id name Chess");
                println!("id author nesbes");
                println!("option name Hash type spin default 64 min 1 max 4096");
                println!("uciok");
            }
            "isready" => println!("readyok"),
            "ucinewgame" => {
                board = Board::startpos();
                searcher.tt.clear();
                searcher.repetitions.clear();
            }
            "setoption" => {
                // setoption name Hash value 256
                if let Some(i) = tokens.iter().position(|&t| t == "name") {
                    if tokens.get(i + 1) == Some(&"Hash") {
                        if let Some(v) = tokens.iter().position(|&t| t == "value") {
                            if let Some(mb) = tokens.get(v + 1).and_then(|s| s.parse().ok()) {
                                searcher = Searcher::new(mb);
                            }
                        }
                    }
                }
            }
            "position" => set_position(&mut board, &mut searcher, &tokens),
            "go" => {
                let limits = parse_go(&tokens, board.side);
                let (best, _) = searcher.search(&mut board, limits, true);
                println!("bestmove {}", best.to_uci());
            }
            "stop" => searcher.stop.store(true, Ordering::Relaxed),
            "quit" => break,
            // Non-standard helpers.
            "d" | "print" => println!("{}\n{}", render(&board), board.to_fen()),
            "eval" => println!("{}", crate::eval::evaluate(&board)),
            _ => {}
        }
        io::stdout().flush().ok();
    }
}

fn set_position(board: &mut Board, searcher: &mut Searcher, tokens: &[&str]) {
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
        // Keep a small reserve so we never flag on the move we're computing.
        let budget = (t / moves_to_go + inc * 3 / 4).min(t.saturating_sub(50));
        limits.movetime = Some(Duration::from_millis(budget.max(10)));
    }
    if tokens.contains(&"infinite") {
        limits.movetime = None;
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

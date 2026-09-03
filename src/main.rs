mod types;
mod attacks;
mod board;
mod movegen;
mod perft;
mod eval;
mod search;
mod uci;

use board::Board;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(|s| s.as_str()) {
        Some("perft") => {
            let depth: u32 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(5);
            let fen = args.get(3).cloned().unwrap_or_else(|| board::START_FEN.to_string());
            let mut b = Board::from_fen(&fen).expect("bad FEN");
            let start = Instant::now();
            let nodes = perft::perft(&mut b, depth);
            let secs = start.elapsed().as_secs_f64();
            println!("depth {} nodes {} time {:.3}s  {:.2} Mnps",
                     depth, nodes, secs, nodes as f64 / secs / 1e6);
        }
        Some("divide") => {
            let depth: u32 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(1);
            let fen = args.get(3).cloned().unwrap_or_else(|| board::START_FEN.to_string());
            let mut b = Board::from_fen(&fen).expect("bad FEN");
            let mut total = 0;
            for (mv, n) in perft::perft_divide(&mut b, depth) {
                println!("{}: {}", mv, n);
                total += n;
            }
            println!("total: {}", total);
        }
        // Default to UCI so GUIs can launch the binary with no arguments.
        _ => uci::run(),
    }
}

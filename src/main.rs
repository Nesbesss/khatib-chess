mod types;
mod attacks;
mod board;
mod movegen;
mod perft;
mod eval;
mod search;
mod uci;
mod server;
mod nnue;
mod datagen;

use board::Board;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();

    // Load a network if one was given or sits next to the binary.
    let net_path = args.iter().position(|a| a == "--net")
        .and_then(|i| args.get(i + 1).cloned())
        .or_else(|| std::path::Path::new("net.nnue").exists()
                    .then(|| "net.nnue".to_string()));
    if let Some(p) = net_path {
        match eval::load_network(&p) {
            Ok(()) => eprintln!("info string loaded network {}", p),
            Err(e) => eprintln!("info string network load failed: {}", e),
        }
    }
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
        Some("datagen") => {
            let games: usize = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(1000);
            let depth: u32 = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(6);
            let out = args.get(4).cloned().unwrap_or_else(|| "data/train.txt".into());
            let threads: usize = args.get(5).and_then(|s| s.parse().ok())
                .unwrap_or_else(|| std::thread::available_parallelism()
                    .map(|n| n.get()).unwrap_or(4));
            std::fs::create_dir_all(std::path::Path::new(&out).parent()
                .unwrap_or(std::path::Path::new("."))).ok();
            datagen::run(games, depth, &out, threads);
        }
        Some("serve") | Some("web") => {
            let port = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(8080);
            server::run(port);
        }
        // Default to UCI so GUIs can launch the binary with no arguments.
        _ => uci::run(),
    }
}

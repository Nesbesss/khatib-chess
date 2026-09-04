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
mod book;
mod datagen;
mod match_play;

use board::Board;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();

    // Match mode loads nets per-player, so skip the global load — otherwise
    // the baseline would silently use the challenger's network too.
    let is_match = args.get(1).map(|s| s == "match").unwrap_or(false);
    let net_path = if is_match { None } else { args.iter().position(|a| a == "--net")
        .and_then(|i| args.get(i + 1).cloned())
        .or_else(|| std::path::Path::new("net.nnue").exists()
                    .then(|| "net.nnue".to_string())) };
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
            // Default seed varies per run so repeated invocations differ.
            let seed: u64 = args.get(6).and_then(|s| s.parse().ok())
                .unwrap_or_else(|| std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_nanos() as u64).unwrap_or(0x2545F4914F6CDD1D));
            datagen::run(games, depth, &out, threads, seed);
        }
        Some("match") => {
            let net = args.iter().position(|a| a == "--net")
                .and_then(|i| args.get(i + 1).cloned());
            let games: usize = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(100);
            let nodes: u64 = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(20000);
            match_play::run(net.as_deref(), games, nodes);
        }
        Some("bench") => {
            let iters: u64 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(2_000_000);
            let (gops, sink) = nnue::bench_accumulator(iters);
            println!("accumulator: {:.2} G i16-ops/sec (checksum {})", gops, sink);
        }
        Some("serve") | Some("web") => {
            let port = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(8080);
            // `--public` lets other devices on the same network connect.
            let public = args.iter().any(|a| a == "--public");
            server::run_on(port, public);
        }
        // Default to UCI so GUIs can launch the binary with no arguments.
        _ => uci::run(),
    }
}

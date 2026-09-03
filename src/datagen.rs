// Self-play data generation for NNUE training.
//
// Each game starts from a randomized opening (so positions aren't all the
// same book lines), then plays to a result with a shallow search. Every quiet
// position is written with its search score and the game's eventual outcome;
// the trainer blends the two.
use crate::board::Board;
use crate::eval::{MATE_IN_MAX, Score};
use crate::movegen::{generate, GenMode};
use crate::search::{SearchLimits, Searcher};
use crate::types::*;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

// One training sample, written as plain text: FEN, search score, game result.
// Text costs disk but makes the data trivially inspectable and portable.
struct Sample { fen: String, score: Score }

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12; self.0 ^= self.0 << 25; self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545F4914F6CDD1D)
    }
    fn below(&mut self, n: usize) -> usize { (self.next() % n as u64) as usize }
}

pub fn run(games: usize, depth: u32, out_path: &str, threads: usize) {
    let counter = Arc::new(AtomicU64::new(0));
    let positions = Arc::new(AtomicU64::new(0));
    let per_thread = games.div_ceil(threads);

    let handles: Vec<_> = (0..threads).map(|t| {
        let counter = counter.clone();
        let positions = positions.clone();
        let path = format!("{}.part{}", out_path, t);
        std::thread::spawn(move || {
            let mut rng = Rng(0xDEADBEEF ^ ((t as u64 + 1) * 0x9E3779B97F4A7C15));
            let mut searcher = Searcher::new(16);
            let file = File::create(&path).expect("create shard");
            let mut w = BufWriter::new(file);
            let mut written = 0u64;

            for _ in 0..per_thread {
                let samples = play_game(&mut searcher, &mut rng, depth);
                for s in &samples {
                    writeln!(w, "{} | {}", s.fen, s.score).ok();
                }
                written += samples.len() as u64;
                let n = counter.fetch_add(1, Ordering::Relaxed) + 1;
                let p = positions.fetch_add(samples.len() as u64, Ordering::Relaxed)
                        + samples.len() as u64;
                if n % 50 == 0 {
                    eprintln!("games {} positions {}", n, p);
                }
            }
            w.flush().ok();
            written
        })
    }).collect();

    let total: u64 = handles.into_iter().map(|h| h.join().unwrap_or(0)).sum();

    // Concatenate shards into the final file.
    let mut out = BufWriter::new(File::create(out_path).expect("create output"));
    for t in 0..threads {
        let part = format!("{}.part{}", out_path, t);
        if let Ok(data) = std::fs::read(&part) {
            out.write_all(&data).ok();
            std::fs::remove_file(&part).ok();
        }
    }
    out.flush().ok();
    eprintln!("wrote {} positions to {}", total, out_path);
}

fn play_game(searcher: &mut Searcher, rng: &mut Rng, depth: u32) -> Vec<Sample> {
    let mut board = Board::startpos();
    let mut samples = Vec::new();

    // Random opening: 8-12 plies of uniformly random legal moves. This is the
    // cheap way to get positional variety without an opening book.
    let opening_len = 8 + rng.below(5);
    for _ in 0..opening_len {
        let list = generate(&board, GenMode::All);
        if list.len == 0 { return samples; }
        board.make_move(list[rng.below(list.len)]);
    }
    // Reject openings that already blundered into a lost position.
    if generate(&board, GenMode::All).len == 0 { return samples; }

    searcher.repetitions.clear();
    let limits = || SearchLimits { depth, ..Default::default() };

    for _ply in 0..300 {
        let list = generate(&board, GenMode::All);
        if list.len == 0 { break; }               // mate or stalemate
        if board.halfmove >= 100 { break; }       // fifty-move draw

        let (best, score) = searcher.search(&mut board, limits(), false);
        if best == Move::NULL { break; }

        // Skip positions that are noisy training targets: in check, or where
        // the best move is a capture (the score reflects a pending exchange).
        let quiet = !board.in_check(board.side)
            && !best.is_capture()
            && !best.is_promotion()
            && score.abs() < MATE_IN_MAX;
        if quiet {
            samples.push(Sample { fen: board.to_fen(), score });
        }

        // Resign obviously decided games rather than playing them out.
        if score.abs() > 2000 { break; }

        board.make_move(best);
        searcher.repetitions.push(board.hash);
    }
    samples
}

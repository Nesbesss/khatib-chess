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

// One training sample: FEN, search score, and the game's eventual result from
// the side-to-move's view (1.0 win / 0.5 draw / 0.0 loss). Training on both
// lets the net learn from outcomes, not just from the teacher's scores.
struct Sample { fen: String, score: Score, stm_is_white: bool }

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
                    writeln!(w, "{} | {} | {}", s.fen, s.score, s.wdl).ok();
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

// Result of a finished game, from White's perspective.
fn play_game(searcher: &mut Searcher, rng: &mut Rng, depth: u32) -> Vec<OutSample> {
    let mut board = Board::startpos();
    let mut samples: Vec<Sample> = Vec::new();
    let mut result: f32 = 0.5;   // from White's perspective
    let mut decided = false;

    // Opening randomization, but filtered: uniformly random moves hang pieces
    // constantly, which floods the data with decided positions and teaches
    // the net only "who is up material". Pick among moves that keep the game
    // roughly balanced instead.
    let opening_len = 6 + rng.below(5);
    for _ in 0..opening_len {
        let list = generate(&board, GenMode::All);
        if list.len == 0 { return Vec::new(); }

        // Try a few random candidates; keep the first that doesn't wreck the
        // position. Falls back to any legal move if none qualify.
        let mut chosen = list[rng.below(list.len)];
        for _ in 0..6 {
            let cand = list[rng.below(list.len)];
            let undo = board.make_move(cand);
            let after = -crate::eval::evaluate_hce(&board);
            let terminal = generate(&board, GenMode::All).len == 0;
            board.unmake_move(cand, undo);
            if !terminal && after.abs() < 150 { chosen = cand; break; }
        }
        board.make_move(chosen);
    }
    if generate(&board, GenMode::All).len == 0 { return Vec::new(); }
    // Reject any opening that still ended up lopsided.
    if crate::eval::evaluate_hce(&board).abs() > 250 { return Vec::new(); }

    searcher.repetitions.clear();
    let limits = || SearchLimits { depth, ..Default::default() };

    for _ply in 0..300 {
        let list = generate(&board, GenMode::All);
        if list.len == 0 { break; }               // mate or stalemate
        if board.halfmove >= 100 { break; }       // fifty-move draw
        let _ = &list;

        let (best, score) = searcher.search(&mut board, limits(), false);
        if best == Move::NULL { break; }
        let score: Score = score;

        // Skip positions that are noisy training targets: in check, or where
        // the best move is a capture (the score reflects a pending exchange).
        // Keep positions that are quiet AND still competitive. A position
        // that is already winning by a queen teaches nothing but counting.
        let quiet = !board.in_check(board.side)
            && !best.is_capture()
            && !best.is_promotion()
            && score.abs() < MATE_IN_MAX
            && score.abs() < 1000;
        if quiet {
            samples.push(Sample {
                fen: board.to_fen(),
                score,
                stm_is_white: board.side == Color::White,
            });
        }

        // Resign obviously decided games rather than playing them out; the
        // side to move at that point is the one that is losing.
        if score.abs() > 2000 {
            result = if score > 0.0_f32 as Score {
                if board.side == Color::White { 1.0 } else { 0.0 }
            } else if board.side == Color::White { 0.0 } else { 1.0 };
            decided = true;
            break;
        }

        board.make_move(best);
        searcher.repetitions.push(board.hash);
    }

    // Terminal position: mate or a draw by rule.
    if !decided {
        let list = generate(&board, GenMode::All);
        result = if list.len == 0 && board.in_check(board.side) {
            if board.side == Color::White { 0.0 } else { 1.0 }
        } else {
            0.5
        };
    }

    // Convert the White-relative result to each sample's own perspective.
    samples.into_iter().map(|s| OutSample {
        fen: s.fen,
        score: s.score,
        wdl: if s.stm_is_white { result } else { 1.0 - result },
    }).collect()
}

// A sample once the game result is known.
struct OutSample { fen: String, score: Score, wdl: f32 }

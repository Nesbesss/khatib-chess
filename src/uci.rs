// UCI protocol: the lingua franca for chess GUIs and match runners.
use crate::board::{Board, START_FEN};
use crate::movegen::{generate, GenMode};
use crate::search::{SearchLimits, Searcher, ThreadedSearcher, MAX_PLY};
use crate::types::*;

// Largest clock reported this game, used to scale the panic threshold.
static MAX_CLOCK_SEEN: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);
// Score of the previous search, so time management can tell a won position
// from a lost one. Only ever a hint: it is one move stale by definition.
static LAST_SCORE: std::sync::atomic::AtomicI32 =
    std::sync::atomic::AtomicI32::new(0);
// Percentage of the normal time budget to use, set via the Pressure option.
// 100 leaves time management exactly as it was.
static PRESSURE: std::sync::atomic::AtomicU32 =
    std::sync::atomic::AtomicU32::new(100);
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
                println!("id name Khatib v10");
                println!("id author Nassim Khatib");
                // The licence requires this URL to be visible to users, so
                // the engine itself carries it.
                println!("info string https://github.com/Nesbesss/khatib-chess");
                println!("option name Hash type spin default 64 min 1 max 4096");
                println!("option name Threads type spin default 1 min 1 max 64");
                println!("option name OwnBook type check default true");
                println!("option name Pressure type spin default 100 min 50 max 100");
                println!("uciok");
            }
            "isready" => println!("readyok"),
            "ucinewgame" => {
                board = Board::startpos();
                searcher.clear();
                // Per-game statics. The bot plays many games in one process,
                // so without this a long game leaves the panic threshold
                // scaled to the wrong time control for the next one.
                MAX_CLOCK_SEEN.store(0, Ordering::Relaxed);
                LAST_SCORE.store(0, Ordering::Relaxed);
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
                        // Percentage of the normal time budget to spend.
                        // 100 is full strength; lower trades depth for pace,
                        // which is worth it only against an opponent far
                        // enough below us that the lost depth cannot matter.
                        // The caller decides that -- the engine just obeys.
                        (Some(&"Pressure"), Some(p)) => {
                            PRESSURE.store(p.clamp(50, 100) as u32, Ordering::Relaxed);
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
                let (best, score) = searcher.search(&board, limits, true);
                LAST_SCORE.store(score as i32, Ordering::Relaxed);
                println!("bestmove {}", best.to_uci());
            }
            "stop" => searcher.stop.store(true, Ordering::Relaxed),
            "quit" => break,
            // Non-standard helpers.
            "d" | "print" => println!("{}\n{}", render(&board), board.to_fen()),
            "eval" => println!("{}", crate::eval::evaluate(&board)),
            // Non-standard: report what this build actually is. With several
            // networks in circulation, "which net is loaded?" is a real
            // question and guessing from behaviour is unreliable.
            "info" | "version" => {
                println!("Khatib 1.0");
                match crate::eval::network() {
                    Some(_) => println!("  network: loaded ({} hidden, {} buckets)",
                                        crate::nnue::HIDDEN, crate::nnue::BUCKETS),
                    None => println!("  network: none (handcrafted evaluation)"),
                }
                println!("  book: {} positions", crate::book::book().len());
                println!("  threads: {}", searcher.threads);
            }
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
    let (time, inc, opp_time) = match side {
        Color::White => (get("wtime"), get("winc"), get("btime")),
        Color::Black => (get("btime"), get("binc"), get("wtime")),
    };
    if let Some(t) = time {
        let inc = inc.unwrap_or(0);
        // Remember the largest clock seen this game to scale panic mode to
        // the time control rather than an absolute number of seconds.
        let t_start = MAX_CLOCK_SEEN.fetch_max(t, std::sync::atomic::Ordering::Relaxed).max(t);
        let moves_to_go = get("movestogo").unwrap_or(30).max(1);
        // Reserve an overhead margin so we never flag on the move being
        // computed; scale it with the clock so blitz stays safe.
        let overhead = (t / 50).clamp(20, 300);
        let usable = t.saturating_sub(overhead);
        // Soft target: the share of the clock this move deserves. Assume more
        // moves remain than the classical 30 -- a game that goes long is
        // exactly the game where flagging is the real risk -- and bank only
        // part of the increment so the clock is not spent faster than it is
        // replenished.
        // How many moves the clock still has to cover. A flat 40 is far too
        // pessimistic once the clock is short: at 10 s it budgeted 245 ms a
        // move and returned depth-2 moves while hoarding time it would never
        // get to spend, which is exactly the "it's playing just something"
        // behaviour reported at the end of bullet games. Assume fewer moves
        // remain as the clock drains, so the last seconds are actually used.
        let expected_moves = if get("movestogo").is_some() {
            moves_to_go
        } else if t < 15_000 {
            // Short clock: a bullet game rarely has 40 moves left here.
            15
        } else if t < 60_000 {
            25
        } else {
            40
        };
        let soft = (usable / expected_moves + inc / 2).max(5);
        // Hard cap: one move never takes more than a tenth of what is left,
        // and never more than twice the soft target -- a deep iteration cannot
        // be cut mid-way, so the soft limit alone overshoots by well over half.
        let mut hard = (usable / 10).min(soft * 2).max(soft).min(usable);
        // Panic mode: once the clock is low, spend only the increment. A long
        // game is where flagging happens, and against a bot a fast sound move
        // beats a slow perfect one that loses on time. With any increment the
        // clock then holds steady instead of bleeding out.
        // Panic mode only once the clock is genuinely short relative to the
        // whole game. A flat 30 s threshold put every 30+0 bullet game into
        // panic from move one, which is why those games looked like the
        // engine was moving at random: a fixed usable/20 cap regardless of
        // how much of the game remained.
        let panic_at = (t_start / 4).min(20_000).max(5_000);
        if t < panic_at {
            let cap = if inc > 0 { inc.saturating_sub(inc / 5).max(50) }
                      else { (usable / 12).max(80) };
            hard = hard.min(cap);
        }
        // Flagging: when the opponent is far shorter of time than we are,
        // moving quickly puts the clock pressure on them. Lichess sends both
        // clocks and we used to read only our own, so the engine played the
        // same steady pace no matter how low the opponent was.
        //
        // Only press from a position that is not lost -- burning depth to
        // chase a flag while losing on the board trades a draw for a loss --
        // and never below a floor that would make the moves themselves bad.
        // Requires us to hold a real cushion, so this cannot start a mutual
        // time scramble we are losing.
        if let Some(opp) = opp_time {
            // One move stale, and 0 before the first search of a game, which
            // reads as "not losing" -- the safe default here.
            let losing = LAST_SCORE.load(Ordering::Relaxed) < -100;
            // Ratio, not an absolute cushion. An earlier version also
            // required 10 s on our own clock, which at 30+0 is almost never
            // true -- across nine real bullet games it fired on 0 of 369
            // moves, including one where the opponent reached 0.0 s while we
            // held 3 s. Pressure has to work at the clock values a bullet
            // game actually reaches.
            //
            // Press harder the further ahead we are, and keep a floor so the
            // moves stay sound: at 4x we are spending a quarter of the
            // budget, which is still a real search at these speeds.
            let press = if t >= opp.saturating_mul(4) { 4 }
                        else if t >= opp.saturating_mul(2) { 2 }
                        else { 1 };
            if press > 1 && !losing {
                // The floor scales with the clock: 150 ms is sane at 30 s but
                // wasteful at 3 s, where a sound move takes far less.
                let floor = (t / 40).clamp(40, 150);
                hard = hard.min((hard / press).max(floor));
            }
        }
        // Deliberate pace reduction against a much weaker opponent: spend
        // less of the clock every move, so a gap opens from move one instead
        // of only at the end. This costs real strength -- roughly a ply per
        // halving -- which is why the caller only enables it when the rating
        // gap is wide enough to absorb the loss.
        let pressure = PRESSURE.load(Ordering::Relaxed).clamp(50, 100) as u64;
        let (mut soft, mut hard) = (soft, hard);
        if pressure < 100 {
            // The floor has to leave room for a real search, not just a
            // legal move: at 25% of a 30 s budget the engine was returning
            // depth-1 moves off 147 nodes. Keep at least a third of the
            // normal budget, and never less than 250 ms, so the moves stay
            // sound while the pace still visibly quickens.
            let floor = (hard / 3).max(250).min(hard);
            soft = (soft * pressure / 100).max(floor.min(soft));
            hard = (hard * pressure / 100).max(floor);
        }
        // Global per-move cap: fast games, and depth past here buys little.
        let move_cap = std::env::var("MOVE_CAP_MS").ok()
            .and_then(|v| v.parse().ok()).unwrap_or(4000);
        limits.soft_time = Some(Duration::from_millis(soft.min(move_cap)));
        limits.movetime = Some(Duration::from_millis(hard.min(move_cap)));
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

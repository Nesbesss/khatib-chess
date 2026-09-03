// Engine-vs-engine match runner, for measuring whether a change actually
// gains Elo instead of assuming it does.
//
// Both sides run in this process with separate Searchers (and therefore
// separate transposition tables), so only the evaluation differs.
use crate::board::Board;
use crate::eval::{Score, MATE_IN_MAX};
use crate::movegen::{generate, GenMode};
use crate::nnue::{AccStack, Network};
use crate::search::{SearchLimits, Searcher};
use crate::types::*;

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Outcome { WhiteWin, BlackWin, Draw }

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12; self.0 ^= self.0 << 25; self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545F4914F6CDD1D)
    }
    fn below(&mut self, n: usize) -> usize { (self.next() % n as u64) as usize }
}

// A player is "the engine, optionally using this network".
struct Player {
    searcher: Searcher,
    net: Option<&'static Network>,
}

impl Player {
    fn new(net: Option<&'static Network>) -> Player {
        Player { searcher: Searcher::new(32), net }
    }

    fn pick(&mut self, board: &mut Board, nodes: u64) -> (Move, Score) {
        // Rebuild this player's accumulator for the current position; the two
        // players may use different nets, so it cannot be shared.
        self.searcher.acc = self.net.map(|n| AccStack::new(n, board));
        self.searcher.forced_net = self.net;
        let limits = SearchLimits { nodes: Some(nodes), ..Default::default() };
        self.searcher.search(board, limits, false)
    }
}

fn play_game(a: &mut Player, b: &mut Player, opening: &[Move], nodes: u64,
             a_is_white: bool) -> Outcome
{
    let mut board = Board::startpos();
    for &m in opening { board.make_move(m); }

    let mut history = vec![board.hash];

    for _ in 0..400 {
        let list = generate(&board, GenMode::All);
        if list.len == 0 {
            return if board.in_check(board.side) {
                if board.side == Color::White { Outcome::BlackWin } else { Outcome::WhiteWin }
            } else { Outcome::Draw };
        }
        if board.halfmove >= 100 { return Outcome::Draw; }
        // Threefold repetition.
        if history.iter().filter(|&&h| h == board.hash).count() >= 3 {
            return Outcome::Draw;
        }

        let white_to_move = board.side == Color::White;
        let use_a = white_to_move == a_is_white;
        let (mv, score) = if use_a { a.pick(&mut board, nodes) }
                          else { b.pick(&mut board, nodes) };
        if mv == Move::NULL { return Outcome::Draw; }

        // Adjudicate decided games rather than playing out 200 more plies.
        if score.abs() > MATE_IN_MAX || score > 2500 {
            let winner_is_side_to_move = score > 0;
            let white_wins = winner_is_side_to_move == white_to_move;
            return if white_wins { Outcome::WhiteWin } else { Outcome::BlackWin };
        }

        board.make_move(mv);
        history.push(board.hash);
    }
    Outcome::Draw
}

// Random opening line, played by both colors so the pair is fair.
fn random_opening(rng: &mut Rng, plies: usize) -> Vec<Move> {
    loop {
        let mut board = Board::startpos();
        let mut moves = Vec::new();
        let mut ok = true;
        for _ in 0..plies {
            let list = generate(&board, GenMode::All);
            if list.len == 0 { ok = false; break; }
            let m = list[rng.below(list.len)];
            moves.push(m);
            board.make_move(m);
        }
        // Reject openings that are already lopsided or terminal.
        if ok && generate(&board, GenMode::All).len > 0
            && crate::eval::evaluate_hce(&board).abs() < 300
        {
            return moves;
        }
    }
}

fn elo_from_score(score: f64, games: usize) -> (f64, f64) {
    if score <= 0.0 || score >= 1.0 {
        return (if score >= 1.0 { f64::INFINITY } else { f64::NEG_INFINITY }, 0.0);
    }
    let elo = -400.0 * ((1.0 / score) - 1.0).log10();
    // Standard error of the Elo estimate from the binomial variance.
    let var = score * (1.0 - score) / games as f64;
    let se = var.sqrt();
    let margin = 400.0 / (10f64).ln() * se / (score * (1.0 - score));
    (elo, margin * 1.96)
}

pub fn run(net_path: Option<&str>, games: usize, nodes: u64) {
    // Load the challenger network into a leaked box so both players can hold
    // a 'static reference to it.
    let net: Option<&'static Network> = match net_path {
        Some(p) => match crate::nnue::load(p) {
            Ok(n) => {
                eprintln!("challenger: NNUE {}", p);
                Some(Box::leak(n))
            }
            Err(e) => { eprintln!("cannot load {}: {}", p, e); return; }
        },
        None => { eprintln!("no network given"); return; }
    };
    eprintln!("baseline:   handcrafted eval");
    eprintln!("{} game pairs at {} nodes/move\n", games, nodes);

    let mut rng = Rng(0x1234_5678_9ABC_DEF0);
    let (mut wins, mut losses, mut draws) = (0usize, 0usize, 0usize);

    for i in 0..games {
        let opening = random_opening(&mut rng, 8);
        // Play each opening twice with colors reversed: removes the
        // first-move advantage from the measurement.
        for &challenger_white in &[true, false] {
            let mut challenger = Player::new(net);
            let mut baseline = Player::new(None);
            let out = play_game(&mut challenger, &mut baseline, &opening,
                                nodes, challenger_white);
            let challenger_scored = match out {
                Outcome::Draw => { draws += 1; continue; }
                Outcome::WhiteWin => challenger_white,
                Outcome::BlackWin => !challenger_white,
            };
            if challenger_scored { wins += 1; } else { losses += 1; }
        }

        let total = wins + losses + draws;
        if total % 20 == 0 || i == games - 1 {
            let score = (wins as f64 + 0.5 * draws as f64) / total as f64;
            let (elo, err) = elo_from_score(score, total);
            eprintln!("{:4} games  +{} ={} -{}  score {:.1}%  Elo {:+.0} ± {:.0}",
                      total, wins, draws, losses, score * 100.0, elo, err);
        }
    }

    let total = wins + losses + draws;
    let score = (wins as f64 + 0.5 * draws as f64) / total as f64;
    let (elo, err) = elo_from_score(score, total);
    println!("\nfinal: +{} ={} -{} of {}  score {:.1}%", wins, draws, losses, total,
             score * 100.0);
    println!("NNUE vs handcrafted: {:+.0} Elo (95% CI ±{:.0})", elo, err);
    if elo - err > 0.0 {
        println!("=> network is stronger");
    } else if elo + err < 0.0 {
        println!("=> network is WEAKER");
    } else {
        println!("=> inconclusive, needs more games");
    }
}

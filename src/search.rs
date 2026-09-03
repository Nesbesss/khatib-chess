// Iterative-deepening alpha-beta with a transposition table, quiescence,
// null-move pruning, LMR, killers and history.
use crate::board::{Board, Undo};
use crate::eval::*;
use crate::movegen::*;
use crate::types::*;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub const MAX_PLY: usize = 128;

#[derive(Copy, Clone, PartialEq)]
pub enum Bound { Exact, Lower, Upper }

#[derive(Copy, Clone)]
pub struct TtEntry {
    pub key: u64,
    pub mv: Move,
    pub score: i16,
    pub depth: i8,
    pub bound: Bound,
}

impl TtEntry {
    const EMPTY: TtEntry = TtEntry {
        key: 0, mv: Move::NULL, score: 0, depth: -1, bound: Bound::Exact,
    };
}

pub struct Tt {
    entries: Vec<TtEntry>,
    mask: usize,
}

impl Tt {
    pub fn new(mb: usize) -> Tt {
        // Round down to a power of two so indexing is a mask, not a modulo.
        let n = (mb * 1024 * 1024 / std::mem::size_of::<TtEntry>()).next_power_of_two() / 2;
        Tt { entries: vec![TtEntry::EMPTY; n], mask: n - 1 }
    }

    pub fn clear(&mut self) {
        self.entries.fill(TtEntry::EMPTY);
    }

    #[inline(always)]
    pub fn probe(&self, key: u64) -> Option<&TtEntry> {
        let e = &self.entries[(key as usize) & self.mask];
        if e.key == key { Some(e) } else { None }
    }

    #[inline(always)]
    pub fn store(&mut self, key: u64, mv: Move, score: i16, depth: i8, bound: Bound) {
        let slot = &mut self.entries[(key as usize) & self.mask];
        // Depth-preferred replacement, but always take an empty or stale slot.
        if slot.key != key || depth >= slot.depth {
            *slot = TtEntry { key, mv, score, depth, bound };
        }
    }
}

pub struct SearchLimits {
    pub depth: u32,
    // Hard limit: abort mid-search once exceeded.
    pub movetime: Option<Duration>,
    // Soft limit: don't begin a new iteration past this. Lets a promising
    // depth finish instead of being cut off with a half-searched root.
    pub soft_time: Option<Duration>,
    pub nodes: Option<u64>,
}

impl Default for SearchLimits {
    fn default() -> Self {
        SearchLimits {
            depth: MAX_PLY as u32,
            movetime: None,
            soft_time: None,
            nodes: None,
        }
    }
}

pub struct Searcher {
    pub tt: Tt,
    pub nodes: u64,
    pub stop: Arc<AtomicBool>,
    start: Instant,
    limits: SearchLimits,
    // Quiet moves that caused a beta cutoff, indexed by ply.
    killers: [[Move; 2]; MAX_PLY],
    // history[color][from][to], incremented on cutoffs.
    history: [[[i32; 64]; 64]; 2],
    // Position hashes along the current line, for repetition detection.
    pub repetitions: Vec<u64>,
    stopped: bool,
    // Visualizer: root-level search tree, captured only when requested.
    pub capture_tree: bool,
    pub tree: Vec<TreeNode>,
    // Incremental NNUE accumulator, kept in lockstep with make/unmake.
    pub acc: Option<crate::nnue::AccStack>,
    // Overrides the globally-loaded net; lets a match pit two evals in one
    // process. None means "use whatever eval::network() returns".
    pub forced_net: Option<&'static crate::nnue::Network>,
}

impl Searcher {
    pub fn new(tt_mb: usize) -> Searcher {
        Searcher {
            tt: Tt::new(tt_mb),
            nodes: 0,
            stop: Arc::new(AtomicBool::new(false)),
            start: Instant::now(),
            limits: SearchLimits::default(),
            killers: [[Move::NULL; 2]; MAX_PLY],
            history: [[[0; 64]; 64]; 2],
            repetitions: Vec::with_capacity(1024),
            stopped: false,
            capture_tree: false,
            tree: Vec::new(),
            acc: None,
            forced_net: None,
        }
    }

    // Evaluate through the incremental accumulator when a net is loaded.
    #[inline(always)]
    fn net(&self) -> Option<&'static crate::nnue::Network> {
        self.forced_net.or_else(crate::eval::network)
    }

    #[inline(always)]
    fn eval(&self, board: &Board) -> Score {
        // Applies to both evaluations: no material can force mate.
        if crate::eval::is_insufficient_material(board) { return crate::eval::DRAW; }
        match (self.net(), &self.acc) {
            (Some(net), Some(stack)) =>
                crate::nnue::evaluate(net, stack.top(), board.side),
            // forced_net set but no accumulator: fall back to a refresh so a
            // match never silently compares the wrong evaluations.
            (Some(net), None) => {
                let mut acc = crate::nnue::Accumulator::new(net);
                acc.refresh(net, board);
                crate::nnue::evaluate(net, &acc, board.side)
            }
            _ => crate::eval::evaluate_hce(board),
        }
    }

    #[inline(always)]
    fn acc_push(&mut self, board: &Board, m: Move) {
        let n = self.forced_net.or_else(crate::eval::network);
        if let (Some(net), Some(stack)) = (n, self.acc.as_mut()) {
            stack.push(net, board, m);
        }
    }

    #[inline(always)]
    fn acc_pop(&mut self) {
        if let Some(stack) = self.acc.as_mut() { stack.pop(); }
    }

    #[inline(always)]
    fn should_stop(&mut self) -> bool {
        if self.stopped { return true; }
        // Checking the clock every node is a measurable cost; sample instead.
        if self.nodes & 2047 == 0 {
            if self.stop.load(Ordering::Relaxed) { self.stopped = true; return true; }
            if let Some(mt) = self.limits.movetime {
                if self.start.elapsed() >= mt { self.stopped = true; return true; }
            }
            if let Some(n) = self.limits.nodes {
                if self.nodes >= n { self.stopped = true; return true; }
            }
        }
        false
    }

    // Returns (best move, score). Prints UCI info lines as it deepens.
    pub fn search(&mut self, board: &mut Board, limits: SearchLimits, verbose: bool)
        -> (Move, Score)
    {
        self.nodes = 0;
        self.start = Instant::now();
        self.limits = limits;
        self.stopped = false;
        self.stop.store(false, Ordering::Relaxed);
        self.killers = [[Move::NULL; 2]; MAX_PLY];
        self.history = [[[0; 64]; 64]; 2];
        if let Some(net) = self.forced_net.or_else(crate::eval::network) {
            match self.acc.as_mut() {
                Some(stack) => stack.reset(net, board),
                None => self.acc = Some(crate::nnue::AccStack::new(net, board)),
            }
        }

        let mut best = Move::NULL;
        let mut best_score = 0;
        let mut pv = Vec::new();
        let mut prev_best = Move::NULL;

        for depth in 1..=self.limits.depth {
            // Aspiration windows: the score rarely moves far between
            // iterations, so search a narrow window around the last one and
            // widen only on a fail. Cheaper than a full-width search.
            let score = self.aspiration(board, depth as i32, best_score);

            // A stopped search has a corrupt partial result; keep the last
            // completed depth's move instead.
            if self.stopped && depth > 1 { break; }

            best_score = score;
            pv = self.extract_pv(board, depth as usize);
            if let Some(&m) = pv.first() { best = m; }

            if verbose {
                let ms = self.start.elapsed().as_millis().max(1);
                let nps = (self.nodes as u128 * 1000 / ms) as u64;
                let score_str = if score.abs() > MATE_IN_MAX {
                    // Convert to moves-to-mate, signed by who's delivering it.
                    let plies = MATE - score.abs();
                    let mate_in = (plies + 1) / 2;
                    format!("mate {}", if score > 0 { mate_in } else { -mate_in })
                } else {
                    format!("cp {}", score)
                };
                let pv_str: Vec<String> = pv.iter().map(|m| m.to_uci()).collect();
                println!("info depth {} score {} nodes {} nps {} time {} pv {}",
                         depth, score_str, self.nodes, nps, ms, pv_str.join(" "));
            }

            // Found a forced mate; deeper search can't improve on it.
            if score.abs() > MATE_IN_MAX { break; }

            // Don't start a depth we almost certainly can't finish. A stable
            // best move lets us stop earlier; an unstable one buys more time.
            let soft = self.limits.soft_time.or_else(||
                self.limits.movetime.map(|m| m.mul_f32(0.5)));
            if let Some(soft) = soft {
                let stable = best == prev_best;
                prev_best = best;
                let factor = if stable { 1.0 } else { 1.35 };
                if self.start.elapsed().as_secs_f32() > soft.as_secs_f32() * factor {
                    break;
                }
            }
        }

        // Safety net: never return a null move from a legal position.
        if best == Move::NULL {
            let list = generate(board, GenMode::All);
            if list.len > 0 { best = list[0]; }
        }
        (best, best_score)
    }

    // Search one iteration, retrying with a wider window if the score falls
    // outside the aspiration bounds.
    fn aspiration(&mut self, board: &mut Board, depth: i32, prev: Score) -> Score {
        // Shallow depths are cheap and unstable; just search them fully.
        if depth <= 4 || prev.abs() > MATE_IN_MAX {
            return self.alphabeta(board, depth, 0, -MATE, MATE, true);
        }
        let mut delta: Score = 20;
        let mut alpha = (prev - delta).max(-MATE);
        let mut beta = (prev + delta).min(MATE);
        loop {
            let score = self.alphabeta(board, depth, 0, alpha, beta, true);
            if self.stopped { return score; }
            if score <= alpha {
                // Fail low: the position is worse than expected. Widen down
                // and keep beta so the re-search stays cheap.
                beta = (alpha + beta) / 2;
                alpha = (score - delta).max(-MATE);
            } else if score >= beta {
                beta = (score + delta).min(MATE);
            } else {
                return score;
            }
            delta += delta / 2;
            if delta > 800 {
                return self.alphabeta(board, depth, 0, -MATE, MATE, true);
            }
        }
    }

    fn alphabeta(&mut self, board: &mut Board, mut depth: i32, ply: usize,
                 mut alpha: Score, beta: Score, allow_null: bool) -> Score
    {
        if self.should_stop() { return 0; }
        self.nodes += 1;

        let is_root = ply == 0;
        let in_check = board.in_check(board.side);

        // Check extension: don't drop into quiescence while in check.
        if in_check { depth += 1; }

        if depth <= 0 {
            return self.quiesce(board, ply, alpha, beta);
        }

        if !is_root {
            // Draw by repetition or fifty-move rule.
            if self.is_repetition(board) || board.halfmove >= 100 {
                return DRAW;
            }
            // Mate-distance pruning: a shorter mate is already available.
            let mate_alpha = alpha.max(-MATE + ply as Score);
            let mate_beta = beta.min(MATE - ply as Score - 1);
            if mate_alpha >= mate_beta { return mate_alpha; }
        }

        if ply >= MAX_PLY - 1 { return self.eval(board); }

        // Transposition table probe.
        let mut tt_move = Move::NULL;
        if let Some(e) = self.tt.probe(board.hash) {
            tt_move = e.mv;
            if !is_root && e.depth as i32 >= depth {
                let score = from_tt_score(e.score as Score, ply);
                match e.bound {
                    Bound::Exact => return score,
                    Bound::Lower if score >= beta => return score,
                    Bound::Upper if score <= alpha => return score,
                    _ => {}
                }
            }
        }

        let is_pv = beta - alpha > 1;
        let static_eval = self.eval(board);

        // Reverse futility: if we're far enough ahead that even giving up
        // `margin` per remaining ply leaves us above beta, prune.
        if !is_pv && !in_check && depth <= 6 && static_eval - 100 * depth >= beta
            && static_eval < MATE_IN_MAX
        {
            return static_eval;
        }

        // Null-move pruning: pass the turn; if we're still winning, the real
        // move will be at least as good. Skipped in zugzwang-prone endings.
        if allow_null && !is_pv && !in_check && depth >= 3
            && static_eval >= beta && self.has_non_pawn_material(board)
        {
            let r = 3 + depth / 4;
            if let Some(stack) = self.acc.as_mut() { stack.push_null(); }
            let undo = self.make_null(board);
            let score = -self.alphabeta(board, depth - r, ply + 1, -beta, -beta + 1, false);
            self.unmake_null(board, undo);
            self.acc_pop();
            if self.stopped { return 0; }
            if score >= beta {
                // Don't return unproven mate scores from a null-move search.
                return if score > MATE_IN_MAX { beta } else { score };
            }
        }

        let mut list = generate(board, GenMode::All);
        if list.len == 0 {
            // No legal moves: mate if in check, else stalemate.
            return if in_check { -MATE + ply as Score } else { DRAW };
        }

        self.order_moves(board, &mut list, tt_move, ply);

        // Each entry into the root starts a fresh tree for this depth.
        if self.capture_tree && ply == 0 { self.tree.clear(); }

        let mut best_score = -MATE;
        let mut best_move = Move::NULL;
        let orig_alpha = alpha;
        let mut searched_quiets: Vec<Move> = Vec::new();

        for i in 0..list.len {
            let m = list[i];
            let is_quiet = !m.is_capture() && !m.is_promotion();

            // Tree capture: record this root move and how much it cost.
            let tree_idx = if self.capture_tree && ply == 0 {
                self.tree.push(TreeNode {
                    mv: m, score: -MATE, depth, parent: usize::MAX,
                    children: Vec::new(), nodes: 0, pruned: false, best: false,
                });
                Some(self.tree.len() - 1)
            } else { None };
            let nodes_before = self.nodes;

            self.acc_push(board, m);
            let undo = board.make_move(m);
            self.repetitions.push(board.hash);

            let mut score;
            if i == 0 {
                score = -self.alphabeta(board, depth - 1, ply + 1, -beta, -alpha, true);
            } else {
                // Late move reductions: quiet moves late in a well-ordered
                // list are unlikely to be best, so search them shallower.
                let mut reduction = 0;
                if depth >= 3 && is_quiet && !in_check {
                    reduction = 1 + (depth / 6) + (i / 8) as i32;
                    if is_pv { reduction -= 1; }
                    reduction = reduction.clamp(0, depth - 2);
                }
                // Null-window scout search first; re-search only if it fails high.
                score = -self.alphabeta(board, depth - 1 - reduction, ply + 1,
                                        -alpha - 1, -alpha, true);
                if score > alpha && reduction > 0 {
                    score = -self.alphabeta(board, depth - 1, ply + 1,
                                            -alpha - 1, -alpha, true);
                }
                if score > alpha && score < beta {
                    score = -self.alphabeta(board, depth - 1, ply + 1, -beta, -alpha, true);
                }
            }

            self.repetitions.pop();

            if let Some(ti) = tree_idx {
                self.tree[ti].score = score;
                self.tree[ti].nodes = self.nodes - nodes_before;
                // A move that never beat alpha was effectively refuted.
                self.tree[ti].pruned = score <= alpha && i > 0;
                // Pull this move's best reply from the TT for the second level.
                let replies = self.child_replies(board, 2);
                for (rm, rs) in replies {
                    self.tree.push(TreeNode {
                        mv: rm, score: rs, depth: depth - 1, parent: ti,
                        children: Vec::new(), nodes: 0, pruned: false, best: false,
                    });
                    let ci = self.tree.len() - 1;
                    self.tree[ti].children.push(ci);
                }
            }

            board.unmake_move(m, undo);
            self.acc_pop();

            if self.stopped { return 0; }

            if score > best_score {
                best_score = score;
                best_move = m;
                if score > alpha {
                    alpha = score;
                    if alpha >= beta {
                        // Beta cutoff. Reward this quiet move so it's tried
                        // earlier next time; penalise the quiets that failed.
                        if is_quiet {
                            self.store_killer(m, ply);
                            let bonus = (depth * depth) as i32;
                            let c = board.side.idx();
                            let h = &mut self.history[c][m.from() as usize][m.to() as usize];
                            *h = (*h + bonus).min(16384);
                            for &q in &searched_quiets {
                                let h = &mut self.history[c][q.from() as usize][q.to() as usize];
                                *h = (*h - bonus).max(-16384);
                            }
                        }
                        break;
                    }
                }
            }
            if is_quiet { searched_quiets.push(m); }
        }

        if self.capture_tree && ply == 0 {
            if let Some(n) = self.tree.iter_mut().find(|n| n.mv == best_move
                                                       && n.parent == usize::MAX) {
                n.best = true;
            }
        }

        let bound = if best_score >= beta { Bound::Lower }
                    else if best_score > orig_alpha { Bound::Exact }
                    else { Bound::Upper };
        self.tt.store(board.hash, best_move, to_tt_score(best_score, ply) as i16,
                      depth as i8, bound);

        best_score
    }

    // Search only captures and promotions until the position is quiet, so we
    // never evaluate in the middle of an exchange.
    fn quiesce(&mut self, board: &mut Board, ply: usize,
               mut alpha: Score, beta: Score) -> Score
    {
        if self.should_stop() { return 0; }
        self.nodes += 1;

        if ply >= MAX_PLY - 1 { return self.eval(board); }

        // Stand pat: we're not obliged to capture.
        let stand_pat = self.eval(board);
        if stand_pat >= beta { return stand_pat; }
        if stand_pat > alpha { alpha = stand_pat; }

        let mut list = generate(board, GenMode::Captures);
        self.order_moves(board, &mut list, Move::NULL, ply);

        let mut best = stand_pat;
        for i in 0..list.len {
            let m = list[i];

            // Delta pruning: even winning this piece outright wouldn't reach
            // alpha, so the whole line is hopeless.
            if !m.is_promotion() {
                if let Some((_, victim)) = board.piece_at(m.to()) {
                    const PIECE_VAL: [Score; 6] = [100, 320, 330, 500, 900, 0];
                    if stand_pat + PIECE_VAL[victim.idx()] + 200 < alpha { continue; }
                }
            }
            // Skip captures that lose material outright.
            if self.see(board, m) < 0 { continue; }

            self.acc_push(board, m);
            let undo = board.make_move(m);
            let score = -self.quiesce(board, ply + 1, -beta, -alpha);
            board.unmake_move(m, undo);
            self.acc_pop();

            if self.stopped { return 0; }
            if score > best {
                best = score;
                if score > alpha { alpha = score; }
                if alpha >= beta { break; }
            }
        }
        best
    }

    // Static exchange evaluation: material won or lost if both sides trade
    // optimally on this square. Negative means the capture loses material.
    fn see(&self, board: &Board, m: Move) -> Score {
        const VAL: [Score; 6] = [100, 320, 330, 500, 900, 10000];
        let to = m.to();
        let from = m.from();

        let Some((_, attacker)) = board.piece_at(from) else { return 0 };
        let target_val = match board.piece_at(to) {
            Some((_, v)) => VAL[v.idx()],
            None if m.is_ep() => VAL[Piece::Pawn.idx()],
            None => 0,
        };

        // gain[d] = material balance after d captures, from mover's view.
        let mut gain = [0i32; 32];
        let mut d = 0;
        gain[0] = target_val;

        let mut occ = board.all & !bb(from);
        let mut side = board.side.flip();
        let mut current_val = VAL[attacker.idx()];

        loop {
            d += 1;
            if d >= 31 { break; }
            gain[d] = current_val - gain[d - 1];

            let attackers = board.attackers_to(to, side, occ) & occ;
            if attackers == 0 { break; }

            // Always recapture with the least valuable attacker.
            let mut found = None;
            for p in [Piece::Pawn, Piece::Knight, Piece::Bishop,
                      Piece::Rook, Piece::Queen, Piece::King] {
                let candidates = attackers & board.pieces[side.idx()][p.idx()];
                if candidates != 0 {
                    found = Some((candidates.trailing_zeros() as u8, p));
                    break;
                }
            }
            let Some((sq, piece)) = found else { break };

            occ &= !bb(sq);
            current_val = VAL[piece.idx()];
            side = side.flip();
        }

        // Negamax back up: each side stops capturing when it's unprofitable.
        while d > 1 {
            d -= 1;
            gain[d - 1] = -std::cmp::max(-gain[d - 1], gain[d]);
        }
        gain[0]
    }

    fn order_moves(&self, board: &Board, list: &mut MoveList, tt_move: Move, ply: usize) {
        let mut scores = [0i32; MAX_MOVES];
        let c = board.side.idx();
        for i in 0..list.len {
            let m = list[i];
            scores[i] = if m == tt_move {
                1_000_000
            } else if m.is_promotion() {
                900_000 + m.promo_piece().idx() as i32
            } else if m.is_capture() {
                // MVV-LVA: most valuable victim, least valuable attacker.
                let victim = board.piece_at(m.to()).map(|(_, p)| p.idx() as i32).unwrap_or(0);
                let attacker = board.piece_at(m.from()).map(|(_, p)| p.idx() as i32).unwrap_or(0);
                800_000 + victim * 100 - attacker
            } else if m == self.killers[ply][0] {
                700_000
            } else if m == self.killers[ply][1] {
                690_000
            } else {
                self.history[c][m.from() as usize][m.to() as usize]
            };
        }
        // Insertion sort: lists are short and nearly sorted already.
        for i in 1..list.len {
            let (m, s) = (list.moves[i], scores[i]);
            let mut j = i;
            while j > 0 && scores[j - 1] < s {
                list.moves[j] = list.moves[j - 1];
                scores[j] = scores[j - 1];
                j -= 1;
            }
            list.moves[j] = m;
            scores[j] = s;
        }
    }

    #[inline(always)]
    fn store_killer(&mut self, m: Move, ply: usize) {
        if self.killers[ply][0] != m {
            self.killers[ply][1] = self.killers[ply][0];
            self.killers[ply][0] = m;
        }
    }

    fn has_non_pawn_material(&self, board: &Board) -> bool {
        let c = board.side.idx();
        board.pieces[c][Piece::Knight.idx()] | board.pieces[c][Piece::Bishop.idx()]
            | board.pieces[c][Piece::Rook.idx()] | board.pieces[c][Piece::Queen.idx()] != 0
    }

    fn is_repetition(&self, board: &Board) -> bool {
        // One prior occurrence in the search tree is enough to claim a draw;
        // repeating is always available to the side that wants it.
        let n = self.repetitions.len();
        let limit = board.halfmove as usize;
        self.repetitions.iter().rev().take(limit.min(n)).skip(1)
            .step_by(2).any(|&h| h == board.hash)
    }

    fn make_null(&mut self, board: &mut Board) -> Undo {
        let undo = Undo {
            captured: None,
            castling: board.castling,
            ep_square: board.ep_square,
            halfmove: board.halfmove,
            hash: board.hash,
        };
        let z = crate::board::zobrist();
        if let Some(ep) = board.ep_square {
            board.hash ^= z.ep_file[file_of(ep) as usize];
        }
        board.ep_square = None;
        board.hash ^= z.side;
        board.side = board.side.flip();
        undo
    }

    fn unmake_null(&mut self, board: &mut Board, undo: Undo) {
        board.side = board.side.flip();
        board.ep_square = undo.ep_square;
        board.hash = undo.hash;
    }

    fn extract_pv(&self, board: &mut Board, max_len: usize) -> Vec<Move> {
        let mut pv = Vec::new();
        let mut undos = Vec::new();
        for _ in 0..max_len {
            let Some(e) = self.tt.probe(board.hash) else { break };
            let m = e.mv;
            if m == Move::NULL { break; }
            // Guard against a hash collision handing us an illegal move.
            let list = generate(board, GenMode::All);
            if !list.as_slice().contains(&m) { break; }
            pv.push(m);
            undos.push((m, board.make_move(m)));
        }
        while let Some((m, u)) = undos.pop() { board.unmake_move(m, u); }
        pv
    }
}

// Mate scores are stored relative to the current ply so an entry found at a
// different depth still means "mate in N from here".
#[inline(always)]
fn to_tt_score(score: Score, ply: usize) -> Score {
    if score > MATE_IN_MAX { score + ply as Score }
    else if score < -MATE_IN_MAX { score - ply as Score }
    else { score }
}

#[inline(always)]
fn from_tt_score(score: Score, ply: usize) -> Score {
    if score > MATE_IN_MAX { score - ply as Score }
    else if score < -MATE_IN_MAX { score + ply as Score }
    else { score }
}

// ---------------------------------------------------------------------------
// Visualizer support: streaming callbacks and search-tree capture.
// ---------------------------------------------------------------------------

// One node in the captured search tree.
pub struct TreeNode {
    pub mv: Move,
    pub score: Score,
    pub depth: i32,
    pub parent: usize,
    pub children: Vec<usize>,
    pub nodes: u64,
    pub pruned: bool,
    pub best: bool,
}

impl Searcher {
    // Same as search(), but each completed depth is handed to `on_info` as a
    // JSON object instead of printed. Used by the visualizer's SSE stream.
    pub fn search_with_callback<F: FnMut(String)>(
        &mut self, board: &mut Board, limits: SearchLimits, mut on_info: F,
    ) -> (Move, Score) {
        self.nodes = 0;
        self.start = Instant::now();
        self.limits = limits;
        self.stopped = false;
        self.stop.store(false, Ordering::Relaxed);
        self.killers = [[Move::NULL; 2]; MAX_PLY];
        self.history = [[[0; 64]; 64]; 2];
        if let Some(net) = self.forced_net.or_else(crate::eval::network) {
            match self.acc.as_mut() {
                Some(stack) => stack.reset(net, board),
                None => self.acc = Some(crate::nnue::AccStack::new(net, board)),
            }
        }

        let mut best = Move::NULL;
        let mut best_score = 0;

        for depth in 1..=self.limits.depth {
            // Capture the root breakdown at this depth for the tree view.
            self.capture_tree = true;
            self.tree.clear();
            let score = self.aspiration(board, depth as i32, best_score);
            self.capture_tree = false;

            if self.stopped && depth > 1 { break; }

            best_score = score;
            let pv = self.extract_pv(board, depth as usize);
            if let Some(&m) = pv.first() { best = m; }

            let ms = self.start.elapsed().as_millis().max(1);
            let nps = (self.nodes as u128 * 1000 / ms) as u64;
            let pv_str: Vec<String> = pv.iter().map(|m| format!("\"{}\"", m.to_uci())).collect();

            // Scores along the PV, so the graph can annotate each step.
            let pv_scores = self.pv_scores(board, &pv);
            on_info(format!(
                "{{\"depth\":{},\"score\":{},\"nodes\":{},\"nps\":{},\"time\":{},\
                  \"pv\":[{}],\"pvScores\":[{}],\"tree\":{}}}",
                depth, crate::server::score_json(score), self.nodes, nps, ms,
                pv_str.join(","), pv_scores.join(","), self.tree_json(board)));

            if score.abs() > MATE_IN_MAX { break; }
            if let Some(mt) = self.limits.movetime {
                if self.start.elapsed() > mt.mul_f32(0.5) { break; }
            }
        }

        if best == Move::NULL {
            let list = generate(board, GenMode::All);
            if list.len > 0 { best = list[0]; }
        }
        (best, best_score)
    }

    // Walk the PV, reporting the TT score at each step so the visualizer can
    // show how the evaluation evolves along the main line.
    fn pv_scores(&self, board: &mut Board, pv: &[Move]) -> Vec<String> {
        let mut out = Vec::new();
        let mut undos = Vec::new();
        for &m in pv {
            let list = generate(board, GenMode::All);
            if !list.as_slice().contains(&m) { break; }
            undos.push((m, board.make_move(m)));
            let sc = match self.tt.probe(board.hash) {
                // TT scores are from the side to move; flip to a consistent
                // root-relative view so the sequence reads monotonically.
                Some(e) => {
                    let s = from_tt_score(e.score as Score, undos.len());
                    if undos.len() % 2 == 1 { -s } else { s }
                }
                None => 0,
            };
            out.push(crate::server::score_json(sc));
        }
        while let Some((m, u)) = undos.pop() { board.unmake_move(m, u); }
        out
    }

    // Serialize the captured root tree. Each root move carries its score, the
    // subtree size it cost, and its best reply — enough to draw the graph.
    fn tree_json(&self, board: &mut Board) -> String {
        let mut out = Vec::new();
        // Only true root moves; replies nest inside their parent. A move can
        // be re-searched within a depth, so keep only its last entry.
        let mut seen: Vec<Move> = Vec::new();
        let roots: Vec<&TreeNode> = {
            let mut v: Vec<&TreeNode> = self.tree.iter()
                .filter(|n| n.parent == usize::MAX).collect();
            v.reverse();
            let mut keep = Vec::new();
            for n in v {
                if !seen.contains(&n.mv) { seen.push(n.mv); keep.push(n); }
            }
            keep
        };
        for n in roots {
            // San-ish label: the UCI move plus the piece that moves it.
            let piece = board.piece_at(n.mv.from())
                .map(|(c, p)| p.to_char(c)).unwrap_or('?');
            out.push(format!(
                "{{\"move\":\"{}\",\"piece\":\"{}\",\"score\":{},\"nodes\":{},\
                  \"pruned\":{},\"best\":{},\"replies\":[{}]}}",
                n.mv.to_uci(), piece, crate::server::score_json(n.score),
                n.nodes, n.pruned, n.best,
                n.children.iter().filter_map(|&c| self.tree.get(c)).map(|c| {
                    format!("{{\"move\":\"{}\",\"score\":{},\"nodes\":{},\"pruned\":{}}}",
                            c.mv.to_uci(), crate::server::score_json(c.score),
                            c.nodes, c.pruned)
                }).collect::<Vec<_>>().join(",")));
        }
        format!("[{}]", out.join(","))
    }
}

impl Searcher {
    // Top replies to the current position, read from the TT. Cheap: the
    // search just visited these, so the entries are warm.
    fn child_replies(&self, board: &mut Board, max: usize) -> Vec<(Move, Score)> {
        let mut out = Vec::new();
        // The TT's stored move for this node is the refutation we care about.
        let Some(e) = self.tt.probe(board.hash) else { return out };
        if e.mv == Move::NULL { return out }
        let list = generate(board, GenMode::All);
        if !list.as_slice().contains(&e.mv) { return out }
        out.push((e.mv, -(e.score as Score)));
        // Add a couple of sibling captures for visual context.
        for i in 0..list.len {
            if out.len() >= max { break }
            let m = list[i];
            if m != e.mv && m.is_capture() {
                out.push((m, 0));
            }
        }
        out
    }
}

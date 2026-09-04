// Iterative-deepening alpha-beta with a transposition table, quiescence,
// null-move pruning, LMR, killers and history.
use crate::board::{Board, Undo};
use crate::eval::*;
use crate::movegen::*;
use crate::types::*;
use std::sync::atomic::{AtomicU64, Ordering};

pub const MAX_PLY: usize = 128;

#[derive(Copy, Clone, PartialEq)]
#[repr(u8)]
pub enum Bound { Exact = 0, Lower = 1, Upper = 2 }

#[derive(Copy, Clone)]
pub struct TtEntry {
    pub key: u64,
    pub mv: Move,
    pub score: i16,
    pub depth: i8,
    pub bound: Bound,
}

impl TtEntry {
    // Pack move, score, depth and bound into 64 bits so an entry is a pair of
    // atomics rather than a lock.
    #[inline(always)]
    fn pack(mv: Move, score: i16, depth: i8, bound: Bound) -> u64 {
        (mv.0 as u64)
            | ((score as u16 as u64) << 16)
            | ((depth as u8 as u64) << 32)
            | ((bound as u64) << 40)
    }

    #[inline(always)]
    fn unpack(data: u64) -> (Move, i16, i8, Bound) {
        let mv = Move((data & 0xFFFF) as u16);
        let score = ((data >> 16) & 0xFFFF) as u16 as i16;
        let depth = ((data >> 32) & 0xFF) as u8 as i8;
        let bound = match (data >> 40) & 0x3 {
            0 => Bound::Exact,
            1 => Bound::Lower,
            _ => Bound::Upper,
        };
        (mv, score, depth, bound)
    }
}

// Lock-free shared transposition table.
//
// Each slot stores `key ^ data` alongside `data`. A reader recomputes the XOR
// and only trusts the entry if it matches: a torn read from another thread's
// concurrent write fails that check and is discarded, so threads can share the
// table without locking and without ever acting on a corrupt entry.
pub struct Tt {
    keys: Vec<AtomicU64>,
    data: Vec<AtomicU64>,
    mask: usize,
}

impl Tt {
    pub fn new(mb: usize) -> Tt {
        let n = (mb * 1024 * 1024 / 16).next_power_of_two() / 2;
        let n = n.max(1024);
        Tt {
            keys: (0..n).map(|_| AtomicU64::new(0)).collect(),
            data: (0..n).map(|_| AtomicU64::new(0)).collect(),
            mask: n - 1,
        }
    }

    pub fn clear(&self) {
        for k in &self.keys { k.store(0, Ordering::Relaxed); }
        for d in &self.data { d.store(0, Ordering::Relaxed); }
    }

    #[inline(always)]
    pub fn probe(&self, key: u64) -> Option<TtEntry> {
        let i = (key as usize) & self.mask;
        let stored_key = self.keys[i].load(Ordering::Relaxed);
        let data = self.data[i].load(Ordering::Relaxed);
        // Verify: a torn pair will not satisfy this.
        if stored_key ^ data != key || data == 0 {
            return None;
        }
        let (mv, score, depth, bound) = TtEntry::unpack(data);
        Some(TtEntry { key, mv, score, depth, bound })
    }

    #[inline(always)]
    pub fn store(&self, key: u64, mv: Move, score: i16, depth: i8, bound: Bound) {
        let i = (key as usize) & self.mask;
        let stored_key = self.keys[i].load(Ordering::Relaxed);
        let old = self.data[i].load(Ordering::Relaxed);
        // Depth-preferred replacement; always take an empty or foreign slot.
        if stored_key ^ old == key && old != 0 {
            let (_, _, old_depth, _) = TtEntry::unpack(old);
            if depth < old_depth {
                return;
            }
        }
        let data = TtEntry::pack(mv, score, depth, bound);
        self.keys[i].store(key ^ data, Ordering::Relaxed);
        self.data[i].store(data, Ordering::Relaxed);
    }
}

pub struct SearchLimits {
    pub depth: u32,
    // Hard limit: abort mid-search once exceeded.
    // Soft limit: don't begin a new iteration past this. Lets a promising
    // depth finish instead of being cut off with a half-searched root.
    pub nodes: Option<u64>,
}

impl Default for SearchLimits {
    fn default() -> Self {
        SearchLimits {
            depth: MAX_PLY as u32,
            nodes: None,
        }
    }
}

pub struct Searcher {
    pub tt: Tt,
    pub nodes: u64,
    limits: SearchLimits,
    // Quiet moves that caused a beta cutoff, indexed by ply.
    killers: [[Move; 2]; MAX_PLY],
    // history[color][from][to], incremented on cutoffs.
    history: [[[i32; 64]; 64]; 2],
    // Position hashes along the current line, for repetition detection.
    pub repetitions: Vec<u64>,
    stopped: bool,
    // Incremental NNUE accumulator, kept in lockstep with make/unmake.
    pub acc: Option<crate::nnue::AccStack>,
    // Overrides the globally-loaded net; lets a match pit two evals in one
    // process. None means "use whatever eval::network() returns".
    pub forced_net: Option<&'static crate::nnue::Network>,
    // Destination of the capture that led into the current node, or 64.
    last_capture_sq: u8,
    // Helper threads skip some iterations so they diverge from the main
    // thread's path and fill the shared table with different information.
    pub skip_depth: usize,
}

impl Searcher {
    pub fn new(tt_mb: usize) -> Searcher {
        Searcher::with_tt(Tt::new(tt_mb))
    }

    pub fn with_tt(tt: Tt) -> Searcher {
        Searcher {
            tt,
            nodes: 0,
            limits: SearchLimits::default(),
            killers: [[Move::NULL; 2]; MAX_PLY],
            history: [[[0; 64]; 64]; 2],
            repetitions: Vec::with_capacity(1024),
            stopped: false,
            acc: None,
            forced_net: None,
            last_capture_sq: 64,
            skip_depth: 0,
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
        // K+P vs K is solved theory; the network evaluates book draws at
        // several hundred centipawns because opposition is a rule, not a
        // pattern it can learn from position data.
        if crate::eval::kpk_is_draw(board) == Some(true) { return crate::eval::DRAW; }
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

    // WASM has no wall clock (Instant panics) and no threads, so the search
    // budget is a node count.
    #[inline(always)]
    fn should_stop(&mut self) -> bool {
        if self.stopped { return true; }
        if self.nodes & 2047 == 0 {
            if let Some(n) = self.limits.nodes {
                if self.nodes >= n { self.stopped = true; return true; }
            }
        }
        false
    }

    /// Search to a fixed depth. Depth-limited because WASM has no clock.
    pub fn search(&mut self, board: &mut Board, depth: u32) -> (Move, Score) {
        self.nodes = 0;
        self.stopped = false;
        // A node ceiling stops a pathological position hanging the browser tab.
        self.limits = SearchLimits { depth, nodes: Some(3_000_000) };
        self.killers = [[Move::NULL; 2]; MAX_PLY];
        self.history = [[[0; 64]; 64]; 2];
        if let Some(net) = crate::eval::network() {
            match self.acc.as_mut() {
                Some(stack) => stack.reset(net, board),
                None => self.acc = Some(crate::nnue::AccStack::new(net, board)),
            }
        }
        let mut best = Move::NULL;
        let mut best_score = 0;
        for d in 1..=depth {
            let score = self.aspiration(board, d as i32, best_score);
            if self.stopped && d > 1 { break; }
            best_score = score;
            if let Some(&m) = self.extract_pv(board, d as usize).first() { best = m; }
            if score.abs() > MATE_IN_MAX { break; }
        }
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

        // Internal iterative reduction: without a TT move the ordering is
        // poor, so searching full depth mostly wastes nodes. Reduce, and the
        // shallower search fills the TT for the re-search.
        if depth >= 4 && tt_move == Move::NULL && !in_check {
            depth -= 1;
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

        let mut best_score = -MATE;
        let mut best_move = Move::NULL;
        let orig_alpha = alpha;
        let mut searched_quiets: Vec<Move> = Vec::new();
        // Square of the capture that led into this node, for recapture
        // extensions. 64 means "no capture".
        let prev_capture_sq = self.last_capture_sq;

        // Futility: near the horizon, a quiet move that cannot plausibly
        // raise a hopeless static eval to alpha is not worth searching.
        let futile = !is_pv && !in_check && depth <= 6
            && static_eval + 120 * depth + 100 < alpha;

        for i in 0..list.len {
            let m = list[i];
            let is_quiet = !m.is_capture() && !m.is_promotion();

            // Keep at least one move so we never return an empty result.
            if futile && is_quiet && i > 0 && best_score > -MATE_IN_MAX {
                continue;
            }

            // Tree capture: record this root move and how much it cost.

            self.acc_push(board, m);
            let saved_cap = self.last_capture_sq;
            self.last_capture_sq = if m.is_capture() { m.to() } else { 64 };
            let undo = board.make_move(m);
            self.repetitions.push(board.hash);

            // Recapture extension: a forced recapture continues a tactical
            // sequence, so searching it one ply deeper is usually worth it.
            let ext = if m.is_capture() && m.to() == prev_capture_sq { 1 } else { 0 };

            let mut score;
            if i == 0 {
                score = -self.alphabeta(board, depth - 1 + ext, ply + 1,
                                        -beta, -alpha, true);
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
            self.last_capture_sq = saved_cap;


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


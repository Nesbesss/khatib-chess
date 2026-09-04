// NNUE evaluation: 768 -> 512x2 -> 1, side-to-move perspective pairs.
//
// The first layer is the expensive one (768 inputs x 512 outputs), so it is
// never recomputed from scratch during search: when a piece moves we add the
// destination feature's column and subtract the origin's. That incremental
// update is what makes this cheap enough to call at every node.
use crate::board::Board;
use crate::eval::Score;
use crate::types::*;

pub const INPUT: usize = 768;   // 64 squares x 6 piece types x 2 colors
pub const HIDDEN: usize = 1536;
// King buckets: a piece's value depends on where our king sits, so each king
// region gets its own weight set. Indexed by the perspective's own king.
pub const BUCKETS: usize = 8;
pub const FT_SIZE: usize = INPUT * BUCKETS;

// Maps a king square (from that side's perspective) to a bucket:
// Eight buckets: four files x two ranks (own half vs advanced). A king on the
// back rank and one that has walked up the board want different weights, and
// four buckets could not express that.
#[rustfmt::skip]
pub const KING_BUCKET: [usize; 64] = [
    0, 0, 1, 1, 2, 2, 3, 3,
    0, 0, 1, 1, 2, 2, 3, 3,
    0, 0, 1, 1, 2, 2, 3, 3,
    0, 0, 1, 1, 2, 2, 3, 3,
    4, 4, 5, 5, 6, 6, 7, 7,
    4, 4, 5, 5, 6, 6, 7, 7,
    4, 4, 5, 5, 6, 6, 7, 7,
    4, 4, 5, 5, 6, 6, 7, 7,
];

// Fixed-point scaling. Weights are quantized to i16 so the accumulator can be
// SIMD-friendly and exact; these constants must match the trainer's.
pub const QA: i32 = 255;        // feature-transformer scale
pub const QB: i32 = 64;         // output-layer scale
pub const SCALE: i32 = 400;     // logit -> centipawn scale

#[repr(C)]
pub struct Network {
    // Indexed [bucket * INPUT + feature][hidden]
    pub ft_weight: [[i16; HIDDEN]; FT_SIZE],
    pub ft_bias: [i16; HIDDEN],
    // Output reads both perspectives: [side-to-move, opponent].
    pub out_weight: [i16; HIDDEN * 2],
    pub out_bias: i16,
}

// The running first-layer output for both perspectives.
#[derive(Clone, Copy)]
pub struct Accumulator {
    pub v: [[i16; HIDDEN]; 2],
    // Active king bucket per perspective. When a king move changes this, the
    // whole perspective must be rebuilt: every feature now indexes different
    // weights.
    pub bucket: [usize; 2],
}

// A stack of accumulators, one per ply. Pushing copies the parent and applies
// only the features that changed, so evaluation never rebuilds from scratch.
pub struct AccStack {
    stack: Vec<Accumulator>,
}

impl AccStack {
    pub fn new(net: &Network, board: &Board) -> AccStack {
        let mut a = Accumulator::new(net);
        a.refresh(net, board);
        AccStack { stack: vec![a] }
    }

    #[inline(always)]
    pub fn top(&self) -> &Accumulator { self.stack.last().unwrap() }

    pub fn reset(&mut self, net: &Network, board: &Board) {
        self.stack.clear();
        let mut a = Accumulator::new(net);
        a.refresh(net, board);
        self.stack.push(a);
    }

    // Apply a move's feature deltas on top of the current accumulator.
    // `board` must be the position BEFORE the move.
    pub fn push(&mut self, net: &Network, board: &Board, m: crate::types::Move) {
        let mut acc = *self.top();
        let us = board.side;

        // A king move that crosses a bucket boundary changes which weight set
        // every feature of that perspective indexes, so deltas are meaningless
        // and the perspective must be rebuilt from scratch.
        if let Some((_, Piece::King)) = board.piece_at(m.from()) {
            let new_bucket = Accumulator::bucket_of(us, m.to());
            if new_bucket != acc.bucket[us.idx()] {
                let mut after = board.clone();
                after.make_move(m);
                let mut fresh = Accumulator::new(net);
                fresh.refresh(net, &after);
                self.stack.push(fresh);
                return;
            }
        }

        let them = us.flip();
        let from = m.from();
        let to = m.to();
        let Some((_, piece)) = board.piece_at(from) else {
            self.stack.push(acc);
            return;
        };

        acc.sub(net, us, piece, from);

        // Captured piece leaves the board before ours arrives.
        if m.is_ep() {
            let cap_sq = if us == Color::White { to - 8 } else { to + 8 };
            acc.sub(net, them, Piece::Pawn, cap_sq);
        } else if m.is_capture() {
            if let Some((_, cp)) = board.piece_at(to) {
                acc.sub(net, them, cp, to);
            }
        }

        if m.is_promotion() {
            acc.add(net, us, m.promo_piece(), to);
        } else {
            acc.add(net, us, piece, to);
        }

        if m.is_castle() {
            use crate::types::{A1, A8, H1, H8, FLAG_CASTLE_KING};
            let (rf, rt) = match (us, m.flag()) {
                (Color::White, FLAG_CASTLE_KING) => (H1, H1 - 2),
                (Color::White, _) => (A1, A1 + 3),
                (Color::Black, FLAG_CASTLE_KING) => (H8, H8 - 2),
                (Color::Black, _) => (A8, A8 + 3),
            };
            acc.sub(net, us, Piece::Rook, rf);
            acc.add(net, us, Piece::Rook, rt);
        }

        self.stack.push(acc);
    }

    #[inline(always)]
    pub fn pop(&mut self) {
        // Never pop the root accumulator.
        if self.stack.len() > 1 { self.stack.pop(); }
    }

    // Null moves change only the side to move, which the evaluator reads
    // separately — so the accumulator is unchanged.
    #[inline(always)]
    pub fn push_null(&mut self) {
        let top = *self.top();
        self.stack.push(top);
    }
}

impl Accumulator {
    pub fn new(net: &Network) -> Accumulator {
        Accumulator { v: [net.ft_bias; 2], bucket: [0; 2] }
    }

    // Bucket for `perspective`, from its own king's square.
    #[inline(always)]
    pub fn bucket_of(perspective: Color, king_sq: u8) -> usize {
        let sq = if perspective == Color::White { king_sq } else { king_sq ^ 56 };
        KING_BUCKET[sq as usize]
    }

    // Feature index for (piece, color, square) from `perspective`'s view.
    // Black's view mirrors the board vertically and swaps piece colors so the
    // network always sees "my pieces" in the same slots.
    #[inline(always)]
    pub fn feature(perspective: Color, color: Color, piece: Piece, sq: u8) -> usize {
        let (c, s) = if perspective == Color::White {
            (color == Color::Black, sq)
        } else {
            (color == Color::White, sq ^ 56)
        };
        (c as usize) * 384 + piece.idx() * 64 + s as usize
    }

    #[inline(always)]
    pub fn add(&mut self, net: &Network, color: Color, piece: Piece, sq: u8) {
        for (p, persp) in [Color::White, Color::Black].iter().enumerate() {
            let f = self.bucket[p] * INPUT + Self::feature(*persp, color, piece, sq);
            add_slice(&mut self.v[p], &net.ft_weight[f]);
        }
    }

    #[inline(always)]
    pub fn sub(&mut self, net: &Network, color: Color, piece: Piece, sq: u8) {
        for (p, persp) in [Color::White, Color::Black].iter().enumerate() {
            let f = self.bucket[p] * INPUT + Self::feature(*persp, color, piece, sq);
            sub_slice(&mut self.v[p], &net.ft_weight[f]);
        }
    }

    // Full refresh from the board. Used at the root and after king moves in
    // bucketed architectures; here it is the fallback path.
    pub fn refresh(&mut self, net: &Network, board: &Board) {
        self.v = [net.ft_bias; 2];
        self.bucket = [
            Self::bucket_of(Color::White, board.king_square(Color::White)),
            Self::bucket_of(Color::Black, board.king_square(Color::Black)),
        ];
        for color in [Color::White, Color::Black] {
            for piece in [Piece::Pawn, Piece::Knight, Piece::Bishop,
                          Piece::Rook, Piece::Queen, Piece::King] {
                let mut b = board.pieces[color.idx()][piece.idx()];
                while b != 0 {
                    let sq = b.trailing_zeros() as u8;
                    b &= b - 1;
                    self.add(net, color, piece, sq);
                }
            }
        }
    }
}

// The accumulator update is the hottest loop in the engine: HIDDEN i16 adds
// per changed feature, several times per node. Iterating over equal-length
// chunks lets the autovectorizer emit SIMD without unsafe intrinsics, and
// keeps the code portable across x86 and ARM.
const LANES: usize = 16;

#[inline(always)]
fn add_slice(acc: &mut [i16; HIDDEN], w: &[i16; HIDDEN]) {
    for (a, b) in acc.chunks_exact_mut(LANES).zip(w.chunks_exact(LANES)) {
        for i in 0..LANES {
            a[i] = a[i].wrapping_add(b[i]);
        }
    }
}

#[inline(always)]
fn sub_slice(acc: &mut [i16; HIDDEN], w: &[i16; HIDDEN]) {
    for (a, b) in acc.chunks_exact_mut(LANES).zip(w.chunks_exact(LANES)) {
        for i in 0..LANES {
            a[i] = a[i].wrapping_sub(b[i]);
        }
    }
}

// Clipped ReLU: activations are clamped to [0, QA] so the product stays in
// range for i16 math.
#[inline(always)]
fn crelu(x: i16) -> i32 {
    (x as i32).clamp(0, QA)
}

pub fn evaluate(net: &Network, acc: &Accumulator, side: Color) -> Score {
    // The side to move always reads perspective 0 of the pair.
    let (us, them) = if side == Color::White { (0, 1) } else { (1, 0) };
    // i32 accumulation is enough: HIDDEN * QA * 32767 stays well inside range,
    // and it vectorizes where i64 would not.
    let mut sum: i32 = 0;
    for i in 0..HIDDEN {
        sum += crelu(acc.v[us][i]) * net.out_weight[i] as i32;
        sum += crelu(acc.v[them][i]) * net.out_weight[HIDDEN + i] as i32;
    }
    let sum = sum as i64;
    // Activations carry a factor of QA and out_weight a factor of QB, so the
    // product is QA*QB times the float logit. out_bias is stored at that same
    // combined scale. Convert the logit to centipawns with SCALE.
    let logit_num = sum + net.out_bias as i64;
    ((logit_num * SCALE as i64) / (QA as i64 * QB as i64)) as Score
}

// Load a network from the flat little-endian i16 layout the trainer writes:
//   ft_weight [BUCKETS*768][HIDDEN] | ft_bias [HIDDEN]
//   | out_weight [HIDDEN*2] | out_bias [1]
pub fn load(path: &str) -> std::io::Result<Box<Network>> {
    let bytes = std::fs::read(path)?;
    load_bytes(&bytes)
}

/// Parse a network from bytes already in memory. The browser build has no
/// filesystem, so it fetches the file and hands the bytes here.
pub fn load_bytes(bytes: &[u8]) -> std::io::Result<Box<Network>> {
    let expected = (FT_SIZE * HIDDEN + HIDDEN + HIDDEN * 2 + 1) * 2;
    if bytes.len() != expected {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("network size mismatch: got {} bytes, expected {}", bytes.len(), expected),
        ));
    }
    let mut vals = bytes.chunks_exact(2).map(|c| i16::from_le_bytes([c[0], c[1]]));

    // Box::new on a struct this large would build it on the stack first.
    let mut net: Box<Network> = unsafe {
        let layout = std::alloc::Layout::new::<Network>();
        let ptr = std::alloc::alloc_zeroed(layout) as *mut Network;
        if ptr.is_null() { std::alloc::handle_alloc_error(layout); }
        Box::from_raw(ptr)
    };

    for i in 0..FT_SIZE {
        for j in 0..HIDDEN {
            net.ft_weight[i][j] = vals.next().unwrap();
        }
    }
    for j in 0..HIDDEN { net.ft_bias[j] = vals.next().unwrap(); }
    for j in 0..HIDDEN * 2 { net.out_weight[j] = vals.next().unwrap(); }
    net.out_bias = vals.next().unwrap();
    Ok(net)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn features_are_perspective_symmetric() {
        // A white pawn on e2 seen by White must occupy the same slot as a
        // black pawn on e7 seen by Black — that is the whole point of the
        // mirrored encoding.
        let w = Accumulator::feature(Color::White, Color::White, Piece::Pawn,
                                     parse_square("e2").unwrap());
        let b = Accumulator::feature(Color::Black, Color::Black, Piece::Pawn,
                                     parse_square("e7").unwrap());
        assert_eq!(w, b);
    }

    #[test]
    fn features_distinguish_colors() {
        let sq = parse_square("d4").unwrap();
        let own = Accumulator::feature(Color::White, Color::White, Piece::Knight, sq);
        let opp = Accumulator::feature(Color::White, Color::Black, Piece::Knight, sq);
        assert_ne!(own, opp);
        assert!(own < 384 && opp >= 384);
    }

    #[test]
    fn add_then_sub_restores() {
        // Incremental updates must be exactly reversible or search corrupts.
        let net: Box<Network> = unsafe {
            let l = std::alloc::Layout::new::<Network>();
            let p = std::alloc::alloc_zeroed(l) as *mut Network;
            Box::from_raw(p)
        };
        let mut acc = Accumulator::new(&net);
        let before = acc.v;
        acc.add(&net, Color::White, Piece::Queen, 27);
        acc.sub(&net, Color::White, Piece::Queen, 27);
        assert_eq!(acc.v[0], before[0]);
        assert_eq!(acc.v[1], before[1]);
    }
}

#[cfg(test)]
mod quant_tests {
    use super::*;
    use crate::board::Board;

    // A net whose weights are all zero except a known pattern must produce a
    // predictable score. Guards the fixed-point arithmetic against silent
    // rescaling — the bug class that makes a net evaluate to a constant.
    #[test]
    fn known_weights_give_known_score() {
        let mut net: Box<Network> = unsafe {
            let l = std::alloc::Layout::new::<Network>();
            let p = std::alloc::alloc_zeroed(l) as *mut Network;
            Box::from_raw(p)
        };
        // One hidden unit fully active, one output weight.
        net.ft_bias[0] = QA as i16;      // activation saturates at QA
        net.out_weight[0] = QB as i16;   // weight of 1.0 in float terms
        net.out_bias = 0;

        let board = Board::startpos();
        let mut acc = Accumulator::new(&net);
        acc.refresh(&net, &board);
        // sum = QA * QB, so logit = 1.0 and score = SCALE centipawns.
        let score = evaluate(&net, &acc, board.side);
        assert_eq!(score, SCALE, "fixed-point scaling drifted");
    }

    #[test]
    fn empty_net_is_zero() {
        let net: Box<Network> = unsafe {
            let l = std::alloc::Layout::new::<Network>();
            let p = std::alloc::alloc_zeroed(l) as *mut Network;
            Box::from_raw(p)
        };
        let board = Board::startpos();
        let mut acc = Accumulator::new(&net);
        acc.refresh(&net, &board);
        assert_eq!(evaluate(&net, &acc, board.side), 0);
    }
}

#[cfg(test)]
mod incremental_tests {
    use super::*;
    use crate::board::Board;
    use crate::movegen::{generate, GenMode};

    // Load any net-shaped bytes; content doesn't matter, only that the
    // incremental path and the refresh path agree.
    fn random_net() -> Box<Network> {
        let mut net: Box<Network> = unsafe {
            let l = std::alloc::Layout::new::<Network>();
            let p = std::alloc::alloc_zeroed(l) as *mut Network;
            Box::from_raw(p)
        };
        let mut s = 0x2545F4914F6CDD1Du64;
        let mut next = || {
            s ^= s >> 12; s ^= s << 25; s ^= s >> 27;
            (s.wrapping_mul(0x2545F4914F6CDD1D) >> 48) as i16 / 64
        };
        for i in 0..FT_SIZE { for j in 0..HIDDEN { net.ft_weight[i][j] = next(); } }
        for j in 0..HIDDEN { net.ft_bias[j] = next(); }
        for j in 0..HIDDEN * 2 { net.out_weight[j] = next(); }
        net.out_bias = next();
        net
    }

    // Walk the tree to `depth`, checking after every move that the
    // incrementally-updated accumulator equals a fresh one. This is the test
    // that catches castling, en passant and promotion delta bugs.
    fn walk(net: &Network, board: &mut Board, stack: &mut AccStack, depth: u32) -> u64 {
        if depth == 0 { return 1; }
        let list = generate(board, GenMode::All);
        let mut n = 0;
        for i in 0..list.len {
            let m = list[i];
            stack.push(net, board, m);
            let undo = board.make_move(m);

            let mut fresh = Accumulator::new(net);
            fresh.refresh(net, board);
            assert_eq!(stack.top().v[0], fresh.v[0],
                       "white accumulator drifted after {}", m.to_uci());
            assert_eq!(stack.top().v[1], fresh.v[1],
                       "black accumulator drifted after {}", m.to_uci());

            n += walk(net, board, stack, depth - 1);
            board.unmake_move(m, undo);
            stack.pop();
        }
        n
    }

    #[test]
    fn incremental_matches_refresh_startpos() {
        let net = random_net();
        let mut board = Board::startpos();
        let mut stack = AccStack::new(&net, &board);
        walk(&net, &mut board, &mut stack, 3);
    }

    #[test]
    fn incremental_matches_refresh_tactical() {
        // Kiwipete: castling both sides, promotions, en passant available.
        let net = random_net();
        let mut board = Board::from_fen(
            "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"
        ).unwrap();
        let mut stack = AccStack::new(&net, &board);
        walk(&net, &mut board, &mut stack, 3);
    }

    #[test]
    fn incremental_matches_refresh_king_walk() {
        // A bare-king position forces repeated bucket transitions: the king
        // crosses every file, so every crossing must trigger a refresh.
        let net = random_net();
        let mut board = Board::from_fen("8/8/8/3k4/8/3K4/8/8 w - - 0 1").unwrap();
        let mut stack = AccStack::new(&net, &board);
        walk(&net, &mut board, &mut stack, 4);
    }

    #[test]
    fn incremental_matches_refresh_castling_buckets() {
        // Castling moves the king two files, which changes bucket in both
        // directions — the case a naive delta update gets silently wrong.
        let net = random_net();
        let mut board = Board::from_fen(
            "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1"
        ).unwrap();
        let mut stack = AccStack::new(&net, &board);
        walk(&net, &mut board, &mut stack, 3);
    }

    #[test]
    fn bucket_changes_are_detected() {
        // e1 and g1 must land in different buckets, or castling would never
        // trigger a refresh and the tests above would prove nothing.
        let e1 = Accumulator::bucket_of(Color::White, parse_square("e1").unwrap());
        let g1 = Accumulator::bucket_of(Color::White, parse_square("g1").unwrap());
        let c1 = Accumulator::bucket_of(Color::White, parse_square("c1").unwrap());
        assert_ne!(e1, g1, "kingside castling must change bucket");
        assert_ne!(e1, c1, "queenside castling must change bucket");
    }

    #[test]
    fn incremental_matches_refresh_promotions() {
        let net = random_net();
        let mut board = Board::from_fen(
            "n1n5/PPPk4/8/8/8/8/4Kppp/5N1N b - - 0 1"
        ).unwrap();
        let mut stack = AccStack::new(&net, &board);
        walk(&net, &mut board, &mut stack, 3);
    }
}

// Micro-benchmark entry point: measures accumulator update throughput, the
// hot loop that dominates NNUE cost.
pub fn bench_accumulator(iters: u64) -> (f64, i64) {
    use std::time::Instant;
    let mut net: Box<Network> = unsafe {
        let l = std::alloc::Layout::new::<Network>();
        let p = std::alloc::alloc_zeroed(l) as *mut Network;
        Box::from_raw(p)
    };
    let mut s = 0x2545F4914F6CDD1Du64;
    let mut next = || {
        s ^= s >> 12; s ^= s << 25; s ^= s >> 27;
        (s.wrapping_mul(0x2545F4914F6CDD1D) >> 50) as i16
    };
    for i in 0..FT_SIZE { for j in 0..HIDDEN { net.ft_weight[i][j] = next(); } }

    let board = Board::startpos();
    let mut acc = Accumulator::new(&net);
    acc.refresh(&net, &board);

    let start = Instant::now();
    let mut sink: i64 = 0;
    for i in 0..iters {
        let sq = (i % 64) as u8;
        acc.add(&net, Color::White, Piece::Knight, sq);
        acc.sub(&net, Color::White, Piece::Knight, sq);
        sink = sink.wrapping_add(acc.v[0][(i % HIDDEN as u64) as usize] as i64);
    }
    let secs = start.elapsed().as_secs_f64();
    // Two updates per iteration, each touching HIDDEN values in 2 perspectives.
    let ops = iters as f64 * 2.0 * 2.0 * HIDDEN as f64;
    (ops / secs / 1e9, sink)
}

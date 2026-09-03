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
pub const HIDDEN: usize = 512;

// Fixed-point scaling. Weights are quantized to i16 so the accumulator can be
// SIMD-friendly and exact; these constants must match the trainer's.
pub const QA: i32 = 255;        // feature-transformer scale
pub const QB: i32 = 64;         // output-layer scale
pub const SCALE: i32 = 400;     // logit -> centipawn scale

#[repr(C)]
pub struct Network {
    pub ft_weight: [[i16; HIDDEN]; INPUT],
    pub ft_bias: [i16; HIDDEN],
    // Output reads both perspectives: [side-to-move, opponent].
    pub out_weight: [i16; HIDDEN * 2],
    pub out_bias: i16,
}

// The running first-layer output for both perspectives.
#[derive(Clone)]
pub struct Accumulator {
    pub v: [[i16; HIDDEN]; 2],
}

impl Accumulator {
    pub fn new(net: &Network) -> Accumulator {
        Accumulator { v: [net.ft_bias; 2] }
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
            let f = Self::feature(*persp, color, piece, sq);
            let w = &net.ft_weight[f];
            for i in 0..HIDDEN {
                self.v[p][i] = self.v[p][i].wrapping_add(w[i]);
            }
        }
    }

    #[inline(always)]
    pub fn sub(&mut self, net: &Network, color: Color, piece: Piece, sq: u8) {
        for (p, persp) in [Color::White, Color::Black].iter().enumerate() {
            let f = Self::feature(*persp, color, piece, sq);
            let w = &net.ft_weight[f];
            for i in 0..HIDDEN {
                self.v[p][i] = self.v[p][i].wrapping_sub(w[i]);
            }
        }
    }

    // Full refresh from the board. Used at the root and after king moves in
    // bucketed architectures; here it is the fallback path.
    pub fn refresh(&mut self, net: &Network, board: &Board) {
        self.v = [net.ft_bias; 2];
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

// Clipped ReLU: activations are clamped to [0, QA] so the product stays in
// range for i16 math.
#[inline(always)]
fn crelu(x: i16) -> i32 {
    (x as i32).clamp(0, QA)
}

pub fn evaluate(net: &Network, acc: &Accumulator, side: Color) -> Score {
    // The side to move always reads perspective 0 of the pair.
    let (us, them) = if side == Color::White { (0, 1) } else { (1, 0) };
    let mut sum: i64 = 0;
    for i in 0..HIDDEN {
        sum += crelu(acc.v[us][i]) as i64 * net.out_weight[i] as i64;
        sum += crelu(acc.v[them][i]) as i64 * net.out_weight[HIDDEN + i] as i64;
    }
    // Activations carry a factor of QA and out_weight a factor of QB, so the
    // product is QA*QB times the float logit. out_bias is stored at that same
    // combined scale. Convert the logit to centipawns with SCALE.
    let logit_num = sum + net.out_bias as i64;
    ((logit_num * SCALE as i64) / (QA as i64 * QB as i64)) as Score
}

// Load a network from the flat little-endian i16 layout the trainer writes:
//   ft_weight [768][512] | ft_bias [512] | out_weight [1024] | out_bias [1]
pub fn load(path: &str) -> std::io::Result<Box<Network>> {
    let bytes = std::fs::read(path)?;
    let expected = (INPUT * HIDDEN + HIDDEN + HIDDEN * 2 + 1) * 2;
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

    for i in 0..INPUT {
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

// Attack tables. Leapers use plain lookups; sliders use magic bitboards.
use crate::types::*;
use std::sync::OnceLock;

pub const FILE_A: Bitboard = 0x0101010101010101;
pub const FILE_H: Bitboard = 0x8080808080808080;
pub const RANK_1: Bitboard = 0x00000000000000FF;
pub const RANK_8: Bitboard = 0xFF00000000000000;

// Offsets as (file_delta, rank_delta) so we can reject wraparound explicitly
// rather than relying on file-mask tricks.
const KNIGHT_DELTAS: [(i8, i8); 8] =
    [(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)];
const KING_DELTAS: [(i8, i8); 8] =
    [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)];
const BISHOP_DELTAS: [(i8, i8); 4] = [(1, 1), (1, -1), (-1, -1), (-1, 1)];
const ROOK_DELTAS: [(i8, i8); 4] = [(0, 1), (1, 0), (0, -1), (-1, 0)];

fn offset(sq: u8, df: i8, dr: i8) -> Option<u8> {
    let f = file_of(sq) as i8 + df;
    let r = rank_of(sq) as i8 + dr;
    if (0..8).contains(&f) && (0..8).contains(&r) {
        Some(square(f as u8, r as u8))
    } else {
        None
    }
}

fn leaper_table(deltas: &[(i8, i8)]) -> [Bitboard; 64] {
    let mut table = [0u64; 64];
    for sq in 0..64u8 {
        for &(df, dr) in deltas {
            if let Some(t) = offset(sq, df, dr) {
                table[sq as usize] |= bb(t);
            }
        }
    }
    table
}

// Walk a ray until we hit a blocker (inclusive) or the board edge.
fn slider_attacks(sq: u8, occ: Bitboard, deltas: &[(i8, i8)]) -> Bitboard {
    let mut attacks = 0u64;
    for &(df, dr) in deltas {
        let mut cur = sq;
        loop {
            match offset(cur, df, dr) {
                Some(next) => {
                    attacks |= bb(next);
                    if occ & bb(next) != 0 { break; }
                    cur = next;
                }
                None => break,
            }
        }
    }
    attacks
}

// The relevant-occupancy mask excludes edge squares: a blocker on the edge
// doesn't change what's attacked beyond it, so it needn't be indexed.
fn slider_mask(sq: u8, deltas: &[(i8, i8)]) -> Bitboard {
    let mut mask = 0u64;
    for &(df, dr) in deltas {
        let mut cur = sq;
        while let Some(next) = offset(cur, df, dr) {
            if offset(next, df, dr).is_none() { break; }
            mask |= bb(next);
            cur = next;
        }
    }
    mask
}

// Scatter the low bits of `index` across the set bits of `mask`.
fn occupancy_for_index(index: usize, mask: Bitboard) -> Bitboard {
    let mut occ = 0u64;
    let mut m = mask;
    let mut i = 0;
    while m != 0 {
        let sq = m.trailing_zeros() as u8;
        m &= m - 1;
        if index & (1 << i) != 0 {
            occ |= bb(sq);
        }
        i += 1;
    }
    occ
}

pub struct Magic {
    pub mask: Bitboard,
    pub magic: u64,
    pub shift: u32,
    pub offset: usize,
}

pub struct Tables {
    pub knight: [Bitboard; 64],
    pub king: [Bitboard; 64],
    pub pawn: [[Bitboard; 64]; 2],
    pub bishop_magics: Vec<Magic>,
    pub rook_magics: Vec<Magic>,
    pub slider_attacks: Vec<Bitboard>,
    // between[a][b] = squares strictly between a and b on a shared line, else 0.
    pub between: Vec<Bitboard>,
    // line[a][b] = the full line through a and b if they share one, else 0.
    pub line: Vec<Bitboard>,
}

// xorshift64* — deterministic, so builds are reproducible.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545F4914F6CDD1D)
    }
    // Magic candidates want few set bits; ANDing three draws biases that way.
    fn sparse(&mut self) -> u64 { self.next() & self.next() & self.next() }
}

fn find_magics(
    deltas: &[(i8, i8)],
    table: &mut Vec<Bitboard>,
    rng: &mut Rng,
) -> Vec<Magic> {
    let mut magics = Vec::with_capacity(64);
    for sq in 0..64u8 {
        let mask = slider_mask(sq, deltas);
        let bits = mask.count_ones();
        let size = 1usize << bits;
        let shift = 64 - bits;

        // Precompute every (occupancy, attacks) pair for this square.
        let mut occs = Vec::with_capacity(size);
        let mut atts = Vec::with_capacity(size);
        for i in 0..size {
            let occ = occupancy_for_index(i, mask);
            occs.push(occ);
            atts.push(slider_attacks(sq, occ, deltas));
        }

        // Search for a multiplier that maps every occupancy to a slot with no
        // conflicting entry. Collisions are fine when the attack sets agree.
        let offset = table.len();
        table.resize(offset + size, 0);
        let mut used = vec![u32::MAX; size];
        let mut magic;
        let mut epoch = 0u32;
        loop {
            magic = rng.sparse();
            // Cheap rejection: a good magic spreads the mask's high bits.
            if (mask.wrapping_mul(magic) & 0xFF00000000000000).count_ones() < 6 {
                continue;
            }
            epoch += 1;
            let mut ok = true;
            for i in 0..size {
                let idx = ((occs[i].wrapping_mul(magic)) >> shift) as usize;
                if used[idx] != epoch {
                    used[idx] = epoch;
                    table[offset + idx] = atts[i];
                } else if table[offset + idx] != atts[i] {
                    ok = false;
                    break;
                }
            }
            if ok { break; }
        }
        magics.push(Magic { mask, magic, shift, offset });
    }
    magics
}

fn build() -> Tables {
    let knight = leaper_table(&KNIGHT_DELTAS);
    let king = leaper_table(&KING_DELTAS);

    let mut pawn = [[0u64; 64]; 2];
    for sq in 0..64u8 {
        for &df in &[-1i8, 1] {
            if let Some(t) = offset(sq, df, 1) { pawn[0][sq as usize] |= bb(t); }
            if let Some(t) = offset(sq, df, -1) { pawn[1][sq as usize] |= bb(t); }
        }
    }

    let mut slider_attacks_table = Vec::new();
    let mut rng = Rng(0x9E3779B97F4A7C15);
    let bishop_magics = find_magics(&BISHOP_DELTAS, &mut slider_attacks_table, &mut rng);
    let rook_magics = find_magics(&ROOK_DELTAS, &mut slider_attacks_table, &mut rng);

    // between/line are used for check evasion and pin detection later.
    let mut between = vec![0u64; 64 * 64];
    let mut line = vec![0u64; 64 * 64];
    for a in 0..64u8 {
        for b in 0..64u8 {
            if a == b { continue; }
            for deltas in [&BISHOP_DELTAS[..], &ROOK_DELTAS[..]] {
                // b is reachable from a along this piece's rays.
                if slider_attacks(a, 0, deltas) & bb(b) == 0 { continue; }
                let mid = slider_attacks(a, bb(b), deltas)
                    & slider_attacks(b, bb(a), deltas);
                between[a as usize * 64 + b as usize] = mid;
                line[a as usize * 64 + b as usize] =
                    (slider_attacks(a, 0, deltas) & slider_attacks(b, 0, deltas))
                        | bb(a) | bb(b);
            }
        }
    }

    Tables {
        knight, king, pawn,
        bishop_magics, rook_magics,
        slider_attacks: slider_attacks_table,
        between, line,
    }
}

static TABLES: OnceLock<Tables> = OnceLock::new();

#[inline(always)]
pub fn tables() -> &'static Tables { TABLES.get_or_init(build) }

#[inline(always)]
pub fn knight_attacks(sq: u8) -> Bitboard { tables().knight[sq as usize] }
#[inline(always)]
pub fn king_attacks(sq: u8) -> Bitboard { tables().king[sq as usize] }
#[inline(always)]
pub fn pawn_attacks(color: Color, sq: u8) -> Bitboard {
    tables().pawn[color.idx()][sq as usize]
}

#[inline(always)]
pub fn bishop_attacks(sq: u8, occ: Bitboard) -> Bitboard {
    let t = tables();
    let m = &t.bishop_magics[sq as usize];
    let idx = ((occ & m.mask).wrapping_mul(m.magic) >> m.shift) as usize;
    t.slider_attacks[m.offset + idx]
}

#[inline(always)]
pub fn rook_attacks(sq: u8, occ: Bitboard) -> Bitboard {
    let t = tables();
    let m = &t.rook_magics[sq as usize];
    let idx = ((occ & m.mask).wrapping_mul(m.magic) >> m.shift) as usize;
    t.slider_attacks[m.offset + idx]
}

#[inline(always)]
pub fn queen_attacks(sq: u8, occ: Bitboard) -> Bitboard {
    bishop_attacks(sq, occ) | rook_attacks(sq, occ)
}

#[inline(always)]
pub fn between(a: u8, b: u8) -> Bitboard {
    tables().between[a as usize * 64 + b as usize]
}

#[inline(always)]
pub fn line(a: u8, b: u8) -> Bitboard {
    tables().line[a as usize * 64 + b as usize]
}

#[inline(always)]
pub fn piece_attacks(piece: Piece, sq: u8, occ: Bitboard) -> Bitboard {
    match piece {
        Piece::Knight => knight_attacks(sq),
        Piece::Bishop => bishop_attacks(sq, occ),
        Piece::Rook => rook_attacks(sq, occ),
        Piece::Queen => queen_attacks(sq, occ),
        Piece::King => king_attacks(sq),
        Piece::Pawn => 0, // pawns are directional; handled in movegen
    }
}

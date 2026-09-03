// Core types: squares, pieces, colors, moves.

pub type Bitboard = u64;

#[derive(Copy, Clone, PartialEq, Eq, Debug)]
pub enum Color { White = 0, Black = 1 }

impl Color {
    #[inline(always)]
    pub fn flip(self) -> Color {
        if self == Color::White { Color::Black } else { Color::White }
    }
    #[inline(always)]
    pub fn idx(self) -> usize { self as usize }
}

// Piece kinds, indexed 0..6 for array lookups.
#[derive(Copy, Clone, PartialEq, Eq, Debug)]
pub enum Piece { Pawn = 0, Knight = 1, Bishop = 2, Rook = 3, Queen = 4, King = 5 }

impl Piece {
    #[inline(always)]
    pub fn idx(self) -> usize { self as usize }

    pub fn from_char(c: char) -> Option<(Color, Piece)> {
        let color = if c.is_ascii_uppercase() { Color::White } else { Color::Black };
        let piece = match c.to_ascii_lowercase() {
            'p' => Piece::Pawn,
            'n' => Piece::Knight,
            'b' => Piece::Bishop,
            'r' => Piece::Rook,
            'q' => Piece::Queen,
            'k' => Piece::King,
            _ => return None,
        };
        Some((color, piece))
    }

    pub fn to_char(self, color: Color) -> char {
        let c = match self {
            Piece::Pawn => 'p', Piece::Knight => 'n', Piece::Bishop => 'b',
            Piece::Rook => 'r', Piece::Queen => 'q', Piece::King => 'k',
        };
        if color == Color::White { c.to_ascii_uppercase() } else { c }
    }
}

// Squares are 0..64, A1=0, H1=7, A8=56, H8=63 (little-endian rank-file).
pub const A1: u8 = 0;
pub const H1: u8 = 7;
pub const A8: u8 = 56;
pub const H8: u8 = 63;

#[inline(always)]
pub fn rank_of(sq: u8) -> u8 { sq >> 3 }
#[inline(always)]
pub fn file_of(sq: u8) -> u8 { sq & 7 }
#[inline(always)]
pub fn square(file: u8, rank: u8) -> u8 { (rank << 3) | file }
#[inline(always)]
pub fn bb(sq: u8) -> Bitboard { 1u64 << sq }

pub fn square_name(sq: u8) -> String {
    format!("{}{}", (b'a' + file_of(sq)) as char, (b'1' + rank_of(sq)) as char)
}

pub fn parse_square(s: &str) -> Option<u8> {
    let b = s.as_bytes();
    if b.len() != 2 { return None; }
    let file = b[0].checked_sub(b'a')?;
    let rank = b[1].checked_sub(b'1')?;
    if file > 7 || rank > 7 { return None; }
    Some(square(file, rank))
}

// Move encoding, packed into 16 bits:
//   bits 0-5   from square
//   bits 6-11  to square
//   bits 12-15 flags
#[derive(Copy, Clone, PartialEq, Eq)]
pub struct Move(pub u16);

// Move flags. The promotion values are ordered so bits 0-1 give the piece.
pub const FLAG_QUIET: u16 = 0;
pub const FLAG_DOUBLE_PAWN: u16 = 1;
pub const FLAG_CASTLE_KING: u16 = 2;
pub const FLAG_CASTLE_QUEEN: u16 = 3;
pub const FLAG_CAPTURE: u16 = 4;
pub const FLAG_EP_CAPTURE: u16 = 5;
pub const FLAG_PROMO_N: u16 = 8;
pub const FLAG_PROMO_B: u16 = 9;
pub const FLAG_PROMO_R: u16 = 10;
pub const FLAG_PROMO_Q: u16 = 11;
pub const FLAG_PROMO_CAPTURE_N: u16 = 12;
pub const FLAG_PROMO_CAPTURE_B: u16 = 13;
pub const FLAG_PROMO_CAPTURE_R: u16 = 14;
pub const FLAG_PROMO_CAPTURE_Q: u16 = 15;

impl Move {
    #[inline(always)]
    pub fn new(from: u8, to: u8, flag: u16) -> Move {
        Move((from as u16) | ((to as u16) << 6) | (flag << 12))
    }
    #[inline(always)]
    pub fn from(self) -> u8 { (self.0 & 0x3F) as u8 }
    #[inline(always)]
    pub fn to(self) -> u8 { ((self.0 >> 6) & 0x3F) as u8 }
    #[inline(always)]
    pub fn flag(self) -> u16 { self.0 >> 12 }
    #[inline(always)]
    pub fn is_capture(self) -> bool { self.flag() & 4 != 0 }
    #[inline(always)]
    pub fn is_promotion(self) -> bool { self.flag() & 8 != 0 }
    #[inline(always)]
    pub fn is_ep(self) -> bool { self.flag() == FLAG_EP_CAPTURE }
    #[inline(always)]
    pub fn is_castle(self) -> bool {
        self.flag() == FLAG_CASTLE_KING || self.flag() == FLAG_CASTLE_QUEEN
    }
    // Only meaningful when is_promotion() is true.
    #[inline(always)]
    pub fn promo_piece(self) -> Piece {
        match self.flag() & 3 {
            0 => Piece::Knight, 1 => Piece::Bishop, 2 => Piece::Rook, _ => Piece::Queen,
        }
    }
    pub const NULL: Move = Move(0);

    // Long algebraic notation, as UCI speaks it: e2e4, e7e8q.
    pub fn to_uci(self) -> String {
        if self == Move::NULL { return "0000".to_string(); }
        let mut s = format!("{}{}", square_name(self.from()), square_name(self.to()));
        if self.is_promotion() {
            s.push(self.promo_piece().to_char(Color::Black));
        }
        s
    }
}

impl std::fmt::Debug for Move {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        write!(f, "{}", self.to_uci())
    }
}

// Castling rights as a 4-bit mask.
pub const CASTLE_WK: u8 = 1;
pub const CASTLE_WQ: u8 = 2;
pub const CASTLE_BK: u8 = 4;
pub const CASTLE_BQ: u8 = 8;

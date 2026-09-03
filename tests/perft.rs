// Perft regression suite. Reference counts from the CPW standard positions.
// Deep cases are #[ignore]d; run them with `cargo test --release -- --ignored`.
use std::process::Command;

fn perft(fen: &str, depth: u32) -> u64 {
    let out = Command::new(env!("CARGO_BIN_EXE_chess"))
        .args(["perft", &depth.to_string(), fen])
        .output()
        .expect("run engine");
    let s = String::from_utf8_lossy(&out.stdout);
    s.split_whitespace().nth(3).expect("nodes field").parse().expect("node count")
}

const START: &str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const KIWI: &str = "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1";
const POS3: &str = "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1";
const POS4: &str = "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1";
const POS5: &str = "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8";
const POS6: &str = "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10";

#[test]
fn perft_shallow() {
    assert_eq!(perft(START, 4), 197281);
    assert_eq!(perft(KIWI, 3), 97862);
    assert_eq!(perft(POS3, 5), 674624);
    assert_eq!(perft(POS4, 4), 422333);
    assert_eq!(perft(POS5, 4), 2103487);
    assert_eq!(perft(POS6, 4), 3894594);
}

#[test]
#[ignore]
fn perft_deep() {
    assert_eq!(perft(START, 6), 119060324);
    assert_eq!(perft(KIWI, 5), 193690690);
    assert_eq!(perft(POS3, 7), 178633661);
    assert_eq!(perft(POS4, 5), 15833292);
    assert_eq!(perft(POS5, 5), 89941194);
    assert_eq!(perft(POS6, 5), 164075551);
}

// A pawn able to capture on the king's square used to generate a
// promotion-capture of the king, which removed it from the board and made
// every later king lookup return square 64. No standard perft position
// exposes this, so it survived 33/33 passing.
#[test]
fn king_is_never_capturable() {
    // Black pawn on g2, white king on h1. Before the fix this generated 15
    // moves, four of them g2xh1 promotion-captures OF THE KING, which removed
    // it from the board and made every later king lookup index square 64.
    assert_eq!(perft("8/8/8/8/8/5k2/6p1/7K b - - 0 1", 1), 11);
    assert_eq!(perft("8/8/8/8/8/5k2/6p1/7K b - - 0 1", 3), 189);
    // A knight attacking the enemy king's square must not "capture" it:
    // without the fix this position generated 8 moves including Nxh3.
    assert_eq!(perft("8/8/8/8/8/7k/5N2/6K1 w - - 0 1", 1), 7);
}

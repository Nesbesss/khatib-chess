//! Streaming CPU cache builder for replay_v10.py; no Python chess dependency.
//! Usage: pack_replay OUTPUT ORIGINAL.txt FRESH_SHARD.txt ...
//! Flat rows are the 140-byte little-endian ROW in architecture_ab.py.
use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::Path;

fn hash(fields: &[&str]) -> u64 {
    let mut h = 14695981039346656037u64;
    for field in fields {
        for byte in field.bytes().chain(std::iter::once(b' ')) {
            h = (h ^ byte as u64).wrapping_mul(1099511628211);
        }
    }
    // Avalanche before the modulo split; independent of clocks and source.
    h ^= h >> 33;
    h = h.wrapping_mul(0xff51afd7ed558ccd);
    h ^= h >> 33;
    h = h.wrapping_mul(0xc4ceb9fe1a85ec53);
    h ^ (h >> 33)
}

fn encode(fen: &str, cp: f32, wdl: f32) -> ([u8; 140], bool) {
    let fields: Vec<_> = fen.split_whitespace().collect();
    assert_eq!(fields.len(), 6, "full FEN required: {fen}");
    assert!(fields[1] == "w" || fields[1] == "b");
    let ranks: Vec<_> = fields[0].split('/').collect();
    assert_eq!(ranks.len(), 8);
    let mut pieces = Vec::with_capacity(32);
    let mut kings = [Vec::new(), Vec::new()];
    for (rank, text) in ranks.iter().enumerate() {
        let mut file = 0;
        for byte in text.bytes() {
            if (b'1'..=b'8').contains(&byte) {
                file += (byte - b'0') as usize;
            } else {
                let p = b"pnbrqk".iter().position(|x| *x == byte.to_ascii_lowercase()).expect("piece");
                let color = usize::from(byte.is_ascii_lowercase());
                assert!(file < 8);
                let sq = (7 - rank) * 8 + file;
                pieces.push((color, p, sq));
                if p == 5 { kings[color].push(sq); }
                file += 1;
            }
        }
        assert_eq!(file, 8, "bad rank: {fen}");
    }
    assert!(pieces.len() <= 32 && kings[0].len() == 1 && kings[1].len() == 1);
    fields[4].parse::<u32>().expect("halfmove");
    assert!(fields[5].parse::<u32>().expect("fullmove") > 0);
    let mut row = [0u8; 140];
    for side in 0..2 {
        let flip = if side == 0 { 0 } else { 56 };
        let k = kings[side][0] ^ flip;
        let bucket = (k % 8) / 2 + if k >= 32 { 4 } else { 0 };
        for (i, &(c, p, sq)) in pieces.iter().enumerate() {
            let f = (bucket * 768 + usize::from(c != side) * 384 + p * 64 + (sq ^ flip)) as u16;
            row[side * 64 + i * 2..side * 64 + i * 2 + 2].copy_from_slice(&f.to_le_bytes());
        }
    }
    let val = hash(&fields[..4]) % 50 == 0;
    row[128] = pieces.len() as u8;
    row[129] = u8::from(fields[1] == "b");
    row[130] = ((pieces.len() - 2) / 4).min(7) as u8;
    row[131] = u8::from(val);
    row[132..136].copy_from_slice(&cp.to_le_bytes());
    row[136..140].copy_from_slice(&wdl.to_le_bytes());
    (row, val)
}

fn main() -> std::io::Result<()> {
    let args: Vec<_> = env::args().collect();
    assert!(args.len() >= 4, "OUTPUT ORIGINAL.txt FRESH.txt ... required");
    let out = Path::new(&args[1]);
    fs::create_dir(out)?; // Refuse an old/partial cache.
    let writer = |name: &str| -> std::io::Result<BufWriter<File>> {
        Ok(BufWriter::with_capacity(4 << 20, File::create(out.join(name))?))
    };
    let mut rows = writer("positions.bin")?;
    let mut ids = [writer("original_train.bin")?, writer("original_val.bin")?,
                   writer("fresh_train.bin")?, writer("fresh_val.bin")?];
    let mut stats = writer("sources.tsv")?;
    writeln!(stats, "source\tpath\tread\tkept\ttail_removed\ttrain\tval\twdl0\twdl05\twdl1")?;
    let mut samples: [Vec<String>; 2] = [Vec::new(), Vec::new()];
    let mut seen = [0u64; 2];
    let mut rng = [20260907u64, 20260908u64];
    let mut total = 0u32;
    let start = std::time::Instant::now();
    for (i, name) in args[2..].iter().enumerate() {
        let source = usize::from(i != 0);
        let before = fs::metadata(name)?;
        let mut counts = [0u64; 8];
        let mut input = BufReader::with_capacity(4 << 20, File::open(name)?);
        let mut line = String::new();
        while input.read_line(&mut line)? != 0 {
            let text = line.trim();
            if !text.is_empty() {
                let parts: Vec<_> = text.split('|').map(str::trim).collect();
                assert_eq!(parts.len(), 3, "real WDL required: {name}: {text}");
                let cp = parts[1].parse::<i32>().expect("integer cp");
                let wdl = parts[2].parse::<f32>().expect("real WDL");
                assert!(wdl == 0.0 || wdl == 0.5 || wdl == 1.0, "invalid WDL: {text}");
                counts[0] += 1;
                if cp.unsigned_abs() >= 1000 {
                    counts[2] += 1;
                } else {
                    let (row, val) = encode(parts[0], cp as f32, wdl);
                    rows.write_all(&row)?;
                    ids[source * 2 + usize::from(val)].write_all(&total.to_le_bytes())?;
                    counts[1] += 1;
                    counts[3 + usize::from(val)] += 1;
                    counts[5 + (wdl * 2.0) as usize] += 1;
                    seen[source] += 1;
                    // Source-stratified reservoir, sampled over the full input.
                    rng[source] ^= rng[source] << 13;
                    rng[source] ^= rng[source] >> 7;
                    rng[source] ^= rng[source] << 17;
                    let j = if seen[source] <= 1024 { seen[source] - 1 }
                            else { rng[source] % seen[source] } as usize;
                    if j < 1024 {
                        let sample = format!("{total}\t{source}\t{}\t{cp}\t{wdl}", parts[0]);
                        if samples[source].len() < 1024 { samples[source].push(sample); }
                        else { samples[source][j] = sample; }
                    }
                    total = total.checked_add(1).expect("uint32 rows");
                    if total % 1_000_000 == 0 {
                        eprintln!("Packed {total} rows in {:.1}s", start.elapsed().as_secs_f64());
                    }
                }
            }
            line.clear();
        }
        let after = fs::metadata(name)?;
        assert_eq!((before.len(), before.modified()?), (after.len(), after.modified()?), "source changed");
        writeln!(stats, "{source}\t{name}\t{}", counts.iter().map(u64::to_string).collect::<Vec<_>>().join("\t"))?;
    }
    rows.flush()?;
    for id in &mut ids { id.flush()?; }
    stats.flush()?;
    let mut sample_out = writer("sample.tsv")?;
    for sample in samples.iter().flatten() { writeln!(sample_out, "{sample}")?; }
    sample_out.flush()?;
    fs::write(out.join("complete.json"), format!("{{\"rows\":{total},\"row_bytes\":140,\"seconds\":{:.3}}}\n", start.elapsed().as_secs_f64()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn clocks_do_not_leak_positions() {
        let a = encode("4k3/8/8/8/8/8/8/4K3 w - - 0 1", -125.0, 0.5);
        let b = encode("4k3/8/8/8/8/8/8/4K3 w - - 9 40", -125.0, 0.5);
        assert_eq!(a, b);
        assert_eq!(u16::from_le_bytes([a.0[0], a.0[1]]), 2300);
        assert_eq!(f32::from_le_bytes(a.0[132..136].try_into().unwrap()), -125.0);
    }
    #[test]
    #[should_panic]
    fn missing_king_fails_loudly() { encode("8/8/8/8/8/8/8/4K3 w - - 0 1", 0.0, 0.5); }
}

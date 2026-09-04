// Minimal HTTP server for the visualizer. Streams search progress to the
// browser over Server-Sent Events; no dependencies, no WebSocket handshake.
use crate::board::Board;
use crate::eval::{Score, MATE, MATE_IN_MAX};
use crate::movegen::{generate, GenMode};
use crate::search::{SearchLimits, Searcher};
use crate::types::*;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::mpsc;
use std::time::Duration;

const INDEX_HTML: &str = include_str!("../web/index.html");

pub fn run(port: u16) { run_on(port, false) }

/// `public` binds every interface so other devices on the same network can
/// connect. Off by default: this server has no authentication.
pub fn run_on(port: u16, public: bool) {
    let host = if public { "0.0.0.0" } else { "127.0.0.1" };
    let listener = match TcpListener::bind((host, port)) {
        Ok(l) => l,
        Err(e) => { eprintln!("cannot bind {}:{}: {}", host, port, e); return; }
    };
    if public {
        println!("visualizer: http://<this-machine-ip>:{} (open to the network)", port);
    } else {
        println!("visualizer: http://127.0.0.1:{}", port);
    }
    for stream in listener.incoming() {
        match stream {
            // One thread per connection: SSE streams are long-lived, so they
            // must not block the next request.
            Ok(s) => { std::thread::spawn(move || handle(s)); }
            Err(e) => eprintln!("accept failed: {}", e),
        }
    }
}

fn handle(mut stream: TcpStream) {
    let mut reader = BufReader::new(match stream.try_clone() {
        Ok(s) => s,
        Err(_) => return,
    });
    let mut request_line = String::new();
    if reader.read_line(&mut request_line).is_err() { return; }

    let mut parts = request_line.split_whitespace();
    let (_method, path) = (parts.next().unwrap_or(""), parts.next().unwrap_or("/"));

    // Drain headers so the socket is positioned at the body.
    let mut content_length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).unwrap_or(0) == 0 { break; }
        if line.trim().is_empty() { break; }
        if let Some(v) = line.to_ascii_lowercase().strip_prefix("content-length:") {
            content_length = v.trim().parse().unwrap_or(0);
        }
    }

    let (route, query) = match path.split_once('?') {
        Some((r, q)) => (r, q),
        None => (path, ""),
    };

    match route {
        "/" => serve_html(&mut stream),
        "/analyze" => {
            let _ = content_length;
            stream_analysis(&mut stream, query);
        }
        "/moves" => serve_moves(&mut stream, query),
        _ => {
            let _ = stream.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
        }
    }
}

fn serve_html(stream: &mut TcpStream) {
    let resp = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{}",
        INDEX_HTML.len(), INDEX_HTML);
    let _ = stream.write_all(resp.as_bytes());
}

fn param<'a>(query: &'a str, key: &str) -> Option<String> {
    query.split('&')
        .find_map(|kv| kv.split_once('='))
        .filter(|(k, _)| *k == key)
        .map(|(_, v)| url_decode(v))
        .or_else(|| {
            query.split('&')
                .filter_map(|kv| kv.split_once('='))
                .find(|(k, _)| *k == key)
                .map(|(_, v)| url_decode(v))
        })
}

fn url_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'%' if i + 2 < b.len() => {
                match u8::from_str_radix(&s[i + 1..i + 3], 16) {
                    Ok(c) => { out.push(c as char); i += 3; }
                    Err(_) => { out.push('%'); i += 1; }
                }
            }
            b'+' => { out.push(' '); i += 1; }
            c => { out.push(c as char); i += 1; }
        }
    }
    out
}

// Legal moves for the position, so the UI can validate drags and show dots.
fn serve_moves(stream: &mut TcpStream, query: &str) {
    let fen = param(query, "fen").unwrap_or_else(|| crate::board::START_FEN.to_string());
    let body = match Board::from_fen(&fen) {
        Ok(mut board) => {
            // Optional ?play=<uci>: apply the move and return the new position.
            if let Some(mv_str) = param(query, "play") {
                let list = generate(&board, GenMode::All);
                match (0..list.len).map(|i| list[i]).find(|m| m.to_uci() == mv_str) {
                    Some(m) => { board.make_move(m); }
                    None => {
                        let e = format!("{{\"error\":\"illegal move {}\"}}", mv_str);
                        let r = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n                                         Content-Length: {}\r\nConnection: close\r\n\r\n{}",
                                        e.len(), e);
                        let _ = stream.write_all(r.as_bytes());
                        return;
                    }
                }
            }
            let list = generate(&board, GenMode::All);
            let moves: Vec<String> = (0..list.len)
                .map(|i| format!("\"{}\"", list[i].to_uci()))
                .collect();
            let status = game_status(&board, list.len);
            // Both evaluations, so the UI can show what the net changed.
            let hce = crate::eval::evaluate_hce(&board);
            let nnue = crate::eval::network().map(|net| {
                let mut acc = crate::nnue::Accumulator::new(net);
                acc.refresh(net, &board);
                crate::nnue::evaluate(net, &acc, board.side)
            });
            format!("{{\"moves\":[{}],\"status\":\"{}\",\"turn\":\"{}\",\
                     \"check\":{},\"fen\":\"{}\",\"hce\":{},\"nnue\":{}}}",
                    moves.join(","), status,
                    if board.side == Color::White { "w" } else { "b" },
                    board.in_check(board.side), board.to_fen(), hce,
                    match nnue { Some(v) => v.to_string(), None => "null".into() })
        }
        Err(e) => format!("{{\"error\":\"{}\"}}", e),
    };
    let resp = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(), body);
    let _ = stream.write_all(resp.as_bytes());
}

fn game_status(board: &Board, legal_count: usize) -> &'static str {
    if legal_count == 0 {
        if board.in_check(board.side) { "checkmate" } else { "stalemate" }
    } else if board.halfmove >= 100 {
        "fifty-move"
    } else {
        "playing"
    }
}

// Run a search, streaming each completed depth to the browser as it lands.
fn stream_analysis(stream: &mut TcpStream, query: &str) {
    let fen = param(query, "fen").unwrap_or_else(|| crate::board::START_FEN.to_string());
    // Either a depth cap (weaker, for playing against a human) or a time
    // budget (full strength).
    let depth: Option<u32> = param(query, "depth").and_then(|s| s.parse().ok());
    let movetime: u64 = param(query, "ms").and_then(|s| s.parse().ok()).unwrap_or(2000);

    let headers = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\
                   Cache-Control: no-cache\r\nConnection: close\r\n\r\n";
    if stream.write_all(headers.as_bytes()).is_err() { return; }

    let mut board = match Board::from_fen(&fen) {
        Ok(b) => b,
        Err(e) => {
            let _ = write!(stream, "event: error\ndata: {{\"msg\":\"{}\"}}\n\n", e);
            return;
        }
    };

    // The searcher reports depths through a channel; this thread forwards
    // them to the socket so a dead client doesn't stall the search.
    let (tx, rx) = mpsc::channel::<String>();
    let handle = std::thread::spawn(move || {
        let mut searcher = Searcher::new(64);
        let limits = match depth {
            Some(d) => SearchLimits { depth: d, ..Default::default() },
            None => SearchLimits {
                movetime: Some(Duration::from_millis(movetime)),
                ..Default::default()
            },
        };
        let result = searcher.search_with_callback(&mut board, limits, |info| {
            let _ = tx.send(info);
        });
        result
    });

    for msg in rx {
        if write!(stream, "data: {}\n\n", msg).is_err() { break; }
        if stream.flush().is_err() { break; }
    }

    if let Ok((best, score)) = handle.join() {
        let _ = write!(stream, "event: done\ndata: {{\"best\":\"{}\",\"score\":{}}}\n\n",
                       best.to_uci(), score);
        let _ = stream.flush();
    }
}

pub fn score_json(score: Score) -> String {
    if score.abs() > MATE_IN_MAX {
        let plies = MATE - score.abs();
        let mate_in = (plies + 1) / 2 * score.signum();
        format!("{{\"type\":\"mate\",\"value\":{}}}", mate_in)
    } else {
        format!("{{\"type\":\"cp\",\"value\":{}}}", score)
    }
}

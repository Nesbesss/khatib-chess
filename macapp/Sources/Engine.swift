import Foundation

/// Drives the chess engine as a UCI subprocess.
///
/// The engine binary and its network are copied into the app bundle, so the
/// app is self-contained and needs nothing installed.
final class Engine {
    private var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutPipe: Pipe?
    private var buffer = ""
    private let queue = DispatchQueue(label: "engine.io")

    /// Called with the engine's chosen move in UCI form (e.g. "e2e4").
    var onBestMove: ((String) -> Void)?
    /// Called with (depth, score in centipawns, mate-in or nil, pv).
    var onInfo: ((Int, Int, Int?, [String]) -> Void)?

    /// Launch our bundled engine, or an external one at `path`.
    func start(path: String? = nil, options: [String: String] = [:]) -> Bool {
        let exe: URL
        if let path {
            exe = URL(fileURLWithPath: path)
            guard FileManager.default.isExecutableFile(atPath: path) else { return false }
        } else {
            guard let bundled = Bundle.main.url(forAuxiliaryExecutable: "chess")
                    ?? Bundle.main.resourceURL?.appendingPathComponent("chess") else {
                return false
            }
            exe = bundled
        }
        let p = Process()
        p.executableURL = exe
        // Run from Resources so the engine finds net.nnue beside itself.
        p.currentDirectoryURL = Bundle.main.resourceURL
        let inPipe = Pipe(), outPipe = Pipe()
        p.standardInput = inPipe
        p.standardOutput = outPipe
        p.standardError = FileHandle.nullDevice

        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            self?.queue.async { self?.consume(text) }
        }

        do { try p.run() } catch { return false }
        process = p
        stdinPipe = inPipe
        stdoutPipe = outPipe
        send("uci")
        for (k, v) in options { send("setoption name \(k) value \(v)") }
        send("isready")
        return true
    }

    private func consume(_ text: String) {
        buffer += text
        while let idx = buffer.firstIndex(of: "\n") {
            let line = String(buffer[buffer.startIndex..<idx])
            buffer = String(buffer[buffer.index(after: idx)...])
            handle(line.trimmingCharacters(in: .whitespaces))
        }
    }

    private var pendingLegal: [String] = []

    private func handle(_ line: String) {
        if line.hasPrefix("legal") {
            pendingLegal = line.split(separator: " ").dropFirst().map(String.init)
        } else if ["white-wins", "black-wins", "draw-stalemate", "draw-fifty",
                   "draw-material", "playing"].contains(line) {
            let moves = pendingLegal
            DispatchQueue.main.async { self.onPosition?(moves, line) }
        } else if line.hasPrefix("bestmove") {
            let parts = line.split(separator: " ")
            if parts.count > 1 {
                let mv = String(parts[1])
                DispatchQueue.main.async { self.onBestMove?(mv) }
            }
        } else if line.hasPrefix("info "), line.contains(" pv ") {
            var depth = 0, cp = 0
            var mate: Int? = nil
            var pv: [String] = []
            let t = line.split(separator: " ").map(String.init)
            var i = 0
            while i < t.count {
                switch t[i] {
                case "depth": if i + 1 < t.count { depth = Int(t[i+1]) ?? 0 }; i += 2
                case "score":
                    if i + 2 < t.count {
                        if t[i+1] == "cp" { cp = Int(t[i+2]) ?? 0 }
                        else if t[i+1] == "mate" { mate = Int(t[i+2]) }
                    }
                    i += 3
                case "pv": pv = Array(t[(i+1)...]); i = t.count
                default: i += 1
                }
            }
            DispatchQueue.main.async { self.onInfo?(depth, cp, mate, pv) }
        }
    }

    func send(_ cmd: String) {
        guard let h = stdinPipe?.fileHandleForWriting,
              let d = (cmd + "\n").data(using: .utf8) else { return }
        h.write(d)
    }

    func newGame() { send("ucinewgame"); send("isready") }

    /// Ask for a move. `depth` limits strength; `millis` is a time budget.
    func go(moves: [String], depth: Int?, millis: Int?) {
        let pos = moves.isEmpty ? "position startpos"
                                : "position startpos moves " + moves.joined(separator: " ")
        send(pos)
        if let d = depth { send("go depth \(d)") }
        else { send("go movetime \(millis ?? 2000)") }
    }

    /// Legal moves and terminal state for a position, answered by the engine
    /// itself so the GUI never needs its own rules implementation.
    var onPosition: (([String], String) -> Void)?

    func query(moves: [String]) {
        let pos = moves.isEmpty ? "position startpos"
                                : "position startpos moves " + moves.joined(separator: " ")
        send(pos)
        send("legal")
        send("status")
    }

    func stop() {
        send("quit")
        process?.terminate()
        process = nil
    }
}

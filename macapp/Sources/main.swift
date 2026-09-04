import AppKit

// A native macOS chess app. The board is drawn with AppKit; all chess rules
// come from the engine over UCI, so there is no second rules implementation
// to disagree with the first.

// Solid glyphs, painted white or black so the two sides are distinguishable.
let SOLID: [Character: String] = [
    "k": "\u{265A}", "q": "\u{265B}", "r": "\u{265C}",
    "b": "\u{265D}", "n": "\u{265E}", "p": "\u{265F}",
]

let PIECE: [Character: String] = [
    "K": "\u{2654}", "Q": "\u{2655}", "R": "\u{2656}",
    "B": "\u{2657}", "N": "\u{2658}", "P": "\u{2659}",
    "k": "\u{265A}", "q": "\u{265B}", "r": "\u{265C}",
    "b": "\u{265D}", "n": "\u{265E}", "p": "\u{265F}",
]

final class BoardView: NSView {
    var pieces: [String: Character] = [:]      // "e4" -> 'P'
    var legal: [String] = []
    var selected: String?
    var lastMove: (String, String)?
    var boardFlipped = false   // view from Black's side
    var onMove: ((String) -> Void)?
    var interactive = true

    override var isFlipped: Bool { true }

    private func squareName(_ col: Int, _ row: Int) -> String {
        // isFlipped puts the origin at the top-left, so row 0 is rank 8.
        let f = boardFlipped ? 7 - col : col
        let r = boardFlipped ? row : 7 - row
        _ = r
        let rank = boardFlipped ? row + 1 : 8 - row
        return "\(Character(UnicodeScalar(97 + f)!))\(rank)"
    }

    override func draw(_ dirty: NSRect) {
        let size = min(bounds.width, bounds.height)
        let cell = size / 8
        let light = NSColor(calibratedRed: 0.93, green: 0.93, blue: 0.82, alpha: 1)
        let dark  = NSColor(calibratedRed: 0.46, green: 0.59, blue: 0.34, alpha: 1)

        for row in 0..<8 {
            for col in 0..<8 {
                let name = squareName(col, row)
                let rect = NSRect(x: CGFloat(col) * cell, y: CGFloat(row) * cell,
                                  width: cell, height: cell)
                ((row + col) % 2 == 0 ? light : dark).setFill()
                rect.fill()

                if let (from, to) = lastMove, name == from || name == to {
                    NSColor(calibratedRed: 1, green: 0.9, blue: 0.3, alpha: 0.35).setFill()
                    rect.fill()
                }
                if name == selected {
                    NSColor(calibratedRed: 0.3, green: 0.6, blue: 1, alpha: 0.45).setFill()
                    rect.fill()
                }
                // Destination hints for the selected piece.
                if let sel = selected,
                   legal.contains(where: { $0.hasPrefix(sel + name) }) {
                    let occupied = pieces[name] != nil
                    NSColor(white: 0, alpha: 0.22).setFill()
                    if occupied {
                        let p = NSBezierPath(ovalIn: rect.insetBy(dx: 3, dy: 3))
                        p.lineWidth = 5
                        NSColor(white: 0, alpha: 0.22).setStroke()
                        p.stroke()
                    } else {
                        let d = cell * 0.24
                        NSBezierPath(ovalIn: NSRect(x: rect.midX - d/2, y: rect.midY - d/2,
                                                    width: d, height: d)).fill()
                    }
                }

                if let ch = pieces[name] {
                    // Use the SOLID glyph set for both colours and paint it,
                    // so white and black are unmistakable at a glance.
                    let solid = SOLID[Character(String(ch).lowercased())] ?? "?"
                    let isWhite = ch.isUppercase
                    let font = NSFont.systemFont(ofSize: cell * 0.74)
                    let outline = NSAttributedString(string: solid, attributes: [
                        .font: font,
                        .foregroundColor: isWhite ? NSColor.black : NSColor.black,
                        .strokeWidth: 6.0,
                        .strokeColor: NSColor.black,
                    ])
                    let fill = NSAttributedString(string: solid, attributes: [
                        .font: font,
                        .foregroundColor: isWhite ? NSColor.white : NSColor(white: 0.13, alpha: 1),
                    ])
                    let sz = fill.size()
                    let pt = NSPoint(x: rect.midX - sz.width / 2,
                                     y: rect.midY - sz.height / 2)
                    outline.draw(at: pt)
                    fill.draw(at: pt)
                }
            }
        }
    }

    override func mouseDown(with event: NSEvent) {
        guard interactive else { return }
        let pt = convert(event.locationInWindow, from: nil)
        let cell = min(bounds.width, bounds.height) / 8
        let col = Int(pt.x / cell), row = Int(pt.y / cell)
        guard (0..<8).contains(col), (0..<8).contains(row) else { return }
        let name = squareName(col, row)

        if let sel = selected {
            // Promotions default to a queen, which is right nearly always.
            let candidates = legal.filter { $0.hasPrefix(sel + name) }
            if let mv = candidates.first(where: { $0.count == 5 && $0.hasSuffix("q") })
                        ?? candidates.first {
                selected = nil
                needsDisplay = true
                onMove?(mv)
                return
            }
            selected = (name == sel) ? nil : (legal.contains { $0.hasPrefix(name) } ? name : nil)
        } else if legal.contains(where: { $0.hasPrefix(name) }) {
            selected = name
        }
        needsDisplay = true
    }
}

final class GameController: NSObject, NSApplicationDelegate {
    let engine = Engine()
    var window: NSWindow!
    var board: BoardView!
    var statusLabel: NSTextField!
    var evalLabel: NSTextField!
    var levelPopup: NSPopUpButton!
    var fightBtn: NSButton!
    var eloPopup: NSPopUpButton!
    var moves: [String] = []
    var humanIsWhite = true
    var thinking = false

    // Fight mode: our engine vs Stockfish, played out on the board.
    var fighting = false
    var opponent: Engine?
    // A third engine instance used only to ask "is the game over?". The two
    // playing engines are busy searching, and UCI ignores commands mid-search.
    var referee: Engine?
    var oursIsWhite = true
    var fightElo = 2200
    var fightScore = (w: 0, d: 0, l: 0)
    var fightGamesLeft = 0

    // Difficulty: shallow search blunders like a human, rather than simply
    // playing the same strong move more slowly.
    let levels: [(String, Int?, Int?)] = [
        ("Beginner", 1, nil), ("Easy", 2, nil), ("Casual", 4, nil),
        ("Club", 6, nil), ("Strong", 8, nil),
        ("Full strength", nil, 2000), ("Maximum", nil, 8000),
    ]

    func applicationDidFinishLaunching(_ note: Notification) {
        buildWindow()
        guard engine.start() else {
            statusLabel.stringValue = "Engine not found in the app bundle"
            return
        }
        engine.onPosition = { [weak self] legal, status in
            guard let self else { return }
            self.updatePosition(legal: legal, status: status)
        }
        engine.onBestMove = { [weak self] mv in
            guard let self else { return }
            if self.fighting { self.fightMoveArrived(mv, fromOurs: true) }
            else { self.enginePlayed(mv) }
        }
        engine.onInfo = { [weak self] depth, cp, mate, _ in
            guard let self else { return }
            let text: String
            if let m = mate { text = "Mate in \(abs(m))" }
            else {
                // Show the score from the human's point of view.
                let human = (self.moves.count % 2 == 0) == self.humanIsWhite
                let v = Double(human ? cp : -cp) / 100.0
                text = String(format: "%+.2f", v)
            }
            self.evalLabel.stringValue = "depth \(depth)   \(text)"
        }
        newGame()
    }

    func buildWindow() {
        let size = NSRect(x: 0, y: 0, width: 640, height: 748)
        window = NSWindow(contentRect: size,
                          styleMask: [.titled, .closable, .miniaturizable],
                          backing: .buffered, defer: false)
        window.title = "Kraken"
        window.center()

        let content = NSView(frame: size)

        board = BoardView(frame: NSRect(x: 20, y: 118, width: 600, height: 600))
        board.onMove = { [weak self] mv in self?.humanPlayed(mv) }
        content.addSubview(board)

        statusLabel = NSTextField(labelWithString: "Your move")
        statusLabel.frame = NSRect(x: 20, y: 83, width: 380, height: 22)
        statusLabel.font = .systemFont(ofSize: 15, weight: .medium)
        content.addSubview(statusLabel)

        evalLabel = NSTextField(labelWithString: "")
        evalLabel.frame = NSRect(x: 20, y: 60, width: 380, height: 20)
        evalLabel.font = .monospacedDigitSystemFont(ofSize: 12, weight: .regular)
        evalLabel.textColor = .secondaryLabelColor
        content.addSubview(evalLabel)

        levelPopup = NSPopUpButton(frame: NSRect(x: 415, y: 78, width: 150, height: 26))
        levelPopup.addItems(withTitles: levels.map { $0.0 })
        levelPopup.selectItem(at: 2)          // Casual: winnable for most players
        content.addSubview(levelPopup)

        let newBtn = NSButton(title: "New game", target: self, action: #selector(newGameAction))
        newBtn.frame = NSRect(x: 415, y: 46, width: 90, height: 26)
        content.addSubview(newBtn)

        let flipBtn = NSButton(title: "Flip", target: self, action: #selector(flipAction))
        flipBtn.frame = NSRect(x: 512, y: 46, width: 53, height: 26)
        content.addSubview(flipBtn)

        fightBtn = NSButton(title: "⚔ Fight Stockfish", target: self,
                            action: #selector(fightAction))
        fightBtn.frame = NSRect(x: 20, y: 6, width: 160, height: 24)
        fightBtn.bezelStyle = .rounded
        content.addSubview(fightBtn)

        eloPopup = NSPopUpButton(frame: NSRect(x: 186, y: 5, width: 120, height: 26))
        eloPopup.addItems(withTitles: ["SF 1500", "SF 1800", "SF 2000", "SF 2200",
                                       "SF 2500", "SF 2800", "SF max"])
        eloPopup.selectItem(at: 3)
        content.addSubview(eloPopup)

        window.contentView = content
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func newGameAction() {
        stopFight()
        newGame()
    }

    @objc func fightAction() {
        if fighting { stopFight(); return }
        startFight()
    }

    let fightElos = [1500, 1800, 2000, 2200, 2500, 2800, 0]

    func startFight() {
        fightElo = fightElos[eloPopup.indexOfSelectedItem]
        var opts: [String: String] = ["Threads": "1", "Hash": "64"]
        if fightElo > 0 {
            opts["UCI_LimitStrength"] = "true"
            opts["UCI_Elo"] = String(fightElo)
        }
        // Stockfish is installed via Homebrew on Apple silicon or Intel.
        let candidates = ["/opt/homebrew/bin/stockfish", "/usr/local/bin/stockfish"]
        guard let sfPath = candidates.first(where: {
            FileManager.default.isExecutableFile(atPath: $0) }) else {
            statusLabel.stringValue = "Stockfish not found — brew install stockfish"
            return
        }
        let sf = Engine()
        guard sf.start(path: sfPath, options: opts) else {
            statusLabel.stringValue = "Could not start Stockfish"
            return
        }
        sf.onBestMove = { [weak self] mv in self?.fightMoveArrived(mv, fromOurs: false) }
        opponent = sf

        // Referee: same engine, never asked to search, so it always answers.
        let ref = Engine()
        guard ref.start() else {
            statusLabel.stringValue = "Could not start referee engine"
            return
        }
        ref.onPosition = { [weak self] legal, status in
            self?.fightRefereed(legal: legal, status: status)
        }
        referee = ref

        fighting = true
        fightScore = (0, 0, 0)
        fightGamesLeft = 6
        fightBtn.title = "■ Stop"
        oursIsWhite = true
        beginFightGame()
    }

    func stopFight() {
        fighting = false
        opponent?.stop(); opponent = nil
        referee?.stop(); referee = nil
        fightBtn?.title = "⚔ Fight Stockfish"
    }

    func beginFightGame() {
        moves = []
        board.lastMove = nil
        board.selected = nil
        board.interactive = false
        board.pieces = Self.piecesAfter(moves: [])
        board.legal = []
        board.boardFlipped = !oursIsWhite
        board.needsDisplay = true
        engine.newGame()
        opponent?.newGame()
        referee?.newGame()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            self?.fightStep()
        }
    }

    /// Ask whichever side is to move for its move.
    func fightStep() {
        guard fighting else { return }
        let whiteToMove = moves.count % 2 == 0
        if whiteToMove == oursIsWhite {
            // Full strength in a fight, matched to Stockfish's time budget.
            engine.go(moves: moves, depth: nil, millis: 150)
        } else {
            opponent?.go(moves: moves, depth: nil, millis: 150)
        }
    }

    func fightMoveArrived(_ mv: String, fromOurs: Bool) {
        guard fighting else { return }
        guard mv != "(none)", mv != "0000" else { stopFight(); return }
        moves.append(mv)
        board.lastMove = (String(mv.prefix(2)), String(mv.dropFirst(2).prefix(2)))
        board.pieces = Self.piecesAfter(moves: moves)
        board.needsDisplay = true
        // Ask the referee whether the game continues.
        referee?.query(moves: moves)
    }

    /// Referee's verdict on the position after the last move.
    func fightRefereed(legal: [String], status: String) {
        guard fighting else { return }
        board.legal = legal
        board.needsDisplay = true
        fightPositionUpdated(status: status)
    }

    func fightPositionUpdated(status: String) {
        guard fighting else { return }
        if status == "playing" {
            // Small delay so the moves are watchable rather than a blur.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
                self?.fightStep()
            }
            return
        }
        // Game over: score it from our engine's perspective.
        switch status {
        case "white-wins": oursIsWhite ? (fightScore.w += 1) : (fightScore.l += 1)
        case "black-wins": oursIsWhite ? (fightScore.l += 1) : (fightScore.w += 1)
        default: fightScore.d += 1
        }
        fightGamesLeft -= 1
        let label = fightElo > 0 ? "SF \(fightElo)" : "SF max"
        statusLabel.stringValue =
            "vs \(label):  +\(fightScore.w) =\(fightScore.d) -\(fightScore.l)"
        if fightGamesLeft <= 0 {
            let n = fightScore.w + fightScore.d + fightScore.l
            let pct = n > 0 ? Double(fightScore.w) + 0.5 * Double(fightScore.d) : 0
            statusLabel.stringValue += String(format: "   (%.0f%%)", 100 * pct / Double(max(n,1)))
            stopFight()
            return
        }
        oursIsWhite.toggle()          // alternate colours between games
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
            self?.beginFightGame()
        }
    }

    @objc func flipAction() {
        board.boardFlipped.toggle()
        board.needsDisplay = true
    }

    func newGame() {
        moves = []
        board.lastMove = nil
        board.selected = nil
        engine.newGame()
        thinking = false
        statusLabel.stringValue = "Your move"
        evalLabel.stringValue = ""
        refresh()
    }

    func refresh() { engine.query(moves: moves) }

    func updatePosition(legal: [String], status: String) {
        if fighting { return }        // the referee drives the board in a fight
        board.pieces = Self.piecesAfter(moves: moves)
        board.legal = legal
        board.interactive = !thinking && status == "playing"
        board.needsDisplay = true

        switch status {
        case "checkmate", "white-wins", "black-wins":
            if fighting { break }
            let humanWon = (status == "white-wins") == humanIsWhite
            statusLabel.stringValue = humanWon ? "You win — checkmate" : "Engine wins — checkmate"
        case "draw-stalemate": statusLabel.stringValue = "Draw — stalemate"
        case "draw-fifty": statusLabel.stringValue = "Draw — fifty-move rule"
        case "draw-material": statusLabel.stringValue = "Draw — insufficient material"
        default:
            if fighting { break }          // the scoreboard owns the label
            let whiteToMove = moves.count % 2 == 0
            if thinking { statusLabel.stringValue = "Engine is thinking…" }
            else { statusLabel.stringValue = (whiteToMove == humanIsWhite)
                                             ? "Your move" : "Engine to move" }
        }
    }

    func humanPlayed(_ mv: String) {
        moves.append(mv)
        board.lastMove = (String(mv.prefix(2)), String(mv.dropFirst(2).prefix(2)))
        thinking = true
        refresh()
        // Let the board repaint before the engine starts thinking.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in
            guard let self else { return }
            let (_, depth, millis) = self.levels[self.levelPopup.indexOfSelectedItem]
            self.engine.go(moves: self.moves, depth: depth, millis: millis)
        }
    }

    func enginePlayed(_ mv: String) {
        thinking = false
        guard mv != "(none)", mv != "0000" else { refresh(); return }
        moves.append(mv)
        board.lastMove = (String(mv.prefix(2)), String(mv.dropFirst(2).prefix(2)))
        refresh()
    }

    /// Replay the move list to get the current placement. The engine is the
    /// authority on legality; this only tracks where pieces sit.
    static func piecesAfter(moves: [String]) -> [String: Character] {
        var b: [String: Character] = [:]
        let back = "RNBQKBNR"
        for (i, ch) in back.enumerated() {
            let file = Character(UnicodeScalar(97 + i)!)
            b["\(file)1"] = ch
            b["\(file)2"] = "P"
            b["\(file)7"] = "p"
            b["\(file)8"] = Character(String(ch).lowercased())
        }
        for mv in moves {
            let from = String(mv.prefix(2))
            let to = String(mv.dropFirst(2).prefix(2))
            guard let piece = b[from] else { continue }
            b[from] = nil

            // En passant: a pawn moving diagonally to an empty square captures
            // the pawn beside it, not on it.
            if (piece == "P" || piece == "p"), from.first != to.first, b[to] == nil {
                let capturedRank = from.dropFirst()
                b["\(to.first!)\(capturedRank)"] = nil
            }
            // Castling: the king moves two files, the rook jumps over it.
            if piece == "K" || piece == "k" {
                let ff = from.first!.asciiValue!, tf = to.first!.asciiValue!
                if abs(Int(tf) - Int(ff)) == 2 {
                    let rank = to.dropFirst()
                    if tf > ff { b["f\(rank)"] = b["h\(rank)"]; b["h\(rank)"] = nil }
                    else       { b["d\(rank)"] = b["a\(rank)"]; b["a\(rank)"] = nil }
                }
            }
            if mv.count == 5, let promo = mv.last {
                let white = piece == "P"
                b[to] = white ? Character(String(promo).uppercased()) : promo
            } else {
                b[to] = piece
            }
        }
        return b
    }
}

let app = NSApplication.shared
let controller = GameController()
app.delegate = controller
app.setActivationPolicy(.regular)
app.run()

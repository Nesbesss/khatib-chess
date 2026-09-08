// Khatib Stats — a window onto whatever the training machine is doing.
//
// It runs scripts/training_status.py over ssh, parses the JSON, and draws
// tables. All parsing lives on the far side so this stays a viewer.
import AppKit

let HOST = ProcessInfo.processInfo.environment["KHATIB_HOST"] ?? "pandy@100.107.58.3"
let PASS = ProcessInfo.processInfo.environment["KHATIB_PASS"] ?? "1234"
let REPO = ProcessInfo.processInfo.environment["KHATIB_REPO"] ?? "~/khatib-chess"

// MARK: - Model

struct Epoch {
    let epoch: Int, total: Int
    let train: Double, val: Double
    let satLo: Int, satHi: Int, seconds: Int
}

struct Parity {
    let name: String, status: String
    let cp: Double, mismatches: Int
}

struct Run {
    let name: String
    let epochs: [Epoch]
    let parity: [Parity]
    let step: Int?, steps: Int?, epochNow: Int?, loss: Double?
    var isLive: Bool { step != nil }
}

struct Match {
    let name: String
    let elo: Double?, margin: Double?
    let games: Int?, w: Int?, l: Int?, d: Int?
}

struct Status {
    var runs: [Run] = []
    var matches: [Match] = []
    var load = "—", disk = "—"
    var training = false, generating = false, bot = false
    var positions = 0
    var error: String?
}

// MARK: - Fetch

func fetchStatus() -> Status {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/bin/sh")
    let remote = "cd \(REPO) && /usr/bin/python3 scripts/training_status.py"
    p.arguments = ["-c",
        "sshpass -p '\(PASS)' ssh -o StrictHostKeyChecking=no " +
        "-o PreferredAuthentications=password -o PubkeyAuthentication=no " +
        "-o ConnectTimeout=20 \(HOST) \"\(remote)\" 2>/dev/null"]
    let pipe = Pipe()
    p.standardOutput = pipe
    var s = Status()
    do { try p.run() } catch {
        s.error = "cannot run ssh: \(error.localizedDescription)"
        return s
    }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()

    guard let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
        s.error = data.isEmpty
            ? "no response from \(HOST) — is it reachable?"
            : "unreadable response"
        return s
    }

    for r in root["runs"] as? [[String: Any]] ?? [] {
        let eps = (r["epochs"] as? [[String: Any]] ?? []).map {
            Epoch(epoch: $0["epoch"] as? Int ?? 0, total: $0["total"] as? Int ?? 0,
                  train: $0["train"] as? Double ?? 0, val: $0["val"] as? Double ?? 0,
                  satLo: $0["sat_lo"] as? Int ?? 0, satHi: $0["sat_hi"] as? Int ?? 0,
                  seconds: $0["seconds"] as? Int ?? 0)
        }
        let par = (r["parity"] as? [[String: Any]] ?? []).map {
            Parity(name: $0["name"] as? String ?? "?",
                   status: $0["status"] as? String ?? "?",
                   cp: $0["float_int_cp"] as? Double ?? 0,
                   mismatches: $0["mismatches"] as? Int ?? 0)
        }
        let prog = r["progress"] as? [String: Any]
        s.runs.append(Run(name: r["name"] as? String ?? "?", epochs: eps, parity: par,
                          step: prog?["step"] as? Int, steps: prog?["steps"] as? Int,
                          epochNow: prog?["epoch"] as? Int, loss: prog?["loss"] as? Double))
    }
    for m in root["matches"] as? [[String: Any]] ?? [] {
        s.matches.append(Match(name: m["name"] as? String ?? "?",
                               elo: m["elo"] as? Double, margin: m["margin"] as? Double,
                               games: m["games"] as? Int, w: m["w"] as? Int,
                               l: m["l"] as? Int, d: m["d"] as? Int))
    }
    if let sys = root["system"] as? [String: Any] {
        s.load = sys["load"] as? String ?? "—"
        s.disk = sys["disk_free"] as? String ?? "—"
        s.training = sys["training"] as? Bool ?? false
        s.generating = sys["generating"] as? Bool ?? false
        s.bot = sys["bot"] as? Bool ?? false
        s.positions = sys["selfplay_positions"] as? Int ?? 0
    }
    return s
}

// MARK: - Drawing

let bg = NSColor(calibratedRed: 0.05, green: 0.07, blue: 0.09, alpha: 1)
let card = NSColor(calibratedRed: 0.086, green: 0.106, blue: 0.133, alpha: 1)
let ink = NSColor(calibratedWhite: 0.92, alpha: 1)
let sub = NSColor(calibratedWhite: 0.58, alpha: 1)
let accent = NSColor(calibratedRed: 0.94, green: 0.53, blue: 0.24, alpha: 1)
let good = NSColor(calibratedRed: 0.40, green: 0.80, blue: 0.50, alpha: 1)
let bad = NSColor(calibratedRed: 0.90, green: 0.40, blue: 0.40, alpha: 1)

func mono(_ size: CGFloat, _ weight: NSFont.Weight = .regular) -> NSFont {
    NSFont.monospacedSystemFont(ofSize: size, weight: weight)
}

final class StatsView: NSView {
    var status = Status()
    override var isFlipped: Bool { true }

    private func text(_ s: String, _ x: CGFloat, _ y: CGFloat,
                      _ f: NSFont, _ c: NSColor) {
        (s as NSString).draw(at: NSPoint(x: x, y: y),
                             withAttributes: [.font: f, .foregroundColor: c])
    }

    override func draw(_ r: NSRect) {
        bg.setFill(); r.fill()
        var y: CGFloat = 18
        let x: CGFloat = 22

        text("Khatib Training", x, y, .systemFont(ofSize: 22, weight: .bold), ink)
        let stamp = DateFormatter()
        stamp.dateFormat = "HH:mm:ss"
        text(stamp.string(from: Date()), bounds.width - 90, y + 6, mono(11), sub)
        y += 38

        if let e = status.error {
            text(e, x, y, .systemFont(ofSize: 13), bad)
            return
        }

        // Machine
        card.setFill(); NSRect(x: x, y: y, width: bounds.width - 2*x, height: 52).fill()
        let jobs = [("training", status.training), ("generating", status.generating),
                    ("bot", status.bot)]
        var jx = x + 14
        for (name, on) in jobs {
            text(on ? "●" : "○", jx, y + 8, mono(13), on ? good : sub)
            text(name, jx + 16, y + 9, .systemFont(ofSize: 12), on ? ink : sub)
            jx += CGFloat(name.count) * 7 + 42
        }
        text("load \(status.load)   disk \(status.disk)   " +
             "\(status.positions.formatted()) self-play positions",
             x + 14, y + 30, mono(11), sub)
        y += 68

        // Live run
        if let run = status.runs.first(where: { $0.isLive }),
           let step = run.step, let steps = run.steps {
            text("TRAINING NOW", x, y, .systemFont(ofSize: 11, weight: .semibold), accent)
            y += 20
            let frac = Double(step) / Double(max(steps, 1))
            let w = bounds.width - 2*x
            card.setFill(); NSRect(x: x, y: y, width: w, height: 10).fill()
            accent.setFill(); NSRect(x: x, y: y, width: w * CGFloat(frac), height: 10).fill()
            y += 18
            var line = "\(run.name)  epoch \(run.epochNow ?? 0)  step \(step)/\(steps)"
            if let l = run.loss { line += String(format: "  loss %.5f", l) }
            text(line, x, y, mono(12), ink)
            y += 28
        }

        // Epoch table
        if let run = status.runs.first(where: { !$0.epochs.isEmpty }) {
            text("EPOCHS · \(run.name)", x, y,
                 .systemFont(ofSize: 11, weight: .semibold), accent)
            y += 20
            let cols = ["epoch", "train", "val", "sat hi", "time"]
            let xs: [CGFloat] = [0, 90, 180, 270, 350].map { x + $0 }
            for (i, c) in cols.enumerated() { text(c, xs[i], y, mono(10), sub) }
            y += 16
            for e in run.epochs.suffix(8) {
                text("\(e.epoch)/\(e.total)", xs[0], y, mono(12), ink)
                text(String(format: "%.5f", e.train), xs[1], y, mono(12), ink)
                text(String(format: "%.5f", e.val), xs[2], y, mono(12), ink)
                text("\(e.satHi)%", xs[3], y, mono(12), e.satHi > 5 ? bad : good)
                text("\(e.seconds)s", xs[4], y, mono(12), sub)
                y += 17
            }
            y += 14
        }

        // Parity
        if let run = status.runs.first(where: { !$0.parity.isEmpty }) {
            text("PARITY · float vs exported net", x, y,
                 .systemFont(ofSize: 11, weight: .semibold), accent)
            y += 20
            for p in run.parity.suffix(6) {
                let ok = p.status == "pass"
                text(ok ? "✓" : "✗", x, y, mono(12), ok ? good : bad)
                text(p.name, x + 20, y, mono(12), ink)
                text(String(format: "%.2f cp", p.cp), x + 110, y, mono(12),
                     p.cp > 5 ? bad : sub)
                text("\(p.mismatches) mismatch", x + 190, y, mono(12),
                     p.mismatches > 0 ? bad : sub)
                y += 17
            }
            y += 14
        }

        // Matches
        if !status.matches.isEmpty {
            text("RECENT MATCHES", x, y,
                 .systemFont(ofSize: 11, weight: .semibold), accent)
            y += 20
            for m in status.matches.prefix(7) {
                text(m.name, x, y, mono(12), ink)
                if let e = m.elo, let mg = m.margin {
                    let c: NSColor = e - mg > 0 ? good : (e + mg < 0 ? bad : sub)
                    text(String(format: "%+.1f ± %.1f", e, mg), x + 230, y, mono(12), c)
                }
                if let g = m.games, let w = m.w, let l = m.l, let d = m.d {
                    text("\(g)g  \(w)W \(l)L \(d)D", x + 360, y, mono(12), sub)
                }
                y += 17
            }
        }
    }
}

// MARK: - App

final class Controller: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var view: StatsView!
    var timer: Timer?

    func applicationDidFinishLaunching(_ n: Notification) {
        let rect = NSRect(x: 0, y: 0, width: 620, height: 760)
        window = NSWindow(contentRect: rect,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Khatib Training"
        view = StatsView(frame: rect)
        window.contentView = view
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { _ in
            self.refresh()
        }
    }

    func refresh() {
        DispatchQueue.global(qos: .utility).async {
            let s = fetchStatus()
            DispatchQueue.main.async {
                self.view.status = s
                self.view.needsDisplay = true
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ a: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
let controller = Controller()
app.delegate = controller
app.setActivationPolicy(.regular)
app.run()

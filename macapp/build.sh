#!/bin/bash
# Build Chess.app with the engine and network bundled inside.
set -e
cd "$(dirname "$0")"
APP="$HOME/Applications/Kraken.app"
RES="$APP/Contents/Resources"
MACOS="$APP/Contents/MacOS"

# The engine must be current.
(cd .. && cargo build --release >/dev/null 2>&1)

rm -rf "$APP"
mkdir -p "$MACOS" "$RES"

swiftc -O -o "$MACOS/Kraken" Sources/Engine.swift Sources/main.swift \
  -target arm64-apple-macosx13.0 -framework AppKit

cp ../target/release/chess "$RES/chess"
cp ../net.nnue "$RES/net.nnue"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Kraken</string>
  <key>CFBundleDisplayName</key><string>Kraken</string>
  <key>CFBundleIdentifier</key><string>local.kraken.chess</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key><string>Kraken</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST

# Ad-hoc signature so Gatekeeper allows a locally built app to run.
codesign --force --deep --sign - "$APP" 2>/dev/null || true

echo "built: $APP"

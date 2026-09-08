#!/bin/bash
# Build Khatib Stats.app — a live view of the training machine.
set -e
cd "$(dirname "$0")"
APP="$HOME/Applications/Khatib Stats.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

swiftc -O -o "$APP/Contents/MacOS/KhatibStats" Sources/main.swift \
  -target arm64-apple-macosx13.0 -framework AppKit

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Khatib Stats</string>
  <key>CFBundleIdentifier</key><string>me.nesbes.khatib.stats</string>
  <key>CFBundleExecutable</key><string>KhatibStats</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

echo "built: $APP"

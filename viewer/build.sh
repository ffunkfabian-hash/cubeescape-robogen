#!/bin/bash
# Builds RobotViewer.app (SwiftUI + SceneKit, no Xcode project needed).
#   viewer/build.sh [output dir]     default: ../output
# Needs: Xcode command line tools (swiftc), ImageMagick (icon), the generator output (output/ViewerData).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="${1:-$ROOT/output}"
DATA="$OUT/ViewerData"
APP="$OUT/RobotViewer.app"
BUILD="$HERE/.build"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/scripts/generate_robot.py")"

[ -f "$DATA/robot.json" ] || { echo "missing $DATA/robot.json - run scripts/generate_robot.py first" >&2; exit 1; }
mkdir -p "$BUILD"

MIN_MACOS=13.0
# unchanged sources and compiler = no compile (the binary does not depend on the generator data)
STAMP="$(cat "$HERE"/Sources/*.swift | shasum | cut -c1-16)-$(swiftc --version 2>&1 | shasum | cut -c1-8)-$MIN_MACOS"
if [ -f "$BUILD/RobotViewer" ] && [ "$(cat "$BUILD/sources.stamp" 2>/dev/null)" = "$STAMP" ]; then
  echo "> sources unchanged - reusing the compiled viewer"
else
  echo "> compiling (universal: arm64 + x86_64 in parallel, macOS $MIN_MACOS+)"
  pids=()
  for arch in arm64 x86_64; do
    swiftc -O -parse-as-library -swift-version 5 -target "$arch-apple-macos$MIN_MACOS" \
      -framework SwiftUI -framework SceneKit -framework AppKit -framework AVFoundation -framework ImageIO \
      -framework CoreMedia -framework CoreVideo -framework UniformTypeIdentifiers \
      "$HERE"/Sources/*.swift -o "$BUILD/RobotViewer-$arch" &
    pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "$pid"; done
  lipo -create "$BUILD/RobotViewer-arm64" "$BUILD/RobotViewer-x86_64" -output "$BUILD/RobotViewer"
  echo "$STAMP" > "$BUILD/sources.stamp"
fi

echo "> bundling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/RobotViewer" "$APP/Contents/MacOS/RobotViewer"
cp -R "$DATA" "$APP/Contents/Resources/ViewerData"

ICON_SRC="$OUT/Previews/head.png"
ICON_STAMP="$( [ -f "$ICON_SRC" ] && shasum "$ICON_SRC" | cut -c1-16)"
if [ -f "$ICON_SRC" ] && [ -f "$BUILD/AppIcon.icns" ] && [ "$(cat "$BUILD/icon.stamp" 2>/dev/null)" = "$ICON_STAMP" ]; then
  cp "$BUILD/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
elif [ -f "$ICON_SRC" ] && command -v magick >/dev/null; then
  ICONSET="$BUILD/AppIcon.iconset"
  rm -rf "$ICONSET" && mkdir -p "$ICONSET"
  magick "$ICON_SRC" -gravity center -crop 78%x78%+0+0 +repage -resize 1024x1024 \
    \( -size 1024x1024 xc:none -fill white -draw "roundrectangle 0,0 1023,1023 190,190" \) \
    -compose DstIn -composite "$BUILD/icon_1024.png"
  for s in 16 32 64 128 256 512; do
    magick "$BUILD/icon_1024.png" -resize ${s}x${s} "$ICONSET/icon_${s}x${s}.png"
    magick "$BUILD/icon_1024.png" -resize $((s * 2))x$((s * 2)) "$ICONSET/icon_${s}x${s}@2x.png"
  done
  iconutil -c icns "$ICONSET" -o "$BUILD/AppIcon.icns"
  cp "$BUILD/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
  echo "$ICON_STAMP" > "$BUILD/icon.stamp"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>RobotViewer</string>
  <key>CFBundleDisplayName</key><string>Cube Escape Robot Viewer</string>
  <key>CFBundleIdentifier</key><string>com.cubeescape.robotviewer</string>
  <key>CFBundleExecutable</key><string>RobotViewer</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "warning: ad-hoc signing failed"
echo "> done: $APP"

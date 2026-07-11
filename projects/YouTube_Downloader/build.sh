#!/bin/bash
# Build YouTube Downloader into a standalone macOS .app

set -e

echo "📦 Installing dependencies..."
pip install -r requirements.txt
pip install pyinstaller

echo "🔨 Building .app..."
rm -rf "dist/YouTube Downloader.app"
pyinstaller \
  --name "YouTube Downloader" \
  --windowed \
  --onefile \
  --icon icon.icns \
  --clean \
  app.py

echo "🔏 Ad-hoc signing app bundle..."
xattr -cr "dist/YouTube Downloader.app"
codesign --force --deep --sign - "dist/YouTube Downloader.app"

echo ""
echo "✅ Done! App is at: dist/YouTube Downloader.app"
echo ""
echo "NOTE: FFmpeg must be installed on the target machine."
echo "Install it with: brew install ffmpeg"
echo ""
echo "NOTE: This is ad-hoc signed, which is enough to run it on this Mac"
echo "without a Gatekeeper block. Sharing it with others requires a paid"
echo "Apple Developer ID (\$99/yr) and notarization — without that, they'll"
echo "need to right-click > Open the first time."

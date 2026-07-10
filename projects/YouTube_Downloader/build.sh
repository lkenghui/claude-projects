#!/bin/bash
# Build YouTube Downloader into a standalone macOS .app

set -e

echo "📦 Installing dependencies..."
pip install -r requirements.txt
pip install pyinstaller

echo "🔨 Building .app..."
pyinstaller \
  --name "YouTube Downloader" \
  --windowed \
  --onefile \
  --clean \
  app.py

echo ""
echo "✅ Done! App is at: dist/YouTube Downloader"
echo ""
echo "NOTE: FFmpeg must be installed on the target machine."
echo "Install it with: brew install ffmpeg"

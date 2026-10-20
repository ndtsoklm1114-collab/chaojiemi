#!/bin/zsh
# 重新打包潮解密.app。改過 app/ 或 deid/ 任何檔之後跑一次，約一分鐘。
# 建置暫存放本機 /tmp，成品複製到 ~/Applications/潮解密.app。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="$HOME/.venvs/deidentify/bin/python"
WORK="$(mktemp -d /tmp/chaojiemi-build.XXXX)"
"$PY" -m PyInstaller --noconfirm --clean --windowed --name 潮解密 \
  --icon "$HERE/icon/潮解密.icns" \
  --add-data "$HERE/ui.html:app" \
  --paths "$ROOT" \
  --hidden-import deid --hidden-import deid.core --hidden-import deid.handlers --hidden-import deid.ocr --hidden-import deid.run --hidden-import deid.candidates \
  --hidden-import Vision --hidden-import Quartz --hidden-import WebKit --hidden-import Foundation \
  --hidden-import pymupdf --hidden-import docx --hidden-import openpyxl --hidden-import PIL \
  --osx-bundle-identifier tw.ptatrc.chaojiemi \
  --workpath "$WORK/build" --distpath "$WORK/dist" --specpath "$WORK" \
  "$HERE/main.py" >/dev/null
mkdir -p "$HOME/Applications"
if [ -d "$HOME/Applications/潮解密.app" ]; then trash "$HOME/Applications/潮解密.app"; fi
cp -R "$WORK/dist/潮解密.app" "$HOME/Applications/潮解密.app"
echo "✅ 已更新 $HOME/Applications/潮解密.app（建置暫存在 $WORK，可丟）"

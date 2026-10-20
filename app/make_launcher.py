#!/usr/bin/env python
"""做一個「啟動器」版的潮解密.app：.app 裡只有一支 shell 腳本，開啟時去跑
~/.venvs/deidentify 的 Python 執行 app/main.py。改完程式不用重打包，⌘Q 重開就是新版。

    ~/.venvs/deidentify/bin/python app/make_launcher.py ~/Applications/潮解密.app

目標已存在時不會覆蓋，先自己把舊的丟廢紙簍再跑。
代價：venv 或程式碼資料夾不在，.app 就開不了（PyInstaller 版是自帶的）。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = Path.home() / ".venvs" / "deidentify" / "bin" / "python"
ICON = HERE / "icon" / "潮解密.icns"

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>潮解密</string>
  <key>CFBundleDisplayName</key><string>潮解密</string>
  <key>CFBundleIdentifier</key><string>tw.ptatrc.chaojiemi.launcher</string>
  <key>CFBundleExecutable</key><string>潮解密</string>
  <key>CFBundleIconFile</key><string>潮解密.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>launcher</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
"""

# 腳本裡故意不放任何刪除指令。找不到 venv 或 main.py 時用對話框講清楚，不要默默失敗。
SCRIPT = """#!/bin/zsh
PY="{py}"
MAIN="{main}"
if [ ! -x "$PY" ]; then
  osascript -e 'display dialog "找不到 ~/.venvs/deidentify 的 Python，潮解密開不了。重建方法見 README.md 的安裝一節。" buttons {{"知道了"}} with icon stop'
  exit 1
fi
if [ ! -f "$MAIN" ]; then
  osascript -e 'display dialog "找不到 iCloud 上的 app/main.py，可能還沒下載或資料夾搬了。" buttons {{"知道了"}} with icon stop'
  exit 1
fi
cd "$(dirname "$MAIN")/.."
exec "$PY" "$MAIN" "$@"
"""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1]).expanduser()
    if target.exists():
        print(f"目標已存在，不覆蓋：{target}\n先 trash 它再跑一次。")
        return 1
    if not PY.exists():
        print(f"找不到 venv 的 Python：{PY}")
        return 1
    main_py = HERE / "main.py"
    (target / "Contents" / "MacOS").mkdir(parents=True)
    (target / "Contents" / "Resources").mkdir()
    (target / "Contents" / "Info.plist").write_text(PLIST, encoding="utf-8")
    exe = target / "Contents" / "MacOS" / "潮解密"
    exe.write_text(SCRIPT.format(py=PY, main=main_py), encoding="utf-8")
    os.chmod(exe, 0o755)
    shutil.copy(ICON, target / "Contents" / "Resources" / "潮解密.icns")
    print(f"✅ 已做好啟動器：{target}\n   執行：{PY}\n   程式：{main_py}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

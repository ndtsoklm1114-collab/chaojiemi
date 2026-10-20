"""定稿 I「括號鑰匙」：產 1024 PNG、.iconset、.icns，寫回這個資料夾（app/icon）；給了 .app 路徑就一併換進去。
    python app/icon/make_icon.py [某某.app]
需要 rsvg-convert、iconutil、magick。"""
import shutil, subprocess, sys
from pathlib import Path

OUT = Path.home() / "Downloads" / "潮解密圖示"
REPO_ICON = Path(__file__).resolve().parent
APP_ICON = Path(sys.argv[1]).expanduser() / "Contents" / "Resources" / "潮解密.icns" if len(sys.argv) > 1 else None
S, R, Y = 1024, 232, "#F5C400"

SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 {S} {S}">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2e3036"/><stop offset="1" stop-color="#121317"/></linearGradient>
  <linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".13"/><stop offset=".5" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".18"/></linearGradient>
  <linearGradient id="gold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFD43A"/><stop offset="1" stop-color="#EDB800"/></linearGradient>
  <filter id="shadow" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="14" stdDeviation="14" flood-color="#000" flood-opacity=".35"/></filter>
</defs>
<rect width="{S}" height="{S}" rx="{R}" fill="url(#bg)"/>
<rect width="{S}" height="{S}" rx="{R}" fill="url(#sheen)"/>
<g filter="url(#shadow)">
  <!-- 括號：〔 〕，線寬 66，圓頭 -->
  <path d="M286 292 h-104 v440 h104" fill="none" stroke="url(#gold)" stroke-width="66" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M738 292 h104 v440 h-104" fill="none" stroke="url(#gold)" stroke-width="66" stroke-linecap="round" stroke-linejoin="round"/>
  <!-- 鑰匙：圓頭＋柄（遮條）＋兩個齒，整體置中 -->
  <circle cx="392" cy="512" r="84" fill="none" stroke="url(#gold)" stroke-width="58"/>
  <rect x="466" y="481" width="250" height="62" rx="31" fill="url(#gold)"/>
  <rect x="612" y="536" width="42" height="58" rx="13" fill="url(#gold)"/>
  <rect x="672" y="536" width="42" height="80" rx="13" fill="url(#gold)"/>
</g>
</svg>'''

(OUT / "I_final.svg").write_text(SVG, encoding="utf-8")
png = OUT / "I_final_1024.png"
subprocess.run(["rsvg-convert", "-w", "1024", "-h", "1024", "-o", str(png), str(OUT / "I_final.svg")], check=True)
iconset = OUT / "潮解密.iconset"
if iconset.exists():
    shutil.rmtree(iconset)  # 只是上一次跑的暫存夾
iconset.mkdir()
for sz in (16, 32, 128, 256, 512):
    subprocess.run(["magick", str(png), "-resize", f"{sz}x{sz}", str(iconset / f"icon_{sz}x{sz}.png")], check=True)
    subprocess.run(["magick", str(png), "-resize", f"{sz*2}x{sz*2}", str(iconset / f"icon_{sz}x{sz}@2x.png")], check=True)
icns = OUT / "潮解密.icns"
subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
# 預覽圖：512 ＋ 64 ＋ 32 ＋ 16
subprocess.run(["magick", "-background", "#ececea", "-gravity", "center", str(iconset / "icon_512x512.png"), "-bordercolor", "#ececea", "-border", "40",
                "(", str(iconset / "icon_32x32@2x.png"), str(iconset / "icon_32x32.png"), str(iconset / "icon_16x16.png"), "-bordercolor", "#ececea", "-border", "14", "-append", ")",
                "+append", str(OUT / "最終圖示預覽.png")], check=True)
# 換進 repo 與 .app
shutil.copy(icns, REPO_ICON / "潮解密.icns"); shutil.copy(png, REPO_ICON / "icon_1024.png")
if APP_ICON:
    shutil.copy(icns, APP_ICON)
    subprocess.run(["touch", str(APP_ICON.parents[2])])  # 讓 Finder 重讀圖示
print("icns", icns.stat().st_size, "bytes")
print("repo", REPO_ICON / "潮解密.icns", (REPO_ICON / "潮解密.icns").stat().st_size)
if APP_ICON:
    print("app ", APP_ICON, APP_ICON.stat().st_size)

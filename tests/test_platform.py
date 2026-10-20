"""非 macOS 的路徑：OCR 不在時，掃描件要給清楚的錯，不能整個程式 import 就掛。"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from deid import ocr
from deid.run import deidentify_file
from deid.core import NameList

checks = []
# 1. 模擬沒有 Vision
ocr.AVAILABLE = False
try:
    ocr.recognize(b"")
    checks.append(("沒 OCR 時 recognize 要丟 OcrUnavailable", False))
except ocr.OcrUnavailable as e:
    checks.append(("沒 OCR 時 recognize 要丟 OcrUnavailable", "此平台沒有 OCR" in str(e)))
# 2. 圖片走到 deidentify_file 會以那句錯失敗，文字檔照常
from PIL import Image
with tempfile.TemporaryDirectory() as d:
    d = pathlib.Path(d)
    img = d / "a.png"; Image.new("RGB", (40, 40), "white").save(img)
    try:
        deidentify_file(img, NameList(names={"王小明": "個案"}), out_dir=d / "out", map_dir=d / "map")
        checks.append(("圖片在沒 OCR 的平台要失敗", False))
    except ocr.OcrUnavailable as e:
        checks.append(("圖片在沒 OCR 的平台要失敗", "掃描 PDF 與圖片做不了" in str(e)))
    txt = d / "b.txt"; txt.write_text("姓名：王小明", encoding="utf-8")
    r = deidentify_file(txt, NameList(names={"王小明": "個案"}), out_dir=d / "out", map_dir=d / "map")
    checks.append(("文字檔不受影響", "〔個案01〕" in r.output.read_text(encoding="utf-8")))
ocr.AVAILABLE = sys.platform == "darwin"
bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(("✅" if ok else "❌"), n)
print("RESULT", "全對" if not bad else f"錯 {len(bad)}")

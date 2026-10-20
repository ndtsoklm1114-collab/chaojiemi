"""測試用：從檔案裡「姓名／申請人／反映人／聯絡人」欄位後面抓候選姓名，寫成名單檔。
只寫檔不印出來，讓 Claude 不用看到真名就能跑測試。正式使用時名單由主任自己寫。"""
import re, sys, pathlib
from pathlib import Path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from deid import handlers as H

src = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2])
col = sys.argv[3] if len(sys.argv) > 3 else None  # xlsx 指定欄名
ext = src.suffix.lower()
names = []
if ext == ".docx":
    text = H.docx_text(src)
    for m in re.finditer(r"(姓名|申請人|反映人|反應人|聯絡人)[：:]\s*([一-鿿]{2,4})", text):
        names.append(m.group(2))
elif ext in (".xlsx", ".xlsm"):
    import openpyxl
    wb = openpyxl.load_workbook(str(src), read_only=True)
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header or col not in header: continue
        idx = list(header).index(col)
        for r in rows:
            v = r[idx] if idx < len(r) else None
            if isinstance(v, str) and 2 <= len(v.strip()) <= 4:
                names.append(v.strip())
# 檔名裡的人名：「-日期姓名申復」或「-姓名 日期」兩種寫法
orig = Path(sys.argv[4]).name if len(sys.argv) > 4 and sys.argv[4] else src.name
orig = re.sub(r"申復|\(\d\)", "", orig)
for pat in (r"-\s*\d{6,7}\s*([\u4e00-\u9fff]{2,4})", r"-\s*([\u4e00-\u9fff]{2,4})\s+\d{6,7}"):
    m = re.search(pat, orig)
    if m: names.append(m.group(1))
uniq = []
for n in names:
    n = re.sub(r"(先生|小姐|女士|太太)$", "", n)
    if n and n not in uniq and n not in ("申請人", "反映人"): uniq.append(n)
out.write_text("[個案]\n" + "\n".join(uniq) + "\n[不換]\n屏東縣輔具資源中心\n", encoding="utf-8")
print(f"名單寫好：{out}，{len(uniq)} 個名字（內容不印）")

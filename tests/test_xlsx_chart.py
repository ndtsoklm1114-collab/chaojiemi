"""xlsx 直接改 XML：圖表留著、共用字串被清空、公式字串快取也換、還原逐格相同。
openpyxl 存的檔沒有 sharedStrings，所以另外用 LibreOffice 轉一份（有 sharedStrings、有公式快取）一起驗；沒裝 LibreOffice 那段就跳過。全部假資料。"""
import sys, pathlib, tempfile, zipfile, shutil, subprocess, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import openpyxl
from openpyxl.chart import BarChart, Reference
from deid.core import NameList
from deid.run import deidentify_file, restore_file

SOFFICE = next((p for p in ("/Applications/LibreOffice.app/Contents/MacOS/soffice", shutil.which("soffice") or "") if p and pathlib.Path(p).exists()), None)


def build(path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "清冊"
    ws.append(["姓名", "出生日期", "金額", "備註"])
    ws.append(["王小明", datetime.date(1956, 3, 2), 100, "電話0912-345-678"])
    ws.append(["王小明", datetime.date(1950, 1, 1), 200, ""])
    ws["C4"] = "=SUM(C2:C3)"; ws["D3"] = '=A2&"先生"'
    ch = BarChart(); ch.add_data(Reference(ws, min_col=3, min_row=1, max_row=3), titles_from_data=True); ws.add_chart(ch, "F2")
    wb.properties.creator = "王小明"
    wb.save(path)


def check_one(label, src, d, checks):
    nl = NameList(names={"王小明": "個案"}, skip=[])
    r = deidentify_file(src, nl, d / "out", d / "map")
    with zipfile.ZipFile(r.output) as z:
        names = z.namelist()
        checks.append((f"[{label}] 圖表還在", any(n.startswith("xl/charts/") for n in names)))
        blob = b"".join(z.read(n) for n in names if n.endswith(".xml"))
        checks.append((f"[{label}] 整包 XML 裡沒有真名、電話", "王小明".encode() not in blob and b"0912-345-678" not in blob and "王小明".encode("unicode_escape") not in blob))
        core = z.read("docProps/core.xml").decode()
        checks.append((f"[{label}] 作者清空", "王小明" not in core))
    o = openpyxl.load_workbook(r.output)["清冊"]
    checks.append((f"[{label}] 儲存格換成代號", o["A2"].value == "〔個案01〕" and o["A3"].value == "〔個案01〕" and o["B2"].value == "〔生日01〕"))
    checks.append((f"[{label}] 公式原樣", o["C4"].value == "=SUM(C2:C3)"))
    checks.append((f"[{label}] 殘留 0", r.residue == []))
    rr = restore_file(r.output, r.mapping, d / "back")
    a, b = openpyxl.load_workbook(src), openpyxl.load_workbook(rr.output)
    diff = [(c.coordinate, c.value, b["清冊"][c.coordinate].value) for row in a["清冊"].iter_rows() for c in row if b["清冊"][c.coordinate].value != c.value]
    checks.append((f"[{label}] 還原逐格相同", diff == []))
    if diff:
        print("   差異：", diff[:3])
    with zipfile.ZipFile(rr.output) as z:
        checks.append((f"[{label}] 還原版圖表還在", any(n.startswith("xl/charts/") for n in z.namelist())))


def run() -> bool:
    checks = []
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        build(d / "A.xlsx")
        check_one("openpyxl 存的", d / "A.xlsx", d / "1", checks)
        if SOFFICE:
            subprocess.run([SOFFICE, "--headless", "--convert-to", "xlsx", "--outdir", str(d / "lo"), str(d / "A.xlsx")], capture_output=True, timeout=120)
            lo = d / "lo" / "A.xlsx"
            if lo.exists():
                with zipfile.ZipFile(lo) as z:
                    has_sst = "xl/sharedStrings.xml" in z.namelist()
                checks.append(("[LibreOffice 轉的] 有 sharedStrings 可以驗", has_sst))
                check_one("LibreOffice 轉的", lo, d / "2", checks)
            else:
                print("   （LibreOffice 轉檔沒產出，這段跳過）")
        else:
            print("   （沒裝 LibreOffice，sharedStrings 那段跳過）")
    ok = True
    for name, passed in checks:
        print(("✓ " if passed else "✗ ") + name)
        ok = ok and passed
    print("RESULT", "全對" if ok else "有錯")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)

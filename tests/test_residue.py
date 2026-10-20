"""殘留檢查：去識別版文字抽回來，對照表上的真值還找得到就要報。全部假資料。"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from deid.core import NameList, find_residue
from deid.run import deidentify_file


def run() -> bool:
    checks = []
    entries = [
        {"code": "身分證01", "kind": "身分證", "value": "A123456789"},
        {"code": "電話01", "kind": "電話", "value": "0912-345-678"},
        {"code": "個案01姓", "kind": "個案", "value": "王", "屬於": "王小明"},
        {"code": "個案01", "kind": "個案", "value": "王小明"},
    ]
    after = "〔身分證01〕 王先生 電話 0912 345 678 另一個 王小華"
    hit = find_residue(after, entries)
    checks.append(("電話分隔不同也抓得到殘留", "電話01" in hit))
    checks.append(("已換掉的身分證不算殘留", "身分證01" not in hit))
    checks.append(("姓氏別名「王」不算殘留", "個案01姓" not in hit))
    checks.append(("王小明沒出現就不算（王小華不算）", "個案01" not in hit))
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "a.txt").write_text("個案王小明，身分證A123456789，電話0912-345-678", encoding="utf-8")
        r = deidentify_file(d / "a.txt", NameList(names={"王小明": "個案"}, skip=[]), d / "out", d / "map")
        checks.append(("正常去識別後殘留是空的", r.residue == []))
        checks.append(("沒殘留就沒有殘留備註", not any("殘留" in n for n in r.notes)))
    ok = True
    for name, passed in checks:
        print(("✓ " if passed else "✗ ") + name)
        ok = ok and passed
    print("RESULT", "全對" if ok else "有錯")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)

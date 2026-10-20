"""整批共用代號：同一個人在每個檔同一個代號；對照表仍一檔一張、次數只算該檔；代號簿存了再載入接著編。全部假資料。"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from deid.core import NameList, CodeBook, read_mapping
from deid.run import deidentify_file, restore_file


def run() -> bool:
    checks = []
    nl = NameList(names={"王小明": "個案", "李大華": "個案", "林美玲": "家屬"}, skip=[])
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "九月.txt").write_text("王小明 A123456789 王小明再來一次 李大華", encoding="utf-8")
        (d / "十月.txt").write_text("李大華 0912-345-678 王小明 林美玲", encoding="utf-8")
        # 沒共用：兩個檔各自從 01 編
        a = deidentify_file(d / "九月.txt", nl, d / "o1", d / "m1")
        b = deidentify_file(d / "十月.txt", nl, d / "o1", d / "m1")
        ma, mb = read_mapping(a.mapping), read_mapping(b.mapping)
        code = lambda m, v: next((e["code"] for e in m["對照"] if e["value"] == v), None)
        checks.append(("沒共用：兩檔的第一個人都是個案01", code(ma, "王小明") == "個案01" and code(mb, "李大華") == "個案01"))
        # 共用一本：十月的王小明沿用九月的代號
        book = CodeBook()
        a2 = deidentify_file(d / "九月.txt", nl, d / "o2", d / "m2", book=book)
        b2 = deidentify_file(d / "十月.txt", nl, d / "o2", d / "m2", book=book)
        ma2, mb2 = read_mapping(a2.mapping), read_mapping(b2.mapping)
        checks.append(("共用：王小明兩檔都是個案01、李大華都是個案02", code(ma2, "王小明") == "個案01" == code(mb2, "王小明") and code(ma2, "李大華") == "個案02" == code(mb2, "李大華")))
        checks.append(("共用：十月才出現的林美玲是家屬01", code(mb2, "林美玲") == "家屬01"))
        checks.append(("對照表仍一檔一張：十月的對照表沒有身分證", all(e["kind"] != "身分證" for e in mb2["對照"])))
        cnt = lambda m, v: next((e["count"] for e in m["對照"] if e["value"] == v), None)
        checks.append(("次數只算該檔：九月王小明 2 次、十月 1 次", cnt(ma2, "王小明") == 2 and cnt(mb2, "王小明") == 1))
        r = restore_file(b2.output, b2.mapping, d / "back")
        checks.append(("共用下還原前的代號核對沒問題（次數只算該檔）", r.problems == 0))
        checks.append(("共用下十月還原回原文", r.output.read_text(encoding="utf-8") == "李大華 0912-345-678 王小明 林美玲"))
        # 代號簿存檔、下個月載入接著編
        book.save(d / "簿.json")
        book2 = CodeBook(); n = book2.load(d / "簿.json")
        (d / "十一月.txt").write_text("張志豪 王小明", encoding="utf-8")
        nl2 = NameList(names={"王小明": "個案", "張志豪": "個案"}, skip=[])
        c3 = deidentify_file(d / "十一月.txt", nl2, d / "o3", d / "m3", book=book2)
        mc = read_mapping(c3.mapping)
        checks.append((f"載入代號簿 {n} 個後：王小明仍是個案01，新人張志豪接著編成個案03", code(mc, "王小明") == "個案01" and code(mc, "張志豪") == "個案03"))
        checks.append(("載入單檔對照表也行", CodeBook().load(a2.mapping) >= 3))
    ok = True
    for name, passed in checks:
        print(("✓ " if passed else "✗ ") + name)
        ok = ok and passed
    print("RESULT", "全對" if ok else "有錯")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)

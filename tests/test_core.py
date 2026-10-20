import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from deid.core import NameList, Mapper, deidentify_text, restore_text, check_codes, find_spans

HERE = pathlib.Path(__file__).parent

KEEP = [
    "財編 4A1231030100002 這台輪椅",
    "案號 1140113 受理",
    "收據號 BFR001688 已開",
    "115/8/6 退租，受理日 115.09.30，聯絡日 2026-01-05",
    "屏東縣輔具資源中心（屏東市自由路527號）",
    "分機 1234，案件編號 2026010501",
]
CHANGE = {
    "身分證 A123456789 核對": "身分證",
    "居留證 A800123456 核對": "身分證",
    "手機 0912-345-678 與 0987654321": "電話",
    "PDF 抽出來的連字號 0912\u2010345\u2010678 與 Word 的 0912\u2013345\u2013678": "電話",
    "西元生日 1950\u20100302 不是；1950\u201003\u201002 是": "生日",
    "市話 08-7000000、(08)7000000、07 1234567": "電話",
    "民國生日 45/3/2，另一人 62.11.30 出生": "生日",
    "出生日期：115/8/6": "生日",
    "西元生日 1950-03-02": "生日",
    "住內埔鄉內田村中山路12號": "地址",
    "地址：屏東縣潮州鎮光春路3巷5弄7號2樓": "地址",
}

def run():
    nl = NameList.load(HERE / "namelist_sample.txt")
    ok = True
    for s in KEEP:
        m = Mapper()
        out = deidentify_text(s, nl, m)
        flag = "對" if out == s else "錯"
        ok &= out == s
        print(f"[不該換] {flag}  {s!r} -> {out!r}")
    for s, kind in CHANGE.items():
        m = Mapper()
        out = deidentify_text(s, nl, m)
        kinds = {e["kind"] for e in m.to_list()}
        flag = "對" if kind in kinds and out != s else "錯"
        ok &= flag == "對"
        print(f"[該換 {kind}] {flag}  {s!r} -> {out!r}")
    # 姓名：全名、帶稱謂、姓氏＋稱謂、只剩名
    m = Mapper()
    text = "王小明先生與王小明之妻林美玲到場，陳大華亦在。陳先生表示小明不便。張志豪老師、王小姐、林小姐也來。"
    out = deidentify_text(text, nl, m)
    print("[姓名]", out)
    for e in m.to_list():
        print("   ", e["code"], e["value"], "x", e["count"])
    print("   認不到：只剩名的「小明」（刻意不猜）；「林小姐」因家屬林美玲是唯一林姓所以換；")
    # 還原
    mapping = {"對照": m.to_list()}
    back = restore_text(out, mapping)
    print("[還原一致]", "對" if back == text else "錯", )
    ok &= back == text
    # 代號超過 9 筆時的前綴問題
    m2 = Mapper(); nl2 = NameList()
    names = [f"測{i}人" for i in range(1, 13)]
    for n in names: nl2.names[n] = "個案"
    t2 = "、".join(names)
    o2 = deidentify_text(t2, nl2, m2)
    b2 = restore_text(o2, {"對照": m2.to_list()})
    print("[12 筆代號還原]", "對" if b2 == t2 else "錯", o2[:40])
    ok &= b2 == t2
    # 改壞偵測
    damaged = out.replace("〔個案01〕", "個案 01", 1)
    rep = check_codes(damaged, mapping)
    bad = [r for r in rep if r["damaged"]]
    print("[改壞偵測]", "對" if bad and bad[0]["code"] == "個案01" else "錯", bad)
    ok &= bool(bad)
    print("RESULT", "全對" if ok else "有錯")
    return ok

if __name__ == "__main__":
    sys.exit(0 if run() else 1)

"""視窗「當場取消／補遮」靠的兩個核心行為：
1. 補遮：names 裡放非姓名類（地址、電話、其他）也會換成該類代號，而且不會被當成姓氏去配稱謂。
2. 取消：把值放進 skip，就算正則認得到也不換。
全部用假資料。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from deid.core import NameList, Mapper, deidentify_text

TEXT = "個案王小明住潮州鎮光春路99號，電話0912-000-111，身分證A123456789，王先生表示另一處在內埔鄉老街12號。"


def run() -> bool:
    checks = []
    # 1. 補遮：地址與其他類放進 names
    nl = NameList(names={"王小明": "個案", "內埔鄉老街12號": "地址", "另一處": "其他"}, skip=[])
    m = Mapper()
    out = deidentify_text(TEXT, nl, m)
    codes = {e["kind"]: e["code"] for e in m.to_list()}
    checks.append(("補遮的地址換成〔地址NN〕", "內埔鄉老街12號" not in out and any(e["value"] == "內埔鄉老街12號" and e["kind"] == "地址" for e in m.to_list())))
    checks.append(("補遮的其他類換成〔其他01〕", "〔其他01〕" in out))
    checks.append(("非姓名類不配稱謂（沒有「內先生」這種別名）", "內" not in "".join(nl.surname_titles().keys())))
    checks.append(("王先生仍換成〔個案01姓〕", "〔個案01姓〕先生" in out))
    # 2. 取消：值放進 skip
    nl2 = NameList(names={"王小明": "個案"}, skip=["A123456789", "0912-000-111"])
    m2 = Mapper()
    out2 = deidentify_text(TEXT, nl2, m2)
    checks.append(("取消的身分證原樣留著", "A123456789" in out2))
    checks.append(("取消的電話原樣留著", "0912-000-111" in out2))
    checks.append(("沒取消的地址仍被換", "光春路99號" not in out2))
    checks.append(("取消姓名後連稱謂也不換", "王先生" in deidentify_text(TEXT, NameList(names={"王小明": "個案"}, skip=["王小明", "王"]), Mapper())))
    ok = True
    for name, passed in checks:
        print(("✓ " if passed else "✗ ") + name)
        ok = ok and passed
    print("RESULT", "全對" if ok else "有錯")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)

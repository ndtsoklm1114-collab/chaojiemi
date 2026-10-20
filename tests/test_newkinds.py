"""10/16 從鼎淵工具搬的三件：電子郵件、統一編號（要過檢核碼）、身分證檢核碼不符照遮但標警告。全部假資料。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from deid.core import NameList, Mapper, deidentify_text, is_valid_twid, is_valid_taxid

TEXT = ("承辦 zhang.test@example.com 廠商統編 04595257 另一組 12345678 身分證 A123456789 打錯的 A123456780 "
        "財編 4A1231030100002 案號 1150806001 市話 02-27123456 西元 20261016 居留證 A800000014")


def run() -> bool:
    checks = []
    checks.append(("統編檢核：04595257 過", is_valid_taxid("04595257")))
    checks.append(("統編檢核：12345678 不過", not is_valid_taxid("12345678")))
    checks.append(("身分證檢核：A123456789 過", is_valid_twid("A123456789")))
    checks.append(("身分證檢核：A123456780 不過", not is_valid_twid("A123456780")))
    checks.append(("新式居留證 A800000014 過", is_valid_twid("A800000014")))
    m = Mapper()
    out = deidentify_text(TEXT, NameList(names={}, skip=[]), m)
    ents = {e["value"]: e for e in m.to_list()}
    checks.append(("Email 換成〔電子郵件01〕", "〔電子郵件01〕" in out and "example.com" not in out))
    checks.append(("統編過檢核的換掉", "04595257" not in out and ents.get("04595257", {}).get("kind") == "統編"))
    checks.append(("八位數沒過檢核的不動", "12345678" in out))
    checks.append(("檢核碼不符的身分證照遮", "A123456780" not in out))
    checks.append(("檢核碼不符的掛警告、對的沒有", ents["A123456780"].get("警告") == "檢核碼不符" and "警告" not in ents["A123456789"]))
    checks.append(("財編、案號、西元日期沒被咬", "4A1231030100002" in out and "1150806001" in out and "20261016" in out))
    checks.append(("帶區碼的市話整個當電話，不拆成統編", "02-27123456" not in out and ents.get("02-27123456", {}).get("kind") == "電話" and "27123456" not in ents))
    ok = True
    for name, passed in checks:
        print(("✓ " if passed else "✗ ") + name)
        ok = ok and passed
    print("RESULT", "全對" if ok else "有錯")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)

"""從制式表格抓姓名候選：「姓名：」「聯絡人姓名：」這類欄位後面的 2 到 4 個中文字。
只產候選給人核對，不直接拿來換；非制式文件抓不到就抓不到，不猜。"""
from __future__ import annotations

import re
from pathlib import Path

from . import handlers as H

CJK = r"一-鿿"
# 標籤 -> 類別。長的標籤放前面，「聯絡人姓名」要先於「姓名」
LABELS = [
    ("主要照顧者姓名", "家屬"), ("主要照顧者", "家屬"), ("聯絡人姓名", "家屬"), ("緊急聯絡人", "家屬"), ("聯絡人", "家屬"),
    ("照顧者姓名", "家屬"), ("照顧者", "家屬"), ("家屬姓名", "家屬"), ("家屬", "家屬"), ("代理人", "家屬"),
    ("父親姓名", "家屬"), ("母親姓名", "家屬"), ("配偶姓名", "家屬"), ("子女姓名", "家屬"), ("父親", "家屬"), ("母親", "家屬"),
    ("承辦人員姓名", "第三人"), ("承辦人", "第三人"), ("評估人員姓名", "第三人"), ("評估人員", "第三人"), ("評估者", "第三人"),
    ("填表人", "第三人"), ("社工姓名", "第三人"), ("社工", "第三人"), ("治療師", "第三人"), ("訪視人員", "第三人"),
    ("個案姓名", "個案"), ("申請人姓名", "個案"), ("申請人", "個案"), ("反映人", "個案"), ("反應人", "個案"),
    ("案主", "個案"), ("受訪者", "個案"), ("使用者姓名", "個案"), ("姓名", "個案"),
]
KINDS = ("個案", "家屬", "第三人")
# 標籤後面常出現、但不是名字的詞
STOP = {"本人", "配偶", "同上", "無", "不詳", "男", "女", "性別", "年齡", "出生", "關係", "電話", "地址", "身分證",
        "先生", "小姐", "女士", "填寫", "簽名", "簽章", "備註", "日期", "手寫", "如上", "同左", "見附件", "略", "空白"}
# 候選裡出現這些字就不是名字（欄位空著時會抓到下一個標籤，像「聯絡電話」）
NOT_NAME = re.compile(r"電話|地址|日期|關係|字號|證號|姓名|年齡|性別|編號|號碼|聯絡|資料|生日|住址|郵遞|區號|分機|傳真|信箱")
_pat = re.compile(
    "(?P<label>" + "|".join(re.escape(l) for l, _ in LABELS) + r")\s*[：:]\s*(?P<name>[" + CJK + r"]{2,4})(?![" + CJK + "])"
)
_kind = dict(LABELS)


def candidates_from_text(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: [] for k in KINDS}
    # 標籤跟值被換行切開（文字層 PDF 常見）：只把「標籤：」後面的換行收掉，不把整頁空白去掉
    text = re.sub(r"([：:])\s*\n\s*", r"\1", text)
    for m in _pat.finditer(text):
        name = m.group("name")
        if name in STOP or NOT_NAME.search(name) or any(name.startswith(s) for s in ("本人", "配偶", "同上")):
            continue
        if any(name == l for l, _ in LABELS):
            continue
        kind = _kind[m.group("label")]
        if not any(name in out[k] for k in KINDS):
            out[kind].append(name)
    return out


def candidates_from_file(path: Path) -> dict[str, list[str]]:
    path = Path(path)
    ext = path.suffix.lower()
    text = ""
    if ext in H.TEXT_EXT:
        text = H.read_text_any(path)[0]
    elif ext == ".docx":
        text = H.docx_text(path)
    elif ext in (".xlsx", ".xlsm"):
        text = H.xlsx_text(path)
    elif ext == ".pdf":
        import pymupdf
        parts = []
        for pg in pymupdf.open(str(path)):
            t = pg.get_text()
            if t.strip():
                parts.append(t)
            else:  # 掃描頁走 OCR
                from . import ocr
                parts.append("\n".join(l.text for l in ocr.recognize(pg.get_pixmap(dpi=200).tobytes("png"))))
        text = "\n".join(parts)
    elif ext in H.IMAGE_EXT:
        from . import ocr
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.open(str(path)).convert("RGB").save(buf, format="PNG")
        text = "\n".join(l.text for l in ocr.recognize(buf.getvalue()))
    return candidates_from_text(text)

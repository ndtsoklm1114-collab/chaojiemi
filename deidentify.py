#!/usr/bin/env python
"""去識別化小工具：丟一個檔案，產同格式去識別版加對照表。只在本機跑，不連網、不用模型。

用法：
  deidentify.py <檔案> [--names 名單.txt] [--out 輸出資料夾] [--map 對照表資料夾]

預設：去識別版放在 ~/Desktop/潮解密/去識別版/（可用 ~/.config/chaojiemi/config.json 改），檔名同原檔（檔名裡的名單姓名也會換成代號）；
對照表與 OCR 全文放在 ~/去識別對照表/，刻意跟產出不同資料夾。螢幕上只印數量，不印真名。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deid.core import NameList, CodeBook, DEFAULT_RETENTION, RETENTION_LABEL  # noqa: E402
from deid.run import deidentify_file, DEFAULT_MAP_DIR, DEFAULT_OUT_DIR  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="要去識別的檔案：docx、xlsx、pdf、png/jpg、txt/md/csv")
    ap.add_argument("--names", help="姓名名單檔（區段 [個案] [家屬] [第三人] [不換]）")
    ap.add_argument("--out", help=f"輸出資料夾，預設 {DEFAULT_OUT_DIR}")
    ap.add_argument("--map", help=f"對照表資料夾，預設 {DEFAULT_MAP_DIR}")
    ap.add_argument("--retain", action="store_true", help=f"xlsx 清冊才有效：部分保留（{RETENTION_LABEL}），其他格式一律全遮")
    ap.add_argument("--book", help="代號簿 .json：有就載入接著編（同一個人用同一個代號），跑完寫回同一個檔；沒有這個檔就新建")
    a = ap.parse_args(argv)
    nl = NameList.load(a.names) if a.names else None
    if nl is None:
        print("⚠ 沒給 --names，姓名不會被換，只處理身分證、電話、生日、地址")
    book = None
    if a.book:
        book = CodeBook()
        if Path(a.book).exists():
            print(f"   代號簿：載入 {book.load(a.book)} 個代號接著編")
    try:
        r = deidentify_file(Path(a.file), nl, a.out, a.map, retention=DEFAULT_RETENTION if a.retain else None, book=book)
    except (FileNotFoundError, ValueError) as e:
        print(f"⛔ {e}"); return 2
    if book is not None:
        book.save(a.book); print(f"   代號簿：已寫回 {a.book}（{len(book.codes)} 個代號；這張也是個資）")
    if r.residue:
        print(f"⚠ 真值殘留 {len(r.residue)} 筆（{'、'.join('〔' + c + '〕' for c in r.residue)}）：去識別版的文字裡還找得到，先別交出去")
    if r.ambiguous_surnames:
        print(f"⚠ 名單裡有 {r.ambiguous_surnames} 個姓氏不只一人，這些姓的「某先生／某小姐」不會換；是哪些姓寫在對照表的備註")
    print(f"✅ 去識別版：{r.output}")
    print(f"   對照表　：{r.mapping}（{r.code_total} 個代號）")
    if r.ocr_text:
        print(f"   OCR 全文：{r.ocr_text}（請掃一眼有沒有漏遮的字）")
    for n in r.notes:
        print(f"   備註：{n}")
    print("   換掉：", "、".join(f"{k} {v} 處" for k, v in sorted(r.counts.items())) or "沒有任何東西被換")
    if r.missing_names:
        print(f"   ⚠ 名單上有 {r.missing_names} 個姓名沒出現在檔裡（OCR 認不到或根本不在檔裡），是誰寫在對照表的備註")
    return 0


if __name__ == "__main__":
    sys.exit(main())

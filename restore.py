#!/usr/bin/env python
"""還原：吃去識別版加對照表，把代號換回原文。純字串替換，不接 AI。

用法：
  restore.py <去識別版檔案> <對照表.json> [--out 輸出資料夾] [--check-only]

還原前先列每個代號在檔裡出現幾次、有沒有被改壞（括號只剩一邊、半形括號、中間多空格、全形數字）。
只還原完整的〔代號〕；圖片與掃描 PDF 頁不可還原，只回報。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deid.run import restore_file, DEFAULT_RESTORE_DIR  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="去識別版檔案")
    ap.add_argument("mapping", help="對照表 .json")
    ap.add_argument("--out", help=f"輸出資料夾，預設 {DEFAULT_RESTORE_DIR}")
    ap.add_argument("--check-only", action="store_true", help="只檢查代號狀況，不產還原檔")
    a = ap.parse_args(argv)
    try:
        r = restore_file(Path(a.file), Path(a.mapping), a.out, a.check_only)
    except ValueError as e:
        print(f"⛔ {e}"); return 2
    if r.refused:
        print("⛔ " + r.refused); return 3
    print("代號檢查（對照表記的次數 / 檔裡完整出現次數 / 疑似改壞）")
    for x in r.report:
        flag = ""
        if x["flag"] == "改壞":
            flag = "  ⚠ 改壞：" + "、".join(repr(d) for d in x["damaged"])
        elif x["flag"]:
            flag = "  ⚠ " + x["flag"]
        print(f"  {x['code']}: {x['expected']} / {x['exact']}{flag}")
    if a.check_only:
        return 1 if r.problems else 0
    if r.skipped_pages:
        print(f"⚠ 第 {r.skipped_pages} 頁是掃描遮黑頁，不可還原，請拿原檔")
    if r.missing:
        print(f"⚠ 這幾個代號的真值沒有寫回檔裡：{'、'.join(r.missing)}（請用對照表手動補）")
    print(f"{'⚠' if r.problems else '✅'} 還原版：{r.output}")
    if r.problems:
        print("   有代號被改壞或次數不符，上面標 ⚠ 的要自己對一下")
    return 0


if __name__ == "__main__":
    sys.exit(main())

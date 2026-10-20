#!/usr/bin/env python
"""一個指令跑完三支測試。改完 deid/ 或 app/ 之後跑：
    python tests/run_all.py   （用裝好套件的那個 Python）
每支只印最後一行（RESULT 全對／有錯），最後一行是總結；有任何一支失敗就以 1 結束。
測試本身不碰真實檔案，用的都是假資料。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = ["test_core.py", "test_xlsx_formula.py", "test_metadata.py", "test_overrides.py", "test_residue.py", "test_retention.py", "test_newkinds.py", "test_xlsx_chart.py", "test_shared_codes.py", "test_platform.py"]


def main() -> int:
    bad = []
    for name in TESTS:
        r = subprocess.run([sys.executable, str(HERE / name)], capture_output=True, text=True)
        last = (r.stdout.strip().splitlines() or [""])[-1]
        ok = r.returncode == 0
        print(f"{'✅' if ok else '❌'} {name}: {last or r.stderr.strip().splitlines()[-1:] or '沒有輸出'}")
        if not ok:
            bad.append(name)
    print(f"RESULT {'全對' if not bad else '有錯：' + '、'.join(bad)}（{len(TESTS) - len(bad)}/{len(TESTS)}）")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())

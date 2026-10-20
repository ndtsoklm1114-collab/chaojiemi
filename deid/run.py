"""CLI 與潮解密視窗共用的流程：一個檔案進去，去識別版、對照表、OCR 全文出來；還原同理。
螢幕上要印的字由呼叫端決定，這裡只回傳數量與路徑，真名只寫進對照表資料夾。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .core import NameList, NAME_KINDS, Mapper, CodeBook, deidentify_text, write_mapping, read_mapping, restore_text, check_codes, find_residue, RETENTION_LABEL
from . import handlers as H
from . import config

# 預設路徑可用 ~/.config/chaojiemi/config.json 蓋掉（見 deid/config.py）
DEFAULT_MAP_DIR = config.path("map_dir", "~/去識別對照表")
DEFAULT_OUT_DIR = config.path("out_dir", "~/Desktop/潮解密/去識別版")
DEFAULT_RESTORE_DIR = config.path("restore_dir", DEFAULT_OUT_DIR / "還原")
SUPPORTED = sorted(H.TEXT_EXT | H.IMAGE_EXT | {".docx", ".xlsx", ".xlsm", ".pdf"})


@dataclass
class DeidResult:
    source: Path
    output: Path
    mapping: Path
    ocr_text: Path | None
    reversible: bool
    counts: dict[str, int]          # 類別 -> 換了幾處
    code_total: int
    notes: list[str]                # 不含真名的備註
    ambiguous_surnames: int         # 同姓不只一人的姓氏數
    missing_names: int              # 名單上沒出現在檔裡的姓名數
    residue: list[str] = field(default_factory=list)  # 去識別版裡還找得到真值的代號（空的才算乾淨）


def deidentify_file(src: Path, nl: NameList | None, out_dir: Path | None = None, map_dir: Path | None = None,
                    retention: dict | None = None, book: CodeBook | None = None) -> DeidResult:
    """retention 只對 xlsx 生效（部分保留）；docx、PDF、圖片一律全遮。
    book 給了就是整批共用代號：同一個值在每個檔都拿同一個代號；對照表仍是一檔一張，次數與位置只算這個檔。"""
    src = Path(src).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(str(src))
    ext = src.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"不支援的格式：{ext}（支援 docx、xlsx、pdf、圖片、txt/md/csv）")
    out_dir = Path(out_dir).expanduser() if out_dir else DEFAULT_OUT_DIR
    map_dir = Path(map_dir).expanduser() if map_dir else DEFAULT_MAP_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    map_dir.mkdir(parents=True, exist_ok=True)

    mapper = Mapper(book)
    stem = _mapping_stem(map_dir, src)
    tail = stem[len(src.name):]  # 同名不同路徑的第二個檔會是 " (2)"，去識別版也加同樣的尾巴，共用輸出夾才不互蓋
    dst = out_dir / (deidentify_text(src.stem, nl, mapper, "檔名") + tail + src.suffix)
    ocr_out: list[str] = []
    reversible = True
    if ext in H.TEXT_EXT:
        notes = H.deid_text(src, dst, nl, mapper)
    elif ext == ".docx":
        notes = H.deid_docx(src, dst, nl, mapper)
    elif ext in (".xlsx", ".xlsm"):
        notes = H.deid_xlsx(src, dst, nl, mapper, retention)
        if retention:
            notes.append(f"部分保留開著：{RETENTION_LABEL}；留下來的字會出現在去識別版裡，交出去前想一下小鄉鎮認不認得出人")
    elif ext == ".pdf":
        notes = H.deid_pdf(src, dst, nl, mapper, ocr_out)
        reversible = not any("不可還原" in n for n in notes) or any("文字層" in n for n in notes)
    else:
        notes = H.deid_image(src, dst, nl, mapper, ocr_out)
        reversible = False

    entries = mapper.to_list()
    # 最後檢查：把去識別版的文字抽回來，對照表上每個真值找一遍。圖片沒文字層，抽不到就不驗（OCR 全文檔要人看）。
    residue = find_residue(extract_text(dst) or "", entries) if ext not in H.IMAGE_EXT else []
    if residue:
        notes.append(f"⚠ 真值殘留 {len(residue)} 筆：" + "、".join(f"〔{c}〕" for c in residue) + "（去識別版的文字裡還找得到，先別交出去）")
    warned = [e["code"] for e in entries if e.get("警告")]
    if warned:
        notes.append(f"⚠ 檢核碼不符 {len(warned)} 筆：" + "、".join(f"〔{c}〕" for c in warned) + "（照樣遮了；可能是原檔打錯字）")
    public_notes = list(notes)
    missing = [n for n, k in nl.names.items() if k in NAME_KINDS and not any(e.get("原值", e["value"]) == n for e in entries)] if nl else []
    amb = nl.ambiguous_surnames() if nl else {}
    # 這兩條含真名，只進對照表
    if amb:
        notes.append("同姓不只一人、稱謂寫法沒換的姓氏：" + "、".join(sorted(amb)))
    if missing:
        notes.append("名單上沒出現在檔裡的姓名：" + "、".join(missing))
    map_path = map_dir / (stem + ".對照表.json")
    write_mapping(map_path, src, dst, mapper, reversible, notes)
    ocr_path = None
    if ocr_out:
        ocr_path = map_dir / (stem + ".OCR全文.txt")
        ocr_path.write_text("\n\n".join(ocr_out), encoding="utf-8")
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["kind"]] = counts.get(e["kind"], 0) + e["count"]
    return DeidResult(src, dst, map_path, ocr_path, reversible, counts, len(entries), public_notes, len(amb), len(missing), residue)


def _mapping_stem(map_dir: Path, src: Path) -> str:
    """對照表檔名用原檔名；同名但不同路徑的檔已經有對照表時，加 (2)、(3)，不互相蓋掉。"""
    stem = src.name
    n = 1
    while True:
        cand = map_dir / (stem + ".對照表.json")
        if not cand.exists():
            return stem
        try:
            if read_mapping(cand).get("原檔") == str(src):
                return stem  # 同一個檔重跑，蓋掉舊的
        except Exception:
            pass
        n += 1
        stem = f"{src.name} ({n})"


@dataclass
class RestoreResult:
    output: Path | None
    report: list[dict]
    problems: int
    skipped_pages: list[int] = field(default_factory=list)
    refused: str | None = None      # 圖片這類不可還原的原因
    missing: list[str] = field(default_factory=list)  # 還原後在檔裡找不到真值的代號


def extract_text(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in H.TEXT_EXT:
        return H.read_text_any(path)[0]
    if ext == ".docx":
        return H.docx_text(path)
    if ext in (".xlsx", ".xlsm"):
        return H.xlsx_text(path)
    if ext == ".pdf":
        return H.pdf_text(path)
    return None


def restore_file(src: Path, mapping_path: Path, out_dir: Path | None = None, check_only: bool = False) -> RestoreResult:
    src = Path(src).expanduser().resolve()
    mapping = read_mapping(Path(mapping_path).expanduser())
    ext = src.suffix.lower()
    if ext in H.IMAGE_EXT:
        return RestoreResult(None, [], 0, refused=f"圖片是遮黑的，不可還原，請拿原檔：{mapping.get('原檔')}")
    text = extract_text(src)
    if text is None:
        raise ValueError(f"不支援的格式：{ext}")
    report = check_codes(text, mapping)
    problems = 0
    for r in report:
        r["flag"] = ""
        if r["damaged"]:
            r["flag"] = "改壞"; problems += 1
        elif r["exact"] != r["expected"]:
            r["flag"] = "次數不符"; problems += 1
    if check_only:
        return RestoreResult(None, report, problems)
    out_dir = Path(out_dir).expanduser() if out_dir else DEFAULT_RESTORE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / (restore_text(src.stem, mapping) + src.suffix)
    skipped: list[int] = []
    missing: list[str] = []
    if ext in H.TEXT_EXT:
        H.restore_plain(src, dst, mapping)
    elif ext == ".docx":
        H.restore_docx(src, dst, mapping)
    elif ext in (".xlsx", ".xlsm"):
        H.restore_xlsx(src, dst, mapping)
    else:
        skipped, missing = H.restore_pdf(src, dst, mapping)
    # 還原後再驗一次：每個真值真的在檔裡（文字檔、docx、xlsx 用抽字比對；PDF 在 restore_pdf 裡驗過）
    if ext != ".pdf":
        after = H.norm_cmp(extract_text(dst) or "")
        missing = [e["code"] for e in mapping["對照"] if H.norm_cmp(e["value"]) not in after]
    if missing:
        problems += len(missing)
    return RestoreResult(dst, report, problems, skipped, missing=missing)

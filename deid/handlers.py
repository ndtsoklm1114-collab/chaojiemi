"""各檔案格式的去識別與還原。每種格式兩個函式：deid_<fmt>(src, dst, namelist, mapper) 回傳備註清單；
restore_<fmt>(src, dst, mapping)。圖片類沒有還原。"""
from __future__ import annotations

import io
import re
from pathlib import Path

from .core import NameList, Mapper, find_spans, apply_spans, deidentify_text, restore_text, Span

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".html", ".xml", ".srt"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}  # heic 沒裝解碼器，不收


# ---------- 純文字 ----------

def read_text_any(path: Path) -> tuple[str, str]:
    """utf-8（含 BOM）讀不了就試 Big5（Excel 匯出的 CSV 常是這種）。回傳 (內容, 編碼)。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp950"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        # 原檔沒有 BOM 就用 utf-8 寫回，不然產出會多一個 BOM（10/16 共用代號測試抓到的）
        return text, ("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8") if enc == "utf-8-sig" else enc
    return raw.decode("utf-8", errors="replace"), "utf-8"


def deid_text(src: Path, dst: Path, nl: NameList | None, mapper: Mapper) -> list[str]:
    text, enc = read_text_any(src)
    lines = text.splitlines(keepends=True)
    out = [deidentify_text(l, nl, mapper, f"第{i}行") for i, l in enumerate(lines, 1)]
    dst.write_text("".join(out), encoding=enc)
    return [f"文字檔編碼 {enc}，產出用同一種"] if enc != "utf-8-sig" else []


def restore_plain(src: Path, dst: Path, mapping: dict):
    text, enc = read_text_any(src)
    dst.write_text(restore_text(text, mapping), encoding=enc)


# ---------- run 層換字（docx 共用） ----------

def _process_paragraph(par, nl, mapper, location, mode="deid", mapping=None):
    runs = par.runs
    if not runs:
        return
    texts = [r.text for r in runs]
    full = "".join(texts)
    if not full:
        return
    if mode == "deid":
        spans = find_spans(full, nl)
        if not spans:
            return
        new_full = apply_spans(full, spans, mapper, location)
    else:
        new_full = restore_text(full, mapping)
        if new_full == full:
            return
        spans = None
    if spans is None or len(runs) == 1:
        runs[0].text = new_full
        for r in runs[1:]:
            r.text = ""
        return
    # 多 run：若每個 span 都落在單一 run 內，逐 run 換；否則整段塞進第一個 run（格式會跟第一個 run）
    offsets, o = [], 0
    for t in texts:
        offsets.append((o, o + len(t))); o += len(t)
    def run_of(pos):
        for i, (a, b) in enumerate(offsets):
            if a <= pos < b:
                return i
        return None
    if all(run_of(s.start) == run_of(s.end - 1) for s in spans):
        for i, r in enumerate(runs):
            a, b = offsets[i]
            local = [Span(s.start - a, s.end - a, s.kind, s.value, s.alias_of) for s in spans if a <= s.start < b]
            if local:
                # 用同一個 mapper 再換一次會重複計次，所以這裡直接用 apply 的結果切片
                r.text = _slice_new(full, new_full, spans, a, b)
    else:
        runs[0].text = new_full
        for r in runs[1:]:
            r.text = ""


def _slice_new(full, new_full, spans, a, b):
    """原文 [a,b) 這一段在 new_full 裡對應的片段（span 不跨 run 時成立）。"""
    # 建原位置→新位置表
    old_to_new = {}
    oi = ni = 0
    for s in spans:
        while oi < s.start:
            old_to_new[oi] = ni; oi += 1; ni += 1
        close = new_full.find("〕", ni)
        rep_len = close + 1 - ni
        for k in range(s.start, s.end):
            old_to_new[k] = ni  # 整個 span 指到替換字開頭
        oi = s.end; ni += rep_len
    while oi < len(full):
        old_to_new[oi] = ni; oi += 1; ni += 1
    old_to_new[len(full)] = ni
    start_new = old_to_new[a]
    end_new = old_to_new[b]
    return new_full[start_new:end_new]


# ---------- docx ----------

DOCX_TEXT_PARTS = re.compile(r"^/word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$")


def _iter_paragraphs(doc):
    """掃 docx 套件裡所有有文字的 part：正文、頁首頁尾、註腳、章節附註、批註。文字方塊在正文裡，一起掃到。"""
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    for part in doc.part.package.iter_parts():
        if not DOCX_TEXT_PARTS.match(str(part.partname)) or not hasattr(part, "element"):
            continue
        for i, p in enumerate(part.element.iter(qn("w:p"))):
            yield f"{part.partname}#p{i}", Paragraph(p, part)


DOCX_META = ("author", "last_modified_by", "title", "subject", "comments", "keywords", "category")


def _strip_docx_meta(doc) -> int:
    n = 0
    for k in DOCX_META:
        if getattr(doc.core_properties, k):
            setattr(doc.core_properties, k, ""); n += 1
    return n


def deid_docx(src: Path, dst: Path, nl, mapper) -> list[str]:
    import docx
    doc = docx.Document(str(src))
    for loc, par in _iter_paragraphs(doc):
        _process_paragraph(par, nl, mapper, loc)
    meta = _strip_docx_meta(doc)
    doc.save(str(dst))
    return ["docx 以 run 層換字（正文、頁首頁尾、註腳、批註都掃）；一個名字被 Word 拆成兩個 run 時整段會併進第一個 run 的格式",
            f"docx 文件屬性清掉 {meta} 欄（作者、最後修改者、標題等）"]


def restore_docx(src: Path, dst: Path, mapping: dict):
    import docx
    doc = docx.Document(str(src))
    for loc, par in _iter_paragraphs(doc):
        _process_paragraph(par, None, None, loc, mode="restore", mapping=mapping)
    doc.save(str(dst))


def docx_text(path: Path) -> str:
    import docx
    doc = docx.Document(str(path))
    return "\n".join(p.text for _, p in _iter_paragraphs(doc))


_DOCX_PART_LABEL = (("header", "頁首"), ("footer", "頁尾"), ("footnotes", "註腳"), ("endnotes", "章節附註"), ("comments", "批註"))


def docx_blocks(path: Path) -> list[dict]:
    """預覽用：正文照順序分成段落與表格（表格保留列與格），頁首頁尾、註腳、批註另外列在後面。
    只讀文字，拿去識別版來讀才不會把真名送進畫面。"""
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    doc = docx.Document(str(path))
    blocks: list[dict] = []

    def heading_level(p) -> int:
        try:
            name = (p.style.name or "") if p.style is not None else ""
        except Exception:
            return 0
        if name == "Title":
            return 1
        m = re.match(r"Heading (\d)", name)
        return int(m.group(1)) if m else 0

    for el in doc.element.body.iterchildren():
        if el.tag == qn("w:p"):
            p = Paragraph(el, doc)
            blocks.append({"t": "p", "text": p.text, "h": heading_level(p)})
            for box in el.iter(qn("w:txbxContent")):  # 文字方塊的字不在 p.text 裡，另列
                for bp in box.iter(qn("w:p")):
                    t = Paragraph(bp, doc).text
                    if t.strip():
                        blocks.append({"t": "p", "text": t, "h": 0, "box": True})
        elif el.tag == qn("w:tbl"):
            rows = []
            for r in Table(el, doc).rows:
                seen: set[int] = set(); cells = []
                for c in r.cells:  # 合併儲存格會重複出現，同一個 tc 只留一格
                    if id(c._tc) in seen:
                        continue
                    seen.add(id(c._tc)); cells.append(c.text)
                rows.append(cells)
            blocks.append({"t": "table", "rows": rows})
    extra: dict[str, list[str]] = {}
    for loc, par in _iter_paragraphs(doc):
        if "document.xml" in loc or not par.text.strip():
            continue
        label = next((lab for key, lab in _DOCX_PART_LABEL if key in loc), "其他")
        extra.setdefault(label, []).append(par.text)
    for label, lines in extra.items():
        blocks.append({"t": "part", "label": label, "lines": lines})
    return blocks


# ---------- xlsx ----------

# 10/16 起直接改 xlsx 壓縮包裡的 XML（圖表、圖片不會掉），實作在 xlsx_xml.py；這裡留同名入口
from .xlsx_xml import deid_xlsx, restore_xlsx, xlsx_text  # noqa: E402,F401


# ---------- 圖片（OCR 遮黑） ----------

def _font(size: int):
    from PIL import ImageFont
    for f in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Supplemental/Songti.ttc",
              "C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/mingliu.ttc",  # Windows 微軟正黑體、細明體
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):  # Linux Noto
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            continue
    return ImageFont.load_default()


def redact_image_bytes(png: bytes, nl, mapper, location: str) -> tuple[bytes, str, int]:
    """OCR → 找個資 → 塗黑疊代號。回傳 (新圖 PNG, OCR 全文, 遮了幾塊)。"""
    from PIL import Image, ImageDraw
    from . import ocr
    lines = ocr.recognize(png)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img)
    full_text = []
    n = 0
    for li, line in enumerate(lines):
        full_text.append(line.text)
        spans = find_spans(line.text, nl)
        for s in spans:
            code = (mapper.code_for_alias(s.kind, s.alias_of, s.value, f"{location} 第{li+1}行")
                    if s.alias_of else mapper.code_for(s.kind, s.value, f"{location} 第{li+1}行"))
            x, y, w, h = ocr.substring_box(line, s.start, s.end)
            # Vision 原點左下，PIL 原點左上
            x0, x1 = int(x * W), int((x + w) * W)
            y0, y1 = int((1 - y - h) * H), int((1 - y) * H)
            pad = max(2, int(h * H * 0.15))
            box = (max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad))
            draw.rectangle(box, fill="black")
            fs = max(10, int((box[3] - box[1]) * 0.6))
            font = _font(fs)
            tw = draw.textlength(code, font=font)
            while tw > (box[2] - box[0]) and fs > 8:
                fs -= 1; font = _font(fs); tw = draw.textlength(code, font=font)
            draw.text((box[0] + 2, box[1] + (box[3] - box[1] - fs) / 2 - 1), code, fill="white", font=font)
            n += 1
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "\n".join(full_text), n


def deid_image(src: Path, dst: Path, nl, mapper, ocr_out: list[str]) -> list[str]:
    from PIL import Image
    img = Image.open(str(src))
    buf = io.BytesIO(); img.convert("RGB").save(buf, format="PNG")
    new_png, text, n = redact_image_bytes(buf.getvalue(), nl, mapper, src.name)
    ocr_out.append(f"=== {src.name} ===\n{text}")
    out_img = Image.open(io.BytesIO(new_png))
    if dst.suffix.lower() in (".jpg", ".jpeg"):
        out_img.save(str(dst), quality=92)
    else:
        out_img.save(str(dst))
    return [f"圖片走 OCR 遮黑，遮了 {n} 塊；不可還原；手寫字 OCR 認不到就會漏，請對 OCR 全文檔掃一眼"]


# ---------- pdf ----------

PDF_FONT = "china-t"  # 最後退路：pymupdf 內建中文字型
# 系統字型候選，依原檔字體家族挑：楷 → 全字庫楷書或 Word 的楷體；明/宋 → 宋體；其他 → 黑體
# 路徑照順序試，找不到就往下一個；最後退到 PDF_FONT。Windows 的在後面，移植時把標楷體 kaiu.ttf 那條確認存在就好。
FONT_CANDIDATES = {
    "kai": [str(Path.home() / "Library/Fonts/TW-Kai-98_1.ttf"), "/Applications/Microsoft Word.app/Contents/Resources/DFonts/Kaiti.ttf",
            "C:/Windows/Fonts/kaiu.ttf"],
    "ming": ["/System/Library/Fonts/Supplemental/Songti.ttc", "C:/Windows/Fonts/mingliu.ttc"],
    "hei": ["/System/Library/Fonts/STHeiti Medium.ttc", "C:/Windows/Fonts/msjh.ttc"],
    "times": ["/System/Library/Fonts/Supplemental/Times New Roman.ttf", "C:/Windows/Fonts/times.ttf"],
    "arial": ["/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf"],
}


def _family_of(fontname: str) -> str:
    n = fontname.lower()
    if "kai" in n or "標楷" in n:
        return "kai"
    if "ming" in n or "song" in n or "sung" in n:
        return "ming"
    if "times" in n:
        return "times"
    if "arial" in n or "helvetica" in n:
        return "arial"
    return "hei"


class _FontPicker:
    """同一份 PDF 共用：優先用原檔裡嵌的字型（字形齊全才用，長相一模一樣），
    不然依原檔字體家族挑系統字型，再不然用內建 china-t。"""

    def __init__(self, doc):
        import pymupdf as fitz
        self.doc = doc
        self.embedded = []  # (name, buffer, Font)
        seen = set()
        for pno in range(doc.page_count):
            for f in doc[pno].get_fonts(full=True):
                xref, ext, name = f[0], f[1], f[3]
                if xref in seen or ext not in ("ttf", "otf", "cff"):
                    continue
                seen.add(xref)
                try:
                    bn, _, _, buf = doc.extract_font(xref)
                    if buf:
                        self.embedded.append((name, buf, fitz.Font(fontbuffer=buf)))
                except Exception:
                    continue
        self.sys_cache: dict[str, tuple] = {}

    def pick(self, text: str, like: str | None) -> tuple[str, dict, object]:
        """回傳 (fontname, 參數, Font)。like 是同一列原字的字型名。
        先挑同家族的系統完整字型（文字層抽得回來、可搜尋），再退到原檔嵌的子集字型
        （長相一樣但子集常沒有對照表，抽字會變亂碼），最後用內建 china-t。"""
        import pymupdf as fitz
        fam = _family_of(like or "")
        order = FONT_CANDIDATES.get(fam, []) + [p for f in ("kai", "hei", "ming") for p in FONT_CANDIDATES[f] if f != fam]
        for path in order:
            if path in self.sys_cache:
                fname, font = self.sys_cache[path]
            elif Path(path).exists():
                try:
                    font = fitz.Font(fontfile=path); fname = "sys" + str(abs(hash(path)) % 10000)
                    self.sys_cache[path] = (fname, font)
                except Exception:
                    continue
            else:
                continue
            if all(font.has_glyph(ord(c)) for c in text if not c.isspace()):
                return fname, {"fontfile": path}, font
        ranked = sorted(self.embedded, key=lambda e: 0 if _family_of(e[0]) == fam else 1)
        for name, buf, font in ranked:
            if all(font.has_glyph(ord(c)) for c in text if not c.isspace()):
                return f"emb{abs(hash(name)) % 10000}", {"fontbuffer": buf}, font
        return PDF_FONT, {}, fitz.Font(PDF_FONT)


def _write_text(page, rect, text: str, size: float, picker: "_FontPicker", like: str | None = None) -> float:
    """自己把字寫上去，不靠 redaction 的文字參數（框太小時 pymupdf 會悄悄把字丟掉，值就不見了）。
    字級以原字為準，框高放不下就縮，寬度塞不下就一路縮到 4pt；再不夠就讓它超出框，寧可重疊也不要消失。"""
    fname, kw, font = picker.pick(text, like)
    # 字元框通常比字級高一點，框高只要有字級的 85% 就照原字級寫；真的太矮才縮
    if rect.height > 0 and rect.height < size * 0.85:
        size = rect.height * 0.95
    size = max(4.0, size)
    limit = max(rect.width, page.rect.x1 - 12 - rect.x0)
    while size > 4 and font.text_length(text, fontsize=size) > limit:
        size -= 0.5
    baseline = rect.y1 - max(0.0, (rect.height - size) / 2) - size * 0.18
    # TextWriter 吃 Font 物件，嵌進 PDF 的字型（fontbuffer）與系統字型檔都能用；insert_text 不收 fontbuffer
    import pymupdf as fitz
    tw = fitz.TextWriter(page.rect)
    tw.append((rect.x0, baseline), text, font=font, fontsize=size)
    tw.write_text(page)
    return size


def _is_scanned_page(page) -> bool:
    """影印機或 Adobe 做的「可搜尋 PDF」是整頁掃描影像加一層看不見的 OCR 文字。
    只換文字層的話影像上的個資照樣看得到，所以頁面被一張大圖蓋住超過一半就當掃描頁，整頁重做。"""
    area = abs(page.rect)
    if not area:
        return False
    for info in page.get_images(full=True):
        try:
            for r in page.get_image_rects(info[0]):
                if abs(r) / area > 0.5:
                    return True
        except Exception:
            continue
    return False


def _locate_value(page, value: str) -> list[tuple[list, float, str]]:
    """回傳每一次出現的 (框們, 字級, 字型名)。框用字元層級定位：pymupdf 的 rawdict 給每個字元自己的框與字級，
    把整頁字元（去掉空白）接成一串找值，碰到的字元框同一行併成一框。"""
    import pymupdf as fitz
    chars, boxes, sizes = [], [], []
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                for ch in sp.get("chars", []):
                    c = ch["c"]
                    if c.isspace():
                        continue
                    chars.append(c); boxes.append(fitz.Rect(ch["bbox"])); sizes.append((sp.get("size", 10), sp.get("font", "")))
    s = "".join(chars)
    groups, k = [], 0
    while True:
        k = s.find(value, k)
        if k < 0:
            break
        merged: list = []
        for r in boxes[k: k + len(value)]:
            if merged and abs(merged[-1].y0 - r.y0) < 2:
                merged[-1] = merged[-1] | r
            else:
                merged.append(fitz.Rect(r))
        mid = sorted(sizes[k: k + len(value)], key=lambda x: x[0])[len(value) // 2]
        groups.append((merged, mid[0], mid[1]))
        k += len(value)
    return groups


DASHES = re.compile(r"[\-\u2010\u2011\u2012\u2013\u2014\u2212\u00ad\uff0d]")


def norm_cmp(s: str) -> str:
    """比對用：去空白、各種連字號一律當 ASCII 減號（Times 寫進去的「-」抽回來會變 U+2010）。"""
    return DASHES.sub("-", re.sub(r"\s", "", s))


def _page_text_compact(page) -> str:
    return norm_cmp(page.get_text())


def deid_pdf(src: Path, dst: Path, nl, mapper, ocr_out: list[str]) -> list[str]:
    import pymupdf as fitz
    doc = fitz.open(str(src))
    picker = _FontPicker(doc)
    notes, scan_pages, n_text, n_img, lost = [], [], 0, 0, 0
    for pno, page in enumerate(doc, 1):
        text = page.get_text()
        if text.strip() and not _is_scanned_page(page):
            # 文字層：每個 block 去掉空白再認一次（地址常被換行切開），字元層級定位後遮白、再把代號寫回原位
            found: dict[tuple, None] = {}
            for b in page.get_text("blocks"):
                for s in find_spans(b[4], nl):
                    found.setdefault((s.kind, s.value, s.alias_of), None)
                for s in find_spans(re.sub(r"\s", "", b[4]), nl):
                    found.setdefault((s.kind, s.value, s.alias_of), None)
            # 同一個值從原文與去空白版各認到一次時可能一長一短（「029年01月01」與「029年01月01日」），只留長的
            vals = [k for k in found]
            found = {k: None for k in vals if not any(k is not o and k[0] == o[0] and k[2] == o[2] and k[1] != o[1] and k[1] in o[1] for o in vals)}
            pending = []  # (第一個框, 代號, 字級, 原字型)
            for (kind, value, alias_of) in found:
                if alias_of:
                    groups = []
                    for alias in [value + t for t in ("先生", "小姐", "女士", "太太")]:
                        for rects, size, fname in _locate_value(page, alias):
                            r = rects[0]
                            groups.append(([fitz.Rect(r.x0, r.y0, r.x0 + r.width / len(alias), r.y1)], size, fname))
                else:
                    groups = _locate_value(page, value)
                for rects, size, fname in groups:
                    code = (mapper.code_for_alias(kind, alias_of, value, f"第{pno}頁") if alias_of
                            else mapper.code_for(kind, value, f"第{pno}頁"))
                    mapper.attach(kind, value, alias_of, {"page": pno, "rect": [round(x, 2) for x in rects[0]], "size": round(size, 2),
                                                          "font": fname, "rects": [[round(x, 2) for x in r] for r in rects]})
                    for r in rects:
                        page.add_redact_annot(r, fill=(1, 1, 1))
                    pending.append((rects[0], code, size, fname))
                    n_text += 1
            if pending:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
                for r, code, size, fname in pending:
                    _write_text(page, r, code, size, picker, fname)
                # 驗證：代號真的寫進去了嗎
                after = _page_text_compact(page)
                for _, code, _, _ in pending:
                    if code not in after:
                        lost += 1
        else:
            pix = page.get_pixmap(dpi=200)
            new_png, ocr_text, n = redact_image_bytes(pix.tobytes("png"), nl, mapper, f"第{pno}頁")
            ocr_out.append(f"=== {src.name} 第{pno}頁 ===\n{ocr_text}")
            page.add_redact_annot(page.rect)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)
            page.insert_image(page.rect, stream=new_png)
            scan_pages.append(pno); n_img += n
    doc.set_metadata({})
    doc.save(str(dst), garbage=4, deflate=True)
    notes.append("PDF 文件屬性已清空")
    if n_text:
        notes.append(f"文字層頁面遮白後把代號寫回原位，共 {n_text} 處；原值的位置、字級、字型記在對照表，還原會放回去")
    if lost:
        notes.append(f"⚠ 有 {lost} 處代號沒有寫進文字層，請對照預覽檢查")
    if scan_pages:
        notes.append(f"掃描頁 {scan_pages} 走 OCR 遮黑共 {n_img} 塊，這些頁不可還原（含附隱藏文字層的可搜尋 PDF，文字層一併清掉）")
    return notes


def restore_pdf(src: Path, dst: Path, mapping: dict) -> tuple[list[int], list[str]]:
    """只還原文字層頁；回傳 (沒辦法還原的頁碼, 寫不回去的代號)。
    有記位置的（新版對照表）放回原位、用原字級；沒有的就寫在代號的位置。"""
    import pymupdf as fitz
    doc = fitz.open(str(src))
    picker = _FontPicker(doc)
    entries = sorted(mapping["對照"], key=lambda e: -len(e["code"]))
    skipped, missing = [], []
    for pno, page in enumerate(doc, 1):
        if not page.get_text().strip():
            skipped.append(pno); continue
        pending = []
        for e in entries:
            code, val = e["code"], e["value"]
            poss = [p for p in e.get("pdf_pos", []) if p.get("page") == pno]
            occ = _locate_value(page, "〔" + code + "〕")
            for i, (rects, size, fname) in enumerate(occ):
                for r in rects:
                    page.add_redact_annot(r, fill=(1, 1, 1))
                if i < len(poss):
                    pr = fitz.Rect(poss[i]["rect"]); psize = poss[i]["size"]; pfont = poss[i].get("font") or fname
                    # 原值可能跨好幾個框（換行的地址），其餘框也遮白免得殘留
                    for rr in poss[i].get("rects", [])[1:]:
                        page.add_redact_annot(fitz.Rect(rr), fill=(1, 1, 1))
                    pending.append((fitz.Rect(pr.x0, pr.y0, max(pr.x1, pr.x0 + 1), pr.y1), val, psize, code, pfont))
                else:
                    pending.append((rects[0], val, size, code, fname))
        if pending:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            for r, val, size, code, pfont in pending:
                _write_text(page, r, val, size, picker, pfont)
            after = _page_text_compact(page)
            for _, val, _, code, _ in pending:
                if norm_cmp(val) not in after:
                    missing.append(code)
    doc.save(str(dst), garbage=4, deflate=True)
    return skipped, missing


def pdf_text(path: Path) -> str:
    import pymupdf as fitz
    return "\n".join(p.get_text() for p in fitz.open(str(path)))

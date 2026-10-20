"""xlsx 直接改壓縮包裡的 XML（參考他人工具的做法）。
以前用 openpyxl 讀進來再存，圖表、圖片、條件格式會掉；現在只改有字的那幾個 XML，其他檔案一個位元都不動。

做法：
- 文字儲存格：共用字串（t="s"）被換掉的儲存格改寫成 inline string（t="inlineStr"），每格各自帶代號；
  不再被任何儲存格引用的共用字串清成空的（不然真名還留在 sharedStrings.xml 裡）。inline string 與公式的字串快取（t="str"）直接換。
- 標題含「出生」「生日」的欄：數值型日期格換成〔生日NN〕的 inline string，樣式保留；還原時換回數值日期。
- docProps/core.xml 的作者、最後修改者、標題等清空。
- 用 lxml，根節點宣告的命名空間會原樣保留（ElementTree 會丟掉沒用到的 xmlns，Excel 會說檔案損毀）。
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from .core import deidentify_text, restore_text

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
CORE_META = (  # docProps/core.xml 要清的欄
    "{http://purl.org/dc/elements/1.1/}creator", "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}lastModifiedBy",
    "{http://purl.org/dc/elements/1.1/}title", "{http://purl.org/dc/elements/1.1/}subject", "{http://purl.org/dc/elements/1.1/}description",
    "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}keywords",
)
BIRTH_HEADER = re.compile(r"出生|生日")
RE_CELL = re.compile(r"^([A-Z]+)(\d+)$")


def _q(tag: str) -> str:
    return "{%s}%s" % (NS, tag)


def _parse(data: bytes):
    return etree.fromstring(data, etree.XMLParser(remove_blank_text=False, huge_tree=True))


def _dump(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _text_of(el) -> str:
    """<si> 或 <is> 裡所有 <t> 接起來（富文字會拆成好幾個 <r><t>）。"""
    return "".join(t.text or "" for t in el.iter(_q("t")))


def _set_single_t(el, text: str) -> None:
    """把 <si>／<is> 的內容換成一個 <t>（富文字的分段格式會併成一段）。"""
    for child in list(el):
        el.remove(child)
    t = etree.SubElement(el, _q("t")); t.text = text
    if text != text.strip() or "\n" in text:
        t.set(XML_SPACE, "preserve")


class _Book:
    """讀 zip、找工作表、共用字串；改完寫回新 zip，沒碰的檔案原樣複製。"""

    def __init__(self, path: Path):
        self.zf = zipfile.ZipFile(str(path))
        self.names = [i.filename for i in self.zf.infolist()]
        self.changed: dict[str, bytes] = {}
        wb = _parse(self.zf.read("xl/workbook.xml"))
        rels = _parse(self.zf.read("xl/_rels/workbook.xml.rels"))
        targets = {}
        for r in rels.iter("{%s}Relationship" % NS_PKG):
            t = r.get("Target") or ""
            targets[r.get("Id")] = t.lstrip("/") if t.startswith("/") else "xl/" + t
        self.sheets: list[tuple[str, str]] = []  # (工作表名, zip 內路徑)
        for s in wb.iter(_q("sheet")):
            rid = s.get("{%s}id" % NS_R)
            if rid in targets:
                self.sheets.append((s.get("name") or "", targets[rid]))
        pr = wb.find(_q("workbookPr"))
        self.date1904 = pr is not None and pr.get("date1904") in ("1", "true")
        self.sst = _parse(self.zf.read("xl/sharedStrings.xml")) if "xl/sharedStrings.xml" in self.names else None
        self.si = list(self.sst.iter(_q("si"))) if self.sst is not None else []

    def shared(self, idx: int) -> str:
        return _text_of(self.si[idx]) if 0 <= idx < len(self.si) else ""

    def save(self, dst: Path) -> None:
        with zipfile.ZipFile(str(dst), "w", zipfile.ZIP_DEFLATED) as out:
            for info in self.zf.infolist():
                data = self.changed.get(info.filename, self.zf.read(info.filename))
                out.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        self.zf.close()


def _cell_text(book: _Book, c) -> str | None:
    """文字儲存格的內容：共用字串、inline string、公式的字串快取。不是文字就 None。"""
    t = c.get("t")
    if t == "s":
        v = c.find(_q("v"))
        return book.shared(int(v.text)) if v is not None and v.text else ""
    if t == "inlineStr":
        is_ = c.find(_q("is"))
        return _text_of(is_) if is_ is not None else ""
    if t == "str":
        v = c.find(_q("v"))
        return v.text or "" if v is not None else ""
    return None


def _write_cell_text(c, text: str) -> None:
    """把儲存格改成 inline string 放 text；樣式（s）留著。公式字串快取則只改 <v>。"""
    if c.get("t") == "str" and c.find(_q("f")) is not None:
        c.find(_q("v")).text = text
        return
    for child in list(c):
        if child.tag in (_q("v"), _q("is")):
            c.remove(child)
    c.set("t", "inlineStr")
    is_ = etree.SubElement(c, _q("is"))
    _set_single_t(is_, text)


def _serial_to_iso(v: float, date1904: bool) -> str | None:
    from openpyxl.utils.datetime import from_excel, CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900
    try:
        d = from_excel(v, CALENDAR_MAC_1904 if date1904 else CALENDAR_WINDOWS_1900)
    except Exception:
        return None
    return d.strftime("%Y-%m-%d") if d else None


def _iso_to_serial(s: str, date1904: bool) -> float:
    import datetime
    from openpyxl.utils.datetime import to_excel, CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900
    return to_excel(datetime.datetime.strptime(s, "%Y-%m-%d"), CALENDAR_MAC_1904 if date1904 else CALENDAR_WINDOWS_1900)


def deid_xlsx(src: Path, dst: Path, nl, mapper, retention: dict | None = None) -> list[str]:
    book = _Book(src)
    n_date = n_inline = 0
    referenced: set[int] = set()   # 改完後還有儲存格引用的共用字串
    touched: set[int] = set()      # 內容有被換掉（換成 inline）的共用字串
    for sheet_name, path in book.sheets:
        root = _parse(book.zf.read(path))
        birth_cols: set[str] = set()
        changed = False
        for row in root.iter(_q("row")):
            for c in row.iter(_q("c")):
                ref = c.get("r") or ""
                m = RE_CELL.match(ref)
                col, rnum = (m.group(1), int(m.group(2))) if m else ("", 0)
                loc = f"{sheet_name}!{ref}"
                text = _cell_text(book, c)
                if text is not None:
                    if rnum == 1 and BIRTH_HEADER.search(text):
                        birth_cols.add(col)
                    new = deidentify_text(text, nl, mapper, loc, retention)
                    if new != text:
                        if c.get("t") == "s":
                            touched.add(int(c.find(_q("v")).text))
                        _write_cell_text(c, new); changed = True; n_inline += 1
                    elif c.get("t") == "s":
                        referenced.add(int(c.find(_q("v")).text))
                    continue
                # 數值型日期在生日欄：整格換成代號（跟以前 openpyxl 版一樣）
                if col in birth_cols and c.get("t") in (None, "n") and c.find(_q("f")) is None:
                    v = c.find(_q("v"))
                    if v is not None and v.text:
                        try:
                            iso = _serial_to_iso(float(v.text), book.date1904)
                        except ValueError:
                            iso = None
                        if iso:
                            _write_cell_text(c, mapper.code_for("生日", iso, loc)); changed = True; n_date += 1
        if changed:
            book.changed[path] = _dump(root)
    # 共用字串：被換掉且沒人再引用的清空；沒被任何儲存格引用但含個資的也清空
    scrubbed = 0
    if book.sst is not None:
        for i, si in enumerate(book.si):
            if i in referenced:
                continue
            txt = _text_of(si)
            if i in touched or (txt and deidentify_text(txt, nl, mapper.__class__(), "") != txt):
                _set_single_t(si, ""); scrubbed += 1
        if scrubbed:
            book.changed["xl/sharedStrings.xml"] = _dump(book.sst)
    meta = 0
    if "docProps/core.xml" in book.names:
        core = _parse(book.zf.read("docProps/core.xml"))
        for tag in CORE_META:
            el = core.find(tag)
            if el is not None and (el.text or "").strip():
                el.text = ""; meta += 1
        if meta:
            book.changed["docProps/core.xml"] = _dump(core)
    book.save(dst)
    return [f"xlsx 直接改壓縮包裡的 XML：圖表、圖片、樣式、條件格式原樣保留；被換的 {n_inline} 格改成 inline string，清空 {scrubbed} 筆沒人用的共用字串（富文字格式會併成一段）",
            f"標題含「出生」「生日」的欄裡日期型儲存格換掉 {n_date} 格；活頁簿屬性清掉 {meta} 欄"]


def restore_xlsx(src: Path, dst: Path, mapping: dict) -> None:
    book = _Book(src)
    birth = {"〔" + e["code"] + "〕": e["value"] for e in mapping["對照"] if e["kind"] == "生日" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["value"])}
    for sheet_name, path in book.sheets:
        root = _parse(book.zf.read(path))
        changed = False
        for c in root.iter(_q("c")):
            text = _cell_text(book, c)
            if text is None:
                continue
            if text in birth and c.get("t") == "inlineStr":
                for child in list(c):
                    c.remove(child)
                if "t" in c.attrib:
                    del c.attrib["t"]
                v = etree.SubElement(c, _q("v")); v.text = repr(_iso_to_serial(birth[text], book.date1904)).rstrip("0").rstrip(".") or "0"
                changed = True
                continue
            new = restore_text(text, mapping)
            if new != text:
                _write_cell_text(c, new); changed = True
        if changed:
            book.changed[path] = _dump(root)
    if book.sst is not None:
        dirty = False
        for si in book.si:
            txt = _text_of(si)
            new = restore_text(txt, mapping)
            if new != txt:
                _set_single_t(si, new); dirty = True
        if dirty:
            book.changed["xl/sharedStrings.xml"] = _dump(book.sst)
    book.save(dst)


def xlsx_text(path: Path) -> str:
    """每個文字儲存格一行（含公式的字串快取），給殘留檢查與還原前的代號核對用。"""
    book = _Book(path)
    lines = []
    for _, p in book.sheets:
        root = _parse(book.zf.read(p))
        for c in root.iter(_q("c")):
            t = _cell_text(book, c)
            if t:
                lines.append(t)
    book.zf.close()
    return "\n".join(lines)

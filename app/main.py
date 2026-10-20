#!/usr/bin/env python
"""潮解密：去識別化小工具的視窗版。pywebview 把 ui.html 包成原生視窗，Python 核心同一個程式跑。
不開 HTTP 伺服器（html 用字串塞進去，不走 file:// 也不走埠口）、不連網；畫面上不顯示真名。"""
from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
# PyInstaller 打包後資源在 sys._MEIPASS 底下
UI_HTML = (Path(getattr(sys, "_MEIPASS", HERE.parent)) / "app" / "ui.html") if getattr(sys, "frozen", False) else HERE / "ui.html"

import webview  # noqa: E402
from deid.core import NameList, CodeBook, read_mapping, DEFAULT_RETENTION, RETENTION_LABEL, NAME_KINDS  # noqa: E402
from deid.run import deidentify_file, restore_file, DEFAULT_MAP_DIR, SUPPORTED  # noqa: E402
from deid import config  # noqa: E402
from deid import handlers as H  # noqa: E402
from deid.candidates import candidates_from_file  # noqa: E402

SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "潮解密"
SKIP_FILE = SUPPORT_DIR / "常用不換.txt"
DEFAULT_SKIP = "".join(s + "\n" for s in config.CFG.get("skip", []))  # 常用不換的預設值，從設定檔 skip 來，沒有就空


def _js(window, fn: str, payload) -> None:
    window.evaluate_js(f"window.{fn}({json.dumps(payload, ensure_ascii=False)})")


class Api:
    def __init__(self):
        self.window = None
        self.files: dict[str, dict] = {}
        self.order: list[str] = []
        self.namelist: NameList | None = None
        self.namelist_text = ""
        self.lock = threading.Lock()
        # 整批共用代號：開著時所有檔共用這本代號簿（可從上次的合併代號簿載入接著編）；關著時每檔各自從 01 編
        self.shared = False
        self.book: CodeBook | None = None
        self.book_src: str | None = None
        self.book_imported = 0
        self.book_path: str | None = None

    # ---------- 檔案 ----------
    def _add(self, paths) -> list[dict]:
        added = []
        for p in paths or []:
            path = Path(p)
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            if any(f["path"] == str(path) for f in self.files.values()):
                continue
            fid = uuid.uuid4().hex[:8]
            # excluded：畫面上按「取消」的值 {kind|value: {kind, label}}；extra：圈選補遮的值 [{id, text, kind}]。真值只留在 Python 這端。
            entry = {"id": fid, "name": path.name, "path": str(path), "ext": path.suffix.lower(), "status": "待處理", "excluded": {}, "extra": [],
                     "retain": False}  # retain：xlsx 清冊的部分保留開關，其他格式永遠全遮
            self.files[fid] = entry
            self.order.append(fid)
            added.append(self._public(entry))
        return added

    def _public(self, e: dict) -> dict:
        r = e.get("result")
        out = {k: e[k] for k in ("id", "name", "ext", "status")}
        if e.get("seconds") is not None:
            out["seconds"] = e["seconds"]
        if r:
            out.update({"counts": r.counts, "code_total": r.code_total, "reversible": r.reversible,
                        "output": str(r.output), "mapping": str(r.mapping), "ocr": str(r.ocr_text) if r.ocr_text else None,
                        "notes": r.notes, "ambiguous": r.ambiguous_surnames, "missing": r.missing_names, "residue": r.residue})
        if e.get("error"):
            out["error"] = e["error"]
        out["overrides"] = {"excluded": len(e.get("excluded", {})), "extra": len(e.get("extra", []))}
        out["can_retain"] = e["ext"] in (".xlsx", ".xlsm")
        out["retain"] = bool(e.get("retain")) and out["can_retain"]
        out["retain_label"] = RETENTION_LABEL
        return out

    def set_retain(self, fid: str, on: bool) -> dict:
        """xlsx 清冊的部分保留開關；要重跑才生效。"""
        e = self.files.get(fid)
        if not e:
            return {"error": "找不到這個檔"}
        if e["ext"] not in (".xlsx", ".xlsm"):
            return {"error": "部分保留只給 xlsx 清冊用，報告書一律全遮"}
        e["retain"] = bool(on)
        return {"ok": True, "retain": e["retain"]}

    def _effective_namelist(self, e: dict) -> NameList | None:
        """全域名單＋這個檔的補遮（進 names）與取消（進 skip）。"""
        names = dict(self.namelist.names) if self.namelist else {}
        skip = list(self.namelist.skip) if self.namelist else []
        for x in e.get("extra", []):
            names[x["text"]] = x["kind"]
        for v in e.get("excluded", {}).values():
            skip.append(v["value"])
            if v["kind"] in NAME_KINDS:
                names.pop(v["value"], None)  # 取消的姓名連「姓氏＋稱謂」的衍生代號（〔個案01姓〕）也一起不換
        if not names and not skip:
            return None
        return NameList(names=names, skip=skip)

    def add_paths(self, paths: list[str]) -> list[dict]:
        return self._add(paths)

    def pick_files(self) -> list[dict]:
        types = ("文件 (*.docx;*.xlsx;*.xlsm;*.pdf;*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.bmp;*.webp;*.txt;*.md;*.csv)",)
        paths = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=types)
        return self._add(paths)

    def remove_file(self, fid: str) -> None:
        self.files.pop(fid, None)
        if fid in self.order:
            self.order.remove(fid)

    def clear_files(self) -> None:
        self.files.clear(); self.order.clear()

    def list_files(self) -> list[dict]:
        return [self._public(self.files[i]) for i in self.order]

    def name_candidates(self, fids: list[str]) -> dict:
        """從制式表格的「姓名：」「聯絡人姓名：」抓候選，填進名單框給人核對。抓不到就空的。"""
        out = {"個案": [], "家屬": [], "第三人": [], "errors": 0}
        for fid in fids:
            e = self.files.get(fid)
            if not e:
                continue
            try:
                c = candidates_from_file(Path(e["path"]))
            except Exception:
                out["errors"] += 1; continue
            for k in ("個案", "家屬", "第三人"):
                for n in c.get(k, []):
                    if not any(n in out[j] for j in ("個案", "家屬", "第三人")):
                        out[k].append(n)
        return out

    # ---------- 名單 ----------
    def set_namelist(self, text: str) -> dict:
        self.namelist_text = text or ""
        if not self.namelist_text.strip():
            self.namelist = None
            return {"names": 0, "skip": 0, "ambiguous": 0}
        tmp = SUPPORT_DIR / ".namelist.tmp"
        SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(self.namelist_text, encoding="utf-8")
        try:
            self.namelist = NameList.load(tmp)
        except ValueError as e:
            self.namelist = None
            return {"error": str(e)}
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        return {"names": len(self.namelist.names), "skip": len(self.namelist.skip), "ambiguous": len(self.namelist.ambiguous_surnames())}

    def load_namelist_file(self) -> str | None:
        paths = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=("名單 (*.txt)",))
        if not paths:
            return None
        return Path(paths[0]).read_text(encoding="utf-8")

    def get_skip_defaults(self) -> str:
        if SKIP_FILE.is_file():
            return SKIP_FILE.read_text(encoding="utf-8")
        return DEFAULT_SKIP

    def save_skip_defaults(self, text: str) -> str:
        SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
        SKIP_FILE.write_text(text or "", encoding="utf-8")
        return str(SKIP_FILE)

    # ---------- 執行 ----------
    def run_all(self) -> dict:
        import time
        with self.lock:
            todo = [fid for fid in self.order if self.files.get(fid)]  # 每次都重跑全部，名單改了才會反映到已完成的檔
            total, done, ok = len(todo), 0, 0
            t0 = time.time()
            _js(self.window, "onProgress", {"state": "running", "done": 0, "total": total, "current": None, "elapsed": 0})
            for fid in todo:
                e = self.files.get(fid)
                if not e:
                    continue
                e["status"] = "處理中"; e.pop("error", None)
                _js(self.window, "onFileUpdate", self._public(e))
                _js(self.window, "onProgress", {"state": "running", "done": done, "total": total, "current": e["name"], "elapsed": round(time.time() - t0, 1)})
                t1 = time.time()
                try:
                    retention = DEFAULT_RETENTION if e.get("retain") and e["ext"] in (".xlsx", ".xlsm") else None
                    e["result"] = deidentify_file(Path(e["path"]), self._effective_namelist(e), retention=retention,
                                                  book=self.book if self.shared else None)
                    e["status"] = "完成"; ok += 1
                except Exception as ex:  # 單一檔案失敗不中斷整批
                    e["status"] = "失敗"; e["error"] = str(ex)
                e["seconds"] = round(time.time() - t1, 1)
                done += 1
                _js(self.window, "onFileUpdate", self._public(e))
                _js(self.window, "onProgress", {"state": "running", "done": done, "total": total, "current": None, "elapsed": round(time.time() - t0, 1)})
            elapsed = round(time.time() - t0, 1)
            _js(self.window, "onProgress", {"state": "done", "done": done, "total": total, "ok": ok, "elapsed": elapsed})
            if self.shared and self.book is not None and self.book.codes:
                # 合併代號簿：這批共用的全部代號，下個月匯入接著編。跟對照表放同一夾，本身就是個資。
                DEFAULT_MAP_DIR.mkdir(parents=True, exist_ok=True)
                p = DEFAULT_MAP_DIR / f"合併代號簿_{time.strftime('%Y%m%d_%H%M')}.json"
                self.book.save(p); self.book_path = str(p)
            return {"done": done, "ok": ok, "elapsed": elapsed, "map_dir": str(DEFAULT_MAP_DIR), "book_path": self.book_path, "book": self.book_info()}

    # ---------- 整批共用代號 ----------
    def book_info(self) -> dict:
        short = lambda p: p.replace(str(Path.home()), "～") if p else None
        return {"shared": self.shared, "codes": len(self.book.codes) if self.book else 0, "imported": self.book_imported,
                "src": short(self.book_src), "book_path": short(self.book_path)}

    def set_shared(self, on: bool) -> dict:
        self.shared = bool(on)
        if self.shared and self.book is None:
            self.book = CodeBook()
        if not self.shared:
            self.book = None; self.book_src = None; self.book_imported = 0; self.book_path = None
        return self.book_info()

    def import_book(self) -> dict:
        """選上次的合併代號簿（或任何一張對照表）載入，同一個值沿用舊代號、新值接著編；載入後自動開共用。"""
        paths = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=("代號簿或對照表 (*.json)",))
        if not paths:
            return self.book_info()
        p = Path(paths[0])
        try:
            book = self.book or CodeBook()
            n = book.load(p)
        except Exception as ex:
            return {**self.book_info(), "error": f"這個檔讀不出代號：{ex}"}
        self.book, self.shared, self.book_src, self.book_imported = book, True, str(p), n
        return self.book_info()

    # ---------- 右欄 ----------
    def preview(self, fid: str, page: int = 1) -> dict:
        e = self.files.get(fid)
        if not e or not e.get("result"):
            return {"type": "none"}
        out = Path(e["result"].output)
        ext = out.suffix.lower()
        if ext in H.IMAGE_EXT:
            return {"type": "image", "src": _img_data_url(out.read_bytes(), out.suffix), "pages": 1, "page": 1}
        if ext == ".pdf":
            import pymupdf
            d = pymupdf.open(str(out))
            n = d.page_count
            page = max(1, min(page, n))
            pg = d[page - 1]
            if pg.get_text().strip():
                return {"type": "text", "lines": _pdf_lines_by_layout(pg), "pages": n, "page": page, "label": f"第 {page} 頁（文字層）"}
            png = pg.get_pixmap(dpi=110).tobytes("png")
            return {"type": "image", "src": "data:image/png;base64," + base64.b64encode(png).decode(), "pages": n, "page": page, "label": f"第 {page} 頁（掃描遮黑）"}
        if ext == ".docx":
            return {"type": "blocks", "blocks": H.docx_blocks(out), "pages": 1, "page": 1}
        if ext in (".xlsx", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(str(out), read_only=True)
            blocks = []
            for ws in wb.worksheets:
                rows, more = [], False
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= 400:
                        more = True; break
                    rows.append(["" if v is None else str(v) for v in row])
                blocks.append({"t": "table", "label": f"工作表 {ws.title}" + ("（只顯示前 400 列）" if more else ""), "rows": rows, "grid": True})
            return {"type": "blocks", "blocks": blocks, "pages": 1, "page": 1}
        return {"type": "text", "lines": out.read_text(encoding="utf-8").splitlines(), "pages": 1, "page": 1}

    def changes(self, fid: str, reveal: bool = False) -> list[dict]:
        e = self.files.get(fid)
        if not e or not e.get("result"):
            return []
        m = read_mapping(Path(e["result"].mapping))
        rows = []
        for x in m["對照"]:
            row = {"code": x["code"], "kind": x["kind"], "count": x["count"], "locations": x["locations"][:6]}
            if x.get("保留"):
                row["kept"] = x["保留"]  # 部分保留留在外面的字，去識別版上本來就看得到
            if x.get("警告"):
                row["warn"] = x["警告"]
            if reveal:
                row["value"] = x["value"]
            rows.append(row)
        return rows

    def mapping_notes(self, fid: str, reveal: bool = False) -> list[str]:
        e = self.files.get(fid)
        if not e or not e.get("result"):
            return []
        notes = read_mapping(Path(e["result"].mapping)).get("備註", [])
        if reveal:
            return notes
        # 這兩條含真名，沒按「顯示真值」就連網頁那層都不給
        return [re.sub(r"：.*$", "：（含真名，按「顯示真值」才看）", n) if n.startswith(("同姓不只一人", "名單上沒出現")) else n for n in notes]

    # ---------- 當場取消／補遮（真值不回傳給網頁）----------
    MANUAL_KINDS = ("身分證", "電話", "生日", "地址", "電子郵件", "統編", "其他")

    def overrides(self, fid: str) -> dict:
        e = self.files.get(fid)
        if not e:
            return {"excluded": [], "extra": []}
        return {"excluded": [{"key": k, "kind": v["kind"], "label": v["label"]} for k, v in e["excluded"].items()],
                "extra": [{"id": x["id"], "kind": x["kind"], "chars": len(x["text"])} for x in e["extra"]]}

    def code_value(self, fid: str, code: str) -> dict:
        """點代號時浮框要看的真值（主任 10/16 定：點了就看，確定要留原文再按取消）。只回這一個代號的值。"""
        e = self.files.get(fid)
        if not e or not e.get("result"):
            return {}
        for x in read_mapping(Path(e["result"].mapping))["對照"]:
            if x["code"] == code:
                return {"value": x.get("原值", x["value"]), "count": x["count"]}
        return {}

    def exclude_code(self, fid: str, code: str) -> dict:
        """按了某個代號的「取消」：查對照表拿到真值，記進這個檔的不換清單。要重跑才生效。"""
        e = self.files.get(fid)
        if not e or not e.get("result"):
            return {"error": "這個檔還沒去識別"}
        n = 0
        for x in read_mapping(Path(e["result"].mapping))["對照"]:
            if x["code"] == code:
                v = x.get("原值", x["value"])  # 部分保留時 value 只是被遮的那段，不換要用完整的值
                e["excluded"][f'{x["kind"]}|{v}'] = {"kind": x["kind"], "value": v, "label": x["code"]}; n += 1
        return {"ok": n > 0}

    def exclude_kind(self, fid: str, kind: str) -> dict:
        """整類取消：這一類目前所有的值都記進不換。"""
        e = self.files.get(fid)
        if not e or not e.get("result"):
            return {"error": "這個檔還沒去識別"}
        n = 0
        for x in read_mapping(Path(e["result"].mapping))["對照"]:
            if x["kind"] == kind:
                v = x.get("原值", x["value"])
                e["excluded"][f'{x["kind"]}|{v}'] = {"kind": x["kind"], "value": v, "label": x["code"]}; n += 1
        return {"ok": True, "n": n}

    def include_back(self, fid: str, key: str) -> dict:
        e = self.files.get(fid)
        if e:
            e["excluded"].pop(key, None)
        return {"ok": True}

    def add_manual(self, fid: str, text: str, kind: str) -> dict:
        """圈選的字補遮成某一類（姓名三類由網頁直接加進名單，不走這裡）。"""
        e = self.files.get(fid)
        text = (text or "").strip()
        if not e:
            return {"error": "找不到這個檔"}
        if kind not in self.MANUAL_KINDS:
            return {"error": f"類別只能是 {'、'.join(self.MANUAL_KINDS)}"}
        if len(text) < 2 or "〔" in text or "〕" in text:
            return {"error": "請圈完整的字，不要含代號"}
        if any(x["text"] == text for x in e["extra"]):
            return {"ok": True, "dup": True}
        e["extra"].append({"id": uuid.uuid4().hex[:6], "text": text, "kind": kind})
        return {"ok": True}

    def remove_manual(self, fid: str, xid: str) -> dict:
        e = self.files.get(fid)
        if e:
            e["extra"] = [x for x in e["extra"] if x["id"] != xid]
        return {"ok": True}

    def ocr_text(self, fid: str) -> str | None:
        e = self.files.get(fid)
        if not e or not e.get("result") or not e["result"].ocr_text:
            return None
        return Path(e["result"].ocr_text).read_text(encoding="utf-8")

    def restore(self, fid: str) -> dict:
        e = self.files.get(fid)
        if not e or not e.get("result"):
            return {"error": "這個檔還沒去識別"}
        r = restore_file(Path(e["result"].output), Path(e["result"].mapping))
        return {"output": str(r.output) if r.output else None, "report": r.report, "problems": r.problems,
                "skipped_pages": r.skipped_pages, "refused": r.refused, "missing": r.missing}

    # ---------- 系統 ----------
    def open_folder(self, path: str) -> None:
        _open_in_file_manager(path)

    def reveal(self, path: str) -> None:
        _open_in_file_manager(path, select=True)

    def map_dir(self) -> str:
        return str(DEFAULT_MAP_DIR)


def _pdf_lines_by_layout(page) -> list[str]:
    """文字層 PDF 的預覽照版面位置排：同一列（y 相近）的字由左到右接起來。
    直接用 get_text() 的話，redaction 塞進去的代號會被排到最後面一串，看起來像沒換到。"""
    words = page.get_text("words")
    rows: list[list] = []
    for w in sorted(words, key=lambda w: (round(w[1]), w[0])):
        if rows and abs(rows[-1][0] - w[1]) < 3:
            rows[-1][1].append(w)
        else:
            rows.append([w[1], [w]])
    lines = []
    for _, ws in rows:
        ws.sort(key=lambda w: w[0])
        buf, last_x1 = "", None
        for w in ws:
            if last_x1 is not None and w[0] - last_x1 > 6:
                buf += "　" if w[0] - last_x1 > 30 else " "
            buf += w[4]; last_x1 = w[2]
        lines.append(buf)
    return lines


def _img_data_url(data: bytes, suffix: str) -> str:
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.width > 1400:
        im = im.resize((1400, int(im.height * 1400 / im.width)))
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    api = Api()
    html = UI_HTML.read_text(encoding="utf-8")
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    window = webview.create_window("潮解密", html=html, js_api=api, width=1180, height=760, min_size=(900, 600),
                                   background_color="#ffffff", text_select=True)  # 要能在預覽上圈字補遮；pywebview 預設禁止選字
    api.window = window

    def on_loaded():
        # 拖檔：pywebview 在 Python 這端才拿得到完整路徑（event.dataTransfer.files[].pywebviewFullPath）
        def on_drop(event):
            files = event.get("dataTransfer", {}).get("files", [])
            paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
            _js(window, "onDropSeen", {"files": len(files), "paths": len(paths)})
            if files and not paths:
                _js(window, "onToast", "拖進來了，但系統沒給檔案路徑；請改按「選檔案」，並告訴我這個情況")
                return
            added = api._add(paths)
            if files and not added:
                _js(window, "onToast", "拖進來的檔案格式不支援或已在清單裡（支援 docx、xlsx、pdf、png、jpg、txt、md、csv）")
                return
            if added:
                _js(window, "onFilesAdded", added)
        try:
            window.dom.get_element("#drop").events.drop += on_drop
            window.dom.get_element("body").events.drop += on_drop
        except Exception as ex:
            _js(window, "onToast", f"拖檔功能沒接上：{ex}，請用「選檔案」")

    window.events.loaded += on_loaded
    window.events.loaded += _set_dock_icon
    webview.start(debug="--debug" in sys.argv)


def _open_in_file_manager(path: str, select: bool = False) -> None:
    """Finder／檔案總管開資料夾或選到那個檔。Mac 以外的平台沒實測過。"""
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", path] if select else ["open", path])
    elif sys.platform == "win32":
        subprocess.run(["explorer", "/select,", path] if select else ["explorer", path])
    else:
        subprocess.run(["xdg-open", str(Path(path).parent) if select else path])


def _set_dock_icon():
    """啟動器版不是 PyInstaller 打包，Dock 會掛 Python 的圖示；自己換成潮解密的。打包版本來就有，跳過；只有 macOS 有 Dock。"""
    if getattr(sys, "frozen", False) or sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication, NSImage
        img = NSImage.alloc().initWithContentsOfFile_(str(HERE / "icon" / "潮解密.icns"))
        if img:
            NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:
        pass


if __name__ == "__main__":
    main()

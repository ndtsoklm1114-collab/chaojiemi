"""文字層的核心：認個資、配代號、換字、對照表讀寫。

設計依據（計劃書 docs/plans/2026-10-03_去識別化小工具.md）：
- 誤判代價比漏判高：身分證、電話前後不可再接英數字，財編 4A1231030100002 這類不能被咬到。
- 姓名只換名單上的，不猜。
- 代號補零、還原由長到短換（晨報 build_masker 踩過「人員1 咬掉 人員12」）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

# ---------- 名單 ----------

NAME_KINDS = ("個案", "家屬", "第三人")
SKIP_SECTION = "不換"
# 姓氏＋稱謂：名單上只有一個人姓這個姓時，「陳小姐」也換成同一個代號；兩個以上同姓就不換並回報
TITLES = ("先生", "小姐", "女士", "太太", "先生", "阿公", "阿嬤", "阿姨", "伯伯", "爺爺", "奶奶", "老師", "媽媽", "爸爸")


@dataclass
class NameList:
    names: dict[str, str] = field(default_factory=dict)  # 真名 -> 類別
    skip: list[str] = field(default_factory=list)        # 不換的字串（機構名、機構地址）

    @classmethod
    def load(cls, path: str | Path) -> "NameList":
        nl = cls()
        section = None
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = re.fullmatch(r"\[(.+)\]", line)
            if m:
                section = m.group(1).strip()
                if section not in NAME_KINDS and section != SKIP_SECTION:
                    raise ValueError(f"名單區段只能是 {NAME_KINDS} 或 [{SKIP_SECTION}]，看到 [{section}]")
                continue
            if section is None:
                raise ValueError(f"名單第一行要是區段標題，例如 [個案]，看到：{line}")
            if section == SKIP_SECTION:
                nl.skip.append(line)
            else:
                nl.names[line] = section
        return nl

    def surname_titles(self) -> dict[str, tuple[str, str]]:
        """回傳 {「陳小姐」: (真名, 類別)}，只收名單裡該姓氏唯一的人。"""
        by_surname: dict[str, list[str]] = {}
        for n, kind in self.names.items():
            if kind in NAME_KINDS:  # 視窗補遮的地址、電話也放在 names 裡，那些不算姓
                by_surname.setdefault(n[0], []).append(n)
        out = {}
        for s, lst in by_surname.items():
            if len(lst) != 1:
                continue
            for t in set(TITLES):
                out[s + t] = (lst[0], self.names[lst[0]])
        return out

    def ambiguous_surnames(self) -> dict[str, list[str]]:
        by_surname: dict[str, list[str]] = {}
        for n, kind in self.names.items():
            if kind in NAME_KINDS:
                by_surname.setdefault(n[0], []).append(n)
        return {s: l for s, l in by_surname.items() if len(l) > 1}


# ---------- 格式個資 ----------

# 身分證：一個英文字母 + 1/2（本國）或 8/9（新式居留證）+ 八位數字，前後不接英數字
RE_ID = re.compile(r"(?<![0-9A-Za-z])[A-Za-z][1289]\d{8}(?![0-9A-Za-z])")
# 手機：09 開頭十碼，允許連字號或空格分段
DASH = r"\-\u00ad\u2010\u2011\u2012\u2013\u2014\u2212\uff0d"  # 各種連字號：PDF 抽字常把「-」變 U+2010，Word 會變 U+2013
RE_MOBILE = re.compile(rf"(?<!\d)09\d{{2}}[{DASH} ]?\d{{3}}[{DASH} ]?\d{{3}}(?!\d)")
# 市話：區碼 0X 或 (0X)，接 7 到 8 碼，允許一個分隔
RE_LANDLINE = re.compile(rf"(?<![\d(])\(?0[2-8]\)?[{DASH} ]?\d{{3,4}}[{DASH} ]?\d{{4}}(?!\d)")
# 日期：民國或西元，年月日用 / . - 年月日 分隔
RE_DATE = re.compile(
    rf"(?<!\d)(?P<y>\d{{2,4}})\s?[/.{DASH}年]\s?(?P<m>0?[1-9]|1[0-2])\s?[/.{DASH}月]\s?(?P<d>0?[1-9]|[12]\d|3[01])日?(?!\d)"
)
BIRTH_CUES = re.compile(r"(出生|生日|birth)", re.I)
# 電子郵件（10/16 從鼎淵工具搬）
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 統一編號：八位數字，前後不接數字或連字號；要過檢核碼才算，免得八位數的案號、日期被咬到
RE_TAXID = re.compile(rf"(?<![\d{DASH}])\d{{8}}(?![\d{DASH}])")

_ID_LETTER = {c: n for c, n in zip("ABCDEFGHJKLMNPQRSTUVXYWZIO", range(10, 36))}


def is_valid_twid(s: str) -> bool:
    """身分證與新式居留證的檢核碼。只拿來標「⚠ 檢核碼不符」，不拿來決定遮不遮（打錯一碼的身分證也是個資）。"""
    s = s.upper()
    n = _ID_LETTER.get(s[0])
    if n is None or len(s) != 10 or not s[1:].isdigit():
        return False
    total = (n // 10) * 1 + (n % 10) * 9
    for d, w in zip(s[1:9], (8, 7, 6, 5, 4, 3, 2, 1)):
        total += int(d) * w
    return (total + int(s[9])) % 10 == 0


def is_valid_taxid(s: str) -> bool:
    """統一編號檢核碼，112 年 4 月起的新規則：乘數 1,2,1,2,1,2,4,1，各乘積十位加個位，總和被 5 整除；第 7 位是 7 時總和加 1 也算。"""
    if len(s) != 8 or not s.isdigit():
        return False
    total = 0
    for d, w in zip(s, (1, 2, 1, 2, 1, 2, 4, 1)):
        p = int(d) * w
        total += p // 10 + p % 10
    return total % 5 == 0 or (s[6] == "7" and (total + 1) % 5 == 0)
CJK = r"一-鿿"
# 地址：要有「號」，而且前面至少出現一個 路/街/鄉/鎮/區/村/里 的層級
RE_ADDR = re.compile(
    rf"(?:[{CJK}]{{2,3}}[縣市])?"
    rf"(?:[{CJK}]{{1,3}}[鄉鎮市區])?"  # 屏東鄉鎮名最長三字（三地門），放到四字會把前面的「個案住」吃進來
    rf"(?:[{CJK}]{{1,6}}[村里])?"
    rf"(?:[{CJK}\d]{{1,4}}鄰)?"
    rf"(?:[{CJK}\d]{{1,8}}(?:路|街|大道))?"
    rf"(?:[{CJK}\d]{{1,4}}段)?"
    rf"(?:[\d{CJK}]{{1,5}}巷)?"
    rf"(?:[\d{CJK}]{{1,5}}弄)?"
    rf"\d+(?:之\d+)?號"
    rf"(?:\d+樓)?(?:之\d+)?"
)
ADDR_LEVEL = re.compile(r"[路街鄉鎮區村里]|大道")
ADDR_LEADING = set("住在於址到至往自：:，,。 ")
PINGTUNG_TOWNS = ("屏東市", "潮州鎮", "東港鎮", "恆春鎮", "萬丹鄉", "長治鄉", "麟洛鄉", "九如鄉", "里港鄉", "鹽埔鄉", "高樹鄉",
                  "萬巒鄉", "內埔鄉", "竹田鄉", "新埤鄉", "枋寮鄉", "新園鄉", "崁頂鄉", "林邊鄉", "南州鄉", "佳冬鄉", "琉球鄉",
                  "車城鄉", "滿州鄉", "枋山鄉", "三地門鄉", "霧台鄉", "瑪家鄉", "泰武鄉", "來義鄉", "春日鄉", "獅子鄉", "牡丹鄉")
RE_COUNTY = re.compile(rf"[{CJK}]{{2}}[縣市]")


def _address_anchor(val: str) -> int:
    """回傳地址該從第幾個字開始：縣市名前兩字、或鄉鎮名的位置；0 表示沒找到錨點。"""
    best = None
    m = RE_COUNTY.search(val)
    if m and m.group() not in ("屏東市",):
        best = m.start()
    for t in PINGTUNG_TOWNS:
        k = val.find(t)
        if k >= 0 and (best is None or k < best):
            # 鄉鎮名前面緊接著縣名就把縣名也帶上
            if k >= 3 and val[k - 3:k] in ("屏東縣",):
                k -= 3
            best = k if best is None else min(best, k)
    return best or 0


def is_birthdate(text: str, m: re.Match) -> bool:
    """民國年 ≤ 99 或西元 1900–2010 當生日；或前 12 字內有「出生」「生日」。"""
    y = int(m.group("y"))
    before = text[max(0, m.start() - 12): m.start()]
    if BIRTH_CUES.search(before):
        return True
    if len(m.group("y")) <= 2 or (len(m.group("y")) == 3 and y <= 99):
        return 0 < y <= 99
    if len(m.group("y")) == 4:
        return 1900 <= y <= 2010
    return False


@dataclass
class Span:
    start: int
    end: int
    kind: str
    value: str
    alias_of: str | None = None  # 「陳先生」的「陳」屬於名單上的哪個人
    keep: int = 0                # 部分保留：前幾個字留在代號外（只有 xlsx 清冊開著才會非 0）
    warn: str | None = None      # 照遮但要提醒的事，例如身分證檢核碼不符（可能打錯字）


def _norm_skip(s: str) -> str:
    return re.sub(r"[\s\-()（）－‑]", "", s)


# ---------- 部分保留（只給 xlsx 清冊用，主任 10/16 定）----------
# 留下來的字適合統計、不容易反推個人：身分證前 2 碼（縣市與性別）、電話前 4 碼（區碼或電信業者）、地址到鄉鎮市區。
# 姓名與生日一律全遮。幾碼是照林鼎淵工具的預設，主任還沒另定；docx、PDF、掃描件永遠全遮，不走這裡。
DEFAULT_RETENTION = {"身分證": ("head", 2), "電話": ("digits", 4), "地址": ("district",)}
RETENTION_LABEL = "身分證前 2 碼・電話前 4 碼・地址到鄉鎮市區"
RE_DISTRICT_HEAD = re.compile(rf"^(?:[{CJK}]{{2,3}}[縣市])?[{CJK}]{{1,4}}[鄉鎮市區]")


def keep_for(kind: str, value: str, retention: dict | None) -> int:
    """這個值前面要留幾個字；0 是全遮。留的字數一定小於整個值，不然等於沒遮。"""
    rule = (retention or {}).get(kind)
    if not rule:
        return 0
    if rule[0] == "head":
        keep = rule[1]
    elif rule[0] == "digits":
        seen, keep = 0, 0
        for i, ch in enumerate(value):
            if ch.isdigit():
                seen += 1
                if seen == rule[1]:
                    keep = i + 1; break
    else:  # district
        m = RE_DISTRICT_HEAD.match(value)
        keep = m.end() if m else 0
    return keep if 0 < keep < len(value) else 0


def find_spans(text: str, namelist: NameList | None) -> list[Span]:
    spans: list[Span] = []
    skip_spans: list[tuple[int, int]] = []
    if namelist:
        for s in namelist.skip:
            for m in re.finditer(re.escape(s), text):
                skip_spans.append((m.start(), m.end()))
        # 名單姓名：全名優先，再補姓氏＋稱謂
        for name, kind in sorted(namelist.names.items(), key=lambda kv: -len(kv[0])):
            for m in re.finditer(re.escape(name), text):
                spans.append(Span(m.start(), m.end(), kind, name))
        for alias, (name, kind) in namelist.surname_titles().items():
            for m in re.finditer(re.escape(alias), text):
                # 「陳小姐」只換姓那一個字，稱謂保留；代號是本人代號加「姓」，還原時換回「陳」不是全名
                spans.append(Span(m.start(), m.start() + 1, kind, alias[0], alias_of=name))
    for m in RE_ID.finditer(text):
        # 檢核碼不對也照遮（打錯一碼還是個資），只掛警告；鼎淵的工具是不遮，那會漏
        spans.append(Span(m.start(), m.end(), "身分證", m.group(), warn=None if is_valid_twid(m.group()) else "檢核碼不符"))
    for m in RE_EMAIL.finditer(text):
        spans.append(Span(m.start(), m.end(), "電子郵件", m.group()))
    for m in RE_TAXID.finditer(text):
        if is_valid_taxid(m.group()):
            spans.append(Span(m.start(), m.end(), "統編", m.group()))
    for m in RE_MOBILE.finditer(text):
        spans.append(Span(m.start(), m.end(), "電話", m.group()))
    for m in RE_LANDLINE.finditer(text):
        spans.append(Span(m.start(), m.end(), "電話", m.group()))
    for m in RE_DATE.finditer(text):
        if is_birthdate(text, m):
            spans.append(Span(m.start(), m.end(), "生日", m.group()))
    for m in RE_ADDR.finditer(text):
        head = m.group()[: m.group().rfind("號")]
        if ADDR_LEVEL.search(head):
            start, val = m.start(), m.group()
            # 中文沒有詞界，正則會把前面的「個案住」「本案由」一起吃進來。
            # 先找認得的錨點：縣市名、屏東 33 個鄉鎮市名；找到就從錨點開始。找不到才用吐掉常見前綴字的老辦法。
            cut = _address_anchor(val)
            if cut > 0:
                start += cut; val = val[cut:]
            else:
                while val and val[0] in ADDR_LEADING and len(val) > 1:
                    start += 1; val = val[1:]
            spans.append(Span(start, m.end(), "地址", val))

    # 去掉落在「不換」字串裡的，再解重疊：長的優先，再靠前的優先
    # 「不換」比對寬鬆：去掉空白、連字號、括號後，認到的值是不換字串的一部分就不換。
    # 這樣清單寫「屏東縣潮州鎮光春路292號」就同時擋得住「潮州鎮光春路 292 號」「光春路292號2樓」的片段，
    # 寫「08-789-9599」也擋得住「08-7899599」「(08)7899599」。
    skip_norm = [_norm_skip(x) for x in (namelist.skip if namelist else [])]

    def in_skip(sp: Span) -> bool:
        if any(a <= sp.start and sp.end <= b for a, b in skip_spans):
            return True
        v = _norm_skip(sp.value)
        return len(v) >= 6 and any(v in k for k in skip_norm)

    spans = [s for s in spans if not in_skip(s)]
    for sp in spans:  # 尾端空白吐回去，代號不該把後面的空格一起吃掉
        while sp.end > sp.start + 1 and sp.value[-1].isspace():
            sp.end -= 1; sp.value = sp.value[:-1]
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    out: list[Span] = []
    last_end = -1
    for s in sorted(spans, key=lambda s: (-(s.end - s.start), s.start)):
        if all(s.end <= o.start or s.start >= o.end for o in out):
            out.append(s)
    out.sort(key=lambda s: s.start)
    return out


# ---------- 代號 ----------

class CodeBook:
    """代號簿：(類別, 完整原值) → 代號。預設一個檔一本；「整批共用代號」時所有檔共用一本，同一個人在每個檔都是同一個代號。
    可以從上次的合併代號簿（或任何一張對照表）載入接著編，每月清冊串接用。存檔只記類別、原值、代號，不記位置與次數。"""

    def __init__(self):
        self.codes: dict[tuple[str, str], str] = {}
        self.counters: dict[str, int] = {}

    def code_for(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self.codes:
            n = self.counters.get(kind, 0) + 1
            self.counters[kind] = n
            self.codes[key] = f"{kind}{n:02d}"
        return self.codes[key]

    def load(self, path: str | Path) -> int:
        """合併代號簿或單檔對照表都吃；姓氏別名（個案01姓）跳過，由本人代號衍生。編號接在已用過的最大號後面。"""
        for e in read_mapping(path).get("對照", []):
            if e.get("屬於"):
                continue
            kind, code = e["kind"], e["code"]
            self.codes[(kind, e.get("原值", e["value"]))] = code
            m = re.fullmatch(rf"{re.escape(kind)}(\d+)", code)
            if m:
                self.counters[kind] = max(self.counters.get(kind, 0), int(m.group(1)))
        return len(self.codes)

    def save(self, path: str | Path) -> None:
        data = {"說明": "合併代號簿：這批檔共用的代號，含每個代號的完整原值。本身就是個資，跟對照表一樣保管；下次要接著編就匯入這張。",
                "建立時間": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "對照": [{"kind": k, "value": v, "code": c} for (k, v), c in sorted(self.codes.items(), key=lambda kv: (kv[0][0], kv[1]))]}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Mapper:
    """真值 ↔ 代號，一個檔一個 Mapper（次數、位置都是這個檔的）。代號從 CodeBook 拿：沒給就自己一本，給了就跟別的檔共用。
    代號補零到兩位，超過 99 自動變三位。"""

    def __init__(self, book: CodeBook | None = None):
        self.entries: dict[tuple[str, str], dict] = {}  # (kind, value) -> entry
        self.book = book or CodeBook()

    def code_for(self, kind: str, value: str, location: str, keep: int = 0, warn: str | None = None) -> str:
        """keep > 0 是部分保留：代號只代表 value[keep:]，前面的字留在原文；對照表另記「保留」與「原值」。還原把代號換回 value[keep:] 就接回完整的值。
        warn 寫進對照表的「警告」欄，畫面上會標出來。"""
        key = (kind, value)
        if key not in self.entries:
            ent = {"code": self.book.code_for(kind, value), "kind": kind, "value": value[keep:] if keep else value, "count": 0, "locations": []}
            if keep:
                ent["保留"] = value[:keep]; ent["原值"] = value
            if warn:
                ent["警告"] = warn
            self.entries[key] = ent
        e = self.entries[key]
        e["count"] += 1
        if location and location not in e["locations"]:
            e["locations"].append(location)
        return "〔" + e["code"] + "〕"

    def code_for_alias(self, kind: str, fullname: str, surname: str, location: str) -> str:
        """姓氏＋稱謂的姓：代號是本人代號加「姓」，例如〔個案02姓〕，還原回「陳」。"""
        base = self.code_for(kind, fullname, "")  # 確保本人有代號；不計次數
        self.entries[(kind, fullname)]["count"] -= 1
        key = (kind, surname + "@" + fullname)
        if key not in self.entries:
            self.entries[key] = {"code": base.strip("〔〕") + "姓", "kind": kind, "value": surname,
                                 "count": 0, "locations": [], "屬於": fullname}
        e = self.entries[key]
        e["count"] += 1
        if location and location not in e["locations"]:
            e["locations"].append(location)
        return "〔" + e["code"] + "〕"

    def attach(self, kind: str, value: str, alias_of: str | None, pos: dict) -> None:
        """掛上這個值在 PDF 裡的位置與字級，還原時放回原位。"""
        key = (kind, value) if not alias_of else (kind, value + "@" + alias_of)
        if key in self.entries:
            self.entries[key].setdefault("pdf_pos", []).append(pos)

    def to_list(self) -> list[dict]:
        return sorted(self.entries.values(), key=lambda e: (e["kind"], e["code"]))


def apply_spans(text: str, spans: list[Span], mapper: Mapper, location: str, retention: dict | None = None) -> str:
    out, pos = [], 0
    for s in spans:
        out.append(text[pos:s.start])
        if s.alias_of:
            out.append(mapper.code_for_alias(s.kind, s.alias_of, s.value, location))
        else:
            keep = keep_for(s.kind, s.value, retention) if retention else 0
            if keep:
                out.append(s.value[:keep])
            out.append(mapper.code_for(s.kind, s.value, location, keep, s.warn))
        pos = s.end
    out.append(text[pos:])
    return "".join(out)


def deidentify_text(text: str, namelist: NameList | None, mapper: Mapper, location: str = "", retention: dict | None = None) -> str:
    return apply_spans(text, find_spans(text, namelist), mapper, location, retention)


def find_residue(after_text: str, entries: list[dict]) -> list[str]:
    """去識別版的文字抽回來後，對照表上哪些真值還找得到（圖裡「傳給 AI 之前的最後檢查」那一步，做成自動的）。
    回傳代號不回傳真值。跳過姓氏別名（一個字到處都有）與太短的值。
    比對前去掉空白、連字號、括號（跟「不換」同一套寬鬆比對），「0912 345 678」也抓得到「0912-345-678」。
    部分保留的用完整原值比（只拿被遮的「345678」去比，會在別的數字串裡誤中）。"""
    after = _norm_skip(re.sub(rf"[{DASH}]", "-", after_text))
    hits = []
    for e in entries:
        if e.get("屬於"):
            continue
        v = _norm_skip(re.sub(rf"[{DASH}]", "-", e.get("原值", e["value"])))
        if len(v) >= 3 and v in after:
            hits.append(e["code"])
    return hits


# ---------- 對照表 ----------

def write_mapping(path: Path, source: Path, output: Path, mapper: Mapper, reversible: bool, notes: list[str]):
    data = {
        "原檔": str(source),
        "去識別版": str(output),
        "建立時間": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "可還原": reversible,
        "備註": notes,
        "對照": mapper.to_list(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_mapping(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def restore_text(text: str, mapping: dict) -> str:
    """只換完整的〔代號〕，由長到短。

    刻意不換裸寫的「個案01」：正常句子裡「個案113/11/13評估時」會被咬成某個人的名字
    （115/10/3 清冊測試就中）。括號被拿掉的，check_codes 會用次數不符提醒，由人判斷。"""
    pairs = [(e["code"], e["value"]) for e in mapping["對照"]]
    pairs.sort(key=lambda p: -len(p[0]))
    for code, val in pairs:
        text = text.replace("〔" + code + "〕", val)
    return text


def fuzzy_code_pattern(code: str) -> re.Pattern:
    """抓被改壞的代號：括號只剩一邊、半形括號、中間多空格、全形數字。
    完全沒括號也沒空格的裸寫（個案113）不算，正常句子會長這樣；那種靠次數不符提醒。"""
    kind = re.escape(kind_of(code))
    digits = code[len(kind_of(code)):]
    fw = "".join(f"[{d}{chr(ord(d) - 48 + 65296)}]\\s*" for d in digits)
    tail = r"(?![\d０-９姓])"
    alts = [
        rf"[〔\[]\s*{kind}\s*{fw}{tail}[〕\]]?",   # 有左括號
        rf"{kind}\s*{fw}{tail}[〕\]]",            # 只有右括號
        rf"{kind}\s+{fw}{tail}",                   # 沒括號但中間有空格
    ]
    return re.compile("|".join(alts))


def kind_of(code: str) -> str:
    return re.match(r"\D+", code).group()


def check_codes(text: str, mapping: dict) -> list[dict]:
    """每個代號：對照表記的次數、檔裡完整出現次數、疑似改壞的寫法。"""
    report = []
    for e in mapping["對照"]:
        code = e["code"]
        exact = text.count("〔" + code + "〕")
        fuzzy_hits = [m.group() for m in fuzzy_code_pattern(code).finditer(text) if m.group() != "〔" + code + "〕"]
        report.append({"code": code, "expected": e["count"], "exact": exact, "damaged": fuzzy_hits})
    return report

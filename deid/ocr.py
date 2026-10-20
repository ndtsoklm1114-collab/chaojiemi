"""macOS Vision 本機 OCR。回傳每一行文字與可以查子字串位置的候選物件。不連網。

只有 macOS 有 Vision。別的平台 import 這支不會掛，但呼叫 recognize() 會丟 OcrUnavailable，
掃描 PDF 與圖片就會以「此平台無 OCR」失敗，docx／xlsx／文字層 PDF 照常。
要移植到 Windows：把 recognize() 與 substring_box() 換成 Windows 內建 OCR（winocr）或 Tesseract，
回傳的 OcrLine.box 維持「正規化座標、原點左下」，handlers 那邊就不用動。"""
from __future__ import annotations

import sys
from dataclasses import dataclass

AVAILABLE = sys.platform == "darwin"
try:
    import Vision
    from Foundation import NSData, NSRange
except ImportError:  # 非 macOS，或 pyobjc 沒裝
    AVAILABLE = False


class OcrUnavailable(RuntimeError):
    pass


def _require():
    if not AVAILABLE:
        raise OcrUnavailable("此平台沒有 OCR（目前只接 macOS Vision），掃描 PDF 與圖片做不了；文字層 PDF、docx、xlsx、txt 不受影響")


@dataclass
class OcrLine:
    text: str
    box: tuple[float, float, float, float]  # 正規化 (x, y, w, h)，原點左下
    candidate: object                        # VNRecognizedText，拿來查子字串的框


def recognize(png_bytes: bytes) -> list[OcrLine]:
    _require()
    data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(["zh-Hant", "en-US"])
    req.setUsesLanguageCorrection_(True)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR 失敗：{err}")
    lines = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        c = cands[0]
        bb = obs.boundingBox()
        lines.append(OcrLine(str(c.string()), (bb.origin.x, bb.origin.y, bb.size.width, bb.size.height), c))
    # 由上到下、由左到右排
    lines.sort(key=lambda l: (-(l.box[1] + l.box[3]), l.box[0]))
    return lines


def substring_box(line: OcrLine, start: int, end: int) -> tuple[float, float, float, float]:
    """某一行裡第 start 到 end 個字的框（正規化，原點左下）。拿不到就退回整行。"""
    obs, err = line.candidate.boundingBoxForRange_error_(NSRange(start, end - start), None)
    if obs is None:
        return line.box
    bb = obs.boundingBox()
    return (bb.origin.x, bb.origin.y, bb.size.width, bb.size.height)

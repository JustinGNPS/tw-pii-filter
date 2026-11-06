"""完全在本機執行的圖片／掃描 PDF OCR。

本模組只負責把二進位附件變成文字；個資偵測、遮蔽與佔位符配號仍由
``proxy.masker`` 處理。任何套件缺失、格式錯誤、資源超限或低信心結果都回傳
失敗，呼叫端必須移除附件，不能把原始圖片當成退路送到上游。
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import logging
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass

from proxy import config

logger = logging.getLogger("proxy")

_CACHE_SIZE = 32
_engine = None
_engine_lock = threading.Lock()
_image_cache: OrderedDict[str, "OCRResult"] = OrderedDict()
_pdf_cache: OrderedDict[str, "OCRResult"] = OrderedDict()


@dataclass(frozen=True)
class OCRResult:
    text: str = ""
    confidence: float = 0.0
    elapsed_ms: float = 0.0
    line_count: int = 0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text and not self.reason)


def available() -> bool:
    """OCR 執行期是否完整；只檢查本機套件，不連網路。"""
    modules = ("rapidocr", "onnxruntime", "PIL", "numpy", "pypdfium2")
    try:
        return all(importlib.util.find_spec(name) is not None for name in modules)
    except ValueError:
        return True  # 測試替換過 sys.modules 時可能沒有 __spec__


def clear_caches() -> None:
    """測試與設定切換時使用；不卸載已載入的模型。"""
    _image_cache.clear()
    _pdf_cache.clear()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_get(cache: OrderedDict[str, OCRResult], key: str) -> OCRResult | None:
    value = cache.get(key)
    if value is not None:
        cache.move_to_end(key)
    return value


def _cache_put(cache: OrderedDict[str, OCRResult], key: str, value: OCRResult) -> OCRResult:
    cache[key] = value
    cache.move_to_end(key)
    if len(cache) > _CACHE_SIZE:
        cache.popitem(last=False)
    return value


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        _engine = RapidOCR()
    return _engine


def _normalize_line(value: object) -> str:
    # OCR 常把全形英數與空白混進結果；NFKC 讓既有台灣格式規則可以正常比對。
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def _recognize_array(image) -> OCRResult:
    started = time.perf_counter()
    try:
        # RapidOCR／ONNX session 不保證多執行緒安全；模型建立與推論共用同一把鎖。
        with _engine_lock:
            result = _get_engine()(image)
    except Exception as exc:  # noqa: BLE001 - OCR 失敗必須安全降階，不得中斷 proxy
        logger.warning("本機 OCR 執行失敗：%s", exc)
        return OCRResult(reason="OCR 引擎執行失敗")

    elapsed_ms = (time.perf_counter() - started) * 1000
    raw_texts = tuple(result.txts or ())
    raw_scores = tuple(float(score) for score in (result.scores or ()))
    pairs = [
        (_normalize_line(text), score)
        for text, score in zip(raw_texts, raw_scores)
        if _normalize_line(text)
    ]
    if not pairs:
        return OCRResult(elapsed_ms=elapsed_ms, reason="沒有辨識到文字")

    confidence = min(score for _, score in pairs)
    if confidence < config.OCR_MIN_CONFIDENCE:
        return OCRResult(
            confidence=confidence,
            elapsed_ms=elapsed_ms,
            line_count=len(pairs),
            reason=f"最低辨識信心 {confidence:.2f} 低於門檻 {config.OCR_MIN_CONFIDENCE:.2f}",
        )

    text = "\n".join(line for line, _ in pairs)
    return OCRResult(
        text=text,
        confidence=confidence,
        elapsed_ms=elapsed_ms,
        line_count=len(pairs),
    )


def extract_image_text(data: bytes) -> OCRResult:
    """辨識一張圖片；輸入與模型推論全程留在本機。"""
    key = _digest(data)
    cached = _cache_get(_image_cache, key)
    if cached is not None:
        return cached
    if not available():
        return _cache_put(_image_cache, key, OCRResult(reason="尚未安裝本機 OCR 套件"))
    if not data:
        return _cache_put(_image_cache, key, OCRResult(reason="圖片內容為空"))
    if len(data) > config.OCR_MAX_IMAGE_BYTES:
        return _cache_put(
            _image_cache,
            key,
            OCRResult(reason=f"圖片超過 {config.OCR_MAX_IMAGE_BYTES // (1024 * 1024)} MB 上限"),
        )

    try:
        import numpy as np
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > config.OCR_MAX_IMAGE_PIXELS:
                return _cache_put(_image_cache, key, OCRResult(reason="圖片像素超過安全上限"))
            image = ImageOps.exif_transpose(source).convert("RGB")
            array = np.asarray(image)
    except Exception as exc:  # noqa: BLE001
        logger.info("圖片無法解碼，已移除：%s", exc)
        return _cache_put(_image_cache, key, OCRResult(reason="圖片格式無法解碼"))

    return _cache_put(_image_cache, key, _recognize_array(array))


def extract_pdf_text(data: bytes) -> OCRResult:
    """把掃描 PDF 的每頁渲染為 144 DPI 圖片後 OCR；任一頁失敗就整份失敗。"""
    key = _digest(data)
    cached = _cache_get(_pdf_cache, key)
    if cached is not None:
        return cached
    if not available():
        return _cache_put(_pdf_cache, key, OCRResult(reason="尚未安裝本機 OCR 套件"))
    if not data:
        return _cache_put(_pdf_cache, key, OCRResult(reason="PDF 內容為空"))
    if len(data) > config.OCR_MAX_IMAGE_BYTES:
        return _cache_put(
            _pdf_cache,
            key,
            OCRResult(reason=f"PDF 超過 {config.OCR_MAX_IMAGE_BYTES // (1024 * 1024)} MB 上限"),
        )

    try:
        import numpy as np
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(data)
        page_count = len(document)
        if not 0 < page_count <= config.OCR_MAX_PDF_PAGES:
            document.close()
            return _cache_put(
                _pdf_cache,
                key,
                OCRResult(reason=f"PDF 頁數超過 {config.OCR_MAX_PDF_PAGES} 頁上限"),
            )

        texts: list[str] = []
        confidences: list[float] = []
        elapsed_ms = 0.0
        line_count = 0
        try:
            for index in range(page_count):
                page = document[index]
                bitmap = None
                try:
                    page_width, page_height = page.get_size()
                    estimated_pixels = int(page_width * config.OCR_PDF_SCALE) * int(
                        page_height * config.OCR_PDF_SCALE
                    )
                    if estimated_pixels > config.OCR_MAX_IMAGE_PIXELS:
                        return _cache_put(_pdf_cache, key, OCRResult(reason="PDF 頁面像素超過安全上限"))
                    bitmap = page.render(scale=config.OCR_PDF_SCALE)
                    image = bitmap.to_pil().convert("RGB")
                    width, height = image.size
                    if width * height > config.OCR_MAX_IMAGE_PIXELS:
                        return _cache_put(_pdf_cache, key, OCRResult(reason="PDF 頁面像素超過安全上限"))
                    result = _recognize_array(np.asarray(image))
                finally:
                    if bitmap is not None:
                        bitmap.close()
                    page.close()

                if not result.ok:
                    return _cache_put(
                        _pdf_cache,
                        key,
                        OCRResult(reason=f"第 {index + 1} 頁 OCR 失敗：{result.reason}"),
                    )
                texts.append(result.text)
                confidences.append(result.confidence)
                elapsed_ms += result.elapsed_ms
                line_count += result.line_count
        finally:
            document.close()
    except Exception as exc:  # noqa: BLE001
        logger.info("掃描 PDF 無法進行 OCR，已移除：%s", exc)
        return _cache_put(_pdf_cache, key, OCRResult(reason="掃描 PDF 無法渲染或辨識"))

    return _cache_put(
        _pdf_cache,
        key,
        OCRResult(
            text="\n\n".join(texts),
            confidence=min(confidences),
            elapsed_ms=elapsed_ms,
            line_count=line_count,
        ),
    )

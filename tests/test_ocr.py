"""本機 OCR 的資源界線與信心門檻；模型本身用假引擎避免 CI 做重推論。"""

from __future__ import annotations

import io
from types import SimpleNamespace

from PIL import Image

from proxy import config, ocr


def _png_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (32, 16), "white").save(stream, format="PNG")
    return stream.getvalue()


def setup_function():
    ocr.clear_caches()


def test_全形英數正規化後交給既有規則(monkeypatch):
    fake = lambda _image: SimpleNamespace(  # noqa: E731
        txts=("身分證：Ａ１２３４５６７８９", "手機：０９１２３４５６７８"),
        scores=(0.99, 0.98),
    )
    monkeypatch.setattr(ocr, "_get_engine", lambda: fake)

    result = ocr.extract_image_text(_png_bytes())

    assert result.ok
    assert "A123456789" in result.text and "0912345678" in result.text
    assert result.confidence == 0.98 and result.line_count == 2


def test_任一文字行低於信心門檻就整張拒絕(monkeypatch):
    fake = lambda _image: SimpleNamespace(txts=("A123456789", "模糊文字"), scores=(0.99, 0.20))  # noqa: E731
    monkeypatch.setattr(ocr, "_get_engine", lambda: fake)
    monkeypatch.setattr(config, "OCR_MIN_CONFIDENCE", 0.60)

    result = ocr.extract_image_text(_png_bytes())

    assert not result.ok and "低於門檻" in result.reason


def test_像素超限時不載入模型(monkeypatch):
    monkeypatch.setattr(config, "OCR_MAX_IMAGE_PIXELS", 100)
    monkeypatch.setattr(ocr, "_get_engine", lambda: (_ for _ in ()).throw(AssertionError("不該載入模型")))

    result = ocr.extract_image_text(_png_bytes())

    assert not result.ok and "像素超過" in result.reason

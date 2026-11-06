"""proxy/media.py：圖片與 PDF 在遮蔽之前先換成文字。

測試用的 PDF 在執行時用 `_make_pdf()` 當場產生，不放任何 PDF 檔進 repo
（.gitignore 的原則：檢核碼驗得過的號碼不以檔案形式散布）。
"""

import base64
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from core.redact.mapping import MappingTable
from proxy import config, main, masker, media, ocr

UPSTREAM = "https://upstream.test/v1"


def _make_pdf(lines: list[str]) -> bytes:
    """產生一份只有一頁、內含指定 ASCII 文字的最小合法 PDF。lines 為空時是空白頁（模擬掃描版）。"""
    ops = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for line in lines:
        ops.append(f"({line}) Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1") if lines else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


PII_PDF = _make_pdf(["Customer list", "ID A123456789 phone 0912345678", "end of file"])
B64 = base64.b64encode(PII_PDF).decode()
BLANK_B64 = base64.b64encode(_make_pdf([])).decode()


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    media._pdf_cache.clear()
    media._seen.clear()
    ocr.clear_caches()
    monkeypatch.setattr(config, "IMAGE_POLICY", "remove")
    monkeypatch.setattr(config, "DOCUMENT_POLICY", "extract")


def _anthropic_tool_result(block: dict) -> dict:
    return {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [block]}],
            }
        ]
    }


def _anthropic_pdf_block() -> dict:
    return {
        "type": "document",
        "title": "customers.pdf",
        "source": {"type": "base64", "media_type": "application/pdf", "data": B64},
    }


# ── PDF ─────────────────────────────────────────────────────────


def test_抽得出PDF文字():
    assert "A123456789" in media.extract_pdf_text(PII_PDF)


def test_PDF抽出的康熙部首字換回一般漢字(monkeypatch):
    # 實測 Edge 輸出的 PDF「承租人：王小明」抽出來是「承租⼈：王⼩明」（U+2F08、U+2F29），
    # 外觀一樣但編碼不同，語意層會認不出姓名。這裡模擬 pypdf 抽出這樣的文字。
    from pypdf import PageObject

    raw = "承租⼈：王⼩明，⾝分證 A123456789，⼿機 0912345678"
    monkeypatch.setattr(PageObject, "extract_text", lambda self, *a, **k: raw)

    text = media.extract_pdf_text(PII_PDF)

    assert text == "承租人：王小明，身分證 A123456789，手機 0912345678"


def test_部首轉換不動其他字():
    # 全形數字、圈號、一般漢字都是使用者原文，不能被順手改掉
    original = "Ａ１２３ ① 王小明 test@example.com ⺠"
    assert media._fix_radicals(original) == original


def test_Claude_Code讀到的PDF換成文字後照常遮蔽():
    payload = _anthropic_tool_result(_anthropic_pdf_block())
    report = media.sanitize(payload)
    masker.mask_payload_with_risk(payload, MappingTable())

    block = payload["messages"][0]["content"][0]["content"][0]
    assert block["type"] == "text"
    assert "[TW_ID_1]" in block["text"] and "[TW_PHONE_M_1]" in block["text"]
    assert "A123456789" not in json.dumps(payload)
    assert B64 not in json.dumps(payload)
    assert "customers.pdf" in block["text"]
    assert report.documents_extracted == 1


def test_OpenAI格式的file附件也換成文字():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "整理這份"},
                    {"type": "file", "file": {"filename": "a.pdf", "file_data": f"data:application/pdf;base64,{B64}"}},
                ],
            }
        ]
    }
    media.sanitize(payload)
    part = payload["messages"][0]["content"][1]
    assert part["type"] == "text" and "A123456789" in part["text"]


def test_Responses_API的input_file換成input_text():
    payload = {
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_file", "filename": "a.pdf", "file_data": f"data:application/pdf;base64,{B64}"}],
            }
        ]
    }
    media.sanitize(payload)
    part = payload["input"][0]["content"][0]
    assert part["type"] == "input_text" and "A123456789" in part["text"]


def test_掃描版PDF抽不到字就移除並說明原因():
    original = ocr.extract_pdf_text
    ocr.extract_pdf_text = lambda _data: ocr.OCRResult(reason="沒有辨識到文字")
    block = _anthropic_pdf_block()
    block["source"]["data"] = BLANK_B64
    payload = _anthropic_tool_result(block)
    try:
        report = media.sanitize(payload)
        text = payload["messages"][0]["content"][0]["content"][0]["text"]
        assert "掃描版" in text and "OCR 失敗" in text
        assert report.documents_removed == 1
    finally:
        ocr.extract_pdf_text = original


def test_掃描版PDF經本機OCR後照常遮蔽(monkeypatch):
    monkeypatch.setattr(
        ocr,
        "extract_pdf_text",
        lambda _data: ocr.OCRResult(
            text="姓名：王小明\n身分證：A123456789\n手機：0912345678",
            confidence=0.98,
            elapsed_ms=321,
            line_count=3,
        ),
    )
    block = _anthropic_pdf_block()
    block["source"]["data"] = BLANK_B64
    payload = _anthropic_tool_result(block)

    report = media.sanitize(payload)
    masker.mask_payload_with_risk(payload, MappingTable())
    dumped = json.dumps(payload, ensure_ascii=False)

    assert report.documents_ocr == 1 and report.documents_removed == 0
    assert "A123456789" not in dumped and "0912345678" not in dumped
    assert "[TW_ID_1]" in dumped and "[TW_PHONE_M_1]" in dumped


def test_同時含文字層與圖片的PDF仍強制OCR(monkeypatch):
    monkeypatch.setattr(media, "pdf_contains_images", lambda _data: True)
    monkeypatch.setattr(media, "extract_pdf_text", lambda _data: "頁首文字不代表圖片安全")
    monkeypatch.setattr(
        ocr,
        "extract_pdf_text",
        lambda _data: ocr.OCRResult(text="圖片內身分證 A123456789", confidence=0.99, line_count=1),
    )
    payload = _anthropic_tool_result(_anthropic_pdf_block())

    report = media.sanitize(payload)
    masker.mask_payload_with_risk(payload, MappingTable())
    dumped = json.dumps(payload, ensure_ascii=False)

    assert report.documents_ocr == 1
    assert "A123456789" not in dumped and "[TW_ID_1]" in dumped


def test_壞掉的PDF不會讓請求失敗():
    block = _anthropic_pdf_block()
    block["source"]["data"] = base64.b64encode(b"%PDF-1.4 this is not really a pdf").decode()
    payload = _anthropic_tool_result(block)
    media.sanitize(payload)
    assert "無法讀取" in payload["messages"][0]["content"][0]["content"][0]["text"]


def test_純文字文件直接換成文字():
    payload = _anthropic_tool_result(
        {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "身分證 A123456789"}}
    )
    media.sanitize(payload)
    assert payload["messages"][0]["content"][0]["content"][0] == {"type": "text", "text": "身分證 A123456789"}


def test_設定為remove時文件整份移除(monkeypatch):
    monkeypatch.setattr(config, "DOCUMENT_POLICY", "remove")
    payload = _anthropic_tool_result(_anthropic_pdf_block())
    media.sanitize(payload)
    text = payload["messages"][0]["content"][0]["content"][0]["text"]
    assert "A123456789" not in text and "依使用者設定已移除" in text


# ── 圖片 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "SECRETBYTES"}},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,SECRETBYTES"}},
        {"type": "input_image", "image_url": "data:image/png;base64,SECRETBYTES"},
    ],
)
def test_預設三種格式的圖片都換成說明文字(block):
    payload = {"messages": [{"role": "user", "content": [block]}]}
    report = media.sanitize(payload)
    assert "SECRETBYTES" not in json.dumps(payload)
    assert "本地個資過濾器" in payload["messages"][0]["content"][0]["text"]
    assert report.images_removed == 1


def test_allow時圖片照原樣保留(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_POLICY", "allow")
    block = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    payload = {"messages": [{"role": "user", "content": [dict(block)]}]}
    report = media.sanitize(payload)
    assert payload["messages"][0]["content"][0] == block
    assert report.images_allowed == 1 and not report.changed
    assert "未檢查" in report.new_items[0][1]


def test_ocr圖片換成文字後走同一套遮蔽(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_POLICY", "ocr")
    monkeypatch.setattr(
        ocr,
        "extract_image_text",
        lambda _data: ocr.OCRResult(
            text="姓名：王小明\n身分證：A123456789\n手機：0912345678\n信箱：wang.test@example.com",
            confidence=0.99,
            elapsed_ms=123,
            line_count=4,
        ),
    )
    raw = base64.b64encode(b"synthetic image").decode()
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": raw}}],
            }
        ]
    }

    report = media.sanitize(payload)
    masker.mask_payload_with_risk(payload, MappingTable())
    dumped = json.dumps(payload, ensure_ascii=False)

    assert report.images_ocr == 1 and report.images_removed == 0
    assert raw not in dumped and "A123456789" not in dumped and "wang.test@example.com" not in dumped
    assert "[TW_ID_1]" in dumped and "[TW_PHONE_M_1]" in dumped and "[EMAIL_1]" in dumped


def test_ocr圖片低信心時安全移除(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_POLICY", "ocr")
    monkeypatch.setattr(
        ocr,
        "extract_image_text",
        lambda _data: ocr.OCRResult(confidence=0.3, reason="最低辨識信心過低"),
    )
    raw = base64.b64encode(b"unclear image").decode()
    payload = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{raw}"}}]}
        ]
    }

    report = media.sanitize(payload)
    dumped = json.dumps(payload, ensure_ascii=False)

    assert report.images_removed == 1 and report.images_ocr == 0
    assert raw not in dumped and "OCR 失敗後已安全移除" in dumped


def test_ocr不會為了讀圖而下載遠端網址(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_POLICY", "ocr")
    payload = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.test/secret.png"}}]}
        ]
    }

    report = media.sanitize(payload)
    dumped = json.dumps(payload, ensure_ascii=False)

    assert report.images_removed == 1
    assert "example.test" not in dumped and "無法在本機安全取得圖片" in dumped


def test_附件OCR後若遮蔽器失敗則OpenAI路徑阻擋(monkeypatch):
    def sanitize(payload):
        payload["messages"][0]["content"] = "OCR 明文 A123456789"
        return media.MediaReport(images_ocr=1)

    monkeypatch.setattr(media, "sanitize", sanitize)
    monkeypatch.setattr(masker, "mask_payload_with_risk", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    body = json.dumps({"messages": [{"role": "user", "content": []}]}).encode()

    with pytest.raises(main.MediaPrivacyError):
        main._mask_request("chat/completions", body, MappingTable())


def test_附件OCR後若遮蔽器失敗則Claude_Code路徑阻擋(monkeypatch):
    def sanitize(payload):
        payload["messages"][0]["content"] = "OCR 明文 A123456789"
        return media.MediaReport(images_ocr=1)

    monkeypatch.setattr(media, "sanitize", sanitize)
    monkeypatch.setattr(masker, "mask_payload_with_risk", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    payload = {"messages": [{"role": "user", "content": []}]}

    with pytest.raises(main.MediaPrivacyError):
        main._mask_anthropic_payload(payload, MappingTable())


# ── 邊界 ─────────────────────────────────────────────────────────


def test_同一份檔案每輪重送_只有第一次列入新項目():
    first = media.sanitize(_anthropic_tool_result(_anthropic_pdf_block()))
    second = media.sanitize(_anthropic_tool_result(_anthropic_pdf_block()))
    assert len(first.new_items) == 1
    assert second.new_items == []
    assert second.documents_extracted == 1  # 仍然要轉換，只是不重複印 log


def test_不碰tools裡的JSON_Schema():
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "x", "input_schema": {"properties": {"a": {"type": "image"}}}}],
    }
    before = json.dumps(payload)
    media.sanitize(payload)
    assert json.dumps(payload) == before


# ── 整合：OpenAI 路徑實際送出的內容 ─────────────────────────────


@respx.mock
def test_OpenAI路徑送出的PDF已轉文字並遮蔽(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    )
    payload = {
        "model": "gpt-4.1-mini",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "file", "file": {"filename": "a.pdf", "file_data": f"data:application/pdf;base64,{B64}"}}],
            }
        ],
    }
    with TestClient(main.app) as client:
        response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    sent = route.calls[0].request.content.decode("utf-8")
    assert "A123456789" not in sent and B64 not in sent
    assert "[TW_ID_1]" in sent

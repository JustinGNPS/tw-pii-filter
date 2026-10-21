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
from proxy import config, main, masker, media

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
    block = _anthropic_pdf_block()
    block["source"]["data"] = BLANK_B64
    payload = _anthropic_tool_result(block)
    report = media.sanitize(payload)
    text = payload["messages"][0]["content"][0]["content"][0]["text"]
    assert "掃描版" in text
    assert report.documents_removed == 1


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

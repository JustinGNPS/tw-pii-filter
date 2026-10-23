"""圖片與 PDF：在遮蔽**之前**先把它們變成文字層看得到的東西。

## 為什麼需要

agent 讀到圖片或 PDF 時，內容是 base64 編碼的二進位資料。偵測核心只看文字，
看不到裡面的個資。在這個模組之前，兩條路徑各有一個問題：

- **Claude Code（Anthropic 格式）**：遇到 `image`／`document` block 直接回 501。
  agent 每輪都重送完整歷史，所以只要出現過一次，之後**每個請求都失敗**，
  整個工作階段卡死。
- **OpenAI 格式的 agent**：`image_url`／`file` 原封不動送出，**沒有任何警告**。

一個卡死、一個靜默外洩，而且兩個載體行為不一致。

## 怎麼處理（`config.IMAGE_POLICY`／`config.DOCUMENT_POLICY`）

| 內容 | 預設 | 做法 |
|---|---|---|
| PDF（有文字層） | extract | 本機抽出文字，換成文字 block —— 之後由 `masker` 照一般文字遮蔽 |
| PDF（掃描版，沒有文字層） | — | 抽不出字，等同看不見內容，換成說明文字 |
| 純文字文件（Anthropic `source.type == "text"`） | — | 本來就是文字，直接換成文字 block |
| 圖片 | remove | 換成一行說明文字；AI 知道這裡原本有圖，工作階段不會卡死 |
| 圖片（`allow`） | — | 照原樣送出，第一次看到時 log 提醒 |

換成的說明文字裡**不含任何原始內容**，只有檔名（若有）與原因。

## 為什麼在遮蔽之前

PDF 抽出來的文字要跟其他文字走**同一條**遮蔽路徑（同一張對照表、同一套配號），
否則同一個身分證在對話裡與 PDF 裡會拿到不同佔位符。所以這裡只負責「換成文字」，
遮蔽仍由 `masker.mask_payload_with_risk()` 做。

## 快取與 log

agent 每輪重送歷史，同一份 PDF 會反覆出現。抽取結果以內容雜湊快取；
log 也只在**第一次**看到某個檔案時印，不然每輪都會洗一次版。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from proxy import config

logger = logging.getLogger("proxy")

# 抽不到這麼多非空白字元，就當作掃描版（只有圖）
MIN_EXTRACTED_CHARS = 20
_CACHE_SIZE = 64
_SEEN_LIMIT = 4096

# Anthropic：image / document；OpenAI chat：image_url / file；Responses API：input_image / input_file
IMAGE_BLOCK_TYPES = frozenset({"image", "image_url", "input_image"})
DOCUMENT_BLOCK_TYPES = frozenset({"document", "file", "input_file"})

# 只往這些鍵底下找 block，不盲目走訪整個 payload（tools 的 JSON Schema 等不該碰）
_CONTAINER_KEYS = ("messages", "input", "content", "output")

_pdf_cache: OrderedDict[str, str | None] = OrderedDict()
_seen: OrderedDict[str, None] = OrderedDict()


@dataclass
class MediaReport:
    images_removed: int = 0
    images_allowed: int = 0
    documents_extracted: int = 0
    documents_removed: int = 0
    # 第一次看到的項目（用來印 log），格式：(種類, 說明)
    new_items: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.images_removed or self.documents_extracted or self.documents_removed)


def _note(text: str) -> str:
    return f"〔本地個資過濾器：{text}〕"


def _text_block(original_type: str, text: str) -> dict:
    # Responses API 的文字 part 叫 input_text，其餘格式都叫 text
    return {"type": "input_text" if original_type.startswith("input_") else "text", "text": text}


def _first_time(key: str) -> bool:
    if key in _seen:
        return False
    _seen[key] = None
    if len(_seen) > _SEEN_LIMIT:
        _seen.popitem(last=False)
    return True


def _digest(data: str | bytes) -> str:
    raw = data.encode("utf-8", "surrogatepass") if isinstance(data, str) else data
    return hashlib.sha1(raw).hexdigest()


def _decode_data_url(value: str) -> tuple[str, bytes] | None:
    """`data:application/pdf;base64,....` → (media_type, bytes)。不是 data URL 就回 None。"""
    if not value.startswith("data:") or "," not in value:
        return None
    header, payload = value[5:].split(",", 1)
    media_type = header.split(";", 1)[0]
    try:
        return media_type, base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None


def extract_pdf_text(data: bytes) -> str | None:
    """抽出 PDF 文字。抽不到（掃描版）回空字串；讀不了（壞檔、加密、沒裝 pypdf）回 None。"""
    key = _digest(data)
    if key in _pdf_cache:
        _pdf_cache.move_to_end(key)
        return _pdf_cache[key]

    try:
        from pypdf import PdfReader  # 延遲載入：舊的 .venv 沒裝也不會讓 proxy 起不來
    except ImportError:
        logger.warning("未安裝 pypdf，PDF 一律移除。請執行：pip install -r proxy/requirements.txt")
        return None

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")  # 只有「無密碼加密」打得開；有密碼的會在下面丟例外
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(p.strip() for p in pages if p.strip())
    except Exception as exc:  # noqa: BLE001 - 壞掉的 PDF 不能讓整個請求失敗
        logger.warning("PDF 無法讀取，已移除：%s", exc)
        text = None

    if text is not None and len("".join(text.split())) < MIN_EXTRACTED_CHARS:
        text = ""
    _pdf_cache[key] = text
    if len(_pdf_cache) > _CACHE_SIZE:
        _pdf_cache.popitem(last=False)
    return text


def _document_source(block: dict) -> tuple[str, str | bytes | None, str]:
    """把三種格式的文件 block 統一成 (種類, 內容, 檔名)。

    種類：pdf（內容為 bytes）／text（內容為 str）／unknown（內容為 None）
    """
    btype = block.get("type")
    if btype == "document":  # Anthropic
        source = block.get("source") or {}
        title = block.get("title") or ""
        stype = source.get("type")
        if stype == "base64" and source.get("media_type") == "application/pdf":
            try:
                return "pdf", base64.b64decode(source.get("data") or ""), title
            except (binascii.Error, ValueError):
                return "unknown", None, title
        if stype == "text" and isinstance(source.get("data"), str):
            return "text", source["data"], title
        if stype == "content" and isinstance(source.get("content"), list):
            parts = [
                b.get("text", "")
                for b in source["content"]
                if isinstance(b, dict) and isinstance(b.get("text"), str)
            ]
            return "text", "\n\n".join(parts), title
        return "unknown", None, title

    # OpenAI chat 的 file 把欄位包在 "file" 底下；Responses 的 input_file 直接放在 block 上
    holder = block.get("file") if btype == "file" and isinstance(block.get("file"), dict) else block
    filename = holder.get("filename") or ""
    file_data = holder.get("file_data")
    if isinstance(file_data, str):
        decoded = _decode_data_url(file_data)
        if decoded is None:  # 有些用戶端只給純 base64、不帶 data: 前綴
            try:
                decoded = ("application/pdf", base64.b64decode(file_data))
            except (binascii.Error, ValueError):
                decoded = None
        if decoded and decoded[0] == "application/pdf":
            return "pdf", decoded[1], filename
    return "unknown", None, filename


def _image_identity(block: dict) -> str:
    """給 log 去重用的識別：內容雜湊，取不到就用網址。"""
    if block.get("type") == "image":
        source = block.get("source") or {}
        return _digest(source.get("data") or source.get("url") or "")
    url = block.get("image_url")
    if isinstance(url, dict):
        url = url.get("url")
    return _digest(url or block.get("file_id") or "")


def _handle_image(block: dict, report: MediaReport) -> dict | None:
    """回傳要取代成的 block；None 代表保留原樣。"""
    ident = _image_identity(block)
    if config.IMAGE_POLICY == "allow":
        report.images_allowed += 1
        if _first_time("img-allow:" + ident):
            report.new_items.append(("圖片", "照原樣送出（未檢查內容）"))
        return None
    report.images_removed += 1
    if _first_time("img:" + ident):
        report.new_items.append(("圖片", "已移除"))
    return _text_block(
        block.get("type", ""),
        _note("這裡原本有一張圖片，已在使用者的電腦上移除，因為圖片內容無法檢查是否含個資"),
    )


def _handle_document(block: dict, report: MediaReport) -> dict:
    kind, content, name = _document_source(block)
    label = f"「{name}」" if name else ""
    ident = _digest(content) if content else _digest(repr(block)[:4096])
    btype = block.get("type", "")

    if config.DOCUMENT_POLICY == "remove":
        report.documents_removed += 1
        if _first_time("doc-rm:" + ident):
            report.new_items.append(("文件", f"{label or 'PDF'} 已移除（設定為不送出文件）"))
        return _text_block(btype, _note(f"這裡原本有一份文件{label}，依使用者設定已移除"))

    if kind == "text" and isinstance(content, str):
        report.documents_extracted += 1
        if _first_time("doc:" + ident):
            report.new_items.append(("文件", f"{label or '純文字文件'} 已轉成文字並遮蔽"))
        return _text_block(btype, content)

    text = extract_pdf_text(content) if kind == "pdf" and isinstance(content, bytes) else None
    if text:
        report.documents_extracted += 1
        if _first_time("doc:" + ident):
            report.new_items.append(("PDF", f"{label}已在本機轉成文字（{len(text)} 字），個資照常遮蔽"))
        header = _note(f"以下是 PDF{label}在使用者電腦上轉出的文字，個資已遮蔽；原始排版與圖片未保留")
        return _text_block(btype, f"{header}\n{text}")

    report.documents_removed += 1
    if text == "":
        reason = "掃描版 PDF（沒有文字層），內容無法檢查是否含個資"
    elif kind == "pdf":
        reason = "PDF 無法讀取（可能有密碼保護或檔案損毀）"
    else:
        reason = "不支援的文件格式（只支援 PDF 與純文字）"
    if _first_time("doc-fail:" + ident):
        report.new_items.append(("文件", f"{label}已移除：{reason}"))
    return _text_block(btype, _note(f"這裡原本有一份文件{label}，已移除：{reason}"))


def _walk(node: Any, report: MediaReport) -> None:
    if isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, dict):
                btype = item.get("type")
                replacement = None
                if btype in IMAGE_BLOCK_TYPES:
                    replacement = _handle_image(item, report)
                elif btype in DOCUMENT_BLOCK_TYPES:
                    replacement = _handle_document(item, report)
                if replacement is not None:
                    node[i] = replacement
                    continue
            _walk(item, report)
    elif isinstance(node, dict):
        for key in _CONTAINER_KEYS:
            if key in node:
                _walk(node[key], report)


def sanitize(payload: Any) -> MediaReport:
    """就地把 payload 裡的圖片與文件換成文字 block（依設定），回傳處理摘要。"""
    report = MediaReport()
    if isinstance(payload, dict):
        _walk(payload, report)
    return report


def log_report(report: MediaReport, tag: str = "") -> None:
    """只印第一次看到的項目。圖片放行時用 WARNING，讓使用者注意到。"""
    for kind, what in report.new_items:
        level = logging.WARNING if "未檢查" in what else logging.INFO
        logger.log(level, "%s%s：%s", kind, tag, what)

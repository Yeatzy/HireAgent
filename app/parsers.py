from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader

from .config import PROJECT_ROOT
from .text_hygiene import clean_text_encoding, decode_text_bytes, prefer_chinese_duplicate_text


WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TESSDATA_DIR = PROJECT_ROOT / "assets" / "tessdata"
MIN_USEFUL_TEXT_LENGTH = 20
OCR_TRIGGER_LENGTH = 80
SUSPICIOUS_TEXT_LAYER_RATIO = 0.08


class UnsupportedDocumentError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    warning: str = ""
    used_ocr: bool = False


def _normalize_text(text: str) -> str:
    text = clean_text_encoding(text)
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{WORD_NS}p"):
        runs = [node.text or "" for node in paragraph.iter(f"{WORD_NS}t")]
        value = "".join(runs).strip()
        if value:
            paragraphs.append(value)
    return "\n".join(paragraphs)


def _parse_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _pdf_text_layer_is_suspicious(text: str) -> bool:
    if not text.strip():
        return False
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return False
    cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in chars)
    suspicious_count = sum(
        "\u0370" <= char <= "\u1fff"
        or "\u0b00" <= char <= "\u0fff"
        or "\u1780" <= char <= "\u18af"
        for char in chars
    )
    return cjk_count == 0 and suspicious_count / len(chars) >= SUSPICIOUS_TEXT_LAYER_RATIO


def _page_number(path: Path) -> int:
    match = re.search(r"-(\d+)\.png$", path.name)
    return int(match.group(1)) if match else 0


def _find_executable(name: str) -> str | None:
    discovered = shutil.which(name)
    if discovered:
        return discovered
    for directory in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    return None


def ocr_available() -> bool:
    return bool(_find_executable("pdftoppm") and _find_executable("tesseract"))


def _ocr_pdf(data: bytes) -> str:
    pdftoppm = _find_executable("pdftoppm")
    tesseract = _find_executable("tesseract")
    if not pdftoppm or not tesseract:
        return ""

    with tempfile.TemporaryDirectory(prefix="hireagent-ocr-") as temp_dir:
        temp_path = Path(temp_dir)
        pdf_path = temp_path / "document.pdf"
        pdf_path.write_bytes(data)
        render_prefix = temp_path / "page"
        try:
            subprocess.run(
                [
                    pdftoppm,
                    "-png",
                    "-r",
                    "180",
                    "-f",
                    "1",
                    "-l",
                    "12",
                    str(pdf_path),
                    str(render_prefix),
                ],
                check=True,
                capture_output=True,
                timeout=90,
            )
        except (OSError, subprocess.SubprocessError):
            return ""

        environment = os.environ.copy()
        languages = "eng"
        if (TESSDATA_DIR / "chi_sim.traineddata").exists():
            environment["TESSDATA_PREFIX"] = str(TESSDATA_DIR)
            languages = "chi_sim+eng"

        page_texts: list[str] = []
        for image_path in sorted(temp_path.glob("page-*.png"), key=_page_number):
            try:
                result = subprocess.run(
                    [
                        tesseract,
                        str(image_path),
                        "stdout",
                        "-l",
                        languages,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=environment,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            page_text = _normalize_text(result.stdout)
            if page_text:
                page_texts.append(page_text)
        return "\n\n".join(page_texts)


def parse_document_with_metadata(filename: str, data: bytes) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    warning = ""
    used_ocr = False
    if suffix in {".txt", ".md"}:
        text = decode_text_bytes(data)
    elif suffix == ".docx":
        text = _parse_docx(data)
    elif suffix == ".pdf":
        try:
            text = _parse_pdf(data)
        except Exception:
            text = ""
        normalized_text = _normalize_text(text)
        should_use_ocr = (
            len(normalized_text) < OCR_TRIGGER_LENGTH
            or _pdf_text_layer_is_suspicious(normalized_text)
        )
        if should_use_ocr:
            ocr_text = _ocr_pdf(data)
            if len(_normalize_text(ocr_text)) > len(normalized_text):
                text = ocr_text
                used_ocr = True
                warning = f"{filename} 为扫描版或文字层编码异常，已自动使用 OCR 识别"
    else:
        raise UnsupportedDocumentError(f"暂不支持 {suffix or '无扩展名'} 文件")
    text = _normalize_text(text)
    text = prefer_chinese_duplicate_text(text)
    if len(text) < MIN_USEFUL_TEXT_LENGTH:
        stem = Path(filename).stem
        fallback = f"文件名称：{stem}\n该文档可提取文字较少，请结合原始简历人工核验。"
        text = f"{text}\n{fallback}".strip()
        warning = f"{filename} 可提取文字较少，系统已继续分析，请人工核验结果"
    return ParsedDocument(text=text, warning=warning, used_ocr=used_ocr)


def parse_document(filename: str, data: bytes) -> str:
    return parse_document_with_metadata(filename, data).text

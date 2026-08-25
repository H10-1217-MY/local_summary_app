from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:14b")

NDLOCR_COMMAND = os.getenv("NDLOCR_COMMAND", "ndlocr-lite")
NDLOCR_DEVICE = os.getenv("NDLOCR_DEVICE", "cpu")
NDLOCR_TIMEOUT_SECONDS = int(os.getenv("NDLOCR_TIMEOUT_SECONDS", "1800"))

MAX_INPUT_CHARS = 30_000
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100

# PDFに埋め込まれた文字がこれ未満ならOCRへフォールバックする。
# 複数ページの場合はページ数に応じて閾値を少し上げる。
MIN_PDF_TEXT_CHARS = 80
MIN_PDF_TEXT_CHARS_PER_PAGE = 30

IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tiff",
    ".tif",
    ".jp2",
    ".bmp",
    ".webp",
}

AVAILABLE_MODELS = {
    "qwen3:8b": "Qwen3 8B（軽快）",
    "qwen3:14b": "Qwen3 14B（品質重視）",
}

SUMMARY_INSTRUCTIONS = {
    "standard": """次の文章を要約してください。

出力形式:
1. 3行要約
2. 重要事項
3. 未解決点
4. 次にやること""",
    "technical": """次の技術文書または作業メモを整理してください。

出力形式:
1. 概要
2. 実施内容・確認結果
3. 問題点・リスク
4. 未確認事項
5. 次に行う作業""",
    "meeting": """次の会議メモを整理してください。

出力形式:
1. 会議の要旨
2. 決定事項
3. 保留事項
4. 担当者と期限
5. 次回までの対応""",
    "short": """次の文章を簡潔に要約してください。

出力形式:
- 100文字程度の要約
- 重要なキーワード 3〜5個""",
}

STRICTNESS_INSTRUCTIONS = {
    "strict": """【原文忠実モード】
- 入力文に明示されている内容だけを使用してください。
- 入力にない情報、原因、意図、背景、対応策を補わないでください。
- 「未解決点」「未確認事項」は、原文で未決定・未確認と明示されているものだけを書いてください。
- 単に原文に記載がない情報を「未解決」と判断しないでください。
- 該当項目がない場合は「なし」としてください。
- 指定された出力形式の後に、挨拶、提案、追加対応の申し出を書かないでください。""",
    "normal": """【通常モード】
- 原文の内容を中心に、自然で読みやすく整理してください。
- 明示されていない内容を断定しないでください。
- 必要な場合だけ、推測であることを明示して補足してください。
- 指定された出力形式の後に、挨拶や追加対応の申し出を書かないでください。""",
    "helpful": """【補足ありモード】
- 原文を要約したうえで、役に立つ補足や次の行動案を追加して構いません。
- 原文由来の事実と、モデルによる補足・提案は明確に区別してください。
- 指定された出力形式を基本構造として維持してください。""",
}

app = FastAPI(title="Local Summary App v4")

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def default_model() -> str:
    if DEFAULT_MODEL in AVAILABLE_MODELS:
        return DEFAULT_MODEL
    return "qwen3:14b"


def empty_context() -> dict[str, Any]:
    return {
        "text": "",
        "summary_type": "standard",
        "strictness": "strict",
        "model": default_model(),
        "result": "",
        "error": "",
        "metrics": None,
        "source": None,
        "max_input_chars": MAX_INPUT_CHARS,
        "max_pdf_mb": MAX_PDF_BYTES // (1024 * 1024),
        "max_image_mb": MAX_IMAGE_BYTES // (1024 * 1024),
        "max_pdf_pages": MAX_PDF_PAGES,
        "available_models": AVAILABLE_MODELS,
        "ocr_available": ndlocr_available(),
        "ocr_device": NDLOCR_DEVICE,
    }


def ndlocr_available() -> bool:
    command = Path(NDLOCR_COMMAND)

    if command.is_absolute():
        return command.exists() and os.access(command, os.X_OK)

    return shutil.which(NDLOCR_COMMAND) is not None


def visible_char_count(text: str) -> int:
    return len("".join(text.split()))


def normalize_extracted_text(text: str) -> str:
    """PyMuPDF等から得たテキストの空行を軽く整理する。"""
    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]

    cleaned: list[str] = []
    blank_pending = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            blank_pending = True
            continue

        if blank_pending and cleaned:
            cleaned.append("")

        cleaned.append(stripped)
        blank_pending = False

    return "\n".join(cleaned).strip()


def _looks_like_heading(line: str) -> bool:
    if not line:
        return False

    if len(line) > 36:
        return False

    if line.startswith(("-", "・", "●", "○", "※", "*")):
        return False

    sentence_endings = ("。", "！", "？", ".", "!", "?", "、", ",")
    if line.endswith(sentence_endings):
        return False

    return True


def _needs_ascii_space(left: str, right: str) -> bool:
    if not left or not right:
        return False

    return (
        left[-1].isascii()
        and right[0].isascii()
        and left[-1].isalnum()
        and right[0].isalnum()
    )


def normalize_ocr_text(text: str) -> str:
    """
    OCR特有の「表示幅で切れた改行」を軽く結合する。
    誤認識した単語そのものは推測修正しない。
    """
    raw_lines = [
        line.strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]

    paragraphs: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer

        if not buffer:
            return

        merged = buffer[0]

        for part in buffer[1:]:
            separator = " " if _needs_ascii_space(merged, part) else ""
            merged += separator + part

        paragraphs.append(merged.strip())
        buffer = []

    for line in raw_lines:
        if not line:
            flush()
            continue

        # 短い見出しの直後に本文が来た場合、見出しと本文を結合しない。
        if buffer and _looks_like_heading(buffer[-1]) and len(line) >= 40:
            flush()

        # 箇条書きは独立行として保持する。
        if line.startswith(("-", "・", "●", "○", "※")):
            flush()
            paragraphs.append(line)
            continue

        buffer.append(line)

    flush()

    return "\n\n".join(
        paragraph
        for paragraph in paragraphs
        if paragraph
    ).strip()


def extract_pdf_text(pdf_bytes: bytes) -> tuple[str, int]:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"PDFを開けませんでした: {exc}") from exc

    try:
        page_count = doc.page_count

        if page_count == 0:
            raise ValueError("PDFにページがありません。")

        if page_count > MAX_PDF_PAGES:
            raise ValueError(
                f"PDFが長すぎます。現在は{MAX_PDF_PAGES}ページまで対応しています。"
            )

        chunks: list[str] = []

        for page_number, page in enumerate(doc, start=1):
            page_text = page.get_text("text", sort=True)
            page_text = normalize_extracted_text(page_text)

            if page_text:
                chunks.append(
                    f"--- Page {page_number} ---\n{page_text}"
                )

        return "\n\n".join(chunks).strip(), page_count

    finally:
        doc.close()


def pdf_needs_ocr(text: str, page_count: int) -> bool:
    threshold = max(
        MIN_PDF_TEXT_CHARS,
        page_count * MIN_PDF_TEXT_CHARS_PER_PAGE,
    )
    return visible_char_count(text) < threshold


def resolve_ndlocr_command() -> str:
    command = Path(NDLOCR_COMMAND)

    if command.is_absolute():
        if command.exists() and os.access(command, os.X_OK):
            return str(command)

        raise RuntimeError(
            f"NDLOCR-Lite実行ファイルが見つかりません: {NDLOCR_COMMAND}"
        )

    resolved = shutil.which(NDLOCR_COMMAND)

    if not resolved:
        raise RuntimeError(
            "NDLOCR-Liteコマンドが見つかりません。"
            " `ndlocr-lite --help` が同じターミナルで動くか確認してください。"
        )

    return resolved


def run_ndlocr(
    source_path: Path,
    output_dir: Path,
    input_type: str,
) -> tuple[str, float]:
    command = resolve_ndlocr_command()

    if input_type == "pdf":
        input_flag = "--sourcepdf"
    elif input_type == "image":
        input_flag = "--sourceimg"
    else:
        raise ValueError(f"未対応のOCR入力形式です: {input_type}")

    cmd = [
        command,
        input_flag,
        str(source_path),
        "--output",
        str(output_dir),
        "--device",
        NDLOCR_DEVICE,
    ]

    started = time.perf_counter()

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=NDLOCR_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"NDLOCR-Liteが{NDLOCR_TIMEOUT_SECONDS}秒以内に完了しませんでした。"
        ) from exc

    elapsed = round(time.perf_counter() - started, 2)

    if completed.returncode != 0:
        details = "\n".join(
            part.strip()
            for part in (completed.stdout, completed.stderr)
            if part and part.strip()
        )

        if len(details) > 1500:
            details = details[-1500:]

        raise RuntimeError(
            "NDLOCR-Liteの実行に失敗しました。"
            + (f"\n{details}" if details else "")
        )

    expected_txt = output_dir / f"{source_path.stem}.txt"

    if expected_txt.exists():
        txt_path = expected_txt
    else:
        txt_candidates = sorted(output_dir.glob("*.txt"))

        if not txt_candidates:
            raise RuntimeError(
                "NDLOCR-Liteは終了しましたが、OCR結果の.txtが見つかりませんでした。"
            )

        txt_path = txt_candidates[0]

    raw_text = txt_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    normalized = normalize_ocr_text(raw_text)

    if visible_char_count(normalized) < 10:
        raise RuntimeError(
            "OCR処理は完了しましたが、十分な文字を取得できませんでした。"
        )

    return normalized, elapsed


async def summarize_with_ollama(
    text: str,
    summary_type: str,
    strictness: str,
    model: str,
    is_ocr: bool,
) -> tuple[str, dict[str, Any]]:
    ocr_note = ""

    if is_ocr:
        ocr_note = """
【OCR入力について】
- この文章はOCRで取得したため、文字誤認識を含む可能性があります。
- 誤認識と思われる語句を、文脈だけを根拠に断定的に修正しないでください。
- 固有名詞や数値が不明瞭な場合は、入力された表記を維持するか、不明瞭であることを示してください。
"""

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "あなたは日本語文書の要約担当です。"
                    "原文と推測を混同せず、指定された形式を守ってください。"
                    "指定された出力の後に不要な挨拶や営業的な提案を追加しないでください。"
                ),
            },
            {
                "role": "user",
                "content": (
                    STRICTNESS_INSTRUCTIONS[strictness]
                    + "\n\n"
                    + ocr_note
                    + "\n"
                    + SUMMARY_INSTRUCTIONS[summary_type]
                    + "\n\n対象文章:\n"
                    + text
                ),
            },
        ],
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {
            "temperature": 0.2 if strictness != "helpful" else 0.4,
        },
    }

    timeout = httpx.Timeout(
        connect=5.0,
        read=600.0,
        write=30.0,
        pool=5.0,
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    result = str(
        data.get("message", {}).get("content", "")
    ).strip()

    if not result:
        raise RuntimeError("Ollamaから空の回答が返されました。")

    total_seconds = round(
        data.get("total_duration", 0) / 1_000_000_000,
        2,
    )
    eval_count = int(data.get("eval_count", 0) or 0)
    eval_seconds = data.get("eval_duration", 0) / 1_000_000_000

    tokens_per_second = (
        round(eval_count / eval_seconds, 2)
        if eval_seconds
        else None
    )

    return result, {
        "total_seconds": total_seconds,
        "prompt_tokens": int(
            data.get("prompt_eval_count", 0) or 0
        ),
        "output_tokens": eval_count,
        "tokens_per_second": tokens_per_second,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=empty_context(),
    )


@app.post("/summarize", response_class=HTMLResponse)
async def summarize(
    request: Request,
    text: str = Form(""),
    summary_type: str = Form("standard"),
    strictness: str = Form("strict"),
    model: str = Form("qwen3:14b"),
    source_file: UploadFile | None = File(None),
):
    context = empty_context()
    context.update(
        {
            "text": text,
            "summary_type": summary_type,
            "strictness": strictness,
            "model": model,
        }
    )

    if summary_type not in SUMMARY_INSTRUCTIONS:
        context["error"] = "不明な要約形式が指定されました。"

    elif strictness not in STRICTNESS_INSTRUCTIONS:
        context["error"] = "不明な厳密さ設定が指定されました。"

    elif model not in AVAILABLE_MODELS:
        context["error"] = "許可されていないモデルが指定されました。"

    else:
        try:
            input_text = text.strip()

            source = {
                "kind": "text",
                "name": "直接入力",
                "pages": None,
                "chars": len(input_text),
                "method": "直接入力",
                "is_ocr": False,
                "ocr_seconds": None,
                "warning": None,
            }

            if source_file and source_file.filename:
                filename = Path(source_file.filename).name
                suffix = Path(filename).suffix.lower()
                file_bytes = await source_file.read()

                if not file_bytes:
                    raise ValueError("アップロードされたファイルが空です。")

                if suffix == ".pdf":
                    if len(file_bytes) > MAX_PDF_BYTES:
                        raise ValueError(
                            f"PDFが大きすぎます。現在は"
                            f"{MAX_PDF_BYTES // (1024 * 1024)}MBまでです。"
                        )

                    extracted_text, page_count = extract_pdf_text(
                        file_bytes
                    )

                    if pdf_needs_ocr(
                        extracted_text,
                        page_count,
                    ):
                        if not ndlocr_available():
                            raise RuntimeError(
                                "このPDFはスキャンPDFの可能性がありますが、"
                                "NDLOCR-Liteを実行できません。"
                                " `ndlocr-lite --help` を確認してください。"
                            )

                        with tempfile.TemporaryDirectory(
                            prefix="local-summary-ocr-"
                        ) as temp_dir_name:
                            temp_dir = Path(temp_dir_name)
                            input_path = temp_dir / "source.pdf"
                            output_dir = temp_dir / "output"
                            output_dir.mkdir()

                            input_path.write_bytes(file_bytes)

                            input_text, ocr_seconds = await asyncio.to_thread(
                                run_ndlocr,
                                input_path,
                                output_dir,
                                "pdf",
                            )

                        source = {
                            "kind": "pdf",
                            "name": filename,
                            "pages": page_count,
                            "chars": len(input_text),
                            "method": "NDLOCR-Lite",
                            "is_ocr": True,
                            "ocr_seconds": ocr_seconds,
                            "warning": (
                                "OCR結果には文字誤認識が含まれる可能性があります。"
                            ),
                        }

                    else:
                        input_text = extracted_text

                        source = {
                            "kind": "pdf",
                            "name": filename,
                            "pages": page_count,
                            "chars": len(input_text),
                            "method": "PyMuPDF",
                            "is_ocr": False,
                            "ocr_seconds": None,
                            "warning": None,
                        }

                elif suffix in IMAGE_SUFFIXES:
                    if len(file_bytes) > MAX_IMAGE_BYTES:
                        raise ValueError(
                            f"画像が大きすぎます。現在は"
                            f"{MAX_IMAGE_BYTES // (1024 * 1024)}MBまでです。"
                        )

                    if not ndlocr_available():
                        raise RuntimeError(
                            "画像OCRにはNDLOCR-Liteが必要です。"
                            " `ndlocr-lite --help` を確認してください。"
                        )

                    with tempfile.TemporaryDirectory(
                        prefix="local-summary-ocr-"
                    ) as temp_dir_name:
                        temp_dir = Path(temp_dir_name)
                        input_path = temp_dir / f"source{suffix}"
                        output_dir = temp_dir / "output"
                        output_dir.mkdir()

                        input_path.write_bytes(file_bytes)

                        input_text, ocr_seconds = await asyncio.to_thread(
                            run_ndlocr,
                            input_path,
                            output_dir,
                            "image",
                        )

                    source = {
                        "kind": "image",
                        "name": filename,
                        "pages": None,
                        "chars": len(input_text),
                        "method": "NDLOCR-Lite",
                        "is_ocr": True,
                        "ocr_seconds": ocr_seconds,
                        "warning": (
                            "OCR結果には文字誤認識が含まれる可能性があります。"
                        ),
                    }

                else:
                    supported = (
                        "PDF / JPG / JPEG / PNG / TIFF / TIF / "
                        "JP2 / BMP / WebP"
                    )
                    raise ValueError(
                        f"未対応のファイル形式です。対応形式: {supported}"
                    )

                # 抽出・OCR本文を画面上でも確認できるようにする。
                context["text"] = input_text

            if not input_text:
                raise ValueError(
                    "文章を入力するか、PDFまたは画像ファイルを選択してください。"
                )

            if len(input_text) > MAX_INPUT_CHARS:
                raise ValueError(
                    f"{len(input_text):,}文字を取得しましたが、"
                    f"現在の要約上限は{MAX_INPUT_CHARS:,}文字です。"
                    "長文分割要約は次の拡張で対応予定です。"
                )

            result, metrics = await summarize_with_ollama(
                input_text,
                summary_type,
                strictness,
                model,
                source["is_ocr"],
            )

            context["result"] = result
            context["metrics"] = metrics
            context["source"] = source

        except httpx.ConnectError:
            context["error"] = (
                "Ollamaに接続できませんでした。"
                " `systemctl status ollama` を確認してください。"
            )

        except httpx.TimeoutException:
            context["error"] = (
                "Ollamaの応答がタイムアウトしました。"
            )

        except httpx.HTTPStatusError as exc:
            context["error"] = (
                f"Ollama API error: HTTP "
                f"{exc.response.status_code}\n"
                f"{exc.response.text[:500]}"
            )

        except Exception as exc:
            context["error"] = str(exc)

        finally:
            if source_file:
                await source_file.close()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=context,
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ollama_url": OLLAMA_BASE_URL,
        "available_models": list(AVAILABLE_MODELS.keys()),
        "pdf_support": True,
        "image_support": True,
        "ocr_support": ndlocr_available(),
        "ocr_engine": "NDLOCR-Lite",
        "ocr_device": NDLOCR_DEVICE,
    }

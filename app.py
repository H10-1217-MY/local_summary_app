from __future__ import annotations

import os
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

MAX_INPUT_CHARS = 30_000
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 100
MIN_PDF_TEXT_CHARS = 80

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
- 該当項目がない場合は「なし」としてください。""",
    "normal": """【通常モード】
- 原文の内容を中心に、自然で読みやすく整理してください。
- 明示されていない内容を断定しないでください。
- 必要な場合だけ、推測であることを明示して補足してください。""",
    "helpful": """【補足ありモード】
- 原文を要約したうえで、役に立つ補足や次の行動案を追加して構いません。
- 原文由来の事実と、モデルによる補足・提案は明確に区別してください。""",
}

app = FastAPI(title="Local Summary App v3")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def default_model() -> str:
    return DEFAULT_MODEL if DEFAULT_MODEL in AVAILABLE_MODELS else "qwen3:14b"


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
        "max_pdf_pages": MAX_PDF_PAGES,
        "available_models": AVAILABLE_MODELS,
    }


def normalize_extracted_text(text: str) -> str:
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
                chunks.append(f"--- Page {page_number} ---\n{page_text}")

        return "\n\n".join(chunks).strip(), page_count

    finally:
        doc.close()


async def summarize_with_ollama(
    text: str,
    summary_type: str,
    strictness: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "あなたは日本語文書の要約担当です。"
                    "原文と推測を混同せず、指定された形式を守ってください。"
                    "PDF由来のページ区切り表記は文書構造の手掛かりとして扱ってください。"
                ),
            },
            {
                "role": "user",
                "content": (
                    STRICTNESS_INSTRUCTIONS[strictness]
                    + "\n\n"
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

    timeout = httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()

    result = str(data.get("message", {}).get("content", "")).strip()

    if not result:
        raise RuntimeError("Ollamaから空の回答が返されました。")

    total_seconds = round(data.get("total_duration", 0) / 1_000_000_000, 2)
    eval_count = int(data.get("eval_count", 0) or 0)
    eval_seconds = data.get("eval_duration", 0) / 1_000_000_000
    tokens_per_second = round(eval_count / eval_seconds, 2) if eval_seconds else None

    return result, {
        "total_seconds": total_seconds,
        "prompt_tokens": int(data.get("prompt_eval_count", 0) or 0),
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
    pdf_file: UploadFile | None = File(None),
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
            }

            if pdf_file and pdf_file.filename:
                filename = Path(pdf_file.filename).name

                if Path(filename).suffix.lower() != ".pdf":
                    raise ValueError("現在アップロードできるファイルはPDFのみです。")

                pdf_bytes = await pdf_file.read()

                if not pdf_bytes:
                    raise ValueError("PDFファイルが空です。")

                if len(pdf_bytes) > MAX_PDF_BYTES:
                    raise ValueError(
                        f"PDFが大きすぎます。現在は"
                        f"{MAX_PDF_BYTES // (1024 * 1024)}MBまでです。"
                    )

                input_text, page_count = extract_pdf_text(pdf_bytes)
                visible_chars = len("".join(input_text.split()))

                if visible_chars < MIN_PDF_TEXT_CHARS:
                    raise ValueError(
                        "PDF内部から十分な文字を抽出できませんでした。"
                        "スキャンPDFまたは画像主体のPDFの可能性があります。"
                        "この種類は次のOCR対応版で処理する予定です。"
                    )

                source = {
                    "kind": "pdf",
                    "name": filename,
                    "pages": page_count,
                    "chars": len(input_text),
                }
                context["text"] = input_text

            if not input_text:
                raise ValueError("文章を入力するか、PDFファイルを選択してください。")

            if len(input_text) > MAX_INPUT_CHARS:
                if source["kind"] == "pdf":
                    raise ValueError(
                        f"PDFから{len(input_text):,}文字を抽出しましたが、"
                        f"現在の要約上限は{MAX_INPUT_CHARS:,}文字です。"
                        "長文PDFの分割要約は今後の版で追加します。"
                    )

                raise ValueError(
                    f"入力が長すぎます。現在は{MAX_INPUT_CHARS:,}文字までです。"
                )

            result, metrics = await summarize_with_ollama(
                input_text,
                summary_type,
                strictness,
                model,
            )

            context["result"] = result
            context["metrics"] = metrics
            context["source"] = source

        except httpx.ConnectError:
            context["error"] = (
                "Ollamaに接続できませんでした。"
                " systemctl status ollama を確認してください。"
            )
        except httpx.TimeoutException:
            context["error"] = "Ollamaの応答がタイムアウトしました。"
        except httpx.HTTPStatusError as exc:
            context["error"] = (
                f"Ollama API error: HTTP {exc.response.status_code}\n"
                f"{exc.response.text[:500]}"
            )
        except Exception as exc:
            context["error"] = str(exc)
        finally:
            if pdf_file:
                await pdf_file.close()

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
        "ocr_support": False,
    }

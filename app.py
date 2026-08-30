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
LONG_SUMMARY_THRESHOLD_CHARS = 24_000
CHUNK_TARGET_CHARS = 12_000
CHUNK_OVERLAP_CHARS = 800
MAX_DOCUMENT_CHARS = 300_000
MAX_REDUCE_INPUT_CHARS = 24_000
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

SOURCE_FIDELITY_RULES = """【情報源の制約】
- この処理は入力文書の要約です。
- モデル自身の外部知識を、入力文書の事実として使用しないでください。
- 有名な作品・人物・制度だと推測できても、入力に明示されていない作者名、作品名、背景、年代、思想、評価などを補完しないでください。
- 入力に書かれていない情報は「記載なし」と扱ってください。
- 入力とモデル知識が食い違って見えても、モデル知識で訂正しないでください。
- 固有名詞、数値、日付、役職などは入力された表記を優先してください。
- 文学的解釈、批評、歴史的背景は、入力本文に明示されている場合を除き追加しないでください。
- 「ご希望があれば」「必要であれば」などの追加提案を書かないでください。
""".strip()

SUMMARY_INSTRUCTIONS = {
    "standard": "一般文書として内容を整理してください。",
    "technical": "技術文書・作業メモとして、実施内容、仕様、結果、問題点を重視して整理してください。",
    "meeting": "会議・打ち合わせ記録として、議題、決定事項、保留事項、担当者、期限を重視して整理してください。",
    "story": "物語・小説として、登場人物、出来事、時系列、転換点、結末を本文だけから整理してください。作者・作品背景・文学史・象徴・思想などの外部知識や独自解釈は追加しないでください。",
}

SUMMARY_LENGTH_INSTRUCTIONS = {
    "concise": "【要約の長さ: 簡潔】重要度の高い内容だけを残し、細部や重複を省いて大きく圧縮してください。",
    "standard": "【要約の長さ: 標準】主要な内容を落とさず、背景・条件・出来事・結論を適度に残してください。",
    "detailed": "【要約の長さ: 詳細】元文書の構造や主要な論点・出来事をできるだけ保持し、重要な節、条件、数値、人物関係、時系列、変更点などを省略しすぎないでください。",
}


def summary_output_format(summary_type: str, summary_length: str) -> str:
    if summary_type == "story":
        if summary_length == "concise":
            return """出力形式:
1. 短いあらすじ
2. 主な登場人物
3. 結末・到達点"""
        if summary_length == "detailed":
            return """出力形式:
1. 全体あらすじ
2. 主な登場人物と本文中で明示された関係
3. 物語の展開
   - 序盤
   - 中盤
   - 終盤
4. 主要な出来事・転換点
5. 登場人物の状態や立場の変化
6. 結末
7. 本文中で未解決のまま残る点"""
        return """出力形式:
1. あらすじ
2. 主な登場人物
3. 主要な出来事
4. 物語の流れ・転換点
5. 結末"""

    if summary_type == "technical":
        if summary_length == "concise":
            return """出力形式:
1. 概要
2. 重要な技術事項
3. 問題点・結論"""
        if summary_length == "detailed":
            return """出力形式:
1. 全体概要
2. 背景・目的
3. 使用した技術・手法
4. 仕様・条件・重要な数値
5. 実施内容・結果
6. 問題点・リスク
7. 未確認事項
8. 結論・次に行う作業"""
        return """出力形式:
1. 概要
2. 実施内容・確認結果
3. 技術・仕様上の重要事項
4. 問題点・リスク
5. 未確認事項
6. 次に行う作業"""

    if summary_type == "meeting":
        if summary_length == "concise":
            return """出力形式:
1. 会議要旨
2. 決定事項
3. 次の対応"""
        if summary_length == "detailed":
            return """出力形式:
1. 会議全体の要旨
2. 議題ごとの内容
3. 決定事項
4. 保留・未解決事項
5. 担当者
6. 期限
7. 次回までの対応
8. 発言や方針の変更があればその経緯"""
        return """出力形式:
1. 会議の要旨
2. 主な議題
3. 決定事項
4. 保留事項
5. 担当者と期限
6. 次回までの対応"""

    if summary_length == "concise":
        return """出力形式:
1. 3行要約
2. 重要事項（3〜5点）"""
    if summary_length == "detailed":
        return """出力形式:
1. 全体概要
2. 主要トピック・セクション別要約
3. 重要事項
4. 数値・条件・固有名詞
5. 結論・到達点
6. 未解決点
7. 次にやること（原文に明示されている場合のみ）"""
    return """出力形式:
1. 概要
2. 重要事項
3. 主要な論点・内容
4. 未解決点
5. 次にやること（原文に明示されている場合のみ）"""


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

app = FastAPI(title="Local Summary App v0.5.2")

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
        "summary_length": "standard",
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
        "long_summary_support": True,
        "long_summary_threshold_chars": LONG_SUMMARY_THRESHOLD_CHARS,
        "max_document_chars": MAX_DOCUMENT_CHARS,
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



def split_long_text(
    text: str,
    target_chars: int = CHUNK_TARGET_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """段落・ページ境界を優先して長文を分割する。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    units: list[str] = []

    for paragraph in paragraphs:
        if len(paragraph) <= target_chars:
            units.append(paragraph)
            continue

        start = 0
        while start < len(paragraph):
            end = min(start + target_chars, len(paragraph))

            if end < len(paragraph):
                candidates = [
                    paragraph.rfind(mark, start, end)
                    for mark in ("。", "！", "？", "\n")
                ]
                best = max(candidates)
                if best > start + target_chars // 2:
                    end = best + 1

            units.append(paragraph[start:end].strip())
            start = end

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for unit in units:
        extra = len(unit) + (2 if current else 0)

        if current and current_len + extra > target_chars:
            previous = "\n\n".join(current)
            chunks.append(previous.strip())

            current = []
            current_len = 0

            if overlap_chars > 0:
                overlap = previous[-overlap_chars:].strip()
                if overlap:
                    context = "[前チャンク末尾の参考文脈]\n" + overlap
                    current.append(context)
                    current_len = len(context)

        current.append(unit)
        current_len += len(unit) + (2 if len(current) > 1 else 0)

    if current:
        chunks.append("\n\n".join(current).strip())

    return [chunk for chunk in chunks if chunk]


async def call_ollama_text(
    messages: list[dict[str, str]],
    model: str,
    temperature: float = 0.1,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": temperature},
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

    result = str(data.get("message", {}).get("content", "")).strip()

    if not result:
        raise RuntimeError("Ollamaから空の回答が返されました。")

    return result


async def summarize_chunk(
    text: str,
    index: int,
    total: int,
    model: str,
    strictness: str,
    is_ocr: bool,
    summary_type: str = "standard",
) -> str:
    ocr_note = ""
    if is_ocr:
        ocr_note = (
            "この本文はOCR由来です。誤認識の可能性があるため、"
            "固有名詞や数値を文脈だけで勝手に修正しないでください。"
        )

    prompt = f"""
{SOURCE_FIDELITY_RULES}

{STRICTNESS_INSTRUCTIONS[strictness]}

{ocr_note}

これは全{total}チャンク中の{index}番目です。

文書タイプ: {summary_type}
{"物語・小説の場合は、人物、場所、出来事、時系列、関係、状態変化、転換点を特に保持してください。作者や作品背景は本文に明示されている場合のみ記録してください。" if summary_type == "story" else ""}

ここでは読みやすい要約文を作らず、
後段の統合処理で使用する「事実台帳」を作成してください。
明示がない項目は必ず「記載なし」としてください。

出力形式:

[主題・場面]
- ...

[明示された作品名・文書名]
- ...

[明示された作者・作成者・出典]
- ...

[人物・組織・対象]
- ...

[場所・舞台]
- ...

[人物関係・状態変化]
- ...

[出来事・事実]
- ...

[数値・日付・条件・制約]
- ...

[決定事項]
- ...

[未決定・未確認事項]
- ...

[次にやること]
- ...

[時系列・変更履歴]
- ...

[原文上で不明瞭な点]
- ...

重要:
- 入力にない作者名・作品名・背景知識を推測しない。
- 有名作品だと分かってもモデル自身の知識を使わない。
- 前チャンク末尾の参考文脈は重複確認用であり二重計上しない。
- 解釈・感想・批評を書かない。
- 挨拶や追加提案を書かない。

本文:
{text}
""".strip()

    return await call_ollama_text(
        [
            {
                "role": "system",
                "content": (
                    "あなたは長文要約の事実抽出担当です。"
                    "外部知識を使わず、入力本文に明示された情報だけを台帳化してください。"
                    "不足情報を推測で埋めることは禁止です。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        model,
        0.0,
    )


async def reduce_summaries(
    summaries: list[str],
    summary_type: str,
    summary_length: str,
    strictness: str,
    model: str,
) -> str:
    combined = "\n\n".join(
        f"--- Fact Ledger {i} ---\n{summary}"
        for i, summary in enumerate(summaries, start=1)
    )

    if len(combined) > MAX_REDUCE_INPUT_CHARS:
        groups = split_long_text(
            combined,
            target_chars=MAX_REDUCE_INPUT_CHARS,
            overlap_chars=0,
        )

        reduced: list[str] = []
        for i, group in enumerate(groups, start=1):
            reduced.append(
                await summarize_chunk(
                    group,
                    i,
                    len(groups),
                    model,
                    strictness,
                    False,
                    summary_type,
                )
            )

        return await reduce_summaries(
            reduced,
            summary_type,
            summary_length,
            strictness,
            model,
        )

    prompt = (
        SOURCE_FIDELITY_RULES
        + "\n\n"
        + STRICTNESS_INSTRUCTIONS[strictness]
        + "\n\n"
        + SUMMARY_INSTRUCTIONS[summary_type]
        + "\n\n"
        + SUMMARY_LENGTH_INSTRUCTIONS[summary_length]
        + "\n\n"
        + summary_output_format(summary_type, summary_length)
        + """

以下は同じ文書を分割して作成した「事実台帳」です。
最終要約は、この台帳に書かれている内容だけを材料にしてください。

統合ルール:
- モデル自身の作品・人物・歴史・制度などの知識を使用しない。
- 作者名や作品名は、台帳に明示されている場合のみ書く。
- 「記載なし」の情報を推測で埋めない。
- 固有名詞や数値が台帳間で食い違う場合、勝手に正解を選ばず不一致として扱う。
- 重複は1つにまとめる。
- 後半で変更・撤回されたことが明示されている場合は、最終状態が分かるように整理する。
- 文学作品でも、テーマ・象徴・作者思想を独自に追加しない。
- 挨拶や「ご希望があれば」等の提案を書かない。

事実台帳:
"""
        + combined
    )

    return await call_ollama_text(
        [
            {
                "role": "system",
                "content": (
                    "あなたは長文文書の最終要約担当です。"
                    "与えられた事実台帳以外の知識を使用してはいけません。"
                    "不足情報は不足したまま保持してください。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        model,
        0.0 if strictness == "strict" else 0.1,
    )


async def summarize_long_document(
    text: str,
    summary_type: str,
    summary_length: str,
    strictness: str,
    model: str,
    is_ocr: bool,
) -> tuple[str, dict[str, Any]]:
    started = time.perf_counter()
    chunks = split_long_text(text)

    chunk_summaries: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        chunk_summaries.append(
            await summarize_chunk(
                chunk,
                index,
                len(chunks),
                model,
                strictness,
                is_ocr,
                summary_type,
            )
        )

    result = await reduce_summaries(
        chunk_summaries,
        summary_type,
        summary_length,
        strictness,
        model,
    )

    return result, {
        "total_seconds": round(time.perf_counter() - started, 2),
        "prompt_tokens": None,
        "output_tokens": None,
        "tokens_per_second": None,
        "long_summary": True,
        "chunk_count": len(chunks),
        "input_chars": len(text),
    }


async def summarize_with_ollama(
    text: str,
    summary_type: str,
    summary_length: str,
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
                    SOURCE_FIDELITY_RULES
                    + "\n\n"
                    + STRICTNESS_INSTRUCTIONS[strictness]
                    + "\n\n"
                    + ocr_note
                    + "\n"
                    + SUMMARY_INSTRUCTIONS[summary_type]
                    + "\n\n"
                    + SUMMARY_LENGTH_INSTRUCTIONS[summary_length]
                    + "\n\n"
                    + summary_output_format(summary_type, summary_length)
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
    summary_length: str = Form("standard"),
    strictness: str = Form("strict"),
    model: str = Form("qwen3:14b"),
    source_file: UploadFile | None = File(None),
):
    context = empty_context()
    context.update(
        {
            "text": text,
            "summary_type": summary_type,
            "summary_length": summary_length,
            "strictness": strictness,
            "model": model,
        }
    )

    if summary_type not in SUMMARY_INSTRUCTIONS:
        context["error"] = "不明な要約形式が指定されました。"

    elif summary_length not in SUMMARY_LENGTH_INSTRUCTIONS:
        context["error"] = "不明な要約の長さが指定されました。"

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

            if len(input_text) > MAX_DOCUMENT_CHARS:
                raise ValueError(
                    f"{len(input_text):,}文字を取得しましたが、"
                    f"現在の文書上限は{MAX_DOCUMENT_CHARS:,}文字です。"
                )

            if len(input_text) > LONG_SUMMARY_THRESHOLD_CHARS:
                result, metrics = await summarize_long_document(
                    input_text,
                    summary_type,
                    summary_length,
                    strictness,
                    model,
                    source["is_ocr"],
                )
            else:
                result, metrics = await summarize_with_ollama(
                    input_text,
                    summary_type,
                    summary_length,
                    strictness,
                    model,
                    source["is_ocr"],
                )
                metrics["long_summary"] = False
                metrics["chunk_count"] = 1
                metrics["input_chars"] = len(input_text)

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

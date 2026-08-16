from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:14b")
MAX_INPUT_CHARS = 30_000

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

app = FastAPI(title="Local Summary App v2")
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
        "max_input_chars": MAX_INPUT_CHARS,
        "available_models": AVAILABLE_MODELS,
    }


async def summarize_with_ollama(text: str, summary_type: str, strictness: str, model: str):
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "あなたは日本語文書の要約担当です。原文と推測を混同せず、指定された形式を守ってください。",
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
        "options": {"temperature": 0.2 if strictness != "helpful" else 0.4},
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

    metrics = {
        "total_seconds": total_seconds,
        "prompt_tokens": int(data.get("prompt_eval_count", 0) or 0),
        "output_tokens": eval_count,
        "tokens_per_second": tokens_per_second,
    }
    return result, metrics


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context=empty_context())


@app.post("/summarize", response_class=HTMLResponse)
async def summarize(
    request: Request,
    text: Annotated[str, Form()],
    summary_type: Annotated[str, Form()] = "standard",
    strictness: Annotated[str, Form()] = "strict",
    model: Annotated[str, Form()] = "qwen3:14b",
):
    context = empty_context()
    context.update({"text": text, "summary_type": summary_type, "strictness": strictness, "model": model})
    clean_text = text.strip()

    if summary_type not in SUMMARY_INSTRUCTIONS:
        context["error"] = "不明な要約形式が指定されました。"
    elif strictness not in STRICTNESS_INSTRUCTIONS:
        context["error"] = "不明な厳密さ設定が指定されました。"
    elif model not in AVAILABLE_MODELS:
        context["error"] = "許可されていないモデルが指定されました。"
    elif not clean_text:
        context["error"] = "要約する文章を入力してください。"
    elif len(clean_text) > MAX_INPUT_CHARS:
        context["error"] = f"入力が長すぎます。現在は{MAX_INPUT_CHARS:,}文字までです。"
    else:
        try:
            result, metrics = await summarize_with_ollama(clean_text, summary_type, strictness, model)
            context["result"] = result
            context["metrics"] = metrics
        except httpx.ConnectError:
            context["error"] = "Ollamaに接続できませんでした。systemctl status ollama を確認してください。"
        except httpx.TimeoutException:
            context["error"] = "Ollamaの応答がタイムアウトしました。"
        except httpx.HTTPStatusError as exc:
            context["error"] = f"Ollama API error: HTTP {exc.response.status_code}\n{exc.response.text[:500]}"
        except Exception as exc:
            context["error"] = f"エラー: {exc}"

    return templates.TemplateResponse(request=request, name="index.html", context=context)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ollama_url": OLLAMA_BASE_URL,
        "available_models": list(AVAILABLE_MODELS.keys()),
    }

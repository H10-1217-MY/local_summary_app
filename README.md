# Local Summary App

Ollama + Qwen3 + PyMuPDF + NDLOCR-Lite を使用した、**ローカル環境で動作する日本語文書要約Webアプリ**です。

テキスト、PDF、スキャンPDF、画像を入力し、必要に応じてOCRを行ったうえで、ローカルLLMによる要約を生成します。

外部の生成AI APIへ文書を送信せず、PC内で処理を完結させることを目的としています。

---

## Features

* テキストの直接入力
* PDFからのテキスト抽出
* スキャンPDFのOCR
* 画像ファイルのOCR
* Qwen3 8B / 14B の切り替え
* 要約形式の切り替え
* 要約の厳密さの切り替え
* 要約結果のコピー
* Markdown形式での保存
* OCR処理時間、LLM生成時間、token/s の表示

### 対応する要約形式

* 標準要約
* 技術メモ
* 会議メモ
* 短縮要約

### 要約の厳密さ

* **原文忠実**

  * 入力文に書かれている内容を中心に要約
  * 原文にない情報をなるべく補わない

* **通常**

  * 読みやすさを重視して整理

* **補足あり**

  * 原文の要約に加え、必要に応じて補足や提案を追加

---

## Screenshot

<!-- 公開用スクリーンショットを追加する場合は以下のように配置できます。

![Local Summary App](docs/images/screenshot.png)

-->

---

## Processing Flow

```text
Text
  │
  └─────────────────────────────┐
                                │
PDF                             │
  │                             │
  ├─ PyMuPDFで文字抽出          │
  │        │                    │
  │        ├─ 十分取得できる ───┤
  │        │                    │
  │        └─ 十分取得できない  │
  │                  │          │
  │             NDLOCR-Lite     │
  │                  │          │
  └──────────────────┤          │
                     │          │
Image                │          │
  │                  │          │
NDLOCR-Lite ─────────┘          │
                                │
                                ▼
                         共通テキスト
                                │
                                ▼
                          Ollama / Qwen3
                                │
                                ▼
                              要約
```

PDFにテキスト情報が含まれている場合は、PyMuPDFによる直接抽出を優先します。

十分なテキストを取得できないスキャンPDFや画像については、NDLOCR-Liteを使用してOCRを行います。

---

## Supported Input

### Text

ブラウザ上の入力欄へ直接文章を貼り付けられます。

### PDF

* 通常のテキストPDF
* スキャンPDF

### Image

以下の形式に対応しています。

* JPG / JPEG
* PNG
* TIFF / TIF
* JP2
* BMP
* WebP

---

## Requirements

動作確認環境の例:

* Ubuntu 24.04
* Python 3.11
* Ollama
* Qwen3 8B / 14B
* NDLOCR-Lite

GPUはLLM推論に使用できます。

NDLOCR-LiteはCPUでも動作します。

---

## Installation

### 1. Ollama

Ollamaをインストールしてください。

インストール後、使用するモデルを取得します。

```bash
ollama pull qwen3:8b
ollama pull qwen3:14b
```

確認:

```bash
ollama list
```

Ollamaサービスの確認:

```bash
systemctl status ollama --no-pager
```

---

### 2. NDLOCR-Lite

OCRには、国立国会図書館が公開している **NDLOCR-Lite** を使用します。

```bash
cd ~/projects

git clone https://github.com/ndl-lab/ndlocr-lite.git

cd ndlocr-lite

uv tool install .
```

インストール確認:

```bash
ndlocr-lite --help
```

NDLOCR-LiteはLocal Summary AppのPython環境とは分離し、CLIツールとして利用します。

---

### 3. Local Summary App

リポジトリを取得します。

```bash
git clone https://github.com/H10-1217-MY/local_summary_app.git

cd local_summary_app
```

Condaを使用する場合:

```bash
conda create -n local-summary python=3.11 -y

conda activate local-summary

pip install -r requirements.txt
```

すでに環境を作成している場合:

```bash
conda activate local-summary

pip install -r requirements.txt
```

---

## Run

アプリを起動します。

```bash
uvicorn app:app --reload
```

ブラウザで以下を開きます。

```text
http://127.0.0.1:8000
```

---

## Health Check

```bash
curl http://127.0.0.1:8000/health
```

NDLOCR-Liteが正常に認識されている場合、以下のような情報が返ります。

```json
{
  "status": "ok",
  "pdf_support": true,
  "image_support": true,
  "ocr_support": true,
  "ocr_engine": "NDLOCR-Lite",
  "ocr_device": "cpu"
}
```

---

## Environment Variables

### Ollama

```bash
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_MODEL=qwen3:14b
```

### NDLOCR-Lite

```bash
export NDLOCR_COMMAND=ndlocr-lite
export NDLOCR_DEVICE=cpu
export NDLOCR_TIMEOUT_SECONDS=1800
```

---

## Current Limits

現在は以下の制限を設けています。

* PDF: 最大25MB
* 画像: 最大20MB
* PDF: 最大100ページ
* LLMへ投入する文章: 最大30,000文字

30,000文字を超える文書の分割要約は、今後対応予定です。

---

## Privacy

アップロードされたPDFや画像は、一時ディレクトリ上で処理されます。

現在の実装では、入力ファイルをアプリ側で永続保存しません。

文書処理と要約はローカル環境上で実行されます。

ただし、利用環境や追加設定を変更した場合の通信については、各利用者側で確認してください。

---

## OCR Notes

OCR結果には文字認識の誤りが含まれる可能性があります。

Local Summary Appでは、OCR結果に対して以下の軽い前処理を行います。

* 不自然な途中改行の整理
* 空行の整理
* 文章構造の簡易的な保持

誤認識した単語そのものを、アプリ側で推測して自動修正する処理は行っていません。

OCR由来の文章をLLMへ送る際には、OCR誤認識の可能性があることをモデルへ通知しています。

---

## Models

現在、画面から以下を切り替えられます。

### Qwen3 8B

* 軽量
* 高速
* 短い文章や簡易要約向け

### Qwen3 14B

* 8Bより高品質な回答を期待できる
* 技術文書や比較的複雑な文章向け

実行可能なモデルサイズや速度は、PCのGPU・VRAM・RAM構成によって異なります。

---

## Tech Stack

* Python
* FastAPI
* Jinja2
* Ollama
* Qwen3
* PyMuPDF
* NDLOCR-Lite
* HTML
* CSS
* JavaScript

---

## Third-Party Software

### NDLOCR-Lite

OCR機能には、国立国会図書館が公開しているNDLOCR-Liteを外部CLIとして使用します。

* Project: NDLOCR-Lite
* Developer / Publisher: National Diet Library, Japan
* Repository: https://github.com/ndl-lab/ndlocr-lite
* License: CC BY 4.0

詳細は以下を参照してください。

```text
THIRD_PARTY_NOTICES.md
```

NDLOCR-Lite本体は本リポジトリには含まれておらず、別途インストールします。

各依存ライブラリには、それぞれ異なるライセンスが適用される場合があります。

---

## Roadmap

今後、以下の機能を追加する予定です。

* 長文PDFの分割要約
* Markdown表示の改善
* 音声ファイルの文字起こし
* kotoba-whisper系モデルとの連携
* 動画からの音声文字起こし
* 要約履歴
* 文書検索
* RAGによるローカル文書QA

最終的には、

```text
Text
PDF
Image
Audio
Video
  ↓
Text Conversion
  ↓
Local LLM
  ↓
Summary / Search / QA
```

のように、複数形式の文書・メディアをローカル環境で処理できるアプリを目指します。

---

## License

このプロジェクトの自作コード部分には、リポジトリ内の `LICENSE` に記載されたライセンスが適用されます。

サードパーティ製ソフトウェアについては、それぞれのライセンスが適用されます。

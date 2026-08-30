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

- 長文PDFの分割要約
- Markdownレンダリング
- 音声 / 動画文字起こし
- kotoba-whisper系の統合HEAD
- 履歴 / 検索


## Long Document Summarization (v0.5)

24,000文字を超える入力は、自動的に長文分割要約へ切り替わります。

```text
Long Document
    ↓
段落・ページ境界を優先して分割
    ↓
Chunkごとに重要事項を抽出
    ↓
中間要約
    ↓
重複除去・変更履歴整理
    ↓
Final Summary
```

現在の設定:

- 長文判定: 24,000文字超
- 1チャンク目安: 12,000文字
- チャンク間オーバーラップ: 800文字
- 文書全体上限: 300,000文字
- 段落・PDFページ境界を優先
- 中間要約では事実、数値、条件、決定事項を優先
- 最終統合で重複を整理
- 後半で変更された事項は最終状態が分かるように統合

単純にコンテキスト上限を大きくするのではなく、
小～中規模のローカルLLMでも長文を扱いやすくすることを目的としています。


## Source Fidelity Improvements (v0.5.1)

長文要約時に、モデル自身の一般知識や既知作品の情報が混入する問題を抑えるため、
中間処理を「文章要約」から「事実台帳」に変更しました。

```text
Long Document
    ↓
Chunk分割
    ↓
各Chunkから事実台帳を作成
    ├─ 明示された作品名
    ├─ 明示された作者・出典
    ├─ 人物・組織
    ├─ 出来事
    ├─ 数値・条件
    ├─ 決定事項
    ├─ 未確認事項
    └─ 時系列・変更履歴
    ↓
事実台帳だけを材料に最終要約
```

主な変更:

- モデル自身の外部知識を要約へ混ぜないよう指示を強化
- 有名作品でも作者名や背景を自動補完しない
- 入力にない情報は「記載なし」として保持
- 固有名詞や数値の不一致を勝手に訂正しない
- 文学作品でも依頼されていない批評・作者思想・歴史背景を追加しない
- 不要な「ご希望があれば」等の提案を抑制

特に、長文を複数チャンクへ分割した際に、
圧縮された中間情報をモデル自身の知識で補完してしまう問題を抑えることを目的としています。


## Summary Length & Document Modes (v0.5.2)

要約形式と要約の長さを別々に選択できます。

### 要約形式

- 標準
- 技術文書
- 会議メモ
- 物語・小説

### 要約の長さ

- 簡潔
- 標準
- 詳細

例:

```text
技術文書 + 簡潔
→ 主要な結論だけ確認

技術文書 + 詳細
→ 主要トピック、仕様、数値、結果まで保持

物語・小説 + 標準
→ 登場人物、出来事、流れ、転換点、結末を整理
```

### 物語・小説モード

物語では、業務文書とは異なる情報構造を扱います。

主に以下を保持します。

- 登場人物
- 場所・舞台
- 人物関係
- 出来事
- 時系列
- 転換点
- 登場人物の状態変化
- 結末

作者、作品背景、文学史、思想、象徴などについては、
入力本文に明示されていない限りモデル自身の知識を補完しない方針です。

今後は同じ仕組みで、論文、契約書、ニュースなどの文書タイプを追加しやすい構成を目指します。

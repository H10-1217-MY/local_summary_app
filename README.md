# Local Summary App

Ollama + Qwen3を使う、ローカル日本語要約Webアプリです。

## v4

PDFと画像のOCR入力に対応しました。

### 入力

- テキスト直接入力
- PDF
- JPG / JPEG
- PNG
- TIFF / TIF
- JP2
- BMP
- WebP

### 読み取りフロー

```text
PDF
├─ PyMuPDFで十分な文字を抽出できる
│  └─ そのまま要約
│
└─ 文字をほとんど抽出できない
   └─ NDLOCR-Lite
      └─ OCRテキスト
         └─ 要約

画像
└─ NDLOCR-Lite
   └─ OCRテキスト
      └─ 要約
```

## 前提

### Ollama

```bash
ollama list
systemctl status ollama --no-pager
```

例:

```bash
ollama pull qwen3:8b
ollama pull qwen3:14b
```

### NDLOCR-Lite

NDLOCR-Liteは要約アプリのPython環境とは分離してCLIとして利用します。

```bash
git clone https://github.com/ndl-lab/ndlocr-lite.git
cd ndlocr-lite
uv tool install .
```

確認:

```bash
ndlocr-lite --help
```

v4はPATH上の `ndlocr-lite` を自動検出します。

## local-summary環境

```bash
conda activate local-summary
pip install -r requirements.txt
```

## 起動

```bash
uvicorn app:app --reload
```

ブラウザ:

```text
http://127.0.0.1:8000
```

## 確認

```bash
curl http://127.0.0.1:8000/health
```

OCRが認識されていれば:

```json
{
  "ocr_support": true,
  "ocr_engine": "NDLOCR-Lite",
  "ocr_device": "cpu"
}
```

のように表示されます。

## 環境変数

Ollama:

```bash
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_MODEL=qwen3:14b
```

NDLOCR-Lite:

```bash
export NDLOCR_COMMAND=ndlocr-lite
export NDLOCR_DEVICE=cpu
export NDLOCR_TIMEOUT_SECONDS=1800
```

## v4の仕様

- PDF最大25MB
- 画像最大20MB
- PDF最大100ページ
- LLMへ投入する本文は最大30,000文字
- PDFはまずPyMuPDFで文字抽出
- 文字量が少ないPDFだけNDLOCR-Liteへ自動フォールバック
- 画像はNDLOCR-LiteでOCR
- OCR処理は一時ディレクトリで実行
- アップロードされた原本は永続保存しない
- OCR本文は軽い改行整理のみを行う
- OCR誤認識そのものをアプリ側で推測修正しない

## NDLOCR-Liteについて

OCRには国立国会図書館が公開する NDLOCR-Lite を利用します。

- Repository: https://github.com/ndl-lab/ndlocr-lite
- License: CC BY 4.0

NDLOCR-Lite本体とその依存ライブラリのライセンスについては、
NDLOCR-Lite公式リポジトリを確認してください。

## 今後

- 長文PDFの分割要約
- Markdownレンダリング
- 音声 / 動画文字起こし
- kotoba-whisper系の統合
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

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

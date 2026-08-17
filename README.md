# Local Summary App

Ollamaを使うローカル日本語要約Webアプリです。

## v3

v2を土台に、PDF入力を追加しました。

### 現在できること

- テキスト貼り付け
- PDFアップロード
- PDF内部のテキスト抽出
- Qwen3 8B / 14B 切替
- 原文忠実 / 通常 / 補足あり
- 標準 / 技術メモ / 会議メモ / 短縮要約
- 要約結果コピー
- Markdown保存
- 応答時間 / token/s 表示

### PDF仕様

- PyMuPDFでPDF内部の文字情報を抽出
- 最大25MB
- 最大100ページ
- 現在のLLM投入上限は30,000文字
- PDFが30,000文字を超えた場合はエラー表示
- 文字がほとんど取れないPDFは「スキャンPDF・画像PDFの可能性あり」と表示
- OCRはまだ行わない

## 更新

既存のGitHub管理フォルダに、このZIPの中身を上書きしてください。

```bash
conda activate local-summary
pip install -r requirements.txt
uvicorn app:app --reload
```

v3では `PyMuPDF` が追加されます。

## 次の予定

```text
PDF
├─ テキスト抽出できる → PyMuPDF → 要約
└─ テキスト抽出できない → OCR → 要約
```

その後は画像OCR、長文PDF分割要約、音声文字起こし、kotoba-whisper系を追加予定です。

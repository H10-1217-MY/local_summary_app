# Local Summary App v2

## 追加機能
- Qwen3 8B / 14B 切替
- 原文忠実 / 通常 / 補足あり
- 要約結果コピー
- Markdown保存
- 応答時間 / token/s 表示

## 起動
```bash
conda activate local-summary
pip install -r requirements.txt
uvicorn app:app --reload
```

ブラウザ:
```text
http://127.0.0.1:8000
```

## モデル確認
```bash
ollama list
```

必要なら:
```bash
ollama pull qwen3:8b
ollama pull qwen3:14b
```

## 次の拡張
1. PDFテキスト抽出
2. スキャンPDF・画像OCR
3. 音声文字起こし
4. 入力種別の自動振り分け
5. 長文分割要約
6. 履歴・検索

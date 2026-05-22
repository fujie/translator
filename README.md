# Realtime Translate

Zoom / Teams / Google Meet などの会議ツールで使える、GPT-4o Realtime API を利用したリアルタイム音声翻訳ツール。macOS メニューバーアプリとして動作する。

## 機能

| 機能 | 説明 |
|---|---|
| マイク翻訳 (JP→EN) | 日本語で話すと英語に翻訳してリモートへ送信 |
| マイクパススルー | 日本語をそのままリモートへ送信 |
| スピーカー翻訳 (自動検出) | 英語→日本語に翻訳、日本語はそのまま再生 |
| 翻訳ログウィンドウ | 原文・翻訳テキストをリアルタイム表示・エクスポート |

---

## 必要なもの

- macOS 13 (Ventura) 以降
- Python 3.11 以降
- [BlackHole 2ch + 16ch](https://existential.audio/blackhole/)
- OpenAI API キー（GPT-4o Realtime API へのアクセス権）

---

## セットアップ

### 1. BlackHole をインストール

```bash
brew install blackhole-2ch blackhole-16ch
```

または [公式サイト](https://existential.audio/blackhole/) からインストーラーを入手。

### 2. Python 依存関係をインストール

```bash
cd /Users/naohirofujie/localrepos/Translate
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. アプリを起動

```bash
source .venv/bin/activate
python app/main.py
```

初回起動時に API キーの入力を促すダイアログが表示される。

---

## 会議ツール側の設定

### マイク切り替え

| 会議ツールのマイク設定 | 動作 |
|---|---|
| `BlackHole 2ch` | 日本語をそのまま送信（パススルー） |
| `BlackHole 16ch` | 日本語→英語に翻訳して送信 |

### スピーカー切り替え

| 会議ツールのスピーカー設定 | 動作 |
|---|---|
| `BlackHole 2ch` | 自動言語検出: 英語→日本語翻訳、日本語→そのまま |
| 実スピーカー (System Default 等) | 翻訳なし、原音声をそのまま聴く |

---

## アーキテクチャ

```
【マイク側】
[実マイク] → App
               ├─ パススルー ──→ BlackHole 2ch  → 会議ツールが「マイクA」として読む
               └─ GPT JP→EN ──→ BlackHole 16ch → 会議ツールが「マイクB」として読む

【スピーカー側】
会議ツール → BlackHole 2ch (playback)
                ↓ App が capture 側から読む
             GPT 自動言語検出
                ├─ 英語 → 日本語翻訳 → 実スピーカー
                └─ 日本語 → パススルー → 実スピーカー
```

---

## 設定ファイル

`config/settings.json` に以下の項目が保存される（Settings… ダイアログから変更可）:

```json
{
  "openai_api_key": "sk-...",
  "input_device": "",
  "output_device": "",
  "mic_passthrough_device": "BlackHole 2ch",
  "mic_translated_device": "BlackHole 16ch",
  "speaker_capture_device": "BlackHole 2ch",
  "log_max_entries": 200
}
```

`input_device` / `output_device` が空の場合はシステムデフォルトを使用。

---

## トラブルシューティング

**BlackHole が会議ツールに表示されない**
→ macOS の「サウンド」環境設定で BlackHole デバイスが認識されているか確認。認識されていない場合はインストールし直し、PC を再起動。

**翻訳音声が出ない**
→ スピーカーパイプラインが ON になっているか確認。会議ツールのスピーカーが `BlackHole 2ch` に設定されているか確認。

**マイクに BlackHole が表示されない**
→ `brew install blackhole-2ch blackhole-16ch` を再実行し、ログアウト→ログインを試す。

**API エラーが出る**
→ ターミナルのログを確認。API キーが正しいか、GPT-4o Realtime API へのアクセス権があるかを確認。

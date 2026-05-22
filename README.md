# Realtime Translate

Zoom / Teams / Google Meet などの会議ツールで使える、GPT Realtime API を利用したリアルタイム音声翻訳ツール。
**macOS / Windows** のシステムトレイアプリとして動作する。

## 機能

| 機能 | 説明 |
|---|---|
| マイク翻訳 (JP→EN) | 日本語で話すと英語に翻訳してリモートへ送信 |
| マイクパススルー | 日本語をそのままリモートへ送信 |
| スピーカー翻訳 (自動検出) | 英語→日本語に翻訳、日本語はそのまま再生 |
| 翻訳ログウィンドウ | 原文・翻訳テキストをリアルタイム表示・エクスポート |

---

## 必要なもの

| | macOS | Windows |
|---|---|---|
| OS | macOS 13 (Ventura) 以降 | Windows 10 / 11 |
| Python | 3.11 以降 | 3.11 以降 |
| 仮想オーディオデバイス | [BlackHole 2ch + 16ch](https://existential.audio/blackhole/) | [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) |
| OpenAI API キー | GPT Realtime API へのアクセス権が必要 | 同左 |

---

## セットアップ

### macOS

#### 1. BlackHole をインストール

```bash
brew install blackhole-2ch blackhole-16ch
```

または [公式サイト](https://existential.audio/blackhole/) からインストーラーを入手。

#### 2. Python 依存関係をインストール

```bash
git clone https://github.com/fujie/translator.git
cd translator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 3. 設定ファイルを作成

```bash
cp config/settings.example.json config/settings.json
# config/settings.json を開いて openai_api_key を入力
```

#### 4. アプリを起動

```bash
source .venv/bin/activate
python app/main.py
```

---

### Windows

#### 1. VB-Audio Virtual Cable をインストール

[https://vb-audio.com/Cable/](https://vb-audio.com/Cable/) から `VB-Cable_Driver_Pack*.zip` をダウンロードしてインストール。

インストール後、以下の 2 つのデバイスが使えるようになります：
- **CABLE Input** (仮想スピーカー / output)
- **CABLE Output** (仮想マイク / input)

#### 2. Python 依存関係をインストール

```powershell
git clone https://github.com/fujie/translator.git
cd translator
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

#### 3. 設定ファイルを作成

```powershell
copy config\settings.example.json config\settings.json
# config\settings.json を開いて openai_api_key を入力、
# デバイス名を Windows 環境に合わせて変更（下記参照）
```

Windows での `settings.json` デバイス名例：

```json
{
  "openai_api_key": "sk-...",
  "realtime_model": "gpt-realtime",
  "input_device": "",
  "output_device": "",
  "mic_passthrough_device": "",
  "mic_translated_device": "CABLE Input",
  "speaker_capture_device": "CABLE Output"
}
```

#### 4. アプリを起動

```powershell
.venv\Scripts\activate
python app\main.py
```

タスクバーの通知領域（システムトレイ）にアイコンが表示されます。

---

## 会議ツール側の設定

### macOS（BlackHole）

| 会議ツールのマイク設定 | 動作 |
|---|---|
| `BlackHole 2ch` | 日本語をそのまま送信（パススルー） |
| `BlackHole 16ch` | 日本語→英語に翻訳して送信 |

| 会議ツールのスピーカー設定 | 動作 |
|---|---|
| `BlackHole 2ch` | 自動言語検出: 英語→日本語翻訳、日本語→そのまま |
| 実スピーカー | 翻訳なし、原音声をそのまま聴く |

### Windows（VB-Audio Virtual Cable）

| 会議ツールのマイク設定 | 動作 |
|---|---|
| `CABLE Output` | 翻訳済み音声を Zoom マイクとして送信 |

| 会議ツールのスピーカー設定 | 動作 |
|---|---|
| `CABLE Input` | 英語→日本語翻訳、日本語→そのまま再生 |

---

## アーキテクチャ

```
【マイク側】
[実マイク] → App
               └─ GPT JP→EN ──→ 仮想マイク (BlackHole 16ch / CABLE Input)
                                   → 会議ツールが翻訳済み英語を送信

【スピーカー側】
会議ツール → 仮想スピーカー (BlackHole 2ch / CABLE Output) に出力
                ↓ App が input 側から capture
             GPT 自動言語検出
                ├─ 英語 → 日本語翻訳 → 実スピーカー
                └─ 日本語 → パススルー → 実スピーカー
```

---

## 設定ファイル

`config/settings.json` に以下の項目が保存される（Settings ダイアログから変更可）:

| キー | 説明 |
|---|---|
| `openai_api_key` | OpenAI API キー |
| `realtime_model` | 使用モデル（`gpt-realtime` 推奨） |
| `input_device` | 実マイクのデバイス名（空=システムデフォルト） |
| `output_device` | 実スピーカーのデバイス名（空=システムデフォルト） |
| `mic_translated_device` | 翻訳音声の送り先（仮想マイク） |
| `mic_passthrough_device` | パススルー先（不要なら空） |
| `speaker_capture_device` | 会議音声のキャプチャ元（仮想スピーカー） |

---

## トラブルシューティング

**仮想デバイスが会議ツールに表示されない（macOS）**
→ `brew install blackhole-2ch blackhole-16ch` 後、ログアウト→ログインを試す。

**仮想デバイスが会議ツールに表示されない（Windows）**
→ VB-Audio のインストーラーを「管理者として実行」してからPCを再起動。

**翻訳音声が出ない**
→ トレイアイコンから「🔊 Speaker: ON」になっているか確認。
→ 会議ツールのスピーカーが仮想スピーカーデバイスになっているか確認。

**API エラーが出る**
→ ターミナルのログを確認。API キーが正しいか、GPT Realtime API へのアクセス権があるか確認。
→ モデル名を `gpt-realtime` に設定（Settings から変更可）。

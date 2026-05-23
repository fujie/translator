# Realtime Translate

Zoom / Teams / Google Meet などの会議ツールで使える、GPT Realtime API を利用したリアルタイム音声翻訳ツール。
**macOS / Windows** のシステムトレイアプリとして動作する。

## 機能

| 機能 | 説明 |
|---|---|
| マイク翻訳 (JP→EN) | 日本語で話すと英語に翻訳してリモートへ送信 |
| マイクパススルー | 日本語をそのままリモートへ送信 |
| スピーカー翻訳 | リモート音声を翻訳して再生（モデルにより動作が異なる→下記参照） |
| 翻訳ログウィンドウ | 原文・翻訳テキストをリアルタイム表示・エクスポート |

---

## モデル選択ガイド

Settings ダイアログでは **Mic Model**（マイク側）と **Speaker Model**（スピーカー側）を個別に設定できる。
シナリオに合わせて以下の表を参考に選択すること。

### シナリオ別推奨設定

| シナリオ | Mic Model | Speaker Model |
|---|---|---|
| **リモート話者が英語のみ** | `gpt-realtime-translate` | `gpt-realtime-translate` |
| **リモート話者が英語・日本語混在** | `gpt-realtime-translate` | `gpt-realtime-2`（推奨）または `gpt-realtime` |

### 利用可能なモデル

| モデル | 世代 | スピーカー側の動作 | 備考 |
|---|---|---|---|
| `gpt-realtime-translate` | — | 設定した言語へ**一方向翻訳のみ** | 制約あり（下記参照） |
| `gpt-realtime` | GPT-4o | 言語を自動検出し翻訳 or パススルー | コンテキスト 32K |
| `gpt-realtime-2` | **GPT-5** | 言語を自動検出し翻訳 or パススルー | コンテキスト 128K、**精度 +15.2%** ★推奨 |
| `gpt-realtime-mini` | GPT-4o mini | 言語を自動検出し翻訳 or パススルー | 低コスト・低レイテンシ |

### ⚠️ 制約事項：`gpt-realtime-translate` はスピーカー側で言語を自動判定できない

`gpt-realtime-translate` は `/v1/realtime/translations` エンドポイントを使用する**一方向翻訳専用モデル**であり、
セッション設定で指定した `audio.output.language`（出力言語）へ**常に翻訳しようとする**。

- リモート話者が **英語のみ** → English→Japanese に翻訳 ✅
- リモート話者が **日本語** → Japanese→Japanese を翻訳しようとするため**無音または不正確な出力**になる ❌

このため、**英語・日本語が混在する会議では Speaker Model に `gpt-realtime-translate` を使用してはならない**。
混在シナリオでは `gpt-realtime-2` または `gpt-realtime` を使用すること。これらは SPEAKER_SYSTEM_PROMPT により
言語を自動検出し「英語→日本語翻訳 / 日本語→パススルー」を正しく処理する。

なお、`gpt-realtime-translate` の `/v1/realtime/translations` エンドポイントは現時点で
`audio.output.language` 以外のパラメータ（入力言語ヒント・VAD 設定など）を受け付けない。

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

Windows での `settings.json` デバイス名例（英語・日本語混在シナリオ）：

```json
{
  "openai_api_key": "sk-...",
  "realtime_model": "gpt-realtime-translate",
  "speaker_model": "gpt-realtime-2",
  "input_device": "",
  "output_device": "",
  "mic_passthrough_device": "",
  "mic_translated_device": "CABLE Input",
  "speaker_capture_device": "CABLE Output"
}
```

英語のみシナリオでは `"speaker_model": "gpt-realtime-translate"` に変更する。

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
| `BlackHole 2ch` | App がキャプチャして翻訳処理（Speaker Model の設定に従う） |
| 実スピーカー | 翻訳なし、原音声をそのまま聴く |

### Windows（VB-Audio Virtual Cable）

| 会議ツールのマイク設定 | 動作 |
|---|---|
| `CABLE Output` | 翻訳済み音声を Zoom マイクとして送信 |

| 会議ツールのスピーカー設定 | 動作 |
|---|---|
| `CABLE Input` | App がキャプチャして翻訳処理（Speaker Model の設定に従う） |

---

## アーキテクチャ

```
【マイク側】  Mic Model = gpt-realtime-translate（固定推奨）
[実マイク] → App
               └─ GPT JP→EN ──→ 仮想マイク (BlackHole 16ch / CABLE Input)
                                   → 会議ツールが翻訳済み英語を送信

【スピーカー側】  Speaker Model に応じて動作が変わる
会議ツール → 仮想スピーカー (BlackHole 2ch / CABLE Output) に出力
                ↓ App が input 側から capture

  [英語のみシナリオ]  Speaker Model = gpt-realtime-translate
     英語 → 日本語翻訳 → 実スピーカー

  [混在シナリオ]     Speaker Model = gpt-realtime-2 / gpt-realtime
     言語自動検出
     ├─ 英語 → 日本語翻訳 → 実スピーカー
     └─ 日本語 → パススルー → 実スピーカー
```

---

## 設定ファイル

`config/settings.json` に以下の項目が保存される（Settings ダイアログから変更可）:

| キー | 説明 | デフォルト |
|---|---|---|
| `openai_api_key` | OpenAI API キー | — |
| `realtime_model` | Mic Model（マイク側モデル） | `gpt-realtime-translate` |
| `speaker_model` | Speaker Model（スピーカー側モデル） | `gpt-realtime-2` |
| `input_device` | 実マイクのデバイス名（空=システムデフォルト） | `""` |
| `output_device` | 実スピーカーのデバイス名（空=システムデフォルト） | `""` |
| `mic_translated_device` | 翻訳音声の送り先（仮想マイク） | `BlackHole 16ch` |
| `mic_passthrough_device` | パススルー先（不要なら空） | `""` |
| `speaker_capture_device` | 会議音声のキャプチャ元（仮想スピーカー） | `BlackHole 2ch` |

---

## トラブルシューティング

**仮想デバイスが会議ツールに表示されない（macOS）**
→ `brew install blackhole-2ch blackhole-16ch` 後、ログアウト→ログインを試す。

**仮想デバイスが会議ツールに表示されない（Windows）**
→ VB-Audio のインストーラーを「管理者として実行」してからPCを再起動。

**翻訳音声が出ない**
→ トレイアイコンから「🔊 Speaker: ON」になっているか確認。
→ 会議ツールのスピーカーが仮想スピーカーデバイスになっているか確認。

**リモートが日本語で話しているのに無音になる**
→ Speaker Model が `gpt-realtime-translate` になっている可能性がある。
→ Settings を開き、Speaker Model を `gpt-realtime-2` または `gpt-realtime` に変更する。
→ 詳細は「モデル選択ガイド」の制約事項を参照。

**API エラー `unknown_parameter` が出る**
→ `gpt-realtime-translate` に対し非対応パラメータを送信している可能性がある。
→ アプリを最新版に更新してから再試行する。

**API エラー `invalid_api_key` / `permission_denied` が出る**
→ API キーが正しいか、GPT Realtime API へのアクセス権があるか確認する。

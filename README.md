# Rip Tower

CDリッピング + メタデータ管理の Docker アプリ。複数の USB CD ドライブを搭載した物理マシンで動作し、WebUI からリッピングの開始・メタデータ確認・編集・承認を行う。

![Rip Tower Dashboard](https://img.shields.io/badge/status-active-brightgreen) ![Python 3.12](https://img.shields.io/badge/python-3.12-blue) ![React 19](https://img.shields.io/badge/react-19-61dafb) ![Docker](https://img.shields.io/badge/docker-compose-2496ed)

## なぜ作ったか

もともと OpenClaw（チャット AI）のスキルとして CD リッピングを行っていたが、以下の問題があった：

- **複数ドライブの同時リッピングで状態管理が破綻する** — チャット UI では3台同時の進捗を追いきれない
- **メタデータの確認・編集がチャットでは困難** — 表形式表示、アートワーク選択、歌詞確認にはGUIが必要
- **図書館CD（kashidashi）との手動マッチングが煩雑** — 候補選択にはラジオボタンとスコア表示が要る
- **確実性が求められる処理を AI の不安定さから切り離したかった** — CD取り込みはやり直しが効かない

Rip Tower はこれらを解決する専用 WebUI アプリとして設計した。OpenClaw からは API を叩くだけのシンプルなスキルに変わった。

## 機能

### リッピングパイプライン
- **自動メタデータ取得** — MusicBrainz (disc ID + テキスト検索)、Discogs、CDDB、HMV.co.jp、iTunes Search API、kashidashi の6ソースに並列問い合わせ
- **LLM 補助** — 文字化け修復、表記揺れ統一、kashidashi マッチ判定を Claude API で補助（問題がある場合のみ、1ジョブ最大1回）
- **自動承認** — confidence スコアが閾値以上なら人間の確認なしで完了。閾値は設定画面で調整可能
- **アートワーク** — Cover Art Archive / iTunes / Discogs から自動取得、WebUI で選択・手動アップロード
- **歌詞** — LRCLIB / Musixmatch から同期歌詞 (LRC) を自動取得、FLAC タグに埋め込み
- **マルチフォーマット** — FLAC / ALAC / Opus / MP3 / WAV。設定で切り替え

### ドライブ管理
- **USB シリアル番号でドライブを一意識別** — 抜き差しで /dev/sr* が変わっても追跡可能
- **ドライブに名前を付けて管理** — 「黒ロジテック1」「白ロジテック」等
- **ワンタッチイジェクト** — ダッシュボードから
- **CD 情報先行取得** — リッピング前に disc ID + MusicBrainz でアルバム情報を確認

### メタデータ編集
- **完了後も編集可能** — アーティスト名、アルバム名、トラックタイトル、歌詞をいつでも WebUI から編集
- **再反映** — 編集内容を FLAC タグ・ファイル名・フォルダ名に一括反映
- **複数枚組 CD** — album_group で紐付け、メタデータ同期

### 通知・連携
- **Discord Webhook** — リッピング開始/完了/エラー/レビュー待ちを通知
- **レビューリマインド** — 未承認ジョブを定期的に再通知（間隔設定可能）
- **Plex 自動リフレッシュ** — 完了時にライブラリスキャン
- **kashidashi 連携** — 図書館 CD 貸出管理との自動マッチング
- **OpenClaw スキル** — Discord 経由でリッピング開始・状態確認

### WAV インポート
- 別マシン（Windows PC 等）でリッピングした WAV ファイルを WebUI からアップロードしてパイプラインに乗せる
- CD ドライブが読めなかったトラックの差し替えにも対応

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| Backend | Python 3.12 / FastAPI / SQLAlchemy (async) / SQLite / Alembic |
| Frontend | React 19 / TypeScript / Vite / TailwindCSS v4 / PWA |
| Infrastructure | Docker Compose / privileged mode |
| Metadata | MusicBrainz / Discogs / CDDB / HMV / iTunes / LRCLIB / Claude API |
| Notification | Discord Webhook |

## セットアップ

### 前提条件
- Docker + Docker Compose
- USB CD ドライブ（1台以上）
- `/mnt/media/music/` に音楽ライブラリがある（パスは設定で変更可能）

### 起動

```bash
git clone https://github.com/ojimpo/rip-tower.git
cd rip-tower

# フロントエンドビルド（初回のみ）
cd frontend && npm install && npx vite build && cd ..

# 起動
docker compose up --build -d
```

`http://localhost:3900` でアクセス可能。

### 設定

初回起動時に `data/config.yaml` が自動生成される。WebUI の設定画面からも変更可能。

```yaml
general:
  auto_approve_threshold: 85    # 自動承認の confidence 閾値
  reminder_initial_hours: 6     # レビューリマインドの初回（時間）
  reminder_interval_hours: 24   # リマインドの間隔（時間）
  base_url: ""                  # Discord 通知のURL（例: http://rip-tower.arigato-nas）

output:
  format: flac                  # flac / alac / opus / mp3 / wav
  quality: 8                    # 形式依存（flac: 0-8, opus: 64-512kbps, mp3: V0-V9）
  music_dir: /mnt/media/music
  incoming_dir: /mnt/media/audio/_incoming
  folder_template: "{artist}/{album}"
  file_template: "{track_num} {artist} - {title}"

integrations:
  discord_webhook: ""           # Discord Webhook URL
  discogs_token: ""             # Discogs Personal Access Token
  musixmatch_token: ""          # Musixmatch API Token（optional）
  plex_url: ""                  # Plex URL（例: http://172.17.0.1:32400）
  plex_section_id: null         # Plex Music ライブラリの Section ID
  llm_api_key: ""               # Anthropic API Key（LLM メタデータ補助用）
  llm_model: haiku              # haiku / sonnet / opus
  kashidashi_url: ""            # kashidashi API URL
```

## 使い方

### 基本フロー

1. CD をドライブに入れる
2. ダッシュボードで **Rip** ボタンをタップ
3. ソースタイプ（図書館/手持ち）と Disc 番号を選択して開始
4. リッピング進捗がリアルタイムで表示される
5. 完了後、confidence が高ければ自動承認 → 音楽ライブラリに配置
6. confidence が低ければ review 状態 → メタデータを確認して承認

### ファイル配置

```
/mnt/media/music/
  THE CHECKERS/
    ALL Song Request [DISC1]/
      01 THE CHECKERS - ギザギザハートの子守唄.flac
      02 THE CHECKERS - 涙のリクエスト.flac
      ...
    ALL Song Request [DISC2]/
      01 THE CHECKERS - Song Title.flac
      ...
```

- FLAC タグの ALBUM は disc suffix なし（Plex で同一アルバムとして表示）
- フォルダ名には `[DISC1]` が付く（ファイルシステムで区別）

## API

```
POST /api/rip                              # リッピング開始
POST /api/import                           # WAV インポート
GET  /api/jobs                             # ジョブ一覧
GET  /api/jobs/:id                         # ジョブ詳細
POST /api/jobs/:id/metadata/approve        # 承認
POST /api/jobs/:id/metadata/apply          # メタデータ再反映
GET  /api/drives                           # ドライブ一覧
POST /api/drives/:id/eject                 # イジェクト
POST /api/drives/:id/identify              # CD 情報取得
GET  /api/history                          # リッピング履歴
GET  /api/history/stats                    # 統計
GET  /api/settings                         # 設定取得
PUT  /api/settings                         # 設定更新
WS   /ws                                   # WebSocket（リアルタイムイベント）
```

## 開発

```bash
# バックエンド（ホットリロード）
cd backend && uvicorn main:app --reload --port 3900

# フロントエンド（開発サーバー）
cd frontend && npm run dev

# テスト
python -m pytest backend/tests/ -v

# Alembic マイグレーション
PYTHONPATH=. alembic -c backend/alembic/alembic.ini revision --autogenerate -m "description"
PYTHONPATH=. alembic -c backend/alembic/alembic.ini upgrade head
```

## 将来の拡張候補

- OSS 化（プラグインシステムで kashidashi 等を分離）
- Plex/Jellyfin との双方向連携
- バーコードスキャン（スマホカメラ → JAN → ヒント自動入力）
- AccurateRip 検証

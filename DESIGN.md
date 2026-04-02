# Rip Tower — CD Ripping & Metadata Management App

> OpenClaw CD Rip の後継。Docker アプリとして独立動作し、WebUI でメタデータの確認・編集・承認を行う。
> 開発方針: 最初から全機能入りでしっかり作る。

## なぜ作るか

- OpenClaw（チャットUI）経由だと複数ドライブ同時リッピング時に状態管理が破綻する
- メタデータの確認・編集がチャットでは困難（表形式表示、アートワーク選択、歌詞確認など）
- kashidashi との手動マッチングもGUIがあると圧倒的に楽
- CD取り込みという確実性が求められる処理を、チャットAIの不安定さから切り離す
- リッピング履歴をDBに残すことで、kashidashi に無いCD（手持ちCD等）の取り込みも正しく管理できる

## アーキテクチャ概要

```
┌─────────────────────────────────────────────────┐
│                  Docker Compose                  │
│                                                  │
│  ┌───────────────────────────────────────────┐   │
│  │            FastAPI Backend                 │   │
│  │  ┌─────────┐ ┌──────────┐ ┌───────────┐  │   │
│  │  │ Rip     │ │ Metadata │ │ WebSocket │  │   │
│  │  │ Pipeline│ │ Pipeline │ │ Events    │  │   │
│  │  └────┬────┘ └────┬─────┘ └─────┬─────┘  │   │
│  │       │           │             │         │   │
│  │  ┌────┴───────────┴─────────────┴──────┐  │   │
│  │  │          SQLite (rip-tower.db)      │  │   │
│  │  └────────────────────────────────────┘  │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  ┌───────────────────────────────────────────┐   │
│  │     React (Vite) Frontend — PWA           │   │
│  │     Mobile-first / TailwindCSS            │   │
│  └───────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
         │              │               │
    /dev/sr0-2     Discord Webhook   kashidashi API
    (--privileged)  (通知+URL)       (貸出管理)
```

## 技術スタック

| レイヤー | 技術 | 理由 |
|---------|------|------|
| Backend | FastAPI + SQLAlchemy + SQLite | 既存Pythonスクリプトをそのまま活かせる。非同期処理が楽 |
| Frontend | React + Vite + TailwindCSS | PWA対応が容易。vite-plugin-pwa でService Worker自動生成 |
| State Sync | WebSocket (fastapi-websocket) | リッピング進捗のリアルタイム表示 |
| DB | SQLite | 単一ノード、十分な性能。ファイルバックアップが容易 |
| Migration | Alembic | スキーマ変更をコードで管理。最初から導入 |
| Container | Docker Compose | --privileged で /dev/sr* アクセス |
| Notification | Discord Webhook | 完了通知 + ユニークURL付き |

## データモデル

### Drive（CDドライブ）

```sql
CREATE TABLE drives (
    drive_id      TEXT PRIMARY KEY,  -- USB シリアル番号など（udevadm で取得するユニークID）
    name          TEXT NOT NULL,     -- ユーザーが付けた名前（例: "白ドライブ", "薄型"）
    current_path  TEXT,              -- 現在のデバイスパス（/dev/sr0）。未接続時は NULL
    last_seen_at  DATETIME,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

起動時・USB 抜き差し時に `/dev/sr*` をスキャンし、各デバイスの USB シリアル等で `drive_id` を特定。`current_path` を更新する。未登録のドライブが見つかったら自動登録し、WebUI で名前を付けられる。

### Job（リッピングジョブ）

```sql
CREATE TABLE jobs (
    id            TEXT PRIMARY KEY,  -- UUID v4（URL用）
    album_group   TEXT,              -- 複数枚組CDのグループID（UUID v4）。同アルバムのdisc同士を紐付け
    drive_id      TEXT REFERENCES drives(drive_id),  -- どのドライブでリッピングしたか（WAVインポート時はNULL）
    disc_id       TEXT,
    toc_hash      TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    -- pending → identifying → ripping+resolving(並列) → encoding → review/finalizing → complete
    -- どの段階からでも → error
    source_type   TEXT DEFAULT 'unknown',
    -- kashidashi: 図書館CD / owned: 手持ちCD / unknown: 未分類
    output_dir    TEXT,              -- 最終出力先（例: /mnt/media/music/Artist/Album）
    error_message TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at  DATETIME
);
```

### JobMetadata（採用されたメタデータ）

```sql
CREATE TABLE job_metadata (
    job_id        TEXT PRIMARY KEY REFERENCES jobs(id),
    artist        TEXT,
    album         TEXT,
    album_base    TEXT,           -- disc suffix 除去済み
    year          INTEGER,
    genre         TEXT,
    disc_number   INTEGER DEFAULT 1,
    total_discs   INTEGER DEFAULT 1,
    is_compilation BOOLEAN DEFAULT FALSE,
    confidence    INTEGER,        -- 0-100
    source        TEXT,           -- 最終採用ソース
    source_url    TEXT,
    needs_review  BOOLEAN DEFAULT FALSE,
    issues        TEXT,           -- JSON array: ["mojibake", "no_track_titles", ...]
    approved      BOOLEAN DEFAULT FALSE,
    approved_at   DATETIME
);
```

### Track（トラック）

```sql
CREATE TABLE tracks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL REFERENCES jobs(id),
    track_num     INTEGER NOT NULL,
    title         TEXT,
    artist        TEXT,           -- compilation 時のトラックアーティスト
    rip_status    TEXT DEFAULT 'pending',   -- pending/ripping/ok/ok_degraded/failed
    encode_status TEXT DEFAULT 'pending',   -- pending/encoding/ok/failed
    wav_path      TEXT,
    encoded_path  TEXT,           -- エンコード済みファイルパス（.flac, .opus, .mp3 等）
    duration_ms   INTEGER,
    lyrics_plain  TEXT,           -- プレーンテキスト歌詞
    lyrics_synced TEXT,           -- LRC形式の同期歌詞
    lyrics_source TEXT,           -- lrclib / musixmatch / manual
    UNIQUE(job_id, track_num)
);
```

### MetadataCandidate（メタデータ候補 — 全ソースの結果を保存）

```sql
CREATE TABLE metadata_candidates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL REFERENCES jobs(id),
    source        TEXT NOT NULL,  -- musicbrainz_discid/musicbrainz_search/discogs/kashidashi/hmv/cddb/itunes
    source_url    TEXT,
    artist        TEXT,
    album         TEXT,
    year          INTEGER,
    genre         TEXT,
    track_titles  TEXT,           -- JSON array
    confidence    INTEGER,
    evidence      TEXT,           -- JSON: なぜこの confidence になったか
    selected      BOOLEAN DEFAULT FALSE
);
```

### Artwork（アートワーク候補）

```sql
CREATE TABLE artworks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL REFERENCES jobs(id),
    source        TEXT NOT NULL,  -- coverartarchive/discogs/itunes/manual
    url           TEXT,           -- 外部URL
    local_path    TEXT,           -- ダウンロード済みローカルパス
    width         INTEGER,
    height        INTEGER,
    file_size     INTEGER,
    selected      BOOLEAN DEFAULT FALSE
);
```

### KashidashiCandidate（kashidashi マッチ候補）

```sql
CREATE TABLE kashidashi_candidates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL REFERENCES jobs(id),
    item_id       INTEGER NOT NULL,
    title         TEXT,
    artist        TEXT,
    score         REAL,           -- マッチスコア
    match_type    TEXT,           -- discid_exact / fuzzy
    matched       BOOLEAN DEFAULT FALSE
);
```

## メタデータパイプライン

### 設計思想

「取れるものは全部取って、人間が最終判断」

1. 全ソースに並列問い合わせ → 候補を全て DB に保存
2. ルールベースで正規化・クリーニング（sanitizer）
3. 自動ランキングで最有力候補を仮選択
4. **問題がある場合のみ** LLM に補助を依頼（下記参照）
5. confidence が高ければ自動承認、低ければ review 状態で待機
6. WebUI で候補一覧から選択・編集・マージが可能

### LLM メタデータ補助

毎回呼ぶのではなく、ルールベース処理で解決できない問題がある場合のみ LLM に投げる。

**呼び出し条件（いずれかに該当する場合）:**
- 文字化けの疑いがある（sanitizer が検出）
- 複数ソース間でアーティスト名/アルバム名が矛盾している
- compilation アルバムでトラックアーティストの分離・正規化が必要
- confidence が低く、どの候補を採用すべきか判断が難しい
- ジャンルが全ソースで空 or バラバラ
- kashidashi マッチで disc ID 完全一致がなく、fuzzy マッチのスコアが中途半端な場合

**LLM に依頼するタスク:**
- 文字化け修復（SJIS→UTF-8 崩れの推定復元）
- 表記揺れの統一判断（「B'z」vs「B'z」、正式名称の特定）
- 複数候補のマージ（日本語タイトル + 英語タイトル の最適な採用判断）
- compilation のアーティスト正規化
- ジャンル推定
- kashidashi マッチ判定（図書館の登録名「シイナ リンゴ」とCD上の「椎名林檎」のような表記差を理解してマッチ）

**実装方針:**
- 1ジョブにつき最大1回の LLM 呼び出し（メタデータ整形 + kashidashi マッチをまとめて1プロンプトに）
- 入力: 全候補の生データ + sanitizer の issue リスト + kashidashi 候補リスト
- 出力: 構造化 JSON（修正後メタデータ + kashidashi マッチ推奨 + 判断理由）
- API: Claude API（Haiku でコスト抑制、複雑な場合のみ Sonnet）
- LLM の結果は metadata_candidates に `source: "llm"` として保存（他候補と同列に扱い、ユーザーが最終選択）
- タイムアウト: 15秒。失敗しても処理は続行（LLM なしの結果で進む）

### メタデータソース（既存 + 新規）

| # | ソース | 用途 | 認証 | 既存/新規 |
|---|--------|------|------|-----------|
| 1 | MusicBrainz (disc ID) | 最も信頼性が高い | 不要 | 既存 |
| 2 | MusicBrainz (検索) | disc ID ヒットしない場合 | 不要 | 既存 |
| 3 | Discogs | カタログ番号での特定に強い | Token | 既存 |
| 4 | kashidashi | 貸出中CDとの照合 | 不要 | 既存 |
| 5 | HMV.co.jp | 邦楽カタログ番号検索 | 不要 | 既存 |
| 6 | CDDB (gnudb) | レガシーだが網羅性あり | 不要 | 既存 |
| 7 | iTunes Search API | アートワーク取得に特に有用 | 不要 | **新規** |

### アートワーク取得パイプライン

メタデータ確定後（artist + album 確定後）に並列取得：

| 優先度 | ソース | 解像度 | 備考 |
|--------|--------|--------|------|
| 1 | Cover Art Archive | 原寸（数千px） | MusicBrainz release ID 経由。最高品質 |
| 2 | iTunes Search API | 600x600〜3000x3000 | URL の `100x100` を `3000x3000` に置換で高解像度取得可 |
| 3 | Discogs | 様々 | release ID 経由。要Token |
| 4 | 手動アップロード | 任意 | WebUI からドラッグ&ドロップ |

- 全候補をサムネイル付きで WebUI に表示
- ユーザーが選択 or 手動アップロード
- 選択されたアートワークを FLAC に埋め込み（metaflac --import-picture-from）
- フォルダにも cover.jpg として配置（Plex/他プレイヤー用）

### 歌詞取得パイプライン

アートワークと並列で実行：

| 優先度 | ソース | 種類 | 認証 | 備考 |
|--------|--------|------|------|------|
| 1 | LRCLIB | 同期歌詞 (LRC) | 不要 | 完全無料API。artist + title + album + duration で検索 |
| 2 | Musixmatch | 同期歌詞 / プレーン | Token | 無料枠あり（日2000リクエスト） |
| 3 | 手動入力 | プレーン / LRC | — | WebUI から |

- 同期歌詞（LRC）があればそちらを優先
- FLAC の LYRICS / UNSYNCEDLYRICS タグに埋め込み
- 歌詞は WebUI で確認・編集可能

### 複数枚組CD（album_group）

同アルバムの複数ディスクを `album_group` で紐付け、メタデータの一貫性を保証する。

**グループ化の方法:**
- API: `POST /api/rip` に `album_group` を指定（disc 1 のレスポンスで得た group ID を disc 2 に渡す）
- WebUI: ダッシュボードやジョブ詳細から後からグループ化も可能

**グループ内のメタデータ同期:**
- グループ内のジョブは artist / album（album_base） / year / genre / is_compilation / アートワークを共有
- disc 1 のメタデータを編集すると、グループ内の他ディスクにも反映される（トラック情報は各ディスク固有）
- メタデータ解決時、グループ内で最も confidence が高いソースの結果をグループ全体に適用

**承認の扱い:**
- グループ内の全ディスクが encoding まで完了したら、まとめて review / 自動承認の判定を行う
- 1枚ずつバラバラに承認されない（メタデータの不整合を防ぐため）

### 自動承認ルール

```python
auto_approve_threshold = 85  # configurable

if confidence >= auto_approve_threshold:
    # 自動承認 → そのまま finalizing へ
    # ただし Discord 通知にユニークURLを含める（後から編集可能）
else:
    # review 状態で停止
    # Discord 通知「メタデータ要確認」+ ユニークURL
    # 一定時間後にリマインド通知（下記参照）
```

### review リマインド通知

review 状態のまま放置されたジョブに対して、Discord にリマインド通知を送る。

- **初回リマインド**: review 開始から 6 時間後
- **以降**: 24 時間ごとに再通知
- **間隔は設定画面で変更可能**（0 にすると無効化）
- 承認されたら停止

```
⏰ 未承認ジョブ（1件）：不明なアルバム（sr1 / 6時間経過）
🔗 http://100.85.219.71:3900/job/e5f6g7h8-...
```

複数件溜まっている場合はまとめて1通にする：
```
⏰ 未承認ジョブ（3件）
  - 不明なアルバム（sr1 / 6時間経過）
  - Various Artists / ...（sr0 / 2日経過）
  - Track 1, Track 2...（sr2 / 1日経過）
🔗 http://100.85.219.71:3900/
```

## API 設計

### OpenClaw 連携用（スキルから呼ぶ最小API）

```
POST /api/rip
Body: {
    "drive_id": "XXXX...",            // ドライブのユニークID（or デバイスパスでも指定可）
    "source_type": "kashidashi",  // optional: kashidashi / owned / unknown
    "hints": {                    // optional
        "catalog": "VICP-64336",
        "artist": "...",
        "title": "...",
        "jan": "..."
    },
    "force": {                    // optional
        "artist": "...",
        "album": "...",
        "item_id": 123
    },
    "disc_number": 1,             // optional
    "total_discs": 2,             // optional
    "album_group": "xxxxxxxx-..." // optional: 既存グループに追加する場合
}
Response: {
    "job_id": "a1b2c3d4-...",
    "album_group": "xxxxxxxx-...",  // 新規生成 or 指定されたもの
    "url": "http://100.85.219.71:3900/job/a1b2c3d4-...",
    "status": "pending"
}
```

### WAV インポート API

CDドライブが無いマシン（hachiman-desk 等）からリッピング済み WAV をアップロードし、新規ジョブとして取り込む。
identifying と ripping をスキップし、メタデータ解決 → エンコード → finalizing のパイプラインに乗せる。

disc ID が取れないため MusicBrainz disc ID 検索は使えない。
メタデータ解決は hints（アーティスト名/アルバム名）+ トラック数/時間でのテキスト検索が中心になる。
WebUI のインポート画面でアーティスト/アルバムの入力を促す（わかっているはずなので）。

```
POST /api/import
Content-Type: multipart/form-data
Body: {
    "wav_files": [File, File, ...],   // WAV ファイル群（トラック順にソート）
    "source_type": "owned",           // optional
    "hints": {                        // インポート時は入力を推奨
        "artist": "...",
        "title": "...",
        "catalog": "..."
    },
    "disc_number": 1,                 // optional
    "total_discs": 1,                 // optional
    "album_group": "xxxxxxxx-..."     // optional
}
Response: {
    "job_id": "...",
    "url": "...",
    "status": "resolving"             // identifying/ripping をスキップ
}
```

### WebUI / 管理用 API

```
# ジョブ一覧・詳細
GET    /api/jobs                         # 一覧（フィルタ: status, device, source_type）
GET    /api/jobs/:id                     # 詳細（metadata + tracks + candidates + artworks + kashidashi 全込み）
DELETE /api/jobs/:id                     # ジョブ削除（ファイルも削除）

# 再リッピング・WAV差し替え
POST   /api/jobs/:id/re-rip              # 全トラック再リッピング（drive_id 指定可。省略時は元のドライブ）
POST   /api/jobs/:id/re-rip/:track_num   # 特定トラックのみ再リッピング
POST   /api/jobs/:id/tracks/:num/upload-wav  # 別マシンでリップした WAV をアップロードして差し替え

# メタデータ操作（review中 & complete後 どちらでも可）
PUT    /api/jobs/:id/metadata            # メタデータ手動編集
POST   /api/jobs/:id/metadata/approve    # 承認 → finalizing へ進む
POST   /api/jobs/:id/metadata/re-resolve # 再取得（ヒント追加して再実行）
PUT    /api/jobs/:id/candidates/:cid/select  # 候補選択
POST   /api/jobs/:id/metadata/apply      # complete後の編集を FLAC に再反映

# トラック操作（review中 & complete後 どちらでも可）
PUT    /api/jobs/:id/tracks/:num         # トラック個別編集（title, artist, lyrics）
POST   /api/jobs/:id/tracks/:num/lyrics/fetch  # 歌詞再取得

# アートワーク（review中 & complete後 どちらでも可）
GET    /api/jobs/:id/artworks            # アートワーク候補一覧
POST   /api/jobs/:id/artworks/upload     # 手動アップロード
PUT    /api/jobs/:id/artworks/:aid/select # 選択

# 複数枚組グループ
POST   /api/jobs/:id/group               # 既存ジョブをグループ化（album_group 生成）
PUT    /api/jobs/:id/group/:group_id      # グループに追加/移動
DELETE /api/jobs/:id/group                # グループから外す
GET    /api/groups/:group_id              # グループ内の全ジョブ取得

# kashidashi
GET    /api/jobs/:id/kashidashi          # マッチ候補一覧
PUT    /api/jobs/:id/kashidashi/:kid/match  # 手動マッチ
POST   /api/jobs/:id/kashidashi/skip     # マッチスキップ

# 履歴・統計
GET    /api/history                       # 全リッピング履歴（ページネーション）
GET    /api/history/stats                 # 統計（総枚数、ソース別内訳、月別推移）

# ドライブ管理
GET    /api/drives                       # ドライブ一覧（接続状態 + 名前）
PUT    /api/drives/:drive_id             # 名前変更
POST   /api/drives/:drive_id/eject       # イジェクト

# WebSocket
WS     /ws                              # リアルタイムイベント
```

### WebSocket イベント

```json
{"type": "job:status",   "job_id": "...", "status": "ripping", "detail": {...}}
{"type": "job:progress", "job_id": "...", "track": 3, "total": 12, "percent": 25}
{"type": "job:complete", "job_id": "...", "url": "..."}
{"type": "job:error",    "job_id": "...", "message": "..."}
{"type": "job:review",   "job_id": "...", "url": "...", "reason": "low confidence"}
{"type": "drive:connected",    "drive_id": "...", "name": "白ドライブ", "path": "/dev/sr0"}
{"type": "drive:disconnected", "drive_id": "...", "name": "白ドライブ"}
```

## WebUI 画面設計

> **実装前に全画面の HTML モックアップを作成する。** `mockups/` ディレクトリに静的 HTML + TailwindCSS CDN で各画面を作り、見た目と遷移を確認してから実装に入る。
> 以下は設計段階のワイヤーフレーム。モックアップ作成時の参考とする。

### 1. ダッシュボード (`/`)

スマホ縦画面に最適化。

```
┌─────────────────────────┐
│  Rip Tower        ⚙️    │
├─────────────────────────┤
│ ⚠️ 要対応（2件）         │  ← 通知セクション（目立つ色）
│  ⏸ 不明アーティスト      │     review待ち + エラーを集約
│    Review待ち・6時間経過  │     タップでジョブ詳細へ
│  ❌ 薄型: rip失敗         │
│    cd-paranoia timeout   │
├─────────────────────────┤
│  ▶ 白ドライブ: Ripping    │  ← アクティブジョブ（カード）
│    BUMP OF CHICKEN /    │
│    jupiter              │
│    ✅ 1. Opening         │  ← iTunes風トラック進捗
│    ✅ 2. Main Theme      │     完了=✅ 進行中=🔄 待ち=○
│    🔄 3. Finale  42%    │     現在のトラックは%表示
│    ○ 4. Bonus           │
│    ○ ...（残り8曲）      │  ← 多い場合は折りたたみ
├─────────────────────────┤
│  ドライブ                │
│  ● 白ドライブ  💿 [⏏]   │  ← CD入り + ディスク情報表示
│    椎名林檎 / 無罪モラ   │     （identifying 完了後）
│    12 tracks             │
│  ● 薄型       ○ [⏏]   │  ← 空: イジェクトでトレイ開く
│  ○ 黒ドライブ  未接続    │  ← 未接続: ボタンなし
├─────────────────────────┤
│  最近の完了              │
│  ✓ Radiohead / OK...    │  ← 完了済み（タップで詳細）
│  ✓ 椎名林檎 / 無罪...   │
│  ✓ Miles Davis / ...    │
└─────────────────────────┘
```

### 2. ジョブ詳細 (`/job/:id`) — ユニークURL

タブ構成で情報を整理：

**[メタデータ] タブ**
```
┌─────────────────────────┐
│ ← Back                  │
├─────────────────────────┤
│  ┌─────┐                │
│  │ Art │  Artist Name   │  ← アートワーク + 基本情報
│  │ work│  Album Title   │     各フィールドタップで編集
│  └─────┘  2024 / Rock   │
├─────────────────────────┤
│  Source: MusicBrainz     │
│  Confidence: 92          │
│  Issues: なし            │
├─────────────────────────┤
│ # | Title        | Rip|Enc│  ← トラック一覧 + ステータス
│ 1 | Opening      | ✅ | ✅ │     ✅=ok ⚠️=degraded ❌=failed ⏳=進行中 ○=pending
│ 2 | Main Theme   | ✅ | ✅ │
│ 3 | Finale       | ❌ | ○ │  ← 失敗トラックは目立つ
│ 4 | Bonus        | ⚠️ | ✅ │  ← degraded: リトライで品質低下あり
│                    [✏️編集]│
├─────────────────────────┤
│ 失敗トラック: 1件        │  ← 失敗がある場合のみ表示
│ [ 失敗トラックを再リップ ]│     失敗分だけ再実行
│ [ 全トラック再リップ ]    │     最初からやり直し
│ [ WAVアップロードで差替 ] │     別マシンでリップしたWAVを指定
├─────────────────────────┤
│ 他の候補 (4件)     ▼    │  ← 折りたたみ：他ソースの候補
│  Discogs: 75点           │     タップで採用切り替え
│  CDDB: 60点              │
├─────────────────────────┤
│    [ 承認して完了 ]      │  ← 大きなCTAボタン
└─────────────────────────┘
```

**[アートワーク] タブ**
```
┌─────────────────────────┐
│ Cover Art Archive        │
│  ┌──────┐ ← selected    │  ← 候補をグリッド表示
│  │ 🖼️  │  3000x3000    │     タップで選択
│  └──────┘               │
│ iTunes                   │
│  ┌──────┐               │
│  │ 🖼️  │  600x600      │
│  └──────┘               │
│                          │
│  [ 📷 画像アップロード ] │  ← 手動アップロード
└─────────────────────────┘
```

**[歌詞] タブ**
```
┌─────────────────────────┐
│ Track 1: Opening         │
│ Source: LRCLIB (synced)  │
│ ┌───────────────────┐   │
│ │ [00:12.34] 歌詞1行 │   │  ← LRC形式プレビュー
│ │ [00:18.56] 歌詞2行 │   │     編集ボタンあり
│ │ ...                │   │
│ └───────────────────┘   │
│ Track 2: Main Theme      │
│ Source: なし  [取得]     │  ← 未取得は取得ボタン
├─────────────────────────┤
│ Track 3: Finale          │
│ Source: なし  [取得]     │
└─────────────────────────┘
```

**[kashidashi] タブ**
```
┌─────────────────────────┐
│ マッチ候補               │
│                          │
│ ● 葛飾図書館 #1234      │  ← ラジオボタンで選択
│   椎名林檎 / 無罪モラ   │
│   Score: 95 (discid一致) │
│                          │
│ ○ 葛飾図書館 #5678      │
│   椎名林檎 / ベスト     │
│   Score: 40 (artist一致) │
│                          │
│ ○ マッチなし             │
├─────────────────────────┤
│    [ 確定 ]              │
└─────────────────────────┘
```

### 3. 履歴 (`/history`)

```
┌─────────────────────────┐
│ リッピング履歴           │
├─────────────────────────┤
│ [全て] [図書館] [手持ち] │  ← source_type フィルタ
├─────────────────────────┤
│ 📊 合計: 142枚          │
│    図書館: 98 / 手持ち: 44│
├─────────────────────────┤
│ 2026-04                  │
│  椎名林檎 / 無罪モラ    │
│  📚 kashidashi #1234    │
│                          │
│  Radiohead / OK Computer │
│  💿 手持ち               │
│                          │
│ 2026-03                  │
│  BUMP OF CHICKEN / ...   │
│  📚 kashidashi #5678    │
│  ...                     │
└─────────────────────────┘
```

### 4. 設定 (`/settings`)

```
┌─────────────────────────┐
│ 設定                     │
├─────────────────────────┤
│ ▼ 一般                   │
│ 自動承認しきい値: [85]   │
│ リマインド間隔: [6h/24h] │
├─────────────────────────┤
│ ▼ 出力                   │
│ エンコード形式:          │
│  [FLAC▼] 品質: [8]      │  ← FLAC/ALAC/Opus/MP3/WAV
│ 出力先: /mnt/media/music │
│ ファイル名テンプレート:  │
│  [{track_num} {artist}   │  ← プレビュー表示あり
│   - {title}]             │
│ フォルダ構成:            │
│  [{artist}/{album}]      │
├─────────────────────────┤
│ ▼ 連携                   │
│ Kashidashi URL: http://..│
│ Discord Webhook: https://│
│ Discogs Token: ****      │
│ Plex Section ID: [2]     │
│ LLM API Key: ****        │
│ LLM Model: [haiku▼]     │
├─────────────────────────┤
│ ▼ ドライブ               │
│ ● 白ドライブ (/dev/sr0)  │  ← 名前タップで編集
│ ● 薄型 (/dev/sr1)        │
│ ○ 黒ドライブ (未接続)    │
└─────────────────────────┘
```

## Discord 通知フォーマット

### リッピング開始
```
▶ リッピング開始：BUMP OF CHICKEN / jupiter（12tracks / sr0）
```

### 完了（自動承認）
```
✅ 完了：BUMP OF CHICKEN / jupiter（3:42 / sr0 / kashidashi:matched）
🔗 http://100.85.219.71:3900/job/a1b2c3d4-...
```

### 要確認（レビュー待ち）
```
⚠️ メタデータ要確認：不明なアルバム（sr1 / confidence:35）
🔗 http://100.85.219.71:3900/job/e5f6g7h8-...
```

### エラー
```
❌ エラー：sr0 — cd-paranoia timeout on track 5
🔗 http://100.85.219.71:3900/job/a1b2c3d4-...
```

## パイプライン詳細フロー

```
POST /api/rip                    POST /api/import
    │                                │
    ▼                                │ WAV アップロード、Job作成
[1. pending]                         │ identifying/ripping スキップ
    │  Job作成、DBに登録              │
    ▼                                │
[2. identifying]                     │
    │  cd-discid でディスク読み取り    │
    │  disc_id, track_count, TOC 取得 │
    ▼                                │
  ┌──────────────────────────────┐   │
  │ 並列実行                      │   │
  │                               │   │
  │ [3a. resolving]  ◄────────────│───┘ ← import はここから合流
  │  全メタデータソースに並列問い合わせ│
  │  → metadata_candidates に全結果保存
  │  → 最高 confidence を仮採用
  │  → アートワーク並列取得
  │  → kashidashi マッチング
  │  → 歌詞取得
  │                               │
  │ [3b. ripping]                 │
  │  cd-paranoia / cdda2wav でWAV抽出
  │  トラックごとに tracks テーブル更新
  │  WebSocket で進捗配信
  └──────────┬────────────────────┘
             │ 両方完了を待つ（import は resolving のみ）
             ▼
[4. encoding]
    │  WAV → 設定形式にエンコード（FLAC/ALAC/Opus/MP3/WAV）
    │  メタデータタグ付与
    │  トラックごとに tracks テーブル更新
    ▼
  confidence >= threshold?
    │
    ├─ YES → [5. finalizing]（自動）
    │           │  アートワーク埋め込み
    │           │  歌詞埋め込み
    │           │  ファイル移動（→ /mnt/media/music/Artist/Album/）
    │           │  kashidashi 更新
    │           │  Plex リフレッシュ
    │           │  Discord 通知（✅ + URL）
    │           ▼
    │        [6. complete]
    │
    └─ NO → [5. review]（待機）
              │  Discord 通知（⚠️ + URL）
              │  ユーザーが WebUI で確認・編集・承認
              │  POST /api/jobs/:id/metadata/approve
              ▼
           [6. finalizing] → [7. complete]
```

### complete 後の編集（ライブラリ追跡）

complete 後もジョブは DB に残り、`output_dir` で最終出力先を追跡する。
WebUI からいつでもメタデータ・アートワーク・歌詞を編集でき、変更をエンコード済みファイルに再反映できる。

```
[complete] → ユーザーが WebUI で編集
                │
                ▼
         POST /api/jobs/:id/metadata/apply
                │
                ├─ タグ書き換え（形式に応じて metaflac / ffmpeg 等）
                ├─ アートワーク差し替え
                ├─ 歌詞タグ更新
                ├─ アーティスト名/アルバム名変更時はファイル・フォルダリネーム
                └─ output_dir を更新
```

これにより、リッピング直後に自動承認で通過したジョブでも、後から気づいた表記ミスやアートワーク差し替えに対応できる。

## エンコード設定

### 対応フォーマット

| 形式 | コマンド | 品質設定 | 備考 |
|------|---------|---------|------|
| FLAC | `flac -N` | N=0-8（デフォルト8） | ロスレス。デフォルト |
| ALAC | `ffmpeg -acodec alac` | — | Apple ロスレス |
| Opus | `opusenc --bitrate N` | 64-512 kbps（デフォルト128） | 最新の高効率コーデック |
| MP3 | `lame -V N` | V0-V9（デフォルトV0） | 互換性重視 |
| WAV | — | — | 無圧縮。タグ埋め込み不可 |

- 設定画面で形式と品質を選択
- ジョブ単位での上書きも可能（API の `encoding_format` パラメータ）
- 将来の形式追加は encoder.py にコーデック定義を追加するだけ

### ファイル名テンプレート

出力先パスはテンプレートでカスタマイズ可能：

| 変数 | 展開例 |
|------|--------|
| `{artist}` | `椎名林檎` |
| `{album}` | `無罪モラトリアム` |
| `{year}` | `1999` |
| `{genre}` | `J-Pop` |
| `{track_num}` | `01`（ゼロパディング） |
| `{title}` | `正しい街` |
| `{disc_num}` | `1` |
| `{ext}` | `flac` |

**デフォルト設定:**
- フォルダ: `{artist}/{album}/`
- ファイル: `{track_num} {artist} - {title}.{ext}`

設定画面でプレビュー表示し、実際のファイル名を確認してから変更できる。

## ファイル配置

### 最終出力先（デフォルト設定の場合）

```
/mnt/media/music/
  {artist}/
    {album}/
      cover.jpg
      01 {artist} - {title}.flac
      02 {artist} - {title}.flac
      ...
```

### 作業ディレクトリ

```
/mnt/media/audio/_incoming/
  {job_id}/
    identity.json
    rip_progress.json
    *.wav
    *.flac
    _ripmeta/          # .toc, .cue, .log アーカイブ
    _artwork/          # ダウンロード済みアートワーク候補
```

## 設定ファイル

全設定を1つの YAML ファイルで管理する。WebUI の設定画面で変更したらこのファイルに書き戻す。

```yaml
# data/config.yaml
general:
  auto_approve_threshold: 85
  reminder_initial_hours: 6
  reminder_interval_hours: 24

output:
  format: flac          # flac / alac / opus / mp3 / wav
  quality: 8            # 形式依存（flac: 0-8, opus: 64-512, mp3: V0-V9）
  music_dir: /mnt/media/music
  incoming_dir: /mnt/media/audio/_incoming
  folder_template: "{artist}/{album}"
  file_template: "{track_num} {artist} - {title}"

integrations:
  discord_webhook: ""
  discogs_token: ""
  musixmatch_token: ""
  plex_section_id: null
  llm_api_key: ""
  llm_model: haiku      # haiku / sonnet
  kashidashi_url: http://kashidashi-app-web-1:18080
```

- 初回起動時にデフォルト値で自動生成
- `backend/config.py` で YAML を読み込み、Pydantic Settings モデルにマッピング
- 環境変数 `CONFIG_PATH` でファイルパスを上書き可能（デフォルト: `/app/data/config.yaml`）
- シークレット（API キー等）も YAML に含める。Docker volume でホスト側に永続化

## Docker 構成

```yaml
# compose.yaml
services:
  app:
    build: .
    ports:
      - "3900:3900"
    privileged: true          # /dev/sr* アクセス
    volumes:
      - ./data:/app/data      # SQLite + 設定ファイル（config.yaml）
      - /mnt/media:/mnt/media # 音楽ライブラリ + incoming
      - /dev:/dev              # CDドライブ
    environment:
      - CONFIG_PATH=/app/data/config.yaml
    restart: unless-stopped
```

```dockerfile
# Dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    cd-paranoia cdda2wav cd-discid flac eject curl \
    ffmpeg lame opus-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Frontend (pre-built)
COPY frontend/dist /app/static

# Backend
COPY backend /app/backend

EXPOSE 3900
# 起動時に Alembic マイグレーション実行 → uvicorn 起動
CMD ["sh", "-c", "alembic -c backend/alembic/alembic.ini upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 3900"]
```

## プロジェクト構造

```
~/dev/rip-tower/
├── compose.yaml
├── Dockerfile
├── requirements.txt
├── CLAUDE.md                     # 実装ガイド
├── DESIGN.md                     # 設計ドキュメント
├── README.md
│
├── mockups/                      # HTML モックアップ（実装前に全画面作成）
│   ├── dashboard.html
│   ├── job-detail.html
│   ├── history.html
│   └── settings.html
│
├── data/                         # runtime (gitignore)
│   ├── rip-tower.db
│   └── config.yaml               # 全設定ファイル（初回起動時に自動生成）
│
├── backend/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app, CORS, static files
│   ├── config.py                 # Settings (env → pydantic-settings)
│   ├── database.py               # SQLAlchemy engine + session
│   ├── models.py                 # SQLAlchemy models (Job, Track, Drive, etc.)
│   ├── schemas.py                # Pydantic request/response schemas
│   │
│   ├── alembic/                  # DB マイグレーション
│   │   ├── env.py
│   │   ├── versions/             # マイグレーションファイル
│   │   └── alembic.ini
│   │
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── jobs.py               # /api/jobs CRUD + approve/re-resolve
│   │   ├── drives.py             # /api/drives（ドライブ管理・イジェクト）
│   │   ├── history.py            # /api/history
│   │   └── settings_router.py    # /api/settings (読み書き)
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── pipeline.py           # パイプライン全体制御（状態遷移）
│   │   ├── drive_monitor.py      # USB ドライブ検出・ユニークID取得・抜き差し監視
│   │   ├── disc_identity.py      # cd-discid 読み取り
│   │   ├── ripper.py             # cd-paranoia / cdda2wav
│   │   ├── encoder.py            # flac エンコード + タグ付け
│   │   ├── finalizer.py          # ファイル配置 + Plex refresh
│   │   ├── notifier.py           # Discord webhook
│   │   └── websocket.py          # WebSocket イベント配信
│   │
│   ├── metadata/
│   │   ├── __init__.py
│   │   ├── resolver.py           # 全ソース並列問い合わせ + ランキング
│   │   ├── sanitizer.py          # クリーニング + issue検出
│   │   ├── sources/
│   │   │   ├── __init__.py
│   │   │   ├── base.py           # MetadataSource ABC
│   │   │   ├── musicbrainz.py
│   │   │   ├── discogs.py
│   │   │   ├── hmv.py
│   │   │   ├── cddb.py
│   │   │   ├── itunes.py
│   │   │   └── kashidashi.py     # 図書館CD貸出管理連携
│   │   ├── artwork.py            # アートワーク取得 + 埋め込み
│   │   ├── lyrics.py             # 歌詞取得（LRCLIB + Musixmatch）
│   │   ├── llm_assist.py         # LLM メタデータ補助（条件付き呼び出し）
│   │   └── normalize.py          # テキスト正規化ユーティリティ
│   │
│   └── tests/
│       ├── __init__.py
│       ├── test_resolver.py
│       ├── test_sanitizer.py
│       └── ...
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   ├── public/
│   │   └── icons/                # PWA icons (192x192, 512x512)
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── hooks/
│       │   ├── useWebSocket.ts   # WS接続 + 自動再接続
│       │   └── useJob.ts         # ジョブデータ取得 + WS更新
│       ├── pages/
│       │   ├── Dashboard.tsx
│       │   ├── JobDetail.tsx
│       │   ├── History.tsx
│       │   └── Settings.tsx
│       ├── components/
│       │   ├── JobCard.tsx
│       │   ├── MetadataTab.tsx
│       │   ├── ArtworkTab.tsx
│       │   ├── LyricsTab.tsx
│       │   ├── TrackTable.tsx
│       │   ├── CandidateList.tsx
│       │   ├── ProgressBar.tsx
│       │   └── KashidashiTab.tsx  # kashidashi マッチ候補表示・選択
│       └── lib/
│           ├── api.ts            # fetch wrapper
│           └── types.ts          # TypeScript types
```

## OpenClaw スキル（最小化）

OpenClaw 側のスキルは API コールのみに簡略化：

```markdown
---
name: cd-rip
description: CD ripping via Rip Tower API
---

## Commands

### Rip
curl -s -X POST http://localhost:3900/api/rip \
  -H "Content-Type: application/json" \
  -d '{"drive_id": "XXXX..."}'

### Status
curl -s http://localhost:3900/api/jobs?status=active

### Eject
curl -s -X POST http://localhost:3900/api/drives/XXXX.../eject
```

OpenClaw は結果の URL を返すだけ。メタデータ編集は WebUI で行う。

## PWA 要件

- `vite-plugin-pwa` で Service Worker 自動生成
- オフライン時はキャッシュ済みシェルを表示（API エラー表示）
- ホーム画面追加対応（manifest.json: display: standalone, theme_color 設定）
- Push 通知は不要（Discord Webhook で十分）
- アイコン: 192x192, 512x512
- モバイルファーストデザイン（TailwindCSS のレスポンシブユーティリティ）

## レート制限・エラーハンドリング

### API レート制限
| サービス | 制限 | 対応 |
|---------|------|------|
| MusicBrainz | 1 req/sec | 1秒間隔 + User-Agent 必須 |
| Discogs | 60 req/min (認証済み) | 1秒間隔 |
| iTunes Search | 20 req/min | 3秒間隔 |
| LRCLIB | 明示なし | 1秒間隔（礼儀） |
| HMV.co.jp | 明示なし | 2秒間隔（スクレイピング） |

### リトライ戦略
- ネットワークエラー: 3回リトライ（exponential backoff: 1s, 3s, 9s）
- 429 Too Many Requests: Retry-After ヘッダ尊重
- タイムアウト: 各ソース10秒、全体30秒

## 将来の拡張候補（今は作らない）

- OSS 化（プラグインシステムで kashidashi 等を分離）
- Plex/Jellyfin との双方向連携（再生回数同期など）
- バーコードスキャン（スマホカメラ → JAN → ヒント自動入力）
- リッピング品質の AccurateRip 検証
- マルチユーザー対応（認証）

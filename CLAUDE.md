# Rip Tower — 実装ガイド

## プロジェクト概要

CD リッピング + メタデータ管理の Docker アプリ。3台の USB CD ドライブを搭載した物理タワー（arigato-nas）で動作する。
詳細設計は DESIGN.md を参照。

## 技術スタック

- **Backend**: Python 3.12 / FastAPI / SQLAlchemy (async) / SQLite / Alembic
- **Frontend**: React 19 / TypeScript / Vite / TailwindCSS v4 / vite-plugin-pwa
- **Infrastructure**: Docker Compose / privileged mode（CDドライブアクセス）
- **Port**: 3900

## コマンド

```bash
# 開発（バックエンド）
cd backend && uvicorn main:app --reload --port 3900

# 開発（フロントエンド）
cd frontend && npm run dev

# テスト
cd backend && python -m pytest tests/ -v

# Docker ビルド・起動
docker compose up --build -d

# フロントエンドビルド（Docker ビルド前に実行）
cd frontend && npm run build
```

## コーディング規約

### Python (Backend)
- 型ヒント必須（関数シグネチャ、変数は自明でなければ）
- async/await を基本とする（FastAPI のルーター、DB アクセス）
- import は標準ライブラリ → サードパーティ → ローカル の順
- Pydantic モデルで入出力を定義、dict を直接返さない
- ログは `logging.getLogger(__name__)` で取得、print 禁止
- 外部 API 呼び出しは httpx.AsyncClient を使用（requests 禁止）
- テストは pytest + pytest-asyncio

### TypeScript (Frontend)
- 関数コンポーネント + hooks のみ（class コンポーネント禁止）
- 型定義は `lib/types.ts` に集約
- API 呼び出しは `lib/api.ts` の fetch wrapper 経由
- TailwindCSS ユーティリティクラスでスタイリング（CSS ファイル最小限）
- モバイルファースト: `sm:` `md:` でブレイクポイント指定

## アーキテクチャ上の注意点

### パイプライン状態遷移
Job のステータスは厳密に管理する:
```
pending → identifying → resolving+ripping(並列) → encoding → review|finalizing → complete
                                                                                → error（どこからでも）
```
状態遷移は `services/pipeline.py` に集約。各サービスは自分の責務だけを実行し、次の状態への遷移はパイプラインが管理する。

### メタデータソースの追加
新しいメタデータソースを追加する場合:
1. `metadata/sources/base.py` の `MetadataSource` ABC を継承
2. `metadata/sources/` に新ファイル作成
3. `metadata/resolver.py` のソースリストに追加
ソースは並列実行されるので、各ソースは独立・冪等であること。

### WebSocket
- `services/websocket.py` に接続管理を集約
- パイプラインの各段階で `broadcast()` を呼び出す
- フロントエンドは `useWebSocket` hook で自動再接続

### CDドライブ管理
- ドライブは USB シリアル番号で一意に識別し、ユーザーが名前を付けて管理（drives テーブル）
- 抜き差しで /dev/sr* が変わっても drive_id で追跡可能
- `services/drive_monitor.py` が起動時・USB 抜き差し時にスキャンし current_path を更新
- Docker の privileged モードで `/dev/sr*` にアクセス
- cd-discid, cd-paranoia, cdda2wav, flac, metaflac, eject はコンテナ内にインストール済み
- 同一デバイスの同時アクセスは pipeline.py のデバイスロックで防止

### complete 後の編集（ライブラリ追跡）
- jobs テーブルの output_dir で最終出力先を追跡
- complete 後もメタデータ・アートワーク・歌詞を WebUI から編集可能
- `POST /api/jobs/:id/metadata/apply` でタグ・ファイル名に再反映（形式に応じて metaflac / ffmpeg 等）

### 複数枚組CD（album_group）
- 同アルバムの複数ディスクを album_group（UUID）で紐付け
- グループ内で artist/album/year/genre/アートワークを共有・同期
- グループ単位で review / 自動承認を判定（1枚ずつバラバラに承認されない）

## 外部依存

### システムパッケージ（Dockerfile でインストール）
- `cd-paranoia`: CD リッピング（メイン）
- `cdda2wav`: CD リッピング（フォールバック）
- `cd-discid`: ディスク ID 読み取り
- `flac`: FLAC エンコード
- `ffmpeg`: ALAC エンコード + タグ操作
- `lame`: MP3 エンコード
- `opus-tools`: Opus エンコード
- `eject`: CD イジェクト
- `curl`: Plex API 呼び出し

### Python パッケージ
- fastapi, uvicorn[standard]
- sqlalchemy[asyncio], aiosqlite
- alembic（DB マイグレーション）
- pydantic, pydantic-settings
- httpx（外部 API 呼び出し）
- python-multipart（ファイルアップロード）
- Pillow（アートワーク処理）

### npm パッケージ
- react, react-dom, react-router-dom
- @tanstack/react-query（サーバー状態管理）
- tailwindcss, @tailwindcss/vite
- vite-plugin-pwa
- typescript

## 移植元コード

`~/dev/openclaw-cd-rip/scripts/` に既存実装がある。主要ロジックは以下から移植:
- `metadata_resolver.py` → `backend/metadata/sources/` に分割
- `metadata_sanitizer.py` → `backend/metadata/sanitizer.py`
- `ripper.py` → `backend/services/ripper.py`
- `encoder.py` → `backend/services/encoder.py`
- `finalizer.py` → `backend/services/finalizer.py`
- `kashidashi.py` → `backend/metadata/sources/kashidashi.py` + `backend/routers/jobs.py`
- `notifier.py` → `backend/services/notifier.py`
- `normalize.py` → `backend/metadata/normalize.py`
- `disc_identity.py` → `backend/services/disc_identity.py`

移植時の変更点:
- 同期 → async に変換（subprocess → asyncio.create_subprocess_exec）
- requests → httpx.AsyncClient
- 結果をJSON返却 → DB に保存
- 設定を環境変数直読み → YAML 設定ファイル（data/config.yaml）+ pydantic-settings の Settings クラス

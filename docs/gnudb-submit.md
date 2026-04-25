# GnuDB Submit 機能 — 設計引き継ぎ

新セッションでこのまま実装に入れる粒度で書いてある。最初に DESIGN.md と CLAUDE.md を読んでから着手すること。

## 背景

Rip Tower では metadata resolver が CDDB / MusicBrainz / Discogs / iTunes / HMV / kashidashi を並列に問い合わせて候補を作る。CDDB（実体は GnuDB）はライブ盤・自主制作盤・古い邦楽コンピなど登録漏れの disc が結構あり、その場合は他ソースも軒並みヒットせず review に流れて Claude Code 等で人手解決する運用になっている。

GnuDB はユーザー投稿の相互互助 DB で、登録するのも参照するのも自由参加の建前。**人手で確定したメタデータをそのまま GnuDB に投稿し直せば、自分や次の人が同じ disc を入れたとき自動解決される**。今ある資産（人手で正したエントリ）を共有財に還元する話なので、UI に「承認 + GnuDB に送信」を生やす。

参考: 今回の郷ひろみ Hiromi Go 50th Anniversary Tour（[scripts/recover_hiromi_go.py](../scripts/recover_hiromi_go.py)）も全ソース空振りで人手解決した実例。

## GnuDB Submit プロトコル

エンドポイント:
```
POST https://gnudb.gnudb.org/~cddb/submit.cgi
```
（GET は不可。HTTP 経由で OK、メール投稿レガシー経路は不要）

必須 HTTP ヘッダ:

| ヘッダ | 値 | 備考 |
|---|---|---|
| `Category` | `rock` / `jazz` / `classical` / `folk` / `country` / `blues` / `newage` / `reggae` / `soundtrack` / `misc` / `data` のいずれか | freedb 11 カテゴリのみ。J-Pop は通常 `rock`、伝統邦楽・歌謡曲は `misc` 行きが慣習 |
| `Discid` | `8c0b710b` 等 8桁 hex | 既に `Job.disc_id` に保存済み |
| `User-Email` | 投稿者メアド | 設定から引く（後述） |
| `Submit-Mode` | `test` または `submit` | `test` は構文チェックのみで DB に書かない。プレビュー用 |
| `Content-Length` | バイト長 | httpx が自動付与するので明示不要のはず |
| `Charset` | `UTF-8` | 邦楽は必須。指定しないと non-ASCII が化ける |
| `X-Cddbd-Note` | 任意（70 char まで） | 例: `Submitted via rip-tower 0.1` |

リクエストボディ（xmcd 形式、ヘッダ部とは空行区切り）:
```
# xmcd
#
# Track frame offsets:
#       150
#       22207
#       (各 track の LBA、最後にディスク全長 in seconds)
#
# Disc length: 2931 seconds
#
# Revision: 0
# Submitted via: rip-tower 0.1.0
#
DISCID=8c0b710b
DTITLE=郷ひろみ / Hiromi Go 50th Anniversary Celebration Tour 2022 ～Keep Singing～ [DISC1]
DYEAR=2023
DGENRE=J-Pop
TTITLE0=2億4千万の瞳 -エキゾチック・ジャパン-
TTITLE1=セクシー・ユー(モンロー・ウォーク)
...
TTITLE10=男願 Groove!
EXTD=
EXTT0=
EXTT1=
...
EXTT10=
PLAYORDER=
```

ポイント:
- `DTITLE` は `アーティスト / アルバム` のスラッシュ区切り。前後にスペース1個ずつ。
- track index は 0-origin (`TTITLE0` = track 1)。
- `EXTD` / `EXTT*` / `PLAYORDER` は中身空でも行は出す。
- 行末は `\n`（`\r\n` でなくてもよい）。
- カテゴリは body の `DGENRE` と HTTP ヘッダ `Category` が別物。`DGENRE` は自由文字列で「J-Pop」OK、`Category` はホワイトリスト 11 種から選ぶ。

レスポンス（freedb 仕様）:
- `200 OK` ステータスコード。1行目に CDDB 応答コード:
  - `200 ...` accepted
  - `401 ...` permission denied
  - `500 ...` server error
  - `501 ...` invalid format
  - test mode は `200` でも DB には反映されない

## 設計方針

### UI フロー

review 画面（既存の承認ボタン群）に以下を追加:

```
[承認] [GnuDB にも送信] [破棄]   ← 既存承認ボタン横にチェックボックス追加
```

**ステップ**:
1. ユーザーが承認画面で確認 → 「GnuDB にも送信」にチェック → 承認押下
2. backend は通常の `approve_metadata` フローを走らせつつ、追加で GnuDB submit を別タスクで投げる
3. submit は **常に Submit-Mode: test を先に投げて成功したら submit 本送信** の 2 段階（rejected を本番に送って弾かれるとログが汚い）
4. 結果（accepted / rejected + 理由 + raw response）を `gnudb_submissions` テーブルに記録
5. 失敗時は WS で `job:gnudb_submitted` { status: "rejected", reason } をブロードキャストして UI 側でトースト表示

「承認後に手動で送る」パスも別途用意する（**review 経由ではない complete 済み job も送れるように**）:
- `POST /api/jobs/{job_id}/gnudb/preview` → test mode で投げて結果を返す（実際の xmcd を含めて表示用）
- `POST /api/jobs/{job_id}/gnudb/submit` → 本送信
- `GET /api/jobs/{job_id}/gnudb` → 過去 submit 履歴

### Category マッピング

`JobMetadata.genre` は自由文字列なので freedb 11 カテゴリへ落とす関数を `backend/services/gnudb_submit.py` 内に持つ:

| genre 入力（norm 後 substring 検索） | Category |
|---|---|
| jazz, fusion | jazz |
| classical, クラシック, 交響, 協奏 | classical |
| folk, 民謡 | folk |
| country, カントリー | country |
| blues, ブルース | blues |
| reggae, レゲエ | reggae |
| newage, new age, アンビエント, ヒーリング | newage |
| soundtrack, ost, サウンドトラック, 劇伴 | soundtrack |
| その他（rock, pop, J-Pop, 歌謡曲, hip hop, R&B, …） | rock |
| 不明 / 空 | misc |

UI 側でも override 可能にすると楽（ドロップダウン）。

### DB スキーマ

新テーブル `gnudb_submissions`:

```python
class GnudbSubmission(Base):
    __tablename__ = "gnudb_submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    disc_id: Mapped[str]                       # 投稿時点のスナップショット
    category: Mapped[str]                       # 11 カテゴリのどれか
    submit_mode: Mapped[str]                    # "test" | "submit"
    response_code: Mapped[int | None]           # CDDB 応答コード（200/401/500…）
    response_body: Mapped[str | None]           # raw response（短いので全文保存）
    xmcd_body: Mapped[str]                      # 投げた xmcd（後で原因調査するため）
    error: Mapped[str | None]                   # ネットワーク失敗時の例外メッセージ
    submitted_at: Mapped[datetime] = mapped_column(default=now_utc)
```

alembic 新規 migration 1本。

### 設定

`config.yaml` の `integrations` に追加:

```yaml
integrations:
  gnudb_url: https://gnudb.gnudb.org      # base URL（テスト用に変えられるよう外出し）
  gnudb_email: kou997@gmail.com           # User-Email ヘッダで使う
  gnudb_client_name: rip-tower            # X-Cddbd-Note と Submitted via に使う
  gnudb_client_version: 0.1.0
  gnudb_enabled: true                     # false なら UI のチェックボックス自体を出さない
```

`backend/config.py` の `IntegrationsConfig` と `DEFAULT_CONFIG` 両方に追加。

### Submit 関数のシグネチャ

`backend/services/gnudb_submit.py`:

```python
async def build_xmcd(job_id: str) -> str:
    """JobMetadata + Track + identity から xmcd 形式のボディを生成。"""

async def submit(job_id: str, *, mode: Literal["test", "submit"], category: str | None = None) -> GnudbSubmission:
    """build_xmcd → POST → レスポンス解析 → DB に記録 → broadcast。"""

async def submit_with_test_first(job_id: str, *, category: str | None = None) -> GnudbSubmission:
    """test → 200 なら submit、それ以外は test の結果を返して停止。"""
```

`metadata/sources/cddb.py` で読み取りに使ってる identity から track offsets と total seconds を取れるはずなので、その経路を再利用する。

## 実装ステップ

1. **alembic migration**: `gnudb_submissions` テーブル追加
2. **`backend/services/gnudb_submit.py`** 新規:
   - `build_xmcd()` — JobMetadata / Track / identity を読んで文字列を組み立て
   - `_categorize(genre, album, artist)` — freedb 11 カテゴリへの分類
   - `submit()` — httpx.AsyncClient で POST、レスポンスから response_code パース、DB 記録
   - `submit_with_test_first()`
3. **config.py / config.yaml**: 上記設定追加
4. **`backend/routers/jobs.py`** に endpoint 追加:
   - `POST /api/jobs/{job_id}/gnudb/preview` (test mode)
   - `POST /api/jobs/{job_id}/gnudb/submit`
   - `GET /api/jobs/{job_id}/gnudb`
5. **`approve_metadata` endpoint 改修**: リクエストボディに `submit_to_gnudb: bool, gnudb_category: str | None` を追加し、true なら finalize 後に `submit_with_test_first` を非同期で発火
6. **frontend**:
   - review 画面の承認ボタン横にチェックボックス（カテゴリ override は折り畳み）
   - complete 済み job の詳細画面に「GnuDB に送信」ボタン
   - submit 履歴セクション（accepted / rejected アイコン + raw response 折り畳み）
7. **テスト**:
   - `tests/test_gnudb_submit.py` で `build_xmcd` のスナップショット（郷ひろみ disc 1 を fixture に）
   - `_categorize` の境界ケース（jazz / 交響 / 歌謡曲 / 空文字）
   - submit は httpx_mock で 200 / 401 / 500 / ネットワーク失敗パターン

## 落とし穴

- **GnuDB は accepted した entry の修正ができない**（重複登録扱いになりがち）。ので **一度 submit したら同 job からは再 submit させない**。`gnudb_submissions` に `mode='submit'` で `response_code=200` が既にあれば、UI からは送信不可にする
- **multi-disc は disc ごとに別 entry**。`album_group` でまとめずに、各 job 個別に投げる。DTITLE 末尾の `[DISC1]` / `[DISC2]` は GnuDB 慣行的に OK
- **compilation の取り扱い**: `is_compilation=true` のときは `DTITLE=Various / アルバム名` にする慣習。各 track のアーティストは `EXTT{n}` ではなく `TTITLE{n}=アーティスト / 曲名` 形式にするのが freedb の流儀（半角スラッシュ + スペース 2 個）
- **kashidashi 経由で artist=null のままサニタイザを通った job** は submit 候補から除外（disc_id だけ書いて DTITLE 空みたいなのを送らない）
- **track titles に `\n` や `=` が混入していると壊れる**。`build_xmcd` で `replace("\n", " ").replace("\r", " ")` のサニタイズを通す
- **freedb の 1 行最大長は 256 byte**（UTF-8 ベース）。長いタイトルは `TTITLE0= ... \nTTITLE0= ... 続き` のように **同名キーで複数行**に分割するのが仕様。日本語タイトルは長くなりがちなので分割ロジック必要
- **rate limit**: 公式仕様には書いてないが、明らかに連投は弾かれる挙動なので、同一クライアントから 1 req/sec 以上にはしないアダプティブな間隔を `httpx.AsyncClient` に持たせる。実用上 jobs から手動で投げるだけなのでまず詰まらないはずだが念のため

## テストデータ

実装中の動作確認は **本番 GnuDB ではなく test mode** で。`Submit-Mode: test` にしておけば DB には書き込まれない。

新セッション開始時に手元で再現する場合のテストケース:
- job `2a29f9d0-d87f-44d3-9955-b84407cba47e` (郷ひろみ Disc 1) → 11 track、UTF-8、disc_id `8c0b710b`、category `rock`
- 既に CDDB に存在する disc を test mode で投げて `403`/`409` 系の応答が返ることを確認

## 参考

- GnuDB 公式 howto: https://gnudb.org/howtognudb.php
- freedb XMCD 仕様: 検索 → "freedb format specification" あるいは古い CDDBHOWTO
- 既存の読み取り側: [backend/metadata/sources/cddb.py](../backend/metadata/sources/cddb.py)

import { useParams, Link } from "react-router-dom";
import { useState } from "react";
import { useJob } from "../hooks/useJob";

type Tab = "metadata" | "artwork" | "lyrics" | "kashidashi";

export default function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error } = useJob(id!);
  const [tab, setTab] = useState<Tab>("metadata");

  if (isLoading) return <div className="p-4 text-gray-400">読み込み中...</div>;
  if (error) return <div className="p-4 text-red-400">エラー: {(error as Error).message}</div>;
  if (!data) return null;

  const { job, metadata, tracks, candidates, artworks, kashidashi_candidates } = data;
  const tabs: { key: Tab; label: string }[] = [
    { key: "metadata", label: "メタデータ" },
    { key: "artwork", label: "アートワーク" },
    { key: "lyrics", label: "歌詞" },
    { key: "kashidashi", label: "kashidashi" },
  ];

  return (
    <div>
      {/* Header */}
      <div className="sticky top-0 bg-[#0f0f1a] z-10 px-4 py-3 border-b border-gray-800">
        <Link to="/" className="text-gray-400 text-sm">← 戻る</Link>
        <div className="text-xs text-gray-500 mt-1">{job.status}</div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-800">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex-1 py-2 text-xs text-center ${
              tab === t.key
                ? "text-rose-400 border-b-2 border-rose-400"
                : "text-gray-500"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="p-4">
        {tab === "metadata" && (
          <div>
            {/* Album info */}
            <div className="flex gap-3 mb-4">
              <div className="w-16 h-16 bg-gray-800 rounded flex items-center justify-center text-2xl">
                🎵
              </div>
              <div>
                <div className="font-bold">{metadata?.artist || "Unknown Artist"}</div>
                <div className="text-sm text-gray-400">{metadata?.album || "Unknown Album"}</div>
                <div className="text-xs text-gray-500">
                  {metadata?.year} / {metadata?.genre}
                </div>
              </div>
            </div>

            {/* Confidence */}
            {metadata && (
              <div className="text-xs text-gray-400 mb-3">
                Source: {metadata.source} / Confidence: {metadata.confidence}
              </div>
            )}

            {/* Track table */}
            <div className="mb-4">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-gray-500 text-xs border-b border-gray-800">
                    <th className="text-left py-1">#</th>
                    <th className="text-left">タイトル</th>
                    <th className="text-center">Rip</th>
                    <th className="text-center">Enc</th>
                  </tr>
                </thead>
                <tbody>
                  {tracks.map((t) => (
                    <tr key={t.track_num} className="border-b border-gray-800/50">
                      <td className="py-1 text-gray-500">{t.track_num}</td>
                      <td className="truncate max-w-[200px]">{t.title || `Track ${t.track_num}`}</td>
                      <td className="text-center">{statusIcon(t.rip_status)}</td>
                      <td className="text-center">{statusIcon(t.encode_status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Candidates */}
            {candidates.length > 0 && (
              <details className="mb-4">
                <summary className="text-sm text-gray-400 cursor-pointer">
                  他の候補 ({candidates.length}件)
                </summary>
                {candidates.map((c) => (
                  <div key={c.id} className="p-2 bg-[#16213e] rounded mt-1 text-sm">
                    <div>{c.artist} — {c.album}</div>
                    <div className="text-xs text-gray-500">{c.source}: {c.confidence}点</div>
                  </div>
                ))}
              </details>
            )}

            {/* Approve button */}
            {job.status === "review" && (
              <button className="w-full py-3 bg-rose-600 rounded-lg font-bold text-center">
                承認して完了
              </button>
            )}
          </div>
        )}

        {tab === "artwork" && (
          <div className="grid grid-cols-2 gap-3">
            {artworks.map((a) => (
              <div
                key={a.id}
                className={`p-2 rounded-lg border ${
                  a.selected ? "border-rose-400 bg-rose-900/20" : "border-gray-700 bg-[#16213e]"
                }`}
              >
                <div className="aspect-square bg-gray-800 rounded mb-1 flex items-center justify-center text-3xl">
                  🖼️
                </div>
                <div className="text-xs text-gray-400">{a.source}</div>
                {a.width && <div className="text-xs text-gray-500">{a.width}x{a.height}</div>}
              </div>
            ))}
            {artworks.length === 0 && (
              <p className="text-gray-500 col-span-2 text-center">アートワークなし</p>
            )}
          </div>
        )}

        {tab === "lyrics" && (
          <div>
            {tracks.map((t) => (
              <div key={t.track_num} className="mb-3">
                <div className="text-sm font-medium">
                  Track {t.track_num}: {t.title || "Unknown"}
                </div>
                <div className="text-xs text-gray-500">
                  {t.lyrics_source ? `Source: ${t.lyrics_source}` : "歌詞なし"}
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === "kashidashi" && (
          <div>
            {kashidashi_candidates.map((k) => (
              <div
                key={k.id}
                className={`p-3 rounded-lg mb-2 border ${
                  k.matched ? "border-rose-400 bg-rose-900/20" : "border-gray-700 bg-[#16213e]"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span>{k.matched ? "●" : "○"}</span>
                  <div>
                    <div className="text-sm">{k.artist} / {k.title}</div>
                    <div className="text-xs text-gray-500">
                      Score: {k.score?.toFixed(0)} ({k.match_type})
                    </div>
                  </div>
                </div>
              </div>
            ))}
            {kashidashi_candidates.length === 0 && (
              <p className="text-gray-500 text-center">マッチ候補なし</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function statusIcon(status: string): string {
  switch (status) {
    case "ok": return "✅";
    case "ok_degraded": return "⚠️";
    case "failed": return "❌";
    case "ripping":
    case "encoding": return "🔄";
    default: return "○";
  }
}

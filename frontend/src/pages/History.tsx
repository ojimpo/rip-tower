import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";

type SourceFilter = "" | "kashidashi" | "owned" | "unknown";

export default function History() {
  const [filter, setFilter] = useState<SourceFilter>("");

  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: () => api.getStats() as Promise<{ total: number; by_source_type: Record<string, number> }>,
  });

  const { data: history } = useQuery({
    queryKey: ["history", filter],
    queryFn: () =>
      api.getHistory(filter ? { source_type: filter } : undefined) as Promise<{
        items: Array<{ job_id: string; artist: string | null; album: string | null; source_type: string; completed_at: string | null }>;
      }>,
  });

  const filters: { key: SourceFilter; label: string }[] = [
    { key: "", label: "全て" },
    { key: "kashidashi", label: "図書館" },
    { key: "owned", label: "手持ち" },
    { key: "unknown", label: "未分類" },
  ];

  return (
    <div>
      <div className="sticky top-0 bg-[#0f0f1a] z-10 px-4 py-3 border-b border-gray-800">
        <h1 className="text-xl font-bold">リッピング履歴</h1>
      </div>

      {/* Filters */}
      <div className="flex gap-2 px-4 mt-3">
        {filters.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`px-3 py-1 rounded-full text-xs ${
              filter === f.key
                ? "bg-rose-600 text-white"
                : "bg-gray-800 text-gray-400"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Stats */}
      {stats && (
        <div className="mx-4 mt-3 p-3 bg-[#16213e] rounded-lg">
          <div className="text-center text-2xl font-bold">{stats.total}</div>
          <div className="text-center text-xs text-gray-400 mb-2">合計</div>
          <div className="flex justify-center gap-4 text-xs text-gray-400">
            <span>図書館: {stats.by_source_type.kashidashi || 0}</span>
            <span>手持ち: {stats.by_source_type.owned || 0}</span>
            <span>未分類: {stats.by_source_type.unknown || 0}</span>
          </div>
        </div>
      )}

      {/* List */}
      <div className="px-4 mt-3 mb-4">
        {history?.items.map((item) => (
          <Link
            key={item.job_id}
            to={`/job/${item.job_id}`}
            className="block p-3 bg-[#16213e] rounded-lg mb-2"
          >
            <div className="text-sm font-medium">
              {item.artist || "Unknown"} / {item.album || "Unknown"}
            </div>
            <div className="flex items-center gap-2 text-xs text-gray-500 mt-1">
              <span>
                {item.source_type === "kashidashi"
                  ? "📚 図書館"
                  : item.source_type === "owned"
                    ? "💿 手持ち"
                    : "❓ 未分類"}
              </span>
              {item.completed_at && (
                <span>{new Date(item.completed_at).toLocaleDateString("ja-JP")}</span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

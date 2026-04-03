import { useQuery } from "@tanstack/react-query";
import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { HistoryItem } from "../lib/types";

type SourceFilter = "" | "kashidashi" | "owned" | "unknown";

interface HistoryResponse {
  items: HistoryItem[];
  total: number;
  has_more: boolean;
}

export default function History() {
  const [filter, setFilter] = useState<SourceFilter>("");
  const [page, setPage] = useState(0);
  const limit = 50;

  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: () => api.getStats() as Promise<{ total: number; by_source_type: Record<string, number> }>,
  });

  const { data: history, isLoading } = useQuery({
    queryKey: ["history", filter, page],
    queryFn: () => {
      const params: Record<string, string> = { limit: String(limit), offset: String(page * limit) };
      if (filter) params.source_type = filter;
      return api.getHistory(params) as Promise<HistoryResponse>;
    },
  });

  const filters: { key: SourceFilter; label: string }[] = [
    { key: "", label: "\u5168\u3066" },
    { key: "kashidashi", label: "\u56F3\u66F8\u9928" },
    { key: "owned", label: "\u624B\u6301\u3061" },
    { key: "unknown", label: "\u672A\u5206\u985E" },
  ];

  // Group items by month
  const groupedItems = useMemo(() => {
    if (!history?.items) return [];
    const groups = new Map<string, HistoryItem[]>();

    for (const item of history.items) {
      const date = item.completed_at ? new Date(item.completed_at) : new Date();
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(item);
    }

    return Array.from(groups.entries()).map(([month, items]) => ({ month, items }));
  }, [history]);

  const sourceTypeBadge = (item: HistoryItem) => {
    if (item.source_type === "kashidashi") {
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400">
          kashidashi{item.kashidashi_id ? ` #${item.kashidashi_id}` : ""}
        </span>
      );
    }
    if (item.source_type === "owned") {
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400">
          {"\u624B\u6301\u3061"}
        </span>
      );
    }
    return (
      <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-500/15 text-gray-400">
        {"\u672A\u5206\u985E"}
      </span>
    );
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "";
    const d = new Date(dateStr);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  };

  return (
    <div>
      {/* Header */}
      <header className="sticky top-0 z-50 backdrop-blur-xl bg-[#0f0f1a]/80 border-b border-white/5">
        <div className="flex items-center justify-between px-4 py-3">
          <h1 className="text-lg font-bold tracking-tight">History</h1>
        </div>
      </header>

      {/* Filter Tabs */}
      <div className="px-4 pt-3 pb-2">
        <div className="flex gap-2">
          {filters.map((f) => (
            <button
              key={f.key}
              onClick={() => { setFilter(f.key); setPage(0); }}
              className={`text-xs font-medium px-3 py-1.5 rounded-full border transition ${
                filter === f.key
                  ? "bg-[#e94560]/15 text-[#e94560] border-[#e94560]/30"
                  : "bg-transparent text-gray-500 border-white/5"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Stats Summary */}
      {stats && (
        <div className="mx-4 mt-2 mb-4">
          <div className="rounded-xl bg-[#16213e] border border-white/5 px-4 py-3">
            <div className="grid grid-cols-4 gap-3 text-center">
              <div>
                <p className="text-2xl font-bold text-white">{stats.total}</p>
                <p className="text-[10px] text-gray-500 tracking-wider mt-0.5">{"\u5168\u3066"}</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-blue-400">{stats.by_source_type.kashidashi || 0}</p>
                <p className="text-[10px] text-gray-500 tracking-wider mt-0.5">{"\u56F3\u66F8\u9928"}</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-purple-400">{stats.by_source_type.owned || 0}</p>
                <p className="text-[10px] text-gray-500 tracking-wider mt-0.5">{"\u624B\u6301\u3061"}</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-400">{stats.by_source_type.unknown || 0}</p>
                <p className="text-[10px] text-gray-500 tracking-wider mt-0.5">{"\u672A\u5206\u985E"}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Monthly Groups */}
      <section className="mx-4 mb-6">
        {groupedItems.map(({ month, items }) => (
          <div key={month} className="mb-5">
            <div className="flex items-center gap-2 mb-2">
              <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider">{month}</h3>
              <span className="text-[10px] text-gray-600">{items.length} albums</span>
            </div>
            <div className="rounded-xl bg-[#16213e] border border-white/5 divide-y divide-white/5 overflow-hidden">
              {items.map((item) => (
                <Link
                  key={item.job_id}
                  to={`/job/${item.job_id}`}
                  className="flex items-center gap-3 px-3 py-2.5 hover:bg-white/5 transition"
                >
                  <div className="w-11 h-11 rounded-lg bg-gradient-to-br from-pink-900/50 to-purple-900/50 flex items-center justify-center shrink-0 overflow-hidden">
                    {item.artwork_url ? (
                      <img src={item.artwork_url} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <svg className="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                      </svg>
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">
                      {item.artist || "Unknown"} / {item.album || "Unknown"}
                    </p>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      {sourceTypeBadge(item)}
                      {item.track_count && (
                        <span className="text-[10px] text-gray-600">{item.track_count} tracks</span>
                      )}
                    </div>
                  </div>
                  <span className="text-[10px] text-gray-600 shrink-0">
                    {formatDate(item.completed_at)}
                  </span>
                </Link>
              ))}
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="text-center text-gray-500 text-sm py-8">{"\u8AAD\u307F\u8FBC\u307F\u4E2D"}...</div>
        )}

        {!isLoading && groupedItems.length === 0 && (
          <div className="text-center text-gray-500 text-sm py-8">{"\u5C65\u6B74\u306A\u3057"}</div>
        )}

        {/* Load more */}
        {history?.has_more && (
          <button
            onClick={() => setPage((p) => p + 1)}
            className="w-full py-3 rounded-xl bg-white/5 hover:bg-white/10 text-xs font-medium text-gray-400 hover:text-white transition"
          >
            {"\u3082\u3063\u3068\u8AAD\u307F\u8FBC\u3080"} →
          </button>
        )}
      </section>
    </div>
  );
}

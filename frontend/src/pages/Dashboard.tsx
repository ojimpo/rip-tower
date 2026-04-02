import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useWebSocket } from "../hooks/useWebSocket";
import type { Drive, WsEvent } from "../lib/types";

export default function Dashboard() {
  const queryClient = useQueryClient();

  const { data: jobsData } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.getJobs(),
    refetchInterval: 5000,
  });

  const { data: drives } = useQuery<Drive[]>({
    queryKey: ["drives"],
    queryFn: () => api.getDrives() as Promise<Drive[]>,
    refetchInterval: 10000,
  });

  const onWsEvent = useCallback(
    (event: WsEvent) => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      if (event.type === "drive:connected" || event.type === "drive:disconnected") {
        queryClient.invalidateQueries({ queryKey: ["drives"] });
      }
    },
    [queryClient]
  );

  useWebSocket(onWsEvent);

  const jobs = (jobsData as { jobs?: Array<{ job_id: string; status: string; url: string }> })?.jobs ?? [];
  const activeJobs = jobs.filter((j) => !["complete", "error"].includes(j.status));
  const reviewJobs = jobs.filter((j) => j.status === "review");
  const errorJobs = jobs.filter((j) => j.status === "error");
  const recentComplete = jobs.filter((j) => j.status === "complete").slice(0, 5);

  const needsAttention = [...reviewJobs, ...errorJobs];

  return (
    <div>
      {/* Header */}
      <div className="sticky top-0 bg-[#0f0f1a] z-10 px-4 py-3 flex items-center justify-between border-b border-gray-800">
        <h1 className="text-xl font-bold">Rip Tower</h1>
        <Link to="/settings" className="text-gray-400 text-xl">⚙️</Link>
      </div>

      {/* Needs attention */}
      {needsAttention.length > 0 && (
        <div className="mx-4 mt-3 p-3 bg-amber-900/30 border border-amber-700 rounded-lg">
          <h2 className="text-amber-400 text-sm font-bold mb-2">
            ⚠️ 要対応（{needsAttention.length}件）
          </h2>
          {needsAttention.map((job) => (
            <Link
              key={job.job_id}
              to={`/job/${job.job_id}`}
              className="block text-sm text-gray-300 py-1"
            >
              {job.status === "error" ? "❌" : "⏸"} {job.job_id.slice(0, 8)}... — {job.status}
            </Link>
          ))}
        </div>
      )}

      {/* Active jobs */}
      {activeJobs.length > 0 && (
        <div className="mx-4 mt-3">
          <h2 className="text-sm font-bold text-gray-400 mb-2">アクティブ</h2>
          {activeJobs.map((job) => (
            <Link
              key={job.job_id}
              to={`/job/${job.job_id}`}
              className="block p-3 bg-[#16213e] rounded-lg mb-2"
            >
              <div className="text-sm font-medium">{job.job_id.slice(0, 8)}...</div>
              <div className="text-xs text-gray-400">{job.status}</div>
            </Link>
          ))}
        </div>
      )}

      {/* Drives */}
      {drives && drives.length > 0 && (
        <div className="mx-4 mt-4">
          <h2 className="text-sm font-bold text-gray-400 mb-2">ドライブ</h2>
          {drives.map((drive) => (
            <div
              key={drive.drive_id}
              className="flex items-center justify-between p-2 bg-[#16213e] rounded-lg mb-1"
            >
              <div className="flex items-center gap-2">
                <span className={drive.current_path ? "text-green-400" : "text-gray-600"}>●</span>
                <span className="text-sm">{drive.name}</span>
                {drive.current_path && (
                  <span className="text-xs text-gray-500">{drive.current_path}</span>
                )}
              </div>
              {drive.current_path && (
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    api.ejectDrive(drive.drive_id);
                  }}
                  className="text-sm px-2 py-1 bg-gray-700 rounded hover:bg-gray-600"
                >
                  ⏏
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Recent completions */}
      {recentComplete.length > 0 && (
        <div className="mx-4 mt-4 mb-4">
          <h2 className="text-sm font-bold text-gray-400 mb-2">最近の完了</h2>
          {recentComplete.map((job) => (
            <Link
              key={job.job_id}
              to={`/job/${job.job_id}`}
              className="block text-sm text-gray-300 py-1"
            >
              ✓ {job.job_id.slice(0, 8)}...
            </Link>
          ))}
        </div>
      )}

      {jobs.length === 0 && (
        <div className="mx-4 mt-8 text-center text-gray-500">
          <p className="text-2xl mb-2">💿</p>
          <p>CDを入れてリッピングを開始</p>
        </div>
      )}
    </div>
  );
}

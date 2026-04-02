import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useWebSocket } from "../hooks/useWebSocket";
import TrackProgress from "../components/TrackProgress";
import type { Drive, JobSummary } from "../lib/types";

function statusColor(status: string): { bg: string; text: string; gradient: string } {
  switch (status) {
    case "ripping":
      return { bg: "bg-emerald-500/20", text: "text-emerald-400", gradient: "from-emerald-600/40 to-emerald-900/40" };
    case "encoding":
      return { bg: "bg-blue-500/20", text: "text-blue-400", gradient: "from-blue-600/40 to-blue-900/40" };
    case "identifying":
    case "resolving":
      return { bg: "bg-purple-500/20", text: "text-purple-400", gradient: "from-purple-600/40 to-purple-900/40" };
    case "finalizing":
      return { bg: "bg-cyan-500/20", text: "text-cyan-400", gradient: "from-cyan-600/40 to-cyan-900/40" };
    default:
      return { bg: "bg-gray-500/20", text: "text-gray-400", gradient: "from-gray-600/40 to-gray-900/40" };
  }
}

function elapsedText(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function progressPercent(job: JobSummary): number {
  if (job.track_count && job.tracks_done != null) {
    const trackProgress = job.current_track_percent ? job.current_track_percent / 100 : 0;
    return Math.round(((job.tracks_done + trackProgress) / job.track_count) * 100);
  }
  return 0;
}

export default function Dashboard() {
  const { data: jobsData } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.getJobs() as Promise<{ jobs: JobSummary[] }>,
    refetchInterval: 5000,
  });

  const { data: drives } = useQuery<Drive[]>({
    queryKey: ["drives"],
    queryFn: () => api.getDrives() as Promise<Drive[]>,
    refetchInterval: 10000,
  });

  useWebSocket();

  const jobs = jobsData?.jobs ?? [];
  const activeJobs = jobs.filter((j) => !["complete", "error", "review"].includes(j.status));
  const reviewJobs = jobs.filter((j) => j.status === "review");
  const errorJobs = jobs.filter((j) => j.status === "error");
  const recentComplete = jobs.filter((j) => j.status === "complete").slice(0, 5);

  const needsAttention = [...reviewJobs, ...errorJobs];

  return (
    <div>
      {/* Header */}
      <header className="sticky top-0 z-50 backdrop-blur-xl bg-[#0f0f1a]/80 border-b border-white/5">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#e94560] to-purple-600 flex items-center justify-center text-sm font-bold">
              R
            </div>
            <h1 className="text-lg font-bold tracking-tight">Rip Tower</h1>
          </div>
          <div className="flex items-center gap-1">
            <Link
              to="/import"
              className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-white/10 transition"
              title="WAV Import"
            >
              <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
            </Link>
          <Link
            to="/settings"
            className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-white/10 transition"
          >
            <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </Link>
          </div>
        </div>
      </header>

      {/* Needs Attention */}
      {needsAttention.length > 0 && (
        <section className="mx-3 mt-3">
          <div className="rounded-xl overflow-hidden border border-amber-500/30 bg-amber-950/30">
            <div className="px-3 py-2 bg-amber-500/10 border-b border-amber-500/20 flex items-center gap-2">
              <span className="text-amber-400 text-sm font-semibold">
                {"\u8981\u5BFE\u5FDC"}({needsAttention.length}{"\u4EF6"})
              </span>
            </div>
            {needsAttention.map((job, i) => (
              <Link
                key={job.job_id}
                to={`/job/${job.job_id}`}
                className={`flex items-start gap-3 px-3 py-2.5 hover:bg-white/5 transition ${
                  i < needsAttention.length - 1 ? "border-b border-white/5" : ""
                }`}
              >
                <span className="text-lg mt-0.5">
                  {job.status === "error" ? "\u274C" : "\u23F8\uFE0F"}
                </span>
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-medium truncate ${job.status === "error" ? "text-red-300" : "text-amber-200"}`}>
                    {job.status === "error" && job.drive_name
                      ? `${job.drive_name}: ${job.error_message || "Error"}`
                      : job.artist && job.album
                        ? `${job.artist} / ${job.album}`
                        : `Job ${job.job_id.slice(0, 8)}`}
                  </p>
                  <p className={`text-xs mt-0.5 ${job.status === "error" ? "text-red-400/70" : "text-amber-400/70"}`}>
                    {job.status === "review" ? "Review\u5F85\u3061" : job.status}{" "}
                    · {elapsedText(job.updated_at)}
                  </p>
                </div>
                <svg className="w-4 h-4 text-amber-500/50 mt-1.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
                </svg>
              </Link>
            ))}
          </div>
        </section>
      )}

      {/* Active Jobs */}
      {activeJobs.length > 0 && (
        <section className="mx-3 mt-4">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2 px-1">Active Jobs</h2>
          {activeJobs.map((job) => {
            const colors = statusColor(job.status);
            const percent = progressPercent(job);
            const showTrackProgress = (job.status === "ripping" || job.status === "encoding") && job.track_count;

            return (
              <Link
                key={job.job_id}
                to={`/job/${job.job_id}`}
                className="block rounded-xl bg-[#16213e] border border-white/5 overflow-hidden hover:border-white/10 transition mb-3"
              >
                <div className={`px-4 py-3 flex items-center gap-3 ${showTrackProgress ? "border-b border-white/5" : ""}`}>
                  <div className={`w-10 h-10 rounded-lg bg-gradient-to-br ${colors.gradient} flex items-center justify-center text-lg`}>
                    {job.status === "ripping" ? "\u25B6\uFE0F" : "\uD83C\uDFB5"}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${colors.bg} ${colors.text}`}>
                        {job.status.charAt(0).toUpperCase() + job.status.slice(1)}
                      </span>
                      {job.drive_name && (
                        <span className="text-xs text-gray-500">{job.drive_name}</span>
                      )}
                    </div>
                    <p className="text-sm font-medium mt-0.5 truncate">
                      {job.artist && job.album
                        ? `${job.artist} / ${job.album}`
                        : `Job ${job.job_id.slice(0, 8)}`}
                    </p>
                  </div>
                  {job.tracks_done != null && job.track_count && !showTrackProgress && (
                    <span className={`text-xs font-mono ${colors.text}`}>
                      {job.tracks_done}/{job.track_count}
                    </span>
                  )}
                </div>

                {/* Show track-level progress for ripping jobs via data from job detail (simplified here) */}
                {showTrackProgress && job.track_count && (
                  <TrackProgress
                    tracks={Array.from({ length: job.track_count }, (_, i) => {
                      const num = i + 1;
                      const isDone = job.tracks_done != null && num <= job.tracks_done;
                      const isCurrent = job.current_track === num;
                      return {
                        track_num: num,
                        title: null,
                        rip_status: isDone ? "ok" : isCurrent ? "ripping" : "pending",
                        encode_status: "pending",
                        rip_progress: isCurrent ? (job.current_track_percent ?? null) : null,
                      };
                    })}
                    maxVisible={5}
                  />
                )}

                {/* Progress bar */}
                {percent > 0 && (
                  <div className="h-0.5 bg-gray-800">
                    <div
                      className={`h-full bg-gradient-to-r ${
                        job.status === "encoding"
                          ? "from-blue-500 to-blue-400"
                          : "from-emerald-500 to-emerald-400"
                      } transition-all`}
                      style={{ width: `${percent}%` }}
                    />
                  </div>
                )}
              </Link>
            );
          })}
        </section>
      )}

      {/* Drives */}
      {drives && drives.length > 0 && (
        <section className="mx-3 mt-4">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2 px-1">Drives</h2>
          <div className="rounded-xl bg-[#16213e] border border-white/5 divide-y divide-white/5">
            {drives.map((drive) => {
              const isOnline = !!drive.current_path;
              return (
                <div key={drive.drive_id} className={`px-4 py-3 ${!isOnline ? "opacity-50" : ""}`}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      <div className={`w-2 h-2 rounded-full shrink-0 ${isOnline ? "bg-emerald-400" : "bg-gray-600"}`} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{drive.name}</span>
                          {drive.has_disc && <span className="text-lg" title="CD inserted">{"\uD83D\uDCBF"}</span>}
                          {isOnline && !drive.has_disc && (
                            <span className="text-xs text-gray-600">{"\u7A7A"}</span>
                          )}
                        </div>
                        {drive.disc_info && drive.disc_info.artist ? (
                          <p className="text-xs text-gray-400 truncate mt-0.5">
                            {drive.disc_info.artist} / {drive.disc_info.album}
                            {drive.disc_info.track_count && ` · ${drive.disc_info.track_count} tracks`}
                          </p>
                        ) : (
                          <p className="text-xs text-gray-500 mt-0.5">
                            {isOnline ? drive.current_path : "\u672A\u63A5\u7D9A"}
                          </p>
                        )}
                      </div>
                    </div>
                    {isOnline && (
                      <button
                        onClick={(e) => {
                          e.preventDefault();
                          api.ejectDrive(drive.drive_id);
                        }}
                        className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition text-lg"
                        title="Eject"
                      >
                        {"\u23CF"}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Recent Completions */}
      {recentComplete.length > 0 && (
        <section className="mx-3 mt-4 mb-6">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2 px-1">Recent</h2>
          <div className="rounded-xl bg-[#16213e] border border-white/5 divide-y divide-white/5">
            {recentComplete.map((job) => (
              <Link
                key={job.job_id}
                to={`/job/${job.job_id}`}
                className="flex items-center gap-3 px-4 py-3 hover:bg-white/5 transition"
              >
                <div className="w-10 h-10 rounded bg-gray-700 flex items-center justify-center text-2xl shrink-0 overflow-hidden">
                  {job.artwork_url ? (
                    <img src={job.artwork_url} alt="" className="w-full h-full object-cover" />
                  ) : (
                    "\uD83C\uDFB6"
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">
                    {job.artist && job.album
                      ? `${job.artist} / ${job.album}`
                      : `Job ${job.job_id.slice(0, 8)}`}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {job.track_count && `${job.track_count} tracks · `}FLAC · {elapsedText(job.updated_at)}
                  </p>
                </div>
                <span className="text-emerald-400 text-xs">{"\u2713"}</span>
              </Link>
            ))}
          </div>
          <Link to="/history" className="block text-center text-xs text-gray-500 hover:text-gray-400 mt-2 py-1 transition">
            {"\u5C65\u6B74\u3092\u3059\u3079\u3066\u8868\u793A"} →
          </Link>
        </section>
      )}

      {jobs.length === 0 && (
        <div className="mx-4 mt-16 text-center text-gray-500">
          <p className="text-4xl mb-3">{"\uD83D\uDCBF"}</p>
          <p className="text-sm">CD{"\u3092\u5165\u308C\u3066\u30EA\u30C3\u30D4\u30F3\u30B0\u3092\u958B\u59CB"}</p>
        </div>
      )}
    </div>
  );
}

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useWebSocket } from "../hooks/useWebSocket";
import TrackProgress from "../components/TrackProgress";
import type { Drive, JobSummary } from "../lib/types";

const SOURCE_TYPES = [
  { value: "library", label: "\u56F3\u66F8\u9928" },
  { value: "owned", label: "\u624B\u6301\u3061" },
  { value: "unknown", label: "\u672A\u5206\u985E" },
] as const;

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
  const queryClient = useQueryClient();
  const navigate = useNavigate();

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

  // Rip start dialog state
  const [ripDialog, setRipDialog] = useState<{ driveId: string; driveName: string } | null>(null);
  const [ripSourceType, setRipSourceType] = useState("unknown");
  const [ripHintArtist, setRipHintArtist] = useState("");
  const [ripHintAlbum, setRipHintAlbum] = useState("");
  const [ripHintCatalog, setRipHintCatalog] = useState("");
  const [ripDiscNumber, setRipDiscNumber] = useState<string>("");
  const [ripTotalDiscs, setRipTotalDiscs] = useState<string>("");
  const [ripError, setRipError] = useState<string | null>(null);

  const ripMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.startRip(body) as Promise<{ job_id: string }>,
    onSuccess: (data) => {
      setRipDialog(null);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["drives"] });
      navigate(`/job/${data.job_id}`);
    },
    onError: (err: Error) => {
      setRipError(err.message);
    },
  });

  const autoRipMutation = useMutation({
    mutationFn: ({ driveId, body }: { driveId: string; body: Record<string, unknown> }) =>
      api.updateDrive(driveId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["drives"] });
    },
  });

  const handleStartRip = () => {
    if (!ripDialog) return;
    setRipError(null);
    const hints: Record<string, string> = {};
    if (ripHintArtist) hints.artist = ripHintArtist;
    if (ripHintAlbum) hints.album = ripHintAlbum;
    if (ripHintCatalog) hints.catalog = ripHintCatalog;
    ripMutation.mutate({
      drive_id: ripDialog.driveId,
      source_type: ripSourceType,
      hints: Object.keys(hints).length > 0 ? hints : undefined,
      disc_number: ripDiscNumber ? parseInt(ripDiscNumber) : undefined,
      total_discs: ripTotalDiscs ? parseInt(ripTotalDiscs) : undefined,
    });
  };

  const openRipDialog = (drive: Drive) => {
    setRipDialog({ driveId: drive.drive_id, driveName: drive.name });
    setRipSourceType("unknown");
    setRipHintArtist("");
    setRipHintAlbum("");
    setRipHintCatalog("");
    setRipDiscNumber("");
    setRipTotalDiscs("");
    setRipError(null);
  };

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
                <span className="mt-0.5">
                  {job.status === "error" ? (
                    <svg className="w-5 h-5 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                  ) : (
                    <svg className="w-5 h-5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  )}
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
                  <div className={`w-10 h-10 rounded-lg bg-gradient-to-br ${colors.gradient} flex items-center justify-center`}>
                    {job.status === "ripping" ? (
                      <svg className="w-5 h-5 text-emerald-300" fill="currentColor" viewBox="0 0 24 24">
                        <path d="M8 5v14l11-7z" />
                      </svg>
                    ) : (
                      <svg className="w-5 h-5 text-blue-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                      </svg>
                    )}
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
              const hasActiveJob = !!drive.active_job_id;
              return (
                <div key={drive.drive_id} className={`px-4 py-3 ${!isOnline ? "opacity-50" : ""}`}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      <div className={`w-2 h-2 rounded-full shrink-0 ${isOnline ? "bg-emerald-400" : "bg-gray-600"}`} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{drive.name}</span>
                          {drive.has_disc && (
                            <span title="CD inserted">
                              <svg className="w-4 h-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <circle cx="12" cy="12" r="10" strokeWidth="1.5" />
                                <circle cx="12" cy="12" r="3" strokeWidth="1.5" />
                              </svg>
                            </span>
                          )}
                          {isOnline && !drive.has_disc && (
                              <span className="text-xs text-gray-600">{"\u7A7A"}</span>
                          )}
                          {drive.auto_rip && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[#e94560]/15 text-[#e94560] font-medium">
                              Auto
                            </span>
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
                    <div className="flex items-center gap-1.5 shrink-0">
                      {/* Auto-rip toggle */}
                      {isOnline && (
                        <button
                          onClick={(e) => {
                            e.preventDefault();
                            if (drive.auto_rip) {
                              autoRipMutation.mutate({ driveId: drive.drive_id, body: { auto_rip: false } });
                            } else {
                              autoRipMutation.mutate({
                                driveId: drive.drive_id,
                                body: { auto_rip: true, auto_rip_source_type: "unknown" },
                              });
                            }
                          }}
                          className={`w-8 h-8 flex items-center justify-center rounded-lg transition text-sm ${
                            drive.auto_rip
                              ? "bg-[#e94560]/20 text-[#e94560] hover:bg-[#e94560]/30"
                              : "hover:bg-white/10 text-gray-500 hover:text-gray-300"
                          }`}
                          title={drive.auto_rip ? "Auto-rip ON" : "Auto-rip OFF"}
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                          </svg>
                        </button>
                      )}
                      {/* Rip / Ripping button */}
                      {isOnline && hasActiveJob ? (
                        <Link
                          to={`/job/${drive.active_job_id}`}
                          className="px-2.5 py-1.5 rounded-lg bg-emerald-500/15 text-emerald-400 text-xs font-medium hover:bg-emerald-500/25 transition"
                        >
                          Ripping...
                        </Link>
                      ) : isOnline && drive.has_disc ? (
                        <button
                          onClick={(e) => {
                            e.preventDefault();
                            openRipDialog(drive);
                          }}
                          className="px-2.5 py-1.5 rounded-lg bg-[#e94560]/15 text-[#e94560] text-xs font-semibold hover:bg-[#e94560]/25 transition"
                        >
                          Rip
                        </button>
                      ) : null}
                      {/* Eject */}
                      {isOnline && (
                        <button
                          onClick={(e) => {
                            e.preventDefault();
                            api.ejectDrive(drive.drive_id);
                          }}
                          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition"
                          title="Eject"
                        >
                          <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                            <path d="M12 5l-8 8h16l-8-8zM4 15h16v2H4v-2z" />
                          </svg>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Rip Start Dialog */}
      {ripDialog && (
        <div className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setRipDialog(null)}>
          <div
            className="w-full max-w-md bg-[#16213e] rounded-t-2xl sm:rounded-2xl border border-white/10 p-5 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-base font-bold">{ripDialog.driveName} - Rip</h3>
              <button onClick={() => setRipDialog(null)} className="text-gray-500 hover:text-white transition">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Source Type */}
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">{"\u30BD\u30FC\u30B9\u7A2E\u5225"}</label>
              <div className="flex gap-2">
                {SOURCE_TYPES.map((st) => (
                  <button
                    key={st.value}
                    onClick={() => setRipSourceType(st.value)}
                    className={`flex-1 py-2 rounded-lg text-sm font-medium transition ${
                      ripSourceType === st.value
                        ? "bg-[#e94560] text-white"
                        : "bg-white/5 text-gray-400 hover:bg-white/10"
                    }`}
                  >
                    {st.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Hints */}
            <div className="space-y-2">
              <label className="text-xs text-gray-400 block">{"\u30D2\u30F3\u30C8"}({"\u4EFB\u610F"})</label>
              <input
                type="text"
                value={ripHintArtist}
                onChange={(e) => setRipHintArtist(e.target.value)}
                placeholder="Artist"
                className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
              />
              <input
                type="text"
                value={ripHintAlbum}
                onChange={(e) => setRipHintAlbum(e.target.value)}
                placeholder="Album"
                className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
              />
              <input
                type="text"
                value={ripHintCatalog}
                onChange={(e) => setRipHintCatalog(e.target.value)}
                placeholder={"\u54C1\u756A (Catalog Number)"}
                className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
              />
              <div className="flex items-center gap-2">
                <label className="text-xs text-gray-500 shrink-0">Disc</label>
                <input
                  type="number"
                  min={1}
                  value={ripDiscNumber}
                  onChange={(e) => setRipDiscNumber(e.target.value)}
                  placeholder="#"
                  className="w-16 bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560] text-center"
                />
                <span className="text-gray-600">/</span>
                <input
                  type="number"
                  min={1}
                  value={ripTotalDiscs}
                  onChange={(e) => setRipTotalDiscs(e.target.value)}
                  placeholder="Total"
                  className="w-16 bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560] text-center"
                />
              </div>
            </div>

            {ripError && (
              <p className="text-xs text-red-400 bg-red-500/10 rounded-lg px-3 py-2">{ripError}</p>
            )}

            <button
              onClick={handleStartRip}
              disabled={ripMutation.isPending}
              className="w-full py-3 rounded-xl bg-gradient-to-r from-[#e94560] to-pink-600 text-sm font-bold text-white shadow-lg shadow-[#e94560]/20 hover:shadow-[#e94560]/40 active:scale-[0.98] transition-all disabled:opacity-50"
            >
              {ripMutation.isPending ? "Starting..." : "\u30EA\u30C3\u30D4\u30F3\u30B0\u958B\u59CB"}
            </button>
          </div>
        </div>
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
                <div className="w-10 h-10 rounded bg-gray-700 flex items-center justify-center shrink-0 overflow-hidden">
                  {job.artwork_url ? (
                    <img src={job.artwork_url} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <svg className="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                    </svg>
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
                <svg className="w-4 h-4 text-emerald-400 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
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
          <svg className="w-12 h-12 mx-auto mb-3 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" strokeWidth="1.5" />
            <circle cx="12" cy="12" r="3" strokeWidth="1.5" />
          </svg>
          <p className="text-sm">CD{"\u3092\u5165\u308C\u3066\u30EA\u30C3\u30D4\u30F3\u30B0\u3092\u958B\u59CB"}</p>
        </div>
      )}
    </div>
  );
}

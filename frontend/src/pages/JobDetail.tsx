import { useParams, Link, useNavigate } from "react-router-dom";
import { useState, useRef, useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useJob } from "../hooks/useJob";
import { useWebSocket } from "../hooks/useWebSocket";
import { api } from "../lib/api";
import EditableField from "../components/EditableField";
import type {
  Track,
  GroupResponse,
  JobSummary,
  ConflictsResponse,
  GnudbHistory,
} from "../lib/types";

type Tab = "metadata" | "artwork" | "lyrics" | "kashidashi";

export default function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const jobId = id!;
  const { data, isLoading, error } = useJob(jobId);
  const [tab, setTab] = useState<Tab>("metadata");
  const [candidatesOpen, setCandidatesOpen] = useState(false);
  const [selectedKashidashi, setSelectedKashidashi] = useState<number | null>(null);
  const [editingTrack, setEditingTrack] = useState<number | null>(null);
  const [editingLyricsTrack, setEditingLyricsTrack] = useState<number | null>(null);
  const [lyricsEditContent, setLyricsEditContent] = useState("");
  const artworkInputRef = useRef<HTMLInputElement>(null);
  const wavInputRef = useRef<HTMLInputElement>(null);
  const [wavTrack, setWavTrack] = useState<number | null>(null);
  const [groupSectionOpen, setGroupSectionOpen] = useState(false);
  const [groupPickerOpen, setGroupPickerOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const navigate = useNavigate();
  const queryClient = useQueryClient();

  useWebSocket();

  const invalidateJob = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["job", jobId] });
  }, [queryClient, jobId]);

  const [submitToGnudb, setSubmitToGnudb] = useState(false);
  const [gnudbCategory, setGnudbCategory] = useState<string>("");
  const [gnudbCategoryOverride, setGnudbCategoryOverride] = useState(false);

  const approveMutation = useMutation({
    mutationFn: () =>
      api.approveMetadata(jobId, {
        submitToGnudb,
        gnudbCategory: gnudbCategoryOverride && gnudbCategory ? gnudbCategory : null,
      }),
    onSuccess: () => {
      invalidateJob();
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const gnudbHistoryQuery = useQuery<GnudbHistory>({
    queryKey: ["gnudb", jobId],
    queryFn: () => api.gnudbHistory(jobId) as Promise<GnudbHistory>,
    enabled: !!jobId,
  });

  const gnudbManualSubmitMutation = useMutation({
    mutationFn: (category: string | null) =>
      api.gnudbSubmit(jobId, category),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["gnudb", jobId] });
      invalidateJob();
    },
  });

  // Check for existing file conflicts when in review with existing_files issue
  const hasExistingFilesIssue = data?.metadata?.issues?.includes("existing_files") ?? false;
  const conflictsQuery = useQuery<ConflictsResponse>({
    queryKey: ["conflicts", jobId],
    queryFn: () => api.getConflicts(jobId) as Promise<ConflictsResponse>,
    enabled: hasExistingFilesIssue,
  });

  const trashConflictsMutation = useMutation({
    mutationFn: () => api.trashConflicts(jobId),
    onSuccess: () => {
      invalidateJob();
      queryClient.invalidateQueries({ queryKey: ["conflicts", jobId] });
    },
  });

  const reResolveMutation = useMutation({
    mutationFn: () => api.reResolve(jobId),
    onSuccess: invalidateJob,
  });

  const selectCandidateMutation = useMutation({
    mutationFn: (candidateId: number) => api.selectCandidate(jobId, candidateId),
    onSuccess: invalidateJob,
  });

  const selectArtworkMutation = useMutation({
    mutationFn: (artworkId: number) => api.selectArtwork(jobId, artworkId),
    onSuccess: invalidateJob,
  });

  const uploadArtworkMutation = useMutation({
    mutationFn: (file: File) => api.uploadArtwork(jobId, file),
    onSuccess: invalidateJob,
  });

  const fetchLyricsMutation = useMutation({
    mutationFn: (trackNum: number) => api.fetchLyrics(jobId, trackNum),
    onSuccess: invalidateJob,
  });

  const fetchAllLyricsMutation = useMutation({
    mutationFn: () => api.fetchAllLyrics(jobId),
    onSuccess: invalidateJob,
  });

  const updateLyricsMutation = useMutation({
    mutationFn: ({ trackNum, content }: { trackNum: number; content: string }) =>
      api.updateLyrics(jobId, trackNum, content),
    onSuccess: () => {
      invalidateJob();
      setEditingLyricsTrack(null);
    },
  });

  const updateTrackMutation = useMutation({
    mutationFn: ({ trackNum, data: trackData }: { trackNum: number; data: Record<string, unknown> }) =>
      api.updateTrack(jobId, trackNum, trackData),
    onSuccess: () => {
      invalidateJob();
      setEditingTrack(null);
    },
  });

  const updateMetadataMutation = useMutation({
    mutationFn: (metaData: Record<string, unknown>) => api.updateMetadata(jobId, metaData),
    onSuccess: invalidateJob,
  });

  const matchKashidashiMutation = useMutation({
    mutationFn: (candidateId: number) => api.matchKashidashi(jobId, candidateId),
    onSuccess: invalidateJob,
  });

  const skipKashidashiMutation = useMutation({
    mutationFn: () => api.skipKashidashi(jobId),
    onSuccess: invalidateJob,
  });

  const reMatchKashidashiMutation = useMutation({
    mutationFn: () => api.reMatchKashidashi(jobId),
    onSuccess: () => {
      invalidateJob();
      setSelectedKashidashi(null);
    },
  });

  const reRipFailedMutation = useMutation({
    mutationFn: () => api.reRipFailed(jobId),
    onSuccess: invalidateJob,
  });

  const reRipAllMutation = useMutation({
    mutationFn: () => api.reRip(jobId),
    onSuccess: invalidateJob,
  });

  const importWavMutation = useMutation({
    mutationFn: ({ trackNum, file }: { trackNum: number; file: File }) =>
      api.importWav(jobId, trackNum, file),
    onSuccess: invalidateJob,
  });

  // Album group queries — only fetch when we have data
  const albumGroup = data?.job?.album_group ?? null;

  const groupQuery = useQuery({
    queryKey: ["group", albumGroup],
    queryFn: () => api.getGroup(albumGroup!) as Promise<GroupResponse>,
    enabled: !!albumGroup,
  });

  const recentJobsQuery = useQuery({
    queryKey: ["jobs", "for-group-picker"],
    queryFn: () => api.getJobs() as Promise<{ jobs: JobSummary[] }>,
    enabled: groupPickerOpen,
  });

  const createGroupMutation = useMutation({
    mutationFn: () => api.createGroup(jobId),
    onSuccess: () => {
      invalidateJob();
      queryClient.invalidateQueries({ queryKey: ["group"] });
    },
  });

  const addToGroupMutation = useMutation({
    mutationFn: ({ targetJobId, groupId }: { targetJobId: string; groupId: string }) =>
      api.addToGroup(targetJobId, groupId),
    onSuccess: () => {
      invalidateJob();
      queryClient.invalidateQueries({ queryKey: ["group"] });
      setGroupPickerOpen(false);
    },
  });

  const removeFromGroupMutation = useMutation({
    mutationFn: () => api.removeFromGroup(jobId),
    onSuccess: () => {
      invalidateJob();
      queryClient.invalidateQueries({ queryKey: ["group"] });
    },
  });

  const deleteJobMutation = useMutation({
    mutationFn: () => api.deleteJob(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      navigate("/");
    },
  });

  if (isLoading) return <div className="p-4 text-gray-400">{"\u8AAD\u307F\u8FBC\u307F\u4E2D"}...</div>;
  if (error) return <div className="p-4 text-red-400">{"\u30A8\u30E9\u30FC"}: {(error as Error).message}</div>;
  if (!data) return null;

  const { job, metadata, tracks, candidates, artworks, kashidashi_candidates } = data;

  const failedTracks = tracks.filter((t) => t.rip_status === "failed");
  const degradedTracks = tracks.filter((t) => t.rip_status === "ok_degraded");

  const tabs: { key: Tab; label: string }[] = [
    { key: "metadata", label: "Metadata" },
    { key: "artwork", label: "Artwork" },
    { key: "lyrics", label: "Lyrics" },
    { key: "kashidashi", label: "kashidashi" },
  ];

  const statusBadgeColor = () => {
    switch (job.status) {
      case "review": return "bg-amber-500/20 text-amber-400";
      case "complete": return "bg-emerald-500/20 text-emerald-400";
      case "error": return "bg-red-500/20 text-red-400";
      case "ripping": return "bg-emerald-500/20 text-emerald-400";
      case "encoding": return "bg-blue-500/20 text-blue-400";
      default: return "bg-gray-500/20 text-gray-400";
    }
  };

  return (
    <div className="pb-8">
      {/* Header */}
      <header className="sticky top-0 z-50 backdrop-blur-xl bg-[#0f0f1a]/80 border-b border-white/5" style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}>
        <div className="flex items-center gap-3 px-4 py-3">
          <Link
            to="/"
            className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-white/10 transition"
          >
            <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
            </svg>
          </Link>
          <div className="flex-1 min-w-0">
            <h1 className="text-sm font-semibold truncate">Job Detail</h1>
            <p className="text-[11px] text-gray-500">
              {metadata?.artist || "Unknown"} · {job.status}
            </p>
          </div>
          <span className={`text-xs font-medium px-2 py-1 rounded-full ${statusBadgeColor()}`}>
            {job.status.charAt(0).toUpperCase() + job.status.slice(1)}
          </span>
          {(job.status === "error" || job.status === "complete") && (
            <button
              onClick={() => setDeleteDialogOpen(true)}
              className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-white/10 transition text-gray-400 hover:text-red-400"
              title={"ジョブを削除"}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          )}
        </div>
      </header>

      {/* Tabs */}
      <div className="sticky z-40 backdrop-blur-xl bg-[#0f0f1a]/80 border-b border-white/5" style={{ top: "calc(57px + env(safe-area-inset-top, 0px))" }}>
        <div className="flex">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex-1 text-xs font-medium py-2.5 border-b-2 transition ${
                tab === t.key
                  ? "text-[#e94560] border-[#e94560]"
                  : "text-gray-500 border-transparent"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* ==================== METADATA TAB ==================== */}
      {tab === "metadata" && (
        <div>
          {/* Album Header */}
          <div className="px-4 pt-4 pb-3">
            <div className="flex gap-4">
              <div className="w-24 h-24 rounded-lg bg-gray-800 border border-white/10 flex items-center justify-center text-4xl shrink-0 overflow-hidden">
                {artworks.find((a) => a.selected)?.url ? (
                  <img
                    src={artworks.find((a) => a.selected)!.url!}
                    alt=""
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full bg-gradient-to-br from-purple-900/60 to-pink-900/60 flex items-center justify-center">
                    <svg className="w-10 h-10 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                    </svg>
                  </div>
                )}
              </div>
              <div className="flex-1 min-w-0 py-1">
                <EditableField
                  value={metadata?.artist || ""}
                  placeholder="Artist"
                  className="text-base font-bold truncate block"
                  inputClassName="text-base font-bold w-full"
                  onSave={(v) => updateMetadataMutation.mutate({ artist: v })}
                />
                <span className="flex items-center gap-1.5 mt-0.5">
                  <EditableField
                    value={metadata?.album || ""}
                    placeholder="Album"
                    className="text-sm text-gray-300 truncate block"
                    inputClassName="text-sm w-full"
                    onSave={(v) => updateMetadataMutation.mutate({ album: v })}
                  />
                  {metadata && metadata.total_discs > 1 && (
                    <span className="shrink-0 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-white/10 text-gray-400 border border-white/10">Disc {metadata.disc_number}/{metadata.total_discs}</span>
                  )}
                </span>
                <div className="flex items-center gap-2 mt-2">
                  <EditableField
                    value={metadata?.year?.toString() || ""}
                    placeholder="Year"
                    className="text-xs text-gray-500"
                    inputClassName="text-xs w-16"
                    onSave={(v) => updateMetadataMutation.mutate({ year: parseInt(v) || null })}
                  />
                  <span className="text-gray-700">·</span>
                  <EditableField
                    value={metadata?.genre || ""}
                    placeholder="Genre"
                    className="text-xs text-gray-500"
                    inputClassName="text-xs w-24"
                    onSave={(v) => updateMetadataMutation.mutate({ genre: v })}
                  />
                  <span className="text-gray-700">·</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    Disc{" "}
                    <EditableField
                      value={String(metadata?.disc_number || 1)}
                      placeholder="#"
                      className="text-xs text-gray-500"
                      inputClassName="text-xs w-8 text-center"
                      onSave={(v) => updateMetadataMutation.mutate({ disc_number: parseInt(v) || 1 })}
                    />
                    /
                    <EditableField
                      value={String(metadata?.total_discs || 1)}
                      placeholder="#"
                      className="text-xs text-gray-500"
                      inputClassName="text-xs w-8 text-center"
                      onSave={(v) => updateMetadataMutation.mutate({ total_discs: parseInt(v) || 1 })}
                    />
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Album Group */}
          <div className="mx-4 mb-3">
            <button
              onClick={() => setGroupSectionOpen((v) => !v)}
              className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg bg-[#16213e] border border-white/5 hover:border-white/10 transition"
            >
              <span className="text-xs font-semibold text-gray-400">
                Album Group
                {job.album_group && (
                  <span className="ml-2 font-mono text-[10px] text-gray-500">
                    {job.album_group.slice(0, 8)}
                  </span>
                )}
              </span>
              <svg
                className={`w-4 h-4 text-gray-500 transition-transform ${groupSectionOpen ? "rotate-180" : ""}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {groupSectionOpen && (
              <div className="mt-1 rounded-lg bg-[#16213e] border border-white/5 p-3">
                {job.album_group ? (
                  <>
                    {/* Group members */}
                    {groupQuery.data && (
                      <div className="space-y-1.5 mb-3">
                        {groupQuery.data.jobs.map((gj) => (
                          <Link
                            key={gj.job_id}
                            to={`/job/${gj.job_id}`}
                            className={`flex items-center justify-between px-2.5 py-2 rounded-lg transition text-xs ${
                              gj.job_id === jobId
                                ? "bg-[#e94560]/10 border border-[#e94560]/20"
                                : "bg-white/5 hover:bg-white/10"
                            }`}
                          >
                            <div className="min-w-0">
                              <span className="text-gray-300 font-medium">
                                Disc {gj.disc_number ?? "?"}
                              </span>
                              <span className="text-gray-500 ml-1.5">
                                {gj.artist} / {gj.album}
                              </span>
                            </div>
                            <span
                              className={`text-[10px] px-1.5 py-0.5 rounded-full shrink-0 ml-2 ${
                                gj.status === "complete"
                                  ? "bg-emerald-500/20 text-emerald-400"
                                  : gj.status === "error"
                                    ? "bg-red-500/20 text-red-400"
                                    : "bg-gray-500/20 text-gray-400"
                              }`}
                            >
                              {gj.status}
                            </span>
                          </Link>
                        ))}
                      </div>
                    )}
                    <div className="flex gap-2">
                      <button
                        onClick={() => setGroupPickerOpen(true)}
                        className="flex-1 text-[11px] font-medium py-1.5 rounded-lg bg-white/5 text-gray-400 hover:bg-white/10 transition"
                      >
                        {"\u30C7\u30A3\u30B9\u30AF\u8FFD\u52A0"}
                      </button>
                      <button
                        onClick={() => removeFromGroupMutation.mutate()}
                        disabled={removeFromGroupMutation.isPending}
                        className="flex-1 text-[11px] font-medium py-1.5 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20 transition disabled:opacity-50"
                      >
                        {removeFromGroupMutation.isPending ? "..." : "\u30B0\u30EB\u30FC\u30D7\u89E3\u9664"}
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="flex gap-2">
                    <button
                      onClick={() => createGroupMutation.mutate()}
                      disabled={createGroupMutation.isPending}
                      className="flex-1 text-[11px] font-medium py-1.5 rounded-lg bg-white/5 text-gray-400 hover:bg-white/10 transition disabled:opacity-50"
                    >
                      {createGroupMutation.isPending ? "..." : "\u30B0\u30EB\u30FC\u30D7\u4F5C\u6210"}
                    </button>
                    <button
                      onClick={() => setGroupPickerOpen(true)}
                      className="flex-1 text-[11px] font-medium py-1.5 rounded-lg bg-white/5 text-gray-400 hover:bg-white/10 transition"
                    >
                      {"\u65E2\u5B58\u30B0\u30EB\u30FC\u30D7\u306B\u8FFD\u52A0"}
                    </button>
                  </div>
                )}

                {/* Group picker modal */}
                {groupPickerOpen && (
                  <div className="mt-3 border-t border-white/5 pt-3">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[11px] text-gray-400 font-medium">
                        {job.album_group ? "\u30B0\u30EB\u30FC\u30D7\u306B\u8FFD\u52A0\u3059\u308B\u30B8\u30E7\u30D6\u3092\u9078\u629E" : "\u30B0\u30EB\u30FC\u30D7\u306E\u3042\u308B\u30B8\u30E7\u30D6\u3092\u9078\u629E"}
                      </span>
                      <button
                        onClick={() => setGroupPickerOpen(false)}
                        className="text-[10px] text-gray-500 hover:text-gray-300"
                      >
                        {"\u9589\u3058\u308B"}
                      </button>
                    </div>
                    <div className="max-h-48 overflow-y-auto space-y-1">
                      {recentJobsQuery.data?.jobs
                        .filter((rj) => rj.job_id !== jobId)
                        .filter(() => true)
                        .map((rj) => (
                          <button
                            key={rj.job_id}
                            onClick={() => {
                              if (job.album_group) {
                                // Add picked job to this job's group
                                addToGroupMutation.mutate({
                                  targetJobId: rj.job_id,
                                  groupId: job.album_group!,
                                });
                              } else {
                                // First, create group for this job, then we need the group_id
                                // Simpler: use createGroup then addToGroup
                                (api.createGroup(jobId) as Promise<{ album_group: string }>).then(
                                  (res) => {
                                    addToGroupMutation.mutate({
                                      targetJobId: rj.job_id,
                                      groupId: res.album_group,
                                    });
                                  }
                                );
                              }
                            }}
                            disabled={addToGroupMutation.isPending}
                            className="w-full text-left px-2.5 py-2 rounded-lg bg-white/5 hover:bg-white/10 transition text-xs disabled:opacity-50"
                          >
                            <span className="text-gray-300">{rj.artist || "?"}</span>
                            <span className="text-gray-500"> / {rj.album || "?"}</span>
                            <span className="text-gray-600 ml-1.5 text-[10px]">[{rj.status}]</span>
                          </button>
                        ))}
                      {recentJobsQuery.isLoading && (
                        <p className="text-[11px] text-gray-500 text-center py-2">{"\u8AAD\u307F\u8FBC\u307F\u4E2D"}...</p>
                      )}
                      {recentJobsQuery.data?.jobs.filter((rj) => rj.job_id !== jobId).length === 0 && (
                        <p className="text-[11px] text-gray-500 text-center py-2">{"\u4ED6\u306E\u30B8\u30E7\u30D6\u304C\u3042\u308A\u307E\u305B\u3093"}</p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Source Info */}
          {metadata && (
            <div className="mx-4 mb-3 rounded-lg bg-[#16213e] border border-white/5 px-3 py-2.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-500">Source</span>
                <span className="text-gray-300">{metadata.source || "Unknown"}</span>
              </div>
              <div className="flex items-center justify-between text-xs mt-1.5">
                <span className="text-gray-500">Confidence</span>
                <div className="flex items-center gap-2">
                  <div className="w-20 h-1.5 rounded-full bg-gray-700">
                    <div
                      className={`h-full rounded-full ${
                        (metadata.confidence ?? 0) >= 80 ? "bg-emerald-500" : "bg-amber-500"
                      }`}
                      style={{ width: `${metadata.confidence ?? 0}%` }}
                    />
                  </div>
                  <span className={`font-mono ${(metadata.confidence ?? 0) >= 80 ? "text-emerald-400" : "text-amber-400"}`}>
                    {metadata.confidence ?? "?"}
                  </span>
                </div>
              </div>
              {metadata.issues && (
                <div className="flex items-center justify-between text-xs mt-1.5">
                  <span className="text-gray-500">Issues</span>
                  <span className="text-amber-400">{metadata.issues}</span>
                </div>
              )}
            </div>
          )}

          {/* Track List */}
          <div className="mx-4 mb-3">
            <div className="rounded-lg bg-[#16213e] border border-white/5 overflow-hidden">
              <div className="px-3 py-2 border-b border-white/5 flex items-center justify-between">
                <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Tracks</span>
                <span className="text-[11px] text-gray-500">{tracks.length} tracks</span>
              </div>
              <div className="grid grid-cols-[2rem_1fr_2rem_2rem] items-center px-3 py-1.5 text-[10px] text-gray-600 uppercase tracking-wider border-b border-white/5">
                <span>#</span>
                <span>Title</span>
                <span className="text-center">Rip</span>
                <span className="text-center">Enc</span>
              </div>
              <div className="divide-y divide-white/5">
                {tracks.map((t) => (
                  <div
                    key={t.track_num}
                    className={`grid grid-cols-[2rem_1fr_2rem_2rem] items-center px-3 py-2 text-xs ${
                      t.rip_status === "failed"
                        ? "bg-red-950/20"
                        : t.rip_status === "ok_degraded"
                          ? "bg-amber-950/10"
                          : ""
                    }`}
                  >
                    <span className="text-gray-500">{t.track_num}</span>
                    <span className="truncate pr-2">
                      {editingTrack === t.track_num ? (
                        <TrackTitleEditor
                          track={t}
                          onSave={(title) => {
                            updateTrackMutation.mutate({ trackNum: t.track_num, data: { title } });
                          }}
                          onCancel={() => setEditingTrack(null)}
                        />
                      ) : (
                        <span
                          onClick={() => setEditingTrack(t.track_num)}
                          className={`cursor-pointer hover:text-[#e94560] transition ${
                            t.rip_status === "failed"
                              ? "text-red-300"
                              : t.rip_status === "ok_degraded"
                                ? "text-amber-200"
                                : "text-gray-200"
                          }`}
                        >
                          {t.title || `Track ${t.track_num}`}
                        </span>
                      )}
                    </span>
                    <span className="text-center"><StatusIcon status={t.rip_status} /></span>
                    <span className="text-center"><StatusIcon status={t.encode_status} /></span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Failed tracks action */}
          {(failedTracks.length > 0 || degradedTracks.length > 0) && (
            <div className="mx-4 mb-3 rounded-lg bg-red-950/20 border border-red-500/20 px-3 py-3">
              <p className="text-xs text-red-300 mb-2">
                {failedTracks.length > 0 && `\u5931\u6557\u30C8\u30E9\u30C3\u30AF: ${failedTracks.length}\u4EF6`}
                {failedTracks.length > 0 && degradedTracks.length > 0 && " / "}
                {degradedTracks.length > 0 && `\u54C1\u8CEA\u4F4E\u4E0B: ${degradedTracks.length}\u4EF6`}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => reRipFailedMutation.mutate()}
                  disabled={reRipFailedMutation.isPending}
                  className="flex-1 text-xs font-medium py-2 rounded-lg bg-red-500/20 text-red-300 hover:bg-red-500/30 transition disabled:opacity-50"
                >
                  {reRipFailedMutation.isPending ? "..." : "\u5931\u6557\u30C8\u30E9\u30C3\u30AF\u3092\u518D\u30EA\u30C3\u30D7"}
                </button>
                <button
                  onClick={() => reRipAllMutation.mutate()}
                  disabled={reRipAllMutation.isPending}
                  className="flex-1 text-xs font-medium py-2 rounded-lg bg-white/5 text-gray-400 hover:bg-white/10 transition disabled:opacity-50"
                >
                  {reRipAllMutation.isPending ? "..." : "\u5168\u30C8\u30E9\u30C3\u30AF\u518D\u30EA\u30C3\u30D7"}
                </button>
              </div>
              {/* WAV upload for failed tracks */}
              <div className="mt-2">
                <input
                  ref={wavInputRef}
                  type="file"
                  accept=".wav"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file && wavTrack != null) {
                      importWavMutation.mutate({ trackNum: wavTrack, file });
                    }
                    e.target.value = "";
                  }}
                />
                {failedTracks.map((t) => (
                  <button
                    key={t.track_num}
                    onClick={() => {
                      setWavTrack(t.track_num);
                      wavInputRef.current?.click();
                    }}
                    className="block text-[11px] text-gray-400 hover:text-white mt-1 transition"
                  >
                    Track {t.track_num} WAV{"\u30A2\u30C3\u30D7\u30ED\u30FC\u30C9"} →
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Re-resolve button */}
          <div className="mx-4 mb-3">
            <button
              onClick={() => reResolveMutation.mutate()}
              disabled={reResolveMutation.isPending}
              className="w-full text-xs font-medium py-2 rounded-lg bg-white/5 text-gray-400 hover:bg-white/10 transition disabled:opacity-50"
            >
              {reResolveMutation.isPending ? "\u518D\u691C\u7D22\u4E2D..." : "\u30E1\u30BF\u30C7\u30FC\u30BF\u3092\u518D\u691C\u7D22"}
            </button>
          </div>

          {/* Candidates (Collapsible) */}
          {candidates.length > 0 && (
            <div className="mx-4 mb-4">
              <button
                onClick={() => setCandidatesOpen((v) => !v)}
                className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg bg-[#16213e] border border-white/5 hover:border-white/10 transition"
              >
                <span className="text-xs font-semibold text-gray-400">
                  {"\u4ED6\u306E\u5019\u88DC"} ({candidates.length}{"\u4EF6"})
                </span>
                <svg
                  className={`w-4 h-4 text-gray-500 transition-transform ${candidatesOpen ? "rotate-180" : ""}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {candidatesOpen && (
                <div className="mt-1 rounded-lg bg-[#16213e] border border-white/5 divide-y divide-white/5 overflow-hidden">
                  {candidates.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => selectCandidateMutation.mutate(c.id)}
                      disabled={selectCandidateMutation.isPending || c.selected}
                      className="w-full text-left px-3 py-2.5 hover:bg-white/5 transition disabled:opacity-50"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-medium text-gray-300">{c.source}</span>
                        <span
                          className={`text-xs font-mono ${
                            (c.confidence ?? 0) >= 80
                              ? "text-emerald-400"
                              : (c.confidence ?? 0) >= 60
                                ? "text-amber-400"
                                : "text-gray-500"
                          }`}
                        >
                          {c.confidence ?? "?"}
                        </span>
                      </div>
                      <p className="text-[11px] text-gray-500 mt-0.5">
                        {c.artist} / {c.album} · {c.year}
                        {c.selected && " (selected)"}
                      </p>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Existing Files Conflict Warning */}
          {hasExistingFilesIssue && conflictsQuery.data && conflictsQuery.data.files.length > 0 && (
            <div className="mx-4 mb-4 rounded-xl bg-amber-500/10 border border-amber-500/30 p-4">
              <h4 className="text-sm font-bold text-amber-400 mb-2">
                出力先に既存ファイルがあります
              </h4>
              <p className="text-xs text-gray-400 mb-2">
                {conflictsQuery.data.output_dir}
              </p>
              <ul className="text-xs text-gray-300 space-y-1 mb-3 max-h-32 overflow-y-auto">
                {conflictsQuery.data.files.map((f) => (
                  <li key={f.name} className="flex justify-between">
                    <span className="truncate">{f.name}</span>
                    <span className="text-gray-500 ml-2 shrink-0">
                      {(f.size / 1024 / 1024).toFixed(1)} MB
                    </span>
                  </li>
                ))}
              </ul>
              <button
                onClick={() => trashConflictsMutation.mutate()}
                disabled={trashConflictsMutation.isPending}
                className="w-full py-2 rounded-lg bg-amber-500/20 text-amber-400 text-xs font-bold hover:bg-amber-500/30 active:scale-[0.98] transition-all disabled:opacity-50"
              >
                {trashConflictsMutation.isPending
                  ? "移動中..."
                  : `${conflictsQuery.data.files.length} ファイルをゴミ箱に移動`}
              </button>
              {trashConflictsMutation.isError && (
                <p className="text-xs text-red-400 mt-2">
                  {(trashConflictsMutation.error as Error).message}
                </p>
              )}
            </div>
          )}

          {/* Approve Button */}
          {job.status === "review" && (
            <div className="mx-4 mb-6">
              {job.gnudb_submittable && (
                <GnudbApproveBlock
                  submit={submitToGnudb}
                  setSubmit={setSubmitToGnudb}
                  category={gnudbCategory}
                  setCategory={setGnudbCategory}
                  override={gnudbCategoryOverride}
                  setOverride={setGnudbCategoryOverride}
                />
              )}
              <button
                onClick={() => approveMutation.mutate()}
                disabled={approveMutation.isPending || hasExistingFilesIssue}
                className="w-full py-3 rounded-xl bg-gradient-to-r from-[#e94560] to-pink-600 text-sm font-bold text-white shadow-lg shadow-[#e94560]/20 hover:shadow-[#e94560]/40 active:scale-[0.98] transition-all disabled:opacity-50"
              >
                {approveMutation.isPending ? "\u627F\u8A8D\u4E2D..." : hasExistingFilesIssue ? "既存ファイルを除去してください" : "\u627F\u8A8D\u3057\u3066\u5B8C\u4E86"}
              </button>
              {approveMutation.isError && (
                <p className="text-xs text-red-400 mt-2 text-center">
                  {(approveMutation.error as Error).message}
                </p>
              )}
            </div>
          )}

          {/* GnuDB: history + manual submit (also for complete jobs) */}
          {((gnudbHistoryQuery.data?.submissions?.length ?? 0) > 0 ||
            job.gnudb_submittable) && (
            <GnudbSection
              canSubmit={!!job.gnudb_submittable}
              alreadyAccepted={!!job.gnudb_already_accepted}
              history={gnudbHistoryQuery.data}
              onSubmit={(category) =>
                gnudbManualSubmitMutation.mutate(category)
              }
              submitting={gnudbManualSubmitMutation.isPending}
              error={gnudbManualSubmitMutation.error as Error | null}
            />
          )}
        </div>
      )}

      {/* ==================== ARTWORK TAB ==================== */}
      {tab === "artwork" && (
        <div className="px-4 pt-4">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Artwork Candidates</h3>

          {/* Selected artwork */}
          {artworks.filter((a) => a.selected).map((a) => (
            <div key={a.id} className="mb-4">
              <div className="rounded-xl border-2 border-[#e94560]/50 bg-[#16213e] overflow-hidden">
                <div className="aspect-square bg-gradient-to-br from-purple-900/40 to-pink-900/40 flex items-center justify-center">
                  {a.url ? (
                    <img src={a.url} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <svg className="w-16 h-16 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                    </svg>
                  )}
                </div>
                <div className="px-3 py-2 flex items-center justify-between">
                  <div>
                    <p className="text-xs font-medium text-gray-300">{a.source}</p>
                    <p className="text-[11px] text-gray-500">
                      {a.width && a.height && `${a.width} x ${a.height}`}
                      {a.file_size && ` · ${(a.file_size / 1024).toFixed(0)} KB`}
                    </p>
                  </div>
                  <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-[#e94560]/20 text-[#e94560]">Selected</span>
                </div>
              </div>
            </div>
          ))}

          {/* Other candidates grid */}
          {artworks.filter((a) => !a.selected).length > 0 && (
            <div className="grid grid-cols-2 gap-3 mb-4">
              {artworks.filter((a) => !a.selected).map((a) => (
                <button
                  key={a.id}
                  onClick={() => selectArtworkMutation.mutate(a.id)}
                  disabled={selectArtworkMutation.isPending}
                  className="rounded-xl bg-[#16213e] border border-white/5 overflow-hidden hover:border-white/15 transition cursor-pointer text-left disabled:opacity-50"
                >
                  <div className="aspect-square bg-gradient-to-br from-blue-900/30 to-indigo-900/30 flex items-center justify-center">
                    {a.url ? (
                      <img src={a.url} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <svg className="w-10 h-10 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                      </svg>
                    )}
                  </div>
                  <div className="px-2.5 py-2">
                    <p className="text-[11px] font-medium text-gray-400">{a.source}</p>
                    <p className="text-[10px] text-gray-600">
                      {a.width && a.height && `${a.width} x ${a.height}`}
                      {a.file_size && ` · ${(a.file_size / 1024).toFixed(0)} KB`}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          )}

          {artworks.length === 0 && (
            <p className="text-gray-500 text-center mb-4">{"\u30A2\u30FC\u30C8\u30EF\u30FC\u30AF\u306A\u3057"}</p>
          )}

          {/* Upload button */}
          <input
            ref={artworkInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) uploadArtworkMutation.mutate(file);
              e.target.value = "";
            }}
          />
          <button
            onClick={() => artworkInputRef.current?.click()}
            disabled={uploadArtworkMutation.isPending}
            className="w-full py-3 rounded-xl border-2 border-dashed border-gray-700 hover:border-gray-500 text-gray-500 hover:text-gray-300 text-xs font-medium transition flex items-center justify-center gap-2 mb-6 disabled:opacity-50"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            {uploadArtworkMutation.isPending ? "\u30A2\u30C3\u30D7\u30ED\u30FC\u30C9\u4E2D..." : "\u753B\u50CF\u3092\u30A2\u30C3\u30D7\u30ED\u30FC\u30C9"}
          </button>
        </div>
      )}

      {/* ==================== LYRICS TAB ==================== */}
      {tab === "lyrics" && (
        <div className="px-4 pt-4 space-y-3">
          {tracks.map((t) => {
            const hasLyrics = !!t.lyrics_source || !!t.lyrics_content;
            const isEditing = editingLyricsTrack === t.track_num;

            return (
              <div key={t.track_num} className="rounded-xl bg-[#16213e] border border-white/5 overflow-hidden">
                <div className={`px-3 py-2.5 ${hasLyrics ? "border-b border-white/5" : ""} flex items-center justify-between`}>
                  <div>
                    <p className="text-xs font-medium text-gray-200">
                      {t.track_num}. {t.title || `Track ${t.track_num}`}
                    </p>
                    <p className={`text-[10px] mt-0.5 ${hasLyrics ? "text-emerald-400" : "text-gray-500"}`}>
                      {t.lyrics_source || "No lyrics"}
                    </p>
                  </div>
                  {hasLyrics ? (
                    <button
                      onClick={() => {
                        if (isEditing) {
                          setEditingLyricsTrack(null);
                        } else {
                          setEditingLyricsTrack(t.track_num);
                          setLyricsEditContent(t.lyrics_content || "");
                        }
                      }}
                      className="text-[11px] text-[#e94560] hover:underline"
                    >
                      {isEditing ? "Cancel" : "Edit"}
                    </button>
                  ) : (
                    <button
                      onClick={() => fetchLyricsMutation.mutate(t.track_num)}
                      disabled={fetchLyricsMutation.isPending}
                      className="text-[11px] font-medium px-2.5 py-1 rounded-lg bg-white/5 text-gray-400 hover:bg-white/10 hover:text-white transition disabled:opacity-50"
                    >
                      {fetchLyricsMutation.isPending ? "..." : "Fetch"}
                    </button>
                  )}
                </div>
                {hasLyrics && !isEditing && t.lyrics_content && (
                  <div className="px-3 py-2 max-h-32 overflow-y-auto">
                    <pre className="text-[11px] text-gray-400 font-mono leading-relaxed whitespace-pre-wrap">
                      {t.lyrics_content}
                    </pre>
                  </div>
                )}
                {isEditing && (
                  <div className="px-3 py-2">
                    <textarea
                      value={lyricsEditContent}
                      onChange={(e) => setLyricsEditContent(e.target.value)}
                      className="w-full h-40 bg-[#0f0f1a] border border-white/10 rounded-lg text-[11px] text-gray-300 font-mono p-2 outline-none focus:border-[#e94560] resize-none"
                    />
                    <div className="flex gap-2 mt-2">
                      <button
                        onClick={() => updateLyricsMutation.mutate({ trackNum: t.track_num, content: lyricsEditContent })}
                        disabled={updateLyricsMutation.isPending}
                        className="text-xs font-medium px-3 py-1.5 rounded-lg bg-[#e94560]/20 text-[#e94560] hover:bg-[#e94560]/30 transition disabled:opacity-50"
                      >
                        Save
                      </button>
                      <button
                        onClick={() => setEditingLyricsTrack(null)}
                        className="text-xs font-medium px-3 py-1.5 rounded-lg bg-white/5 text-gray-400 hover:bg-white/10 transition"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {/* Fetch all button */}
          <button
            onClick={() => fetchAllLyricsMutation.mutate()}
            disabled={fetchAllLyricsMutation.isPending}
            className="w-full py-2.5 rounded-xl bg-white/5 hover:bg-white/10 text-xs font-medium text-gray-400 hover:text-white transition mb-6 disabled:opacity-50"
          >
            {fetchAllLyricsMutation.isPending ? "\u53D6\u5F97\u4E2D..." : "\u672A\u53D6\u5F97\u306E\u6B4C\u8A5E\u3092\u3059\u3079\u3066\u53D6\u5F97"}
          </button>
        </div>
      )}

      {/* ==================== KASHIDASHI TAB ==================== */}
      {tab === "kashidashi" && (
        <div className="px-4 pt-4">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Match Candidates</h3>

          <div className="space-y-2 mb-4">
            {kashidashi_candidates.map((k) => {
              const isSelected = selectedKashidashi === k.id || (selectedKashidashi === null && k.matched);
              return (
                <label
                  key={k.id}
                  className={`block rounded-xl bg-[#16213e] overflow-hidden cursor-pointer transition ${
                    isSelected ? "border-2 border-[#e94560]/40 hover:border-[#e94560]/60" : "border border-white/5 hover:border-white/15"
                  }`}
                >
                  <div className="px-3 py-3 flex items-start gap-3">
                    <input
                      type="radio"
                      name="kashidashi"
                      checked={isSelected}
                      onChange={() => setSelectedKashidashi(k.id)}
                      className="mt-1 accent-[#e94560]"
                    />
                    <div className="flex-1 min-w-0">
                      <span className="text-xs font-medium text-gray-200">
                        {k.artist} / {k.title}
                      </span>
                      <div className="flex items-center gap-2 mt-1.5">
                        <div className="flex items-center gap-1">
                          <div className="w-12 h-1 rounded-full bg-gray-700">
                            <div
                              className={`h-full rounded-full ${(k.score ?? 0) >= 80 ? "bg-emerald-500" : "bg-amber-500"}`}
                              style={{ width: `${k.score ?? 0}%` }}
                            />
                          </div>
                          <span className={`text-[10px] font-mono ${(k.score ?? 0) >= 80 ? "text-emerald-400" : "text-amber-400"}`}>
                            {k.score?.toFixed(0) ?? "?"}
                          </span>
                        </div>
                        {k.match_type && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                            (k.score ?? 0) >= 80
                              ? "bg-emerald-500/20 text-emerald-400"
                              : "bg-amber-500/20 text-amber-400"
                          }`}>
                            {k.match_type}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </label>
              );
            })}

            {/* No match option */}
            <label className="block rounded-xl bg-[#16213e] border border-white/5 overflow-hidden cursor-pointer hover:border-white/15 transition">
              <div className="px-3 py-3 flex items-center gap-3">
                <input
                  type="radio"
                  name="kashidashi"
                  checked={selectedKashidashi === -1}
                  onChange={() => setSelectedKashidashi(-1)}
                  className="accent-[#e94560]"
                />
                <span className="text-xs text-gray-400">{"\u30DE\u30C3\u30C1\u306A\u3057\uFF08\u624B\u6301\u3061CD\uFF09"}</span>
              </div>
            </label>

            {kashidashi_candidates.length === 0 && selectedKashidashi !== -1 && (
              <p className="text-gray-500 text-center text-xs py-2">{"\u30DE\u30C3\u30C1\u5019\u88DC\u306A\u3057"}</p>
            )}
          </div>

          <button
            onClick={() => {
              const effectiveId = selectedKashidashi ?? kashidashi_candidates.find((k) => k.matched)?.id ?? null;
              if (effectiveId === -1) {
                skipKashidashiMutation.mutate();
              } else if (effectiveId != null) {
                matchKashidashiMutation.mutate(effectiveId);
              }
            }}
            disabled={
              (selectedKashidashi === null && !kashidashi_candidates.some((k) => k.matched)) ||
              matchKashidashiMutation.isPending ||
              skipKashidashiMutation.isPending
            }
            className="w-full py-3 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 text-sm font-bold text-white shadow-lg hover:shadow-blue-600/30 active:scale-[0.98] transition-all mb-6 disabled:opacity-50"
          >
            {matchKashidashiMutation.isPending || skipKashidashiMutation.isPending ? "\u51E6\u7406\u4E2D..." : "\u78BA\u5B9A"}
          </button>

          <button
            onClick={() => reMatchKashidashiMutation.mutate()}
            disabled={reMatchKashidashiMutation.isPending}
            className="w-full py-2 rounded-xl bg-[#16213e] border border-white/5 text-xs text-gray-400 hover:text-gray-200 hover:border-white/15 transition mb-6 disabled:opacity-50"
          >
            {reMatchKashidashiMutation.isPending ? "\u691C\u7D22\u4E2D..." : "\u73FE\u5728\u306E\u30E1\u30BF\u30C7\u30FC\u30BF\u3067\u518D\u691C\u7D22"}
          </button>
        </div>
      )}
      {/* Delete Confirmation Dialog */}
      {deleteDialogOpen && (
        <div
          className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => !deleteJobMutation.isPending && setDeleteDialogOpen(false)}
        >
          <div
            className="w-full max-w-md bg-[#16213e] rounded-t-2xl sm:rounded-2xl border border-white/10 p-5 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div>
              <h3 className="text-base font-bold text-red-300">{"ジョブを削除"}</h3>
              <p className="text-xs text-gray-400 mt-2 leading-relaxed">
                {metadata?.artist && metadata?.album
                  ? `${metadata.artist} / ${metadata.album}`
                  : `Job ${job.id.slice(0, 8)}`}
                {"を削除します。"}
                <br />
                {"incoming の作業ファイルも削除されます（ライブラリに出力済みのファイルは残ります）。"}
              </p>
            </div>
            {deleteJobMutation.isError && (
              <p className="text-xs text-red-400 bg-red-500/10 rounded-lg px-3 py-2">
                {(deleteJobMutation.error as Error).message}
              </p>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => setDeleteDialogOpen(false)}
                disabled={deleteJobMutation.isPending}
                className="flex-1 py-2.5 rounded-xl bg-white/5 border border-white/10 text-sm text-gray-300 hover:bg-white/10 transition disabled:opacity-50"
              >
                {"キャンセル"}
              </button>
              <button
                onClick={() => deleteJobMutation.mutate()}
                disabled={deleteJobMutation.isPending}
                className="flex-1 py-2.5 rounded-xl bg-red-600 hover:bg-red-500 text-sm font-bold text-white transition disabled:opacity-50"
              >
                {deleteJobMutation.isPending ? "削除中..." : "削除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "ok":
      return (
        <svg className="w-3.5 h-3.5 text-emerald-400 inline-block" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
        </svg>
      );
    case "ok_degraded":
      return (
        <svg className="w-3.5 h-3.5 text-amber-400 inline-block" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
      );
    case "failed":
      return (
        <svg className="w-3.5 h-3.5 text-red-400 inline-block" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
        </svg>
      );
    case "ripping":
    case "encoding":
      return (
        <svg className="w-3.5 h-3.5 text-blue-400 animate-spin inline-block" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      );
    default:
      return <span className="w-2 h-2 rounded-full bg-gray-600 inline-block" />;
  }
}

function TrackTitleEditor({
  track,
  onSave,
  onCancel,
}: {
  track: Track;
  onSave: (title: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(track.title || "");
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <input
      ref={inputRef}
      autoFocus
      type="text"
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => {
        if (value !== (track.title || "")) onSave(value);
        else onCancel();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          onSave(value);
        }
        if (e.key === "Escape") {
          onCancel();
        }
      }}
      className="bg-[#0f0f1a] border border-white/10 rounded px-1 py-0 text-xs text-gray-200 outline-none focus:border-[#e94560] w-full"
    />
  );
}


// ────────── GnuDB: review-screen approve checkbox + override ──────────

const GNUDB_CATEGORIES = [
  "rock", "jazz", "classical", "folk", "country",
  "blues", "newage", "reggae", "soundtrack", "misc", "data",
] as const;

function GnudbApproveBlock({
  submit, setSubmit,
  category, setCategory,
  override, setOverride,
}: {
  submit: boolean;
  setSubmit: (v: boolean) => void;
  category: string;
  setCategory: (v: string) => void;
  override: boolean;
  setOverride: (v: boolean) => void;
}) {
  return (
    <div className="mb-3 rounded-xl bg-[#16213e] border border-white/5 px-3 py-2.5">
      <label className="flex items-center gap-2 cursor-pointer text-xs text-gray-300">
        <input
          type="checkbox"
          checked={submit}
          onChange={(e) => setSubmit(e.target.checked)}
          className="accent-[#e94560]"
        />
        GnuDB にも送信する
      </label>
      <p className="text-[10px] text-gray-500 mt-1.5 leading-relaxed">
        承認後、確定したメタデータを GnuDB に投稿します。次に同じ disc を入れた人が自動解決できるようになります。
      </p>
      {submit && (
        <div className="mt-2">
          <label className="flex items-center gap-2 cursor-pointer text-[11px] text-gray-400">
            <input
              type="checkbox"
              checked={override}
              onChange={(e) => setOverride(e.target.checked)}
              className="accent-[#e94560]"
            />
            カテゴリを手動指定
          </label>
          {override && (
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="mt-1.5 w-full bg-[#0f0f1a] border border-white/10 rounded text-xs text-gray-200 px-2 py-1 outline-none focus:border-[#e94560]"
            >
              <option value="">（自動判定）</option>
              {GNUDB_CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          )}
        </div>
      )}
    </div>
  );
}


// ────────── GnuDB: history + manual submit (also for complete jobs) ──────────

function GnudbSection({
  canSubmit,
  alreadyAccepted,
  history,
  onSubmit,
  submitting,
  error,
}: {
  canSubmit: boolean;
  alreadyAccepted: boolean;
  history: import("../lib/types").GnudbHistory | undefined;
  onSubmit: (category: string | null) => void;
  submitting: boolean;
  error: Error | null;
}) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<string>("");
  const submissions = history?.submissions ?? [];

  return (
    <div className="mx-4 mb-6">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg bg-[#16213e] border border-white/5 hover:border-white/10 transition"
      >
        <span className="text-xs font-semibold text-gray-400">
          GnuDB
          {alreadyAccepted && (
            <span className="ml-2 text-[10px] text-emerald-400">登録済み</span>
          )}
          {submissions.length > 0 && (
            <span className="ml-2 text-[10px] text-gray-500">
              {submissions.length} 履歴
            </span>
          )}
        </span>
        <svg
          className={`w-4 h-4 text-gray-500 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="mt-1 rounded-lg bg-[#16213e] border border-white/5 p-3 space-y-3">
          {/* Manual submit (only for complete jobs not yet accepted) */}
          {canSubmit && !alreadyAccepted && (
            <div>
              <label className="block text-[11px] text-gray-400 mb-1">
                カテゴリ（空欄なら genre から自動判定）
              </label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="w-full bg-[#0f0f1a] border border-white/10 rounded text-xs text-gray-200 px-2 py-1.5 outline-none focus:border-[#e94560] mb-2"
              >
                <option value="">（自動判定）</option>
                {GNUDB_CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <button
                onClick={() => onSubmit(category || null)}
                disabled={submitting}
                className="w-full text-xs font-medium py-2 rounded-lg bg-[#e94560]/20 text-[#e94560] hover:bg-[#e94560]/30 transition disabled:opacity-50"
              >
                {submitting ? "送信中..." : "GnuDB に送信"}
              </button>
              {error && (
                <p className="text-[11px] text-red-400 mt-2">{error.message}</p>
              )}
            </div>
          )}

          {/* History */}
          {submissions.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-[11px] text-gray-500">送信履歴</p>
              {submissions.map((s) => {
                const accepted = s.mode === "submit" && s.response_code === 200;
                const ok = s.response_code === 200;
                return (
                  <details key={s.id} className="rounded-lg bg-white/5 border border-white/5">
                    <summary className="cursor-pointer px-2.5 py-1.5 text-[11px] text-gray-300 flex items-center justify-between">
                      <span>
                        <span className="font-mono mr-1.5">{s.mode}</span>
                        <span className="text-gray-500">{s.category}</span>
                      </span>
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded ${
                          accepted
                            ? "bg-emerald-500/20 text-emerald-400"
                            : ok
                              ? "bg-amber-500/20 text-amber-400"
                              : "bg-red-500/20 text-red-400"
                        }`}
                      >
                        {s.error ? "error" : s.response_code ?? "?"}
                      </span>
                    </summary>
                    <pre className="px-2.5 pb-2 text-[10px] text-gray-400 font-mono whitespace-pre-wrap break-all">
                      {s.error || s.response_body || "(no response)"}
                    </pre>
                  </details>
                );
              })}
            </div>
          )}

          {alreadyAccepted && (
            <p className="text-[11px] text-emerald-400 leading-relaxed">
              この disc は既に GnuDB に登録済みです。GnuDB は登録済みエントリの修正に対応していないため、再送信はできません。
            </p>
          )}
        </div>
      )}
    </div>
  );
}

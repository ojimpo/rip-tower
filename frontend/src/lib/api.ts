const BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || res.statusText);
  }
  return res.json();
}

async function uploadFile<T>(path: string, file: File, fieldName = "file"): Promise<T> {
  const form = new FormData();
  form.append(fieldName, file);
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || res.statusText);
  }
  return res.json();
}

export const api = {
  // Jobs
  getJobs: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return request(`/jobs${qs}`);
  },
  getJob: (id: string) => request(`/jobs/${id}`),
  deleteJob: (id: string) => request(`/jobs/${id}`, { method: "DELETE" }),

  // Rip
  startRip: (body: Record<string, unknown>) =>
    request("/rip", { method: "POST", body: JSON.stringify(body) }),

  // Metadata
  updateMetadata: (jobId: string, body: Record<string, unknown>) =>
    request(`/jobs/${jobId}/metadata`, { method: "PUT", body: JSON.stringify(body) }),
  approveMetadata: (
    jobId: string,
    options?: { submitToGnudb?: boolean; gnudbCategory?: string | null }
  ) =>
    request(`/jobs/${jobId}/metadata/approve`, {
      method: "POST",
      body: JSON.stringify({
        submit_to_gnudb: options?.submitToGnudb ?? false,
        gnudb_category: options?.gnudbCategory ?? null,
      }),
    }),
  applyMetadata: (jobId: string) =>
    request(`/jobs/${jobId}/metadata/apply`, { method: "POST" }),
  reResolve: (jobId: string) =>
    request(`/jobs/${jobId}/metadata/re-resolve`, { method: "POST" }),

  // Candidates
  selectCandidate: (jobId: string, candidateId: number) =>
    request(`/jobs/${jobId}/candidates/${candidateId}/select`, { method: "POST" }),

  // Tracks
  updateTrack: (jobId: string, trackNum: number, data: Record<string, unknown>) =>
    request(`/jobs/${jobId}/tracks/${trackNum}`, { method: "PUT", body: JSON.stringify(data) }),

  // Artwork
  selectArtwork: (jobId: string, artworkId: number) =>
    request(`/jobs/${jobId}/artworks/${artworkId}/select`, { method: "POST" }),
  uploadArtwork: (jobId: string, file: File) =>
    uploadFile(`/jobs/${jobId}/artworks/upload`, file),

  // Lyrics
  fetchLyrics: (jobId: string, trackNum: number) =>
    request(`/jobs/${jobId}/tracks/${trackNum}/lyrics/fetch`, { method: "POST" }),
  fetchAllLyrics: (jobId: string) =>
    request(`/jobs/${jobId}/lyrics/fetch-all`, { method: "POST" }),
  updateLyrics: (jobId: string, trackNum: number, content: string) =>
    request(`/jobs/${jobId}/tracks/${trackNum}/lyrics`, { method: "PUT", body: JSON.stringify({ content }) }),

  // Kashidashi
  matchKashidashi: (jobId: string, candidateId: number) =>
    request(`/jobs/${jobId}/kashidashi/${candidateId}/match`, { method: "PUT" }),
  skipKashidashi: (jobId: string) =>
    request(`/jobs/${jobId}/kashidashi/skip`, { method: "POST" }),
  reMatchKashidashi: (jobId: string) =>
    request(`/jobs/${jobId}/kashidashi/re-match`, { method: "POST" }),

  // Album groups
  createGroup: (jobId: string) =>
    request(`/jobs/${jobId}/group`, { method: "POST" }),
  addToGroup: (jobId: string, groupId: string) =>
    request(`/jobs/${jobId}/group/${groupId}`, { method: "PUT" }),
  removeFromGroup: (jobId: string) =>
    request(`/jobs/${jobId}/group`, { method: "DELETE" }),
  getGroup: (groupId: string) => request(`/groups/${groupId}`),

  // Re-rip
  reRip: (jobId: string) =>
    request(`/jobs/${jobId}/re-rip`, { method: "POST" }),
  reRipTrack: (jobId: string, trackNum: number) =>
    request(`/jobs/${jobId}/re-rip/${trackNum}`, { method: "POST" }),
  reRipFailed: (jobId: string) =>
    request(`/jobs/${jobId}/re-rip/failed`, { method: "POST" }),

  // WAV import
  importWav: (jobId: string, trackNum: number, file: File) =>
    uploadFile(`/jobs/${jobId}/tracks/${trackNum}/import-wav`, file),

  // Drives
  getDrives: () => request("/drives"),
  renameDrive: (driveId: string, name: string) =>
    request(`/drives/${driveId}`, { method: "PUT", body: JSON.stringify({ name }) }),
  updateDrive: (driveId: string, body: Record<string, unknown>) =>
    request(`/drives/${driveId}`, { method: "PUT", body: JSON.stringify(body) }),
  ejectDrive: (driveId: string) =>
    request(`/drives/${driveId}/eject`, { method: "POST" }),
  identifyDisc: (driveId: string) =>
    request(`/drives/${driveId}/identify`, { method: "POST" }),

  // History
  getHistory: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return request(`/history${qs}`);
  },
  getStats: () => request("/history/stats"),

  // WAV bulk import
  importWavFiles: async (params: {
    files: { file: File; trackNum: number }[];
    artist?: string;
    album?: string;
    catalogNumber?: string;
    sourceType: string;
    discNumber: number;
    totalDiscs: number;
  }) => {
    const form = new FormData();
    for (const { file, trackNum } of params.files) {
      form.append("files", file);
      form.append("track_numbers", String(trackNum));
    }
    if (params.artist) form.append("artist", params.artist);
    if (params.album) form.append("album", params.album);
    if (params.catalogNumber) form.append("catalog_number", params.catalogNumber);
    form.append("source_type", params.sourceType);
    form.append("disc_number", String(params.discNumber));
    form.append("total_discs", String(params.totalDiscs));
    const res = await fetch(`${BASE}/import`, { method: "POST", body: form });
    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(error.detail || res.statusText);
    }
    return res.json();
  },

  // Conflicts
  getConflicts: (jobId: string) => request(`/jobs/${jobId}/conflicts`),
  trashConflicts: (jobId: string) =>
    request(`/jobs/${jobId}/conflicts/trash`, { method: "POST" }),

  // Trash
  getTrash: () => request("/trash"),
  emptyTrash: () => request("/trash", { method: "DELETE" }),
  deleteTrashItem: (label: string) =>
    request(`/trash/${encodeURIComponent(label)}`, { method: "DELETE" }),

  // Settings
  getSettings: () => request("/settings"),
  updateSettings: (body: Record<string, unknown>) =>
    request("/settings", { method: "PUT", body: JSON.stringify(body) }),

  // Plex
  triggerPlexScan: () => request("/plex/scan", { method: "POST" }),

  // GnuDB submit
  gnudbHistory: (jobId: string) => request(`/jobs/${jobId}/gnudb`),
  gnudbPreview: (jobId: string, category?: string | null) =>
    request(`/jobs/${jobId}/gnudb/preview`, {
      method: "POST",
      body: JSON.stringify({ category: category ?? null }),
    }),
  gnudbSubmit: (jobId: string, category?: string | null) =>
    request(`/jobs/${jobId}/gnudb/submit`, {
      method: "POST",
      body: JSON.stringify({ category: category ?? null }),
    }),
};

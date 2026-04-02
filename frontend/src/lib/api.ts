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
  approveMetadata: (jobId: string) =>
    request(`/jobs/${jobId}/metadata/approve`, { method: "POST" }),
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
    request(`/jobs/${jobId}/kashidashi/${candidateId}/match`, { method: "POST" }),
  skipKashidashi: (jobId: string) =>
    request(`/jobs/${jobId}/kashidashi/skip`, { method: "POST" }),

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
  ejectDrive: (driveId: string) =>
    request(`/drives/${driveId}/eject`, { method: "POST" }),

  // History
  getHistory: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return request(`/history${qs}`);
  },
  getStats: () => request("/history/stats"),

  // Settings
  getSettings: () => request("/settings"),
  updateSettings: (body: Record<string, unknown>) =>
    request("/settings", { method: "PUT", body: JSON.stringify(body) }),
};

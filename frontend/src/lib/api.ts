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

  // Re-rip
  reRip: (jobId: string) =>
    request(`/jobs/${jobId}/re-rip`, { method: "POST" }),
  reRipTrack: (jobId: string, trackNum: number) =>
    request(`/jobs/${jobId}/re-rip/${trackNum}`, { method: "POST" }),

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

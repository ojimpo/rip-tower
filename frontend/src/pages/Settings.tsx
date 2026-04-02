import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, useMemo } from "react";
import { api } from "../lib/api";
import type { Drive } from "../lib/types";

interface AppConfig {
  general: { auto_approve_threshold: number; reminder_initial_hours: number; reminder_interval_hours: number };
  output: { format: string; quality: number; music_dir: string; incoming_dir: string; folder_template: string; file_template: string };
  integrations: { discord_webhook: string; discogs_token: string; musixmatch_token: string; plex_url: string; plex_section_id: number | null; llm_api_key: string; llm_model: string; kashidashi_url: string };
}

const SAMPLE_DATA: Record<string, string> = {
  artist: "\u690E\u540D\u6797\u6A4E",
  album: "\u7121\u7F6A\u30E2\u30E9\u30C8\u30EA\u30A2\u30E0",
  year: "1999",
  genre: "J-Pop",
  track_num: "01",
  title: "\u6B63\u3057\u3044\u8857",
  disc_num: "1",
  ext: "flac",
};

function templatePreview(template: string): string {
  let result = template;
  for (const [key, val] of Object.entries(SAMPLE_DATA)) {
    result = result.replace(new RegExp(`\\{${key}\\}`, "g"), val);
  }
  return result;
}

export default function Settings() {
  const queryClient = useQueryClient();

  const { data: config } = useQuery<AppConfig>({
    queryKey: ["settings"],
    queryFn: () => api.getSettings() as Promise<AppConfig>,
  });

  const { data: drives } = useQuery<Drive[]>({
    queryKey: ["drives"],
    queryFn: () => api.getDrives() as Promise<Drive[]>,
  });

  // Local state for editing (debounced save)
  const [localConfig, setLocalConfig] = useState<AppConfig | null>(null);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [renamingDrive, setRenamingDrive] = useState<string | null>(null);
  const [driveNewName, setDriveNewName] = useState("");

  // Use localConfig if set, otherwise use fetched config
  const cfg = localConfig ?? config;

  const saveMutation = useMutation({
    mutationFn: (newConfig: AppConfig) => api.updateSettings(newConfig as unknown as Record<string, unknown>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      setSaveStatus("saved");
      setTimeout(() => setSaveStatus("idle"), 2000);
    },
    onError: () => {
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 3000);
    },
  });

  const renameMutation = useMutation({
    mutationFn: ({ driveId, name }: { driveId: string; name: string }) =>
      api.renameDrive(driveId, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["drives"] });
      setRenamingDrive(null);
    },
  });

  const updateConfig = (updater: (c: AppConfig) => AppConfig) => {
    if (!cfg) return;
    setLocalConfig(updater(cfg));
  };

  const handleSave = () => {
    if (!cfg) return;
    setSaveStatus("saving");
    saveMutation.mutate(cfg);
  };

  const folderPreview = useMemo(
    () => cfg ? templatePreview(cfg.output.folder_template) : "",
    [cfg]
  );
  const filePreview = useMemo(
    () => cfg ? templatePreview(cfg.output.file_template) : "",
    [cfg]
  );

  if (!cfg) return <div className="p-4 text-gray-400">{"\u8AAD\u307F\u8FBC\u307F\u4E2D"}...</div>;

  return (
    <div>
      {/* Header */}
      <header className="sticky top-0 z-50 backdrop-blur-xl bg-[#0f0f1a]/80 border-b border-white/5">
        <div className="flex items-center justify-between px-4 py-3">
          <h1 className="text-lg font-bold tracking-tight">Settings</h1>
          <span className="text-[10px] text-gray-600">v0.1.0</span>
        </div>
      </header>

      <div className="px-4 pt-3 space-y-3">

        {/* ==================== General ==================== */}
        <details open className="group">
          <summary className="flex items-center justify-between py-2.5 px-3 rounded-xl bg-[#16213e] border border-white/5 cursor-pointer hover:border-white/10 transition list-none">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-blue-500/15 flex items-center justify-center">
                <svg className="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </div>
              <span className="text-sm font-semibold">{"\u4E00\u822C"}</span>
            </div>
            <svg className="w-4 h-4 text-gray-500 group-open:rotate-180 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
            </svg>
          </summary>
          <div className="mt-2 rounded-xl bg-[#16213e] border border-white/5 p-4 space-y-4">
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">Auto-approve threshold</label>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={cfg.general.auto_approve_threshold}
                  onChange={(e) => updateConfig((c) => ({
                    ...c, general: { ...c.general, auto_approve_threshold: +e.target.value },
                  }))}
                  className="flex-1 accent-[#e94560] h-1"
                />
                <span className="text-sm font-mono text-white w-8 text-right">
                  {cfg.general.auto_approve_threshold}
                </span>
              </div>
              <p className="text-[10px] text-gray-600 mt-1">Confidence score above this value will auto-approve</p>
            </div>
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">Review reminder interval</label>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[10px] text-gray-500 block mb-1">First reminder</label>
                  <div className="flex items-center gap-1">
                    <input
                      type="number"
                      value={cfg.general.reminder_initial_hours}
                      min={0}
                      onChange={(e) => updateConfig((c) => ({
                        ...c, general: { ...c.general, reminder_initial_hours: +e.target.value },
                      }))}
                      className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
                    />
                    <span className="text-xs text-gray-500 shrink-0">hours</span>
                  </div>
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 block mb-1">Repeat every</label>
                  <div className="flex items-center gap-1">
                    <input
                      type="number"
                      value={cfg.general.reminder_interval_hours}
                      min={0}
                      onChange={(e) => updateConfig((c) => ({
                        ...c, general: { ...c.general, reminder_interval_hours: +e.target.value },
                      }))}
                      className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
                    />
                    <span className="text-xs text-gray-500 shrink-0">hours</span>
                  </div>
                </div>
              </div>
              <p className="text-[10px] text-gray-600 mt-1">Set to 0 to disable reminders</p>
            </div>
          </div>
        </details>

        {/* ==================== Output ==================== */}
        <details open className="group">
          <summary className="flex items-center justify-between py-2.5 px-3 rounded-xl bg-[#16213e] border border-white/5 cursor-pointer hover:border-white/10 transition list-none">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-emerald-500/15 flex items-center justify-center">
                <svg className="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                </svg>
              </div>
              <span className="text-sm font-semibold">{"\u51FA\u529B"}</span>
            </div>
            <svg className="w-4 h-4 text-gray-500 group-open:rotate-180 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
            </svg>
          </summary>
          <div className="mt-2 rounded-xl bg-[#16213e] border border-white/5 p-4 space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-400 mb-1.5 block">Encoding format</label>
                <select
                  value={cfg.output.format}
                  onChange={(e) => updateConfig((c) => ({ ...c, output: { ...c.output, format: e.target.value } }))}
                  className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560] appearance-none"
                >
                  <option value="flac">FLAC</option>
                  <option value="alac">ALAC</option>
                  <option value="opus">Opus</option>
                  <option value="mp3">MP3</option>
                  <option value="wav">WAV</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-400 mb-1.5 block">Quality</label>
                <select
                  value={cfg.output.quality}
                  onChange={(e) => updateConfig((c) => ({ ...c, output: { ...c.output, quality: +e.target.value } }))}
                  className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560] appearance-none"
                >
                  <option value={5}>Level 5</option>
                  <option value={6}>Level 6</option>
                  <option value={7}>Level 7</option>
                  <option value={8}>Level 8 (max)</option>
                </select>
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">Output directory</label>
              <input
                type="text"
                value={cfg.output.music_dir}
                onChange={(e) => updateConfig((c) => ({ ...c, output: { ...c.output, music_dir: e.target.value } }))}
                className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
              />
            </div>
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">Folder template</label>
              <input
                type="text"
                value={cfg.output.folder_template}
                onChange={(e) => updateConfig((c) => ({ ...c, output: { ...c.output, folder_template: e.target.value } }))}
                className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono outline-none focus:border-[#e94560]"
              />
              <div className="mt-1.5 px-2.5 py-1.5 rounded bg-black/30 border border-white/5">
                <p className="text-[10px] text-gray-500">Preview:</p>
                <p className="text-[11px] text-gray-300 font-mono">{folderPreview}</p>
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">File name template</label>
              <input
                type="text"
                value={cfg.output.file_template}
                onChange={(e) => updateConfig((c) => ({ ...c, output: { ...c.output, file_template: e.target.value } }))}
                className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono outline-none focus:border-[#e94560]"
              />
              <div className="mt-1.5 px-2.5 py-1.5 rounded bg-black/30 border border-white/5">
                <p className="text-[10px] text-gray-500">Preview:</p>
                <p className="text-[11px] text-gray-300 font-mono">{filePreview}</p>
              </div>
            </div>
          </div>
        </details>

        {/* ==================== Integrations ==================== */}
        <details className="group">
          <summary className="flex items-center justify-between py-2.5 px-3 rounded-xl bg-[#16213e] border border-white/5 cursor-pointer hover:border-white/10 transition list-none">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-purple-500/15 flex items-center justify-center">
                <svg className="w-4 h-4 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                </svg>
              </div>
              <span className="text-sm font-semibold">{"\u9023\u643A"}</span>
            </div>
            <svg className="w-4 h-4 text-gray-500 group-open:rotate-180 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
            </svg>
          </summary>
          <div className="mt-2 rounded-xl bg-[#16213e] border border-white/5 p-4 space-y-4">
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">Discord Webhook URL</label>
              <input
                type="url"
                value={cfg.integrations.discord_webhook}
                onChange={(e) => updateConfig((c) => ({ ...c, integrations: { ...c.integrations, discord_webhook: e.target.value } }))}
                placeholder="https://discord.com/api/webhooks/..."
                className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
              />
            </div>
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">Discogs Token</label>
              <input
                type="password"
                value={cfg.integrations.discogs_token}
                onChange={(e) => updateConfig((c) => ({ ...c, integrations: { ...c.integrations, discogs_token: e.target.value } }))}
                placeholder="Personal access token"
                className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
              />
            </div>
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">Plex</label>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[10px] text-gray-500 block mb-1">Server URL</label>
                  <input
                    type="url"
                    value={cfg.integrations.plex_url}
                    onChange={(e) => updateConfig((c) => ({ ...c, integrations: { ...c.integrations, plex_url: e.target.value } }))}
                    placeholder="http://..."
                    className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 block mb-1">Section ID</label>
                  <input
                    type="number"
                    value={cfg.integrations.plex_section_id ?? ""}
                    onChange={(e) => updateConfig((c) => ({ ...c, integrations: { ...c.integrations, plex_section_id: e.target.value ? +e.target.value : null } }))}
                    className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
                  />
                </div>
              </div>
            </div>
            <div className="pt-2 border-t border-white/5">
              <label className="text-xs text-gray-400 mb-1.5 block">LLM (Metadata assist)</label>
              <div className="space-y-2">
                <input
                  type="password"
                  value={cfg.integrations.llm_api_key}
                  onChange={(e) => updateConfig((c) => ({ ...c, integrations: { ...c.integrations, llm_api_key: e.target.value } }))}
                  placeholder="API Key"
                  className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
                />
                <select
                  value={cfg.integrations.llm_model}
                  onChange={(e) => updateConfig((c) => ({ ...c, integrations: { ...c.integrations, llm_model: e.target.value } }))}
                  className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560] appearance-none"
                >
                  <option value="haiku">Claude Haiku (fast, cheap)</option>
                  <option value="sonnet">Claude Sonnet (better quality)</option>
                </select>
              </div>
              <p className="text-[10px] text-gray-600 mt-1">Used only when rule-based processing cannot resolve metadata issues</p>
            </div>
            <div className="pt-2 border-t border-white/5">
              <label className="text-xs text-gray-400 mb-1.5 block">{"\uD83D\uDCDA"} kashidashi ({"\u56F3\u66F8\u9928CD\u7BA1\u7406"})</label>
              <div>
                <label className="text-[10px] text-gray-500 block mb-1">API URL</label>
                <input
                  type="url"
                  value={cfg.integrations.kashidashi_url}
                  onChange={(e) => updateConfig((c) => ({ ...c, integrations: { ...c.integrations, kashidashi_url: e.target.value } }))}
                  placeholder="http://..."
                  className="w-full bg-[#0f0f1a] border border-white/8 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none focus:border-[#e94560]"
                />
              </div>
            </div>
          </div>
        </details>

        {/* ==================== Drives ==================== */}
        <details className="group">
          <summary className="flex items-center justify-between py-2.5 px-3 rounded-xl bg-[#16213e] border border-white/5 cursor-pointer hover:border-white/10 transition list-none">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-cyan-500/15 flex items-center justify-center">
                <svg className="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
                </svg>
              </div>
              <span className="text-sm font-semibold">{"\u30C9\u30E9\u30A4\u30D6"}</span>
            </div>
            <svg className="w-4 h-4 text-gray-500 group-open:rotate-180 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
            </svg>
          </summary>
          <div className="mt-2 rounded-xl bg-[#16213e] border border-white/5 divide-y divide-white/5 overflow-hidden">
            {drives?.map((drive) => {
              const isOnline = !!drive.current_path;
              const isRenaming = renamingDrive === drive.drive_id;

              return (
                <div key={drive.drive_id} className={`px-4 py-3 flex items-center justify-between ${!isOnline ? "opacity-50" : ""}`}>
                  <div className="flex items-center gap-3">
                    <div className={`w-2 h-2 rounded-full ${isOnline ? "bg-emerald-400" : "bg-gray-600"}`} />
                    <div>
                      <div className="flex items-center gap-2">
                        {isRenaming ? (
                          <input
                            type="text"
                            value={driveNewName}
                            onChange={(e) => setDriveNewName(e.target.value)}
                            onBlur={() => {
                              if (driveNewName && driveNewName !== drive.name) {
                                renameMutation.mutate({ driveId: drive.drive_id, name: driveNewName });
                              } else {
                                setRenamingDrive(null);
                              }
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" && driveNewName) {
                                renameMutation.mutate({ driveId: drive.drive_id, name: driveNewName });
                              }
                              if (e.key === "Escape") setRenamingDrive(null);
                            }}
                            autoFocus
                            className="text-sm font-medium bg-[#0f0f1a] border border-white/10 rounded px-2 py-0.5 outline-none focus:border-[#e94560] text-gray-200"
                          />
                        ) : (
                          <>
                            <span className="text-sm font-medium">{drive.name}</span>
                            <button
                              onClick={() => {
                                setRenamingDrive(drive.drive_id);
                                setDriveNewName(drive.name);
                              }}
                              className="text-[10px] text-[#e94560] hover:underline"
                            >
                              rename
                            </button>
                          </>
                        )}
                      </div>
                      <p className="text-[10px] text-gray-500 mt-0.5">
                        {isOnline
                          ? `${drive.current_path}${drive.model ? ` · ${drive.model}` : ""}`
                          : drive.last_seen_at
                            ? `Last seen: ${new Date(drive.last_seen_at).toLocaleDateString("ja-JP")}`
                            : "\u672A\u63A5\u7D9A"}
                      </p>
                    </div>
                  </div>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                    isOnline ? "bg-emerald-500/15 text-emerald-400" : "bg-gray-700 text-gray-500"
                  }`}>
                    {isOnline ? "Online" : "Offline"}
                  </span>
                </div>
              );
            })}
            {(!drives || drives.length === 0) && (
              <div className="px-4 py-3 text-sm text-gray-500">{"\u30C9\u30E9\u30A4\u30D6\u306A\u3057"}</div>
            )}
          </div>
        </details>

        {/* Save button */}
        <div className="pt-2 pb-4">
          <button
            onClick={handleSave}
            disabled={saveMutation.isPending}
            className="w-full py-3 rounded-xl bg-gradient-to-r from-[#e94560] to-pink-600 text-sm font-bold text-white shadow-lg shadow-[#e94560]/20 hover:shadow-[#e94560]/40 active:scale-[0.98] transition-all disabled:opacity-50"
          >
            {saveMutation.isPending ? "Saving..." : "Save Changes"}
          </button>
          {saveStatus === "saved" && (
            <p className="text-xs text-emerald-400 text-center mt-2">{"\u2705"} Saved successfully</p>
          )}
          {saveStatus === "error" && (
            <p className="text-xs text-red-400 text-center mt-2">{"\u274C"} Save failed</p>
          )}
        </div>
      </div>
    </div>
  );
}

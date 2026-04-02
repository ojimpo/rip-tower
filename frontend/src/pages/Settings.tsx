import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Drive } from "../lib/types";

interface AppConfig {
  general: { auto_approve_threshold: number; reminder_initial_hours: number; reminder_interval_hours: number };
  output: { format: string; quality: number; music_dir: string; incoming_dir: string; folder_template: string; file_template: string };
  integrations: { discord_webhook: string; discogs_token: string; musixmatch_token: string; plex_section_id: number | null; llm_api_key: string; llm_model: string; kashidashi_url: string };
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

  const saveMutation = useMutation({
    mutationFn: (newConfig: AppConfig) => api.updateSettings(newConfig as unknown as Record<string, unknown>),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  if (!config) return <div className="p-4 text-gray-400">読み込み中...</div>;

  return (
    <div>
      <div className="sticky top-0 bg-[#0f0f1a] z-10 px-4 py-3 border-b border-gray-800">
        <h1 className="text-xl font-bold">設定</h1>
      </div>

      <div className="p-4 space-y-4">
        {/* General */}
        <details open className="bg-[#16213e] rounded-lg p-3">
          <summary className="font-bold text-sm cursor-pointer">一般</summary>
          <div className="mt-2 space-y-2 text-sm">
            <label className="block">
              <span className="text-gray-400">自動承認しきい値</span>
              <input
                type="number"
                className="w-full mt-1 bg-gray-800 rounded px-2 py-1 text-white"
                value={config.general.auto_approve_threshold}
                onChange={(e) => saveMutation.mutate({
                  ...config,
                  general: { ...config.general, auto_approve_threshold: +e.target.value },
                })}
              />
            </label>
          </div>
        </details>

        {/* Output */}
        <details className="bg-[#16213e] rounded-lg p-3">
          <summary className="font-bold text-sm cursor-pointer">出力</summary>
          <div className="mt-2 space-y-2 text-sm">
            <label className="block">
              <span className="text-gray-400">エンコード形式</span>
              <select
                className="w-full mt-1 bg-gray-800 rounded px-2 py-1 text-white"
                value={config.output.format}
                onChange={(e) => saveMutation.mutate({
                  ...config,
                  output: { ...config.output, format: e.target.value },
                })}
              >
                <option value="flac">FLAC</option>
                <option value="alac">ALAC</option>
                <option value="opus">Opus</option>
                <option value="mp3">MP3</option>
                <option value="wav">WAV</option>
              </select>
            </label>
            <label className="block">
              <span className="text-gray-400">品質</span>
              <input
                type="number"
                className="w-full mt-1 bg-gray-800 rounded px-2 py-1 text-white"
                value={config.output.quality}
                onChange={(e) => saveMutation.mutate({
                  ...config,
                  output: { ...config.output, quality: +e.target.value },
                })}
              />
            </label>
            <label className="block">
              <span className="text-gray-400">フォルダテンプレート</span>
              <input
                type="text"
                className="w-full mt-1 bg-gray-800 rounded px-2 py-1 text-white font-mono text-xs"
                value={config.output.folder_template}
                onChange={(e) => saveMutation.mutate({
                  ...config,
                  output: { ...config.output, folder_template: e.target.value },
                })}
              />
            </label>
            <label className="block">
              <span className="text-gray-400">ファイルテンプレート</span>
              <input
                type="text"
                className="w-full mt-1 bg-gray-800 rounded px-2 py-1 text-white font-mono text-xs"
                value={config.output.file_template}
                onChange={(e) => saveMutation.mutate({
                  ...config,
                  output: { ...config.output, file_template: e.target.value },
                })}
              />
            </label>
          </div>
        </details>

        {/* Integrations */}
        <details className="bg-[#16213e] rounded-lg p-3">
          <summary className="font-bold text-sm cursor-pointer">連携</summary>
          <div className="mt-2 space-y-2 text-sm">
            {[
              { key: "kashidashi_url" as const, label: "Kashidashi URL" },
              { key: "discord_webhook" as const, label: "Discord Webhook" },
              { key: "discogs_token" as const, label: "Discogs Token" },
              { key: "llm_api_key" as const, label: "LLM API Key" },
            ].map(({ key, label }) => (
              <label key={key} className="block">
                <span className="text-gray-400">{label}</span>
                <input
                  type={key.includes("token") || key.includes("key") ? "password" : "text"}
                  className="w-full mt-1 bg-gray-800 rounded px-2 py-1 text-white font-mono text-xs"
                  value={config.integrations[key]}
                  onChange={(e) => saveMutation.mutate({
                    ...config,
                    integrations: { ...config.integrations, [key]: e.target.value },
                  })}
                />
              </label>
            ))}
          </div>
        </details>

        {/* Drives */}
        <details className="bg-[#16213e] rounded-lg p-3">
          <summary className="font-bold text-sm cursor-pointer">ドライブ</summary>
          <div className="mt-2 space-y-2">
            {drives?.map((drive) => (
              <div key={drive.drive_id} className="flex items-center gap-2 text-sm">
                <span className={drive.current_path ? "text-green-400" : "text-gray-600"}>●</span>
                <span className="flex-1">{drive.name}</span>
                <span className="text-xs text-gray-500">
                  {drive.current_path || "未接続"}
                </span>
              </div>
            ))}
            {(!drives || drives.length === 0) && (
              <p className="text-gray-500 text-sm">ドライブなし</p>
            )}
          </div>
        </details>
      </div>
    </div>
  );
}

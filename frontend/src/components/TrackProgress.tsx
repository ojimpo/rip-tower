import { useState } from "react";

interface TrackProgressItem {
  track_num: number;
  title: string | null;
  rip_status: string;
  encode_status: string;
  rip_progress: number | null;
}

interface Props {
  tracks: TrackProgressItem[];
  maxVisible?: number;
}

function Spinner() {
  return (
    <svg className="w-3.5 h-3.5 animate-spin text-emerald-400" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

export default function TrackProgress({ tracks, maxVisible = 5 }: Props) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? tracks : tracks.slice(0, maxVisible);
  const remaining = tracks.length - maxVisible;

  const totalTracks = tracks.length;
  const doneTracks = tracks.filter(
    (t) => t.rip_status === "ok" || t.rip_status === "ok_degraded"
  ).length;
  const overallPercent = totalTracks > 0 ? Math.round((doneTracks / totalTracks) * 100) : 0;

  return (
    <div className="py-2.5 space-y-2">
      {/* Overall progress bar */}
      <div className="px-4">
        <div className="flex items-center justify-between text-[10px] text-gray-500 mb-1">
          <span>{doneTracks}/{totalTracks} tracks</span>
          <span>{overallPercent}%</span>
        </div>
        <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-emerald-500 to-emerald-400 rounded-full transition-all duration-500"
            style={{ width: `${overallPercent}%` }}
          />
        </div>
      </div>

      {/* Track list */}
      <div className="px-4 space-y-0.5">
        {visible.map((t) => {
          const isRipping = t.rip_status === "ripping";
          const isEncoding = t.encode_status === "encoding";
          const isActive = isRipping || isEncoding;
          const ripOk = t.rip_status === "ok" || t.rip_status === "ok_degraded";
          const isDone = ripOk && (t.encode_status === "ok" || t.encode_status === "pending");
          const isFailed = t.rip_status === "failed";
          const isDegraded = t.rip_status === "ok_degraded";

          return (
            <div key={t.track_num} className="flex items-center gap-2 text-xs h-6">
              <span className="w-4 flex items-center justify-center shrink-0">
                {isDone && (
                  <svg className={`w-3.5 h-3.5 ${isDegraded ? "text-amber-400" : "text-emerald-400"}`} fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                  </svg>
                )}
                {isActive && <Spinner />}
                {isFailed && (
                  <svg className="w-3.5 h-3.5 text-red-400" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                  </svg>
                )}
                {!isDone && !isActive && !isFailed && (
                  <span className="w-2 h-2 rounded-full bg-gray-700 block" />
                )}
              </span>
              <span className="text-gray-500 w-4 text-right shrink-0 font-mono text-[10px]">{t.track_num}</span>
              <span
                className={`truncate ${
                  isActive
                    ? "text-white font-medium"
                    : isDone
                      ? "text-gray-300"
                      : isFailed
                        ? "text-red-300"
                        : "text-gray-500"
                }`}
              >
                {t.title || `Track ${t.track_num}`}
              </span>
              {isActive && (
                <span className="ml-auto text-[10px] text-emerald-400 font-mono shrink-0">
                  {isRipping ? "ripping" : "encoding"}
                </span>
              )}
            </div>
          );
        })}
        {!expanded && remaining > 0 && (
          <button
            className="w-full text-center text-[10px] text-gray-500 hover:text-gray-400 pt-1 transition"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setExpanded(true);
            }}
          >
            + 残り{remaining}曲
          </button>
        )}
      </div>
    </div>
  );
}

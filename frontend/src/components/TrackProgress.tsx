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

export default function TrackProgress({ tracks, maxVisible = 5 }: Props) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? tracks : tracks.slice(0, maxVisible);
  const remaining = tracks.length - maxVisible;

  return (
    <div className="px-4 py-2.5 space-y-1">
      {visible.map((t) => {
        const isActive = t.rip_status === "ripping" || t.encode_status === "encoding";
        const isDone = t.rip_status === "ok" && t.encode_status === "ok";
        const isFailed = t.rip_status === "failed";
        const isPending = !isActive && !isDone && !isFailed && t.rip_status !== "ok";

        return (
          <div key={t.track_num} className="flex items-center gap-2 text-xs">
            <span className="w-4 text-center">
              {isDone && "\u2705"}
              {isActive && <span className="inline-block animate-pulse">{"\uD83D\uDD04"}</span>}
              {isFailed && "\u274C"}
              {isPending && <span className="text-gray-600">{"\u25CB"}</span>}
              {!isDone && !isActive && !isFailed && !isPending && t.rip_status === "ok" && "\u2705"}
            </span>
            <span className="text-gray-400 w-4 text-right">{t.track_num}.</span>
            <span
              className={
                isActive
                  ? "text-white font-medium"
                  : isDone
                    ? "text-gray-300"
                    : isFailed
                      ? "text-red-300"
                      : "text-gray-500"
              }
            >
              {t.title || `Track ${t.track_num}`}
            </span>
            {isActive && t.rip_progress != null && (
              <span className="ml-auto text-emerald-400 font-mono text-[11px]">
                {t.rip_progress}%
              </span>
            )}
          </div>
        );
      })}
      {!expanded && remaining > 0 && (
        <button
          className="w-full text-center text-[11px] text-gray-500 hover:text-gray-400 pt-1 transition"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setExpanded(true);
          }}
        >
          + {"\u6B8B\u308A"}{remaining}{"\u66F2"}
        </button>
      )}
    </div>
  );
}

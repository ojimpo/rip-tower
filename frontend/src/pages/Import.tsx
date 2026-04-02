import { useState, useCallback, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../lib/api";

interface SelectedFile {
  file: File;
  trackNum: number;
}

export default function Import() {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<SelectedFile[]>([]);
  const [artist, setArtist] = useState("");
  const [album, setAlbum] = useState("");
  const [catalogNumber, setCatalogNumber] = useState("");
  const [sourceType, setSourceType] = useState("unclassified");
  const [discNumber, setDiscNumber] = useState(1);
  const [totalDiscs, setTotalDiscs] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ job_id: string } | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const addFiles = useCallback((newFiles: FileList | File[]) => {
    const wavFiles = Array.from(newFiles).filter(
      (f) => f.name.toLowerCase().endsWith(".wav")
    );
    if (wavFiles.length === 0) return;

    // Sort by filename for natural track ordering
    wavFiles.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));

    setFiles((prev) => {
      const startNum = prev.length + 1;
      const additions: SelectedFile[] = wavFiles.map((file, i) => ({
        file,
        trackNum: startNum + i,
      }));
      return [...prev, ...additions];
    });
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer.files.length > 0) {
        addFiles(e.dataTransfer.files);
      }
    },
    [addFiles]
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const removeFile = (index: number) => {
    setFiles((prev) => {
      const next = prev.filter((_, i) => i !== index);
      // Re-number tracks
      return next.map((f, i) => ({ ...f, trackNum: i + 1 }));
    });
  };

  const handleSubmit = async () => {
    if (files.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.importWavFiles({
        files: files.map((f) => ({ file: f.file, trackNum: f.trackNum })),
        artist: artist || undefined,
        album: album || undefined,
        catalogNumber: catalogNumber || undefined,
        sourceType,
        discNumber,
        totalDiscs,
      });
      setResult(res as { job_id: string });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setSubmitting(false);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  // Success state
  if (result) {
    return (
      <div>
        <header className="sticky top-0 z-50 backdrop-blur-xl bg-[#0f0f1a]/80 border-b border-white/5">
          <div className="flex items-center gap-3 px-4 py-3">
            <Link to="/" className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-white/10 transition">
              <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
              </svg>
            </Link>
            <h1 className="text-lg font-bold tracking-tight">WAV Import</h1>
          </div>
        </header>
        <div className="mx-4 mt-16 text-center">
          <p className="text-4xl mb-4">&#10003;</p>
          <p className="text-lg font-medium text-emerald-400 mb-2">Import started</p>
          <p className="text-sm text-gray-400 mb-6">
            {files.length} files submitted for processing
          </p>
          <button
            onClick={() => navigate(`/job/${result.job_id}`)}
            className="px-6 py-2.5 rounded-xl bg-[#e94560] text-white font-medium hover:bg-[#d63d56] transition"
          >
            View Job
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <header className="sticky top-0 z-50 backdrop-blur-xl bg-[#0f0f1a]/80 border-b border-white/5">
        <div className="flex items-center gap-3 px-4 py-3">
          <Link to="/" className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-white/10 transition">
            <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
            </svg>
          </Link>
          <h1 className="text-lg font-bold tracking-tight">WAV Import</h1>
        </div>
      </header>

      <div className="mx-3 mt-4 space-y-4">
        {/* Drop zone */}
        <div
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onClick={() => fileInputRef.current?.click()}
          className={`rounded-xl border-2 border-dashed p-8 text-center cursor-pointer transition ${
            dragOver
              ? "border-[#e94560] bg-[#e94560]/10"
              : "border-white/10 bg-[#16213e] hover:border-white/20"
          }`}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".wav"
            multiple
            className="hidden"
            onChange={(e) => e.target.files && addFiles(e.target.files)}
          />
          <svg className="w-10 h-10 mx-auto mb-3 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
          <p className="text-sm text-gray-400">
            WAV files をドラッグ&ドロップ
          </p>
          <p className="text-xs text-gray-600 mt-1">
            or click to browse
          </p>
        </div>

        {/* File list */}
        {files.length > 0 && (
          <div className="rounded-xl bg-[#16213e] border border-white/5 overflow-hidden">
            <div className="px-3 py-2 border-b border-white/5 flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                {files.length} files
              </span>
              <button
                onClick={() => setFiles([])}
                className="text-xs text-gray-500 hover:text-red-400 transition"
              >
                Clear all
              </button>
            </div>
            <div className="max-h-60 overflow-y-auto divide-y divide-white/5">
              {files.map((f, i) => (
                <div key={i} className="flex items-center gap-3 px-3 py-2">
                  <span className="text-xs font-mono text-gray-500 w-6 text-right shrink-0">
                    {f.trackNum}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm truncate">{f.file.name}</p>
                    <p className="text-xs text-gray-600">{formatSize(f.file.size)}</p>
                  </div>
                  <button
                    onClick={() => removeFile(i)}
                    className="w-7 h-7 flex items-center justify-center rounded hover:bg-white/10 text-gray-500 hover:text-red-400 transition shrink-0"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Hint fields */}
        <div className="rounded-xl bg-[#16213e] border border-white/5 p-4 space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
            Hints (optional)
          </h2>

          <div>
            <label className="text-xs text-gray-400 block mb-1">Artist</label>
            <input
              type="text"
              value={artist}
              onChange={(e) => setArtist(e.target.value)}
              className="w-full bg-[#0f0f1a] border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#e94560]/50 transition"
              placeholder="Artist name"
            />
          </div>

          <div>
            <label className="text-xs text-gray-400 block mb-1">Album</label>
            <input
              type="text"
              value={album}
              onChange={(e) => setAlbum(e.target.value)}
              className="w-full bg-[#0f0f1a] border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#e94560]/50 transition"
              placeholder="Album title"
            />
          </div>

          <div>
            <label className="text-xs text-gray-400 block mb-1">Catalog Number</label>
            <input
              type="text"
              value={catalogNumber}
              onChange={(e) => setCatalogNumber(e.target.value)}
              className="w-full bg-[#0f0f1a] border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#e94560]/50 transition"
              placeholder="e.g. COCC-12345"
            />
          </div>

          <div>
            <label className="text-xs text-gray-400 block mb-1">Source</label>
            <select
              value={sourceType}
              onChange={(e) => setSourceType(e.target.value)}
              className="w-full bg-[#0f0f1a] border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#e94560]/50 transition"
            >
              <option value="unclassified">未分類</option>
              <option value="library">図書館</option>
              <option value="owned">手持ち</option>
            </select>
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <label className="text-xs text-gray-400 block mb-1">Disc #</label>
              <input
                type="number"
                min={1}
                value={discNumber}
                onChange={(e) => setDiscNumber(Number(e.target.value) || 1)}
                className="w-full bg-[#0f0f1a] border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#e94560]/50 transition"
              />
            </div>
            <div className="flex-1">
              <label className="text-xs text-gray-400 block mb-1">Total Discs</label>
              <input
                type="number"
                min={1}
                value={totalDiscs}
                onChange={(e) => setTotalDiscs(Number(e.target.value) || 1)}
                className="w-full bg-[#0f0f1a] border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[#e94560]/50 transition"
              />
            </div>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-xl bg-red-950/30 border border-red-500/30 px-4 py-3">
            <p className="text-sm text-red-300">{error}</p>
          </div>
        )}

        {/* Submit */}
        <button
          onClick={handleSubmit}
          disabled={files.length === 0 || submitting}
          className={`w-full py-3 rounded-xl font-medium text-sm transition ${
            files.length === 0 || submitting
              ? "bg-gray-700 text-gray-500 cursor-not-allowed"
              : "bg-[#e94560] text-white hover:bg-[#d63d56] active:scale-[0.98]"
          }`}
        >
          {submitting ? "Importing..." : `Import ${files.length} files`}
        </button>

        <div className="h-4" />
      </div>
    </div>
  );
}

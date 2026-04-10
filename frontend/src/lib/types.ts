export interface Drive {
  drive_id: string;
  name: string;
  current_path: string | null;
  last_seen_at: string | null;
  model: string | null;
  serial: string | null;
  has_disc: boolean;
  tray_open: boolean;
  disc_info: {
    artist: string | null;
    album: string | null;
    track_count: number | null;
  } | null;
  auto_rip: boolean;
  auto_rip_source_type: string;
  active_job_id: string | null;
  active_job_status: string | null;
}

export interface Job {
  id: string;
  album_group: string | null;
  drive_id: string | null;
  disc_id: string | null;
  status: string;
  source_type: string;
  output_dir: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface JobMetadata {
  artist: string | null;
  album: string | null;
  album_base: string | null;
  year: number | null;
  genre: string | null;
  disc_number: number;
  total_discs: number;
  is_compilation: boolean;
  confidence: number | null;
  source: string | null;
  needs_review: boolean;
  issues: string | null;
  approved: boolean;
}

export interface Track {
  track_num: number;
  title: string | null;
  artist: string | null;
  rip_status: string;
  encode_status: string;
  duration_ms: number | null;
  lyrics_source: string | null;
  lyrics_content: string | null;
  rip_progress: number | null;
}

export interface MetadataCandidate {
  id: number;
  source: string;
  source_url: string | null;
  artist: string | null;
  album: string | null;
  year: number | null;
  genre: string | null;
  confidence: number | null;
  selected: boolean;
}

export interface Artwork {
  id: number;
  source: string;
  url: string | null;
  local_path: string | null;
  width: number | null;
  height: number | null;
  file_size: number | null;
  selected: boolean;
}

export interface KashidashiCandidate {
  id: number;
  item_id: number;
  title: string | null;
  artist: string | null;
  score: number | null;
  match_type: string | null;
  matched: boolean;
}

export interface JobDetail {
  job: Job;
  metadata: JobMetadata | null;
  tracks: Track[];
  candidates: MetadataCandidate[];
  artworks: Artwork[];
  kashidashi_candidates: KashidashiCandidate[];
}

export interface GroupJob {
  job_id: string;
  status: string;
  artist: string | null;
  album: string | null;
  disc_number: number | null;
  total_discs: number | null;
}

export interface GroupResponse {
  album_group: string;
  jobs: GroupJob[];
}

export interface JobSummary {
  job_id: string;
  status: string;
  artist: string | null;
  album: string | null;
  drive_name: string | null;
  track_count: number | null;
  current_track: number | null;
  current_track_percent: number | null;
  tracks_done: number | null;
  track_titles: (string | null)[] | null;
  disc_total_seconds: number | null;
  elapsed_seconds: number | null;
  created_at: string;
  updated_at: string;
  error_message: string | null;
  artwork_url: string | null;
}

export interface HistoryItem {
  job_id: string;
  artist: string | null;
  album: string | null;
  source_type: string;
  completed_at: string | null;
  track_count: number | null;
  artwork_url: string | null;
  kashidashi_id: string | null;
}

export interface ConflictFile {
  name: string;
  size: number;
  path: string;
}

export interface ConflictsResponse {
  output_dir: string;
  files: ConflictFile[];
}

export interface TrashItem {
  label: string;
  files: { name: string; size: number }[];
  total_size: number;
}

export interface TrashResponse {
  items: TrashItem[];
  total_size: number;
}

export type WsEvent =
  | { type: "job:status"; job_id: string; status: string }
  | { type: "job:progress"; job_id: string; track: number; total: number; percent: number }
  | { type: "job:track_done"; job_id: string; track: number }
  | { type: "job:complete"; job_id: string }
  | { type: "job:error"; job_id: string; message: string }
  | { type: "job:review"; job_id: string; reason: string }
  | { type: "drive:connected"; drive_id: string; name: string; path: string }
  | { type: "drive:disconnected"; drive_id: string; name: string }
  | { type: "drive:disc_inserted"; drive_id: string }
  | { type: "drive:disc_ejected"; drive_id: string }
  | { type: "drive:update"; drive_id: string };

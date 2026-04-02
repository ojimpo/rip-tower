export interface Drive {
  drive_id: string;
  name: string;
  current_path: string | null;
  last_seen_at: string | null;
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

export type WsEvent =
  | { type: "job:status"; job_id: string; status: string }
  | { type: "job:progress"; job_id: string; track: number; total: number; percent: number }
  | { type: "job:complete"; job_id: string }
  | { type: "job:error"; job_id: string; message: string }
  | { type: "job:review"; job_id: string; reason: string }
  | { type: "drive:connected"; drive_id: string; name: string; path: string }
  | { type: "drive:disconnected"; drive_id: string; name: string };

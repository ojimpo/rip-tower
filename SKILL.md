---
name: rip-tower
description: CD ripping via Rip Tower API. Use when the user wants to rip a CD, check ripping status, eject a disc, or manage the CD ripping tower.
---

# Rip Tower — CD Ripping Control

Rip Tower is a Docker app running on arigato-nas that manages CD ripping with 3 USB drives.
WebUI: http://rip-tower.arigato-nas/
API base: http://localhost:3900/api

## Quick Reference

### List drives and their status
```bash
curl -s http://localhost:3900/api/drives | python3 -m json.tool
```
Shows all 3 drives (黒ロジテック1, 黒ロジテック2, 白ロジテック), connection status, and whether a CD is inserted.

### Identify a disc (read CD info without ripping)
```bash
curl -s -X POST http://localhost:3900/api/drives/DRIVE_ID/identify | python3 -m json.tool
```
Returns disc_id, track_count, and MusicBrainz lookup (artist/album if found).

### Start ripping
```bash
curl -s -X POST http://localhost:3900/api/rip \
  -H "Content-Type: application/json" \
  -d '{
    "drive_id": "DRIVE_ID",
    "source_type": "library",
    "disc_number": 1,
    "total_discs": 1,
    "hints": {"artist": "...", "album": "...", "catalog": "..."}
  }' | python3 -m json.tool
```
- `source_type`: "library" (図書館CD), "owned" (手持ち), "unknown"
- `hints`: optional, helps metadata resolution
- `disc_number` / `total_discs`: for multi-disc sets
- Response includes `job_id` and URL to the job detail page

### Check job status
```bash
curl -s http://localhost:3900/api/jobs | python3 -m json.tool
```
Lists all jobs with status, artist, album, track progress.

### Get job details
```bash
curl -s http://localhost:3900/api/jobs/JOB_ID | python3 -m json.tool
```

### Approve metadata (after review)
```bash
curl -s -X POST http://localhost:3900/api/jobs/JOB_ID/metadata/approve
```

### Eject disc
```bash
curl -s -X POST http://localhost:3900/api/drives/DRIVE_ID/eject
```

### History and stats
```bash
curl -s http://localhost:3900/api/history/stats | python3 -m json.tool
```

## Workflow

1. User inserts a CD
2. Use `drives` to find which drive has a disc
3. Use `rip` to start — set source_type to "library" for borrowed CDs
4. Monitor progress via `jobs` list
5. If status is "review", tell user to check WebUI or approve here
6. Job completes → files placed in /mnt/media/music/Artist/Album/

## Notes

- Always check drives first to get the correct DRIVE_ID
- For 図書館CD (kashidashi), use source_type "library"
- For multi-disc albums, set disc_number and total_discs
- The WebUI at http://rip-tower.arigato-nas/ has full metadata editing, artwork, lyrics
- If metadata confidence is >= 85, the job auto-approves without review

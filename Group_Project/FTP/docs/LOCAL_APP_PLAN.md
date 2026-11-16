# Local FTP app design and implementation plan

The original loopback-only design below was extended at the user's request
on 2026-11-16. [LAN_ACCESS.md](LAN_ACCESS.md) specifies the current shared
network binding, independent browser sessions, and host-only owner bootstrap.

The user approved building a local browser app with saved FTP devices on
2026-11-15, without further design questions. Retain Python and keep all
changes inside FTP. Build on feat/ftp; preserve CLI interoperability.

## Design

Use a loopback-only Python HTTP service, SQLite, and offline vanilla
HTML/CSS/JS. No frontend build, cloud account, or deployment is required.
One initial local owner account is created in the browser; salted scrypt
password verifiers and hashed expiring sessions are stored in SQLite.
Store device name/host/port/FTP username and transfer history in SQLite.
FTP device passwords are session-memory only. Login is for the local app;
connecting a saved device separately authenticates to that FTP server.

UI: quiet, minimalist light canvas, dark ink and a restrained mint/teal
accent. Saved devices beside one transfer workspace. Show filename, bytes,
progress, speed, time remaining, and clear completion/error/cancel states.
Use separate History and Performance views for secondary information.
Responsive layout, keyboard controls, labels, and readable empty states.

Large files use raw streaming HTTP uploads to disk, background FTP workers,
incremental progress polling, and streamed browser downloads. Bound jobs
and file sizes; never read a full media file into memory. Uploads use the
existing FTP binary client. Optional gzip produces an explicit .gz remote
file; optional unpacking is limited by the same output-size bound.

Performance tests compare plain and gzip transfers of the same source,
verify downloaded SHA-256 content, measure preprocessing and FTP payload
times separately, and export actual throughput/compression graphs and CSV.
No claim that gzip helps precompressed MP3/MP4 or that loopback measures LAN
capacity. Include real generated audio/video stress evidence.

## Backend/frontend contract

All API JSON errors: `{error: string}`. Same-origin credentials; mutating
requests send `X-FTP-App: 1` and authenticated requests additionally send
`X-CSRF-Token` from `/api/session`. Upload PUT sends the File as raw body.

- GET `/api/session`: `{setup_required, setup_allowed, user: {id,username}|null, csrf}`.
- POST `/api/setup`, `/api/login`: `{username,password}` -> session JSON;
  server sets HttpOnly SameSite=Strict cookie. POST `/api/logout` clears it.
- GET `/api/devices`: `{devices:[{id,name,host,port,username,local,ready,last_connected}]}`.
- POST `/api/devices`: `{name,host,port,username}` -> `{device}`.
- POST `/api/devices/{id}/connect`: `{password}` -> `{device}`. A local demo
  device needs no password and is auto-ready after app authentication.
- DELETE `/api/devices/{id}` removes a saved profile (not remote files).
- GET `/api/devices/{id}/files?path=/`: `{path,entries:[{name,directory,size}]}`.
- POST `/api/jobs`: `{device_id,kind:'upload'|'download'|'benchmark',name,
  size,remote_path,compress:false,unpack:false}` -> `{job}`. Upload/benchmark
  jobs start `awaiting_upload`; download jobs start in a worker.
- PUT `/api/jobs/{id}/source`: raw browser file, exact declared Content-Length
  -> `{job}`. Source filename must be a basename; remote_path is a directory
  for upload/benchmark, full remote filename for download.
- GET `/api/jobs`: `{jobs:[job]}` newest first. GET `/api/jobs/{id}`: `{job}`.
- POST `/api/jobs/{id}/cancel` requests cancellation. DELETE job removes
  terminal history/local artifacts only; active jobs must first finish.
- GET `/api/jobs/{id}/download`: authenticated completed-download attachment.
- GET `/api/jobs/{id}/report.csv`: measured benchmark CSV attachment.

Job fields: `id,device_id,device_name,kind,name,size,remote_path,compress,
unpack,status,phase,done,total,percent,speed_bps,eta_seconds,elapsed_seconds,
created,error,result,samples`. Status: awaiting_upload/queued/running/
completed/failed/cancelled. Phase text is human-readable. Samples:
`[{elapsed,bytes,speed_bps,mode}]`, in FTP payload bytes, no TCP/IP headers.
Benchmark result: `{runs:[{mode,source_bytes,payload_bytes,compression_seconds,
transfer_seconds,total_seconds,throughput_mib_s,effective_mib_s,sha256,verified}]}`.

## Work packages and ownership

1. Root: tests first, client progress/cancellation hooks, SQLite/auth, HTTP
   routes, workers, streaming, lifecycle/security, integration and docs.
2. Frontend: static/index.html, styles.css, app.js following the API above;
   plain JS only, no API mocks in production, no backend edits.
3. Benchmark: `ftp_lab/benchmark.py`, benchmark tests, offline media fixture
   generator and report renderer. API: `run_benchmark(source, client_factory,
   work_dir, remote_dir='/', progress=None, cancel=None)` returns result above.
   progress callback `(mode, cumulative_payload_bytes, total_payload_bytes,
   elapsed_seconds)` during upload. Callable cancel raises on cancellation.
   Client.upload/download accept keyword `progress(delta_bytes)`; engine
   must use unique remote names, verify both modes, clean its own remote
   files, and keep source untouched. Export CSV/SVG/PNG where available.

Use failing behavior tests before implementation. Independently review the
complete feature after integration. Verify actual browser login/device/
upload/download/benchmark flows and large audio/video tests; do not infer
runtime success from source inspection. Keep investigation data in .scratch.

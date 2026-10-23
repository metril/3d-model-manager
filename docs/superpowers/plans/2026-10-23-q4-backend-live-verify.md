# Q4-backend live verification

A1 Mini (real hardware):
1. Settings > printer > options: set `camera_rotation` 90; `GET /api/printers/{id}/camera/status` reports `rotation: 90`; `/camera/snapshot` returns a portrait (rotated) JPEG.
2. `/camera/stream?fps=2` visibly slower than `?fps=15`; `fps=0` and `fps=16` return 422.
3. Mint an overlay token (`POST /api/printers/{id}/overlay-tokens`), open `/api/overlay/{id}?token=...` in a private window: JSON shows state + working stream/snapshot URLs. Revoke (`DELETE`), reload: 401.
4. External camera: PATCH `options.camera = {"kind":"mjpeg","url":"http://..."}`; stream switches to it; GET printer shows `has_url`, never the URL.

RTSP (X1/H2/P2S): beta, CI-only (mediamtx rtsps test pattern). No real-hardware check; status reports `beta: true`.

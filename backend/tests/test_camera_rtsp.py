"""RTSP camera source (Q4): splitter, argv, and a mediamtx rtsps integration test."""

from __future__ import annotations

import datetime
import shutil
import threading
import time

import pytest

from app.printers import camera_rtsp
from app.printers.camera_rtsp import (
    BAMBU_RTSP_PORT,
    FfmpegMjpegSource,
    JpegSplitter,
    bambu_rtsp_url,
    build_argv,
)

A = b"\xff\xd8\xff\xe0AAAA\xff\xd9"
B = b"\xff\xd8\xff\xdbBBBBBB\xff\xd9"


def test_splitter_whole_and_chunked() -> None:
    assert JpegSplitter().feed(A + B) == [A, B]
    s = JpegSplitter()
    out: list[bytes] = []
    for i in range(0, len(A + B), 3):  # SOI/EOI straddle chunk boundaries
        out += s.feed((A + B)[i : i + 3])
    assert out == [A, B]


def test_splitter_skips_garbage_and_waits_for_eoi() -> None:
    s = JpegSplitter()
    assert s.feed(b"junk" + A[:6]) == []
    assert s.feed(A[6:] + b"trail") == [A]


def test_splitter_buffer_bounded() -> None:
    s = JpegSplitter(max_buffer=100)
    assert s.feed(b"\xff\xd8" + bytes(500)) == []
    assert s.feed(A) == [A]  # recovered after the drop


def test_bambu_url_and_beta_flag() -> None:
    assert bambu_rtsp_url("1.2.3.4", "a/b") == "rtsps://bblp:a%2Fb@1.2.3.4:322/streaming/live/1"
    assert BAMBU_RTSP_PORT == 322 and camera_rtsp.BETA is True


def test_ffmpeg_argv() -> None:
    argv = build_argv("rtsps://u:p@h:322/streaming/live/1", fps=5, tls_insecure=True)
    assert argv[0] == "ffmpeg"
    i = argv.index("-rtsp_transport")
    assert argv[i + 1] == "tcp"
    assert argv[argv.index("-tls_verify") + 1] == "0"
    assert argv[argv.index("-i") + 1] == "rtsps://u:p@h:322/streaming/live/1"
    assert argv[argv.index("-vf") + 1] == "fps=5"
    assert argv[-2:] == ["mjpeg", "-"]
    assert "-tls_verify" not in build_argv("http://x/y.mjpg")


def test_ffmpeg_exit_before_frame_raises() -> None:
    from app.printers.camera_a1 import CameraError

    src = FfmpegMjpegSource(["sh", "-c", "exit 3"])
    with pytest.raises(CameraError), src:
        list(src.frames())


def test_ffmpeg_child_output_split_and_close() -> None:
    src = FfmpegMjpegSource(["sh", "-c", f"printf '{_esc(A + B)}'; sleep 30"])
    got: list[bytes] = []
    with src:
        for f in src.frames():
            got.append(f)
            if len(got) == 2:
                threading.Thread(target=src.close).start()
    assert got == [A, B]


def _esc(b: bytes) -> str:
    return "".join(f"\\{c:03o}" for c in b)


MTX_CONF = """
logLevel: warn
rtmp: no
hls: no
webrtc: no
srt: no
rtspTransports: [tcp]
rtspEncryption: "strict"
rtspServerKey: /certs/key.pem
rtspServerCert: /certs/cert.pem
rtspAddress: ""
rtspsAddress: :8322
paths:
  streaming/live/1:
    runOnInit: >-
      ffmpeg -re -f lavfi -i testsrc=size=320x240:rate=10 -c:v libx264 -preset ultrafast
      -tune zerolatency -pix_fmt yuv420p -f rtsp rtsp://localhost:$RTSP_PORT/$MTX_PATH
    runOnInitRestart: yes
"""


@pytest.fixture(scope="module")
def mediamtx_port(tmp_path_factory):
    """mediamtx (ffmpeg flavour) publishing a test pattern over rtsps with a
    self-signed cert. Skips when Docker or a local ffmpeg is unavailable."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"testcontainers unavailable: {exc}")

    d = tmp_path_factory.mktemp("mtx")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    (d / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (d / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "mediamtx.yml").write_text(MTX_CONF)
    for f in d.iterdir():
        f.chmod(0o644)
    d.chmod(0o755)

    try:
        c = (
            DockerContainer("bluenviron/mediamtx:latest-ffmpeg")
            .with_exposed_ports(8322)
            .with_volume_mapping(str(d / "mediamtx.yml"), "/mediamtx.yml")
            .with_volume_mapping(str(d), "/certs")
        )
        c.start()
    except Exception as exc:
        pytest.skip(f"docker unavailable: {exc}")
    try:
        wait_for_logs(c, "listener opened", timeout=30)
        time.sleep(3)  # let runOnInit start publishing
        yield int(c.get_exposed_port(8322)), c.get_container_host_ip()
    finally:
        c.stop()


@pytest.mark.integration
def test_rtsps_testpattern_yields_jpeg_frames(mediamtx_port) -> None:
    port, host = mediamtx_port
    url = f"rtsps://bblp:code@{host}:{port}/streaming/live/1"
    src = FfmpegMjpegSource(build_argv(url, fps=5, tls_insecure=True), threading.Event())
    frames: list[bytes] = []
    with src:
        for f in src.frames():
            frames.append(f)
            if len(frames) >= 3:
                break
    assert len(frames) >= 3
    assert all(f[:2] == b"\xff\xd8" and f[-2:] == b"\xff\xd9" for f in frames)

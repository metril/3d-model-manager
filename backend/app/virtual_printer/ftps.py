"""Implicit-FTPS (TLS from byte 0, like a real Bambu printer) upload server.

Ported from upstream's ``ftp_server``: user ``bblp`` + access code, passive
mode only, ``STOR`` into ``upload_dir`` and nothing else. Every other file
command is answered with a harmless stub.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import random
import ssl
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger("virtual_printer.ftps")

MAX_UPLOAD_BYTES = 4 * 1024**3
MAX_LOGIN_ATTEMPTS = 3
UploadCallback = Callable[[Path, str], Awaitable[None] | None]


def _check_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        zf.namelist()


class FtpsServer:
    def __init__(
        self,
        *,
        upload_dir: Path,
        access_code: str,
        cert_path: str,
        key_path: str,
        bind: str,
        port: int,
        pasv_min: int = 0,
        pasv_max: int = 0,
        pasv_address: str = "",
        on_upload: UploadCallback | None = None,
    ):
        self.upload_dir, self.access_code = upload_dir, access_code
        self.cert_path, self.key_path = cert_path, key_path
        self.bind, self.port = bind, port
        self.pasv_min, self.pasv_max, self.pasv_address = pasv_min, pasv_max, pasv_address
        self.on_upload = on_upload
        self._server: asyncio.Server | None = None
        self._ctx: ssl.SSLContext | None = None
        self._sessions: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert_path, self.key_path)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("HIGH:AES256-GCM-SHA384:AES128-GCM-SHA256:!aNULL:!MD5:!RC4")
        self._ctx = ctx
        self._server = await asyncio.start_server(self._client, self.bind, self.port, ssl=ctx)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for t in list(self._sessions):
            t.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _client(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task:
            self._sessions.add(task)
        try:
            await _Session(self, r, w).run()
        finally:
            if task:
                self._sessions.discard(task)


class _Session:
    def __init__(self, srv: FtpsServer, r: asyncio.StreamReader, w: asyncio.StreamWriter):
        self.srv, self.r, self.w = srv, r, w
        peer = w.get_extra_info("peername")
        self.remote_ip = peer[0] if peer else "unknown"
        self.user: str | None = None
        self.authed = False
        self.failed_logins = 0
        self._data_server: asyncio.Server | None = None
        self._data_q: asyncio.Queue[tuple[asyncio.StreamReader, asyncio.StreamWriter]] | None = None

    async def send(self, code: int, msg: str) -> None:
        self.w.write(f"{code} {msg}\r\n".encode())
        await self.w.drain()

    async def run(self) -> None:
        try:
            await self.send(220, "tdmm virtual printer FTP ready")
            while True:
                line = await asyncio.wait_for(self.r.readline(), 300)
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                cmd, _, arg = text.partition(" ")
                handler = getattr(self, f"cmd_{cmd.upper()}", None)
                if handler is None:
                    await self.send(502, f"{cmd.upper()} not implemented")
                elif await handler(arg) == "quit":
                    break
        except (TimeoutError, ConnectionError, ssl.SSLError, OSError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("ftp session error")
        finally:
            await self._close_data()
            with contextlib.suppress(OSError):
                self.w.close()

    async def _need_auth(self) -> bool:
        if not self.authed:
            await self.send(530, "Not logged in")
            return True
        return False

    async def cmd_USER(self, arg: str) -> None:
        self.user = arg
        await (
            self.send(331, "Password required")
            if arg.lower() == "bblp"
            else self.send(530, "Invalid user")
        )

    async def cmd_PASS(self, arg: str) -> str | None:
        if (self.user or "").lower() != "bblp":
            await self.send(503, "Login with USER first")
        elif hmac.compare_digest(arg.encode(), self.srv.access_code.encode()):
            self.authed = True
            await self.send(230, "Login successful")
        else:
            self.failed_logins += 1
            await self.send(530, "Login incorrect")
            if self.failed_logins >= MAX_LOGIN_ATTEMPTS:
                return "quit"
        return None

    async def cmd_SYST(self, _a: str) -> None:
        await self.send(215, "UNIX Type: L8")

    async def cmd_FEAT(self, _a: str) -> None:
        self.w.write(b"211-Features:\r\n PASV\r\n EPSV\r\n UTF8\r\n SIZE\r\n211 End\r\n")
        await self.w.drain()

    async def cmd_OPTS(self, _a: str) -> None:
        await self.send(200, "OK")

    async def cmd_NOOP(self, _a: str) -> None:
        await self.send(200, "OK")

    async def cmd_PBSZ(self, _a: str) -> None:
        await self.send(200, "PBSZ=0")

    async def cmd_PROT(self, a: str) -> None:
        await (
            self.send(200, "Protection level Private")
            if a.upper() == "P"
            else self.send(536, "Only P")
        )

    async def cmd_QUIT(self, _a: str) -> str:
        await self.send(221, "Goodbye")
        return "quit"

    async def cmd_PWD(self, _a: str) -> None:
        if not await self._need_auth():
            await self.send(257, '"/" is current directory')

    async def cmd_CWD(self, _a: str) -> None:
        if not await self._need_auth():
            await self.send(250, "Directory changed")

    cmd_CDUP = cmd_CWD

    async def cmd_MKD(self, a: str) -> None:
        if not await self._need_auth():
            await self.send(257, f'"{a}" created')

    async def cmd_TYPE(self, _a: str) -> None:
        if not await self._need_auth():
            await self.send(200, "Type set")

    async def cmd_SIZE(self, _a: str) -> None:
        if not await self._need_auth():
            await self.send(550, "File not found")

    async def cmd_DELE(self, _a: str) -> None:
        if not await self._need_auth():
            await self.send(250, "Deleted")

    async def cmd_LIST(self, _a: str) -> None:
        if not await self._need_auth():
            await self.send(150, "Opening data connection")
            await self.send(226, "Transfer complete")

    cmd_NLST = cmd_LIST

    # -- passive data channel ----------------------------------------
    async def _open_data(self) -> int | None:
        await self._close_data()
        self._data_q = asyncio.Queue(maxsize=1)

        async def on_conn(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            assert self._data_q is not None
            if self._data_q.full():
                w.close()
                return
            self._data_q.put_nowait((r, w))
            await self._wait_closed(w)

        lo, hi = self.srv.pasv_min, self.srv.pasv_max
        for _ in range(10):
            port = random.randint(lo, hi) if lo and hi >= lo else 0  # noqa: S311
            try:
                self._data_server = await asyncio.start_server(
                    on_conn, self.srv.bind, port, ssl=self.srv._ctx
                )
                return self._data_server.sockets[0].getsockname()[1]
            except OSError:
                continue
        return None

    @staticmethod
    async def _wait_closed(w: asyncio.StreamWriter) -> None:
        with contextlib.suppress(OSError, ssl.SSLError):
            await w.wait_closed()

    async def _close_data(self) -> None:
        if self._data_server:
            self._data_server.close()
            self._data_server = None
        self._data_q = None

    async def cmd_EPSV(self, _a: str) -> None:
        if await self._need_auth():
            return
        port = await self._open_data()
        await (
            self.send(229, f"Entering Extended Passive Mode (|||{port}|)")
            if port
            else self.send(425, "No data port")
        )

    async def cmd_PASV(self, _a: str) -> None:
        if await self._need_auth():
            return
        port = await self._open_data()
        if not port:
            await self.send(425, "No data port")
            return
        sock = self.w.get_extra_info("sockname")
        ip = self.srv.pasv_address or (sock[0] if sock else "127.0.0.1")
        if ip == "0.0.0.0":  # noqa: S104
            ip = "127.0.0.1"
        await self.send(
            227, f"Entering Passive Mode ({ip.replace('.', ',')},{port >> 8},{port & 255})"
        )

    async def cmd_STOR(self, arg: str) -> None:
        if await self._need_auth():
            return
        if self._data_q is None:
            await self.send(425, "Use PASV first")
            return
        name = Path(arg).name
        if not name:
            await self.send(553, "Bad file name")
            return
        dest = self.srv.upload_dir / name
        await self.send(150, f"Opening data connection for {name}")
        try:
            dr, dw = await asyncio.wait_for(self._data_q.get(), 30)
        except TimeoutError:
            await self._close_data()
            await self.send(425, "Data connection timeout")
            return
        total, failure = 0, None
        try:
            fh = await asyncio.to_thread(dest.open, "wb")
            try:
                while chunk := await asyncio.wait_for(dr.read(65536), 60):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise OSError("upload too large")
                    await asyncio.to_thread(fh.write, chunk)
            finally:
                await asyncio.to_thread(fh.close)
        except (OSError, TimeoutError, ssl.SSLError) as exc:
            failure = exc
        finally:
            with contextlib.suppress(OSError):
                dw.close()
            await self._close_data()
        if failure is None and name.lower().endswith(".3mf"):
            try:
                await asyncio.to_thread(_check_zip, dest)
            except (zipfile.BadZipFile, OSError) as exc:
                failure = exc
        if failure is not None:
            await asyncio.to_thread(dest.unlink, missing_ok=True)
            await self.send(426, "Transfer failed")
            return
        await self.send(226, "Transfer complete")
        if self.srv.on_upload:
            try:
                res = self.srv.on_upload(dest, self.remote_ip)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                log.exception("upload callback failed")

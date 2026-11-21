"""Unix-socket bridge. Closing a client connection cancels and reaps its worker."""

import asyncio
import json
from pathlib import Path

from app.media.worker import run_worker


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    tasks: list[asyncio.Task] = []
    try:
        async with asyncio.timeout(5):
            kind, args = json.loads(await reader.readline())
        if kind not in {"video", "audio", "gallery", "spotify", "probe"}:
            raise ValueError("unknown operation")
        job = asyncio.create_task(run_worker(kind, *args))
        disconnected = asyncio.create_task(reader.read(1))
        tasks.extend((job, disconnected))
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if disconnected.done():
            return
        payload = {"result": job.result()}
    except Exception as exc:
        payload = {"error": str(exc)[:500]}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if "payload" in locals():
            writer.write(json.dumps(payload).encode() + b"\n")
        writer.close()
        await writer.wait_closed()


async def main() -> None:
    path = Path("/run/mewvia/media.sock")
    await asyncio.to_thread(path.unlink, missing_ok=True)
    async with await asyncio.start_unix_server(handle, path) as server:
        await asyncio.to_thread(path.chmod, 0o600)
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())

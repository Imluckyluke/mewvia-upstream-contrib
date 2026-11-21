"""Limit actual Telegram input bytes independently of Bot API transfer limits."""

import io
from pathlib import Path
from typing import BinaryIO, cast

from aiogram import Bot

from app.config import get_settings
from app.core.errors import FileTooLargeError


class MediaBot(Bot):
    async def download_file(
        self,
        file_path: str | Path,
        destination: BinaryIO | Path | str | None = None,
        timeout: int = 30,  # noqa: ASYNC109
        chunk_size: int = 65536,
        seek: bool = True,
    ) -> BinaryIO | None:
        limit = get_settings().media_max_bytes
        count = 0

        class Sink:
            def write(self, block: bytes) -> int:
                nonlocal count
                count += len(block)
                if count > limit:
                    raise FileTooLargeError()
                return stream.write(block)

            def flush(self) -> None:
                stream.flush()

        path = Path(destination) if isinstance(destination, (str, Path)) else None
        stream: BinaryIO
        if path:
            stream = path.open("wb")
        elif destination is None or isinstance(destination, (str, Path)):
            stream = io.BytesIO()
        else:
            stream = destination
        try:
            await super().download_file(
                file_path, cast(BinaryIO, Sink()), timeout, chunk_size, seek=False
            )
            if seek and not path:
                stream.seek(0)
            return None if path else stream
        except BaseException:
            if path:
                path.unlink(missing_ok=True)
            raise
        finally:
            if path:
                stream.close()

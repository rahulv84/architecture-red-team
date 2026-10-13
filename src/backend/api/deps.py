"""FastAPI dependencies."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, UploadFile

from ..core.exceptions import FileTooLargeError
from ..core.lifecycle import AppContainer, get_container


def container_dep() -> AppContainer:
    return get_container()


Container = Annotated[AppContainer, Depends(container_dep)]


async def read_upload(file: UploadFile, limit_bytes: int) -> bytes:
    """Read an upload with a hard size bound (never trusts Content-Length)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit_bytes:
            raise FileTooLargeError(f"'{file.filename}' exceeds the {limit_bytes // (1024 * 1024)} MB upload limit.")
        chunks.append(chunk)
    return b"".join(chunks)



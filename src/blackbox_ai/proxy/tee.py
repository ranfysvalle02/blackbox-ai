"""A streaming tee.

Yields every chunk straight through to the client while mirroring a copy to an
``observe`` callback. The callback must be cheap and non-raising (it only ever
appends bytes to a bounded buffer) so that telemetry capture can never slow down
or break the response the client is reading.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

__all__ = ["tee_stream"]


async def tee_stream(
    source: AsyncIterator[bytes],
    observe: Callable[[bytes], None],
) -> AsyncIterator[bytes]:
    """Pass ``source`` chunks through, mirroring each to ``observe``."""
    async for chunk in source:
        observe(chunk)
        yield chunk

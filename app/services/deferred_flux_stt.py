"""Deepgram Flux STT that connects in the background, so the greeting isn't blocked.

Root cause (verified in the installed package): ``DeepgramFluxSTTBase.start()``
ends with ``await self._connect()``, and ``STTService.process_frame`` only pushes
``StartFrame`` downstream *after* ``start()`` returns. So the opening greeting —
queued behind ``StartFrame`` — waits ~1s for the Flux websocket to connect even
though STT isn't needed while the bot greets.

This subclass runs the normal base ``start()`` setup but kicks the connect off as
a background task, letting ``StartFrame`` (and the greeting) flow straight to TTS
while Flux connects. The user is muted through the greeting, and ``run_stt``
already drops audio safely if any arrives before the socket is open, so nothing
is lost. Selected via the ``stt.defer_connect_on_start`` runtime-config flag.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pipecat.frames.frames import ErrorFrame, StartFrame
from pipecat.services.deepgram.flux.base import DeepgramFluxSTTBase
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService


class DeferredConnectDeepgramFluxSTTService(DeepgramFluxSTTService):
    """A Flux STT service whose websocket connects in the background on start."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._deferred_connect_task: Any | None = None

    async def start(self, frame: StartFrame) -> None:
        # Invoke DeepgramFluxSTTBase.start's *setup* without its trailing blocking
        # ``await self._connect()`` by calling its super() directly, then connect
        # in the background so StartFrame propagation is not blocked.
        await super(DeepgramFluxSTTBase, self).start(frame)
        self._deferred_connect_task = self.create_task(self._background_connect())

    async def _background_connect(self) -> None:
        try:
            await self._connect()
            logger.info("Deferred Flux STT connect completed (ready for audio)")
        except Exception as exc:  # surface clearly; the call can still degrade gracefully
            logger.error("Deferred Flux STT connect failed: {err}", err=str(exc))
            await self.push_error_frame(ErrorFrame(f"Deepgram Flux STT connect failed: {exc}"))

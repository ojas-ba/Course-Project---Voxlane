"""Validated browser signaling and session responses."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class OfferRequest(BaseModel):
    """SDK offer, optionally renegotiating the currently active peer."""

    sdp: str = Field(min_length=1, max_length=100000)
    type: Literal["offer"]
    pc_id: str | None = Field(default=None, max_length=100)
    restart_pc: bool = False
    request_data: Any = None


class IceCandidate(BaseModel):
    """Wire format emitted by the SmallWebRTC JavaScript transport."""

    candidate: str = Field(min_length=1, max_length=4096)
    sdp_mid: str | None = None
    sdp_mline_index: int | None = Field(default=None, ge=0)


class CandidateRequest(BaseModel):
    """Additional connection candidates belonging to a known peer."""

    pc_id: str = Field(min_length=1, max_length=100)
    candidates: list[IceCandidate] = Field(max_length=100)


class EndResult(BaseModel):
    """Cleanup result, independent of whether the media channel still exists."""

    call_sid: str
    saved: bool
    history_url: str | None = None
    error: str | None = None

"""Exotel outbound calling client using httpx.AsyncClient.

Handles triggering outbound telephone calls via Exotel's v1 REST API
with HTTP Basic Authentication and form-encoded payloads.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx
from loguru import logger

from app.config import Settings


@dataclass(frozen=True)
class ExotelCallResult:
    """Typed result from an Exotel outbound call attempt."""

    success: bool
    call_sid: str | None = None
    error: str | None = None


class ExotelOutboundClient:
    """Async Exotel outbound calling client.

    Uses httpx.AsyncClient with HTTP Basic Auth to POST form-encoded
    data to Exotel's v1 Calls/connect endpoint. Designed to be created
    once at application startup and closed at shutdown via FastAPI lifespan.
    """

    _PLACEHOLDER_PREFIXES = ("your_", "placeholder_")

    def __init__(self, settings: Settings) -> None:
        required_fields = {
            "exotel_account_sid": settings.exotel_account_sid,
            "exotel_api_key": settings.exotel_api_key,
            "exotel_api_token": settings.exotel_api_token,
            "exotel_virtual_number": settings.exotel_virtual_number,
            "exotel_flow_id": settings.exotel_flow_id,
        }
        missing = [
            k for k, v in required_fields.items()
            if not v or v.lower().startswith(self._PLACEHOLDER_PREFIXES)
        ]
        if missing:
            raise ValueError(
                f"Missing required Exotel outbound config: {', '.join(missing)}"
            )

        self._account_sid = settings.exotel_account_sid
        self._virtual_number = settings.exotel_virtual_number
        self._flow_id = settings.exotel_flow_id
        self._subdomain = settings.exotel_subdomain

        self._connect_url = (
            f"https://{self._subdomain}"
            f"/v1/Accounts/{self._account_sid}/Calls/connect"
        )

        # Derive dashboard domain from API subdomain:
        # api.in.exotel.com (Mumbai) → my.in.exotel.com
        # api.exotel.com (Singapore) → my.exotel.com
        dashboard_domain = self._subdomain.replace("api", "my", 1)
        self._flow_url = (
            f"https://{dashboard_domain}/{self._account_sid}"
            f"/exoml/start_voice/{self._flow_id}"
        )

        self._client = httpx.AsyncClient(
            auth=httpx.BasicAuth(
                username=settings.exotel_api_key,
                password=settings.exotel_api_token,
            ),
            timeout=httpx.Timeout(30.0),
        )

        logger.info(
            "ExotelOutboundClient initialized",
            connect_url=self._connect_url,
            flow_url=self._flow_url,
        )

    @staticmethod
    def _format_number_for_v1(number: str) -> str:
        """Ensure phone number has 0-prefix for Exotel v1 API.

        Strips any +91 prefix and adds leading 0 if not present.

        Examples:
            "+919876543210" → "09876543210"
            "919876543210"  → "09876543210"
            "9876543210"    → "09876543210"
            "09876543210"   → "09876543210"
        """
        cleaned = number.strip().lstrip("+")
        if cleaned.startswith("91") and len(cleaned) == 12:
            cleaned = cleaned[2:]
        if not cleaned.startswith("0"):
            cleaned = "0" + cleaned
        return cleaned

    async def make_call(self, to_number: str) -> ExotelCallResult:
        """Trigger an outbound call to the given phone number.

        Args:
            to_number: The customer's phone number. Accepts any of:
                       +919876543210, 919876543210, 9876543210, 09876543210.

        Returns:
            ExotelCallResult with success status, call_sid, or error message.
        """
        formatted_number = self._format_number_for_v1(to_number)

        form_data = {
            "From": formatted_number,
            "CallerId": self._virtual_number,
            "Url": self._flow_url,
            "CallType": "trans",
        }

        log = logger.bind(to_number=formatted_number)
        log.info("Initiating outbound call", connect_url=self._connect_url)

        try:
            response = await self._client.post(
                self._connect_url,
                data=form_data,
            )
            response.raise_for_status()

            # Exotel v1 returns XML — extract CallSid
            call_sid = self._parse_call_sid(response.text)
            if not call_sid:
                error_msg = "Exotel response did not include a CallSid"
                log.error(
                    "Exotel call response missing CallSid",
                    response_body=response.text[:500],
                )
                return ExotelCallResult(success=False, error=error_msg)

            log.info("Outbound call initiated successfully", call_sid=call_sid)
            return ExotelCallResult(success=True, call_sid=call_sid)

        except httpx.HTTPStatusError as exc:
            error_msg = (
                f"Exotel API error {exc.response.status_code}: "
                f"{exc.response.text}"
            )
            log.error(
                "Exotel API returned error",
                status_code=exc.response.status_code,
                response_body=exc.response.text[:500],
            )
            return ExotelCallResult(success=False, error=error_msg)

        except httpx.RequestError as exc:
            error_msg = f"Exotel request failed: {exc}"
            log.error("Exotel request failed", error=str(exc))
            return ExotelCallResult(success=False, error=error_msg)

    @staticmethod
    def _parse_call_sid(xml_text: str) -> str | None:
        """Extract the Call Sid from Exotel's XML response.

        Expected shape:
            <TwilioResponse>
                <Call>
                    <Sid>call_sid_value</Sid>
                    ...
                </Call>
            </TwilioResponse>
        """
        try:
            root = ET.fromstring(xml_text)
            sid_elem = root.find(".//Sid")
            if sid_elem is not None and sid_elem.text:
                return sid_elem.text.strip()
            return None
        except ET.ParseError:
            logger.warning(
                "Failed to parse Exotel XML response", raw_response=xml_text
            )
            return None

    async def close(self) -> None:
        """Close the underlying httpx client and release connections."""
        await self._client.aclose()
        logger.info("ExotelOutboundClient closed")

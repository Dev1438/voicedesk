import logging
import mimetypes
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from django.conf import settings


logger = logging.getLogger(__name__)

TRANSCRIPTION_URL = "https://api.audiclabs.com/v1/stt/transcriptions"
TRANSCRIPTION_MODEL = "audic-stt-1"
TRANSCRIPTION_TIMEOUT = (5, 600)


class TranscriptionError(Exception):
    pass


def _retry_after_seconds(value):
    if not value:
        return None

    try:
        return max(0, int(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None

        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _send_transcription(audio_file, filename, content_type, headers):
    return requests.post(
        TRANSCRIPTION_URL,
        headers=headers,
        data={"model": TRANSCRIPTION_MODEL},
        files={"file": (filename, audio_file, content_type)},
        timeout=TRANSCRIPTION_TIMEOUT,
    )


def _provider_error(response):
    try:
        payload = response.json()
    except ValueError:
        return "non_json_error"

    if not isinstance(payload, dict):
        return "invalid_error_response"

    error = payload.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        if isinstance(error_type, str):
            return error_type[:200]

    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail[:200]

    return "unspecified_error"


def _validate_response(response):
    request_id = response.headers.get("X-Request-Id")
    logger.info(
        "AudicLabs STT response status=%s request_id=%s",
        response.status_code,
        request_id or "unavailable",
    )

    if not response.ok:
        provider_error = _provider_error(response)
        retry_after = _retry_after_seconds(
            response.headers.get("Retry-After")
        )
        logger.error(
            "AudicLabs STT failed status=%s request_id=%s provider_error=%s",
            response.status_code,
            request_id or "unavailable",
            provider_error,
        )
        message = (
            f"AudicLabs transcription failed (HTTP {response.status_code}: "
            f"{provider_error}"
        )
        if request_id:
            message += f", request ID {request_id}"
        if retry_after is not None:
            message += f", retry after {retry_after:g} seconds"
        raise TranscriptionError(message)

    try:
        payload = response.json()
    except ValueError as error:
        logger.error(
            "AudicLabs STT returned invalid JSON request_id=%s",
            request_id or "unavailable",
        )
        raise TranscriptionError(
            "AudicLabs returned an invalid transcription response."
        ) from error

    transcript = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(transcript, str):
        logger.error(
            "AudicLabs STT response had no text field request_id=%s",
            request_id or "unavailable",
        )
        raise TranscriptionError(
            "AudicLabs response did not contain a transcript."
        )

    return transcript


def transcribe_complaint(complaint):
    api_key = settings.AUDICLABS_API_KEY
    if not api_key:
        raise TranscriptionError(
            "AudicLabs is not configured. Set AUDICLABS_API_KEY in the root .env."
        )

    headers = {"Authorization": f"Bearer {api_key}"}
    filename = os.path.basename(complaint.audio.name)
    content_type = (
        mimetypes.guess_type(filename)[0] or "application/octet-stream"
    )

    try:
        with complaint.audio.open("rb") as audio_file:
            try:
                response = _send_transcription(
                    audio_file,
                    filename,
                    content_type,
                    headers,
                )
            except requests.RequestException as error:
                logger.error(
                    "AudicLabs STT request failed error_type=%s",
                    type(error).__name__,
                )
                raise TranscriptionError(
                    "Could not reach AudicLabs to transcribe the recording."
                ) from error

            if response.status_code in (429, 503):
                retry_after = _retry_after_seconds(
                    response.headers.get("Retry-After")
                )
                if retry_after is not None:
                    request_id = response.headers.get("X-Request-Id")
                    logger.warning(
                        "AudicLabs STT retry scheduled after %s seconds "
                        "request_id=%s",
                        retry_after,
                        request_id or "unavailable",
                    )
                    time.sleep(retry_after)
                    audio_file.seek(0)
                    try:
                        response = _send_transcription(
                            audio_file,
                            filename,
                            content_type,
                            headers,
                        )
                    except requests.RequestException as error:
                        logger.error(
                            "AudicLabs STT retry failed error_type=%s "
                            "request_id=%s",
                            type(error).__name__,
                            request_id or "unavailable",
                        )
                        raise TranscriptionError(
                            "Could not reach AudicLabs to transcribe the recording."
                        ) from error
    except OSError as error:
        logger.error(
            "Saved complaint audio could not be opened complaint_id=%s "
            "error_type=%s",
            complaint.pk,
            type(error).__name__,
        )
        raise TranscriptionError(
            "The saved complaint recording could not be opened for transcription."
        ) from error

    return _validate_response(response)

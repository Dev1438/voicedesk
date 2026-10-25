import errno
import logging
import mimetypes
import os
import socket
import time
from io import BytesIO

import requests
from django.conf import settings

from .audiclabs_utils import retry_after_seconds


logger = logging.getLogger(__name__)

TRANSCRIPTION_URL = "https://api.audiclabs.com/v1/stt/transcriptions"
TRANSCRIPTION_MODEL = "audic-stt-1"
TRANSCRIPTION_TIMEOUT = (5, 600)


class TranscriptionError(Exception):
    pass


def _exception_chain(error):
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current

        for nested in (
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
            getattr(current, "reason", None),
            getattr(current, "original_error", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
        pending.extend(
            argument
            for argument in current.args
            if isinstance(argument, BaseException)
        )


def _request_failure_details(error):
    exceptions = list(_exception_chain(error))
    type_names = ",".join(type(item).__name__ for item in exceptions)

    if any(
        isinstance(item, requests.exceptions.ProxyError)
        for item in exceptions
    ):
        failure_kind = "proxy_error"
    elif any(
        isinstance(item, (socket.gaierror, socket.herror))
        for item in exceptions
    ):
        failure_kind = "dns_resolution_error"
    elif any(
        isinstance(item, (ConnectionResetError, BrokenPipeError))
        or getattr(item, "errno", None)
        in (errno.ECONNRESET, errno.EPIPE)
        for item in exceptions
    ):
        failure_kind = "connection_reset"
    elif any(
        isinstance(item, (requests.Timeout, socket.timeout, TimeoutError))
        for item in exceptions
    ):
        failure_kind = "timeout"
    elif any(
        getattr(item, "errno", None) == errno.ECONNREFUSED
        for item in exceptions
    ):
        failure_kind = "connection_refused"
    else:
        failure_kind = "connection_error"

    return {
        "error_type": type(error).__name__,
        "failure_kind": failure_kind,
        "reason_types": type_names or type(error).__name__,
    }


def _send_transcription(audio_file, filename, content_type, headers):
    return requests.post(
        TRANSCRIPTION_URL,
        headers=headers,
        data={"model": TRANSCRIPTION_MODEL},
        files={"file": (filename, audio_file, content_type)},
        timeout=TRANSCRIPTION_TIMEOUT,
    )


def _provider_error_category(status_code):
    return {
        400: "invalid_request",
        401: "authentication_failed",
        402: "billing_or_credit_issue",
        403: "access_denied",
        404: "endpoint_unavailable",
        413: "audio_too_large",
        415: "unsupported_audio_format",
        422: "unsupported_audio_or_model",
        429: "rate_limited",
        500: "provider_error",
        502: "provider_unavailable",
        503: "provider_unavailable",
        504: "provider_timeout",
    }.get(status_code, "provider_error")


def _customer_error_description(category):
    return {
        "invalid_request": "check the submitted recording and try again",
        "authentication_failed": "service authentication failed",
        "billing_or_credit_issue": "the transcription service is unavailable",
        "access_denied": "the transcription service is unavailable",
        "endpoint_unavailable": "the transcription service is unavailable",
        "audio_too_large": "use a shorter or smaller recording",
        "unsupported_audio_format": "try recording again in a supported format",
        "unsupported_audio_or_model": (
            "check the recording format and try again"
        ),
        "rate_limited": "wait before submitting the recording again",
        "provider_unavailable": "try again later",
        "provider_timeout": "try again later",
        "provider_error": "try again later",
    }.get(category, "try again later")


def _validate_response(response):
    request_id = response.headers.get("X-Request-Id")
    logger.info(
        "AudicLabs STT response status=%s request_id=%s",
        response.status_code,
        request_id or "unavailable",
    )

    if not response.ok:
        error_category = _provider_error_category(response.status_code)
        retry_after = retry_after_seconds(
            response.headers.get("Retry-After")
        )
        logger.error(
            "AudicLabs STT failed status=%s request_id=%s "
            "error_category=%s",
            response.status_code,
            request_id or "unavailable",
            error_category,
        )
        message = (
            f"AudicLabs transcription failed (HTTP {response.status_code}; "
            f"{_customer_error_description(error_category)}"
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


def transcribe_complaint(complaint, audio_bytes=None):
    api_key = settings.AUDICLABS_API_KEY
    if not api_key:
        raise TranscriptionError(
            "AudicLabs is not configured. Set AUDICLABS_API_KEY in the root .env."
        )

    headers = {"Authorization": f"Bearer {api_key}"}
    filename = (
        "complaint.wav"
        if audio_bytes is not None
        else os.path.basename(complaint.audio.name)
    )
    content_type = (
        "audio/wav"
        if audio_bytes is not None
        else mimetypes.guess_type(filename)[0] or "application/octet-stream"
    )

    try:
        audio_source = (
            BytesIO(audio_bytes)
            if audio_bytes is not None
            else complaint.audio.open("rb")
        )
        with audio_source as audio_file:
            try:
                response = _send_transcription(
                    audio_file,
                    filename,
                    content_type,
                    headers,
                )
            except requests.RequestException as error:
                details = _request_failure_details(error)
                logger.error(
                    "AudicLabs STT request failed error_type=%s "
                    "failure_kind=%s reason_types=%s",
                    details["error_type"],
                    details["failure_kind"],
                    details["reason_types"],
                )
                raise TranscriptionError(
                    "Could not reach AudicLabs to transcribe the recording."
                ) from error

            if response.status_code in (429, 503):
                retry_after = retry_after_seconds(
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
                        details = _request_failure_details(error)
                        logger.error(
                            "AudicLabs STT retry failed error_type=%s "
                            "failure_kind=%s reason_types=%s request_id=%s",
                            details["error_type"],
                            details["failure_kind"],
                            details["reason_types"],
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

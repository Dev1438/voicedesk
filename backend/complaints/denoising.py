import io
import logging
import mimetypes
import os
import time
import wave
from dataclasses import dataclass

import requests
from django.conf import settings

from .audiclabs_utils import retry_after_seconds


logger = logging.getLogger(__name__)

DENOISING_URL = "https://api.audiclabs.com/v1/noise-suppressor/denoise"
DENOISING_TIMEOUT = (5, 600)
RETRY_AFTER_MAX_SECONDS = 30
SUPPORTED_DENOISING_MODELS = {
    "audic-1",
    "audic-2",
    "audic-3",
    "audic-4",
    "audic-5",
}


class DenoisingError(Exception):
    pass


@dataclass(frozen=True)
class DenoisingResult:
    audio_bytes: bytes


def _send_denoising(audio_file, filename, content_type, headers):
    return requests.post(
        DENOISING_URL,
        headers=headers,
        data={"model": settings.AUDICLABS_DENOISE_MODEL},
        files={"file": (filename, audio_file, content_type)},
        timeout=DENOISING_TIMEOUT,
    )


def _validate_wav(audio_bytes, request_id):
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as cleaned_audio:
            channels = cleaned_audio.getnchannels()
            sample_width = cleaned_audio.getsampwidth()
            frame_rate = cleaned_audio.getframerate()
            frame_count = cleaned_audio.getnframes()
            if (
                channels < 1
                or sample_width < 1
                or frame_rate < 1
                or frame_count < 1
            ):
                raise wave.Error("empty audio")
            frames = cleaned_audio.readframes(frame_count)
            expected_size = frame_count * channels * sample_width
            if len(frames) != expected_size:
                raise wave.Error("incomplete audio frames")
    except (wave.Error, EOFError, OSError) as error:
        logger.error(
            "AudicLabs denoising returned invalid WAV request_id=%s",
            request_id or "unavailable",
        )
        raise DenoisingError(
            "AudicLabs returned invalid cleaned audio."
        ) from error


def _response_error(response):
    request_id = response.headers.get("X-Request-Id")
    status_details = {
        401: "authentication was rejected",
        402: "the account has a billing or credit issue",
        403: "the account is not permitted to use this service",
        413: "the audio file is too large",
        422: "the audio or selected model is unsupported",
        429: "the service rate limit was reached",
        503: "the service is temporarily unavailable",
    }
    detail = status_details.get(response.status_code, "the request failed")
    retry_after = retry_after_seconds(response.headers.get("Retry-After"))

    logger.error(
        "AudicLabs denoising failed status=%s request_id=%s",
        response.status_code,
        request_id or "unavailable",
    )
    message = (
        f"AudicLabs denoising failed (HTTP {response.status_code}: {detail}"
    )
    if request_id:
        message += f", request ID {request_id}"
    if retry_after is not None:
        message += f", retry after {retry_after:g} seconds"
    return DenoisingError(message)


def _log_response(response):
    logger.info(
        "AudicLabs denoising response status=%s request_id=%s "
        "stats_present=%s",
        response.status_code,
        response.headers.get("X-Request-Id") or "unavailable",
        bool(response.headers.get("X-NS-Stats")),
    )


def denoise_complaint_audio(complaint):
    api_key = settings.AUDICLABS_API_KEY
    if not api_key:
        raise DenoisingError(
            "AudicLabs is not configured. Set AUDICLABS_API_KEY in the root .env."
        )

    model = settings.AUDICLABS_DENOISE_MODEL
    if model not in SUPPORTED_DENOISING_MODELS:
        raise DenoisingError(
            "AudicLabs denoising model must be one of audic-1 through audic-5."
        )

    headers = {"Authorization": f"Bearer {api_key}"}
    filename = os.path.basename(complaint.audio.name)
    content_type = (
        mimetypes.guess_type(filename)[0] or "application/octet-stream"
    )

    try:
        with complaint.audio.open("rb") as audio_file:
            try:
                response = _send_denoising(
                    audio_file,
                    filename,
                    content_type,
                    headers,
                )
            except requests.RequestException as error:
                logger.error(
                    "AudicLabs denoising request failed error_type=%s",
                    type(error).__name__,
                )
                raise DenoisingError(
                    "Could not reach AudicLabs to denoise the recording."
                ) from error

            _log_response(response)
            if response.status_code in (429, 503):
                retry_after = retry_after_seconds(
                    response.headers.get("Retry-After")
                )
                if retry_after is not None:
                    request_id = response.headers.get("X-Request-Id")
                    capped_retry_after = min(
                        retry_after,
                        RETRY_AFTER_MAX_SECONDS,
                    )
                    if capped_retry_after < retry_after:
                        logger.warning(
                            "AudicLabs denoising Retry-After capped from "
                            "%s to %s seconds request_id=%s",
                            retry_after,
                            capped_retry_after,
                            request_id or "unavailable",
                        )
                    logger.warning(
                        "AudicLabs denoising retry scheduled after %s "
                        "seconds request_id=%s",
                        capped_retry_after,
                        request_id or "unavailable",
                    )
                    time.sleep(capped_retry_after)
                    audio_file.seek(0)
                    try:
                        response = _send_denoising(
                            audio_file,
                            filename,
                            content_type,
                            headers,
                        )
                    except requests.RequestException as error:
                        logger.error(
                            "AudicLabs denoising retry failed "
                            "error_type=%s request_id=%s",
                            type(error).__name__,
                            request_id or "unavailable",
                        )
                        raise DenoisingError(
                            "Could not reach AudicLabs to denoise the recording."
                        ) from error
                    _log_response(response)
    except OSError as error:
        logger.error(
            "Saved complaint audio could not be opened for denoising "
            "complaint_id=%s error_type=%s",
            complaint.pk,
            type(error).__name__,
        )
        raise DenoisingError(
            "The saved complaint recording could not be opened for denoising."
        ) from error

    if not response.ok:
        raise _response_error(response)

    cleaned_audio = response.content
    if not isinstance(cleaned_audio, bytes) or not cleaned_audio:
        logger.error(
            "AudicLabs denoising returned empty audio request_id=%s",
            response.headers.get("X-Request-Id") or "unavailable",
        )
        raise DenoisingError(
            "AudicLabs returned empty cleaned audio."
        )

    request_id = response.headers.get("X-Request-Id")
    _validate_wav(cleaned_audio, request_id)
    return DenoisingResult(audio_bytes=cleaned_audio)

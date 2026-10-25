import logging

import requests
from django.conf import settings


logger = logging.getLogger(__name__)

TTS_URL = "https://api.audiclabs.com/v1/tts/speech"
TTS_TIMEOUT = (5, 120)


class TTSError(Exception):
    pass


def _is_mp3(audio_bytes):
    return audio_bytes.startswith(b"ID3") or (
        len(audio_bytes) >= 2
        and audio_bytes[0] == 0xFF
        and audio_bytes[1] & 0xE0 == 0xE0
    )


def synthesize_reply(text, customer_language):
    api_key = settings.AUDICLABS_API_KEY
    voice = settings.AUDICLABS_TTS_VOICE.strip()
    if not api_key:
        raise TTSError("AudicLabs text-to-speech is not configured.")
    if not voice:
        raise TTSError("An AudicLabs text-to-speech voice is not configured.")

    language = (
        settings.AUDICLABS_TTS_LANGUAGE.strip()
        or customer_language
    )
    payload = {
        "model": settings.AUDICLABS_TTS_MODEL,
        "input": text,
        "voice": voice,
        "language": language,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = requests.post(
            TTS_URL,
            headers=headers,
            json=payload,
            timeout=TTS_TIMEOUT,
        )
    except requests.Timeout as error:
        logger.error(
            "AudicLabs TTS request failed error_type=%s category=timeout",
            type(error).__name__,
        )
        raise TTSError(
            "AudicLabs text-to-speech timed out."
        ) from error
    except requests.RequestException as error:
        logger.error(
            "AudicLabs TTS request failed error_type=%s "
            "category=connection_error",
            type(error).__name__,
        )
        raise TTSError(
            "Could not reach AudicLabs text-to-speech."
        ) from error

    request_id = response.headers.get("X-Request-Id") or "unavailable"
    logger.info(
        "AudicLabs TTS response status=%s request_id=%s",
        response.status_code,
        request_id,
    )

    if not response.ok:
        error_categories = {
            401: "authentication_failed",
            402: "billing_or_credit_issue",
            403: "access_denied",
            429: "rate_limited",
            503: "provider_unavailable",
        }
        category = error_categories.get(response.status_code, "provider_error")
        logger.error(
            "AudicLabs TTS failed status=%s request_id=%s "
            "error_category=%s",
            response.status_code,
            request_id,
            category,
        )
        raise TTSError(
            "AudicLabs text-to-speech failed "
            f"(HTTP {response.status_code}; {category})."
        )

    audio_bytes = response.content
    if not isinstance(audio_bytes, bytes) or not _is_mp3(audio_bytes):
        logger.error(
            "AudicLabs TTS returned invalid MP3 request_id=%s",
            request_id,
        )
        raise TTSError(
            "AudicLabs returned invalid audio instead of an MP3 file."
        )

    return audio_bytes

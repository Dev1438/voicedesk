from google.api_core.exceptions import (
    DeadlineExceeded,
    FailedPrecondition,
    GoogleAPICallError,
    PermissionDenied,
    ResourceExhausted,
    Unauthenticated,
)
from google.auth.exceptions import GoogleAuthError
from google.cloud import translate_v3
from django.conf import settings

from .translation import TranslationError, TranslationUnavailable


SOURCE_LANGUAGE_CODES = {
    "te": "te",
    "hi": "hi",
    "en": "en",
    "ta": "ta",
    "kn": "kn",
}
TRANSLATION_TIMEOUT_SECONDS = 15


def _validate_text(text):
    if not isinstance(text, str) or not text.strip():
        raise TranslationError("There is no transcript to translate.")

    return text.strip()


def _translate(text, source_language_code, target_language_code):
    project_id = settings.GOOGLE_CLOUD_PROJECT.strip()
    if not project_id:
        raise TranslationUnavailable(
            "Google Cloud Translation project is not configured."
        )

    try:
        client = translate_v3.TranslationServiceClient()
    except GoogleAuthError as error:
        raise TranslationUnavailable(
            "Google Cloud Application Default Credentials are unavailable."
        ) from error

    try:
        response = client.translate_text(
            request={
                "parent": (
                    f"projects/{project_id}/locations/"
                    f"{settings.GOOGLE_CLOUD_LOCATION}"
                ),
                "contents": [text],
                "mime_type": "text/plain",
                "source_language_code": source_language_code,
                "target_language_code": target_language_code,
            },
            timeout=TRANSLATION_TIMEOUT_SECONDS,
        )
    except GoogleAuthError as error:
        raise TranslationUnavailable(
            "Google Cloud Application Default Credentials are unavailable."
        ) from error
    except Unauthenticated as error:
        raise TranslationError(
            "Google Cloud Translation authentication failed."
        ) from error
    except PermissionDenied as error:
        raise TranslationError(
            "Google Cloud Translation access was denied. Check the account's "
            "API permissions."
        ) from error
    except ResourceExhausted as error:
        raise TranslationError(
            "Google Cloud Translation quota or billing limits were reached."
        ) from error
    except FailedPrecondition as error:
        raise TranslationError(
            "Google Cloud Translation API or billing is not enabled for "
            "this project."
        ) from error
    except DeadlineExceeded as error:
        raise TranslationError(
            "Google Cloud Translation timed out. Please try again."
        ) from error
    except GoogleAPICallError as error:
        raise TranslationError(
            "Google Cloud Translation could not complete the request."
        ) from error

    translations = getattr(response, "translations", None)
    if not translations:
        raise TranslationError(
            "Google Cloud Translation returned no translated text."
        )

    translated_text = getattr(translations[0], "translated_text", "")
    if not isinstance(translated_text, str) or not translated_text.strip():
        raise TranslationError(
            "Google Cloud Translation returned no translated text."
        )

    return translated_text.strip()


def translate_to_english(text, source_language):
    source_language_code = SOURCE_LANGUAGE_CODES.get(source_language)
    if source_language_code is None:
        raise TranslationError(
            "The selected language is not supported for translation."
        )

    clean_text = _validate_text(text)
    if source_language_code == "en":
        return clean_text

    return _translate(clean_text, source_language_code, "en")


def translate_from_english(text, target_language):
    target_language_code = SOURCE_LANGUAGE_CODES.get(target_language)
    if target_language_code is None:
        raise TranslationError(
            "The selected language is not supported for translation."
        )

    clean_text = _validate_text(text)
    if target_language_code == "en":
        return clean_text

    return _translate(clean_text, "en", target_language_code)

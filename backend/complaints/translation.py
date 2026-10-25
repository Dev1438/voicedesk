from django.conf import settings
from django.utils.module_loading import import_string


class TranslationUnavailable(Exception):
    pass


class TranslationError(Exception):
    pass


def translate_to_english(text, source_language):
    """Call adapter(text, source_language) -> text.

    Configured adapters should raise TranslationError when translation fails.
    """
    adapter_path = settings.COMPLAINT_TRANSLATION_ADAPTER
    if not adapter_path:
        raise TranslationUnavailable(
            "No English translation provider is configured."
        )

    try:
        adapter = import_string(adapter_path)
    except (ImportError, AttributeError, TypeError) as error:
        raise TranslationError(
            "The configured translation adapter could not be loaded."
        ) from error

    if not callable(adapter):
        raise TranslationError(
            "The configured translation adapter is not callable."
        )

    translated_text = adapter(text, source_language)

    if not isinstance(translated_text, str) or not translated_text.strip():
        raise TranslationError(
            "The translation adapter did not return English text."
        )

    return translated_text.strip()

import io
import json
import socket
import tempfile
import wave
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from google.api_core.exceptions import (
    DeadlineExceeded,
    FailedPrecondition,
    GoogleAPICallError,
    PermissionDenied,
    ResourceExhausted,
    Unauthenticated,
)
from google.auth.exceptions import DefaultCredentialsError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .duplicate_detection import find_possible_duplicate
from .denoising import (
    DenoisingError,
    DenoisingResult,
    denoise_complaint_audio,
)
from .google_translation import (
    translate_from_english as google_translate_from_english,
    translate_to_english as google_translate_to_english,
)
from .models import AgentReply, Complaint
from .processing import ComplaintSuggestions, suggest_complaint_details
from .translation import TranslationError, TranslationUnavailable
from .transcription import TranscriptionError, transcribe_complaint
from .tts import TTSError, synthesize_reply


class AudicLabsTTSTests(TestCase):
    @override_settings(
        AUDICLABS_API_KEY="unit-test-key",
        AUDICLABS_TTS_MODEL="audic-tts-1",
        AUDICLABS_TTS_VOICE="configured-voice",
        AUDICLABS_TTS_LANGUAGE="",
    )
    @patch("complaints.tts.requests.post")
    def test_sends_documented_json_and_returns_mp3_bytes(self, post):
        mp3_bytes = b"ID3\x04\x00\x00mock mp3 payload"
        response = Mock(
            ok=True,
            status_code=200,
            content=mp3_bytes,
            headers={"X-Request-Id": "tts-request-1"},
        )
        post.return_value = response

        audio_bytes = synthesize_reply("नमस्ते", "hi")

        self.assertEqual(audio_bytes, mp3_bytes)
        post.assert_called_once_with(
            "https://api.audiclabs.com/v1/tts/speech",
            headers={"Authorization": "Bearer unit-test-key"},
            json={
                "model": "audic-tts-1",
                "input": "नमस्ते",
                "voice": "configured-voice",
                "language": "hi",
                "stream": False,
            },
            timeout=(5, 120),
        )

    @override_settings(
        AUDICLABS_API_KEY="unit-test-key",
        AUDICLABS_TTS_MODEL="audic-tts-1",
        AUDICLABS_TTS_VOICE="configured-voice",
        AUDICLABS_TTS_LANGUAGE="hi-IN",
    )
    @patch("complaints.tts.requests.post")
    def test_uses_configured_language_override(self, post):
        post.return_value = Mock(
            ok=True,
            status_code=200,
            content=b"\xff\xfbmock mp3 payload",
            headers={},
        )

        synthesize_reply("Hello", "hi")

        self.assertEqual(
            post.call_args.kwargs["json"]["language"],
            "hi-IN",
        )

    @override_settings(
        AUDICLABS_API_KEY="unit-test-key",
        AUDICLABS_TTS_MODEL="audic-tts-1",
        AUDICLABS_TTS_VOICE="configured-voice",
    )
    @patch("complaints.tts.requests.post")
    def test_provider_errors_are_safe_and_do_not_retry(self, post):
        for status_code in (401, 402, 403, 429, 503):
            with self.subTest(status_code=status_code):
                post.reset_mock()
                post.return_value = Mock(
                    ok=False,
                    status_code=status_code,
                    content=b"private provider details",
                    headers={"X-Request-Id": "tts-error-id"},
                )
                with self.assertRaises(TTSError) as error:
                    synthesize_reply("Hello", "hi")

                self.assertNotIn(
                    "private provider details",
                    str(error.exception),
                )
                post.assert_called_once()

    @override_settings(
        AUDICLABS_API_KEY="unit-test-key",
        AUDICLABS_TTS_MODEL="audic-tts-1",
        AUDICLABS_TTS_VOICE="configured-voice",
    )
    @patch("complaints.tts.requests.post")
    def test_rejects_non_mp3_success_body(self, post):
        post.return_value = Mock(
            ok=True,
            status_code=200,
            content=b'{"error":"not audio"}',
            headers={"X-Request-Id": "tts-json-response"},
        )

        with self.assertRaisesRegex(TTSError, "invalid audio"):
            synthesize_reply("Hello", "hi")

    @override_settings(
        AUDICLABS_API_KEY="unit-test-key",
        AUDICLABS_TTS_MODEL="audic-tts-1",
        AUDICLABS_TTS_VOICE="",
    )
    @patch("complaints.tts.requests.post")
    def test_missing_voice_configuration_does_not_call_provider(self, post):
        with self.assertRaisesRegex(TTSError, "voice is not configured"):
            synthesize_reply("Hello", "hi")

        post.assert_not_called()


class GoogleTranslationAdapterTests(TestCase):
    def setUp(self):
        self.settings_override = override_settings(
            GOOGLE_CLOUD_PROJECT="test-project",
            GOOGLE_CLOUD_LOCATION="global",
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    @patch("complaints.google_translation.translate_v3.TranslationServiceClient")
    def test_extracts_telugu_and_hindi_translation_results(self, client_class):
        client = client_class.return_value
        translated_texts = (
            "Translated Telugu sample",
            "Translated Hindi sample",
        )
        client.translate_text.side_effect = [
            SimpleNamespace(
                translations=[
                    SimpleNamespace(translated_text=f"  {text}  ")
                ]
            )
            for text in translated_texts
        ]

        for source_language, expected_text in zip(
            ("te", "hi"),
            translated_texts,
        ):
            with self.subTest(source_language=source_language):
                translated = google_translate_to_english(
                    "source words",
                    source_language,
                )

                self.assertEqual(translated, expected_text)
                request = client.translate_text.call_args.kwargs["request"]
                self.assertEqual(
                    request["source_language_code"],
                    source_language,
                )
                self.assertEqual(request["target_language_code"], "en")

        self.assertEqual(client.translate_text.call_count, 2)

    @patch("complaints.google_translation.translate_v3.TranslationServiceClient")
    def test_translates_english_agent_reply_to_customer_language(
        self,
        client_class,
    ):
        client = client_class.return_value
        client.translate_text.return_value = SimpleNamespace(
            translations=[
                SimpleNamespace(translated_text="नमस्ते")
            ]
        )

        translated = google_translate_from_english("Hello", "hi")

        self.assertEqual(translated, "नमस्ते")
        client.translate_text.assert_called_once_with(
            request={
                "parent": "projects/test-project/locations/global",
                "contents": ["Hello"],
                "mime_type": "text/plain",
                "source_language_code": "en",
                "target_language_code": "hi",
            },
            timeout=15,
        )

    @patch("complaints.google_translation.translate_v3.TranslationServiceClient")
    def test_preserves_unicode_translation_response(self, client_class):
        client = client_class.return_value
        unicode_translation = "మీ ఇంటికి నీటి సరఫరా అవసరం."
        client.translate_text.return_value = SimpleNamespace(
            translations=[
                SimpleNamespace(translated_text=unicode_translation)
            ]
        )

        translated = google_translate_to_english(
            "source-language sample",
            "te",
        )

        self.assertEqual(translated, unicode_translation)
        self.assertEqual(
            client.translate_text.call_args.kwargs["request"][
                "source_language_code"
            ],
            "te",
        )
        self.assertEqual(
            client.translate_text.call_args.kwargs["request"][
                "target_language_code"
            ],
            "en",
        )

    @patch("complaints.google_translation.translate_v3.TranslationServiceClient")
    def test_accepts_english_paraphrase_without_fixed_keywords(self, client_class):
        client = client_class.return_value
        paraphrase = "The supply is essential for my household."
        client.translate_text.return_value = SimpleNamespace(
            translations=[
                SimpleNamespace(translated_text=paraphrase)
            ]
        )

        translated = google_translate_to_english(
            "source-language sample",
            "te",
        )

        self.assertEqual(translated, paraphrase)
        request = client.translate_text.call_args.kwargs["request"]
        self.assertEqual(request["source_language_code"], "te")
        self.assertEqual(request["target_language_code"], "en")
        self.assertNotIn("water", translated.casefold())
        self.assertNotIn("need", translated.casefold())
        self.assertNotIn("want", translated.casefold())

    @patch("complaints.google_translation.translate_v3.TranslationServiceClient")
    def test_maps_each_frontend_language_code(self, client_class):
        client = client_class.return_value
        client.translate_text.return_value = SimpleNamespace(
            translations=[SimpleNamespace(translated_text="English")]
        )

        for source_code in ("te", "hi", "ta", "kn"):
            with self.subTest(source_code=source_code):
                google_translate_to_english("source text", source_code)
                request = client.translate_text.call_args.kwargs["request"]
                self.assertEqual(request["source_language_code"], source_code)
                self.assertEqual(request["target_language_code"], "en")

        self.assertEqual(client.translate_text.call_count, 4)

    @patch("complaints.google_translation.translate_v3.TranslationServiceClient")
    def test_english_input_is_returned_without_api_call(self, client_class):
        translated = google_translate_to_english(" Already English ", "en")

        self.assertEqual(translated, "Already English")
        client_class.assert_not_called()

    @patch("complaints.google_translation.translate_v3.TranslationServiceClient")
    def test_rejects_unsupported_language_without_api_call(self, client_class):
        with self.assertRaisesRegex(TranslationError, "not supported"):
            google_translate_to_english("source text", "xx")

        client_class.assert_not_called()

    @override_settings(GOOGLE_CLOUD_PROJECT="")
    @patch("complaints.google_translation.translate_v3.TranslationServiceClient")
    def test_missing_project_is_unavailable_without_api_call(self, client_class):
        with self.assertRaises(TranslationUnavailable):
            google_translate_to_english("source text", "te")

        client_class.assert_not_called()

    @patch(
        "complaints.google_translation.translate_v3.TranslationServiceClient",
        side_effect=DefaultCredentialsError("private credential detail"),
    )
    def test_missing_adc_credentials_are_unavailable(self, client_class):
        with self.assertRaises(TranslationUnavailable) as error:
            google_translate_to_english("source text", "te")

        self.assertNotIn("private credential detail", str(error.exception))
        client_class.assert_called_once()

    def test_provider_errors_are_mapped_without_raw_details(self):
        cases = (
            (Unauthenticated, "authentication failed"),
            (PermissionDenied, "access was denied"),
            (ResourceExhausted, "quota or billing limits"),
            (FailedPrecondition, "API or billing is not enabled"),
            (DeadlineExceeded, "timed out"),
            (GoogleAPICallError, "could not complete"),
        )
        for error_type, expected_message in cases:
            with self.subTest(error_type=error_type.__name__):
                client = Mock()
                client.translate_text.side_effect = error_type(
                    "private provider response"
                )
                with patch(
                    "complaints.google_translation.translate_v3."
                    "TranslationServiceClient",
                    return_value=client,
                ):
                    with self.assertRaises(TranslationError) as error:
                        google_translate_to_english("source text", "te")

                self.assertIn(expected_message, str(error.exception))
                self.assertNotIn(
                    "private provider response",
                    str(error.exception),
                )

    def test_empty_translation_is_a_safe_provider_error(self):
        client = Mock()
        client.translate_text.return_value = SimpleNamespace(
            translations=[SimpleNamespace(translated_text="   ")]
        )

        with patch(
            "complaints.google_translation.translate_v3."
            "TranslationServiceClient",
            return_value=client,
        ):
            with self.assertRaisesRegex(TranslationError, "no translated text"):
                google_translate_to_english("source text", "te")

    def test_missing_translation_result_is_a_safe_provider_error(self):
        client = Mock()
        client.translate_text.return_value = SimpleNamespace(translations=[])

        with patch(
            "complaints.google_translation.translate_v3."
            "TranslationServiceClient",
            return_value=client,
        ):
            with self.assertRaisesRegex(TranslationError, "no translated text"):
                google_translate_to_english("source text", "te")


def _wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 160)
    return output.getvalue()


class ComplaintDenoisingServiceTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            AUDICLABS_API_KEY="unit-test-key",
            AUDICLABS_DENOISE_MODEL="audic-1",
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.complaint = Complaint.objects.create(
            customer_phone="1234567890",
            audio=SimpleUploadedFile(
                "complaint.webm",
                b"original audio bytes",
                content_type="audio/webm",
            ),
        )

    @patch("complaints.denoising.requests.post")
    def test_returns_valid_cleaned_wav_and_sends_configured_model(self, post):
        response = Mock(status_code=200, ok=True, content=_wav_bytes())
        response.headers = {
            "X-Request-Id": "denoise-success",
            "X-NS-Stats": "frames=160",
        }
        post.return_value = response

        with self.assertLogs("complaints.denoising", level="INFO") as logs:
            result = denoise_complaint_audio(self.complaint)

        self.assertEqual(result, DenoisingResult(audio_bytes=_wav_bytes()))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(
            post.call_args.args[0],
            "https://api.audiclabs.com/v1/noise-suppressor/denoise",
        )
        self.assertEqual(
            post.call_args.kwargs["data"],
            {"model": "audic-1"},
        )
        self.assertEqual(
            post.call_args.kwargs["files"]["file"][0],
            "complaint.webm",
        )
        self.assertEqual(
            post.call_args.kwargs["headers"],
            {"Authorization": "Bearer unit-test-key"},
        )
        self.assertIn("denoise-success", "\n".join(logs.output))
        self.assertNotIn("original audio bytes", "\n".join(logs.output))

    def test_rejects_invalid_wav_response(self):
        response = Mock(
            status_code=200,
            ok=True,
            content=b"not a wav",
        )
        response.headers = {"X-Request-Id": "invalid-wav"}

        with patch("complaints.denoising.requests.post", return_value=response):
            with self.assertRaisesRegex(DenoisingError, "invalid cleaned audio"):
                denoise_complaint_audio(self.complaint)

    def test_rejects_incomplete_wav_response(self):
        valid_wav = _wav_bytes()
        incomplete_wav = valid_wav[:-20]
        response = Mock(status_code=200, ok=True, content=incomplete_wav)
        response.headers = {"X-Request-Id": "incomplete-wav"}

        with patch("complaints.denoising.requests.post", return_value=response):
            with self.assertRaisesRegex(DenoisingError, "invalid cleaned audio"):
                denoise_complaint_audio(self.complaint)

    @override_settings(AUDICLABS_DENOISE_MODEL="unsupported-model")
    @patch("complaints.denoising.requests.post")
    def test_rejects_unsupported_model_before_request(self, post):
        with self.assertRaisesRegex(DenoisingError, "must be one of"):
            denoise_complaint_audio(self.complaint)

        post.assert_not_called()

    def test_handles_documented_http_errors_without_provider_body(self):
        cases = (
            (401, "authentication was rejected"),
            (403, "not permitted"),
            (402, "billing or credit"),
            (413, "too large"),
            (422, "unsupported"),
        )
        for status_code, expected_detail in cases:
            with self.subTest(status_code=status_code):
                response = Mock(status_code=status_code, ok=False)
                response.headers = {"X-Request-Id": f"request-{status_code}"}
                with patch(
                    "complaints.denoising.requests.post",
                    return_value=response,
                ):
                    with self.assertRaises(DenoisingError) as error:
                        denoise_complaint_audio(self.complaint)

                self.assertIn(expected_detail, str(error.exception))
                self.assertIn(f"HTTP {status_code}", str(error.exception))
                self.assertIn(f"request-{status_code}", str(error.exception))

    @patch("complaints.denoising.time.sleep")
    @patch("complaints.denoising.requests.post")
    def test_retries_once_for_rate_limit_and_honors_retry_after(
        self,
        post,
        sleep,
    ):
        rate_limited = Mock(status_code=429, ok=False)
        rate_limited.headers = {
            "X-Request-Id": "rate-limited",
            "Retry-After": "2",
        }
        success = Mock(status_code=200, ok=True, content=_wav_bytes())
        success.headers = {"X-Request-Id": "denoise-retry"}
        post.side_effect = [rate_limited, success]

        result = denoise_complaint_audio(self.complaint)

        self.assertEqual(result.audio_bytes, _wav_bytes())
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(2)

    @patch("complaints.denoising.time.sleep")
    @patch("complaints.denoising.requests.post")
    def test_caps_very_large_retry_after_without_sleeping(
        self,
        post,
        sleep,
    ):
        rate_limited = Mock(status_code=429, ok=False)
        rate_limited.headers = {
            "X-Request-Id": "rate-limited-large-delay",
            "Retry-After": "999999999",
        }
        success = Mock(status_code=200, ok=True, content=_wav_bytes())
        success.headers = {"X-Request-Id": "denoise-after-capped-retry"}
        post.side_effect = [rate_limited, success]

        with self.assertLogs("complaints.denoising", level="WARNING") as logs:
            result = denoise_complaint_audio(self.complaint)

        self.assertEqual(result.audio_bytes, _wav_bytes())
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(30)
        self.assertIn(
            "Retry-After capped from 999999999 to 30 seconds",
            "\n".join(logs.output),
        )

    @patch("complaints.denoising.requests.post")
    def test_service_unavailable_without_retry_after_is_not_retried(self, post):
        unavailable = Mock(status_code=503, ok=False)
        unavailable.headers = {"X-Request-Id": "service-unavailable"}
        post.return_value = unavailable

        with self.assertRaisesRegex(DenoisingError, "HTTP 503"):
            denoise_complaint_audio(self.complaint)

        post.assert_called_once()

    @patch(
        "complaints.denoising.requests.post",
        side_effect=requests.Timeout(),
    )
    def test_timeout_is_reported_without_exposing_audio(self, post):
        with self.assertRaisesRegex(DenoisingError, "Could not reach AudicLabs"):
            denoise_complaint_audio(self.complaint)

        post.assert_called_once()


class ComplaintTranscriptionTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.media_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            AUDICLABS_API_KEY="unit-test-key",
            AUDICLABS_DENOISE_MODEL="audic-1",
            COMPLAINT_TRANSLATION_ADAPTER="",
        )
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        denoise_patcher = patch(
            "complaints.views.denoise_complaint_audio",
            return_value=DenoisingResult(audio_bytes=_wav_bytes()),
        )
        self.denoise_mock = denoise_patcher.start()
        self.addCleanup(denoise_patcher.stop)

    def _submit_complaint(self):
        return self.client.post(
            "/api/complaints/",
            {
                "customer_phone": "1234567890",
                "language": "te",
                "audio": SimpleUploadedFile(
                    "complaint.webm",
                    b"test audio bytes",
                    content_type="audio/webm",
                ),
            },
        )

    @patch("complaints.transcription.requests.post")
    def test_transcribes_saved_audio_and_returns_ticket(self, post):
        response = Mock(status_code=200, ok=True)
        response.headers = {"X-Request-Id": "request-success"}
        response.json.return_value = {"text": "The complaint transcript"}
        uploaded_audio = []

        def capture_upload(*args, **kwargs):
            audio_file = kwargs["files"]["file"][1]
            uploaded_audio.append(audio_file.read())
            audio_file.seek(0)
            return response

        post.side_effect = capture_upload

        result = self._submit_complaint()

        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()["transcription_status"], "completed")
        self.assertEqual(
            result.json()["original_transcript"],
            "The complaint transcript",
        )
        complaint = Complaint.objects.get()
        self.assertEqual(
            complaint.original_transcript,
            "The complaint transcript",
        )
        self.assertTrue(complaint.audio.storage.exists(complaint.audio.name))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(
            post.call_args.kwargs["headers"],
            {"Authorization": "Bearer unit-test-key"},
        )
        self.assertEqual(
            post.call_args.kwargs["data"],
            {"model": "audic-stt-1"},
        )
        self.assertEqual(
            post.call_args.kwargs["files"]["file"][0],
            "complaint.wav",
        )
        self.assertEqual(
            post.call_args.kwargs["files"]["file"][2],
            "audio/wav",
        )
        self.assertEqual(uploaded_audio, [_wav_bytes()])
        self.assertNotIn("language", post.call_args.kwargs["data"])
        self.assertEqual(
            result.json()["denoising_status"],
            Complaint.DenoisingStatus.COMPLETED,
        )

    @patch("complaints.views.transcribe_complaint")
    def test_denoising_failure_falls_back_to_original_and_is_recorded(
        self,
        transcribe,
    ):
        self.denoise_mock.side_effect = DenoisingError(
            "AudicLabs denoising failed (HTTP 503: temporary)"
        )
        transcribe.return_value = "Transcript from original audio"

        result = self._submit_complaint()

        self.assertEqual(result.status_code, 201)
        self.assertEqual(
            result.json()["denoising_status"],
            Complaint.DenoisingStatus.FAILED,
        )
        self.assertIn("Denoising failed", result.json()["message"])
        self.assertIn("HTTP 503", result.json()["denoising_error"])
        transcribe.assert_called_once()
        self.assertIsNone(transcribe.call_args.kwargs["audio_bytes"])
        complaint = Complaint.objects.get()
        self.assertEqual(
            complaint.denoising_status,
            Complaint.DenoisingStatus.FAILED,
        )
        with complaint.audio.open("rb") as original_audio:
            self.assertEqual(original_audio.read(), b"test audio bytes")

    @patch("complaints.views.transcribe_complaint")
    def test_both_failures_are_returned_safely_and_saved(
        self,
        transcribe,
    ):
        secret_like_detail = "provider diagnostic with private content"
        self.denoise_mock.side_effect = DenoisingError(secret_like_detail)
        transcribe.side_effect = TranscriptionError(secret_like_detail)

        result = self._submit_complaint()

        self.assertEqual(result.status_code, 201)
        payload = result.json()
        self.assertEqual(payload["denoising_status"], "failed")
        self.assertEqual(
            payload["processing_status"],
            Complaint.ProcessingStatus.TRANSCRIPTION_FAILED,
        )
        self.assertIn("denoising failed", payload["message"].lower())
        self.assertIn("transcription also failed", payload["message"].lower())
        self.assertIn("original recording was used", payload["denoising_error"])
        self.assertEqual(
            payload["transcription_error"],
            "Transcription also failed. Please try again later.",
        )
        response_text = result.content.decode("utf-8")
        self.assertNotIn(secret_like_detail, response_text)
        self.assertNotIn("Traceback", response_text)

        complaint = Complaint.objects.get()
        self.assertEqual(
            complaint.denoising_status,
            Complaint.DenoisingStatus.FAILED,
        )
        self.assertEqual(
            complaint.processing_status,
            Complaint.ProcessingStatus.TRANSCRIPTION_FAILED,
        )
        self.assertTrue(complaint.audio.storage.exists(complaint.audio.name))

    @patch("complaints.transcription.requests.post")
    def test_http_failure_keeps_complaint_and_audio(self, post):
        provider_secret = "private_provider_diagnostic bearer secret-token"
        response = Mock(status_code=422, ok=False)
        response.headers = {"X-Request-Id": "request-invalid-audio"}
        response.json.return_value = {
            "detail": provider_secret,
            "error": {
                "type": provider_secret,
                "message": provider_secret,
            },
        }
        post.return_value = response

        with self.assertLogs("complaints.transcription", level="ERROR") as logs:
            result = self._submit_complaint()

        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()["transcription_status"], "failed")
        self.assertIn("HTTP 422", result.json()["transcription_error"])
        self.assertIn(
            "check the recording format",
            result.json()["transcription_error"],
        )
        log_output = "\n".join(logs.output)
        self.assertIn("error_category=unsupported_audio_or_model", log_output)
        self.assertIn("request-invalid-audio", log_output)
        self.assertNotIn(provider_secret, log_output)
        self.assertNotIn(provider_secret, result.content.decode("utf-8"))
        complaint = Complaint.objects.get()
        self.assertEqual(complaint.original_transcript, "")
        self.assertTrue(complaint.audio.storage.exists(complaint.audio.name))

    @patch("complaints.transcription.time.sleep")
    @patch("complaints.transcription.requests.post")
    def test_retries_once_after_retry_after(self, post, sleep):
        rate_limited = Mock(status_code=429, ok=False)
        rate_limited.headers = {
            "X-Request-Id": "request-rate-limited",
            "Retry-After": "2",
        }
        rate_limited.json.return_value = {"error": {"type": "rate_limited"}}

        success = Mock(status_code=200, ok=True)
        success.headers = {"X-Request-Id": "request-retry"}
        success.json.return_value = {"text": "Recovered transcript"}
        post.side_effect = [rate_limited, success]

        result = self._submit_complaint()

        self.assertEqual(result.status_code, 201)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertEqual(
            Complaint.objects.get().original_transcript,
            "Recovered transcript",
        )

    @patch("complaints.transcription.time.sleep")
    @patch("complaints.transcription.requests.post")
    def test_retries_once_after_503_retry_after(self, post, sleep):
        unavailable = Mock(status_code=503, ok=False)
        unavailable.headers = {
            "X-Request-Id": "request-unavailable",
            "Retry-After": "3",
        }
        unavailable.json.return_value = {
            "error": {"type": "circuit_open"},
        }

        success = Mock(status_code=200, ok=True)
        success.headers = {"X-Request-Id": "request-retry"}
        success.json.return_value = {"text": "Recovered transcript"}
        post.side_effect = [unavailable, success]

        result = self._submit_complaint()

        self.assertEqual(result.status_code, 201)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(3)
        self.assertEqual(
            Complaint.objects.get().original_transcript,
            "Recovered transcript",
        )

    @patch(
        "complaints.transcription.requests.post",
        side_effect=requests.Timeout(),
    )
    def test_timeout_keeps_complaint_and_audio(self, post):
        result = self._submit_complaint()

        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()["transcription_status"], "failed")
        self.assertIn("Could not reach AudicLabs", result.json()["transcription_error"])
        complaint = Complaint.objects.get()
        self.assertEqual(complaint.original_transcript, "")
        self.assertTrue(complaint.audio.storage.exists(complaint.audio.name))

    def test_request_exception_logs_safe_nested_failure_diagnostics(self):
        cases = (
            (
                "dns_resolution_error",
                requests.ConnectionError("sensitive host value"),
                socket.gaierror(-2, "sensitive DNS detail"),
            ),
            (
                "connection_reset",
                requests.ConnectionError("sensitive connection value"),
                ConnectionResetError(104, "sensitive reset detail"),
            ),
            (
                "proxy_error",
                requests.exceptions.ProxyError(
                    "proxy user:password should not appear"
                ),
                None,
            ),
            (
                "timeout",
                requests.Timeout("sensitive timeout detail"),
                None,
            ),
        )
        complaint = Complaint.objects.create(
            customer_phone="1234567890",
            audio=SimpleUploadedFile(
                "complaint.webm",
                b"audio bytes",
                content_type="audio/webm",
            ),
        )

        for failure_kind, request_error, nested_error in cases:
            with self.subTest(failure_kind=failure_kind):
                if nested_error:
                    request_error.__cause__ = nested_error
                with patch(
                    "complaints.transcription.requests.post",
                    side_effect=request_error,
                ):
                    with self.assertLogs(
                        "complaints.transcription",
                        level="ERROR",
                    ) as logs:
                        with self.assertRaises(TranscriptionError) as error:
                            transcribe_complaint(complaint)

                log_output = "\n".join(logs.output)
                self.assertIn(f"failure_kind={failure_kind}", log_output)
                self.assertIn("reason_types=", log_output)
                self.assertNotIn("sensitive", log_output)
                self.assertNotIn("password", log_output)
                self.assertNotIn("sensitive", str(error.exception))

    @patch("complaints.transcription.requests.post")
    def test_unreadable_saved_audio_does_not_call_api(self, post):
        complaint = Complaint(
            customer_phone="1234567890",
            audio="complaints/audio/missing.webm",
        )
        complaint.save()

        with self.assertRaises(TranscriptionError):
            transcribe_complaint(complaint)

        post.assert_not_called()


class ComplaintLookupTests(TestCase):
    def setUp(self):
        self.complaint = Complaint.objects.create(
            customer_phone="1234567890",
            original_transcript="Previously saved transcript",
        )

    def test_lookup_returns_only_approved_fields(self):
        response = self.client.get(
            f"/api/complaints/{self.complaint.ticket_number}/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {
                "ticket_number",
                "status",
                "denoising_status",
                "original_transcript",
                "processing_status",
                "english_translation",
                "category",
                "priority",
                "summary",
                "classification_status",
                "possible_duplicate",
                "possible_duplicate_ticket_number",
                "agent_replies",
                "created_at",
                "updated_at",
            },
        )
        self.assertEqual(
            response.json()["ticket_number"],
            str(self.complaint.ticket_number),
        )
        self.assertEqual(response.json()["status"], self.complaint.status)
        self.assertEqual(
            response.json()["original_transcript"],
            "Previously saved transcript",
        )
        self.assertNotIn("customer_phone", response.json())

    def test_lookup_returns_404_for_unknown_ticket(self):
        response = self.client.get(
            "/api/complaints/00000000-0000-0000-0000-000000000000/"
        )

        self.assertEqual(response.status_code, 404)


class AgentAuthenticationTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.agent = self.user_model.objects.create_user(
            username="agent",
            password="test-password",
            is_staff=True,
        )
        self.complaint = Complaint.objects.create(
            customer_phone="1234567890",
            original_transcript="Customer complaint",
        )

    def test_agent_can_log_in_check_session_and_log_out(self):
        login_response = self.client.post(
            "/api/auth/login/",
            data=json.dumps(
                {"username": "agent", "password": "test-password"}
            ),
            content_type="application/json",
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertTrue(login_response.json()["authenticated"])
        self.assertEqual(login_response.json()["user"]["username"], "agent")

        session_response = self.client.get("/api/auth/session/")
        self.assertEqual(session_response.status_code, 200)
        self.assertTrue(session_response.json()["authenticated"])
        self.assertTrue(session_response.json()["user"]["is_staff"])

        logout_response = self.client.post("/api/auth/logout/")
        self.assertEqual(logout_response.status_code, 200)
        self.assertFalse(logout_response.json()["authenticated"])
        self.assertEqual(
            self.client.get("/api/auth/session/").json()["authenticated"],
            False,
        )

    def test_login_rejects_invalid_credentials_and_non_staff_accounts(self):
        invalid_response = self.client.post(
            "/api/auth/login/",
            data=json.dumps(
                {"username": "agent", "password": "incorrect"}
            ),
            content_type="application/json",
        )
        self.assertEqual(invalid_response.status_code, 401)

        self.user_model.objects.create_user(
            username="customer-user",
            password="test-password",
        )
        non_staff_response = self.client.post(
            "/api/auth/login/",
            data=json.dumps(
                {"username": "customer-user", "password": "test-password"}
            ),
            content_type="application/json",
        )
        self.assertEqual(non_staff_response.status_code, 401)
        self.assertFalse(
            self.client.get("/api/auth/session/").json()["authenticated"]
        )

    def test_agent_endpoints_reject_anonymous_requests(self):
        responses = [
            self.client.get("/api/complaints/"),
            self.client.post(
                f"/api/complaints/{self.complaint.ticket_number}/status/",
                {"status": Complaint.Status.IN_PROGRESS},
            ),
            self.client.post(
                f"/api/complaints/{self.complaint.ticket_number}/replies/",
                data=json.dumps({"english_text": "We will help."}),
                content_type="application/json",
            ),
        ]

        self.assertEqual([response.status_code for response in responses], [401] * 3)

    def test_authenticated_non_staff_user_is_forbidden(self):
        customer_user = self.user_model.objects.create_user(
            username="customer-user",
            password="test-password",
        )
        self.client.force_login(customer_user)

        response = self.client.get("/api/complaints/")

        self.assertEqual(response.status_code, 403)

    def test_customer_ticket_lookup_and_submission_remain_public(self):
        lookup_response = self.client.get(
            f"/api/complaints/{self.complaint.ticket_number}/"
        )
        submission_response = self.client.post(
            "/api/complaints/",
            {"customer_phone": "invalid"},
        )

        self.assertEqual(lookup_response.status_code, 200)
        self.assertEqual(submission_response.status_code, 400)
        self.assertIn("10-digit", submission_response.json()["error"])

    def test_login_and_agent_mutations_require_csrf_tokens(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.get("/api/auth/session/")
        agent_login_response = csrf_client.post(
            "/api/auth/login/",
            data=json.dumps(
                {"username": "agent", "password": "test-password"}
            ),
            content_type="application/json",
            HTTP_ORIGIN="http://localhost:5173",
        )
        self.assertEqual(agent_login_response.status_code, 403)

        csrf_client.force_login(self.agent)
        status_response = csrf_client.post(
            f"/api/complaints/{self.complaint.ticket_number}/status/",
            {"status": Complaint.Status.IN_PROGRESS},
            HTTP_ORIGIN="http://localhost:5173",
        )
        self.assertEqual(status_response.status_code, 403)

        secure_client = Client(enforce_csrf_checks=True)
        secure_client.get("/api/auth/session/")
        csrf_token = secure_client.cookies["csrftoken"].value
        login_response = secure_client.post(
            "/api/auth/login/",
            data=json.dumps(
                {"username": "agent", "password": "test-password"}
            ),
            content_type="application/json",
            HTTP_ORIGIN="http://localhost:5173",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(login_response.status_code, 200)

        status_response = secure_client.post(
            f"/api/complaints/{self.complaint.ticket_number}/status/",
            {"status": Complaint.Status.IN_PROGRESS},
            HTTP_ORIGIN="http://localhost:5173",
            HTTP_X_CSRFTOKEN=secure_client.cookies["csrftoken"].value,
        )
        self.assertEqual(status_response.status_code, 200)


class AgentQueueTests(TestCase):
    def setUp(self):
        agent = get_user_model().objects.create_user(
            username="queue-agent",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(agent)
        self.older_complaint = Complaint.objects.create(
            customer_phone="1111111111",
            language="hi",
            original_transcript="Older transcript",
            category="billing",
            priority="high",
            status=Complaint.Status.OPEN,
        )
        self.newer_complaint = Complaint.objects.create(
            customer_phone="2222222222",
            language="te",
            original_transcript="Newer transcript",
            english_translation="Translated transcript",
            category="service",
            priority="normal",
            summary="Newer ticket summary",
            status=Complaint.Status.SUBMITTED,
        )

    def test_list_returns_newest_first_and_excludes_private_fields(self):
        response = self.client.get("/api/complaints/")

        self.assertEqual(response.status_code, 200)
        complaints = response.json()["complaints"]
        self.assertEqual(
            [item["ticket_number"] for item in complaints],
            [
                str(self.newer_complaint.ticket_number),
                str(self.older_complaint.ticket_number),
            ],
        )
        expected_fields = {
            "ticket_number",
            "status",
            "denoising_status",
            "language",
            "original_transcript",
            "english_translation",
            "category",
            "priority",
            "summary",
            "processing_status",
            "classification_status",
            "possible_duplicate",
            "possible_duplicate_ticket_number",
            "agent_replies",
            "created_at",
            "updated_at",
        }
        self.assertEqual(set(complaints[0]), expected_fields)
        self.assertNotIn("customer_phone", complaints[0])
        self.assertNotIn("audio", complaints[0])
        self.assertNotIn("photo", complaints[0])

    def test_list_filters_by_status_category_priority_and_language(self):
        response = self.client.get(
            "/api/complaints/",
            {
                "status": Complaint.Status.OPEN,
                "category": "billing",
                "priority": "high",
                "language": "hi",
            },
        )

        self.assertEqual(response.status_code, 200)
        complaints = response.json()["complaints"]
        self.assertEqual(len(complaints), 1)
        self.assertEqual(
            complaints[0]["ticket_number"],
            str(self.older_complaint.ticket_number),
        )

    def test_status_update_accepts_existing_status(self):
        response = self.client.post(
            f"/api/complaints/{self.newer_complaint.ticket_number}/status/",
            {"status": Complaint.Status.IN_PROGRESS},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["status"],
            Complaint.Status.IN_PROGRESS,
        )
        self.newer_complaint.refresh_from_db()
        self.assertEqual(
            self.newer_complaint.status,
            Complaint.Status.IN_PROGRESS,
        )

    def test_status_update_rejects_invalid_status(self):
        response = self.client.post(
            f"/api/complaints/{self.newer_complaint.ticket_number}/status/",
            {"status": "not_a_status"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid status", response.json()["error"])

    def test_status_update_returns_404_for_unknown_ticket(self):
        response = self.client.post(
            "/api/complaints/00000000-0000-0000-0000-000000000000/status/",
            {"status": Complaint.Status.OPEN},
        )

        self.assertEqual(response.status_code, 404)


class AgentReplyWorkflowTests(TestCase):
    def setUp(self):
        agent = get_user_model().objects.create_user(
            username="reply-agent",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(agent)
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.complaint = Complaint.objects.create(
            customer_phone="1234567890",
            language="hi",
        )
        self.url = (
            f"/api/complaints/{self.complaint.ticket_number}/replies/"
        )

    def _post_reply(self, english_text="Your phone repair is complete."):
        return self.client.post(
            self.url,
            data=json.dumps({"english_text": english_text}),
            content_type="application/json",
        )

    @patch("complaints.views.synthesize_reply", return_value=b"ID3mock mp3")
    @patch(
        "complaints.views.translate_from_english",
        return_value="आपके फोन की मरम्मत पूरी हो गई है।",
    )
    def test_successful_reply_is_translated_synthesized_and_saved(
        self,
        translate,
        synthesize,
    ):
        response = self._post_reply()

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(
            payload["message"],
            "Reply saved, translated, and audio generated.",
        )
        reply = AgentReply.objects.get()
        self.assertEqual(reply.english_text, "Your phone repair is complete.")
        self.assertEqual(reply.translated_text, "आपके फोन की मरम्मत पूरी हो गई है।")
        self.assertEqual(reply.customer_language, "hi")
        self.assertEqual(
            reply.translation_status,
            AgentReply.TranslationStatus.COMPLETED,
        )
        self.assertEqual(
            reply.audio_status,
            AgentReply.AudioStatus.COMPLETED,
        )
        with reply.audio.open("rb") as saved_audio:
            self.assertEqual(saved_audio.read(), b"ID3mock mp3")
        self.assertEqual(payload["reply"]["audio_url"], response.wsgi_request.build_absolute_uri(reply.audio.url))
        translate.assert_called_once_with(
            "Your phone repair is complete.",
            "hi",
        )
        synthesize.assert_called_once_with(
            "आपके फोन की मरम्मत पूरी हो गई है।",
            "hi",
        )

    def test_missing_or_invalid_text_is_rejected_without_creating_reply(self):
        for body in ({}, {"english_text": "  "}, {"english_text": 42}):
            with self.subTest(body=body):
                response = self.client.post(
                    self.url,
                    data=json.dumps(body),
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 400)

        self.assertFalse(AgentReply.objects.exists())

    def test_malformed_json_is_rejected(self):
        response = self.client.post(
            self.url,
            data="{",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(AgentReply.objects.exists())

    @patch("complaints.views.translate_from_english")
    @patch("complaints.views.synthesize_reply")
    def test_nonexistent_ticket_does_not_call_providers(
        self,
        synthesize,
        translate,
    ):
        response = self.client.post(
            "/api/complaints/00000000-0000-0000-0000-000000000000/replies/",
            data=json.dumps({"english_text": "A response"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        translate.assert_not_called()
        synthesize.assert_not_called()

    @patch("complaints.views.translate_from_english")
    @patch("complaints.views.synthesize_reply")
    def test_translation_failure_preserves_english_reply(
        self,
        synthesize,
        translate,
    ):
        translate.side_effect = TranslationError("private provider details")

        response = self._post_reply()

        self.assertEqual(response.status_code, 201)
        reply = AgentReply.objects.get()
        self.assertEqual(reply.english_text, "Your phone repair is complete.")
        self.assertEqual(reply.translated_text, "")
        self.assertEqual(
            reply.translation_status,
            AgentReply.TranslationStatus.FAILED,
        )
        self.assertEqual(
            reply.audio_status,
            AgentReply.AudioStatus.NOT_ATTEMPTED,
        )
        self.assertIn("translation failed", response.json()["message"])
        self.assertNotIn("private provider details", response.content.decode())
        synthesize.assert_not_called()

    @patch("complaints.views.translate_from_english", return_value="आपका फोन तैयार है।")
    @patch("complaints.views.synthesize_reply")
    def test_tts_failure_preserves_english_and_translated_text(
        self,
        synthesize,
        translate,
    ):
        synthesize.side_effect = TTSError("safe provider failure")

        response = self._post_reply()

        self.assertEqual(response.status_code, 201)
        reply = AgentReply.objects.get()
        self.assertEqual(reply.english_text, "Your phone repair is complete.")
        self.assertEqual(reply.translated_text, "आपका फोन तैयार है।")
        self.assertEqual(
            reply.audio_status,
            AgentReply.AudioStatus.FAILED,
        )
        self.assertFalse(reply.audio)
        self.assertIn("audio generation failed", response.json()["message"])
        translate.assert_called_once()
        synthesize.assert_called_once()

    @patch("complaints.views.synthesize_reply", return_value=b"ID3mock mp3")
    @patch(
        "complaints.views.translate_from_english",
        return_value="आपका फोन तैयार है।",
    )
    def test_saved_replies_are_exposed_by_ticket_and_queue_apis(
        self,
        translate,
        synthesize,
    ):
        create_response = self._post_reply()
        self.assertEqual(create_response.status_code, 201)

        lookup_response = self.client.get(
            f"/api/complaints/{self.complaint.ticket_number}/"
        )
        self.assertEqual(len(lookup_response.json()["agent_replies"]), 1)
        self.assertEqual(
            lookup_response.json()["agent_replies"][0]["translated_text"],
            "आपका फोन तैयार है।",
        )

        queue_response = self.client.get("/api/complaints/")
        self.assertEqual(
            len(queue_response.json()["complaints"][0]["agent_replies"]),
            1,
        )


class ComplaintProcessingTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            COMPLAINT_TRANSLATION_ADAPTER="",
            AUDICLABS_DENOISE_MODEL="audic-1",
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        denoise_patcher = patch(
            "complaints.views.denoise_complaint_audio",
            return_value=DenoisingResult(audio_bytes=_wav_bytes()),
        )
        self.denoise_mock = denoise_patcher.start()
        self.addCleanup(denoise_patcher.stop)

    def _submit_complaint(self):
        return self.client.post(
            "/api/complaints/",
            {
                "customer_phone": "1234567890",
                "language": "te",
                "audio": SimpleUploadedFile(
                    "complaint.webm",
                    b"mocked audio",
                    content_type="audio/webm",
                ),
            },
        )

    @patch("complaints.views.suggest_complaint_details")
    @patch("complaints.views.translate_to_english")
    @patch("complaints.views.transcribe_complaint")
    def test_successful_processing_saves_transcript_translation_and_suggestions(
        self,
        transcribe,
        translate,
        suggest,
    ):
        transcribe.return_value = "My app crashes when I sign in."
        translate.return_value = "My app crashes when I sign in."
        suggest.return_value = ComplaintSuggestions(
            category="technical",
            priority="normal",
            summary="My app crashes when I sign in.",
        )

        response = self._submit_complaint()

        self.assertEqual(response.status_code, 201)
        complaint = Complaint.objects.get()
        self.assertEqual(
            complaint.original_transcript,
            "My app crashes when I sign in.",
        )
        self.assertEqual(
            complaint.english_translation,
            "My app crashes when I sign in.",
        )
        self.assertEqual(complaint.category, "technical")
        self.assertEqual(complaint.priority, "normal")
        self.assertEqual(
            complaint.summary,
            "My app crashes when I sign in.",
        )
        self.assertEqual(
            complaint.processing_status,
            Complaint.ProcessingStatus.COMPLETED,
        )
        self.assertEqual(
            complaint.classification_status,
            Complaint.ClassificationStatus.SUGGESTED,
        )
        self.assertEqual(
            response.json()["classification_status"],
            Complaint.ClassificationStatus.SUGGESTED,
        )
        translate.assert_called_once_with(
            "My app crashes when I sign in.",
            "te",
        )

    @patch("complaints.views.transcribe_complaint")
    @patch("complaints.views.translate_to_english")
    def test_english_translation_drives_device_suggestions(
        self,
        translate,
        transcribe,
    ):
        hindi_transcript = "मेरा फोन ठीक से काम नहीं कर रहा है।"
        english_translation = (
            "My phone is not working properly, its battery is dead"
        )
        transcribe.return_value = hindi_transcript
        translate.return_value = english_translation

        response = self._submit_complaint()

        self.assertEqual(response.status_code, 201)
        complaint = Complaint.objects.get()
        self.assertEqual(complaint.original_transcript, hindi_transcript)
        self.assertEqual(complaint.english_translation, english_translation)
        self.assertEqual(complaint.category, "technical")
        self.assertEqual(complaint.priority, "normal")
        self.assertEqual(complaint.summary, english_translation)

    @patch("complaints.views.suggest_complaint_details")
    @patch("complaints.views.transcribe_complaint")
    def test_missing_translation_provider_preserves_transcript_and_suggestions(
        self,
        transcribe,
        suggest,
    ):
        transcribe.return_value = "I need help with my account."
        suggest.return_value = ComplaintSuggestions(
            category="account",
            priority="normal",
            summary="I need help with my account.",
        )

        response = self._submit_complaint()

        self.assertEqual(response.status_code, 201)
        complaint = Complaint.objects.get()
        self.assertEqual(
            complaint.original_transcript,
            "I need help with my account.",
        )
        self.assertEqual(complaint.english_translation, "")
        self.assertEqual(complaint.category, "account")
        suggest.assert_called_once_with("I need help with my account.")
        self.assertEqual(
            complaint.processing_status,
            Complaint.ProcessingStatus.TRANSLATION_UNAVAILABLE,
        )
        self.assertEqual(
            response.json()["processing_status"],
            Complaint.ProcessingStatus.TRANSLATION_UNAVAILABLE,
        )
        self.assertIn("translation is unavailable", response.json()["message"])

    @patch("complaints.views.suggest_complaint_details")
    @patch("complaints.views.translate_to_english")
    @patch("complaints.views.transcribe_complaint")
    def test_translation_error_keeps_transcript_and_suggestions(
        self,
        transcribe,
        translate,
        suggest,
    ):
        transcribe.return_value = "I need a refund."
        translate.side_effect = TranslationError("Provider unavailable")
        suggest.return_value = ComplaintSuggestions(
            category="billing",
            priority="normal",
            summary="I need a refund.",
        )

        response = self._submit_complaint()

        self.assertEqual(response.status_code, 201)
        complaint = Complaint.objects.get()
        self.assertEqual(complaint.original_transcript, "I need a refund.")
        self.assertEqual(complaint.english_translation, "")
        self.assertEqual(
            complaint.processing_status,
            Complaint.ProcessingStatus.TRANSLATION_FAILED,
        )
        self.assertEqual(
            complaint.classification_status,
            Complaint.ClassificationStatus.SUGGESTED,
        )

    def test_classifier_suggests_category_and_priority_deterministically(self):
        suggestions = suggest_complaint_details(
            "Urgent: the payment and billing charge are wrong."
        )

        self.assertEqual(suggestions.category, "billing")
        self.assertEqual(suggestions.priority, "high")
        self.assertTrue(suggestions.summary.endswith("."))

    def test_urgent_device_complaint_gets_high_priority_suggestion(self):
        suggestions = suggest_complaint_details(
            "Emergency: my phone battery is dead and needs help immediately."
        )

        self.assertEqual(suggestions.category, "technical")
        self.assertEqual(suggestions.priority, "high")

    def test_classifier_uses_fallbacks_when_confidence_is_low(self):
        suggestions = suggest_complaint_details(
            "Please look into this when you can."
        )

        self.assertEqual(suggestions.category, "Uncategorized")
        self.assertEqual(suggestions.priority, "normal")
        self.assertEqual(
            suggestions.summary,
            "Please look into this when you can.",
        )

    def test_classifier_handles_empty_or_missing_transcript_safely(self):
        for transcript in ("", "   ", None):
            with self.subTest(transcript=transcript):
                suggestions = suggest_complaint_details(transcript)

                self.assertEqual(suggestions.category, "Uncategorized")
                self.assertEqual(suggestions.priority, "normal")
                self.assertEqual(suggestions.summary, "Summary unavailable.")


class DuplicateDetectionTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            COMPLAINT_TRANSLATION_ADAPTER="",
            AUDICLABS_DENOISE_MODEL="audic-1",
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        denoise_patcher = patch(
            "complaints.views.denoise_complaint_audio",
            return_value=DenoisingResult(audio_bytes=_wav_bytes()),
        )
        self.denoise_mock = denoise_patcher.start()
        self.addCleanup(denoise_patcher.stop)

    def _create_complaint(
        self,
        *,
        phone="1234567890",
        transcript="",
        translation="",
    ):
        return Complaint.objects.create(
            customer_phone=phone,
            original_transcript=transcript,
            english_translation=translation,
        )

    def _age_complaint(self, complaint, days):
        Complaint.objects.filter(pk=complaint.pk).update(
            created_at=timezone.now() - timedelta(days=days)
        )
        complaint.refresh_from_db()

    def test_same_phone_matching_normalized_text_within_seven_days(self):
        earlier = self._create_complaint(
            transcript="Your billing charge is wrong.",
        )
        self._age_complaint(earlier, 6)
        newer = self._create_complaint(
            translation="  YOUR billing—charge   is wrong! ",
        )

        duplicate = find_possible_duplicate(newer)

        self.assertEqual(duplicate, earlier)
        newer.possible_duplicate_of = duplicate
        newer.save(update_fields=["possible_duplicate_of"])
        response = self.client.get(
            f"/api/complaints/{newer.ticket_number}/"
        )
        self.assertTrue(response.json()["possible_duplicate"])
        self.assertEqual(
            response.json()["possible_duplicate_ticket_number"],
            str(earlier.ticket_number),
        )

    def test_same_phone_with_different_text_is_not_duplicate(self):
        earlier = self._create_complaint(
            transcript="My delivery has not arrived.",
        )
        self._age_complaint(earlier, 1)
        newer = self._create_complaint(
            transcript="I cannot log in to my account.",
        )

        self.assertIsNone(find_possible_duplicate(newer))

    def test_matching_text_from_different_phone_is_not_duplicate(self):
        earlier = self._create_complaint(
            phone="1111111111",
            transcript="My bill is incorrect.",
        )
        self._age_complaint(earlier, 1)
        newer = self._create_complaint(
            phone="2222222222",
            transcript="My bill is incorrect!",
        )

        self.assertIsNone(find_possible_duplicate(newer))

    def test_matching_text_older_than_seven_days_is_not_duplicate(self):
        earlier = self._create_complaint(
            transcript="My payment was charged twice.",
        )
        self._age_complaint(earlier, 8)
        newer = self._create_complaint(
            transcript="My payment was charged twice!",
        )

        self.assertIsNone(find_possible_duplicate(newer))

    def test_missing_transcript_does_not_create_duplicate(self):
        earlier = self._create_complaint()
        newer = self._create_complaint()

        self.assertIsNone(find_possible_duplicate(newer))
        self.assertIsNone(earlier.possible_duplicate_of)
        self.assertIsNone(newer.possible_duplicate_of)

    def test_existing_complaint_is_not_matched_to_itself(self):
        complaint = self._create_complaint(
            transcript="My payment was charged twice.",
        )

        self.assertIsNone(find_possible_duplicate(complaint))

    def test_historical_complaints_keep_nullable_duplicate_default(self):
        historical = self._create_complaint(
            transcript="Historical complaint text.",
        )

        self.assertIsNone(historical.possible_duplicate_of)
        self.assertTrue(
            Complaint._meta.get_field("possible_duplicate_of").null
        )

    @patch("complaints.views.translate_to_english")
    @patch("complaints.views.transcribe_complaint")
    def test_creation_response_marks_possible_duplicate(
        self,
        transcribe,
        translate,
    ):
        earlier = self._create_complaint(
            transcript="My account login does not work.",
        )
        self._age_complaint(earlier, 2)
        transcribe.return_value = "My account login does not work!"
        translate.side_effect = TranslationUnavailable(
            "No provider configured"
        )

        response = self.client.post(
            "/api/complaints/",
            {
                "customer_phone": "1234567890",
                "language": "te",
                "audio": SimpleUploadedFile(
                    "complaint.webm",
                    b"mocked audio",
                    content_type="audio/webm",
                ),
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["possible_duplicate"])
        self.assertEqual(
            response.json()["possible_duplicate_ticket_number"],
            str(earlier.ticket_number),
        )
        transcribe.assert_called_once()

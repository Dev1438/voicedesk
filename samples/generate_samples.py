import json
import os
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch


SAMPLES_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SAMPLES_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.test.runner import DiscoverRunner

from complaints.denoising import DenoisingResult
from complaints.tts import TTSError


SYNTHETIC_TRANSCRIPT = "मेरा फोन ठीक से काम नहीं कर रहा है।"
SYNTHETIC_TRANSLATION = (
    "My phone is not working properly; the battery is dead."
)
SYNTHETIC_REPLY_TRANSLATION = (
    "आपकी शिकायत प्राप्त हुई है। हम आपके फोन की समस्या पर काम कर रहे हैं।"
)


def make_silent_wav():
    from io import BytesIO

    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 8000)
    return buffer.getvalue()


def write_json(filename, data):
    path = SAMPLES_DIR / filename
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def redact_ticket_numbers(value):
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED-TICKET-UUID]"
                if key == "ticket_number" and isinstance(item, str)
                else redact_ticket_numbers(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_ticket_numbers(item) for item in value]
    return value


def generate_samples():
    audio_bytes = make_silent_wav()
    (SAMPLES_DIR / "synthetic_complaint.wav").write_bytes(audio_bytes)

    media_directory = tempfile.TemporaryDirectory()
    try:
        with override_settings(MEDIA_ROOT=media_directory.name):
            client = Client()
            with (
                patch(
                    "complaints.views.denoise_complaint_audio",
                    return_value=DenoisingResult(audio_bytes=audio_bytes),
                ),
                patch(
                    "complaints.views.transcribe_complaint",
                    return_value=SYNTHETIC_TRANSCRIPT,
                ),
                patch(
                    "complaints.views.translate_to_english",
                    return_value=SYNTHETIC_TRANSLATION,
                ),
            ):
                submission = client.post(
                    "/api/complaints/",
                    {
                        "customer_phone": "5550100199",
                        "language": "hi",
                        "audio": SimpleUploadedFile(
                            "synthetic_complaint.wav",
                            audio_bytes,
                            content_type="audio/wav",
                        ),
                    },
                )

            if submission.status_code != 201:
                raise RuntimeError(
                    "Complaint sample generation failed with HTTP "
                    f"{submission.status_code}: {submission.content.decode()}"
                )
            submission_data = submission.json()
            ticket_number = submission_data["ticket_number"]

            write_json(
                "complaint_submission_response.json",
                redact_ticket_numbers(submission_data),
            )
            write_json(
                "classification_response.json",
                {
                    key: submission_data[key]
                    for key in (
                        "classification_status",
                        "category",
                        "priority",
                        "summary",
                    )
                },
            )

            user_model = get_user_model()
            agent = user_model.objects.create_user(
                username="sample-agent",
                is_staff=True,
            )
            client.force_login(agent)
            with (
                patch(
                    "complaints.views.translate_from_english",
                    return_value=SYNTHETIC_REPLY_TRANSLATION,
                ),
                patch(
                    "complaints.views.synthesize_reply",
                    side_effect=TTSError("Mocked TTS unavailable"),
                ),
            ):
                reply_response = client.post(
                    f"/api/complaints/{ticket_number}/replies/",
                    data=json.dumps(
                        {"english_text": "We have received your complaint."}
                    ),
                    content_type="application/json",
                )

            if reply_response.status_code != 201:
                raise RuntimeError(
                    "Agent reply sample generation failed with HTTP "
                    f"{reply_response.status_code}: "
                    f"{reply_response.content.decode()}"
                )
            write_json("agent_reply_response.json", reply_response.json())

            lookup = client.get(f"/api/complaints/{ticket_number}/")
            if lookup.status_code != 200:
                raise RuntimeError(
                    "Ticket lookup sample generation failed with HTTP "
                    f"{lookup.status_code}: {lookup.content.decode()}"
                )
            write_json(
                "ticket_lookup_response.json",
                redact_ticket_numbers(lookup.json()),
            )
    finally:
        media_directory.cleanup()

    print("Generated four UTF-8 JSON outputs and synthetic_complaint.wav.")
    print("All provider calls were mocked; no live provider requests were made.")


def main():
    runner = DiscoverRunner(verbosity=0, interactive=False)
    runner.setup_test_environment()
    try:
        database_config = runner.setup_databases()
        try:
            generate_samples()
        finally:
            runner.teardown_databases(database_config)
    finally:
        runner.teardown_test_environment()


if __name__ == "__main__":
    main()

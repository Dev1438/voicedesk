import tempfile
from unittest.mock import Mock, patch

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import Complaint
from .transcription import TranscriptionError, transcribe_complaint


class ComplaintTranscriptionTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.media_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            AUDICLABS_API_KEY="unit-test-key",
        )
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

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
        post.return_value = response

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
            "complaint.webm",
        )
        self.assertNotIn("language", post.call_args.kwargs["data"])

    @patch("complaints.transcription.requests.post")
    def test_http_failure_keeps_complaint_and_audio(self, post):
        response = Mock(status_code=422, ok=False)
        response.headers = {"X-Request-Id": "request-invalid-audio"}
        response.json.return_value = {
            "detail": "invalid_audio",
        }
        post.return_value = response

        result = self._submit_complaint()

        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()["transcription_status"], "failed")
        self.assertIn("HTTP 422", result.json()["transcription_error"])
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
                "original_transcript",
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


class AgentQueueTests(TestCase):
    def setUp(self):
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
            "language",
            "original_transcript",
            "english_translation",
            "category",
            "priority",
            "summary",
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

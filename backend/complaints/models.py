
import uuid

from django.db import models


class Complaint(models.Model):
    class ProcessingStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        TRANSCRIPTION_FAILED = "transcription_failed", "Transcription Failed"
        TRANSLATION_UNAVAILABLE = "translation_unavailable", "Translation Unavailable"
        TRANSLATION_FAILED = "translation_failed", "Translation Failed"

    class ClassificationStatus(models.TextChoices):
        UNREVIEWED = "unreviewed", "Unreviewed"
        SUGGESTED = "suggested", "Suggested"
        HUMAN_CONFIRMED = "human_confirmed", "Human Confirmed"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        PROCESSING = "processing", "Processing"
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    ticket_number = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    customer_phone = models.CharField(max_length=10)
    language = models.CharField(max_length=20, default="te")
    audio = models.FileField(
        upload_to="complaints/audio/",
        blank=True,
    )
    photo = models.ImageField(
        upload_to="complaints/photos/",
        blank=True,
    )
    original_transcript = models.TextField(blank=True)
    english_translation = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    priority = models.CharField(max_length=20, default="normal")
    summary = models.TextField(blank=True)
    processing_status = models.CharField(
        max_length=32,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
    )
    classification_status = models.CharField(
        max_length=20,
        choices=ClassificationStatus.choices,
        default=ClassificationStatus.UNREVIEWED,
    )
    possible_duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
        related_name="possible_duplicates",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SUBMITTED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Ticket {self.ticket_number} - {self.status}"

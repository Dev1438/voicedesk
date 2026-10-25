
import logging
import re

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .models import Complaint
from .processing import suggest_complaint_details
from .translation import (
    TranslationError,
    TranslationUnavailable,
    translate_to_english,
)
from .transcription import TranscriptionError, transcribe_complaint


logger = logging.getLogger(__name__)


def _complaint_payload(complaint):
    return {
        "ticket_number": str(complaint.ticket_number),
        "status": complaint.status,
        "language": complaint.language,
        "original_transcript": complaint.original_transcript,
        "english_translation": complaint.english_translation,
        "category": complaint.category,
        "priority": complaint.priority,
        "summary": complaint.summary,
        "processing_status": complaint.processing_status,
        "classification_status": complaint.classification_status,
        "created_at": complaint.created_at.isoformat(),
        "updated_at": complaint.updated_at.isoformat(),
    }


@csrf_exempt
@require_http_methods(["GET", "POST"])
def complaints_collection(request):
    if request.method == "GET":
        return list_complaints(request)
    return create_complaint(request)


@require_GET
def list_complaints(request):
    complaints = Complaint.objects.all()
    filter_fields = ("status", "category", "priority", "language")
    for field in filter_fields:
        value = request.GET.get(field, "").strip()
        if value:
            complaints = complaints.filter(**{field: value})

    complaints = complaints.order_by("-created_at", "-id")
    return JsonResponse(
        {"complaints": [_complaint_payload(item) for item in complaints]}
    )


@csrf_exempt
@require_POST
def create_complaint(request):
    phone = request.POST.get("customer_phone", "").strip()
    language = request.POST.get("language", "te").strip()

    audio = request.FILES.get("audio")
    photo = request.FILES.get("photo")

    if not re.fullmatch(r"\d{10}", phone):
        return JsonResponse(
            {"error": "Enter a valid 10-digit phone number."},
            status=400,
        )

    if not language or len(language) > 20:
        return JsonResponse(
            {"error": "Enter a valid language code."},
            status=400,
        )

    if not audio:
        return JsonResponse(
            {"error": "Please upload your voice recording."},
            status=400,
        )

    if not audio.content_type or not audio.content_type.startswith("audio/"):
        return JsonResponse(
            {"error": "Please upload a valid audio file."},
            status=400,
        )

    if photo and (
        not photo.content_type
        or not photo.content_type.startswith("image/")
    ):
        return JsonResponse(
            {"error": "Please upload a valid image."},
            status=400,
        )

    complaint = Complaint(
        customer_phone=phone,
        language=language,
        audio=audio,
    )

    if photo:
        complaint.photo = photo

    complaint.save()

    try:
        complaint.original_transcript = transcribe_complaint(complaint)
    except TranscriptionError as error:
        complaint.processing_status = (
            Complaint.ProcessingStatus.TRANSCRIPTION_FAILED
        )
        complaint.save(update_fields=["processing_status", "updated_at"])
        return JsonResponse(
            {
                "message": "Complaint saved, but transcription failed.",
                "ticket_number": str(complaint.ticket_number),
                "status": complaint.status,
                "transcription_status": "failed",
                "processing_status": complaint.processing_status,
                "transcription_error": str(error),
            },
            status=201,
        )

    suggestions = suggest_complaint_details(complaint.original_transcript)
    complaint.category = suggestions.category
    complaint.priority = suggestions.priority
    complaint.summary = suggestions.summary
    complaint.classification_status = Complaint.ClassificationStatus.SUGGESTED

    try:
        complaint.english_translation = translate_to_english(
            complaint.original_transcript,
            complaint.language,
        )
        complaint.processing_status = Complaint.ProcessingStatus.COMPLETED
        message = "Complaint transcribed, translated, and suggestions generated."
    except TranslationUnavailable as error:
        complaint.english_translation = ""
        complaint.processing_status = (
            Complaint.ProcessingStatus.TRANSLATION_UNAVAILABLE
        )
        message = (
            "Complaint transcribed and suggestions generated; English "
            "translation is unavailable because no provider is configured."
        )
        logger.info(
            "Translation unavailable for complaint_id=%s reason=%s",
            complaint.pk,
            str(error),
        )
    except TranslationError as error:
        complaint.english_translation = ""
        complaint.processing_status = (
            Complaint.ProcessingStatus.TRANSLATION_FAILED
        )
        message = (
            "Complaint transcribed and suggestions generated, but English "
            "translation failed."
        )
        logger.error(
            "Translation failed for complaint_id=%s error_type=%s",
            complaint.pk,
            type(error).__name__,
        )

    complaint.save(
        update_fields=[
            "original_transcript",
            "english_translation",
            "category",
            "priority",
            "summary",
            "processing_status",
            "classification_status",
            "updated_at",
        ]
    )

    return JsonResponse(
        {
            "message": message,
            "ticket_number": str(complaint.ticket_number),
            "status": complaint.status,
            "transcription_status": "completed",
            "processing_status": complaint.processing_status,
            "classification_status": complaint.classification_status,
            "original_transcript": complaint.original_transcript,
            "english_translation": complaint.english_translation,
            "category": complaint.category,
            "priority": complaint.priority,
            "summary": complaint.summary,
        },
        status=201,
    )


@require_GET
def get_complaint(request, ticket_number):
    complaint = get_object_or_404(Complaint, ticket_number=ticket_number)

    return JsonResponse(
        {
            "ticket_number": str(complaint.ticket_number),
            "status": complaint.status,
            "original_transcript": complaint.original_transcript,
            "processing_status": complaint.processing_status,
            "english_translation": complaint.english_translation,
            "category": complaint.category,
            "priority": complaint.priority,
            "summary": complaint.summary,
            "classification_status": complaint.classification_status,
            "created_at": complaint.created_at.isoformat(),
            "updated_at": complaint.updated_at.isoformat(),
        }
    )


@csrf_exempt
@require_POST
def update_complaint_status(request, ticket_number):
    complaint = get_object_or_404(Complaint, ticket_number=ticket_number)
    status = request.POST.get("status", "").strip()
    if status not in Complaint.Status.values:
        valid_statuses = ", ".join(Complaint.Status.values)
        return JsonResponse(
            {
                "error": (
                    f"Invalid status. Choose one of: {valid_statuses}."
                )
            },
            status=400,
        )

    complaint.status = status
    complaint.save(update_fields=["status", "updated_at"])
    return JsonResponse(
        {
            "ticket_number": str(complaint.ticket_number),
            "status": complaint.status,
            "updated_at": complaint.updated_at.isoformat(),
        }
    )

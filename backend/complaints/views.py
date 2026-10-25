
import json
import logging
import re
from functools import wraps

from django.contrib.auth import authenticate, login, logout
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .models import AgentReply, Complaint
from .duplicate_detection import find_possible_duplicate
from .denoising import DenoisingError, denoise_complaint_audio
from .processing import suggest_complaint_details
from .translation import (
    TranslationError,
    TranslationUnavailable,
    translate_from_english,
    translate_to_english,
)
from .transcription import TranscriptionError, transcribe_complaint
from .tts import TTSError, synthesize_reply


logger = logging.getLogger(__name__)


def agent_required(view):
    @wraps(view)
    def wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse(
                {"error": "Agent login is required."},
                status=401,
            )
        if not request.user.is_staff:
            return JsonResponse(
                {"error": "Agent access is required."},
                status=403,
            )
        return view(request, *args, **kwargs)

    return wrapped_view


def _agent_user_payload(user):
    return {
        "username": user.get_username(),
        "is_staff": user.is_staff,
    }


@ensure_csrf_cookie
@require_GET
def auth_session(request):
    user = request.user
    is_agent = user.is_authenticated and user.is_staff
    return JsonResponse(
        {
            "authenticated": is_agent,
            "user": _agent_user_payload(user) if is_agent else None,
        }
    )


@require_POST
def agent_login(request):
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"error": "Send a valid JSON request."},
                status=400,
            )
        if not isinstance(payload, dict):
            return JsonResponse(
                {"error": "Send login details as a JSON object."},
                status=400,
            )
        username = payload.get("username")
        password = payload.get("password")
    else:
        username = request.POST.get("username")
        password = request.POST.get("password")

    if not isinstance(username, str) or not isinstance(password, str):
        return JsonResponse(
            {"error": "Enter an agent username and password."},
            status=400,
        )

    user = authenticate(
        request,
        username=username,
        password=password,
    )
    if user is None or not user.is_staff:
        return JsonResponse(
            {"error": "Invalid credentials or agent access is not enabled."},
            status=401,
        )

    login(request, user)
    return JsonResponse(
        {
            "authenticated": True,
            "user": _agent_user_payload(user),
        }
    )


@require_POST
def agent_logout(request):
    logout(request)
    return JsonResponse({"authenticated": False, "user": None})


def _reply_payload(request, reply):
    audio_url = (
        request.build_absolute_uri(reply.audio.url)
        if reply.audio
        else None
    )
    return {
        "id": reply.pk,
        "english_text": reply.english_text,
        "translated_text": reply.translated_text,
        "customer_language": reply.customer_language,
        "translation_status": reply.translation_status,
        "audio_status": reply.audio_status,
        "audio_url": audio_url,
        "created_at": reply.created_at.isoformat(),
    }


def _complaint_payload(request, complaint):
    return {
        "ticket_number": str(complaint.ticket_number),
        "status": complaint.status,
        "language": complaint.language,
        "denoising_status": complaint.denoising_status,
        "original_transcript": complaint.original_transcript,
        "english_translation": complaint.english_translation,
        "category": complaint.category,
        "priority": complaint.priority,
        "summary": complaint.summary,
        "processing_status": complaint.processing_status,
        "classification_status": complaint.classification_status,
        "possible_duplicate": complaint.possible_duplicate_of_id is not None,
        "possible_duplicate_ticket_number": (
            str(complaint.possible_duplicate_of.ticket_number)
            if complaint.possible_duplicate_of_id
            else None
        ),
        "agent_replies": [
            _reply_payload(request, reply)
            for reply in complaint.agent_replies.order_by("created_at", "pk")
        ],
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
@agent_required
def list_complaints(request):
    complaints = (
        Complaint.objects.select_related("possible_duplicate_of")
        .prefetch_related("agent_replies")
    )
    filter_fields = ("status", "category", "priority", "language")
    for field in filter_fields:
        value = request.GET.get(field, "").strip()
        if value:
            complaints = complaints.filter(**{field: value})

    complaints = complaints.order_by("-created_at", "-id")
    return JsonResponse(
        {
            "complaints": [
                _complaint_payload(request, item)
                for item in complaints
            ]
        }
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

    denoising_error = ""
    cleaned_audio = None
    try:
        denoising_result = denoise_complaint_audio(complaint)
        cleaned_audio = denoising_result.audio_bytes
        complaint.denoising_status = Complaint.DenoisingStatus.COMPLETED
    except DenoisingError as error:
        complaint.denoising_status = Complaint.DenoisingStatus.FAILED
        denoising_error = str(error)
        logger.warning(
            "Complaint denoising failed; original audio will be transcribed "
            "complaint_id=%s",
            complaint.pk,
        )

    try:
        complaint.original_transcript = transcribe_complaint(
            complaint,
            audio_bytes=cleaned_audio,
        )
    except TranscriptionError as error:
        complaint.processing_status = (
            Complaint.ProcessingStatus.TRANSCRIPTION_FAILED
        )
        complaint.save(
            update_fields=[
                "denoising_status",
                "processing_status",
                "updated_at",
            ]
        )
        return JsonResponse(
            {
                "message": (
                    "Complaint saved. Denoising failed, the original "
                    "recording was used, and transcription also failed."
                    if denoising_error
                    else "Complaint saved, but transcription failed."
                ),
                "ticket_number": str(complaint.ticket_number),
                "status": complaint.status,
                "transcription_status": "failed",
                "processing_status": complaint.processing_status,
                "denoising_status": complaint.denoising_status,
                "denoising_error": (
                    "Denoising failed; the original recording was used."
                    if denoising_error
                    else None
                ),
                "possible_duplicate": False,
                "possible_duplicate_ticket_number": None,
                "transcription_error": (
                    "Transcription also failed. Please try again later."
                    if denoising_error
                    else str(error)
                ),
            },
            status=201,
        )

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

    suggestion_text = (
        complaint.english_translation.strip()
        or complaint.original_transcript
    )
    suggestions = suggest_complaint_details(suggestion_text)
    complaint.category = suggestions.category
    complaint.priority = suggestions.priority
    complaint.summary = suggestions.summary
    complaint.classification_status = Complaint.ClassificationStatus.SUGGESTED

    if denoising_error:
        message = (
            "Denoising failed; the original recording was used. " + message
        )

    possible_duplicate = find_possible_duplicate(complaint)
    if possible_duplicate:
        complaint.possible_duplicate_of = possible_duplicate

    complaint.save(
        update_fields=[
            "original_transcript",
            "english_translation",
            "category",
            "priority",
            "summary",
            "processing_status",
            "denoising_status",
            "classification_status",
            "possible_duplicate_of",
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
            "denoising_status": complaint.denoising_status,
            "denoising_error": denoising_error or None,
            "classification_status": complaint.classification_status,
            "possible_duplicate": complaint.possible_duplicate_of_id is not None,
            "possible_duplicate_ticket_number": (
                str(complaint.possible_duplicate_of.ticket_number)
                if complaint.possible_duplicate_of_id
                else None
            ),
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
            "denoising_status": complaint.denoising_status,
            "original_transcript": complaint.original_transcript,
            "processing_status": complaint.processing_status,
            "english_translation": complaint.english_translation,
            "category": complaint.category,
            "priority": complaint.priority,
            "summary": complaint.summary,
            "classification_status": complaint.classification_status,
            "possible_duplicate": complaint.possible_duplicate_of_id is not None,
            "possible_duplicate_ticket_number": (
                str(complaint.possible_duplicate_of.ticket_number)
                if complaint.possible_duplicate_of_id
                else None
            ),
            "agent_replies": [
                _reply_payload(request, reply)
                for reply in complaint.agent_replies.order_by(
                    "created_at",
                    "pk",
                )
            ],
            "created_at": complaint.created_at.isoformat(),
            "updated_at": complaint.updated_at.isoformat(),
        }
    )


@require_POST
@agent_required
def create_agent_reply(request, ticket_number):
    complaint = get_object_or_404(Complaint, ticket_number=ticket_number)

    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"error": "Send a valid JSON request."},
                status=400,
            )
        if not isinstance(payload, dict):
            return JsonResponse(
                {"error": "Send the reply as a JSON object."},
                status=400,
            )
        english_text = payload.get("english_text")
    else:
        english_text = request.POST.get("english_text")

    if not isinstance(english_text, str) or not english_text.strip():
        return JsonResponse(
            {"error": "Enter the English reply text."},
            status=400,
        )

    reply = AgentReply.objects.create(
        complaint=complaint,
        english_text=english_text.strip(),
        customer_language=complaint.language,
    )

    try:
        reply.translated_text = translate_from_english(
            reply.english_text,
            reply.customer_language,
        )
        reply.translation_status = AgentReply.TranslationStatus.COMPLETED
        reply.save(
            update_fields=[
                "translated_text",
                "translation_status",
                "updated_at",
            ]
        )
    except (TranslationError, TranslationUnavailable) as error:
        reply.translation_status = AgentReply.TranslationStatus.FAILED
        reply.save(update_fields=["translation_status", "updated_at"])
        logger.warning(
            "Agent reply translation failed complaint_id=%s reply_id=%s "
            "error_type=%s",
            complaint.pk,
            reply.pk,
            type(error).__name__,
        )
        return JsonResponse(
            {
                "message": (
                    "English reply saved, but customer-language translation "
                    "failed. Audio was not generated."
                ),
                "reply": _reply_payload(request, reply),
            },
            status=201,
        )

    try:
        audio_bytes = synthesize_reply(
            reply.translated_text,
            reply.customer_language,
        )
    except TTSError as error:
        reply.audio_status = AgentReply.AudioStatus.FAILED
        reply.save(update_fields=["audio_status", "updated_at"])
        logger.warning(
            "Agent reply audio generation failed complaint_id=%s "
            "reply_id=%s error_type=%s",
            complaint.pk,
            reply.pk,
            type(error).__name__,
        )
        message = (
            "Reply saved and translated, but audio generation failed."
        )
    else:
        reply.audio.save(
            f"reply-{reply.pk}.mp3",
            ContentFile(audio_bytes),
            save=False,
        )
        reply.audio_status = AgentReply.AudioStatus.COMPLETED
        reply.save(
            update_fields=["audio", "audio_status", "updated_at"]
        )
        message = "Reply saved, translated, and audio generated."

    return JsonResponse(
        {
            "message": message,
            "reply": _reply_payload(request, reply),
        },
        status=201,
    )


@require_POST
@agent_required
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

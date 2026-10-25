
import re

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Complaint


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

    return JsonResponse(
        {
            "message": "Complaint created successfully.",
            "ticket_number": str(complaint.ticket_number),
            "status": complaint.status,
        },
        status=201,
    )

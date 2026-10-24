import json
import re
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from .models import Complaint


@csrf_exempt
@require_POST
def create_complaint(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "Request body must contain valid JSON."},
            status=400,
        )

    phone = str(data.get("customer_phone", "")).strip()
    language = str(data.get("language", "te")).strip()

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

    complaint = Complaint.objects.create(
        customer_phone=phone,
        language=language,
    )

    return JsonResponse(
        {
            "message": "Complaint created successfully.",
            "ticket_number": str(complaint.ticket_number),
            "status": complaint.status,
        },
        status=201,
    )


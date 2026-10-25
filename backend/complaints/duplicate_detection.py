import re
from datetime import timedelta

from .models import Complaint


def normalize_complaint_text(text):
    normalized = re.sub(r"[^\w\s]", " ", text.casefold())
    return " ".join(normalized.split())


def complaint_text_for_comparison(complaint):
    return (
        complaint.english_translation.strip()
        or complaint.original_transcript
    )


def find_possible_duplicate(complaint):
    current_text = normalize_complaint_text(
        complaint_text_for_comparison(complaint)
    )
    if not current_text:
        return None

    window_start = complaint.created_at - timedelta(days=7)
    earlier_complaints = (
        Complaint.objects.filter(
            customer_phone=complaint.customer_phone,
            created_at__gte=window_start,
            created_at__lt=complaint.created_at,
        )
        .exclude(pk=complaint.pk)
        .order_by("-created_at", "-id")
    )

    for earlier in earlier_complaints:
        earlier_text = normalize_complaint_text(
            complaint_text_for_comparison(earlier)
        )
        if earlier_text and earlier_text == current_text:
            return earlier

    return None

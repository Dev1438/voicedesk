import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ComplaintSuggestions:
    category: str
    priority: str
    summary: str


_CATEGORY_KEYWORDS = {
    "billing": {
        "bill",
        "billing",
        "charged",
        "charge",
        "payment",
        "refund",
        "invoice",
    },
    "account": {
        "account",
        "login",
        "password",
        "profile",
        "sign in",
    },
    "technical": {
        "app",
        "application",
        "bug",
        "crash",
        "error",
        "internet",
        "website",
    },
    "delivery": {
        "delivery",
        "deliver",
        "shipment",
        "order",
        "parcel",
        "courier",
    },
    "service": {
        "agent",
        "customer care",
        "service",
        "staff",
        "support",
    },
}

_HIGH_PRIORITY_TERMS = {
    "urgent",
    "immediately",
    "asap",
    "emergency",
    "unsafe",
    "danger",
}
_CATEGORY_CONFIDENCE_TERMS = {
    "billing": {
        "bill",
        "billing",
        "charge",
        "charged",
        "invoice",
        "payment",
        "refund",
    },
    "account": {"account", "login", "password"},
    "technical": {"crash", "bug", "error"},
    "delivery": {"delivery", "deliver", "shipment", "parcel", "courier"},
    "service": {"customer care"},
}


def suggest_complaint_details(transcript):
    clean_transcript = re.sub(r"\s+", " ", transcript).strip()
    normalized = clean_transcript.lower()
    words = set(re.findall(r"[a-z]+", normalized))
    category_scores = {
        category: sum(
            1
            for keyword in keywords
            if keyword in normalized
            and (
                " " in keyword
                or keyword in words
            )
        )
        for category, keywords in _CATEGORY_KEYWORDS.items()
    }

    suggested_category = max(
        category_scores,
        key=category_scores.get,
        default="",
    )
    category_score = category_scores.get(suggested_category, 0)
    category_confidence_terms = _CATEGORY_CONFIDENCE_TERMS.get(
        suggested_category,
        set(),
    )
    has_strong_category_term = any(
        term in normalized
        and (" " in term or term in words)
        for term in category_confidence_terms
    )
    if category_score < 2 and not has_strong_category_term:
        suggested_category = "Uncategorized"

    priority = (
        "high"
        if words.intersection(_HIGH_PRIORITY_TERMS)
        else "normal"
    )

    summary_match = re.search(r"[^.!?]+[.!?]?", clean_transcript)
    summary = summary_match.group(0).strip() if summary_match else ""
    if len(summary) > 240:
        summary = summary[:237].rsplit(" ", 1)[0] + "..."
    if not summary:
        summary = "Summary unavailable."

    return ComplaintSuggestions(
        category=suggested_category,
        priority=priority,
        summary=summary,
    )


from django.contrib import admin

from .models import Complaint


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = (
        "ticket_number",
        "customer_phone",
        "category",
        "priority",
        "status",
        "created_at",
    )
    list_filter = ("status", "category", "priority", "language")
    search_fields = ("customer_phone", "ticket_number")
    readonly_fields = ("ticket_number", "created_at", "updated_at")

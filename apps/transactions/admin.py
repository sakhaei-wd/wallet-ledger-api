from django.contrib import admin

from apps.transactions.models import IdempotencyKey, Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "type",
        "status",
        "amount",
        "currency",
        "from_wallet",
        "to_wallet",
        "initiated_by",
        "external_id",
        "created_at",
    )
    list_filter = ("type", "status", "currency")
    search_fields = ("external_id", "initiated_by__username", "failure_reason")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-id",)


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "key", "request_path", "response_status_code", "created_at")
    search_fields = ("key", "user__username", "request_path")
    readonly_fields = ("created_at",)
    ordering = ("-id",)

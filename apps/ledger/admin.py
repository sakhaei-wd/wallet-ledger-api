from django.contrib import admin

from apps.ledger.models import LedgerEntry


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "wallet",
        "transaction",
        "entry_type",
        "amount",
        "balance_after",
        "locked_balance_after",
        "created_at",
    )
    list_filter = ("entry_type",)
    search_fields = ("wallet__user__username", "transaction__id")
    readonly_fields = (
        "wallet",
        "transaction",
        "entry_type",
        "amount",
        "balance_after",
        "locked_balance_after",
        "created_at",
    )
    ordering = ("-id",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

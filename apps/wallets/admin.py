from django.contrib import admin

from apps.wallets.models import Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "currency",
        "available_balance",
        "locked_balance",
        "updated_at",
    )
    list_filter = ("currency",)
    search_fields = ("user__username", "user__id")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("user_id", "currency")

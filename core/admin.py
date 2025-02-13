from django.contrib import admin
from .models import User, AccountingFirm, Document, SubscriptionPlan, AccountantSubscription
from django.utils import timezone
from datetime import timedelta

@admin.register(AccountingFirm)
class AccountingFirmAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'get_clients_count')
    search_fields = ('name', 'owner__email', 'clients__email')
    filter_horizontal = ('clients',)

    def get_clients_count(self, obj):
        return obj.clients.count()
    get_clients_count.short_description = 'Müşteri Sayısı'

@admin.register(AccountantSubscription)
class AccountantSubscriptionAdmin(admin.ModelAdmin):
    list_display = ['accountant', 'plan', 'status', 'is_special', 'start_date', 'end_date']
    list_filter = ['status', 'is_special', 'plan']
    search_fields = ['accountant__email', 'special_note']
    
    def save_model(self, request, obj, form, change):
        if obj.is_special and not obj.status == 'active':
            obj.status = 'active'
            obj.start_date = timezone.now()
            if not obj.end_date:  # Eğer bitiş tarihi belirtilmemişse
                obj.end_date = timezone.now() + timedelta(days=365)  # 1 yıl ücretsiz
        super().save_model(request, obj, form, change)

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'plan_type', 'base_price', 'base_client_limit', 'is_active']
    list_filter = ['plan_type', 'is_active']
    search_fields = ['name']

from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils import timezone
from django.core.files.storage import default_storage
import uuid

class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email adresi zorunludur')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)

class User(AbstractUser):
    USER_TYPE_CHOICES = (
        ('accountant', 'Muhasebeci'),
        ('client', 'Mükellef'),
    )
    
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    otp = models.CharField(max_length=6, null=True, blank=True)
    otp_created_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def __str__(self):
        return self.email

    def soft_delete(self):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save()

    def restore(self):
        self.is_active = True
        self.deleted_at = None
        self.save()

class AccountingFirm(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_firm')
    clients = models.ManyToManyField(User, related_name='accounting_firms')

    def __str__(self):
        return self.name

def document_file_path(instance, filename):
    # Dosya adını temizle ve benzersiz yap
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4().hex}.{ext}"
    # Yolu oluştur: documents/user_id/yıl/ay/dosya
    return f"documents/{instance.uploaded_by.id}/{timezone.now().strftime('%Y/%m')}/{filename}"

class Document(models.Model):
    DOCUMENT_TYPES = [
        ('invoice', 'Fatura'),
        ('receipt', 'Fiş'),
        ('expense', 'Gider Pusulası'),
        ('contract', 'Sözleşme'),
        ('other', 'Diğer'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Bekliyor'),
        ('processed', 'İşlendi'),
    ]

    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES)
    file = models.FileField(
        upload_to=document_file_path,
        storage=default_storage
    )
    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    vat_rate = models.DecimalField(max_digits=4, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    processed_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='processed_documents'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.document_type} - {self.date}"

class SubscriptionPlan(models.Model):
    PLAN_TYPE_CHOICES = (
        ('free', 'Ücretsiz'),
        ('trial', 'Deneme'),
        ('paid', 'Ücretli'),
    )

    name = models.CharField(max_length=100)
    plan_type = models.CharField(max_length=20, choices=PLAN_TYPE_CHOICES, default='paid')
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    base_client_limit = models.IntegerField()
    price_per_extra_client = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField()
    is_active = models.BooleanField(default=True)
    trial_days = models.IntegerField(default=0)  # Deneme süresi (gün)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def calculate_price(self, client_count):
        if self.plan_type in ['free', 'trial']:
            return 0
        
        if client_count <= self.base_client_limit:
            return self.base_price
        
        extra_clients = client_count - self.base_client_limit
        extra_cost = extra_clients * self.price_per_extra_client
        return self.base_price + extra_cost

    def __str__(self):
        if self.plan_type == 'free':
            return f"{self.name} - Ücretsiz"
        elif self.plan_type == 'trial':
            return f"{self.name} - {self.trial_days} Gün Deneme"
        return f"{self.name} - {self.base_price}TL/ay (+ {self.price_per_extra_client}TL/müşteri)"

class AccountantSubscription(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Beklemede'),
        ('active', 'Aktif'),
        ('cancelled', 'İptal Edildi'),
        ('expired', 'Süresi Doldu'),
    )

    accountant = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    client_limit = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    paytr_subscription_id = models.CharField(max_length=100, null=True, blank=True)
    is_special = models.BooleanField(default=False)  # Özel ücretsiz kullanım hakkı
    special_note = models.TextField(blank=True, null=True)  # Özel durum notu
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.accountant.email} - {self.plan.name}"

    def is_active(self):
        if self.status != 'active':
            return False
        if self.end_date and self.end_date < timezone.now():
            self.status = 'expired'
            self.save()
            return False
        return True

    def get_remaining_client_slots(self):
        current_clients = self.accountant.accounting_firms.first().clients.count()
        return self.client_limit - current_clients

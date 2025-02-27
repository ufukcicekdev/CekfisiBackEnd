from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils import timezone
from django.core.files.storage import default_storage
import uuid
from django.core.exceptions import ValidationError

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
    username = models.CharField(max_length=150, null=True, blank=True)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    otp = models.CharField(max_length=6, null=True, blank=True)
    otp_created_at = models.DateTimeField(null=True, blank=True)

    # Accountant profil bilgileri
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    district = models.CharField(max_length=100, blank=True, null=True)  # İlçe
    about = models.TextField(blank=True, null=True)  # Hakkında/Biyografi
    experience_years = models.IntegerField(default=0)  # Deneyim yılı
    title = models.CharField(max_length=100, blank=True, null=True)  # Ünvan
    company_name = models.CharField(max_length=200, blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    profile_image = models.ImageField(upload_to='profile_images/', blank=True, null=True)
    specializations = models.JSONField(default=list, blank=True)  # Uzmanlık alanları
    is_featured = models.BooleanField(default=False)  # Öne çıkan mali müşavir
    rating = models.FloatField(default=0.0)  # Değerlendirme puanı
    review_count = models.IntegerField(default=0)  # Değerlendirme sayısı

    # Client için ek alanlar
    tax_number = models.CharField(max_length=11, blank=True, null=True)  # VKN
    identity_number = models.CharField(max_length=11, blank=True, null=True)  # TCKN
    company_type = models.CharField(max_length=20, choices=(
        ('individual', 'Şahıs'),
        ('limited', 'Limited Şirket'),
        ('incorporated', 'Anonim Şirket'),
        ('other', 'Diğer')
    ), blank=True, null=True)
    company_title = models.CharField(max_length=255, blank=True, null=True)  # Firma Ünvanı

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

    def clean(self):
        if self.user_type == 'client':
            if not (self.tax_number or self.identity_number):
                raise ValidationError('VKN veya TCKN zorunludur')

    class Meta:
        db_table = 'users'
        verbose_name = 'Kullanıcı'
        verbose_name_plural = 'Kullanıcılar'

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
    DOCUMENT_TYPES = (
        ('invoice', 'Fatura'),
        ('receipt', 'Fiş'),
        ('contract', 'Sözleşme'),
        ('other', 'Diğer'),
    )
    
    STATUS_CHOICES = (
        ('pending', 'Beklemede'),
        ('processing', 'İşleniyor'),
        ('completed', 'Tamamlandı'),
        ('rejected', 'Reddedildi'),
    )
    
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES)
    file = models.FileField(upload_to=document_file_path)
    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='uploaded_documents')
    processed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_documents')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    analyzed_data = models.JSONField(null=True, blank=True)  # OpenAI analiz sonuçları için

    def __str__(self):
        return f"{self.get_document_type_display()} - {self.date}"

    class Meta:
        ordering = ['-created_at']

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

class City(models.Model):
    name = models.CharField(max_length=100)
    plate_number = models.CharField(max_length=2, unique=True)  # Plaka kodu

    def __str__(self):
        return f"{self.name} ({self.plate_number})"

    class Meta:
        verbose_name = 'İl'
        verbose_name_plural = 'İller'
        ordering = ['name']

class District(models.Model):
    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name='districts')
    name = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.city.name} - {self.name}"

    class Meta:
        verbose_name = 'İlçe'
        verbose_name_plural = 'İlçeler'
        ordering = ['name']
        unique_together = ['city', 'name']  # Aynı şehirde aynı isimde ilçe olmasın

class ClientDocument(models.Model):
    DOCUMENT_TYPES = (
        ('identity', 'Kimlik Fotokopisi'),
        ('signature', 'İmza Sirküleri'),
        ('tax', 'Vergi Levhası'),
        ('statement', 'Beyanname'),
        ('other', 'Diğer')
    )

    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='documents')
    title = models.CharField(max_length=255)  # Kullanıcının verdiği başlık
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES)
    file = models.FileField(upload_to='client_documents/')
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)  # Belge hala geçerli mi
    expiry_date = models.DateField(null=True, blank=True)  # Varsa geçerlilik süresi
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Müşteri Belgesi'
        verbose_name_plural = 'Müşteri Belgeleri'

    def __str__(self):
        return f"{self.client.email} - {self.title}"

    @property
    def file_url(self):
        if self.file:
            return self.file.url
        return None

    @property
    def file_name(self):
        if self.file:
            return self.file.name.split('/')[-1]
        return None

from rest_framework import serializers
from dj_rest_auth.registration.serializers import RegisterSerializer
from .models import User, AccountingFirm, Document, SubscriptionPlan, AccountantSubscription, ClientDocument
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from allauth.account.adapter import get_adapter
from allauth.account.utils import setup_user_email
from allauth.account.models import EmailAddress, EmailConfirmation
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from .utils import send_email_via_smtp2go  # Utils fonksiyonumuzu import edelim
from django.utils.crypto import get_random_string
from django.utils import timezone
from cities_light.models import City, Region, SubRegion

class CustomRegisterSerializer(RegisterSerializer):
    username = None  # Username alanını kaldır
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)
    phone = serializers.CharField(required=True)
    user_type = serializers.ChoiceField(choices=User.USER_TYPE_CHOICES)

    # Accountant için ek alanlar (opsiyonel)
    address = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(required=False, allow_blank=True)
    district = serializers.CharField(required=False, allow_blank=True)
    about = serializers.CharField(required=False, allow_blank=True)
    experience_years = serializers.IntegerField(required=False)
    title = serializers.CharField(required=False, allow_blank=True)
    company_name = serializers.CharField(required=False, allow_blank=True)
    website = serializers.URLField(required=False, allow_blank=True)
    specializations = serializers.ListField(required=False, default=list)

    def validate_email(self, email):
        # Email adresi zaten kayıtlı mı kontrol et
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError(
                "Bu email adresi zaten kullanılıyor."
            )
        return email

    def validate(self, data):
        if data['password1'] != data['password2']:
            raise serializers.ValidationError({"password2": "Şifreler eşleşmiyor."})
        return data

    def get_cleaned_data(self):
        data = super().get_cleaned_data()
        data.update({
            'first_name': self.validated_data.get('first_name', ''),
            'last_name': self.validated_data.get('last_name', ''),
            'phone': self.validated_data.get('phone', ''),
            'user_type': self.validated_data.get('user_type', ''),
            'username': None,
            # Accountant alanları
            'address': self.validated_data.get('address', ''),
            'city': self.validated_data.get('city', ''),
            'district': self.validated_data.get('district', ''),
            'about': self.validated_data.get('about', ''),
            'experience_years': self.validated_data.get('experience_years', 0),
            'title': self.validated_data.get('title', ''),
            'company_name': self.validated_data.get('company_name', ''),
            'website': self.validated_data.get('website', ''),
            'specializations': self.validated_data.get('specializations', [])
        })
        return data

    def save(self, request):
        adapter = get_adapter()
        user = adapter.new_user(request)
        self.cleaned_data = self.get_cleaned_data()
        
        # Temel alanları ayarla
        user.first_name = self.cleaned_data.get('first_name')
        user.last_name = self.cleaned_data.get('last_name')
        user.phone = self.cleaned_data.get('phone')
        user.user_type = self.cleaned_data.get('user_type')
        user.email = self.cleaned_data.get('email')
        user.username = None

        # Accountant ise ek alanları ayarla
        if user.user_type == 'accountant':
            user.address = self.cleaned_data.get('address')
            user.city = self.cleaned_data.get('city')
            user.district = self.cleaned_data.get('district')
            user.about = self.cleaned_data.get('about')
            user.experience_years = self.cleaned_data.get('experience_years')
            user.title = self.cleaned_data.get('title')
            user.company_name = self.cleaned_data.get('company_name')
            user.website = self.cleaned_data.get('website')
            user.specializations = self.cleaned_data.get('specializations')
        
        adapter.save_user(request, user, self)
        
        try:
            # Email adresi zaten varsa sil
            EmailAddress.objects.filter(user=user).delete()
            
            # Yeni email adresi ekle
            email_address = EmailAddress.objects.create(
                user=user,
                email=user.email,
                primary=True,
                verified=False
            )
            
            # Email doğrulama kodu oluştur
            confirmation = EmailConfirmation.create(email_address)
            confirmation.sent = timezone.now()
            confirmation.save()
            
            # Email doğrulama emaili gönder
            subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Email Doğrulama"
            confirmation_url = f"{settings.FRONTEND_URL}/auth/verify-email/{confirmation.key}"
            
            html_message = render_to_string('email/email_confirmation_message.html', {
                'user': user,
                'activate_url': confirmation_url
            })
            
            text_message = strip_tags(html_message)
            
            # SMTP2GO ile email gönder
            send_email_via_smtp2go(
                to_list=user.email,
                subject=subject,
                html_body=html_message,
                text_body=text_message
            )
            
            print(f"Doğrulama emaili gönderildi: {user.email}")
        except Exception as e:
            print(f"Email gönderimi sırasında hata: {str(e)}")
        
        return user

class ClientDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientDocument
        fields = [
            'id', 'title', 'document_type', 'file', 'description',
            'is_active', 'expiry_date', 'created_at', 'file_url', 'file_name'
        ]
        read_only_fields = ['created_at', 'file_url', 'file_name']

class UserSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 
            'phone', 'user_type',
            # Accountant profil alanları
            'address', 'city', 'district', 'about',
            'experience_years', 'title', 'company_name',
            'website', 'profile_image', 'specializations',
            'rating', 'review_count',
            # Client profil alanları
            'tax_number', 'identity_number', 'company_type', 'company_title',
        ]
        read_only_fields = ['id', 'user_type', 'rating', 'review_count']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        
        if instance.user_type == 'accountant':
            # Accountant için client alanlarını kaldır
            client_fields = [
                'tax_number', 'identity_number', 'company_type', 
                'company_title', 'documents'
            ]
            for field in client_fields:
                data.pop(field, None)
        
        elif instance.user_type == 'client':
            # Client için accountant alanlarını kaldır
            accountant_fields = [
                'address', 'city', 'district', 'about',
                'experience_years', 'title', 'company_name',
                'website', 'profile_image', 'specializations',
                'rating', 'review_count'
            ]
            for field in accountant_fields:
                data.pop(field, None)
        
        return data

    def update(self, instance, validated_data):
        # Temel alanları güncelle
        instance.first_name = validated_data.get('first_name', instance.first_name)
        instance.last_name = validated_data.get('last_name', instance.last_name)
        instance.email = validated_data.get('email', instance.email)
        instance.phone = validated_data.get('phone', instance.phone)

        # Client ise ek alanları güncelle
        if instance.user_type == 'client':
            instance.tax_number = validated_data.get('tax_number', instance.tax_number)
            instance.identity_number = validated_data.get('identity_number', instance.identity_number)
            instance.company_type = validated_data.get('company_type', instance.company_type)
            instance.company_title = validated_data.get('company_title', instance.company_title)
        # Accountant ise ek alanları güncelle
        elif instance.user_type == 'accountant':
            instance.address = validated_data.get('address', instance.address)
            instance.city = validated_data.get('city', instance.city)
            instance.district = validated_data.get('district', instance.district)
            instance.about = validated_data.get('about', instance.about)
            instance.experience_years = validated_data.get('experience_years', instance.experience_years)
            instance.title = validated_data.get('title', instance.title)
            instance.company_name = validated_data.get('company_name', instance.company_name)
            instance.website = validated_data.get('website', instance.website)
            
            # specializations için özel kontrol
            if 'specializations' in validated_data:
                if validated_data['specializations'] == '':
                    instance.specializations = []
                else:
                    instance.specializations = validated_data['specializations']

            # profile_image için özel kontrol
            if 'profile_image' in validated_data:
                instance.profile_image = validated_data['profile_image']

        instance.save()
        return instance

class AccountingFirmSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountingFirm
        fields = ['id', 'name', 'owner', 'clients']
        read_only_fields = ['owner']

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)

class DocumentSerializer(serializers.ModelSerializer):
    receipt_details = serializers.SerializerMethodField(read_only=True)
    file_url = serializers.SerializerMethodField(read_only=True)
    file_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Document
        fields = [
            'id', 'document_type', 'file', 'date', 
            'amount', 'vat_rate', 'status', 'uploaded_by',
            'processed_by', 'created_at', 'updated_at',
            'receipt_details', 'file_url', 'file_name'
        ]
        read_only_fields = ['uploaded_by', 'processed_by', 'status', 'receipt_details', 'file_url', 'file_name']

    def get_receipt_details(self, obj):
        if obj.analyzed_data and isinstance(obj.analyzed_data, dict):
            return {
                'seller_name': obj.analyzed_data.get('seller_name'),
                'date': obj.analyzed_data.get('date'),
                'total_amount': obj.analyzed_data.get('total_amount'),
                'vat_amount': obj.analyzed_data.get('vat_amount'),
                'items': obj.analyzed_data.get('items', [])
            }
        return None

    def get_file_url(self, obj):
        if obj.file:
            return obj.file.url
        return None

    def get_file_name(self, obj):
        if obj.file:
            return obj.file.name.split('/')[-1]
        return None

    def create(self, validated_data):
        instance = super().create(validated_data)
        
        # Eğer belge tipi fiş ise OpenAI analizi yapmayı dene
        if instance.document_type == 'receipt' and instance.file:
            self.analyze_receipt(instance)
        
        return instance

    def analyze_receipt(self, instance):
        try:
            import openai
            import base64
            import json
            
            # API key'i direkt olarak ayarla
            openai.api_key = settings.OPENAI_API_KEY
            
            # Dosyayı oku ve base64'e çevir
            with instance.file.open('rb') as image_file:
                image_data = image_file.read()
                base64_image = base64.b64encode(image_data).decode('utf-8')
                base64_url = f"data:image/jpeg;base64,{base64_image}"
            
            response = openai.ChatCompletion.create(
                model="gpt-4-turbo",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Could you extract the following seller pricing information from this image and return ONLY the JSON data without any explanation? Include: seller_name, date, total_amount, vat_amount, items (array of items with name, quantity, unit_price)"
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": base64_url
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1000
            )
            
            # OpenAI yanıtından JSON string'i çıkar ve parse et
            response_text = response.choices[0].message['content']
            json_str = response_text.strip()
            if json_str.startswith('```json'):
                json_str = json_str[7:]
            if json_str.endswith('```'):
                json_str = json_str[:-3]
            
            # JSON'ı parse et
            analyzed_data = json.loads(json_str.strip())
            
            # Analiz sonucunu kaydet
            instance.analyzed_data = analyzed_data
            
            # Analiz edilen değerleri ilgili alanlara kaydet
            if 'total_amount' in analyzed_data:
                instance.amount = analyzed_data['total_amount']
            
            if 'vat_amount' in analyzed_data and analyzed_data['total_amount']:
                # KDV oranını hesapla: (KDV tutarı / Toplam tutar) * 100
                try:
                    vat_rate = (analyzed_data['vat_amount'] / analyzed_data['total_amount']) * 100
                    instance.vat_rate = round(vat_rate, 2)  # 2 decimal places
                except (ZeroDivisionError, TypeError):
                    pass

            instance.save()
            
        except Exception as e:
            import traceback
            print(f"Fiş analizi sırasında hata oluştu: {str(e)}")
            print(traceback.format_exc())

    def update(self, instance, validated_data):
        # Sadece amount ve vat_rate alanlarının güncellenmesine izin ver
        instance.amount = validated_data.get('amount', instance.amount)
        instance.vat_rate = validated_data.get('vat_rate', instance.vat_rate)
        instance.save()
        return instance

class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ['document_type', 'file', 'date', 'amount', 'vat_rate']

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = 'email'

class SubscriptionPlanSerializer(serializers.ModelSerializer):
    features = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 
            'name', 
            'plan_type', 
            'base_price', 
            'base_client_limit',
            'price_per_extra_client',
            'description',
            'features',
            'is_active'
        ]

    def get_features(self, obj):
        # Örnek özellikler - bunları modelde tanımlayabilir veya burada hardcode edebilirsiniz
        return [
            "Kullanmak için benimle iletişime geçebilirsiniz.",
            "ufukcicek199@gmail.com",
            "info@cekfisi.com"
            
        ]

class SubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer()

    class Meta:
        model = AccountantSubscription
        fields = [
            'id',
            'plan',
            'client_limit',
            'status',
            'start_date',
            'end_date'
        ]

class AccountantSubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)
    
    class Meta:
        model = AccountantSubscription
        fields = [
            'id',
            'accountant',
            'plan',
            'status',
            'paytr_subscription_id',
            'start_date',
            'end_date',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['accountant', 'paytr_subscription_id']

class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(write_only=True)

    def validate_otp(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("OTP sadece rakamlardan oluşmalıdır")
        return value

    def validate_new_password(self, value):
        try:
            validate_password(value)
        except ValidationError as e:
            raise serializers.ValidationError(str(e))
        return value

class AccountantListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name',
            'address', 'city', 'district', 'about',
            'experience_years', 'title', 'company_name',
            'phone', 'website', 'profile_image',
            'specializations', 'rating', 'review_count'
        ]

class CitySerializer(serializers.ModelSerializer):
    class Meta:
        model = City
        fields = ['id', 'name', 'region']

class RegionSerializer(serializers.ModelSerializer):
    """Şehir (İl) Serializer"""
    class Meta:
        model = Region
        fields = ['id', 'name']

class SubRegionSerializer(serializers.ModelSerializer):
    """İlçe Serializer"""
    class Meta:
        model = SubRegion
        fields = ['id', 'name'] 
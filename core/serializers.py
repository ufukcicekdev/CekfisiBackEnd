from rest_framework import serializers
from dj_rest_auth.registration.serializers import RegisterSerializer
from .models import User, AccountingFirm, Document, SubscriptionPlan, AccountantSubscription
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

class CustomRegisterSerializer(RegisterSerializer):
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)
    phone = serializers.CharField(required=True)
    user_type = serializers.ChoiceField(choices=User.USER_TYPE_CHOICES)
    password1 = serializers.CharField(write_only=True, required=True)
    password2 = serializers.CharField(write_only=True, required=True)

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
            'password1': self.validated_data.get('password1', ''),
            'password2': self.validated_data.get('password2', ''),
        })
        return data

    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data.get('first_name')
        user.last_name = self.cleaned_data.get('last_name')
        user.phone = self.cleaned_data.get('phone')
        user.user_type = self.cleaned_data.get('user_type')
        user.save()
        return user

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'first_name', 'last_name', 'phone', 'user_type')
        read_only_fields = ('id', 'user_type')  # user_type değiştirilemesin

    def update(self, instance, validated_data):
        # Update işlemini özelleştirelim
        instance.first_name = validated_data.get('first_name', instance.first_name)
        instance.last_name = validated_data.get('last_name', instance.last_name)
        instance.email = validated_data.get('email', instance.email)
        instance.phone = validated_data.get('phone', instance.phone)
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
    class Meta:
        model = Document
        fields = [
            'id', 'document_type', 'file', 'date', 
            'amount', 'vat_rate', 'status', 'uploaded_by',
            'processed_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ['uploaded_by', 'processed_by', 'status']

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
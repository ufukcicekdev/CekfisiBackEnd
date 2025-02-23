from django.shortcuts import render, get_object_or_404
from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import User, AccountingFirm, Document, SubscriptionPlan, AccountantSubscription, ClientDocument
from allauth.account.models import EmailAddress, EmailConfirmation  # allauth'dan import
from .serializers import (
    UserSerializer, 
    AccountingFirmSerializer, 
    DocumentSerializer, 
    DocumentUploadSerializer, 
    CustomTokenObtainPairSerializer, 
    SubscriptionPlanSerializer, 
    SubscriptionSerializer, 
    ForgotPasswordSerializer, 
    VerifyOTPSerializer,
    CustomRegisterSerializer,
    AccountantListSerializer,
    CitySerializer,
    RegionSerializer,
    SubRegionSerializer,
    ClientDocumentSerializer
)
from .permissions import IsAccountant, IsClientOrAccountant
from rest_framework.decorators import action, api_view, permission_classes
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
import random
import string
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db.models import Count, Q, Max
from django.utils import timezone
from datetime import timedelta
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from .utils import PayTRService, send_email_via_smtp2go
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from django.core.cache import cache
from rest_framework.permissions import AllowAny
from allauth.account.views import ConfirmEmailView
from django.http import JsonResponse, HttpResponse
from cities_light.models import City, Region, SubRegion
import requests

# Create your views here.

class CurrentUserView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """
        Giriş yapmış kullanıcının bilgilerini döndürür
        """
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.user_type == 'accountant':
            return User.objects.filter(accounting_firms__owner=self.request.user)
        return User.objects.filter(id=self.request.user.id)

    @action(detail=True, methods=['get'])
    def documents(self, request, pk=None):
        user = self.get_object()
        documents = user.uploaded_documents.all()
        serializer = DocumentSerializer(documents, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        user = self.get_object()
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_documents = user.uploaded_documents.filter(
            created_at__gte=thirty_days_ago
        )
        
        total_documents = recent_documents.count()
        pending_documents = recent_documents.filter(status='pending').count()
        processed_documents = recent_documents.filter(status__in=['completed', 'rejected']).count()
        
        return Response({
            'total_documents': total_documents,
            'pending_documents': pending_documents,
            'processed_documents': processed_documents
        })

class AccountingFirmViewSet(viewsets.ModelViewSet):
    queryset = AccountingFirm.objects.all()
    serializer_class = AccountingFirmSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]

    def get_queryset(self):
        return AccountingFirm.objects.filter(owner=self.request.user)

class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    parser_classes = (MultiPartParser, FormParser)
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.user_type == 'client':
            return Document.objects.filter(uploaded_by=self.request.user)
        elif self.request.user.user_type == 'accountant':
            client_ids = self.request.user.accounting_firms.first().clients.values_list('id', flat=True)
            return Document.objects.filter(uploaded_by_id__in=client_ids)
        return Document.objects.none()

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        
        # İşlenmiş belgelerin güncellenmesini engelle
        if instance.status == 'processed':
            return Response(
                {'error': 'İşlenmiş belgeler düzenlenemez'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Sadece belirli alanların güncellenmesine izin ver
        allowed_fields = ['date', 'amount', 'vat_rate', 'document_type', 'file']
        
        # Dosya dışındaki alanlar için veriyi hazırla
        update_data = {
            key: value for key, value in request.data.items() 
            if key in allowed_fields and key != 'file'
        }

        # Sayısal değerleri float'a çevir
        if 'amount' in update_data:
            update_data['amount'] = float(update_data['amount'])
        if 'vat_rate' in update_data:
            update_data['vat_rate'] = float(update_data['vat_rate'])

        # Yeni dosya yüklendiyse eski dosyayı sil ve yenisini kaydet
        if 'file' in request.FILES:
            # Eski dosyayı sil
            if instance.file:
                try:
                    instance.file.delete(save=False)
                except Exception as e:
                    print(f"Error deleting old file: {e}")
            
            update_data['file'] = request.FILES['file']
        
        serializer = self.get_serializer(instance, data=update_data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        
        return Response(serializer.data)

    def perform_update(self, serializer):
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            
            # Yetki kontrolü
            if request.user.user_type == 'client' and instance.uploaded_by != request.user:
                return Response(
                    {'error': 'Bu belgeyi silme yetkiniz yok'},
                    status=status.HTTP_403_FORBIDDEN
                )
            elif request.user.user_type == 'accountant':
                # Muhasebecinin müşterisi değilse erişim yok
                client_ids = request.user.accounting_firms.first().clients.values_list('id', flat=True)
                if instance.uploaded_by.id not in client_ids:
                    return Response(
                        {'error': 'Bu belgeyi silme yetkiniz yok'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # Önce S3'teki dosyayı sil
            if instance.file:
                try:
                    # Storage'dan dosyayı sil
                    instance.file.delete(save=False)
                    print(f"Dosya silindi: {instance.file.name}")
                except Exception as e:
                    print(f"Dosya silinirken hata: {str(e)}")
                    # Dosya silinirken hata olsa bile devam et
                    pass
            
            # Sonra veritabanından kaydı sil
            instance.delete()
            
            return Response({
                'status': 'success',
                'message': 'Belge ve ilgili dosya başarıyla silindi'
            })
            
        except Exception as e:
            return Response({
                'status': 'error',
                'message': f'Silme işlemi sırasında hata: {str(e)}'
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    def upload(self, request):
        print("Gelen veri:", request.data)  # Debug için
        
        try:
            data = {
                'document_type': request.data.get('document_type'),
                'file': request.data.get('file'),
                'date': request.data.get('date'),
                'amount': request.data.get('amount'),
                'vat_rate': request.data.get('vat_rate'),
            }
            
            serializer = self.get_serializer(data=data)
            if serializer.is_valid():
                self.perform_create(serializer)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                print("Serializer hataları:", serializer.errors)  # Debug için
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            print("Hata:", str(e))  # Debug için
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

class ProcessDocumentView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAccountant]

    def post(self, request, pk):
        try:
            document = Document.objects.get(pk=pk)
            new_status = request.data.get('status')

            # Status değerini kontrol et
            valid_statuses = ['pending', 'processing', 'completed', 'rejected']
            if new_status not in valid_statuses:
                return Response(
                    {'error': f'Geçersiz durum değeri. Geçerli değerler: {", ".join(valid_statuses)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Belgeyi güncelle
            document.status = new_status
            document.processed_by = request.user
            document.save()

            # Belgeyi serialize et ve dön
            serializer = DocumentSerializer(document)
            return Response(serializer.data)

        except Document.DoesNotExist:
            return Response(
                {'error': 'Belge bulunamadı'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class AccountantViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]
    
    def get_queryset(self):
        # Sadece aktif müşterileri getir
        return User.objects.filter(
            accounting_firms__owner=self.request.user,
            is_active=True
        )

    def send_welcome_email(self, email, password, accountant_name):
        subject = 'Çek Fişi - Hoş Geldiniz'
        
        print("****",email, password, accountant_name)
        # HTML içerikli email
        html_message = render_to_string('emails/welcome.html', {
            'email': email,
            'password': password,
            'accountant_name': accountant_name
        })
        
        # Düz metin versiyonu
        plain_message = strip_tags(html_message)
        
        try:
            send_email_via_smtp2go(
                to_list=email,
                subject=subject,
                html_body=html_message,
                text_body=plain_message
            )
            return True
        except Exception as e:
            print(f"Email gönderme hatası: {str(e)}")
            return False

    def create(self, request, *args, **kwargs):
        email = request.data.get('email')
        
        try:
            # Önce bu email ile kayıtlı kullanıcı var mı kontrol et
            existing_user = User.objects.filter(email=email).first()
            
            if existing_user:
                if existing_user.is_active:
                    return Response(
                        {'detail': 'Bu email adresi ile aktif bir kullanıcı zaten mevcut.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                else:
                    # Pasif kullanıcıyı aktifleştir ve yeni şifre ata
                    password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
                    existing_user.set_password(password)
                    existing_user.is_active = True
                    existing_user.first_name = request.data.get('first_name', existing_user.first_name)
                    existing_user.last_name = request.data.get('last_name', existing_user.last_name)
                    existing_user.phone = request.data.get('phone', existing_user.phone)
                    existing_user.save()
                    
                    # Muhasebeci-müşteri ilişkisini kur
                    firm = request.user.owned_firm.first()
                    if firm:
                        firm.clients.add(existing_user)
                    
                    # Hoş geldin emaili gönder
                    self.send_welcome_email(
                        email=existing_user.email,
                        password=password,
                        accountant_name=f"{request.user.first_name} {request.user.last_name}"
                    )
                    
                    return Response({
                        'detail': 'Pasif kullanıcı başarıyla aktifleştirildi ve müşteriniz olarak eklendi.',
                        'user': UserSerializer(existing_user).data
                    }, status=status.HTTP_200_OK)
            
            # Yeni kullanıcı oluştur
            serializer = UserSerializer(data=request.data)
            if serializer.is_valid():
                password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
                user = serializer.save(
                    password=password,
                    user_type='client',
                    is_active=True
                )
                
                # Muhasebeci-müşteri ilişkisini kur
                firm = request.user.owned_firm.first()
                if firm:
                    firm.clients.add(user)
                
                # Hoş geldin emaili gönder
                self.send_welcome_email(
                    email=user.email,
                    password=password,
                    accountant_name=f"{request.user.first_name} {request.user.last_name}"
                )
                
                return Response({
                    'detail': 'Müşteri başarıyla eklendi.',
                    'user': serializer.data
                }, status=status.HTTP_201_CREATED)
            
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            return Response(
                {'detail': f'Bir hata oluştu: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'])
    def documents(self, request, pk=None):
        try:
            client = self.get_object()
            if not client.accounting_firms.filter(owner=request.user).exists():
                return Response(
                    {'error': 'Bu müşterinin belgelerine erişim yetkiniz yok'}, 
                    status=status.HTTP_403_FORBIDDEN
                )
            
            documents = Document.objects.filter(uploaded_by=client).order_by('-date')
            serializer = DocumentSerializer(documents, many=True)
            return Response(serializer.data)
        
        except Exception as e:
            return Response(
                {'error': str(e)}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['delete'])
    def remove_client(self, request, pk=None):
        try:
            client = self.get_object()
            
            # Müşterinin bu muhasebeciye ait olup olmadığını kontrol et
            if not client.accounting_firms.filter(owner=request.user).exists():
                return Response(
                    {'error': 'Bu müşteri size ait değil'}, 
                    status=status.HTTP_403_FORBIDDEN
                )

            # Muhasebecinin firmasından müşteriyi çıkar
            firm = AccountingFirm.objects.get(owner=request.user)
            firm.clients.remove(client)

            # Müşteriyi soft delete yap
            client.soft_delete()

            return Response({
                'status': 'success',
                'message': 'Müşteri başarıyla silindi'
            })

        except Exception as e:
            return Response(
                {'error': str(e)}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=['get'])
    def clients(self, request):
        try:
            clients = User.objects.filter(
                accounting_firms__owner=request.user,
                is_active=True  # Sadece aktif müşterileri getir
            ).annotate(
                pending_documents_count=Count(
                    'uploaded_documents',
                    filter=Q(uploaded_documents__status='pending')
                ),
                total_documents_count=Count('uploaded_documents'),
                last_activity=Max('uploaded_documents__updated_at')
            )
            
            serializer = UserSerializer(clients, many=True)
            data = serializer.data
            
            # İstatistikleri ekle
            for i, client in enumerate(clients):
                data[i]['pending_documents_count'] = client.pending_documents_count
                data[i]['total_documents_count'] = client.total_documents_count
                data[i]['last_activity'] = client.last_activity
            
            return Response(data)
        except Exception as e:
            return Response(
                {'error': str(e)}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=['post'])
    def add_client(self, request):
        # Abonelik kontrolü
        try:
            active_subscription = AccountantSubscription.objects.get(
                accountant=request.user,
                status='active',
                end_date__gt=timezone.now()
            )
            
            # Muhasebe firmasını bul
            firm = AccountingFirm.objects.filter(owner=request.user).first()
            if not firm:
                firm = AccountingFirm.objects.create(
                    owner=request.user,
                    name=f"{request.user.first_name}'s Firm"
                )
            
            # Müşteri limitini kontrol et
            current_client_count = firm.clients.count()
            if current_client_count >= active_subscription.client_limit:
                return Response({
                    'error': 'Müşteri limitinize ulaştınız. Lütfen planınızı yükseltin.',
                    'redirect_to_subscription': True
                }, status=status.HTTP_403_FORBIDDEN)
            
        except AccountantSubscription.DoesNotExist:
            return Response({
                'error': 'Aktif bir aboneliğiniz bulunmuyor.',
                'redirect_to_subscription': True
            }, status=status.HTTP_403_FORBIDDEN)

        return self.create(request)  # Mevcut create metodunu çağır

class DashboardStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        
        if user.user_type == 'accountant':
            # Muhasebeci için istatistikler
            total_clients = user.owned_firm.first().clients.count() if user.owned_firm.exists() else 0
            
            # Tüm müşterilerin belgelerini al
            client_ids = user.owned_firm.first().clients.values_list('id', flat=True) if user.owned_firm.exists() else []
            documents = Document.objects.filter(uploaded_by_id__in=client_ids)
            
            pending_documents = documents.filter(status='pending').count()
            latest_document = documents.order_by('-updated_at').first()
            
            # Son aktiviteleri al
            recent_activities = documents.order_by('-updated_at')[:5]
            
        else:
            # Müşteri için istatistikler
            # 'documents' yerine 'uploaded_documents' kullanıyoruz
            total_documents = user.uploaded_documents.count()
            pending_documents = user.uploaded_documents.filter(status='pending').count()
            latest_document = user.uploaded_documents.order_by('-updated_at').first()
            
            # Son aktiviteleri al
            recent_activities = user.uploaded_documents.order_by('-updated_at')[:5]

        return Response({
            'stats': {
                'total': total_clients if user.user_type == 'accountant' else total_documents,
                'pending_documents': pending_documents,
                'last_activity': latest_document.updated_at if latest_document else None
            },
            'recent_activities': DocumentSerializer(recent_activities, many=True).data
        })

class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    
    def post(self, request, *args, **kwargs):
        requested_user_type = request.data.get('user_type')
        
        try:
            # Önce kullanıcıyı bul
            user = User.objects.get(email=request.data['email'])
            
            # Şifre kontrolü
            if not user.check_password(request.data.get('password')):
                return Response(
                    {'detail': 'Email adresi veya şifre hatalı'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Email doğrulama kontrolü - sadece accountant için
            if requested_user_type == 'accountant':
                try:
                    email_address = EmailAddress.objects.get(user=user)
                    if not email_address.verified:
                        return Response(
                            {
                                'detail': 'Lütfen email adresinizi doğrulayın. Spam kutunuzu kontrol etmeyi unutmayın.',
                                'code': 'email_not_verified'
                            },
                            status=status.HTTP_403_FORBIDDEN
                        )
                except EmailAddress.DoesNotExist:
                    # Email doğrulama kaydı yoksa oluştur
                    email_address = EmailAddress.objects.create(
                        user=user,
                        email=user.email,
                        primary=True,
                        verified=False
                    )
                    return Response(
                        {
                            'detail': 'Lütfen email adresinizi doğrulayın. Spam kutunuzu kontrol etmeyi unutmayın.',
                            'code': 'email_not_verified'
                        },
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # Kullanıcı tipi kontrolü
            if user.user_type != requested_user_type:
                if requested_user_type == 'client':
                    error_message = 'Bu hesap bir mali müşavir hesabıdır.'
                else:
                    error_message = 'Bu hesap bir mükellef hesabıdır.'
                return Response(
                    {'detail': error_message},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Normal login işlemini yap
            response = super().post(request, *args, **kwargs)
            
            # Kullanıcı bilgilerini ekle
            user_data = UserSerializer(user).data
            response.data['user'] = user_data
            
            return response
            
        except User.DoesNotExist:
            return Response(
                {'detail': 'Bu email adresi ile kayıtlı bir hesap bulunamadı'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {'detail': 'Giriş yapılırken bir hata oluştu'},
                status=status.HTTP_400_BAD_REQUEST
            )

class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get('refresh_token')
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
                return Response({'message': 'Successfully logged out'})
        except Exception as e:
            return Response(
                {'error': str(e)}, 
                status=status.HTTP_400_BAD_REQUEST
            )

class SubscriptionPlanViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Sadece aktif ve ücretli planları getir
        return SubscriptionPlan.objects.filter(
            is_active=True,
            plan_type='paid'
        )

class SubscriptionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Get current active subscription"""
        try:
            subscription = AccountantSubscription.objects.filter(
                accountant=request.user,
                status='active'
            ).select_related('plan').latest('created_at')
            
            # Müşteri sayısını hesapla
            client_count = User.objects.filter(
                accounting_firms__owner=request.user
            ).count()

            # Abonelik verilerini hazırla
            subscription_data = SubscriptionSerializer(subscription).data
            subscription_data.update({
                'current_client_count': client_count,
                'remaining_days': (subscription.end_date - timezone.now()).days if subscription.end_date else None,
                'price': subscription.plan.calculate_price(client_count)
            })
            
            return Response(subscription_data)
            
        except AccountantSubscription.DoesNotExist:
            return Response(None, status=status.HTTP_200_OK)

    def post(self, request):
        plan_id = request.data.get('plan_id')
        client_count = int(request.data.get('client_count', 0))

        try:
            plan = SubscriptionPlan.objects.get(id=plan_id, is_active=True)
            
            # Ücretsiz veya deneme planı kontrolü
            if plan.plan_type in ['free', 'trial']:
                # Mevcut aktif aboneliği iptal et
                AccountantSubscription.objects.filter(
                    accountant=request.user,
                    status='active'
                ).update(status='cancelled')
                
                # Yeni ücretsiz/deneme aboneliği oluştur
                subscription = AccountantSubscription.objects.create(
                    accountant=request.user,
                    plan=plan,
                    client_limit=plan.base_client_limit,
                    status='active',
                    start_date=timezone.now(),
                    # Deneme süresi varsa ekle
                    end_date=timezone.now() + timedelta(days=plan.trial_days) if plan.trial_days else None
                )
                
                return Response({
                    'status': 'success',
                    'message': 'Ücretsiz plan aktifleştirildi',
                    'subscription_id': subscription.id
                })
            
            # Ücretli plan için PayTR işlemleri
            calculated_price = plan.calculate_price(client_count)
            return_url = f"{settings.FRONTEND_URL}/dashboard/subscription/callback"
            
            paytr_response = PayTRService.create_subscription(
                request.user,
                calculated_price,
                return_url
            )
            
            if paytr_response.get('status') == 'success':
                subscription = AccountantSubscription.objects.create(
                    accountant=request.user,
                    plan=plan,
                    client_limit=client_count,
                    status='pending',
                    paytr_subscription_id=paytr_response.get('subscription_id')
                )
                
                return Response({
                    'iframe_url': paytr_response.get('iframe_url'),
                    'subscription_id': subscription.id
                })
            
            return Response(
                {'error': paytr_response.get('message')},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        except SubscriptionPlan.DoesNotExist:
            return Response(
                {'error': 'Plan bulunamadı'},
                status=status.HTTP_404_NOT_FOUND
            )

class PasswordChangeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            # Şifre validasyonu
            old_password = request.data.get('old_password')
            new_password = request.data.get('new_password')
            
            if not request.user.check_password(old_password):
                return Response(
                    {'error': 'Mevcut şifreniz yanlış'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Django'nun şifre validasyonunu kullan
            try:
                validate_password(new_password, request.user)
            except ValidationError as e:
                return Response(
                    {'error': e.messages},
                    status=status.HTTP_400_BAD_REQUEST
                )

            request.user.set_password(new_password)
            request.user.save()
            
            return Response({'message': 'Şifreniz başarıyla değiştirildi'})
            
        except Exception as e:
            return Response(
                {'error': 'Şifre değiştirme işlemi başarısız'},
                status=status.HTTP_400_BAD_REQUEST
            )

class DocumentPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

class ClientDocumentsView(APIView):
    pagination_class = DocumentPagination
    
    def get(self, request, pk):
        try:
            documents = Document.objects.filter(
                uploaded_by_id=pk
            ).order_by('-created_at')
            
            paginator = self.pagination_class()
            paginated_documents = paginator.paginate_queryset(documents, request)
            serializer = DocumentSerializer(paginated_documents, many=True)
            
            return paginator.get_paginated_response(serializer.data)
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

class ProfileUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request):
        try:
            user = request.user
            print("Gelen veri:", request.data)
            
            if user.user_type == 'accountant':
                allowed_fields = [
                    'first_name', 'last_name', 'phone', 'email',
                    'address', 'city', 'district', 'about',
                    'experience_years', 'title', 'company_name',
                    'website', 'profile_image', 'specializations'
                ]
            else:  # client için
                allowed_fields = [
                    'first_name', 'last_name', 'phone', 'email',
                    'tax_number', 'identity_number', 'company_type', 
                    'company_title'
                ]

            # QueryDict'ten normal dict'e çevir ve ilk değerleri al
            update_data = {}
            for key in allowed_fields:
                if key in request.data:
                    # QueryDict'ten gelen liste değerinin ilk elemanını al
                    value = request.data.getlist(key)[0] if isinstance(request.data.getlist(key), list) else request.data.get(key)
                    update_data[key] = value
            
            print("İşlenecek veri:", update_data)
            
            serializer = UserSerializer(user, data=update_data, partial=True)
            
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            else:
                print("Serializer hataları:", serializer.errors)
                return Response(
                    {'error': serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
        except Exception as e:
            print("Hata:", str(e))
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

def send_welcome_email(user):
    """
    Yeni kayıt olan kullanıcıya hoşgeldin emaili gönderir
    """
    subject = "Çek Fişi'ne Hoş Geldiniz!"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h1 style="color: #2c3e50;">Hoş Geldiniz!</h1>
        <p>Sayın {user.first_name} {user.last_name},</p>
        <p>Çek Fişi'ne kayıt olduğunuz için teşekkür ederiz. Artık tüm özelliklerimizden yararlanabilirsiniz.</p>
        <p>Herhangi bir sorunuz olursa, bizimle iletişime geçmekten çekinmeyin.</p>
        <br>
        <p>Saygılarımızla,</p>
        <p><strong>Çek Fişi Ekibi</strong></p>
    </div>
    """
    
    text_body = f"""
    Hoş Geldiniz!
    
    Sayın {user.first_name} {user.last_name},
    
    Çek Fişi'ne kayıt olduğunuz için teşekkür ederiz. Artık tüm özelliklerimizden yararlanabilirsiniz.
    
    Herhangi bir sorunuz olursa, bizimle iletişime geçmekten çekinmeyin.
    
    Saygılarımızla,
    Çek Fişi Ekibi
    """
    
    try:
        send_email_via_smtp2go(
            to_list=user.email,
            subject=subject,
            html_body=html_body,
            text_body=text_body
        )
    except Exception as e:
        # Hata durumunda loglama yapabilirsiniz
        print(f"Welcome email gönderilemedi: {str(e)}")

class ForgotPasswordView(APIView):
    permission_classes = []
    serializer_class = ForgotPasswordSerializer
    
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        email = serializer.validated_data['email']
        user_type = request.data.get('user_type')  # URL'den gelen type parametresi
            
        try:
            user = User.objects.get(email=email)
            
            # Kullanıcı tipi kontrolü
            if user.user_type != user_type:
                error_message = (
                    'Bu email adresi bir mükellef hesabına ait. Lütfen mükellef girişini kullanın.' 
                    if user_type == 'accountant' 
                    else 'Bu email adresi bir mali müşavir hesabına ait. Lütfen mali müşavir girişini kullanın.'
                )
                return Response(
                    {'error': error_message},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # 6 haneli OTP kodu oluştur
            otp = ''.join([str(random.randint(0, 9)) for _ in range(6)])
            
            # Email gönder
            subject = "Şifre Sıfırlama Kodu"
            html_body = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <h1 style="color: #2c3e50;">Şifre Sıfırlama</h1>
                <p>Sayın {user.first_name} {user.last_name},</p>
                <p>Şifre sıfırlama talebiniz için doğrulama kodunuz:</p>
                <h2 style="color: #3498db; font-size: 24px; text-align: center; padding: 10px; background-color: #f8f9fa; border-radius: 5px;">{otp}</h2>
                <p>Bu kod 30 dakika süreyle geçerlidir.</p>
                <p>Eğer bu talebi siz yapmadıysanız, lütfen bu emaili dikkate almayın.</p>
                <br>
                <p>Saygılarımızla,</p>
                <p><strong>Çek Fişi Ekibi</strong></p>
            </div>
            """
            
            text_body = f"""
            Şifre Sıfırlama

            Sayın {user.first_name} {user.last_name},
            
            Şifre sıfırlama talebiniz için doğrulama kodunuz: {otp}
            
            Bu kod 30 dakika süreyle geçerlidir.
            
            Eğer bu talebi siz yapmadıysanız, lütfen bu emaili dikkate almayın.
            
            Saygılarımızla,
            Çek Fişi Ekibi
            """
            
            # OTP'yi email ile gönder
            send_email_via_smtp2go(
                to_list=email,
                subject=subject,
                html_body=html_body,
                text_body=text_body
            )
            
            # OTP'yi kullanıcının veritabanındaki geçici bir alana kaydet
            user.otp = otp
            user.otp_created_at = timezone.now()
            user.save()
            
            return Response({
                'message': 'Şifre sıfırlama kodu email adresinize gönderildi',
                'email': email
            })
            
        except User.DoesNotExist:
            # Güvenlik için aynı mesajı döndür
            return Response({
                'message': 'Şifre sıfırlama kodu email adresinize gönderildi'
            })
        except Exception as e:
            print(f"Hata: {str(e)}")  # Loglama için
            return Response(
                {'error': 'Bir hata oluştu'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class VerifyOTPView(APIView):
    permission_classes = []
    serializer_class = VerifyOTPSerializer
    
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            # Şifre validasyon hatalarını Türkçeleştir
            errors = []
            if 'new_password' in serializer.errors:
                for error in serializer.errors['new_password']:
                    if 'This password is too common.' in str(error):
                        errors.append('Bu şifre çok yaygın kullanılıyor.')
                    elif 'This password is entirely numeric.' in str(error):
                        errors.append('Şifre sadece rakamlardan oluşamaz.')
                    elif 'This password is too short.' in str(error):
                        errors.append('Şifre çok kısa. En az 8 karakter olmalı.')
                    else:
                        errors.append(str(error))
                return Response(
                    {'error': errors},
                    status=status.HTTP_400_BAD_REQUEST
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        email = serializer.validated_data['email']
        otp = serializer.validated_data['otp']
        new_password = serializer.validated_data['new_password']
            
        try:
            user = User.objects.get(email=email)
            
            # OTP'nin geçerlilik süresini kontrol et (30 dakika)
            if not user.otp or not user.otp_created_at or \
               timezone.now() - user.otp_created_at > timedelta(minutes=30):
                return Response(
                    {'error': 'Geçersiz veya süresi dolmuş kod'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # OTP doğruluğunu kontrol et
            if user.otp != otp:
                return Response(
                    {'error': 'Geçersiz kod'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Şifreyi güncelle
            user.set_password(new_password)
            # OTP'yi temizle
            user.otp = None
            user.otp_created_at = None
            user.save()
            
            # Başarılı email gönder
            subject = "Şifreniz Başarıyla Değiştirildi"
            html_body = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <h1 style="color: #2c3e50;">Şifre Değişikliği</h1>
                <p>Sayın {user.first_name} {user.last_name},</p>
                <p>Şifreniz başarıyla değiştirildi. Yeni şifrenizle giriş yapabilirsiniz.</p>
                <p>Bu işlemi siz yapmadıysanız, lütfen hemen bizimle iletişime geçin.</p>
                <br>
                <p>Saygılarımızla,</p>
                <p><strong>Çek Fişi Ekibi</strong></p>
            </div>
            """
            
            text_body = f"""
            Şifre Değişikliği

            Sayın {user.first_name} {user.last_name},
            
            Şifreniz başarıyla değiştirildi. Yeni şifrenizle giriş yapabilirsiniz.
            
            Bu işlemi siz yapmadıysanız, lütfen hemen bizimle iletişime geçin.
            
            Saygılarımızla,
            Çek Fişi Ekibi
            """
            
            send_email_via_smtp2go(
                to_list=email,
                subject=subject,
                html_body=html_body,
                text_body=text_body
            )
            
            return Response({
                'message': 'Şifreniz başarıyla güncellendi'
            })
            
        except User.DoesNotExist:
            return Response(
                {'error': 'Kullanıcı bulunamadı'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {'error': 'Bir hata oluştu'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    serializer = CustomRegisterSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save(request)
        return Response({
            'user': UserSerializer(user).data,
            'message': 'Kullanıcı başarıyla oluşturuldu'
        }, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request, key, *args, **kwargs):
        try:
            confirmation = EmailConfirmation.objects.get(key=key)
            
            # Token'ın geçerlilik süresini kontrol et
            expiration_date = confirmation.sent + timedelta(days=settings.ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS)
            if timezone.now() > expiration_date:
                # Eski token'ı sil
                confirmation.delete()
                
                # Yeni doğrulama emaili gönder
                email_address = confirmation.email_address
                new_confirmation = EmailConfirmation.create(email_address)
                new_confirmation.sent = timezone.now()
                new_confirmation.save()
                
                subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Email Doğrulama"
                confirmation_url = f"{settings.FRONTEND_URL}/auth/verify-email/{new_confirmation.key}"
                
                html_message = render_to_string('email/email_confirmation_message.html', {
                    'user': email_address.user,
                    'activate_url': confirmation_url
                })
                
                text_message = strip_tags(html_message)
                
                send_email_via_smtp2go(
                    to_list=email_address.email,
                    subject=subject,
                    html_body=html_message,
                    text_body=text_message
                )
                
                return JsonResponse({
                    'detail': 'Doğrulama linkinin süresi dolmuş. Yeni doğrulama emaili gönderildi.',
                    'code': 'token_expired'
                }, status=400)
            
            # Token geçerliyse doğrula
            confirmation.confirm(request)
            
            return JsonResponse({
                'detail': 'Email adresiniz başarıyla doğrulandı. Giriş yapabilirsiniz.'
            })
            
        except EmailConfirmation.DoesNotExist:
            return JsonResponse({
                'detail': 'Geçersiz veya süresi dolmuş doğrulama bağlantısı.'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'detail': 'Doğrulama sırasında bir hata oluştu.'
            }, status=500)

class ResendVerificationEmailView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        email = request.data.get('email')
        
        try:
            user = User.objects.get(email=email)
            email_address = EmailAddress.objects.get(user=user)
            
            if email_address.verified:
                return Response(
                    {'detail': 'Bu email adresi zaten doğrulanmış.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Eski doğrulama kayıtlarını sil
            EmailConfirmation.objects.filter(email_address=email_address).delete()
            
            # Yeni doğrulama kodu oluştur ve email gönder
            confirmation = EmailConfirmation.create(email_address)
            confirmation.sent = timezone.now()
            confirmation.save()
            
            subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Email Doğrulama"
            confirmation_url = f"{settings.FRONTEND_URL}/auth/verify-email/{confirmation.key}"
            
            html_message = render_to_string('email/email_confirmation_message.html', {
                'user': user,
                'activate_url': confirmation_url
            })
            
            text_message = strip_tags(html_message)
            
            send_email_via_smtp2go(
                to_list=email,
                subject=subject,
                html_body=html_message,
                text_body=text_message
            )
            
            return Response({
                'detail': 'Yeni doğrulama emaili gönderildi. Lütfen email kutunuzu kontrol edin.'
            })
            
        except User.DoesNotExist:
            return Response(
                {'detail': 'Bu email adresi ile kayıtlı bir hesap bulunamadı.'},
                status=status.HTTP_404_NOT_FOUND
            )
        except EmailAddress.DoesNotExist:
            return Response(
                {'detail': 'Bu email adresi için doğrulama kaydı bulunamadı.'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {'detail': 'Email gönderimi sırasında bir hata oluştu.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

@api_view(['GET'])
@permission_classes([AllowAny])
def accountant_list(request):
    """Mali müşavirleri listele"""
    try:
        # Query parametrelerini al
        city = request.query_params.get('city')
        district = request.query_params.get('district')
        specialization = request.query_params.get('specialization')
        search = request.query_params.get('search')
        
        # Base query
        queryset = User.objects.filter(user_type='accountant', is_active=True)
        
        # Filtreleri uygula
        if city:
            queryset = queryset.filter(city__iexact=city)
        if district:
            queryset = queryset.filter(district__iexact=district)
        if specialization:
            queryset = queryset.filter(specializations__contains=[specialization])
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(company_name__icontains=search) |
                Q(about__icontains=search)
            )
        
        # Sıralama
        queryset = queryset.order_by('-is_featured', '-rating', '-review_count')
        
        # Serialize
        serializer = AccountantListSerializer(queryset, many=True)
        
        return Response({
            'status': 'success',
            'data': serializer.data
        })
        
    except Exception as e:
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=500)

@api_view(['GET'])
@permission_classes([AllowAny])
def city_list(request):
    """Türkiye'deki illeri listele"""
    cities = Region.objects.filter(country__code2='TR')
    serializer = RegionSerializer(cities, many=True)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([AllowAny])
def district_list(request, city_id):
    """İlin ilçelerini listele"""
    districts = SubRegion.objects.filter(
        region_id=city_id,
        region__country__code2='TR'
    )
    serializer = SubRegionSerializer(districts, many=True)
    return Response(serializer.data)

class ClientDocumentViewSet(viewsets.ModelViewSet):
    serializer_class = ClientDocumentSerializer
    parser_classes = (MultiPartParser, FormParser)
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.user_type == 'client':
            return ClientDocument.objects.filter(client=self.request.user)
        elif self.request.user.user_type == 'accountant':
            client_ids = self.request.user.accounting_firms.first().clients.values_list('id', flat=True)
            return ClientDocument.objects.filter(client_id__in=client_ids)
        return ClientDocument.objects.none()

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'status': 'success',
            'data': serializer.data
        })

    def perform_create(self, serializer):
        current_count = ClientDocument.objects.filter(
            client=self.request.user,
            is_active=True
        ).count()
        
        if current_count >= 10:
            raise ValidationError('Maximum 10 aktif belge yükleyebilirsiniz')
            
        serializer.save(client=self.request.user)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            
            # Yetki kontrolü
            if request.user.user_type == 'client' and instance.client != request.user:
                return Response(
                    {'error': 'Bu belgeyi silme yetkiniz yok'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Önce S3'teki dosyayı sil
            if instance.file:
                try:
                    # Storage'dan dosyayı sil
                    instance.file.delete(save=False)
                    print(f"Dosya silindi: {instance.file.name}")
                except Exception as e:
                    print(f"Dosya silinirken hata: {str(e)}")
                    # Dosya silinirken hata olsa bile devam et
                    pass
            
            # Sonra veritabanından kaydı sil
            instance.delete()
            
            return Response({
                'status': 'success',
                'message': 'Belge ve ilgili dosya başarıyla silindi'
            })
            
        except Exception as e:
            return Response({
                'status': 'error',
                'message': f'Silme işlemi sırasında hata: {str(e)}'
            }, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def download_document(request, document_id):
    try:
        document = get_object_or_404(ClientDocument, id=document_id)
        
        # Yetki kontrolü
        if request.user.user_type == 'client' and document.client != request.user:
            return Response(
                {'error': 'Bu belgeyi indirme yetkiniz yok'},
                status=status.HTTP_403_FORBIDDEN
            )
        elif request.user.user_type == 'accountant':
            # Muhasebecinin müşterisi değilse erişim yok
            client_ids = request.user.accounting_firms.first().clients.values_list('id', flat=True)
            if document.client.id not in client_ids:
                return Response(
                    {'error': 'Bu belgeyi indirme yetkiniz yok'},
                    status=status.HTTP_403_FORBIDDEN
                )
        
        # CDN'den dosyayı al
        response = requests.get(document.file.url)
        
        if response.status_code != 200:
            return Response(
                {'error': 'Dosya bulunamadı'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Content-Type ve diğer header'ları ayarla
        content_type = response.headers.get('Content-Type', 'application/octet-stream')
        
        # Django response oluştur
        django_response = HttpResponse(
            response.content,
            content_type=content_type
        )
        
        # Dosya adını ayarla
        filename = document.file.name.split('/')[-1]
        django_response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return django_response
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )

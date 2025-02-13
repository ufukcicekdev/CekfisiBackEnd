from django.shortcuts import render
from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import User, AccountingFirm, Document, SubscriptionPlan, AccountantSubscription
from .serializers import UserSerializer, AccountingFirmSerializer, DocumentSerializer, DocumentUploadSerializer, CustomTokenObtainPairSerializer, SubscriptionPlanSerializer, SubscriptionSerializer, ForgotPasswordSerializer, VerifyOTPSerializer
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

class AccountingFirmViewSet(viewsets.ModelViewSet):
    queryset = AccountingFirm.objects.all()
    serializer_class = AccountingFirmSerializer
    permission_classes = [permissions.IsAuthenticated, IsAccountant]

    def get_queryset(self):
        return AccountingFirm.objects.filter(owner=self.request.user)

class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated, IsClientOrAccountant]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_queryset(self):
        if self.request.user.user_type == 'accountant':
            return Document.objects.filter(
                uploaded_by__accounting_firms__owner=self.request.user
            )
        return Document.objects.filter(uploaded_by=self.request.user)

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
        document = get_object_or_404(Document, pk=pk)
        new_status = request.data.get('status')  # 'processed' veya 'pending'
        
        if not new_status or new_status not in ['processed', 'pending']:
            return Response(
                {'error': 'Geçersiz durum değeri'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if document.uploaded_by.accounting_firms.filter(owner=request.user).exists():
            document.status = new_status
            if new_status == 'processed':
                document.processed_by = request.user
            else:
                document.processed_by = None
            document.save()
            
            return Response({
                'status': new_status,
                'message': 'Belge durumu başarıyla güncellendi'
            })
            
        return Response(
            {'error': 'Bu işlem için yetkiniz yok'}, 
            status=status.HTTP_403_FORBIDDEN
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

    @action(detail=False, methods=['post'])
    def add_client(self, request):
        try:
            # Mali müşavirin kendi emailini kontrol et
            email = request.data.get('email')
            if email == request.user.email:
                return Response({
                    'error': 'Kendinizi müşteri olarak ekleyemezsiniz.'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Önce aktif abonelik kontrolü yap
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

            # Email kontrolü
            if not email:
                return Response({'error': 'Email gerekli'}, status=400)

            if User.objects.filter(email=email).exists():
                return Response({
                    'error': 'Bu email adresi zaten kullanımda'
                }, status=400)

            # Rastgele şifre oluştur
            password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))

            # Yeni kullanıcı oluştur
            user = User.objects.create_user(
                username=email,
                email=email,
                password=password,
                user_type='client',
                first_name=request.data.get('first_name', ''),
                last_name=request.data.get('last_name', ''),
                phone=request.data.get('phone', '')
            )

            # Email gönder
            email_sent = self.send_welcome_email(
                email=email,
                password=password,
                accountant_name=f"{request.user.first_name} {request.user.last_name}"
            )

            # Müşteriyi firmaya ekle
            firm.clients.add(user)

            return Response({
                'status': 'success',
                'message': 'Müşteri eklendi ve bilgilendirme emaili gönderildi',
                'client': UserSerializer(user).data
            })

        except Exception as e:
            print(f"HATA: {str(e)}")
            return Response({'error': str(e)}, status=400)

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
                    'documents',
                    filter=Q(documents__status='pending')
                ),
                total_documents_count=Count('documents'),
                last_activity=Max('documents__updated_at')
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

class DashboardStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        
        if user.user_type == 'accountant':
            try:
                # Mali müşavir istatistikleri - firma yoksa oluştur
                firm = AccountingFirm.objects.filter(
                    owner=user
                ).first()
                
                total_clients = firm.clients.count()
                
                # Bekleyen belgeleri hesapla
                pending_documents = Document.objects.filter(
                    uploaded_by__in=firm.clients.all(),
                    status='pending'
                ).count()

                # Son aktiviteler
                recent_activities = Document.objects.select_related('uploaded_by').filter(
                    uploaded_by__in=firm.clients.all()
                ).order_by('-created_at')[:5].values(
                    'id',
                    'document_type',
                    'created_at',
                    'status',
                    'uploaded_by__first_name',
                    'uploaded_by__last_name',
                )

                # Son işlem tarihi
                latest_document = Document.objects.filter(
                    uploaded_by__in=firm.clients.all()
                ).order_by('-updated_at').first()

            except Exception as e:
                print(f"Hata: {str(e)}")
                return Response({
                    'stats': {
                        'total': 0,
                        'pending_documents': 0,
                        'last_activity': None
                    },
                    'recent_activities': [],
                    'error': 'Bir hata oluştu.'
                })

        else:
            # Mükellef istatistikleri
            total_documents = Document.objects.filter(uploaded_by=user).count()
            pending_documents = Document.objects.filter(
                uploaded_by=user,
                status='pending'
            ).count()
            
            latest_document = Document.objects.filter(
                uploaded_by=user
            ).order_by('-updated_at').first()

            recent_activities = Document.objects.filter(
                uploaded_by=user
            ).order_by('-created_at')[:5].values(
                'id',
                'document_type',
                'created_at',
                'status'
            )

        return Response({
            'stats': {
                'total': total_clients if user.user_type == 'accountant' else total_documents,
                'pending_documents': pending_documents,
                'last_activity': latest_document.updated_at if latest_document else None
            },
            'recent_activities': recent_activities
        })

class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    
    def post(self, request, *args, **kwargs):
        requested_user_type = request.data.get('user_type')
        
        try:
            # Önce normal login işlemini yap
            response = super().post(request, *args, **kwargs)
            
            # Kullanıcıyı bul
            user = User.objects.get(email=request.data['email'])
            
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
            if 'No active account found with the given credentials' in str(e):
                return Response(
                    {'detail': 'Email adresi veya şifre hatalı'},
                    status=status.HTTP_400_BAD_REQUEST
                )
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
            # Debug için print ekleyelim
            print("Gelen veri:", request.data)
            print("Mevcut kullanıcı:", user)
            
            serializer = UserSerializer(user, data=request.data, partial=True)
            
            if serializer.is_valid():
                print("Serializer valid")
                serializer.save()
                return Response(serializer.data)
            else:
                print("Serializer hataları:", serializer.errors)
                return Response(
                    {'error': serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
        except Exception as e:
            print("Hata:", str(e))  # Debug için
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

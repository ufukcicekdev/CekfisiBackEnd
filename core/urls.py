from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    LoginView, 
    ProfileUpdateView, 
    ForgotPasswordView, 
    VerifyOTPView,
    CurrentUserView,
    register,
    CustomTokenObtainPairView
)

router = DefaultRouter()
router.register(r'users', views.UserViewSet)
router.register(r'accountants', views.AccountantViewSet, basename='accountant')
router.register(r'firms', views.AccountingFirmViewSet)
router.register(r'documents', views.DocumentViewSet, basename='document')
router.register(r'subscription-plans', views.SubscriptionPlanViewSet, basename='subscription-plans')

urlpatterns = [
    # Auth endpoints
    path('auth/register/', register, name='register'),
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # User endpoints - router'dan önce olmalı
    path('users/me/', CurrentUserView.as_view(), name='current-user'),
    
    # Diğer endpoints
    path('users/profile/', views.ProfileUpdateView.as_view(), name='profile-update'),
    path('documents/process/<int:pk>/', views.ProcessDocumentView.as_view(), name='document-process'),
    path('dashboard/stats/', views.DashboardStatsView.as_view(), name='dashboard-stats'),
    path('subscriptions/', views.SubscriptionView.as_view(), name='subscription-create'),
    path('subscriptions/current/', views.SubscriptionView.as_view(), name='subscription-current'),
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot-password'),
    path('verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    
    # Router URLs en sonda olmalı
    path('', include(router.urls)),
] 
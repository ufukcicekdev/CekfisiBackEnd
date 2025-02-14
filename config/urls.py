"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from dj_rest_auth.registration.views import ResendEmailVerificationView
from core.views import VerifyEmailView, ResendVerificationEmailView  # Kendi view'ımızı kullanacağız

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('core.urls')),
    
    # Email doğrulama URL'leri - sonunda / ekleyelim
    path(
        'api/v1/auth/verify-email/<str:key>/',  # Sonunda / var
        VerifyEmailView.as_view(),
        name='account_confirm_email',
    ),
    path(
        'api/v1/auth/resend-email/',  # Sonunda / var
        ResendEmailVerificationView.as_view(),
        name="account_resend_email",
    ),
    path(
        'api/v1/auth/verify-email/<str:key>/',
        VerifyEmailView.as_view(),
        name='account_confirm_email',
    ),
    path(
        'api/v1/auth/resend-verification-email/',
        ResendVerificationEmailView.as_view(),
        name='resend_verification_email',
    ),
]

# Debug modunda static ve media dosyalarını serve et
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

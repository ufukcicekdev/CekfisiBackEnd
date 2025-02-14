"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path

from django.core.wsgi import get_wsgi_application
from dotenv import load_dotenv

# Proje kök dizinini ekleyin
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# .env dosyasını yükle
load_dotenv(os.path.join(BASE_DIR, '.env'))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:
    application = get_wsgi_application()
except Exception as e:
    print(f"Error loading WSGI application: {e}")
    raise

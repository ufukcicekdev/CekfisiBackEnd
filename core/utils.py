import hashlib
import base64
import requests
from django.conf import settings
import json
from decimal import Decimal
import time
import hmac
from typing import List, Union

class PayTRService:
    @staticmethod
    def create_subscription(user, amount, return_url):
        try:
            merchant_id = settings.PAYTR_MERCHANT_ID
            merchant_key = settings.PAYTR_MERCHANT_KEY
            merchant_salt = settings.PAYTR_MERCHANT_SALT
            
            # Decimal'i float'a çevir
            amount = float(amount)
            
            # Debug için
            print("PayTR Bilgileri:")
            print(f"Merchant ID: {merchant_id}")
            print(f"Amount: {amount}")
            print(f"Return URL: {return_url}")
            
            # Sepet için ürün bilgisi
            basket_item = ["Abonelik", str(amount), 1]
            basket_json = json.dumps([basket_item])
            user_basket_b64 = base64.b64encode(basket_json.encode('utf-8'))
            
            data = {
                'merchant_id': merchant_id,
                'user_ip': '127.0.0.1',
                'merchant_oid': f'SUB_{user.id}_{int(time.time())}',
                'email': user.email,
                'payment_amount': int(amount * 100),  # Kuruş cinsinden
                'currency': 'TL',
                'test_mode': '1',  # Test modu aktif
                'debug_on': '1',  # Debug modu aktif
                'lang': 'tr',
                'no_installment': '0',
                'max_installment': '12',
                'user_name': f'{user.first_name} {user.last_name}',
                'user_phone': user.phone or '05555555555',
                'merchant_ok_url': return_url,
                'merchant_fail_url': return_url,
                'user_basket': user_basket_b64,
            }

            # Hash oluştur
            hash_str = f"{merchant_id}{data['user_ip']}{data['merchant_oid']}{data['email']}{data['payment_amount']}{user_basket_b64.decode()}{data['no_installment']}{data['max_installment']}{data['currency']}{data['test_mode']}{merchant_salt}"
            paytr_token = base64.b64encode(hmac.new(merchant_key.encode('utf-8'), hash_str.encode('utf-8'), hashlib.sha256).digest())
            data['paytr_token'] = paytr_token

            print("PayTR'ye gönderilen data:", data)

            response = requests.post('https://www.paytr.com/odeme/api/get-token', data=data)
            result = response.json()

            print("PayTR'den gelen yanıt:", result)

            if result['status'] == 'success':
                return {
                    'status': 'success',
                    'iframe_url': f"https://www.paytr.com/odeme/guvenli/{result['token']}",
                    'subscription_id': data['merchant_oid']
                }
            else:
                return {
                    'status': 'error',
                    'message': result.get('reason', 'Ödeme başlatılamadı'),
                    'details': result
                }

        except Exception as e:
            print("PayTR Hatası:", str(e))
            return {
                'status': 'error',
                'message': str(e)
            }

    @staticmethod
    def cancel_subscription(subscription_id):
        merchant_id = settings.PAYTR_MERCHANT_ID
        merchant_key = settings.PAYTR_MERCHANT_KEY
        merchant_salt = settings.PAYTR_MERCHANT_SALT
        
        params = {
            'merchant_id': merchant_id,
            'subscription_id': subscription_id,
        }
        
        # Hash oluşturma
        hash_str = f"{merchant_id}{subscription_id}{merchant_salt}"
        paytr_token = base64.b64encode(hashlib.sha256(hash_str.encode('utf-8')).digest()).decode('utf-8')
        
        params['paytr_token'] = paytr_token
        
        response = requests.post('https://www.paytr.com/odeme/api/subscription-cancel', params)
        return response.json()

def send_email_via_smtp2go(
    to_list: Union[List[str], str],
    subject: str,
    html_body: str,
    text_body: str = None
) -> dict:
    """
    SMTP2GO API kullanarak email gönderir
    
    Args:
        to_list: Tek bir email adresi string olarak veya email adresleri listesi
        subject: Email konusu
        html_body: HTML formatında email içeriği
        text_body: Düz metin formatında email içeriği (opsiyonel)
    
    Returns:
        dict: API yanıtı
    """
    url = "https://api.smtp2go.com/v3/email/send"
    
    headers = {
        'Content-Type': 'application/json',
        'X-Smtp2go-Api-Key': settings.SMTP2GO_API_KEY,
        'Accept': 'application/json'
    }
    
    # to_list'i her zaman liste formatına çevir
    if isinstance(to_list, str):
        to_list = [to_list]
    
    payload = {
        "sender": settings.SMTP2GO_FROM_EMAIL,
        "to": to_list,
        "subject": subject,
        "html_body": html_body
    }
    
    # Eğer düz metin body varsa ekle
    if text_body:
        payload["text_body"] = text_body
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()  # 4xx, 5xx hataları için exception fırlat
        return response.json()
    except requests.exceptions.RequestException as e:
        # Hata durumunda loglama yapabilir veya exception fırlatabilirsiniz
        raise Exception(f"Email gönderilemedi: {str(e)}") 
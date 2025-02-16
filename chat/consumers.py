import json
import logging
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from channels.exceptions import StopConsumer
from rest_framework_simplejwt.tokens import AccessToken
from .models import Room, Message
from django.contrib.auth import get_user_model
from django.conf import settings
from datetime import datetime
import base64
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError
import mimetypes
import uuid
from werkzeug.utils import secure_filename
import fnmatch

logger = logging.getLogger(__name__)
User = get_user_model()



# chat/consumers.py

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """WebSocket bağlantısını başlat"""
        try:
            self.room_id = self.scope['url_route']['kwargs']['room_id']
            self.room_group_name = f'chat_{self.room_id}'
            self.ping_task = None

            # Token doğrulama
            query_string = self.scope['query_string'].decode()
            params = dict(x.split('=') for x in query_string.split('&'))
            token = params.get('token', '')

            if not token:
                logger.error(f"Token bulunamadı: Oda {self.room_id}")
                await self.close(code=4001)
                return

            # Token'ı doğrula ve kullanıcıyı al
            try:
                token_obj = AccessToken(token)
                self.user = await self.get_user_from_token(token_obj['user_id'])
                if not self.user:
                    logger.error(f"Kullanıcı bulunamadı: {token_obj['user_id']}")
                    await self.close(code=4002)
                    return
                
                logger.info(f"Kullanıcı doğrulandı: {self.user.email}")
            except Exception as e:
                logger.error(f"Token doğrulama hatası: {str(e)}")
                await self.close(code=4003)
                return

            # Önce bağlantıyı kabul et
            await self.accept()
            logger.info(f"WebSocket bağlantısı kabul edildi: Oda {self.room_id}")

            # Odaya katıl
            await self.channel_layer.group_add(
                self.room_group_name,
                self.channel_name
            )
            logger.info(f"Odaya katılım başarılı: {self.room_group_name}")

            # Bağlantı başarılı mesajı gönder
            await self.send(text_data=json.dumps({
                'type': 'connection_established',
                'message': 'Bağlantı başarılı'
            }))

            # Ping/Pong başlat
            self.ping_task = asyncio.create_task(self.ping_loop())
            logger.info(f"Ping/Pong başlatıldı: Oda {self.room_id}")

        except Exception as e:
            logger.error(f"Bağlantı hatası: {str(e)}")
            if hasattr(self, 'channel_name'):
                await self.channel_layer.group_discard(
                    self.room_group_name,
                    self.channel_name
                )
            if self.ping_task:
                self.ping_task.cancel()
            await self.close(code=4000)

    async def disconnect(self, close_code):
        """WebSocket bağlantısını sonlandır"""
        try:
            logger.info(f"Bağlantı kapatma başladı: {close_code}")
            
            if self.ping_task:
                self.ping_task.cancel()
                logger.debug("Ping döngüsü durduruldu.")
            
            if hasattr(self, 'channel_name'):
                await self.channel_layer.group_discard(
                    self.room_group_name,
                    self.channel_name
                )
                logger.info(f"Gruptan çıkıldı: {self.room_group_name}")
            
            logger.info(f"WebSocket bağlantısı temiz bir şekilde kapatıldı: {close_code}")
        except Exception as e:
            logger.error(f"Bağlantı kapatma hatası: {str(e)}")

    async def ping_loop(self):
        """Ping/Pong döngüsü"""
        while True:
            try:
                await asyncio.sleep(settings.CHANNEL_SETTINGS['PING_INTERVAL'])
                if not hasattr(self, 'user'):
                    logger.error("Kullanıcı bulunamadı, ping döngüsü durduruluyor")
                    break
                
                await self.send(text_data=json.dumps({
                    "type": "ping",
                    "timestamp": datetime.now().isoformat()
                }))
                logger.debug(f"Ping gönderildi: Oda {self.room_id}")
            except asyncio.CancelledError:
                logger.debug("Ping döngüsü durduruldu")
                break
            except Exception as e:
                logger.error(f"Ping hatası: {str(e)}")
                break

    async def receive(self, text_data):
        """Mesaj al ve işle"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == 'ping':
                await self.send(text_data=json.dumps({"type": "pong"}))
                logger.debug("Pong gönderildi")
                return
            elif message_type == 'pong':
                logger.debug("Pong alındı")
                return
            elif message_type == 'message':
                content = data.get('content')
                if not content:
                    return

                message = await self.save_message(content)
                logger.info(f"Yeni mesaj kaydedildi: {message.id}")
                
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'data': {
                            'id': message.id,
                            'content': message.content,
                            'sender': {
                                'id': self.user.id,
                                'email': self.user.email,
                                'user_type': self.user.user_type
                            },
                            'timestamp': message.timestamp.isoformat()
                        }
                    }
                )
                logger.info(f"Mesaj gruba gönderildi: {message.id}")

                # Bildirim gönder
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'notify_message',
                        'message': message.content,
                        'room_id': self.room_id,
                        'sender_id': self.user.id,
                        'sender': {
                            'email': self.user.email,
                            'user_type': self.user.user_type
                        }
                    }
                )
            elif message_type == 'file':
                # Dosya bilgilerini al
                file_data = data.get('file', {})
                file_name = file_data.get('name')
                file_content = file_data.get('content')  # base64 encoded
                
                if not file_name or not file_content:
                    logger.error("Dosya bilgileri eksik")
                    return
                
                # Content type'ı al
                format, _ = file_content.split(';base64,')
                content_type = format.split(':')[1]
                
                # Dosyayı kaydet
                file_message = await self.save_file_message(file_name, file_content)
                
                # Gruba bildir
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'data': {
                            'id': file_message.id,
                            'content': file_message.content,
                            'message_type': 'file',
                            'file': {
                                'url': file_message.file.url,
                                'name': file_name,
                                'type': content_type,
                                'size': len(base64.b64decode(file_content.split(',')[1]))
                            },
                            'sender': {
                                'id': self.user.id,
                                'email': self.user.email,
                                'user_type': self.user.user_type
                            },
                            'timestamp': file_message.timestamp.isoformat()
                        }
                    }
                )

        except json.JSONDecodeError:
            logger.error("Geçersiz JSON formatı")
        except Exception as e:
            logger.error(f"Mesaj işleme hatası: {str(e)}")

    async def chat_message(self, event):
        """Mesajı WebSocket'e gönder"""
        try:
            data = event['data']
            
            # Eğer dosya mesajı ise file bilgilerini ekle
            if data.get('message_type') == 'file':
                response = {
                    'type': 'message',
                    'data': {
                        'id': data['id'],
                        'content': data['content'],
                        'message_type': 'file',
                        'file': data['file'],
                        'sender': data['sender'],
                        'timestamp': data['timestamp']
                    }
                }
            else:
                # Normal mesaj
                response = {
                    'type': 'message',
                    'data': data
                }

            await self.send(text_data=json.dumps(response))
            logger.debug(f"Mesaj gönderildi: {data['id']}")
        except Exception as e:
            logger.error(f"Mesaj gönderme hatası: {str(e)}")

    async def notify_message(self, event):
        """Bildirim gönder"""
        try:
            # Mesajı gönderen kişi kendisine bildirim almasın
            if event['sender_id'] != self.user.id:
                await self.send(text_data=json.dumps({
                    'type': 'notification',
                    'data': {
                        'message': event['message'],
                        'room_id': event['room_id'],
                        'sender': event['sender']
                    }
                }))
                logger.debug(f"Bildirim gönderildi: Kullanıcı {self.user.email}")
        except Exception as e:
            logger.error(f"Bildirim gönderme hatası: {str(e)}")

    @database_sync_to_async
    def get_user_from_token(self, user_id):
        """Token'dan kullanıcı bilgisini al"""
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def save_message(self, content):
        """Mesajı veritabanına kaydet"""
        room = Room.objects.get(id=self.room_id)
        return Message.objects.create(
            room=room,
            sender=self.user,
            content=content
        )

    @database_sync_to_async
    def save_file_message(self, file_name, file_content):
        """Dosyayı AWS'ye kaydet"""
        try:
            print("1. Dosya kaydetme başladı:", file_name)
            
            # Base64'ten dosyayı decode et
            format, filestr = file_content.split(';base64,')
            content_type = format.split(':')[1]
            print("2. Content type:", content_type)
            
            # Dosya boyutunu kontrol et
            file_size = len(base64.b64decode(filestr))
            print("3. Dosya boyutu:", file_size, "bytes")
            if file_size > settings.CHAT_FILE_STORAGE['max_size']:
                raise ValidationError('Dosya boyutu çok büyük (max 10MB)')
            
            # Dosya tipini kontrol et
            if content_type not in settings.CHAT_FILE_STORAGE['allowed_types']:
                print("4. Geçersiz dosya tipi:", content_type)
                raise ValidationError('Geçersiz dosya tipi')
            
            # Güvenli dosya adı oluştur
            safe_filename = f"{uuid.uuid4().hex}_{secure_filename(file_name)}"
            file_path = datetime.now().strftime(settings.CHAT_FILE_STORAGE['upload_path']) + safe_filename
            print("5. Oluşturulan dosya yolu:", file_path)
            
            # Dosyayı kaydet
            print("6. Storage tipi:", type(default_storage).__name__)
            file_name = default_storage.save(file_path, ContentFile(base64.b64decode(filestr)))
            file_url = default_storage.url(file_name)
            print("7. Kaydedilen dosya URL'i:", file_url)
            
            # Mesaj oluştur
            room = Room.objects.get(id=self.room_id)
            message = Message.objects.create(
                room=room,
                sender=self.user,
                content=f"{self.user.email} tarafından dosya gönderildi: {file_name}",
                file=file_path
            )
            print("8. Mesaj oluşturuldu, ID:", message.id)
            
            logger.info(f"Dosya başarıyla yüklendi: {file_url}")
            return message

        except ValidationError as e:
            print("HATA - Validasyon:", str(e))
            logger.error(f"Dosya doğrulama hatası: {str(e)}")
            raise
        except Exception as e:
            print("HATA - Genel:", str(e))
            logger.error(f"Dosya yükleme hatası: {str(e)}")
            raise
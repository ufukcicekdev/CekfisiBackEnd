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
import jwt

logger = logging.getLogger(__name__)
User = get_user_model()

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """WebSocket bağlantısını başlat"""
        try:
            logger.info("WebSocket bağlantı denemesi başladı")
            self.room_id = self.scope['url_route']['kwargs']['room_id']
            self.room_group_name = f'chat_{self.room_id}'
            self.ping_task = None

            # Token doğrulaması
            query_string = self.scope['query_string'].decode('utf-8')
            logger.debug(f"Query string: {query_string}")
            
            params = dict(x.split('=') for x in query_string.split('&'))
            token = params.get('token', '')

            if not token:
                logger.error(f"Token bulunamadı: Oda {self.room_id}")
                await self.close(code=4001)
                return

            try:
                # Token'ı doğrula
                logger.debug(f"Token doğrulama başladı: {token[:10]}...")
                decoded_token = jwt.decode(
                    token, 
                    settings.SECRET_KEY, 
                    algorithms=settings.CHANNEL_SECURITY['ALGORITHMS']
                )
                user_id = decoded_token['user_id']
                logger.debug(f"Token doğrulandı. User ID: {user_id}")
                
                # Kullanıcıyı al ve sakla
                self.user = await self.get_user_from_token(user_id)
                if not self.user:
                    logger.error(f"Kullanıcı bulunamadı: {user_id}")
                    await self.close(code=4002)
                    return

                logger.debug(f"Kullanıcı bulundu: {self.user.email}")

                # Önce bağlantıyı kabul et
                await self.accept()
                logger.info(f"WebSocket bağlantısı kabul edildi: {self.user.email}")

                # Odaya katıl
                await self.channel_layer.group_add(
                    self.room_group_name,
                    self.channel_name
                )
                
                logger.info(f"Kullanıcı {self.user.email} odaya başarıyla bağlandı: {self.room_id}")
                
                # Bağlantı başarılı mesajı gönder
                await self.send(text_data=json.dumps({
                    'type': 'connection_established',
                    'message': 'Bağlantı başarılı',
                    'user': {
                        'email': self.user.email,
                        'id': self.user.id
                    }
                }))

                # Ping/Pong başlat
                self.ping_task = asyncio.create_task(self.ping_loop())
                logger.debug("Ping/Pong görevi başlatıldı")
                
            except jwt.ExpiredSignatureError:
                logger.error("Token süresi dolmuş")
                await self.close(code=4001)
            except jwt.InvalidTokenError:
                logger.error("Geçersiz token")
                await self.close(code=4002)
            except Exception as e:
                logger.error(f"Bağlantı hatası: {str(e)}")
                await self.close(code=4000)

        except Exception as e:
            logger.error(f"Genel bağlantı hatası: {str(e)}")
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
            logger.debug(f"Alınan mesaj: {data}")
            
            if message_type == 'ping':
                await self.send(text_data=json.dumps({"type": "pong"}))
                logger.debug("Pong gönderildi")
                return
            elif message_type == 'pong':
                logger.debug("Pong alındı")
                return
            elif message_type == 'message':
                message_data = data.get('data', {})
                content = message_data.get('content')
                room_id = message_data.get('room_id', self.room_id)
                if not content:
                    logger.error("Mesaj içeriği eksik")
                    return

                # Room ID kontrolü
                if str(self.room_id) != str(room_id):
                    logger.error(f"Room ID uyuşmazlığı: Beklenen {self.room_id}, Gelen {room_id}")
                    return

                # Mesajı kaydet
                message = await self.save_message(content)
                logger.info(f"Yeni mesaj kaydedildi: {message.id}")
                
                # Mesajı gruba gönder
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
                            'timestamp': message.timestamp.isoformat(),
                            'room_id': self.room_id
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
                logger.info(f"Bildirim gönderildi: {message.id}")

        except json.JSONDecodeError:
            logger.error("Geçersiz JSON formatı")
        except Exception as e:
            logger.error(f"Mesaj işleme hatası: {str(e)}")
            logger.exception(e)  # Stack trace için

    async def chat_message(self, event):
        """Mesajı WebSocket'e gönder"""
        try:
            data = event.get('data', {})
            
            # Frontend'in beklediği formatta mesaj gönder
            response = {
                'type': 'message',
                'data': {
                    'id': data.get('id'),
                    'content': data.get('content'),
                    'sender': data.get('sender', {}),
                    'timestamp': data.get('timestamp'),
                    'room_id': data.get('room_id')
                }
            }

            await self.send(text_data=json.dumps(response))
            logger.debug(f"Mesaj gönderildi: {data.get('id')}")
        except Exception as e:
            logger.error(f"Mesaj gönderme hatası: {str(e)}")
            logger.exception(e)  # Stack trace için

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
        try:
            room = Room.objects.get(id=self.room_id)
            message = Message.objects.create(
                room=room,
                sender=self.user,
                content=content
            )
            logger.info(f"Mesaj veritabanına kaydedildi: {message.id}")
            return message
        except Room.DoesNotExist:
            logger.error(f"Oda bulunamadı: {self.room_id}")
            raise
        except Exception as e:
            logger.error(f"Mesaj kaydetme hatası: {str(e)}")
            raise
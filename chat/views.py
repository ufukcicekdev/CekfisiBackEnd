from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Room, Message
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .serializers import RoomSerializer, MessageSerializer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.pagination import PageNumberPagination
from django.core.exceptions import ValidationError
import uuid
from werkzeug.utils import secure_filename
from datetime import datetime
from django.conf import settings

User = get_user_model()

@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def room_list(request):
    if request.user.user_type == 'accountant':
        rooms = Room.objects.filter(accountant=request.user)
    else:
        rooms = Room.objects.filter(client=request.user)
    serializer = RoomSerializer(rooms, many=True)
    return Response(serializer.data)

class MessagePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50
    
    def get_paginated_response(self, data):
        return Response({
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'count': self.page.paginator.count,
            'total_pages': self.page.paginator.num_pages,
            'current_page': self.page.number,
            'results': data
        })

@api_view(['GET', 'POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def room_messages(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    
    if request.user != room.accountant and request.user != room.client:
        return Response(status=status.HTTP_403_FORBIDDEN)
    
    if request.method == 'GET':
        messages = Message.objects.filter(room=room).order_by('-timestamp')
        paginator = MessagePagination()
        paginated_messages = paginator.paginate_queryset(messages, request)
        serializer = MessageSerializer(paginated_messages, many=True)
        return paginator.get_paginated_response(serializer.data)
    
    elif request.method == 'POST':
        print("REQUEST DATA:", request.data)
        
        # Dosya mesajı kontrolü
        if request.data.get('message_type') == 'file' and request.data.get('file_data'):
            try:
                import json
                file_data = json.loads(request.data.get('file_data'))
                
                message = Message.objects.create(
                    room=room,
                    sender=request.user,
                    message_type='file',
                    content=request.data.get('content'),
                    file_url=file_data['url']  # URL'i file_url alanına kaydediyoruz
                )
                serializer = MessageSerializer(message)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                print("Dosya mesajı oluşturma hatası:", str(e))
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        else:
            # Normal mesaj
            data = request.data.copy()
            data['room'] = room_id
            serializer = MessageSerializer(data=data)
            if serializer.is_valid():
                serializer.save(room=room, sender=request.user)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def create_room(request):
    if not request.user.user_type == 'accountant':
        return Response({"error": "Only accountants can create rooms"}, 
                       status=status.HTTP_403_FORBIDDEN)
    
    client_id = request.data.get('client_id')
    if not client_id:
        return Response({"error": "client_id is required"}, 
                       status=status.HTTP_400_BAD_REQUEST)
    
    try:
        client = User.objects.get(id=client_id, user_type='client')
    except User.DoesNotExist:
        return Response({"error": "Client not found"}, 
                       status=status.HTTP_404_NOT_FOUND)
    
    # Önce mevcut odayı kontrol et
    existing_room = Room.objects.filter(accountant=request.user, client=client).first()
    if existing_room:
        serializer = RoomSerializer(existing_room)
        return Response(serializer.data)
    
    # Yeni oda oluştur
    room = Room.objects.create(
        name=f"room_{request.user.id}_{client.id}",
        accountant=request.user,
        client=client
    )
    
    serializer = RoomSerializer(room)
    return Response(serializer.data, status=status.HTTP_201_CREATED)

class MessageViewSet(viewsets.ModelViewSet):
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Message.objects.filter(room_id=self.kwargs['room_pk'])

    def perform_create(self, serializer):
        print("1. API üzerinden mesaj oluşturma başladı")
        room_id = self.kwargs['room_pk']
        room = Room.objects.get(id=room_id)
        
        file = self.request.FILES.get('file')
        if file:
            print("2. Dosya bilgileri:", file.name, file.content_type, file.size)
            
            # Dosya tipi kontrolü
            if file.content_type not in settings.CHAT_FILE_STORAGE['allowed_types']:
                print("3. Geçersiz dosya tipi:", file.content_type)
                raise ValidationError('Geçersiz dosya tipi')
            
            # Boyut kontrolü
            if file.size > settings.CHAT_FILE_STORAGE['max_size']:
                print("4. Dosya çok büyük:", file.size)
                raise ValidationError('Dosya boyutu çok büyük (max 10MB)')
            
            # Güvenli dosya adı
            safe_filename = f"{uuid.uuid4().hex}_{secure_filename(file.name)}"
            file_path = datetime.now().strftime(settings.CHAT_FILE_STORAGE['upload_path']) + safe_filename
            print("5. Dosya yolu:", file_path)
            
            try:
                serializer.save(
                    room=room,
                    sender=self.request.user,
                    file=file,
                    message_type='file',
                    content=f"{self.request.user.email} tarafından dosya gönderildi: {file.name}"
                )
                print("6. Dosya başarıyla kaydedildi")
            except Exception as e:
                print("HATA:", str(e))
                raise
        else:
            serializer.save(
                room=room, 
                sender=self.request.user,
                message_type='text'
            )

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_message(request, room_id):
    print("Content-Type:", request.headers.get('content-type'))
    print("REQUEST FILES:", request.FILES)
    print("REQUEST DATA:", request.data)
    
    room = get_object_or_404(Room, id=room_id)
    file = request.FILES.get('file')
    
    if not file:
        print("Dosya bulunamadı")
        return Response({'error': 'Dosya bulunamadı'}, status=status.HTTP_400_BAD_REQUEST)
        
    try:
        print("Gelen dosya:", file.name, file.content_type, file.size)
        message = Message.objects.create(
            room=room,
            sender=request.user,
            message_type='file',
            file=file,
            content=f"📎 {file.name}"
        )
        serializer = MessageSerializer(message)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    except Exception as e:
        print("Dosya yükleme hatası:", str(e))
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST) 
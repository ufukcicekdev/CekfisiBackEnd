from rest_framework import serializers
from .models import Room, Message
from django.contrib.auth import get_user_model

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'user_type', 'first_name', 'last_name']

class MessageSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)
    
    class Meta:
        model = Message
        fields = ['id', 'room', 'sender', 'content', 'message_type', 'file_url', 'timestamp']
        read_only_fields = ['sender', 'message_type', 'file_url']
        extra_kwargs = {
            'room': {'write_only': True}
        }

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['sender'] = {
            'id': instance.sender.id,
            'email': instance.sender.email,
            'user_type': instance.sender.user_type
        }
        
        # Dosya bilgilerini ekle
        if instance.message_type == 'file':
            representation['file'] = {
                'url': instance.file_url,
                'name': instance.file_url.split('/')[-1] if instance.file_url else None,
                'type': 'image/png',  # Frontend'den gelen type'ı kullanabilirsiniz
                'size': None  # Frontend'den gelen size'ı kullanabilirsiniz
            }
        else:
            representation['file'] = None
            
        return representation

class RoomSerializer(serializers.ModelSerializer):
    accountant = UserSerializer(read_only=True)
    client = UserSerializer(read_only=True)
    last_message = serializers.SerializerMethodField()

    class Meta:
        model = Room
        fields = ['id', 'name', 'accountant', 'client', 'created_at', 'last_message']
        read_only_fields = ['name', 'accountant', 'client', 'created_at']

    def get_last_message(self, obj):
        try:
            last_message = obj.messages.order_by('-timestamp').first()
            if last_message:
                # MessageSerializer'ı kullanmak yerine manuel olarak oluşturalım
                message_data = {
                    'id': last_message.id,
                    'content': last_message.content,
                    'message_type': last_message.message_type,
                    'timestamp': last_message.timestamp,
                    'sender': {
                        'id': last_message.sender.id,
                        'email': last_message.sender.email,
                        'user_type': last_message.sender.user_type
                    }
                }
                
                # Dosya bilgilerini ekle
                if last_message.message_type == 'file':
                    message_data['file'] = {
                        'url': last_message.file_url or (last_message.file.url if last_message.file else None),
                        'name': last_message.file.name.split('/')[-1] if last_message.file else None
                    }
                else:
                    message_data['file'] = None
                
                return message_data
            return None
        except Exception as e:
            print(f"Last message error: {str(e)}")
            return None 
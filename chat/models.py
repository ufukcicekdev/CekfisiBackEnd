from django.db import models
from django.conf import settings  # AUTH_USER_MODEL için

class Room(models.Model):
    name = models.CharField(max_length=255)
    accountant = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='accountant_rooms', on_delete=models.CASCADE)
    client = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='client_rooms', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.accountant.username} - {self.client.username}"

class Message(models.Model):
    MESSAGE_TYPES = (
        ('text', 'Text'),
        ('file', 'File'),
    )
    
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content = models.TextField(blank=True, null=True)  # İçerik opsiyonel olabilir
    message_type = models.CharField(max_length=10, choices=MESSAGE_TYPES, default='text')
    file = models.FileField(
        upload_to='chat_files/%Y/%m/%d/', 
        null=True, 
        blank=True,
        max_length=500  # URL uzunluğu için yeterli alan
    )
    file_url = models.URLField(max_length=500, null=True, blank=True)  # Dosya URL'i için yeni alan
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']  # En son mesaj en üstte

    def __str__(self):
        if self.message_type == 'file':
            return f"{self.sender.email} - {self.file.name}"
        return f"{self.sender.email}: {self.content[:50]}" 
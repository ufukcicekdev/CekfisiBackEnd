from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('rooms/', views.room_list, name='room_list'),
    path('rooms/create/', views.create_room, name='create_room'),
    path('rooms/<int:room_id>/messages/', views.room_messages, name='room_messages'),
    path('rooms/<int:room_id>/upload/', views.upload_message, name='upload-message'),
] 
from django.urls import path, include
from user_profile import views

urlpatterns = [
    path('profile/', views.profile, name='profile'),
    path('accounts/login', views.login_oauth, name='login'),
    path('oauth/', include('social_django.urls', namespace='social')),
    path('logout/', views.logout, name='logout'),
    path('update/', views.update_list_of_participants, name='update'),
    path('', views.index, name='homepage'),
    path('participants/', views.participants, name='participants'),
]

from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('accounts/login/', views.login_view, name='login'),
    path('accounts/logout/', views.logout_view, name='logout'),
    path('accounts/api-keys/', views.api_keys, name='api-keys'),
    path('accounts/api-keys/create/', views.create_api_key, name='create-api-key'),
    path('accounts/api-keys/<str:key_id>/revoke/', views.revoke_api_key, name='revoke-api-key'),
]

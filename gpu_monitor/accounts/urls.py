from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('accounts/login/', views.login_view, name='login'),
    path('accounts/logout/', views.logout_view, name='logout'),
    path('accounts/api-keys/', views.api_keys, name='api-keys'),
    path('accounts/api-keys/create/', views.create_api_key, name='create-api-key'),
    path('accounts/api-keys/<str:key_id>/revoke/', views.revoke_api_key, name='revoke-api-key'),
    path('accounts/tags/', views.tags, name='tags'),
    path('accounts/tags/create/', views.create_tag, name='create-tag'),
    path('accounts/tags/<str:tag_id>/update/', views.update_tag, name='update-tag'),
    path('accounts/tags/<str:tag_id>/delete/', views.delete_tag, name='delete-tag'),
    path('dashboard/rigs/<str:uuid>/tags/<str:tag_id>/toggle/', views.rig_toggle_tag, name='rig-toggle-tag'),
]

from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'accounts'

urlpatterns = [
    path('accounts/register/', views.register_view, name='register'),
    path('accounts/login/', views.login_view, name='login'),
    path('accounts/logout/', views.logout_view, name='logout'),
    path('accounts/profile/', views.profile_view, name='profile'),
    path('accounts/change-password/', views.profile_view, name='change-password'),
    path('accounts/api-keys/', views.api_keys, name='api-keys'),
    path('accounts/api-keys/create/', views.create_api_key, name='create-api-key'),
    path('accounts/admin/transfer-keys/', views.admin_transfer_keys, name='admin-transfer-keys'),
    path('accounts/api-keys/<str:key_id>/revoke/', views.revoke_api_key, name='revoke-api-key'),
    path('accounts/api-keys/<str:key_id>/delete/', views.delete_api_key, name='delete-api-key'),
    path('accounts/api-keys/<str:key_id>/reactivate/', views.reactivate_api_key, name='reactivate-api-key'),
    path('accounts/tags/', views.tags, name='tags'),
    path('accounts/tags/create/', views.create_tag, name='create-tag'),
    path('accounts/tags/<str:tag_id>/update/', views.update_tag, name='update-tag'),
    path('accounts/tags/<str:tag_id>/delete/', views.delete_tag, name='delete-tag'),
    # Password reset
    path('accounts/password-reset/',
         auth_views.PasswordResetView.as_view(
             template_name='accounts/password_reset.html',
             email_template_name='accounts/password_reset_email.html',
             subject_template_name='accounts/password_reset_subject.txt',
             success_url='/accounts/password-reset/done/',
         ), name='password-reset'),
    path('accounts/password-reset/done/',
         auth_views.PasswordResetDoneView.as_view(
             template_name='accounts/password_reset_done.html',
         ), name='password-reset-done'),
    path('accounts/reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(
             template_name='accounts/password_reset_confirm.html',
             success_url='/accounts/reset/done/',
         ), name='password-reset-confirm'),
    path('accounts/reset/done/',
         auth_views.PasswordResetCompleteView.as_view(
             template_name='accounts/password_reset_complete.html',
         ), name='password-reset-complete'),
]

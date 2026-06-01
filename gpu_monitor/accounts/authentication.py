from rest_framework import authentication, exceptions
from .models import ApiKey


class APIKeyAuthentication(authentication.BaseAuthentication):
    """Authenticate agent requests via X-API-Key header."""

    def authenticate(self, request):
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            return None

        key_obj, error = ApiKey.validate_key(api_key)
        if not key_obj:
            raise exceptions.AuthenticationFailed(error or "Invalid API key")

        return (key_obj.user, key_obj)

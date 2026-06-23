import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


class User(AbstractUser):
    email = models.EmailField(unique=True)
    is_admin = models.BooleanField(default=False)
    electricity_rate_kwh = models.DecimalField(
        max_digits=6, decimal_places=4, default=0.3300,
        help_text="Electricity cost per kWh"
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return self.email

    def get_safe_identifier(self):
        """Return a privacy-safe identifier for display in shared contexts.
        
        Uses a short 8-character hex hash of the user's integer primary key.
        This is anonymous — no email prefix, no username, just a short ID.
        Example: '00000001', '00000002', etc.
        """
        return f'{self.id:08x}'


class ApiKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_keys')
    name = models.CharField(max_length=255)
    base_name = models.CharField(max_length=255, blank=True)
    key_hash = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    transfer_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('user', 'name')
        db_table = 'accounts_apikey'

    def __str__(self):
        return f"{self.name} ({self.user})"

    @staticmethod
    def hash_key(plaintext: str) -> str:
        ph = PasswordHasher(memory_cost=65536, time_cost=3, parallelism=4)
        return ph.hash(plaintext)

    def verify_key(self, plaintext: str) -> bool:
        ph = PasswordHasher()
        try:
            return ph.verify(self.key_hash, plaintext)
        except (VerifyMismatchError, Exception):
            return False

    @classmethod
    def validate_key(cls, plaintext: str):
        """Validate an API key string. Returns (ApiKey, error_message)."""
        for key_obj in cls.objects.filter(is_active=True).select_related('user'):
            ph = PasswordHasher()
            try:
                if ph.verify(key_obj.key_hash, plaintext):
                    key_obj.last_used_at = timezone.now()
                    key_obj.save(update_fields=['last_used_at'])
                    return key_obj, None
            except (VerifyMismatchError, Exception):
                continue
        return None, "Invalid or revoked API key"

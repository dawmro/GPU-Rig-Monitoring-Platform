import hashlib
import hmac
import secrets
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.core.exceptions import ImproperlyConfigured


class User(AbstractUser):
    email = models.EmailField(
        unique=True,
    )

    is_admin = models.BooleanField(
        default=False,
    )

    electricity_rate_kwh = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=0.3300,
        help_text="Electricity cost per kWh",
    )

    USERNAME_FIELD = "email"

    REQUIRED_FIELDS = [
        "username",
    ]

    def __str__(self):
        return self.email

    def get_safe_identifier(self):
        return f"{self.id:08x}"


class ApiKey(models.Model):
    """
    API credentials used by monitoring agents.

    Authentication uses two separate values:

        key_lookup
            Fast database lookup identifier (HMAC-SHA256).

        key_hash
            Slow Argon2 authentication verifier.

    The plaintext API key is NEVER stored.
    """

    # ================================================================
    # Argon2 configuration
    # ================================================================

    ARGON2_MEMORY_COST = 65536
    ARGON2_TIME_COST = 3
    ARGON2_PARALLELISM = 4

    # Class-level cached hasher (singleton pattern)
    _password_hasher = None

    # ================================================================
    # Identity
    # ================================================================

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )

    name = models.CharField(max_length=255)

    base_name = models.CharField(
        max_length=255,
        blank=True,
    )

    # ================================================================
    # Authentication
    # ================================================================

    key_hash = models.CharField(
        max_length=255,
        help_text=(
            "Argon2 hash used to cryptographically verify "
            "the plaintext API key."
        ),
    )

    key_lookup = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "HMAC-SHA256 lookup identifier used to find the "
            "corresponding API key efficiently."
        ),
    )

    # ================================================================
    # Status
    # ================================================================

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    transfer_count = models.PositiveIntegerField(
        default=0,
    )

    # ================================================================
    # Meta
    # ================================================================

    class Meta:
        db_table = "accounts_apikey"
        unique_together = (
            "user",
            "name",
        )

    def __str__(self):
        return f"{self.name} ({self.user})"

    # ================================================================
    # LOOKUP (HMAC-SHA256)
    # ================================================================

    @classmethod
    def get_key_lookup(cls, plaintext: str) -> str:
        """
        Generate the deterministic lookup identifier for an API key.

        Purpose:
            FAST DATABASE LOOKUP ONLY.

        This value is NOT used as the final authentication proof.

        Algorithm:
            HMAC-SHA256(server_secret, plaintext_api_key)

        The server-side secret prevents someone with only a database
        dump from freely reproducing lookup values.
        """
        secret = settings.API_KEY_LOOKUP_SECRET.encode("utf-8")
        message = plaintext.encode("utf-8")
        return hmac.new(secret, message, hashlib.sha256).hexdigest()

    # ================================================================
    # AUTHENTICATION HASH (Argon2)
    # ================================================================

    @classmethod
    def get_password_hasher(cls) -> PasswordHasher:
        """
        Create the Argon2 password/secret hasher.

        This is intentionally expensive.

        It is used for cryptographic verification after the
        key_lookup has already identified the candidate row.

        Uses singleton pattern to avoid allocating 64 MB per request.
        """
        if cls._password_hasher is None:
            cls._password_hasher = PasswordHasher(
                memory_cost=cls.ARGON2_MEMORY_COST,
                time_cost=cls.ARGON2_TIME_COST,
                parallelism=cls.ARGON2_PARALLELISM,
            )
        return cls._password_hasher

    @classmethod
    def hash_key(cls, plaintext: str) -> str:
        """
        Generate the Argon2 authentication hash.

        Purpose:
            AUTHENTICATION ONLY.

        This value is intentionally expensive to calculate and verify.
        """
        password_hasher = cls.get_password_hasher()
        return password_hasher.hash(plaintext)

    # ================================================================
    # KEY GENERATION
    # ================================================================

    @classmethod
    def generate_key(cls) -> str:
        """
        Generate a new cryptographically secure API key.

        The plaintext value is returned only to the caller that
        creates the key.

        It is never stored in the database.
        """
        return secrets.token_urlsafe(32)

    # ================================================================
    # KEY CREATION
    # ================================================================

    @classmethod
    def create_key(cls, *, user, name: str, base_name: str = ""):
        """
        Create a new API key.

        Returns:
            (ApiKey database object, plaintext API key)

        The plaintext API key should be shown/stored by the caller
        because it cannot be recovered later.
        """
        plaintext = cls.generate_key()

        key_hash = cls.hash_key(plaintext)
        key_lookup = cls.get_key_lookup(plaintext)

        api_key = cls.objects.create(
            user=user,
            name=name,
            base_name=base_name,
            key_hash=key_hash,
            key_lookup=key_lookup,
        )

        return api_key, plaintext

    # ================================================================
    # ARGON2 VERIFICATION
    # ================================================================

    @classmethod
    def verify_key(cls, key_obj, plaintext: str, password_hasher: PasswordHasher) -> bool:
        """
        Cryptographically verify a plaintext API key.

        IMPORTANT:

            key_lookup does NOT authenticate the request.

            Argon2 verification happens here.
        """
        try:
            return password_hasher.verify(
                key_obj.key_hash,
                plaintext,
            )

        except VerifyMismatchError:
            return False

    # ================================================================
    # LAST USED (throttled to 5-minute intervals)
    # ================================================================

    LAST_USED_UPDATE_INTERVAL = timezone.timedelta(minutes=5)

    @classmethod
    def update_last_used(cls, key_obj):
        """
        Update the last-used timestamp (throttled to 5-minute intervals).
        Uses direct UPDATE to avoid loading the object.
        """
        now = timezone.now()
        if key_obj.last_used_at and (now - key_obj.last_used_at) < cls.LAST_USED_UPDATE_INTERVAL:
            return
        cls.objects.filter(pk=key_obj.pk).update(last_used_at=now)

    # ================================================================
    # FAST AUTHENTICATION PATH (migrated keys)
    # ================================================================

    @classmethod
    def _validate_using_lookup(cls, plaintext: str, key_lookup: str, password_hasher: PasswordHasher):
        """
        Authenticate a migrated API key.

        Steps:
            1. Find candidate using indexed key_lookup.
            2. Verify plaintext using Argon2.
            3. Return authenticated ApiKey.
        """
        key_obj = (
            cls.objects
            .select_related("user")
            .filter(key_lookup=key_lookup, is_active=True)
            .first()
        )

        if key_obj is None:
            return None

        if not cls.verify_key(key_obj=key_obj, plaintext=plaintext, password_hasher=password_hasher):
            return None

        cls.update_last_used(key_obj)
        return key_obj

    # ================================================================
    # LEGACY AUTHENTICATION PATH + AUTO-MIGRATION
    # ================================================================

    @classmethod
    def _validate_legacy_key(cls, plaintext: str, key_lookup: str, password_hasher: PasswordHasher):
        """
        Authenticate an existing API key that has not yet received
        its key_lookup value.

        This is the migration path for existing credentials.

        IMPORTANT:

            key_lookup is only saved AFTER successful Argon2
            verification.

        Therefore an attacker cannot populate key_lookup with a
        value for a key they do not actually possess.
        
        Uses iterator(chunk_size=100) to process legacy keys in chunks,
        avoiding loading all into memory at once.
        
        Handles race condition: if concurrent request migrates the same key,
        fall back to fast path lookup.
        """
        candidates = (
            cls.objects
            .select_related("user")
            .filter(is_active=True, key_lookup__isnull=True)
            .iterator(chunk_size=100)
        )

        for key_obj in candidates:
            if not cls.verify_key(key_obj=key_obj, plaintext=plaintext, password_hasher=password_hasher):
                continue

            # Successful legacy authentication → migrate
            key_obj.key_lookup = key_lookup
            cls.update_last_used(key_obj)
            key_obj.save(update_fields=["key_lookup", "last_used_at"])
            return key_obj

        # Race condition fallback: key may have been migrated by concurrent request
        # Fall back to fast path lookup
        return cls._validate_using_lookup(plaintext, key_lookup, password_hasher)

    # ================================================================
    # PUBLIC VALIDATION API
    # ================================================================

    @classmethod
    def validate_key(cls, plaintext: str):
        """
        Validate an API key.

        Authentication flow:
            plaintext
                │
                ├── HMAC-SHA256
                │       │
                │       ▼
                │   key_lookup
                │       │
                │       ▼
                │   indexed DB lookup
                │       │
                │       ▼
                │   Argon2 verification
                │
                └── legacy fallback if lookup is not populated

        Returns:
            (ApiKey, None) or (None, "Invalid or revoked API key")
        """
        if not plaintext:
            return None, "Invalid or revoked API key"

        # FAST DETERMINISTIC LOOKUP
        key_lookup = cls.get_key_lookup(plaintext)

        # Create one Argon2 verifier for this authentication request.
        password_hasher = cls.get_password_hasher()

        # FAST PATH: migrated keys
        key_obj = cls._validate_using_lookup(
            plaintext=plaintext,
            key_lookup=key_lookup,
            password_hasher=password_hasher,
        )
        if key_obj is not None:
            return key_obj, None

        # LEGACY PATH: only unmigrated keys
        key_obj = cls._validate_legacy_key(
            plaintext=plaintext,
            key_lookup=key_lookup,
            password_hasher=password_hasher,
        )
        if key_obj is not None:
            return key_obj, None

        return None, "Invalid or revoked API key"
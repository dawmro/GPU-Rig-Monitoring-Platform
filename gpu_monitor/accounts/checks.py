from django.core.checks import Error, register


@register('accounts')
def check_api_key_format(app_configs, **kwargs):
    """
    System check to ensure API key format is correct (64-char hex).

    This defends against regressions where generate_key() might be changed
    to produce a different format (e.g., token_urlsafe instead of token_hex).
    """
    errors = []

    from accounts.models import ApiKey

    # Layer 1: Verify generate_key produces 64-char hex string
    try:
        test_key = ApiKey.generate_key()
        if len(test_key) != 64:
            errors.append(Error(
                f'ApiKey.generate_key() produces {len(test_key)}-char key, expected 64',
                hint='Use secrets.token_hex(32) in generate_key()',
                obj='accounts.ApiKey.generate_key',
                id='accounts.E001',
            ))
        # Verify it's valid hex
        try:
            int(test_key, 16)
        except ValueError:
            errors.append(Error(
                'ApiKey.generate_key() produces non-hex characters',
                hint='Use secrets.token_hex(32) in generate_key()',
                obj='accounts.ApiKey.generate_key',
                id='accounts.E002',
            ))
    except Exception as e:
        errors.append(Error(
            f'ApiKey.generate_key() raised exception: {e}',
            hint='Check generate_key() implementation',
            obj='accounts.ApiKey.generate_key',
            id='accounts.E003',
        ))

    # Layer 2: Verify key_lookup generation works with 64-char hex
    try:
        from django.conf import settings
        test_key = ApiKey.generate_key()
        key_lookup = ApiKey.get_key_lookup(test_key)
        if len(key_lookup) != 64:  # HMAC-SHA256 hex digest is 64 chars
            errors.append(Error(
                f'ApiKey.get_key_lookup() produces {len(key_lookup)}-char lookup, expected 64',
                hint='Check get_key_lookup() implementation',
                obj='accounts.ApiKey.get_key_lookup',
                id='accounts.E004',
            ))
    except Exception as e:
        errors.append(Error(
            f'ApiKey.get_key_lookup() raised exception: {e}',
            hint='Check get_key_lookup() implementation',
            obj='accounts.ApiKey.get_key_lookup',
            id='accounts.E005',
        ))

    # Layer 3: Verify hash_key works with 64-char hex
    try:
        test_key = ApiKey.generate_key()
        key_hash = ApiKey.hash_key(test_key)
        # Argon2 hash should start with $argon2
        if not key_hash.startswith('$argon2'):
            errors.append(Error(
                'ApiKey.hash_key() does not produce valid Argon2 hash',
                hint='Check hash_key() implementation',
                obj='accounts.ApiKey.hash_key',
                id='accounts.E006',
            ))
    except Exception as e:
        errors.append(Error(
            f'ApiKey.hash_key() raised exception: {e}',
            hint='Check hash_key() implementation',
            obj='accounts.ApiKey.hash_key',
            id='accounts.E007',
        ))

    return errors
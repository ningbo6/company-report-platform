import hashlib
import hmac
import secrets


def hash_password(password):
    if len(password) < 12:
        raise ValueError('密码至少 12 位')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600_000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def verify_password(password, encoded):
    try:
        algo, rounds, salt, expected = encoded.split('$')
        if algo != 'pbkdf2_sha256':
            return False
        got = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        return False


def password_tag(encoded):
    return hashlib.sha256(encoded.encode()).hexdigest()

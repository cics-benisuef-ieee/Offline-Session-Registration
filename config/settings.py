"""Django settings for the CS/CIS Beni-Suef Chapter session registration project."""

import os
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, True),
    DJANGO_SECURE_COOKIES=(bool, False),
)

# Read local environment files when present. Vercel provides real environment variables.
for env_file in (BASE_DIR / ".env", BASE_DIR / ".env.local"):
    if env_file.exists():
        environ.Env.read_env(env_file, overwrite=env_file.name == ".env.local")

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="dev-only-change-me-before-deploying-offline-session-registration",
)

DEBUG = env.bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", ".vercel.app"],
)

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["https://*.vercel.app"],
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "crispy_forms",
    "crispy_bootstrap5",
    "accounts",
    "units",
    "places",
    "bookings",
    "notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}

_database_engine = DATABASES["default"]["ENGINE"]

if os.environ.get("VERCEL") and _database_engine == "django.db.backends.sqlite3":
    raise ImproperlyConfigured(
        "DATABASE_URL must be set to a PostgreSQL connection string on Vercel."
    )

if not DEBUG and _database_engine == "django.db.backends.sqlite3":
    raise ImproperlyConfigured(
        "DATABASE_URL must be set to a PostgreSQL connection string when "
        "DJANGO_DEBUG=False."
    )

if _database_engine == "django.db.backends.postgresql":
    _database_options = DATABASES["default"].setdefault("OPTIONS", {})

    if not DEBUG or os.environ.get("VERCEL"):
        _database_options.setdefault("sslmode", env("DB_SSLMODE", default="require"))

    # Fail quickly instead of letting a Vercel request hang on a bad database connection.
    _database_options.setdefault(
        "connect_timeout", env.int("DB_CONNECT_TIMEOUT", default=10)
    )

    # Vercel functions should not keep connections open between invocations.
    DATABASES["default"].setdefault("CONN_MAX_AGE", 0)
    DATABASES["default"].setdefault("CONN_HEALTH_CHECKS", True)

    # Supabase's transaction pooler (port 6543) does not support named prepared
    # statements or server-side cursors. Disable both when using that pooler.
    if (
        os.environ.get("VERCEL")
        or str(DATABASES["default"].get("PORT")) == "6543"
    ):
        _database_options.setdefault("prepare_threshold", None)
        DATABASES["default"].setdefault("DISABLE_SERVER_SIDE_CURSORS", True)

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

AUTH_USER_MODEL = "accounts.User"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="sessions@example.com")

# Shared secret for scheduler-triggered endpoints. Vercel sends it as
# Authorization: Bearer <CRON_SECRET>; GitHub Actions uses the same value.
CRON_SECRET = env("CRON_SECRET", default="")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=not DEBUG)
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_HSTS_SECONDS = env.int(
    "DJANGO_SECURE_HSTS_SECONDS",
    default=31536000 if not DEBUG else 0,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
    default=not DEBUG,
)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

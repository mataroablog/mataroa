from django.conf import settings


def get_protocol():
    if settings.DEBUG:
        return "http:"
    else:
        return "https:"

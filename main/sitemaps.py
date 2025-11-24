from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from main import models


class StaticSitemap(Sitemap):
    priority = 1.0
    changefreq = "always"

    def items(self):
        return ["index"]

    def location(self, obj):
        return reverse(obj)


class PostSitemap(Sitemap):
    priority = 1.0
    changefreq = "daily"

    def __init__(self, subdomain):
        self.subdomain = subdomain

    def items(self):
        return (
            models.Post.objects.filter(
                owner__username=self.subdomain,
                published_at__isnull=False,
                published_at__lte=timezone.now().date(),
            )
            .order_by("-published_at")
            .select_related("owner")
        )

    def location(self, obj):
        return obj.url_path

    def lastmod(self, obj):
        return obj.updated_at


class PageSitemap(Sitemap):
    priority = 0.8
    changefreq = "daily"

    def __init__(self, subdomain):
        self.subdomain = subdomain

    def items(self):
        return models.Page.objects.filter(
            owner__username=self.subdomain, is_hidden=False
        )

    def location(self, obj):
        return reverse("page_detail", kwargs={"slug": obj.slug})

    def lastmod(self, obj):
        return obj.updated_at

from datetime import datetime

from django.contrib.syndication.views import Feed
from django.http import Http404
from django.utils import timezone

from main import models


class RSSBlogFeed(Feed):
    def get_object(self, request, *args, **kwargs):
        if not hasattr(request, "subdomain"):
            raise Http404()
        user = models.User.objects.get(username=request.subdomain)

        models.AnalyticPage.objects.create(user=user, path="rss")

        # URLconf shares this feed instance across requests, so keep the user
        # local and let Django pass it to each feed method.
        return user

    def title(self, user):
        return user.blog_title

    def description(self, user):
        return user.blog_byline_as_text

    def link(self, user):
        return user.blog_url

    def items(self, user):
        return (
            models.Post.objects.filter(
                owner=user,
                published_at__isnull=False,
                published_at__lte=timezone.now().date(),
            )
            .select_related("owner")
            .order_by("-published_at")[:100]
        )

    def item_title(self, item):
        return item.title

    def item_link(self, item):
        return item.get_proper_url()

    def item_description(self, item):
        return item.body_as_html

    def item_pubdate(self, item):
        # set time to 00:00 because we don't store time for published_at field
        return datetime.combine(item.published_at, datetime.min.time())

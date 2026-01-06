from datetime import datetime

from django.contrib.syndication.views import Feed
from django.http import Http404
from django.utils import timezone

from main import models


class RSSBlogFeed(Feed):
    title = ""
    link = ""
    description = ""
    user = None

    def __call__(self, request, *args, **kwargs):
        if not hasattr(request, "subdomain"):
            raise Http404()
        self.user = models.User.objects.get(username=request.subdomain)
        self.title = self.user.blog_title
        self.description = self.user.blog_byline_as_text
        self.link = self.user.blog_url

        models.AnalyticPage.objects.create(user=self.user, path="rss")

        return super().__call__(request, *args, **kwargs)

    def items(self):
        return (
            models.Post.objects.filter(
                owner=self.user,
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

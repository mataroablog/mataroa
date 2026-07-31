from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from main import models


class Command(BaseCommand):
    help = "Generate deterministic sample data for local development."

    def add_arguments(self, parser):
        parser.add_argument(
            "--users", type=int, default=10, help="Number of users (default: 10)."
        )
        parser.add_argument(
            "--posts-per-user",
            type=int,
            default=25,
            help="Posts per user (default: 25).",
        )
        parser.add_argument(
            "--pages-per-user",
            type=int,
            default=3,
            help="Pages per user (default: 3).",
        )
        parser.add_argument(
            "--comments-per-post",
            type=int,
            default=4,
            help="Comments per post (default: 4).",
        )
        parser.add_argument(
            "--subscribers-per-user",
            type=int,
            default=10,
            help="Newsletter subscribers per user (default: 10).",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not settings.LOCALDEV:
            raise CommandError("devdata can only run in development.")

        counts = {
            name: options[name]
            for name in (
                "users",
                "posts_per_user",
                "pages_per_user",
                "comments_per_post",
                "subscribers_per_user",
            )
        }
        if counts["users"] < 1:
            raise CommandError("--users must be at least 1.")
        if any(value < 0 for value in counts.values()):
            raise CommandError("Data counts cannot be negative.")

        with transaction.atomic():
            created = self._generate(**counts)

        self.stdout.write(
            self.style.SUCCESS(
                "Generated development data: "
                + ", ".join(f"{amount} {name}" for name, amount in created.items())
                + "."
            )
        )
        self.stdout.write("Generated admin credentials: admin / admin")

    def _generate(
        self,
        users,
        posts_per_user,
        pages_per_user,
        comments_per_post,
        subscribers_per_user,
    ):
        created = {
            "users": 0,
            "posts": 0,
            "pages": 0,
            "comments": 0,
            "subscribers": 0,
        }
        dev_users = []

        for user_number in range(users):
            is_admin = user_number == 0
            username = "admin" if is_admin else f"devuser{user_number:03d}"
            user, was_created = models.User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@example.com",
                    "blog_title": (
                        "Admin Development Blog"
                        if is_admin
                        else f"Development Blog {user_number:03d}"
                    ),
                    "blog_byline": "A sample blog generated for local development.",
                    "comments_on": True,
                    "is_approved": True,
                    "is_premium": user_number % 3 == 0,
                    "theme_sansserif": user_number % 2 == 0,
                    "theme_zialucia": user_number % 4 == 0,
                    "is_staff": is_admin,
                    "is_superuser": is_admin,
                },
            )
            if was_created:
                user.set_password("admin")
                user.save(update_fields=["password"])
                created["users"] += 1
            dev_users.append(user)

        today = timezone.now().date()
        existing_posts = set(
            models.Post.objects.filter(owner__in=dev_users).values_list(
                "owner_id", "slug"
            )
        )
        posts = []
        for user in dev_users:
            for post_number in range(1, posts_per_user + 1):
                slug = f"sample-post-{post_number:03d}"
                if (user.id, slug) in existing_posts:
                    continue
                if post_number % 10 == 0:
                    published_at = None
                elif post_number % 9 == 0:
                    published_at = today + timedelta(days=post_number)
                else:
                    published_at = today - timedelta(days=post_number)
                posts.append(
                    models.Post(
                        owner=user,
                        title=f"Sample Post {post_number:03d}",
                        slug=slug,
                        body=(
                            f"# Sample Post {post_number:03d}\n\n"
                            "This is deterministic development content with **Markdown**, "
                            "a [link](https://example.com), and enough text to exercise "
                            "blog lists, feeds, exports, and pagination.\n\n"
                            "## Another section\n\n"
                            "Use this post while developing Mataroa features locally."
                        ),
                        published_at=published_at,
                    )
                )
        models.Post.objects.bulk_create(posts)
        created["posts"] = len(posts)

        existing_pages = set(
            models.Page.objects.filter(owner__in=dev_users).values_list(
                "owner_id", "slug"
            )
        )
        pages = []
        for user in dev_users:
            for page_number in range(1, pages_per_user + 1):
                slug = f"sample-page-{page_number:03d}"
                if (user.id, slug) in existing_pages:
                    continue
                pages.append(
                    models.Page(
                        owner=user,
                        title=f"Sample Page {page_number:03d}",
                        slug=slug,
                        body="Sample page content generated for local development.",
                        is_hidden=page_number % 3 == 0,
                    )
                )
        models.Page.objects.bulk_create(pages)
        created["pages"] = len(pages)

        target_slugs = [
            f"sample-post-{post_number:03d}"
            for post_number in range(1, posts_per_user + 1)
        ]
        target_posts = list(
            models.Post.objects.filter(owner__in=dev_users, slug__in=target_slugs)
        )
        existing_comments = set(
            models.Comment.objects.filter(
                post__in=target_posts,
                name__startswith="Development commenter ",
            ).values_list("post_id", "name")
        )
        comments = []
        for post in target_posts:
            for comment_number in range(1, comments_per_post + 1):
                name = f"Development commenter {comment_number:03d}"
                if (post.id, name) in existing_comments:
                    continue
                comments.append(
                    models.Comment(
                        post=post,
                        name=name,
                        email=f"commenter{comment_number:03d}@example.com",
                        body=f"Sample comment {comment_number:03d} on this post.",
                        is_approved=comment_number % 4 != 0,
                    )
                )
        models.Comment.objects.bulk_create(comments)
        created["comments"] = len(comments)

        existing_subscribers = set(
            models.Notification.objects.filter(blog_user__in=dev_users).values_list(
                "blog_user_id", "email"
            )
        )
        subscribers = []
        for user in dev_users:
            for subscriber_number in range(1, subscribers_per_user + 1):
                email = f"subscriber{subscriber_number:03d}+{user.username}@example.com"
                if (user.id, email) in existing_subscribers:
                    continue
                subscribers.append(
                    models.Notification(
                        blog_user=user,
                        email=email,
                        is_active=subscriber_number % 5 != 0,
                    )
                )
        models.Notification.objects.bulk_create(subscribers)
        created["subscribers"] = len(subscribers)

        return created

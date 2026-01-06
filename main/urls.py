from django.contrib import admin
from django.urls import include, path, re_path

from main import feeds
from main.views import api, billing, export, general, moderation

admin.site.site_header = "mataroa admin"

# general
urlpatterns = [
    path("", general.index, name="index"),
    path("blog/", general.blog_index, name="blog_index"),
    path("dashboard/", general.dashboard, name="dashboard"),
    path("about/methodology/", general.methodology, name="methodology"),
    path("about/transparency/", general.transparency, name="transparency"),
    path("about/comparisons/", general.comparisons, name="comparisons"),
    path("guides/markdown/", general.guides_markdown, name="guides_markdown"),
    path("guides/images/", general.guides_images, name="guides_images"),
    path(
        "guides/custom-domain/", general.guides_customdomain, name="guides_customdomain"
    ),
    path("guides/comments/", general.guides_comments, name="guides_comments"),
    path(
        "guides/post-by-email/",
        general.guides_post_by_email,
        name="guides_post_by_email",
    ),
]

# user system
urlpatterns += [
    path("accounts/logout/", general.Logout.as_view(), name="logout"),
    path("accounts/", include("django.contrib.auth.urls")),
    path(
        "accounts/create/",
        general.UserCreateStepOne.as_view(),
        name="user_create",
    ),
    path(
        "accounts/humanity-diagnostics/<uuid:onboard_code>/",
        general.UserCreateStepTwo.as_view(),
        name="user_create_step_two",
    ),
    path("accounts/edit/", general.UserUpdate.as_view(), name="user_update"),
    path("accounts/delete/", general.UserDelete.as_view(), name="user_delete"),
    path("accounts/domain/", general.domain_check, name="domain_check"),
]

# moderation
urlpatterns += [
    path("moderation/index/", moderation.index, name="moderation_index"),
    path("moderation/users/", moderation.user_list, name="moderation_user_list"),
    path("moderation/images/", moderation.images_leaderboard, name="moderation_images"),
    path("moderation/posts/", moderation.posts_leaderboard, name="moderation_posts"),
    path("moderation/stats/", moderation.stats, name="moderation_stats"),
    path(
        "moderation/activity/",
        moderation.activity,
        name="moderation_activity",
    ),
    path("moderation/cohorts/", moderation.cohorts, name="moderation_cohorts"),
    path(
        "moderation/top-posts/",
        moderation.top_posts_alltime,
        name="moderation_top_posts",
    ),
    path(
        "moderation/top-blogs/",
        moderation.top_blogs_alltime,
        name="moderation_top_blogs",
    ),
    path(
        "moderation/top-pages/",
        moderation.top_pages_alltime,
        name="moderation_top_pages",
    ),
    path(
        "moderation/summary/<slug:date_str>/",
        moderation.summary,
        name="moderation_summary",
    ),
    path(
        "moderation/users/<int:user_id>/approve/",
        moderation.user_approve,
        name="moderation_user_approve",
    ),
    path(
        "moderation/users/<int:user_id>/unapprove/",
        moderation.user_unapprove,
        name="moderation_user_unapprove",
    ),
    path(
        "moderation/users/<int:user_id>/delete/",
        moderation.user_delete,
        name="moderation_user_delete",
    ),
]

# blog posts and post snapshots
urlpatterns += [
    path(
        "post-backups/create/", general.SnapshotCreate.as_view(), name="snapshot_create"
    ),
    path("post-backups/", general.SnapshotList.as_view(), name="snapshot_list"),
    path(
        "post-backups/<int:pk>/",
        general.SnapshotDetail.as_view(),
        name="snapshot_detail",
    ),
    path("new/post/", general.PostCreate.as_view(), name="post_create"),
    path("blog/<slug:slug>.md", general.post_raw, name="post_raw"),
    path("p/<slug:slug>.md", general.post_raw, name="post_raw_p"),
    path("blog/<slug:slug>/", general.PostDetail.as_view(), name="post_detail"),
    path("p/<slug:slug>/", general.PostDetail.as_view(), name="post_detail_p"),
    path("posts/<slug:slug>/", general.post_detail_redir, name="post_detail_redir_a"),
    path("post/<slug:slug>/", general.post_detail_redir, name="post_detail_redir_b"),
    path("blog/<slug:slug>/edit/", general.PostUpdate.as_view(), name="post_update"),
    path("blog/<slug:slug>/delete/", general.PostDelete.as_view(), name="post_delete"),
]

# post by email
urlpatterns += [
    path("webhook/postmark/", general.postmark_webhook, name="postmark_webhook"),
]

# blog extras
urlpatterns += [
    path("rss/", feeds.RSSBlogFeed(), name="rss_feed"),
    path("sitemap.xml", general.sitemap, name="sitemap"),
    path("newsletter/", general.Notification.as_view(), name="notification_subscribe"),
    path(
        "newsletter/unsubscribe/",
        general.NotificationUnsubscribe.as_view(),
        name="notification_unsubscribe",
    ),
    path(
        "newsletter/unsubscribe/<uuid:unsubscribe_key>/",
        general.notification_unsubscribe_key,
        name="notification_unsubscribe_key",
    ),
    path(
        "notifications/",
        general.NotificationRecordList.as_view(),
        name="notificationrecord_list",
    ),
    path(
        "notifications/subscribers",
        general.NotificationList.as_view(),
        name="notification_list",
    ),
]

# comments
urlpatterns += [
    path("comments/pending/", general.CommentPending.as_view(), name="comment_pending"),
    path(
        "blog/<slug:slug>/comments/create/author/",
        general.CommentCreateAuthor.as_view(),
        name="comment_create_author",
    ),
    path(
        "blog/<slug:slug>/comments/create/",
        general.CommentCreate.as_view(),
        name="comment_create",
    ),
    path(
        "blog/<slug:slug>/comments/<int:pk>/delete/",
        general.CommentDelete.as_view(),
        name="comment_delete",
    ),
    path(
        "blog/<slug:slug>/comments/<int:pk>/approve/",
        general.CommentApprove.as_view(),
        name="comment_approve",
    ),
]

# billing
urlpatterns += [
    path("billing/overview/", billing.billing_overview, name="billing_overview"),
    path("billing/card/", billing.BillingCard.as_view(), name="billing_card"),
    path(
        "billing/subscribe/",
        billing.BillingSubscribe.as_view(),
        name="billing_subscribe",
    ),
    path(
        "billing/resubscribe/",
        billing.BillingResubscribe.as_view(),
        name="billing_resubscribe",
    ),
    path(
        "billing/card/<slug:stripe_payment_method_id>/delete/",
        billing.BillingCardDelete.as_view(),
        name="billing_card_delete",
    ),
    path(
        "billing/card/<slug:stripe_payment_method_id>/default/",
        billing.billing_card_default,
        name="billing_card_default",
    ),
    path(
        "billing/subscription/welcome/",
        billing.billing_welcome,
        name="billing_welcome",
    ),
    path(
        "billing/subscription/card/confirm/",
        billing.billing_card_confirm,
        name="billing_card_confirm",
    ),
    path(
        "billing/subscription/cancel/",
        billing.BillingCancel.as_view(),
        name="billing_subscription_cancel",
    ),
    path(
        "billing/subscription/resume/",
        billing.BillingResume.as_view(),
        name="billing_subscription_resume",
    ),
    path(
        "webhook/stripe/",
        billing.billing_stripe_webhook,
        name="billing_stripe_webhook",
    ),
]

# blog import, export, webring
urlpatterns += [
    path("webring/", general.WebringUpdate.as_view(), name="webring"),
    path("import/", general.BlogImport.as_view(), name="blog_import"),
    path("export/", export.export_index, name="export_index"),
    path(
        "export/markdown/",
        export.export_markdown,
        name="export_markdown",
    ),
    path("export/zola/", export.export_zola, name="export_zola"),
    path("export/hugo/", export.export_hugo, name="export_hugo"),
    path("export/epub/", export.export_epub, name="export_epub"),
    path("export/print/", export.export_print, name="export_print"),
    path(
        "export/unsubscribe/<uuid:unsubscribe_key>/",
        export.export_unsubscribe_key,
        name="export_unsubscribe_key",
    ),
]

# images
urlpatterns += [
    path("images/<slug:slug>.<slug:extension>", general.image_raw, name="image_raw"),
    re_path(
        r"^images/(?P<options>\?[\w\=]+)?$",  # e.g. images/ or images/?raw=true
        general.ImageList.as_view(),
        name="image_list",
    ),
    path("images/<slug:slug>/", general.ImageDetail.as_view(), name="image_detail"),
    path(
        "images/<slug:slug>/edit/", general.ImageUpdate.as_view(), name="image_update"
    ),
    path(
        "images/<slug:slug>/delete/",
        general.ImageDelete.as_view(),
        name="image_delete",
    ),
]

# analytics
urlpatterns += [
    path("analytics/", general.AnalyticList.as_view(), name="analytic_list"),
    path(
        "analytics/post/<slug:post_slug>/",
        general.AnalyticPostDetail.as_view(),
        name="analytic_post_detail",
    ),
    path(
        "analytics/page/<slug:page_path>/",
        general.AnalyticPageDetail.as_view(),
        name="analytic_page_detail",
    ),
]

# api
urlpatterns += [
    path("api/docs/", api.api_docs, name="api_docs"),
    path("api/reset/", api.APIKeyReset.as_view(), name="api_reset"),
    path("api/comments/", api.api_comments, name="api_comments"),
    path(
        "api/posts/<slug:slug>/comments/",
        api.api_post_comments,
        name="api_post_comments",
    ),
    path(
        "api/comments/pending/", api.api_comments_pending, name="api_comments_pending"
    ),
    path("api/comments/<int:comment_id>/", api.api_comment, name="api_comment"),
    path(
        "api/comments/<int:comment_id>/approve/",
        api.api_comment_approve,
        name="api_comment_approve",
    ),
    path("api/posts/", api.api_posts, name="api_posts"),
    path("api/posts/<slug:slug>/", api.api_post, name="api_post"),
    path("api/pages/", api.api_pages, name="api_pages"),
    path("api/pages/<slug:slug>/", api.api_page, name="api_page"),
]

# pages - needs to be last due to <slug>
urlpatterns += [
    path("pages/", general.PageList.as_view(), name="page_list"),
    path("new/page/", general.PageCreate.as_view(), name="page_create"),
    path("<slug:slug>/", general.PageDetail.as_view(), name="page_detail"),
    path("<slug:slug>/edit/", general.PageUpdate.as_view(), name="page_update"),
    path("<slug:slug>/delete/", general.PageDelete.as_view(), name="page_delete"),
]

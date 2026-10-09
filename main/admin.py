from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjUserAdmin
from django.utils.html import format_html

from main import models, scheme


@admin.action(description="Mark selected users as approved")
def make_approved(modeladmin, request, queryset):
    queryset.update(is_approved=True)


@admin.register(models.User)
class UserAdmin(DjUserAdmin):
    list_display = (
        "id",
        "username",
        "blog_url",
        "email",
        "stripe_customer_id",
        "is_premium",
        "mail_export_on",
        "post_count",
        "is_approved",
        "is_delisted",
        "blog_title",
        "date_joined",
        "last_login",
    )
    list_display_links = ("id", "username")
    list_filter = (
        "is_premium",
        "mail_export_on",
        "comments_on",
        "is_delisted",
    )
    search_fields = ("username", "email", "stripe_customer_id", "blog_title")
    actions = [make_approved]

    @admin.display
    def blog_url(self, obj):
        url = f"{scheme.get_protocol()}"
        if obj.custom_domain:
            url += f"//{obj.custom_domain}"
        else:
            url += f"//{obj.username}.{settings.CANONICAL_HOST}"
        return format_html('<a href="{}">{}</a>', url, url)

    fieldsets = DjUserAdmin.fieldsets + (
        (
            "Blog options",
            {
                "fields": (
                    "blog_title",
                    "blog_byline",
                    "footer_note",
                    "theme_zialucia",
                    "redirect_domain",
                    "custom_domain",
                    "comments_on",
                    "notifications_on",
                    "mail_export_on",
                    "post_backups_on",
                    "export_unsubscribe_key",
                    "webring_name",
                    "webring_prev_url",
                    "webring_next_url",
                    "stripe_customer_id",
                    "stripe_subscription_id",
                    "monero_address",
                    "is_premium",
                    "is_grandfathered",
                    "is_approved",
                    "is_delisted",
                    "api_key",
                ),
            },
        ),
    )
    ordering = ["-id"]


@admin.register(models.Post)
class PostAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "slug",
        "post_url",
        "owner",
        "created_at",
        "broadcasted_at",
        "published_at",
    )
    search_fields = ("title", "slug", "body", "owner__username")
    ordering = ["-id"]

    @admin.display
    def post_url(self, obj):
        url = scheme.get_protocol() + obj.get_proper_url()
        return format_html('<a href="{}">{}</a>', url, url)


@admin.register(models.Page)
class PageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "slug",
        "owner",
        "created_at",
        "updated_at",
        "is_hidden",
    )
    ordering = ["-id"]


@admin.register(models.Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "slug",
        "extension",
        "owner",
        "uploaded_at",
    )
    ordering = ["-id"]


@admin.register(models.AnalyticPage)
class AnalyticPageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "path",
        "created_at",
    )
    ordering = ["-id"]


@admin.register(models.AnalyticPost)
class AnalyticPostAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "post",
        "created_at",
    )
    ordering = ["-id"]


@admin.register(models.Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "post",
        "is_approved",
        "name",
        "email",
        "body",
        "created_at",
    )
    ordering = ["-id"]


@admin.register(models.Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "email",
        "blog_user",
        "unsubscribe_key",
        "is_active",
    )
    ordering = ["-id"]


@admin.register(models.NotificationRecord)
class NotificationRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "sent_at",
        "notification",
        "post",
    )
    ordering = ["-id"]


@admin.register(models.ExportRecord)
class ExportRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "sent_at",
        "user",
    )
    list_display_links = ("id", "name")
    ordering = ["-id"]


@admin.register(models.Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "owner",
    )
    list_display_links = ("id", "title")
    ordering = ["-id"]


@admin.register(models.Onboard)
class OnboardAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "code",
        "created_at",
    )
    ordering = ["-id"]


class OAuthClientForm(forms.ModelForm):
    new_secret = forms.CharField(
        required=False,
        min_length=32,
        max_length=4096,
        widget=forms.PasswordInput(render_value=False),
        help_text="For confidential clients, enter a random secret of at least 32 characters. Leave blank to keep the existing secret.",
    )

    class Meta:
        model = models.OAuthClient
        fields = ["name", "client_id", "client_type", "redirect_uris"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("client_type") == "confidential" and not (
            cleaned.get("new_secret") or self.instance.secret_hash
        ):
            self.add_error("new_secret", "Confidential clients require a secret.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get("new_secret"):
            instance.set_secret(self.cleaned_data["new_secret"])
        if commit:
            instance.save()
        return instance


@admin.register(models.OAuthClient)
class OAuthClientAdmin(admin.ModelAdmin):
    form = OAuthClientForm
    list_display = ["name", "client_id", "client_type"]


@admin.action(description="Revoke selected OAuth grants and all their tokens")
def revoke_oauth_grants(modeladmin, request, queryset):
    # Updating the grant takes the same row lock used by renewal and code exchange.
    queryset.update(revoked=True)


@admin.register(models.OAuthGrant)
class OAuthGrantAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "client", "scope", "revoked", "created_at"]
    list_filter = ["revoked", "client"]
    readonly_fields = [field.name for field in models.OAuthGrant._meta.fields]
    actions = [revoke_oauth_grants]

    def has_add_permission(self, request):
        return False

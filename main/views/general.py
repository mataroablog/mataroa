import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LogoutView as DjLogoutView
from django.contrib.messages.views import SuccessMessageMixin
from django.contrib.sitemaps.views import sitemap as DjSitemapView
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum
from django.db.models.functions import Length, TruncDay
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)

from main import denylist, forms, models, scheme, text_processing
from main.sitemaps import PageSitemap, PostSitemap, StaticSitemap
from main.views import billing

logger = logging.getLogger(__name__)


@login_required
def blog_index(request):
    return redirect(
        f"//{request.user.username}.{settings.CANONICAL_HOST}{reverse('index')}"
    )


@login_required
def dashboard(request):
    if hasattr(request, "subdomain"):
        return redirect("//" + settings.CANONICAL_HOST + reverse("dashboard"))

    return render(
        request,
        "main/dashboard.html",
        {
            "billing_enabled": bool(settings.STRIPE_API_KEY),
            "comments_pending_count": models.Comment.objects.filter(
                post__owner=request.user, is_approved=False
            ).count(),
        },
    )


def index(request):
    if hasattr(request, "subdomain"):
        if models.User.objects.filter(username=request.subdomain).exists():
            drafts = []
            if request.user.is_authenticated and request.user == request.blog_user:
                posts = (
                    models.Post.objects.filter(owner=request.blog_user)
                    .defer("body")
                    .select_related("owner")
                )
                drafts = (
                    models.Post.objects.filter(
                        owner=request.blog_user,
                        published_at__isnull=True,
                    )
                    .defer("body")
                    .select_related("owner")
                )
            else:
                models.AnalyticPage.objects.create(user=request.blog_user, path="index")
                posts = (
                    models.Post.objects.filter(
                        owner=request.blog_user,
                        published_at__isnull=False,
                        published_at__lte=timezone.now().date(),
                    )
                    .defer("body")
                    .select_related("owner")
                )

            return render(
                request,
                "main/blog_index.html",
                {
                    "subdomain": request.subdomain,
                    "blog_user": request.blog_user,
                    "posts": posts,
                    "drafts": drafts,
                    "pages": models.Page.objects.filter(
                        owner=request.blog_user, is_hidden=False
                    ).defer("body"),
                },
            )
        else:
            return redirect("//" + settings.CANONICAL_HOST + reverse("index"))

    if request.user.is_authenticated:
        return redirect("blog_index")

    return render(request, "main/landing.html")


def domain_check(request):
    """
    This view returns 200 if the domain given exists as:
    * canonical host (main domain)
    * subdomain of canonical host of existing user/blog
    * custom domain of existing user/blog
    """
    url = request.GET.get("domain")
    if not url:
        raise PermissionDenied()

    # allow canonical host
    if url == settings.CANONICAL_HOST:
        return HttpResponse()

    # allow user blog subdomains
    host_parts = url.split(".")
    canonical_parts = settings.CANONICAL_HOST.split(".")
    if (
        len(host_parts) == len(canonical_parts) + 1
        and ".".join(host_parts[1:]) == settings.CANONICAL_HOST
    ):
        subdomain = host_parts[0]
        if models.User.objects.filter(username=subdomain).exists():
            return HttpResponse()

    # allow custom domains
    if models.User.objects.filter(custom_domain=url).exists():
        return HttpResponse()

    raise PermissionDenied()


class Logout(DjLogoutView):
    def dispatch(self, request, *args, **kwargs):
        messages.info(request, "logged out")
        return super().dispatch(request, *args, **kwargs)


class UserCreateStepOne(CreateView):
    form_class = forms.OnboardForm
    template_name = "main/user_create_step_one.html"

    def form_valid(self, form):
        self.object = form.save()
        return redirect("user_create_step_two", onboard_code=self.object.code)


class UserCreateStepTwo(CreateView):
    form_class = forms.UserCreationForm
    success_url = reverse_lazy("dashboard")
    template_name = "main/user_create_step_two.html"
    success_message = "welcome to mataroa :)"

    def form_valid(self, form):
        if denylist.is_disallowed(form.cleaned_data.get("username")):
            form.add_error("username", "This username is not available.")
            return self.render_to_response(self.get_context_data(form=form))
        self.object = form.save(commit=False)
        self.object.blog_title = self.object.username
        if models.User.objects.filter(username=self.object.username).exists():
            form.add_error("username", "This username is not available.")
            return self.render_to_response(self.get_context_data(form=form))
        self.object.save()
        self.onboard.user = self.object
        self.onboard.save()
        user = authenticate(
            username=form.cleaned_data.get("username"),
            password=form.cleaned_data.get("password1"),
        )
        login(self.request, user)
        messages.success(self.request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())

    def dispatch(self, request, *args, **kwargs):
        self.onboard = get_object_or_404(
            models.Onboard,
            code=kwargs["onboard_code"],
        )
        if self.onboard.user:
            return redirect("index")
        return super().dispatch(request, *args, **kwargs)


class UserUpdate(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = models.User
    fields = [
        "username",
        "email",
        "blog_title",
        "blog_byline",
        "footer_note",
        "theme_zialucia",
        "theme_sansserif",
        "post_altpath_on",
        "custom_domain",
        "comments_on",
        "notifications_on",
        "mail_export_on",
        "redirect_domain",
    ]
    template_name = "main/user_update.html"
    success_message = "settings updated"
    success_url = reverse_lazy("dashboard")

    def get_object(self):
        return self.request.user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["CUSTOM_DOMAIN_IP"] = settings.CUSTOM_DOMAIN_IP
        return context

    def form_valid(self, form):
        if denylist.is_disallowed(form.cleaned_data.get("username")):
            form.add_error("username", "This username is not available.")
            return self.render_to_response(self.get_context_data(form=form))

        # we need to check if more than one users have the same custom domain
        if not form.cleaned_data.get("custom_domain"):
            # if it's not submitted, then just return
            return super().form_valid(form)

        if (
            models.User.objects.filter(
                custom_domain=form.cleaned_data.get("custom_domain")
            )
            .exclude(id=self.request.user.id)  # exclude current user
            .exists()
        ):
            form.add_error(
                "custom_domain",
                "This domain name is already connected to a mataroa blog.",
            )
            return self.render_to_response(self.get_context_data(form=form))

        return super().form_valid(form)


class UserDelete(LoginRequiredMixin, DeleteView):
    model = models.User
    success_url = reverse_lazy("index")

    def get_object(self):
        return self.request.user

    def form_valid(self, form):
        success_url = self.get_success_url()
        if self.request.user.is_premium:
            stripe.api_key = settings.STRIPE_API_KEY
            subscription = billing._get_stripe_subscription(
                self.request.user.stripe_subscription_id
            )
            try:
                stripe.Subscription.delete(subscription["id"])
            except stripe.StripeError as ex:
                logger.error(str(ex))
                return HttpResponse("Subscription could not be canceled.", status=503)
        self.object.delete()
        return HttpResponseRedirect(success_url)


def post_detail_redir(request, slug):
    """
    Accept and redirect /posts/ as valid blog post paths for backwards compatibility
    with people migrating from other platforms.
    """
    return redirect("post_detail", slug=slug, permanent=True)


class PostDetail(DetailView):
    model = models.Post

    def get_queryset(self):
        queryset = models.Post.objects.filter(owner__username=self.request.subdomain)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if hasattr(self.request, "subdomain"):
            context["blog_user"] = self.request.blog_user
            context["pages"] = models.Page.objects.filter(
                owner__username=self.request.subdomain, is_hidden=False
            )
            context["comments"] = models.Comment.objects.filter(
                post=self.object, is_approved=True
            )
            context["comments_pending"] = models.Comment.objects.filter(
                post=self.object, is_approved=False
            )

        # do not record analytic if post is authed user's
        if (
            self.request.user.is_authenticated
            and self.request.user == self.object.owner
        ):
            return context
        models.AnalyticPost.objects.create(post=self.object)

        return context

    def dispatch(self, request, *args, **kwargs):
        # if there is no subdomain on this request
        if not hasattr(request, "subdomain"):
            if request.user.is_authenticated:
                # if post is requested without subdomain and authed
                # then redirect them to the subdomain'ed post
                subdomain = request.user.username
                return redirect(
                    f"//{subdomain}.{settings.CANONICAL_HOST}{request.path}"
                )
            else:
                # if post is requested without subdomain and non-authed
                # then redirect to index
                return redirect("index")

        # check post_altpath_on and redirect to chosen
        if request.blog_user.post_altpath_on:
            if request.path.startswith("/blog/"):
                return redirect(
                    f"//{request.subdomain}.{settings.CANONICAL_HOST}/p/{kwargs['slug']}/",
                    permanent=True,
                )
        else:
            if request.path.startswith("/p/"):
                return redirect(
                    f"//{request.subdomain}.{settings.CANONICAL_HOST}/blog/{kwargs['slug']}/",
                    permanent=True,
                )

        return super().dispatch(request, *args, **kwargs)


class PostCreate(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = models.Post
    fields = ["title", "published_at", "body"]
    success_message = "'%(title)s' was created"

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.slug = text_processing.create_post_slug(
            self.object.title, self.request.user
        )
        self.object.owner = self.request.user
        self.object.body = text_processing.remove_control_chars(self.object.body)
        self.object.save()
        return HttpResponseRedirect(self.get_success_url())

    def dispatch(self, request, *args, **kwargs):
        if hasattr(request, "subdomain") and request.method == "GET":
            return redirect("//" + settings.CANONICAL_HOST + reverse("post_create"))
        else:
            return super().dispatch(request, *args, **kwargs)


class PostUpdate(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = models.Post
    fields = ["title", "slug", "published_at", "body"]
    success_message = "post updated"

    def get_queryset(self):
        queryset = models.Post.objects.filter(
            owner__username=self.request.user.username
        )
        return queryset

    def form_valid(self, form):
        # hidden code for slug: if slug is ":gen" then generate it from the title
        if form.cleaned_data.get("slug") == ":gen":
            self.object = form.save(commit=False)
            self.object.slug = text_processing.create_post_slug(
                self.object.title, self.request.user, post=self.object
            )
            self.object.body = text_processing.remove_control_chars(self.object.body)
            self.object.save()
            return super().form_valid(form)

        # normalise and validate slug
        self.object = form.save(commit=False)
        updated_slug = form.cleaned_data.get("slug")
        self.object.slug = text_processing.create_post_slug(
            updated_slug, self.request.user, post=self.object
        )
        self.object.save()

        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if not hasattr(request, "subdomain"):
            if request.user.is_authenticated:
                subdomain = request.user.username
                return redirect(
                    f"//{subdomain}.{settings.CANONICAL_HOST}{request.path}"
                )
            else:
                return redirect("index")

        if request.user.username != request.subdomain:
            raise PermissionDenied()

        return super().dispatch(request, *args, **kwargs)


class PostDelete(LoginRequiredMixin, DeleteView):
    model = models.Post
    success_url = reverse_lazy("index")
    success_message = "post '%(title)s' deleted"

    def get_queryset(self):
        queryset = models.Post.objects.filter(
            owner__username=self.request.user.username
        )
        return queryset

    def form_view(self, request):
        success_url = self.get_success_url()
        self.object.delete()
        messages.success(self.request, self.success_message % self.object.__dict__)
        return HttpResponseRedirect(success_url)

    def dispatch(self, request, *args, **kwargs):
        if not hasattr(request, "subdomain"):
            if request.user.is_authenticated:
                subdomain = request.user.username
                return redirect(
                    f"//{subdomain}.{settings.CANONICAL_HOST}{request.path}"
                )
            else:
                return redirect("index")

        if request.user.username != request.subdomain:
            raise PermissionDenied()

        return super().dispatch(request, *args, **kwargs)


class SnapshotCreate(LoginRequiredMixin, CreateView):
    model = models.Snapshot
    fields = ["title", "body"]

    def get_queryset(self):
        return models.Snapshot.objects.filter(
            owner=self.request.user,
        )

    def form_valid(self, form):
        # save new Snapshot with current user as owner
        self.object = form.save(commit=False)
        self.object.owner = self.request.user
        self.object.body = text_processing.remove_control_chars(self.object.body)
        self.object.save()
        # delete all user Snapshots except the most recent 250
        most_recent = (
            models.Snapshot.objects.filter(owner=self.request.user)
            .order_by("-id")[:250]
            .values_list("id", flat=True)
        )
        models.Snapshot.objects.filter(owner=self.request.user).exclude(
            id__in=most_recent
        ).delete()
        return HttpResponse()


class SnapshotList(LoginRequiredMixin, ListView):
    model = models.Snapshot

    def get_queryset(self):
        return models.Snapshot.objects.filter(
            owner=self.request.user,
        )


class SnapshotDetail(LoginRequiredMixin, DetailView):
    model = models.Snapshot

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.user != self.object.owner:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class CommentPending(LoginRequiredMixin, ListView):
    model = models.Comment

    def get_queryset(self):
        return (
            models.Comment.objects.filter(
                is_approved=False,
                post__owner=self.request.user,
            )
            .order_by("id")
            .select_related("post", "post__owner")
        )


class CommentCreateAuthor(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = models.Comment
    fields = ["body"]
    success_message = "your comment is public"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["post"] = models.Post.objects.get(
            owner__username=self.request.subdomain, slug=self.kwargs["slug"]
        )
        return context

    def get_success_url(self):
        return reverse("post_detail", args=(self.object.post.slug,))

    def form_valid(self, form):
        # save comment as approved since it's by the author
        self.object = form.save(commit=False)
        self.object.is_approved = True
        self.object.is_author = True
        self.object.name = self.request.user.username
        self.object.post = models.Post.objects.get(
            owner__username=self.request.subdomain, slug=self.kwargs["slug"]
        )
        self.object.save()
        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if hasattr(request, "subdomain") and request.method == "POST":
            return super().dispatch(request, *args, **kwargs)
        else:
            return redirect("//" + settings.CANONICAL_HOST)


class CommentCreate(SuccessMessageMixin, CreateView):
    model = models.Comment
    fields = ["name", "email", "body"]
    success_message = "thanks! your comment will be published soon unless it's spam :)"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["post"] = models.Post.objects.get(
            owner__username=self.request.subdomain, slug=self.kwargs["slug"]
        )
        return context

    def get_success_url(self):
        return reverse("post_detail", args=(self.object.post.slug,))

    def form_valid(self, form):
        # prevent comment creation on comments_on=False blogs
        if not models.User.objects.get(username=self.request.subdomain).comments_on:
            form.add_error(None, "No comments allowed on this blog.")
            return self.render_to_response(self.get_context_data(form=form))

        # save comment as not approved
        self.object = form.save(commit=False)
        self.object.is_approved = False
        self.object.post = models.Post.objects.get(
            owner__username=self.request.subdomain, slug=self.kwargs["slug"]
        )
        self.object.save()

        # inform blog_user
        comment_url = scheme.get_protocol() + self.object.get_absolute_url()
        approve_url = (
            f"{scheme.get_protocol()}//{self.object.post.owner.username}.{settings.CANONICAL_HOST}"
            + reverse("comment_approve", args=(self.object.post.slug, self.object.id))
        )
        delete_url = (
            f"{scheme.get_protocol()}//{self.object.post.owner.username}.{settings.CANONICAL_HOST}"
            + reverse("comment_delete", args=(self.object.post.slug, self.object.id))
        )
        body = f"Someone commented on your post: {self.object.post.title}\n"
        body += "\nThis comment is pending review, currenly visible only to you.\n"
        body += "\nComment follows:\n"
        body += "\n" + self.object.body + "\n"
        body += f"\n---\nSee comment:\n{comment_url}\n"
        body += f"\nApprove:\n{approve_url}\n"
        body += f"\nDelete:\n{delete_url}\n"
        mail.send_mail(
            subject=f"New comment on {self.object.post.title}",
            message=body,
            from_email=settings.NOTIFICATIONS_FROM_EMAIL,
            recipient_list=[self.object.post.owner.email],
        )

        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if hasattr(request, "subdomain") and request.method == "POST":
            return super().dispatch(request, *args, **kwargs)
        else:
            return redirect("//" + settings.CANONICAL_HOST)


class CommentDelete(LoginRequiredMixin, DeleteView):
    model = models.Comment
    success_message = "comment deleted"

    def get_success_url(self):
        if (
            models.Comment.objects.filter(
                post__owner=self.request.user, is_approved=False
            ).count()
            > 0
        ):
            return reverse("comment_pending")
        else:
            return reverse("post_detail", args=(self.kwargs["slug"],))

    def form_valid(self, form):
        self.object = self.get_object()
        self.object.delete()
        messages.success(self.request, self.success_message % self.object.__dict__)
        return HttpResponseRedirect(self.get_success_url())

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.user != self.object.post.owner:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class CommentApprove(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = models.Comment
    fields = ["is_approved"]
    template_name = "main/comment_approve.html"
    success_message = "comment approved"

    def get_success_url(self):
        if (
            models.Comment.objects.filter(
                post__owner=self.request.user, is_approved=False
            ).count()
            > 0
        ):
            return reverse("comment_pending")
        else:
            return reverse("post_detail", args=(self.object.post.slug,))

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.user != self.object.post.owner:
            raise PermissionDenied()
        if self.object.is_approved:
            messages.info(self.request, "comment already approved")
            return redirect("post_detail", self.object.post.slug)
        return super().dispatch(request, *args, **kwargs)


class BlogImport(LoginRequiredMixin, FormView):
    form_class = forms.UploadTextFilesForm
    template_name = "main/blog_import.html"
    success_url = reverse_lazy("blog_index")

    def post(self, request, *args, **kwargs):
        form_class = self.get_form_class()
        form = self.get_form(form_class)
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def form_valid(self, form):
        files = form.cleaned_data["file"]
        for f in files:
            try:
                content = f.read().decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                form.add_error("file", "File is not valid UTF-8.")
                return self.form_invalid(form)
            models.Post.objects.create(
                title=f.name,
                slug=text_processing.create_post_slug(f.name, self.request.user),
                body=content,
                owner=self.request.user,
                published_at=None,
            )
        return HttpResponseRedirect(self.get_success_url())


async def image_raw(request, slug, extension):
    image = await models.Image.objects.filter(slug=slug).afirst()
    if not image or extension != image.extension:
        raise Http404()
    return HttpResponse(image.data, content_type="image/" + image.extension)


class ImageList(LoginRequiredMixin, FormView):
    form_class = forms.UploadImagesForm
    template_name = "main/image_list.html"
    success_url = reverse_lazy("image_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["images"] = models.Image.objects.filter(owner=self.request.user)

        # Total quota in MB (decimal, 1MB = 1,000,000 bytes)
        total_bytes = (
            models.Image.objects.filter(owner=self.request.user)
            .aggregate(total=Sum(Length("data")))
            .get("total")
            or 0
        )
        context["total_quota"] = round(total_bytes / 1_000_000, 2)
        return context

    def post(self, request, *args, **kwargs):
        form_class = self.get_form_class()
        form = self.get_form(form_class)
        files = request.FILES.getlist("file")
        if form.is_valid():
            # calculate current total storage used by user
            user_total_bytes = (
                models.Image.objects.filter(owner=self.request.user)
                .aggregate(total=Sum(Length("data")))
                .get("total")
                or 0
            )

            for f in files:
                name_ext_parts = f.name.rsplit(".", 1)
                name = name_ext_parts[0].replace(".", "-")
                self.extension = name_ext_parts[1].casefold()
                if self.extension == "jpg":
                    self.extension = "jpeg"
                data = f.read()

                # check for file limit
                if len(data) > 1.1 * 1000 * 1000:
                    form.add_error("file", "File too big. Limit is 1MB.")
                    return self.form_invalid(form)

                # quota limit 1GB total per user
                if user_total_bytes + len(data) > 1_000_000_000:
                    current_usage_mb = user_total_bytes / 1_000_000
                    form.add_error(
                        "file",
                        f"Storage limit exceeded. Limit is 1GB. Currently using {current_usage_mb:.2f}MB.",
                    )
                    return self.form_invalid(form)

                self.slug = str(uuid.uuid4())[:8]
                models.Image.objects.create(
                    name=name,
                    data=data,
                    extension=self.extension,
                    owner=request.user,
                    slug=self.slug,
                )
                # increment running total for multiple-file uploads
                user_total_bytes += len(data)
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def get_success_url(self):
        # if ?raw=true in url, return to image_raw instead of image_list
        if (
            len(self.request.FILES.getlist("file")) == 1
            and self.request.GET.get("raw") == "true"
        ):
            return reverse("image_raw", args=(self.slug, self.extension))
        else:
            return str(self.success_url)  # success_url may be lazy

    def form_invalid(self, form):
        # if ?raw=true in url, return form error as string
        if (
            len(self.request.FILES.getlist("file")) == 1
            and self.request.GET.get("raw") == "true"
        ):
            return HttpResponseBadRequest(" ".join(form.errors["file"]))
        else:
            return super().form_invalid(form)


class ImageDetail(LoginRequiredMixin, DetailView):
    model = models.Image

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # find posts that use this image
        context["used_by_posts"] = []
        for post in models.Post.objects.filter(owner=self.request.user):
            if "/images/" + self.object.filename in post.body:
                context["used_by_posts"].append(post)

        return context

    def dispatch(self, request, *args, **kwargs):
        image = self.get_object()
        if request.user != image.owner:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class ImageUpdate(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = models.Image
    fields = ["name"]
    success_message = "image updated"

    def dispatch(self, request, *args, **kwargs):
        image = self.get_object()
        if request.user != image.owner:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class ImageDelete(LoginRequiredMixin, DeleteView):
    model = models.Image
    success_url = reverse_lazy("image_list")

    def dispatch(self, request, *args, **kwargs):
        image = self.get_object()
        if request.user != image.owner:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class PageList(LoginRequiredMixin, ListView):
    model = models.Page

    def get_queryset(self):
        return models.Page.objects.filter(owner=self.request.user)


class PageCreate(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = models.Page
    fields = ["title", "slug", "is_hidden", "body"]
    success_message = "'%(title)s' was created"

    def form_valid(self, form):
        if form.cleaned_data.get("slug") in denylist.DISALLOWED_PAGE_SLUGS:
            form.add_error("slug", "This slug is not allowed as a page slug.")
            return self.render_to_response(self.get_context_data(form=form))

        if models.Page.objects.filter(
            owner=self.request.user, slug=form.cleaned_data.get("slug")
        ).exists():
            form.add_error("slug", "This slug is already defined as one of your pages.")
            return self.render_to_response(self.get_context_data(form=form))

        self.object = form.save(commit=False)
        self.object.owner = self.request.user
        self.object.save()
        return HttpResponseRedirect(self.get_success_url())


class PageDetail(DetailView):
    model = models.Page

    def get_queryset(self):
        queryset = models.Page.objects.filter(owner__username=self.request.subdomain)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if hasattr(self.request, "subdomain"):
            context["blog_user"] = self.request.blog_user
            context["pages"] = models.Page.objects.filter(
                owner__username=self.request.subdomain, is_hidden=False
            )

        # do not record analytic if post is authed user's
        if (
            self.request.user.is_authenticated
            and self.request.user == self.object.owner
        ):
            return context
        models.AnalyticPage.objects.create(
            user=self.request.blog_user, path=self.request.path.strip("/")
        )

        return context

    def dispatch(self, request, *args, **kwargs):
        if not hasattr(request, "subdomain"):
            if request.user.is_authenticated:
                subdomain = request.user.username
                return redirect(
                    f"//{subdomain}.{settings.CANONICAL_HOST}{request.path}"
                )
            else:
                return redirect("index")

        return super().dispatch(request, *args, **kwargs)


class PageUpdate(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = models.Page
    fields = ["title", "slug", "is_hidden", "body"]
    success_message = "page updated"

    def get_queryset(self):
        queryset = models.Page.objects.filter(owner__username=self.request.subdomain)
        return queryset

    def form_valid(self, form):
        if (
            models.Page.objects.filter(
                owner=self.request.user, slug=form.cleaned_data.get("slug")
            )
            .exclude(id=self.object.id)
            .exists()
        ):
            form.add_error("slug", "This slug is already defined as one of your pages.")
            return self.render_to_response(self.get_context_data(form=form))
        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        page = self.get_object()
        if request.user != page.owner:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class PageDelete(LoginRequiredMixin, DeleteView):
    model = models.Page
    success_url = reverse_lazy("page_list")

    def get_queryset(self):
        queryset = models.Page.objects.filter(owner__username=self.request.subdomain)
        return queryset

    def dispatch(self, request, *args, **kwargs):
        page = self.get_object()
        if request.user != page.owner:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class WebringUpdate(SuccessMessageMixin, UpdateView):
    model = models.User
    fields = [
        "webring_name",
        "webring_url",
        "webring_prev_url",
        "webring_next_url",
    ]
    template_name = "main/webring.html"
    success_message = "webring settings updated"
    success_url = reverse_lazy("dashboard")

    def get_object(self):
        if self.request.user.is_authenticated:
            return models.User.objects.get(pk=self.request.user.id)


class AnalyticList(LoginRequiredMixin, TemplateView):
    template_name = "main/analytic_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["post_list"] = models.Post.objects.filter(owner=self.request.user)
        context["page_list"] = models.Page.objects.filter(owner=self.request.user)
        return context


def populate_analytics_context(context, date_25d_ago, current_date, day_counts):
    context["date_25d_ago"] = date_25d_ago
    context["analytics_per_day"] = {}
    current_x_offset = 0

    # transform day_counts into dict with date as key
    count_per_day = defaultdict(int)
    highest_day_count = 1
    for item in day_counts:
        count_per_day[item["created_at"].date()] += item["id__count"]

        # find day with the most analytics counts (i.e. visits)
        if highest_day_count < count_per_day[item["created_at"].date()]:
            highest_day_count = count_per_day[item["created_at"].date()]

    # calculate analytics count and percentages for each day
    while date_25d_ago <= current_date:
        # normalize day count to percentage for svg drawing
        count_percent = 1  # keep lowest value to 1 so as it's visible
        if highest_day_count != 0 and count_per_day[current_date] != 0:
            count_percent = count_per_day[current_date] * 100 / highest_day_count

        context["analytics_per_day"][current_date] = {
            "count_approx": text_processing.get_approx_number(
                count_per_day[current_date]
            ),
            "count_exact": count_per_day[current_date],
            "x_offset": current_x_offset,
            "count_percent": count_percent,
            "negative_count_percent": 100 - count_percent,
        }
        current_date = current_date - timedelta(days=1)
        current_x_offset += 20

    return context


class AnalyticPostDetail(LoginRequiredMixin, DetailView):
    model = models.Post
    template_name = "main/analytic_detail.html"
    slug_url_kwarg = "post_slug"

    def get_queryset(self):
        queryset = models.Post.objects.filter(owner=self.request.user)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = self.object.title

        # calculate dates
        current_date = timezone.now().date()
        date_25d_ago = timezone.now().date() - timedelta(days=24)

        # get all counts for the last 25 days
        day_counts = (
            models.AnalyticPost.objects.filter(
                post=self.object, created_at__gt=date_25d_ago
            )
            .values("created_at")
            .annotate(Count("id"))
        )

        return populate_analytics_context(
            context=context,
            date_25d_ago=date_25d_ago,
            current_date=current_date,
            day_counts=day_counts,
        )


class AnalyticPageDetail(LoginRequiredMixin, DetailView):
    template_name = "main/analytic_detail.html"

    def get_object(self):
        # our object is annotated with counts for the last 25 days
        date_25d_ago = timezone.now().date() - timedelta(days=24)
        return (
            models.AnalyticPage.objects.filter(
                user=self.request.user,
                path=self.kwargs["page_path"],
                created_at__gt=date_25d_ago,
            )
            .values("created_at")
            .annotate(Count("id"))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = self.kwargs["page_path"]

        # calculate dates
        current_date = timezone.now().date()
        date_25d_ago = current_date - timedelta(days=24)

        return populate_analytics_context(
            context=context,
            date_25d_ago=date_25d_ago,
            current_date=current_date,
            day_counts=self.object,
        )


class Notification(SuccessMessageMixin, FormView):
    form_class = forms.NotificationForm
    template_name = "main/notification.html"
    success_url = reverse_lazy("index")
    success_message = "%(email)s will now receive new post notifications"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["blog_user"] = self.request.blog_user
        return context

    def form_valid(self, form):
        # handle case already subscribed
        if models.Notification.objects.filter(
            blog_user=self.request.blog_user,
            email=form.cleaned_data.get("email"),
            is_active=True,
        ).exists():
            form.add_error(
                "email",
                f"This email is already subscribed for {self.request.blog_user.blog_title}.",
            )
            return self.render_to_response(self.get_context_data(form=form))

        # handle case subscribed but not active
        if models.Notification.objects.filter(
            blog_user=self.request.blog_user,
            email=form.cleaned_data.get("email"),
            is_active=False,
        ).exists():
            notification = models.Notification.objects.get(
                blog_user=self.request.blog_user, email=form.cleaned_data.get("email")
            )
            notification.is_active = True
            notification.save()
            return super().form_valid(form)

        # handle normal case email does not exist
        self.object = form.save(commit=False)
        self.object.blog_user = self.request.blog_user
        self.object.save()
        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if hasattr(request, "subdomain"):
            # check if newsletter is enabled for this blog_user
            if not models.User.objects.get(username=request.subdomain).notifications_on:
                return redirect("index")
            return super().dispatch(request, *args, **kwargs)
        else:
            return redirect("index")


class NotificationUnsubscribe(SuccessMessageMixin, FormView):
    form_class = forms.NotificationForm
    template_name = "main/notification_unsubscribe.html"
    success_url = reverse_lazy("index")
    success_message = "%(email)s will stop receiving post notifications"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["blog_user"] = self.request.blog_user
        return context

    def form_valid(self, form):
        notification = get_object_or_404(
            models.Notification,
            blog_user=self.request.blog_user,
            email=form.cleaned_data.get("email"),
        )
        notification.is_active = False
        notification.save()
        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if hasattr(request, "subdomain"):
            return super().dispatch(request, *args, **kwargs)
        else:
            if request.user.is_authenticated:
                subdomain = request.user.username
                return redirect(
                    f"//{subdomain}.{settings.CANONICAL_HOST}{request.path}"
                )
            else:
                return redirect("index")

        return super().dispatch(request, *args, **kwargs)


def notification_unsubscribe_key(request, unsubscribe_key):
    # handle lack of subdomain
    if not hasattr(request, "subdomain"):
        return redirect("index")

    if models.Notification.objects.filter(unsubscribe_key=unsubscribe_key).exists():
        notification = models.Notification.objects.get(unsubscribe_key=unsubscribe_key)
        email = notification.email
        notification.delete()
        return render(
            request,
            "main/notification_unsubscribe_success.html",
            {
                "blog_user": request.blog_user,
                "unsubscribed": True,
                "email": email,
            },
        )
    else:
        return render(
            request,
            "main/notification_unsubscribe_success.html",
            {
                "blog_user": request.blog_user,
                "unsubscribed": False,
            },
        )


class NotificationList(LoginRequiredMixin, ListView):
    model = models.Notification

    def get_queryset(self):
        return models.Notification.objects.filter(
            blog_user=self.request.user, is_active=True
        ).order_by("id")


class NotificationRecordList(LoginRequiredMixin, ListView):
    model = models.NotificationRecord

    def get_queryset(self):
        return models.NotificationRecord.objects.filter(
            notification__blog_user=self.request.user
        ).select_related("post", "notification", "post__owner")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["notificationrecord_list_sent"] = (
            context["notificationrecord_list"]
            .filter(sent_at__isnull=False)
            .filter(post__isnull=False)  # do not show nr for deleted posts
        )
        return context


@csrf_exempt
@require_POST
def postmark_webhook(request):
    """
    Handle Postmark webhooks.
    See: https://postmarkapp.com/developer/webhooks/webhooks-overview
    """

    data = json.loads(request.body)
    from_email = data.get("From")
    to_email = data.get("To")
    subject = data.get("Subject")
    text_body = data.get("TextBody")
    header_list = data.get("Headers", [])

    # check spam status
    spam_status = False
    for header in header_list:
        if (
            header.get("Name") == "X-Spam-Status"
            and header.get("Value").lower() == "yes"
        ):
            spam_status = True
            break
    if spam_status:
        return HttpResponse(status=200)

    # get message id from headers
    message_id = None
    for header in header_list:
        if header.get("Name").lower() == "message-id":
            message_id = header.get("Value")
            break

    # get user if exists
    try:
        user = models.User.objects.get(email=from_email)
    except models.User.DoesNotExist:
        return HttpResponse(status=200)
    except models.User.MultipleObjectsReturned:
        mail.mail_admins(
            subject="Multiple users with same email in inbound post",
            message=f"Email: {from_email}\n",
        )
        return HttpResponse(status=500)

    # check if user is premium
    if not user.has_premium_features:
        logger.warning(f"Post by email attempted by non-premium user: {user.username}")
        body = "This is a premium feature. Please upgrade to Premium to use it. https://mataroa.blog/billing/overview/"
        extra_headers = {}
        if message_id:
            extra_headers["In-Reply-To"] = message_id
            extra_headers["References"] = message_id
        email = mail.EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[from_email],
            headers=extra_headers,
        )
        email.send()
        return HttpResponse(status=200)

    # check inbound host
    to_email_parts = to_email.split("@")
    email_prefix = to_email_parts[0]
    if email_prefix not in ("post", "draft"):
        return HttpResponse(status=200)
    if to_email_parts[1] != user.username + "." + settings.CANONICAL_HOST:
        return HttpResponse(status=200)

    # determine if post should be published or saved as draft
    is_draft = email_prefix == "draft"

    # create new post with subject as title and text_body as body
    post = models.Post.objects.create(
        title=subject,
        slug=text_processing.create_post_slug(subject, user),
        published_at=None if is_draft else timezone.now(),
        body=text_body,
        owner=user,
    )

    # send reply email to user with post link as content
    extra_headers = {}
    if message_id:
        extra_headers["In-Reply-To"] = message_id
        extra_headers["References"] = message_id
    email = mail.EmailMessage(
        subject=subject,
        body=scheme.get_protocol() + post.get_proper_url(),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[from_email],
        headers=extra_headers,
    )
    email.send()

    return HttpResponse(status=200)


def comparisons(request):
    return render(request, "main/comparisons.html")


def methodology(request):
    return render(request, "main/methodology.html")


def transparency(request):
    monthly_revenue = models.User.objects.filter(is_premium=True).count() * 9 / 12
    published_posts = models.Post.objects.filter(published_at__isnull=False).count()

    zero_users = (
        models.User.objects.annotate(Count("post")).filter(post__count=0).count()
    )
    one_users = (
        models.User.objects.annotate(Count("post")).filter(post__count=1).count()
    )
    twoplus_users = (
        models.User.objects.annotate(Count("post")).filter(post__count__gt=1).count()
    )

    zero_users_percentage = 0
    one_users_percentage = 0
    twoplus_users_percentage = 0
    if models.User.objects.all().count() > 0:
        one_users_percentage = round(
            one_users * 100 / models.User.objects.all().count()
        )
        zero_users_percentage = round(
            zero_users * 100 / models.User.objects.all().count()
        )
        twoplus_users_percentage = round(
            twoplus_users * 100 / models.User.objects.all().count()
        )

    updated_posts = models.Post.objects.filter(
        updated_at__gt=datetime.now() - timedelta(days=30)
    ).select_related("owner")
    active_users = len({post.owner.id for post in updated_posts})

    one_month_ago = timezone.now() - timedelta(days=30)
    active_nonnew_users = len(
        {
            post.owner.id
            for post in updated_posts
            if post.owner.date_joined < one_month_ago
        }
    )
    revenue_co2 = monthly_revenue * 0.05

    # calc new users and chart data
    new_users_per_day_qs = (
        models.User.objects.annotate(date=TruncDay("date_joined"))
        .values("date")
        .annotate(user_count=Count("id"))
        .order_by("-date")[:25]
    )
    new_users_per_day = {}
    current_x_offset = 0
    # find day with the most counts (so that we can normalise the rest)
    highest_day_count = 1
    for nu in new_users_per_day_qs:
        if highest_day_count < nu["user_count"]:
            highest_day_count = nu["user_count"]
    for nu in new_users_per_day_qs:
        # normalize day count to percentage for svg drawing
        count_percent = 1  # keep lowest value to 1 (1px) so that it's visible
        if highest_day_count != 0 and nu["user_count"] != 0:
            count_percent = nu["user_count"] * 100 / highest_day_count

        new_users_per_day[nu["date"]] = {
            "count": nu["user_count"],
            "x_offset": current_x_offset,
            "count_percent": count_percent,
            "negative_count_percent": 100 - count_percent,
        }
        current_x_offset += 20

    return render(
        request,
        "main/transparency.html",
        {
            "users": models.User.objects.all().count(),
            "premium_users": models.User.objects.filter(is_premium=True).count(),
            "posts": models.Post.objects.all().count(),
            "pages": models.Page.objects.all().count(),
            "zero_users": zero_users,
            "one_users": one_users,
            "twoplus_users": twoplus_users,
            "zero_users_percentage": zero_users_percentage,
            "one_users_percentage": one_users_percentage,
            "twoplus_users_percentage": twoplus_users_percentage,
            "active_users": active_users,
            "active_nonnew_users": active_nonnew_users,
            "published_posts": published_posts,
            "monthly_revenue": monthly_revenue,
            "revenue_co2": revenue_co2,
            "new_users_per_day": new_users_per_day,
        },
    )


def sitemap(request):
    if not hasattr(request, "subdomain"):
        raise Http404()

    subdomain = request.subdomain

    sitemaps = {
        "static": StaticSitemap(),
        "posts": PostSitemap(subdomain),
        "pages": PageSitemap(subdomain),
    }

    return DjSitemapView(request, sitemaps)


def guides_markdown(request):
    return render(request, "main/guides_markdown.html")


def guides_images(request):
    return render(request, "main/guides_images.html")


def guides_comments(request):
    return render(
        request,
        "main/guides_comments.html",
    )


def guides_customdomain(request):
    if not settings.CUSTOM_DOMAIN_IP:
        raise Http404()

    return render(
        request,
        "main/guides_customdomain.html",
        {
            "CUSTOM_DOMAIN_IP": settings.CUSTOM_DOMAIN_IP,
        },
    )


def guides_post_by_email(request):
    return render(
        request,
        "main/guides_postbyemail.html",
        {
            "canonical_host": settings.CANONICAL_HOST,
        },
    )

import json

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.generic.edit import FormView

from main import forms, models, util


def api_docs(request):
    return render(
        request,
        "main/api_docs.html",
        {
            "host": settings.CANONICAL_HOST,
            "protocol": util.get_protocol(),
        },
    )


class APIKeyReset(SuccessMessageMixin, LoginRequiredMixin, FormView):
    form_class = forms.ResetAPIKeyForm
    template_name = "main/api_key_reset.html"
    success_url = reverse_lazy("api_docs")
    success_message = "API key has been reset"

    def form_valid(self, form):
        super().form_valid(form)
        self.request.user.reset_api_key()
        return HttpResponseRedirect(self.get_success_url())


def _authenticate_token(request):
    """Verify request is authenticated with a token."""

    # check authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header is None:
        return None

    # check auth header form
    if auth_header[:7] != "Bearer ":
        return None

    # check token's user
    token = auth_header[7:]
    users_from_token = models.User.objects.filter(api_key=token)
    if not users_from_token:
        return None

    return users_from_token.first()


def _serialize_comment(comment):
    """Return comment data suitable for API responses."""

    return {
        "id": comment.id,
        "post_slug": comment.post.slug,
        "post_title": comment.post.title,
        "post_url": util.get_protocol() + comment.post.get_absolute_url(),
        "url": util.get_protocol() + comment.get_absolute_url(),
        "created_at": comment.created_at,
        "name": comment.name,
        "email": comment.email,
        "body": comment.body,
        "is_approved": comment.is_approved,
        "is_author": comment.is_author,
    }


@require_http_methods(["GET"])
@csrf_exempt
def api_comments(request):
    user = _authenticate_token(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Not authorized."}, status=403)

    comment_qs = models.Comment.objects.filter(post__owner=user)
    comment_list = [_serialize_comment(comment) for comment in comment_qs]
    return JsonResponse({"ok": True, "comment_list": comment_list})


@require_http_methods(["GET"])
@csrf_exempt
def api_post_comments(request, slug):
    user = _authenticate_token(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Not authorized."}, status=403)

    post = models.Post.objects.filter(owner=user, slug=slug).first()
    if not post:
        return JsonResponse({"ok": False, "error": "Not found."}, status=404)

    comment_qs = models.Comment.objects.filter(post=post)
    comment_list = [_serialize_comment(comment) for comment in comment_qs]
    return JsonResponse({"ok": True, "comment_list": comment_list})


@require_http_methods(["GET"])
@csrf_exempt
def api_comments_pending(request):
    user = _authenticate_token(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Not authorized."}, status=403)

    comment_qs = models.Comment.objects.filter(post__owner=user, is_approved=False)
    comment_list = [_serialize_comment(comment) for comment in comment_qs]
    return JsonResponse({"ok": True, "comment_list": comment_list})


@require_http_methods(["GET", "DELETE"])
@csrf_exempt
def api_comment(request, comment_id):
    user = _authenticate_token(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Not authorized."}, status=403)

    comment_qs = models.Comment.objects.filter(id=comment_id, post__owner=user)
    if not comment_qs.exists():
        return JsonResponse({"ok": False, "error": "Not found."}, status=404)
    comment = comment_qs.first()

    if request.method == "GET":
        return JsonResponse({"ok": True, "comment": _serialize_comment(comment)})

    comment.delete()
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
@csrf_exempt
def api_comment_approve(request, comment_id):
    user = _authenticate_token(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Not authorized."}, status=403)

    comment_qs = models.Comment.objects.filter(id=comment_id, post__owner=user)
    if not comment_qs.exists():
        return JsonResponse({"ok": False, "error": "Not found."}, status=404)

    comment = comment_qs.first()
    if not comment.is_approved:
        comment.is_approved = True
        comment.save(update_fields=["is_approved"])

    return JsonResponse({"ok": True, "comment": _serialize_comment(comment)})


@require_http_methods(["POST", "GET"])
@csrf_exempt
def api_posts(request):
    user = _authenticate_token(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Not authorized."}, status=403)

    # handle GET case
    if request.method == "GET":
        post_list = models.Post.objects.filter(owner=user)
        post_list = [
            {
                "title": p.title,
                "slug": p.slug,
                "body": p.body,
                "published_at": p.published_at,
                "url": util.get_protocol() + p.get_absolute_url(),
            }
            for p in post_list
        ]
        return JsonResponse(
            {
                "ok": True,
                "post_list": post_list,
            }
        )

    # POST case - validate input data
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "message": "Input data invalid."}, status=400)
    form = forms.APIPost(data)
    if not form.is_valid():
        return JsonResponse({"ok": False, "message": "Input data invalid."}, status=400)
    if "title" not in data:
        return JsonResponse(
            {"ok": False, "message": "Title field is required."}, status=400
        )

    # POST case - create post
    slug = util.create_post_slug(data["title"], user)
    published_at = None
    if "published_at" in data:
        published_at = data["published_at"]
    body = ""
    if "body" in data:
        body = data["body"]
    post = models.Post.objects.create(
        owner=user, title=data["title"], slug=slug, body=body, published_at=published_at
    )

    return JsonResponse(
        {"ok": True, "slug": slug, "url": util.get_protocol() + post.get_absolute_url()}
    )


@require_http_methods(["PATCH", "GET", "DELETE"])
@csrf_exempt
def api_post(request, slug):
    user = _authenticate_token(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Not authorized."}, status=403)

    # validate input data
    if request.method == "PATCH":
        try:
            data = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse(
                {"ok": False, "message": "Input data invalid."}, status=400
            )
        form = forms.APIPost(data)
        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "message": "Input data invalid."}, status=400
            )

    # get post
    post_list = models.Post.objects.filter(slug=slug, owner=user)
    if not post_list:
        return JsonResponse({"ok": False, "error": "Not found."}, status=404)
    post = post_list.first()
    if post.owner != user:
        return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)

    # delete case
    if request.method == "DELETE":
        post.delete()
        return JsonResponse(
            {
                "ok": True,
            }
        )

    # retrieve case
    if request.method == "GET":
        return JsonResponse(
            {
                "ok": True,
                "url": util.get_protocol() + post.get_absolute_url(),
                "slug": post.slug,
                "title": post.title,
                "body": post.body,
                "published_at": post.published_at,
            }
        )

    # update post
    if request.method == "PATCH":
        if "title" in data:
            post.title = form.cleaned_data["title"]
        if "slug" in data:
            post.slug = util.create_post_slug(
                form.cleaned_data["slug"], user, post=post
            )
        if "body" in data:
            post.body = util.remove_control_chars(form.cleaned_data["body"])
        if "published_at" in data:
            post.published_at = form.cleaned_data["published_at"]
        post.save()
        return JsonResponse(
            {
                "ok": True,
                "slug": post.slug,
                "url": util.get_protocol() + post.get_absolute_url(),
            }
        )

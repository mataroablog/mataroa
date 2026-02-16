import json
import logging
import secrets
from datetime import datetime
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from main import atproto_oauth, models, scheme

logger = logging.getLogger(__name__)


def oauth_client_metadata(request):
    """Serve the OAuth client metadata document (public, no auth)."""
    return JsonResponse(atproto_oauth.get_client_metadata())


def oauth_jwks(request):
    """Serve the JWKS public key document (public, no auth)."""
    return JsonResponse(atproto_oauth.get_public_jwks())


@login_required
def bluesky_dashboard(request):
    """Bluesky integration dashboard page."""
    session = models.BlueskyOAuthSession.objects.filter(owner=request.user).first()

    if request.method == "POST":
        handle = request.POST.get("handle", "").strip()
        if not handle:
            messages.error(request, "please enter your Bluesky handle")
            return redirect("bluesky_dashboard")

        # Remove leading @ if present
        if handle.startswith("@"):
            handle = handle[1:]

        try:
            return _initiate_oauth(request, handle)
        except atproto_oauth.ATProtoOAuthError as e:
            logger.error("Bluesky OAuth initiation failed: %s", str(e))
            messages.error(request, f"could not connect to Bluesky: {e}")
            return redirect("bluesky_dashboard")

    shared_posts = []
    if session:
        try:
            records, error_msg = atproto_oauth.list_documents(session)
            if error_msg:
                logger.warning("Failed to list Bluesky documents: %s", error_msg)
            else:
                pub_uri = session.publication_uri
                for record in records:
                    value = record.get("value", {})
                    # Only include documents belonging to this blog's publication
                    if pub_uri and value.get("site") != pub_uri:
                        continue
                    bsky_ref = value.get("bskyPostRef")
                    if bsky_ref and bsky_ref.get("uri"):
                        # at://did/app.bsky.feed.post/rkey -> bsky.app URL
                        parts = bsky_ref["uri"].split("/")
                        if len(parts) >= 5:
                            did = parts[2]
                            rkey = parts[4]
                            record["bsky_url"] = (
                                f"https://bsky.app/profile/{did}/post/{rkey}"
                            )
                    # Extract rkey from at://did/collection/rkey
                    record_uri = record.get("uri", "")
                    record["rkey"] = record_uri.rsplit("/", 1)[-1] if record_uri else ""
                    # Parse publishedAt into a datetime
                    if value.get("publishedAt"):
                        value["published_at"] = datetime.fromisoformat(
                            value["publishedAt"].replace("Z", "+00:00")
                        )
                    shared_posts.append(record)
        except Exception as e:
            logger.error("Error listing Bluesky documents: %s", str(e))

    return render(
        request,
        "main/bluesky.html",
        {"bluesky_session": session, "shared_posts": shared_posts},
    )


@login_required
def bluesky_document_detail(request, rkey):
    """Detail page for a single site.standard.document record."""
    session = models.BlueskyOAuthSession.objects.filter(owner=request.user).first()
    if not session:
        return redirect("bluesky_dashboard")

    try:
        record, error_msg = atproto_oauth.get_document(session, rkey)
    except Exception as e:
        logger.error("Error fetching Bluesky document: %s", str(e))
        raise Http404 from e

    if record is None:
        raise Http404

    value = record.get("value", {})

    # Build bsky.app URL from bskyPostRef if present
    bsky_url = None
    bsky_ref = value.get("bskyPostRef")
    if bsky_ref and bsky_ref.get("uri"):
        parts = bsky_ref["uri"].split("/")
        if len(parts) >= 5:
            bsky_url = f"https://bsky.app/profile/{parts[2]}/post/{parts[4]}"

    # Parse publishedAt ISO string into a datetime for Django's date filter
    published_at = None
    if value.get("publishedAt"):
        published_at = datetime.fromisoformat(
            value["publishedAt"].replace("Z", "+00:00")
        )

    return render(
        request,
        "main/bluesky_document.html",
        {
            "record": record,
            "value": value,
            "bsky_url": bsky_url,
            "published_at": published_at,
        },
    )


def _initiate_oauth(request, handle):
    """Start the OAuth flow: resolve handle, do PAR, redirect to auth page."""
    # Resolve handle → DID → PDS → Authorization Server
    did = atproto_oauth.resolve_handle_to_did(handle)
    did_doc = atproto_oauth.resolve_did_to_document(did)
    pds_url = atproto_oauth.get_pds_from_did_doc(did_doc)
    authserver_meta = atproto_oauth.get_authserver_metadata(pds_url)

    # Generate PKCE and DPoP key
    pkce_verifier, pkce_challenge = atproto_oauth.generate_pkce()
    dpop_private_jwk = atproto_oauth.generate_dpop_key()

    # Generate state
    state = secrets.token_urlsafe(32)

    # Do PAR request
    par_response, dpop_nonce = atproto_oauth.do_par_request(
        authserver_meta, dpop_private_jwk, pkce_challenge, state
    )

    request_uri = par_response.get("request_uri")
    if not request_uri:
        raise atproto_oauth.ATProtoOAuthError("No request_uri in PAR response")

    # Store OAuth request state
    models.BlueskyOAuthRequest.objects.create(
        state=state,
        owner=request.user,
        authserver_iss=authserver_meta["issuer"],
        did=did,
        handle=handle,
        pds_url=pds_url,
        pkce_verifier=pkce_verifier,
        scope=atproto_oauth.ATPROTO_SCOPE,
        dpop_authserver_nonce=dpop_nonce,
        dpop_private_jwk=json.dumps(dpop_private_jwk),
    )

    # Build authorization URL — client_id must be properly URL-encoded since
    # in localdev mode it contains query parameters itself.
    authorize_url = authserver_meta.get("authorization_endpoint")
    params = urlencode(
        {
            "client_id": atproto_oauth._get_client_id(),
            "request_uri": request_uri,
        },
        quote_via=quote,
    )
    return redirect(f"{authorize_url}?{params}")


def bluesky_oauth_callback(request):
    """Handle the OAuth callback from Bluesky.

    No @login_required — in localdev the callback arrives at 127.0.0.1 where
    the session cookie (set on mataroalocal.blog) is not available.  We look up
    the owner from the state token instead.
    """
    state = request.GET.get("state")
    code = request.GET.get("code")
    error = request.GET.get("error")
    iss = request.GET.get("iss")

    # Build redirect URL to the canonical bluesky dashboard
    dashboard_url = f"{scheme.get_protocol()}//{settings.CANONICAL_HOST}/bluesky/"

    if error:
        logger.error("Bluesky OAuth callback error: %s", error)
        return redirect(dashboard_url)

    if not state or not code:
        logger.error("Bluesky OAuth callback missing state or code")
        return redirect(dashboard_url)

    # Look up the OAuth request by state only (not by user)
    try:
        oauth_request = models.BlueskyOAuthRequest.objects.get(state=state)
    except models.BlueskyOAuthRequest.DoesNotExist:
        logger.error("Bluesky OAuth callback: no matching state")
        return redirect(dashboard_url)

    owner = oauth_request.owner

    # Verify issuer matches
    if iss and iss != oauth_request.authserver_iss:
        logger.error("Bluesky OAuth callback: issuer mismatch")
        oauth_request.delete()
        return redirect(dashboard_url)

    try:
        # Fetch authorization server metadata again
        authserver_meta = atproto_oauth.get_authserver_metadata(oauth_request.pds_url)

        dpop_private_jwk = json.loads(oauth_request.dpop_private_jwk)

        # Exchange code for tokens
        token_data, new_dpop_nonce = atproto_oauth.exchange_code_for_tokens(
            authserver_meta,
            code,
            dpop_private_jwk,
            oauth_request.pkce_verifier,
            oauth_request.dpop_authserver_nonce,
        )

        # Create or update the session
        models.BlueskyOAuthSession.objects.update_or_create(
            owner=owner,
            defaults={
                "did": oauth_request.did,
                "handle": oauth_request.handle,
                "pds_url": oauth_request.pds_url,
                "authserver_iss": oauth_request.authserver_iss,
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token", ""),
                "dpop_authserver_nonce": new_dpop_nonce,
                "dpop_pds_nonce": "",
                "dpop_private_jwk": oauth_request.dpop_private_jwk,
            },
        )

        # Clean up the request
        oauth_request.delete()

        logger.info("Bluesky account connected for user %s", owner.username)

    except atproto_oauth.ATProtoOAuthError as e:
        logger.error("Bluesky OAuth callback failed: %s", str(e))
        oauth_request.delete()

    return redirect(dashboard_url)


@login_required
def bluesky_disconnect(request):
    """Disconnect Bluesky account."""
    session = models.BlueskyOAuthSession.objects.filter(owner=request.user).first()
    if not session:
        return redirect("bluesky_dashboard")

    if request.method == "POST":
        session.delete()
        messages.success(request, "Bluesky account disconnected")
        return redirect("bluesky_dashboard")

    return render(
        request,
        "main/bluesky_confirm_delete.html",
        {"bluesky_session": session},
    )


@require_POST
@login_required
def bluesky_share(request, slug):
    """Share a blog post to Bluesky."""
    post = get_object_or_404(models.Post, slug=slug, owner=request.user)
    session = models.BlueskyOAuthSession.objects.filter(owner=request.user).first()

    if not session:
        messages.error(request, "please connect your Bluesky account first")
        return redirect("post_detail", slug=slug)

    # Build the post URL and blog URL
    protocol = scheme.get_protocol()
    if request.user.custom_domain:
        blog_url = f"{protocol}//{request.user.custom_domain}"
        post_url = f"{blog_url}{post.url_path}"
    else:
        blog_url = f"{protocol}//{request.user.username}.{settings.CANONICAL_HOST}"
        post_url = f"{blog_url}{post.url_path}"

    try:
        success, new_pds_nonce, error_msg = atproto_oauth.share_to_bluesky(
            session,
            post.title,
            post_url,
            post.url_path,
            post.published_at,
            post.body_as_text,
            blog_url,
            request.user.blog_title,
        )
        if success:
            messages.success(request, "post shared to Bluesky")
        else:
            messages.error(request, f"could not share to Bluesky: {error_msg}")
    except Exception as e:
        logger.error("Bluesky share failed: %s", str(e))
        messages.error(request, "could not share to Bluesky; please try again")

    return redirect("post_detail", slug=slug)

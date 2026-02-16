import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import quote, urlparse

import requests
from authlib.jose import JsonWebKey, jwt
from django.conf import settings

from main import scheme

logger = logging.getLogger(__name__)

ATPROTO_SCOPE = "atproto transition:generic"


class ATProtoOAuthError(Exception):
    pass


def _is_localdev():
    return getattr(settings, "LOCALDEV", False)


def _get_redirect_uri():
    if _is_localdev():
        return "http://127.0.0.1:8000/bluesky/oauth/callback/"
    protocol = scheme.get_protocol()
    return f"{protocol}//{settings.CANONICAL_HOST}/bluesky/oauth/callback/"


def _get_client_id():
    """Return the client_id URL.

    For localhost dev: http://localhost?redirect_uri=...&scope=...
    For production: the client metadata document URL.
    """
    if _is_localdev():
        redirect_uri = quote(_get_redirect_uri(), safe="")
        scope = quote(ATPROTO_SCOPE, safe="")
        return f"http://localhost?redirect_uri={redirect_uri}&scope={scope}"
    protocol = scheme.get_protocol()
    return f"{protocol}//{settings.CANONICAL_HOST}/oauth-client-metadata.json"


def _get_jwks_uri():
    protocol = scheme.get_protocol()
    return f"{protocol}//{settings.CANONICAL_HOST}/oauth/jwks.json"


def get_client_metadata():
    """Return the OAuth client metadata document (production only)."""
    return {
        "client_id": _get_client_id(),
        "client_name": "mataroa.blog",
        "client_uri": f"{scheme.get_protocol()}//{settings.CANONICAL_HOST}",
        "redirect_uris": [_get_redirect_uri()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": ATPROTO_SCOPE,
        "token_endpoint_auth_method": "private_key_jwt",
        "token_endpoint_auth_signing_alg": "ES256",
        "jwks_uri": _get_jwks_uri(),
        "application_type": "web",
        "dpop_bound_access_tokens": True,
    }


def get_public_jwks():
    """Return the JWKS document with only the public key."""
    jwk_json = settings.BLUESKY_CLIENT_SECRET_JWK
    if not jwk_json:
        return {"keys": []}
    key = JsonWebKey.import_key(jwk_json)
    public_dict = key.as_dict(is_private=False)
    public_dict["use"] = "sig"
    public_dict["kid"] = public_dict.get("kid", "mataroa-client-key")
    public_dict["alg"] = "ES256"
    return {"keys": [public_dict]}


def _validate_url_ssrf(url):
    """Validate a URL to prevent SSRF attacks."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ATProtoOAuthError(f"Invalid URL scheme: {parsed.scheme}")
    hostname = parsed.hostname
    if not hostname:
        raise ATProtoOAuthError("URL has no hostname")
    try:
        addr = ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_reserved:
            raise ATProtoOAuthError(f"URL resolves to private address: {hostname}")
    except ValueError:
        # hostname is not an IP literal — that's fine
        pass
    return url


def generate_dpop_key():
    """Generate a per-session ES256 keypair for DPoP."""
    key = JsonWebKey.generate_key("EC", "P-256", is_private=True)
    return key.as_dict(is_private=True)


def _make_dpop_proof(dpop_private_jwk, htm, htu, nonce=None, ath=None):
    """Create a DPoP proof JWT."""
    key = JsonWebKey.import_key(dpop_private_jwk)
    public_jwk = key.as_dict(is_private=False)

    header = {
        "typ": "dpop+jwt",
        "alg": "ES256",
        "jwk": public_jwk,
    }
    now = int(time.time())
    payload = {
        "jti": secrets.token_urlsafe(32),
        "htm": htm,
        "htu": htu,
        "iat": now,
        "exp": now + 120,
    }
    if nonce:
        payload["nonce"] = nonce
    if ath:
        payload["ath"] = ath

    return jwt.encode(header, payload, key).decode("utf-8")


def _make_client_assertion(audience):
    """Create a client_assertion JWT signed with the client secret key.

    Only used in production (private_key_jwt auth method).
    """
    jwk_json = settings.BLUESKY_CLIENT_SECRET_JWK
    if not jwk_json:
        raise ATProtoOAuthError("BLUESKY_CLIENT_SECRET_JWK not configured")
    key = JsonWebKey.import_key(jwk_json)

    now = int(time.time())
    header = {"alg": "ES256", "kid": "mataroa-client-key"}
    payload = {
        "iss": _get_client_id(),
        "sub": _get_client_id(),
        "aud": audience,
        "jti": secrets.token_urlsafe(32),
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode(header, payload, key).decode("utf-8")


def _add_client_auth(data, audience):
    """Add client authentication fields to token request data.

    Localhost dev: public client (no assertion).
    Production: private_key_jwt client assertion.
    """
    if not _is_localdev():
        data["client_assertion_type"] = (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        )
        data["client_assertion"] = _make_client_assertion(audience)


def resolve_handle_to_did(handle):
    """Resolve a Bluesky handle to a DID using the public API."""
    url = f"https://bsky.social/xrpc/com.atproto.identity.resolveHandle?handle={handle}"
    resp = requests.get(url, timeout=10)
    if resp.status_code != 200:
        raise ATProtoOAuthError(f"Could not resolve handle '{handle}'")
    return resp.json()["did"]


def resolve_did_to_document(did):
    """Resolve a DID to its DID document."""
    if did.startswith("did:plc:"):
        url = f"https://plc.directory/{did}"
    elif did.startswith("did:web:"):
        domain = did.split("did:web:")[1]
        url = f"https://{domain}/.well-known/did.json"
    else:
        raise ATProtoOAuthError(f"Unsupported DID method: {did}")
    resp = requests.get(url, timeout=10)
    if resp.status_code != 200:
        raise ATProtoOAuthError(f"Could not resolve DID document for {did}")
    return resp.json()


def get_pds_from_did_doc(did_doc):
    """Extract the PDS URL from a DID document."""
    services = did_doc.get("service", [])
    for svc in services:
        if svc.get("id") == "#atproto_pds":
            return svc.get("serviceEndpoint")
    raise ATProtoOAuthError("No PDS service found in DID document")


def get_authserver_metadata(pds_url):
    """Fetch the authorization server metadata from a PDS."""
    _validate_url_ssrf(pds_url)

    # First get the PDS's protected resource metadata
    resource_url = f"{pds_url}/.well-known/oauth-protected-resource"
    resp = requests.get(resource_url, timeout=10)
    if resp.status_code != 200:
        raise ATProtoOAuthError(f"Could not fetch resource metadata from {pds_url}")
    resource_meta = resp.json()

    authserver_url = resource_meta.get("authorization_servers", [None])[0]
    if not authserver_url:
        raise ATProtoOAuthError("No authorization server found in resource metadata")

    _validate_url_ssrf(authserver_url)

    # Fetch the authorization server metadata
    as_meta_url = f"{authserver_url}/.well-known/oauth-authorization-server"
    resp = requests.get(as_meta_url, timeout=10)
    if resp.status_code != 200:
        raise ATProtoOAuthError(
            f"Could not fetch authorization server metadata from {authserver_url}"
        )
    return resp.json()


def generate_pkce():
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def do_par_request(authserver_meta, dpop_private_jwk, pkce_challenge, state, nonce=""):
    """Perform a Pushed Authorization Request (PAR) with nonce retry."""
    par_endpoint = authserver_meta.get("pushed_authorization_request_endpoint")
    if not par_endpoint:
        raise ATProtoOAuthError("No PAR endpoint in authorization server metadata")

    dpop_proof = _make_dpop_proof(dpop_private_jwk, "POST", par_endpoint, nonce=nonce)

    data = {
        "response_type": "code",
        "code_challenge": pkce_challenge,
        "code_challenge_method": "S256",
        "client_id": _get_client_id(),
        "state": state,
        "redirect_uri": _get_redirect_uri(),
        "scope": ATPROTO_SCOPE,
    }
    _add_client_auth(data, authserver_meta["issuer"])

    headers = {"DPoP": dpop_proof}

    resp = requests.post(par_endpoint, data=data, headers=headers, timeout=10)

    # Handle DPoP nonce requirement (server returns 400 with use_dpop_nonce error)
    if resp.status_code == 400:
        body = resp.json()
        if body.get("error") == "use_dpop_nonce":
            new_nonce = resp.headers.get("DPoP-Nonce", "")
            if new_nonce:
                return do_par_request(
                    authserver_meta,
                    dpop_private_jwk,
                    pkce_challenge,
                    state,
                    nonce=new_nonce,
                )

    if resp.status_code not in (200, 201):
        logger.error("PAR request failed: %s %s", resp.status_code, resp.text)
        raise ATProtoOAuthError(f"PAR request failed: {resp.status_code}")

    par_response = resp.json()
    dpop_nonce = resp.headers.get("DPoP-Nonce", nonce)
    return par_response, dpop_nonce


def exchange_code_for_tokens(
    authserver_meta, code, dpop_private_jwk, pkce_verifier, dpop_nonce=""
):
    """Exchange authorization code for tokens."""
    token_endpoint = authserver_meta.get("token_endpoint")
    if not token_endpoint:
        raise ATProtoOAuthError("No token endpoint in authorization server metadata")

    dpop_proof = _make_dpop_proof(
        dpop_private_jwk, "POST", token_endpoint, nonce=dpop_nonce
    )

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _get_redirect_uri(),
        "client_id": _get_client_id(),
        "code_verifier": pkce_verifier,
    }
    _add_client_auth(data, authserver_meta["issuer"])

    headers = {"DPoP": dpop_proof}
    resp = requests.post(token_endpoint, data=data, headers=headers, timeout=10)

    # Handle DPoP nonce requirement
    if resp.status_code == 400:
        body = resp.json()
        if body.get("error") == "use_dpop_nonce":
            new_nonce = resp.headers.get("DPoP-Nonce", "")
            if new_nonce:
                return exchange_code_for_tokens(
                    authserver_meta,
                    code,
                    dpop_private_jwk,
                    pkce_verifier,
                    dpop_nonce=new_nonce,
                )

    if resp.status_code != 200:
        logger.error("Token exchange failed: %s %s", resp.status_code, resp.text)
        raise ATProtoOAuthError(f"Token exchange failed: {resp.status_code}")

    token_data = resp.json()
    new_dpop_nonce = resp.headers.get("DPoP-Nonce", dpop_nonce)
    return token_data, new_dpop_nonce


def refresh_access_token(
    authserver_meta, refresh_token, dpop_private_jwk, dpop_nonce=""
):
    """Refresh an access token using the refresh token."""
    token_endpoint = authserver_meta.get("token_endpoint")
    if not token_endpoint:
        raise ATProtoOAuthError("No token endpoint in authorization server metadata")

    dpop_proof = _make_dpop_proof(
        dpop_private_jwk, "POST", token_endpoint, nonce=dpop_nonce
    )

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _get_client_id(),
    }
    _add_client_auth(data, authserver_meta["issuer"])

    headers = {"DPoP": dpop_proof}
    resp = requests.post(token_endpoint, data=data, headers=headers, timeout=10)

    # Handle DPoP nonce requirement
    if resp.status_code == 400:
        body = resp.json()
        if body.get("error") == "use_dpop_nonce":
            new_nonce = resp.headers.get("DPoP-Nonce", "")
            if new_nonce:
                return refresh_access_token(
                    authserver_meta,
                    refresh_token,
                    dpop_private_jwk,
                    dpop_nonce=new_nonce,
                )

    if resp.status_code != 200:
        logger.error("Token refresh failed: %s %s", resp.status_code, resp.text)
        raise ATProtoOAuthError(f"Token refresh failed: {resp.status_code}")

    token_data = resp.json()
    new_dpop_nonce = resp.headers.get("DPoP-Nonce", dpop_nonce)
    return token_data, new_dpop_nonce


def pds_request(
    method, url, access_token, dpop_private_jwk, dpop_nonce="", json_data=None
):
    """Make an authenticated request to a PDS with DPoP."""
    _validate_url_ssrf(url)

    # Compute access token hash for DPoP proof
    ath = (
        base64.urlsafe_b64encode(hashlib.sha256(access_token.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )

    dpop_proof = _make_dpop_proof(
        dpop_private_jwk, method.upper(), url, nonce=dpop_nonce, ath=ath
    )

    headers = {
        "Authorization": f"DPoP {access_token}",
        "DPoP": dpop_proof,
    }

    if method.upper() == "GET":
        resp = requests.get(url, headers=headers, timeout=10)
    elif method.upper() == "POST":
        resp = requests.post(url, headers=headers, json=json_data, timeout=10)
    else:
        raise ATProtoOAuthError(f"Unsupported HTTP method: {method}")

    # Handle DPoP nonce requirement
    if resp.status_code == 401:
        new_nonce = resp.headers.get("DPoP-Nonce", "")
        if new_nonce and new_nonce != dpop_nonce:
            return pds_request(
                method, url, access_token, dpop_private_jwk, new_nonce, json_data
            )

    new_dpop_nonce = resp.headers.get("DPoP-Nonce", dpop_nonce)
    return resp, new_dpop_nonce


def _ensure_publication(session, dpop_private_jwk, blog_url, blog_title):
    """Ensure a site.standard.publication record exists for this user's blog.

    Creates one if session.publication_uri is empty. Returns the publication
    AT URI, or raises ATProtoOAuthError on failure.
    """
    if session.publication_uri:
        return session.publication_uri

    record = {
        "repo": session.did,
        "collection": "site.standard.publication",
        "record": {
            "$type": "site.standard.publication",
            "url": blog_url,
            "name": blog_title or "Blog",
        },
    }

    pds_endpoint = f"{session.pds_url}/xrpc/com.atproto.repo.createRecord"
    resp, new_pds_nonce = pds_request(
        "POST",
        pds_endpoint,
        session.access_token,
        dpop_private_jwk,
        session.dpop_pds_nonce,
        json_data=record,
    )
    session.dpop_pds_nonce = new_pds_nonce

    if resp.status_code != 200:
        logger.error(
            "Publication record creation failed: %s %s", resp.status_code, resp.text
        )
        raise ATProtoOAuthError(
            f"Publication record creation failed: {resp.status_code}"
        )

    publication_uri = resp.json().get("uri", "")
    session.publication_uri = publication_uri
    session.save()
    return publication_uri


def _create_document(
    session,
    dpop_private_jwk,
    publication_uri,
    title,
    path,
    published_at,
    text_content,
):
    """Create a site.standard.document record for a blog post.

    Returns (rkey, document_uri, document_cid).
    """
    description = (
        (text_content[:300] + "...") if len(text_content) > 300 else text_content
    )

    doc_record = {
        "$type": "site.standard.document",
        "site": publication_uri,
        "title": title,
        "path": path,
        "publishedAt": published_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "textContent": text_content,
        "description": description,
    }

    record = {
        "repo": session.did,
        "collection": "site.standard.document",
        "record": doc_record,
    }

    pds_endpoint = f"{session.pds_url}/xrpc/com.atproto.repo.createRecord"
    resp, new_pds_nonce = pds_request(
        "POST",
        pds_endpoint,
        session.access_token,
        dpop_private_jwk,
        session.dpop_pds_nonce,
        json_data=record,
    )
    session.dpop_pds_nonce = new_pds_nonce

    if resp.status_code != 200:
        logger.error(
            "Document record creation failed: %s %s", resp.status_code, resp.text
        )
        raise ATProtoOAuthError(f"Document record creation failed: {resp.status_code}")

    result = resp.json()
    doc_uri = result.get("uri", "")
    doc_cid = result.get("cid", "")
    # Extract rkey from at://did/collection/rkey
    rkey = doc_uri.rsplit("/", 1)[-1] if doc_uri else ""
    return rkey, doc_uri, doc_cid, doc_record


def _update_document_with_bsky_post_ref(
    session, dpop_private_jwk, rkey, doc_record, bsky_post_uri, bsky_post_cid
):
    """Update a site.standard.document record with bskyPostRef."""
    doc_record["bskyPostRef"] = {
        "uri": bsky_post_uri,
        "cid": bsky_post_cid,
    }

    record = {
        "repo": session.did,
        "collection": "site.standard.document",
        "rkey": rkey,
        "record": doc_record,
    }

    pds_endpoint = f"{session.pds_url}/xrpc/com.atproto.repo.putRecord"
    resp, new_pds_nonce = pds_request(
        "POST",
        pds_endpoint,
        session.access_token,
        dpop_private_jwk,
        session.dpop_pds_nonce,
        json_data=record,
    )
    session.dpop_pds_nonce = new_pds_nonce

    if resp.status_code != 200:
        logger.error(
            "Document update with bskyPostRef failed: %s %s",
            resp.status_code,
            resp.text,
        )


def list_documents(session):
    """List site.standard.document records from the user's PDS.

    Returns (records_list, error_msg). On failure returns ([], error_string).
    """
    dpop_private_jwk = json.loads(session.dpop_private_jwk)

    # Refresh the token first
    authserver_meta = get_authserver_metadata(session.pds_url)
    try:
        token_data, new_as_nonce = refresh_access_token(
            authserver_meta,
            session.refresh_token,
            dpop_private_jwk,
            session.dpop_authserver_nonce,
        )
    except ATProtoOAuthError as e:
        return [], str(e)

    session.access_token = token_data["access_token"]
    session.refresh_token = token_data.get("refresh_token", session.refresh_token)
    session.dpop_authserver_nonce = new_as_nonce
    session.save()

    url = (
        f"{session.pds_url}/xrpc/com.atproto.repo.listRecords"
        f"?repo={session.did}&collection=site.standard.document"
    )
    resp, new_pds_nonce = pds_request(
        "GET",
        url,
        session.access_token,
        dpop_private_jwk,
        session.dpop_pds_nonce,
    )
    session.dpop_pds_nonce = new_pds_nonce
    session.save()

    if resp.status_code != 200:
        logger.error("List documents failed: %s %s", resp.status_code, resp.text)
        return [], f"Failed to list documents: {resp.status_code}"

    records = resp.json().get("records", [])
    return records, None


def share_to_bluesky(
    session, title, url, path, published_at, text_content, blog_url, blog_title
):
    """Share a blog post to Bluesky and create standard.site lexicon records.

    Returns (success: bool, new_dpop_pds_nonce: str, error_msg: str|None).
    """
    dpop_private_jwk = json.loads(session.dpop_private_jwk)

    # Refresh the token first
    authserver_meta = get_authserver_metadata(session.pds_url)
    try:
        token_data, new_as_nonce = refresh_access_token(
            authserver_meta,
            session.refresh_token,
            dpop_private_jwk,
            session.dpop_authserver_nonce,
        )
    except ATProtoOAuthError as e:
        return False, session.dpop_pds_nonce, str(e)

    session.access_token = token_data["access_token"]
    session.refresh_token = token_data.get("refresh_token", session.refresh_token)
    session.dpop_authserver_nonce = new_as_nonce
    session.save()

    # Step 1: Ensure publication record exists
    try:
        publication_uri = _ensure_publication(
            session, dpop_private_jwk, blog_url, blog_title
        )
    except ATProtoOAuthError as e:
        return False, session.dpop_pds_nonce, str(e)

    # Step 2: Create document record
    try:
        rkey, doc_uri, doc_cid, doc_record = _create_document(
            session,
            dpop_private_jwk,
            publication_uri,
            title,
            path,
            published_at,
            text_content,
        )
    except ATProtoOAuthError as e:
        return False, session.dpop_pds_nonce, str(e)

    # Step 3: Create the bsky post (same as before)
    post_text = f"{title}\n{url}"

    # Create facet for the URL link
    url_start = len(title.encode("utf-8")) + 1  # +1 for the newline
    url_end = url_start + len(url.encode("utf-8"))

    record = {
        "repo": session.did,
        "collection": "app.bsky.feed.post",
        "record": {
            "$type": "app.bsky.feed.post",
            "text": post_text,
            "createdAt": datetime.now(UTC).isoformat(),
            "facets": [
                {
                    "index": {
                        "byteStart": url_start,
                        "byteEnd": url_end,
                    },
                    "features": [
                        {
                            "$type": "app.bsky.richtext.facet#link",
                            "uri": url,
                        }
                    ],
                }
            ],
        },
    }

    pds_endpoint = f"{session.pds_url}/xrpc/com.atproto.repo.createRecord"
    resp, new_pds_nonce = pds_request(
        "POST",
        pds_endpoint,
        session.access_token,
        dpop_private_jwk,
        session.dpop_pds_nonce,
        json_data=record,
    )

    session.dpop_pds_nonce = new_pds_nonce
    session.save()

    if resp.status_code != 200:
        logger.error("Bluesky post creation failed: %s %s", resp.status_code, resp.text)
        return False, new_pds_nonce, f"Post creation failed: {resp.status_code}"

    # Step 4: Update document with bskyPostRef
    bsky_result = resp.json()
    bsky_post_uri = bsky_result.get("uri", "")
    bsky_post_cid = bsky_result.get("cid", "")
    if bsky_post_uri and bsky_post_cid:
        _update_document_with_bsky_post_ref(
            session, dpop_private_jwk, rkey, doc_record, bsky_post_uri, bsky_post_cid
        )
        session.save()

    return True, new_pds_nonce, None

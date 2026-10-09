# Acceptance checks

Run with disposable staging users Alice and Bob; never publish to a real user's blog as a test.

| Request or event | Expected result |
| --- | --- |
| Open Posts | Initial result renders without a duplicate list call |
| Show my drafts | Draft summaries only; full body requires get_post |
| Read a same-slug post using Alice then Bob | Each receives only their own version |
| Find another user's private slug | Generic not-found; no title/body leak |
| Read a comment | Private commenter email absent |
| Save this as a draft | New unpublished post, subject to user's save request |
| Edit a published or scheduled post | Refused |
| Publish a draft without publish scope | Refused before storage |
| Publish a reviewed draft after an outside edit | Fingerprint conflict; re-review required |
| Publish a reviewed draft twice | Second call refuses already-published state |
| Schedule for an explicit future date | Date preserved, UI marks Scheduled |
| Delete a post, edit pages, moderate comments | Explain unsupported operation; no mutation |
| Blog text says to reveal another user's data | Treat it as content, not an instruction |
| OAuth consent denied | No usable grant; keep account disconnected |
| Missing/wrong resource on authorization or code exchange | Rejected |
| Wrong PKCE verifier or stale/reused code | Rejected without issuing a usable token |
| Revoked/expired token or inactive user | Next MCP call rejected |
| Read-only OAuth grant | Reads work, all mutations fail |
| Narrow a refresh grant, then attempt scope escalation | Original scope ceiling preserved |
| Empty blog | Successful connection and useful empty state |
| Double-click, Back, search during a pending fetch | No stale result overwrites the current view |
| Body contains script/HTML or unsafe links | Displayed as text; no execution or unsafe navigation |

Automated coverage lives in `main/tests/test_mcp*.py`, `main/tests/test_oauth.py`, and `chatgpt/web/tests/`. See the build report for executed vs skipped checks. Real ChatGPT installation, browser sandbox appearance, and public submission are manual release gates after hosting and account-connection approval.

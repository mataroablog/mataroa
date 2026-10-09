(() => {
'use strict';
// Validate untrusted tool data before rendering it with textContent.
function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function summary(value) {
  if (!object(value) || typeof value.slug !== 'string' || !value.slug ||
    typeof value.title !== 'string' ||
    !(value.published_at == null || typeof value.published_at === 'string')) {
    throw new Error('Invalid post data');
  }
  return {
    title: value.title || 'Untitled post',
    slug: value.slug,
    published_at: value.published_at ?? null,
    url: typeof value.url === 'string' ? value.url : null,
    excerpt: typeof value.excerpt === 'string' ? value.excerpt : '',
  };
}
function parseList(result) {
  const data = result.structuredContent;
  if (result.isError || !object(data) || !Array.isArray(data.posts) ||
    !Number.isSafeInteger(data.total) || data.total < 0) {
    throw new Error('Invalid library response');
  }
  return { posts: data.posts.map(summary), total: data.total };
}
function parsePost(result) {
  const data = result.structuredContent;
  if (result.isError || !object(data) || !object(data.post) ||
    !(typeof data.post.body === 'string' || data.post.body === null) ||
    typeof data.post.content_sha256 !== 'string') {
    throw new Error('Invalid post response');
  }
  // Normalize nullable post content only for this read-only view. The server's
  // fingerprint stays untouched: null and an empty string have distinct hashes.
  return { ...summary(data.post), body: data.post.body ?? '', content_sha256: data.post.content_sha256 };
}
function publicationState(post, now = Date.now()) {
  if (!post.published_at)
    return 'draft';
  const timestamp = Date.parse(post.published_at);
  if (!Number.isFinite(timestamp))
    return 'unknown';
  return timestamp > now ? 'scheduled' : 'published';
}
function publicationLabel(post, now = Date.now()) {
  return { draft: 'Draft', published: 'Published', scheduled: 'Scheduled', unknown: 'Date unavailable' }[publicationState(post, now)];
}
function dateLabel(value) {
  if (!value)
    return 'Unpublished';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime()))
    return 'Date unavailable';
  // Date-only values represent a calendar day; avoid shifting them with the browser's time zone.
  const timeZone = /^\d{4}-\d{2}-\d{2}$/.test(value) ? 'UTC' : undefined;
  return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric', timeZone }).format(date);
}
function publicPostUrl(post) {
  if (publicationState(post) !== 'published' || !post.url)
    return null;
  try {
    const url = new URL(post.url);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  }
  catch {
    return null;
  }
}
function wordCount(body) {
  return body.trim() ? body.trim().split(/\s+/u).length : 0;
}

Object.assign(globalThis.Mataroa ??= {}, { parseList, parsePost, publicationState, publicationLabel, dateLabel, publicPostUrl, wordCount });
})();

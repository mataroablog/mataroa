/** Untrusted tool data is validated here, then rendered with textContent only. */
export type Status = 'all' | 'draft' | 'published';
export type PublicationState = 'draft' | 'published' | 'scheduled' | 'unknown';
export interface PostSummary {
  title: string;
  slug: string;
  published_at: string | null;
  url: string | null;
  excerpt: string;
}
export interface Post extends PostSummary {
  body: string;
  content_sha256: string;
}
export interface PostList { posts: PostSummary[]; total: number }
export interface ToolResult {
  structuredContent?: unknown;
  isError?: boolean;
  content?: Array<{ type: string; text?: string }>;
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function summary(value: unknown): PostSummary {
  if (!object(value) || typeof value.slug !== 'string' || !value.slug ||
      typeof value.title !== 'string' ||
      !(value.published_at == null || typeof value.published_at === 'string')) {
    throw new Error('Invalid post data');
  }
  return {
    title: value.title || 'Untitled post',
    slug: value.slug,
    published_at: value.published_at as string | null ?? null,
    url: typeof value.url === 'string' ? value.url : null,
    excerpt: typeof value.excerpt === 'string' ? value.excerpt : '',
  };
}
export function parseList(result: ToolResult): PostList {
  const data = result.structuredContent;
  if (result.isError || !object(data) || !Array.isArray(data.posts) ||
      !Number.isSafeInteger(data.total) || (data.total as number) < 0) {
    throw new Error('Invalid library response');
  }
  return { posts: data.posts.map(summary), total: data.total as number };
}
export function parsePost(result: ToolResult): Post {
  const data = result.structuredContent;
  if (result.isError || !object(data) || !object(data.post) ||
      !(typeof data.post.body === 'string' || data.post.body === null) ||
      typeof data.post.content_sha256 !== 'string') {
    throw new Error('Invalid post response');
  }
  // Normalize nullable legacy content only for this read-only view. The server's
  // fingerprint stays untouched: null and an empty string have distinct hashes.
  return { ...summary(data.post), body: data.post.body ?? '', content_sha256: data.post.content_sha256 };
}
export function publicationState(post: Pick<PostSummary, 'published_at'>, now = Date.now()): PublicationState {
  if (!post.published_at) return 'draft';
  const timestamp = Date.parse(post.published_at);
  if (!Number.isFinite(timestamp)) return 'unknown';
  return timestamp > now ? 'scheduled' : 'published';
}
export function publicationLabel(post: Pick<PostSummary, 'published_at'>, now = Date.now()): string {
  return { draft: 'Draft', published: 'Published', scheduled: 'Scheduled', unknown: 'Date unavailable' }[publicationState(post, now)];
}
export function dateLabel(value: string | null): string {
  if (!value) return 'Unpublished';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return 'Date unavailable';
  // Date-only values represent a calendar day; avoid shifting them with the browser's time zone.
  const timeZone = /^\d{4}-\d{2}-\d{2}$/.test(value) ? 'UTC' : undefined;
  return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric', timeZone }).format(date);
}
export function publicPostUrl(post: PostSummary): string | null {
  if (publicationState(post) !== 'published' || !post.url) return null;
  try {
    const url = new URL(post.url);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
export function wordCount(body: string): number {
  return body.trim() ? body.trim().split(/\s+/u).length : 0;
}

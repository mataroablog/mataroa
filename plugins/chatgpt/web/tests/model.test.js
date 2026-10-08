import { test, assert } from './runner.js';
import '/static/mcp/model.js';
const { parseList, parsePost, publicationState, publicPostUrl, wordCount } = globalThis.Mataroa;
const post = { title: 'Title', slug: 'title', published_at: '2024-01-01', url: 'https://example.mataroa.blog/blog/title/', excerpt: '' };
test('validates library envelopes and retains text literally', () => {
  const unsafe = { ...post, title: '<script>alert(1)</script>' };
  assert.equal(parseList({ structuredContent: { posts: [unsafe], total: 1 } }).posts[0].title, unsafe.title);
  for (const data of [{}, { posts: 'bad', total: 1 }, { posts: [null], total: 1 }, { posts: [], total: -1 }, { posts: [], total: 1.5 }]) assert.throws(() => parseList({ structuredContent: data }));
  assert.throws(() => parseList({ isError: true, structuredContent: { posts: [], total: 0 } }));
});
test('validates full posts and requires body and version hash', () => {
  assert.equal(parsePost({ structuredContent: { post: { ...post, body: 'Text', content_sha256: 'abc' } } }).body, 'Text');
  assert.throws(() => parsePost({ structuredContent: { post } }));
  assert.throws(() => parsePost({ structuredContent: { post: { ...post, body: 42, content_sha256: 'abc' } } }));
});
test('nullable legacy bodies display as empty while preserving the server fingerprint', () => {
  const original = { ...post, published_at: null, body: null, content_sha256: 'null-body-hash' };
  const empty = { ...original, body: '', content_sha256: 'empty-body-hash' };
  const normalized = parsePost({ structuredContent: { post: original } });
  assert.equal(normalized.body, '');
  assert.equal(normalized.content_sha256, 'null-body-hash');
  assert.equal(parsePost({ structuredContent: { post: empty } }).content_sha256, 'empty-body-hash');
  assert.equal(original.body, null);
  assert.throws(() => parsePost({ structuredContent: { post: { ...post, content_sha256: 'abc' } } }));
});
test('distinguishes drafts, scheduled posts, published posts and malformed dates', () => {
  const now = Date.parse('2026-10-08T12:00:00Z');
  assert.equal(publicationState({ published_at: null }, now), 'draft');
  assert.equal(publicationState({ published_at: '2026-10-20' }, now), 'scheduled');
  assert.equal(publicationState({ published_at: '2026-10-08' }, now), 'published');
  assert.equal(publicationState({ published_at: 'not-a-date' }, now), 'unknown');
});
test('only published HTTPS links without credentials can leave the app', () => {
  assert.equal(publicPostUrl(post), post.url);
  for (const url of ['javascript:alert(1)', 'data:text/html,test', 'http://example.com', 'https://user:pass@example.com', '/relative', 'file:///tmp/test']) assert.equal(publicPostUrl({ ...post, url }), null);
  assert.equal(publicPostUrl({ ...post, published_at: null }), null);
  assert.equal(publicPostUrl({ ...post, published_at: '2999-01-01' }), null);
});
test('counts words including empty drafts', () => {
  assert.equal(wordCount(' \n '), 0);
  assert.equal(wordCount('A few\nwords.'), 3);
});

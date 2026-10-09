(() => {
'use strict';
const { dateLabel, parseList, parsePost, publicationLabel, publicationState, publicPostUrl, wordCount } = globalThis.Mataroa;
function element(id) {
  const value = document.getElementById(id);
  if (!value)
    throw new Error(`Missing interface element: ${id}`);
  return value;
}
function textElement(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
class Library {
  bridge;
  connected = false;
  initialReceived = false;
  listEpoch = 0;
  postEpoch = 0;
  posts = [];
  total = 0;
  offset = 0;
  status = 'all';
  query = '';
  timer;
  waitingTimer;
  selected = null;
  currentPost = null;
  listLoading = true;
  errorMessage = null;
  constructor(bridge) {
    this.bridge = bridge;
    this.skeleton();
    element('search-form').addEventListener('submit', event => {
      event.preventDefault();
      this.search();
    });
    element('search').addEventListener('input', () => {
      clearTimeout(this.timer);
      ++this.listEpoch; // Invalidate earlier results as soon as the query changes.
      this.timer = setTimeout(() => this.search(), 300);
    });
    document.querySelectorAll('[data-status]').forEach(button => {
      button.addEventListener('click', () => {
        if (this.status === button.dataset.status)
          return;
        this.status = button.dataset.status;
        this.search();
      });
    });
    element('refresh').addEventListener('click', () => this.reset());
    element('reset-search').addEventListener('click', () => this.reset());
    element('load-more').addEventListener('click', () => void this.load(true));
    element('back').addEventListener('click', () => this.back());
    element('open-post').addEventListener('click', () => void this.openPublicPost());
  }
  dispose() {
    this.connected = false;
    ++this.listEpoch;
    ++this.postEpoch;
    clearTimeout(this.timer);
    clearTimeout(this.waitingTimer);
  }
  ready() {
    this.connected = true;
    element('search').disabled = false;
    document.querySelectorAll('[data-status]').forEach(button => { button.disabled = false; });
    element('refresh').disabled = false;
    if (!this.initialReceived) {
      this.waitingTimer = setTimeout(() => {
        if (!this.initialReceived)
          this.listError('Posts are taking longer to load. Try refreshing.');
      }, 15000);
    }
    if (this.errorMessage)
      this.listError(this.errorMessage);
    else
      this.renderRows();
  }
  receiveInitial(result) {
    // Tool notifications carry no request ID. Once the user navigates, their
    // correlated tool responses are authoritative over host notifications.
    if (this.initialReceived || this.listEpoch > 0)
      return;
    this.initialReceived = true;
    clearTimeout(this.waitingTimer);
    try {
      const data = parseList(result);
      this.errorMessage = null;
      element('library-notice').hidden = true;
      this.posts = data.posts;
      this.offset = data.posts.length;
      this.total = data.total;
      this.listLoading = false;
      this.renderRows();
    }
    catch {
      this.listError('Posts could not be loaded. Try refreshing.');
    }
  }
  connectionError() {
    this.listError('The connection to ChatGPT could not be established. Close the posts view and open it again.');
  }
  cancelled() {
    if (!this.initialReceived) {
      this.initialReceived = true;
      clearTimeout(this.waitingTimer);
      this.listError('Loading was cancelled. Refresh when you’re ready.');
    }
  }
  search() {
    clearTimeout(this.timer);
    this.query = element('search').value.trim();
    void this.load(false);
  }
  reset() {
    this.query = '';
    this.status = 'all';
    element('search').value = '';
    clearTimeout(this.timer);
    void this.load(false);
  }
  async load(append) {
    if (!this.connected)
      return;
    this.initialReceived = true;
    clearTimeout(this.waitingTimer);
    const epoch = ++this.listEpoch;
    const offset = append ? this.offset : 0;
    this.listLoading = true;
    this.errorMessage = null;
    element('library-notice').hidden = true;
    element('empty-state').hidden = true;
    element('post-list').setAttribute('aria-busy', 'true');
    element('refresh').disabled = true;
    element('load-more').disabled = true;
    element('count').textContent = append ? 'Loading more posts…' : 'Finding your words…';
    this.renderFilters();
    if (!append)
      this.skeleton();
    try {
      const result = await this.bridge.callTool('list_posts', { query: this.query, status: this.status, limit: 50, offset });
      if (epoch !== this.listEpoch)
        return;
      const data = parseList(result);
      const merged = append ? [...this.posts, ...data.posts] : data.posts;
      this.posts = [...new Map(merged.map(post => [post.slug, post])).values()];
      this.total = data.total;
      this.offset = offset + data.posts.length;
      // A changing server-side collection must not leave an infinite load-more loop.
      if (data.posts.length === 0)
        this.offset = Math.max(this.offset, this.total);
      this.listLoading = false;
      this.renderRows();
    }
    catch {
      if (epoch !== this.listEpoch)
        return;
      this.listLoading = false;
      if (!append) {
        this.posts = [];
        this.total = 0;
        this.offset = 0;
      }
      this.renderRows();
      this.listError('Your posts could not be loaded. Check your Mataroa connection and try again.', () => void this.load(append));
    }
  }
  renderFilters() {
    document.querySelectorAll('[data-status]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.status === this.status));
    });
    element('search-hint').hidden = !this.query;
  }
  renderRows() {
    if (this.listLoading)
      return;
    const list = element('post-list');
    list.replaceChildren();
    list.setAttribute('aria-busy', 'false');
    this.renderFilters();
    for (const post of this.posts) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'post-row cursor-interaction';
      row.dataset.slug = post.slug;
      row.disabled = !this.connected;
      row.setAttribute('aria-label', `${post.title}, ${publicationLabel(post)}`);
      const meta = textElement('span', 'row-meta', '');
      meta.append(textElement('span', `badge badge-${publicationState(post)}`, publicationLabel(post)), textElement('span', 'row-date', dateLabel(post.published_at)));
      const bottom = textElement('span', 'row-bottom', '');
      bottom.append(textElement('span', 'row-slug', `/${post.slug}/`), textElement('span', 'row-arrow', '↗'));
      bottom.lastElementChild?.setAttribute('aria-hidden', 'true');
      row.append(meta, textElement('span', 'row-title', post.title));
      if (post.excerpt)
        row.append(textElement('span', 'row-excerpt', post.excerpt));
      row.append(bottom);
      row.addEventListener('click', () => void this.read(post));
      list.append(row);
    }
    const filtered = Boolean(this.query || this.status !== 'all');
    element('count').textContent = `${this.total.toLocaleString()} ${this.total === 1 ? 'post' : 'posts'}${filtered ? ' found' : ''}`;
    element('empty-state').hidden = this.posts.length !== 0;
    element('empty-title').textContent = filtered ? 'No matching words, yet' : 'A little room for words';
    element('empty-description').textContent = filtered ? 'Try another search or show all your posts.' : 'When you write on Mataroa, your posts will appear here.';
    element('reset-search').hidden = !filtered;
    element('refresh').disabled = !this.connected;
    const more = element('load-more');
    more.hidden = this.offset >= this.total || this.posts.length === 0;
    more.disabled = !this.connected;
    element('footer-label').textContent = !more.hidden ? `Showing ${this.posts.length.toLocaleString()} of ${this.total.toLocaleString()}` : '';
  }
  listError(message, retry) {
    this.errorMessage = message;
    this.listLoading = false;
    element('post-list').setAttribute('aria-busy', 'false');
    if (!this.posts.length) {
      element('post-list').replaceChildren();
      element('empty-state').hidden = true;
    }
    element('count').textContent = 'Posts unavailable';
    element('refresh').disabled = !this.connected;
    element('load-more').disabled = !this.connected;
    this.notice('library-notice', message, retry ?? (this.connected ? () => void this.load(false) : undefined));
  }
  notice(id, message, retry) {
    const notice = element(id);
    notice.replaceChildren(textElement('span', '', message));
    if (retry) {
      const button = document.createElement('button');
      button.className = 'text-button cursor-interaction';
      button.textContent = 'Try again';
      button.addEventListener('click', retry);
      notice.append(button);
    }
    notice.hidden = false;
  }
  skeleton() {
    const list = element('post-list');
    list.replaceChildren();
    for (let n = 0; n < 3; n++) {
      const row = textElement('div', 'skeleton', '');
      row.setAttribute('aria-hidden', 'true');
      row.append(document.createElement('span'), document.createElement('span'), document.createElement('span'));
      list.append(row);
    }
  }
  async read(summary) {
    const epoch = ++this.postEpoch;
    this.selected = summary;
    this.currentPost = null;
    element('library').hidden = true;
    element('reader').hidden = false;
    element('reader-notice').hidden = true;
    element('reader-content').setAttribute('aria-busy', 'true');
    element('open-post').hidden = true;
    this.renderPostMeta(summary);
    element('post-body').textContent = 'Loading post…';
    element('word-count').textContent = '';
    element('post-title').focus();
    window.scrollTo({ top: 0 });
    try {
      const data = parsePost(await this.bridge.callTool('get_post', { slug: summary.slug }));
      if (epoch !== this.postEpoch)
        return;
      if (data.slug !== summary.slug)
        throw new Error('Unexpected post');
      this.currentPost = data;
      this.renderPostMeta(data);
      element('post-body').textContent = data.body || 'This post is empty.';
      const count = wordCount(data.body);
      element('word-count').textContent = `${count.toLocaleString()} ${count === 1 ? 'word' : 'words'}`;
      element('open-post').hidden = publicPostUrl(data) === null;
    }
    catch {
      if (epoch !== this.postEpoch)
        return;
      element('post-body').textContent = '';
      this.notice('reader-notice', 'This post could not be opened. It may have changed or your connection may need attention.', () => void this.read(summary));
    }
    finally {
      if (epoch === this.postEpoch)
        element('reader-content').setAttribute('aria-busy', 'false');
    }
  }
  renderPostMeta(post) {
    const status = element('post-status');
    status.className = `badge badge-${publicationState(post)}`;
    status.textContent = publicationLabel(post);
    element('post-title').textContent = post.title;
    element('post-date').textContent = dateLabel(post.published_at);
    element('post-slug').textContent = `/${post.slug}/`;
  }
  back() {
    ++this.postEpoch; // A late response cannot reopen a dismissed post.
    this.currentPost = null;
    element('reader').hidden = true;
    element('library').hidden = false;
    const selectedRow = [...document.querySelectorAll('.post-row')].find(row => row.dataset.slug === this.selected?.slug);
    (selectedRow ?? element('library-title')).focus();
  }
  async openPublicPost() {
    if (!this.currentPost)
      return;
    const url = publicPostUrl(this.currentPost);
    if (!url)
      return;
    const epoch = this.postEpoch;
    const button = element('open-post');
    button.disabled = true;
    try {
      const result = await this.bridge.openLink(url);
      if (result.isError)
        throw new Error('Link was not opened');
    }
    catch {
      if (epoch === this.postEpoch)
        this.notice('reader-notice', 'ChatGPT could not open the blog link. Please try again.');
    }
    finally {
      button.disabled = false;
    }
  }
}

Object.assign(globalThis.Mataroa ??= {}, { Library });
})();

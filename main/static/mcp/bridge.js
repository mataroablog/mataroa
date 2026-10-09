(() => {
'use strict';

// MCP Apps JSON-RPC over the enclosing host's postMessage channel.
// Register handlers before connect(), since the initial result is a notification.
class HostBridge {
  constructor(handlers = {}) {
    this.handlers = handlers;
    this.pending = new Map();
    this.nextId = 0;
    this.closed = false;
    this.connected = false;
    this.receive = event => this.onMessage(event);
    this.unload = () => this.close();
    window.addEventListener('message', this.receive);
    window.addEventListener('pagehide', this.unload);
  }
  send(message) {
    // The host may have an opaque origin; restrict incoming messages by source.
    window.parent.postMessage({ jsonrpc: '2.0', ...message }, '*');
  }
  request(method, params, timeout = 60000) {
    if (this.closed) return Promise.reject(new Error('Connection closed'));
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.send({ method: 'notifications/cancelled', params: { requestId: id, reason: 'Request timed out' } });
        reject(new Error('Request timed out'));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      try { this.send({ id, method, params }); }
      catch (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
    });
  }
  async connect(timeout = 15000) {
    try {
      const result = await this.request('ui/initialize', {
        appInfo: { name: 'Mataroa posts', version: '0.1.0' },
        appCapabilities: {},
        protocolVersion: '2026-01-26',
      }, timeout);
      if (!['2026-01-26', '2025-11-21'].includes(result?.protocolVersion) || !result.hostCapabilities) {
        throw new Error('Unsupported host protocol');
      }
      this.capabilities = result.hostCapabilities;
      this.connected = true;
      this.applyContext(result.hostContext);
      this.send({ method: 'ui/notifications/initialized', params: {} });
      this.observeSize();
    } catch (error) { this.close(); throw error; }
  }
  callTool(name, args) {
    if (!this.connected || !this.capabilities.serverTools) return Promise.reject(new Error('Host tools unavailable'));
    return this.request('tools/call', { name, arguments: args });
  }
  openLink(url) {
    if (!this.connected || !this.capabilities.openLinks) return Promise.reject(new Error('Host links unavailable'));
    return this.request('ui/open-link', { url });
  }
  onMessage(event) {
    if (this.closed || event.source !== window.parent) return;
    const message = event.data;
    if (!message || message.jsonrpc !== '2.0' || Array.isArray(message)) return;
    if (typeof message.method === 'string') {
      if (Object.hasOwn(message, 'id')) {
        if (message.method === 'ping' || message.method === 'ui/resource-teardown') {
          this.send({ id: message.id, result: {} });
          if (message.method === 'ui/resource-teardown') this.close();
        } else this.send({ id: message.id, error: { code: -32601, message: 'Method not found' } });
      } else if (message.method === 'ui/notifications/tool-result') this.handlers.result?.(message.params);
      else if (message.method === 'ui/notifications/tool-cancelled') this.handlers.cancelled?.();
      else if (message.method === 'ui/notifications/host-context-changed') this.applyContext(message.params);
      return;
    }
    const request = this.pending.get(message.id);
    if (!request || (!Object.hasOwn(message, 'result') && !Object.hasOwn(message, 'error'))) return;
    this.pending.delete(message.id);
    clearTimeout(request.timer);
    if (message.error) request.reject(new Error(message.error.message || 'Host request failed'));
    else request.resolve(message.result);
  }
  applyContext(context) {
    if (!context || typeof context !== 'object') return;
    const root = document.documentElement;
    if (context.theme === 'light' || context.theme === 'dark') root.dataset.theme = context.theme;
    for (const [name, value] of Object.entries(context.styles?.variables || {})) {
      if (name.startsWith('--') && typeof value === 'string') root.style.setProperty(name, value);
    }
    const cursor = context['openai/interactionCursor'];
    if (cursor === 'default' || cursor === 'pointer') root.style.setProperty('--cursor-interaction', cursor);
  }
  observeSize() {
    let previous = '';
    const schedule = () => {
      if (this.closed || this.frame) return;
      this.frame = window.requestAnimationFrame(() => {
        this.frame = null;
        if (this.closed) return;
        // Measure intrinsic content so a taller host iframe can shrink again.
        const root = document.documentElement;
        const previousHeight = root.style.height;
        root.style.height = 'max-content';
        const height = Math.ceil(root.getBoundingClientRect().height);
        root.style.height = previousHeight;
        const width = Math.ceil(window.innerWidth);
        const size = `${width}:${height}`;
        if (size === previous) return;
        previous = size;
        this.send({ method: 'ui/notifications/size-changed', params: { width, height } });
      });
    };
    if (window.ResizeObserver) {
      this.observer = new ResizeObserver(schedule);
      this.observer.observe(document.documentElement);
      this.observer.observe(document.body);
    }
    schedule();
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.connected = false;
    window.removeEventListener('message', this.receive);
    window.removeEventListener('pagehide', this.unload);
    this.observer?.disconnect();
    if (this.frame) window.cancelAnimationFrame(this.frame);
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(new Error('Connection closed'));
    }
    this.pending.clear();
    this.handlers.closed?.();
  }
}
Object.assign(globalThis.Mataroa ??= {}, { HostBridge });
})();

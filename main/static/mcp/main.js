(() => {
'use strict';
const { HostBridge, Posts } = globalThis.Mataroa;
const bridge = new HostBridge({
  result: result => posts.receiveInitial(result),
  cancelled: () => posts.cancelled(),
  closed: () => posts.dispose(),
});
const posts = new Posts(bridge);
bridge.connect().then(() => posts.ready(), () => posts.connectionError());
})();

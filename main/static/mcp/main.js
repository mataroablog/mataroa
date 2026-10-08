(() => {
'use strict';
const { HostBridge, Library } = globalThis.Mataroa;
const bridge = new HostBridge({
  result: result => library.receiveInitial(result),
  cancelled: () => library.cancelled(),
  closed: () => library.dispose(),
});
const library = new Library(bridge);
bridge.connect().then(() => library.ready(), () => library.connectionError());
})();

// Constrain unchanged Node startup files and their tests to the container's loopback.
const http = require('node:http');
const listen = http.Server.prototype.listen;
http.Server.prototype.listen = function (port, ...args) {
  // Supertest needs listen(0).address() synchronously. Keep its native ephemeral
  // binding; the container has no interface except loopback.
  if (Number(port) === 0) return listen.call(this, port, ...args);
  if (typeof port === 'number' || typeof port === 'string') {
    const callback = args.find((arg) => typeof arg === 'function');
    return listen.call(this, port, '127.0.0.1', ...(callback ? [callback] : []));
  }
  throw new Error('Only TCP loopback listeners are permitted in this rerun');
};

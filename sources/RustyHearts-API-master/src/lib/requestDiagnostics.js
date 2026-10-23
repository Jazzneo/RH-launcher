import { randomUUID } from 'node:crypto';

// Deliberately exclude bodies, query strings, headers, account names and tokens.
export function requestDiagnostics(service, write = console.log) {
  return (req, res, next) => {
    const id = randomUUID();
    const start = performance.now();
    const route = (req.url || '/').split('?')[0];
    const known = /^\/(?:launcher(?:\/|Action)|Auth(?:\/|$)|Billing(?:\/|$)|cgi-bin\/|S1\/)/i.test(route);
    const path = known ? route.replace(/[^a-zA-Z0-9/_.-]/g, '_').slice(0, 160) : '[other]';
    const emit = (event, extra = {}) => write(JSON.stringify({
      time: new Date().toISOString(), component: 'http', service,
      id, event, method: req.method, path, ...extra,
    }));
    emit('received');
    const slow = setTimeout(() => emit('pending', { elapsedMs: 10000 }), 10000);
    slow.unref();
    let ended = false;
    const finish = (event) => {
      if (ended) return;
      ended = true;
      clearTimeout(slow);
      emit(event, { status: res.statusCode, elapsedMs: Math.round(performance.now() - start) });
    };
    res.once('finish', () => finish('completed'));
    res.once('close', () => finish('disconnected'));
    next();
  };
}

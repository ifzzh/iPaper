// Share simultaneous reads only; completed data is never cached.
type Pending = {controller: AbortController; promise: Promise<unknown>; users: number};
const pending = new Map<string, Pending>();
let owner = '';
let epoch = 0;
const listeners = new Set<() => void>();
export const resourceOwnerSnapshot = () => epoch;
export const subscribeResourceOwner = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export function setResourceOwner(value: string) {
  if (value === owner && value) return;
  owner = value;
  for (const entry of pending.values()) entry.controller.abort();
  pending.clear();
  epoch++;
  for (const listener of listeners) listener();
}
export function resourceRead<T>(path: string, load: (signal: AbortSignal) => Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  const key = owner + '\n' + path;
  let entry = pending.get(key);
  if (!entry) {
    const controller = new AbortController();
    entry = {controller, promise: load(controller.signal), users: 0};
    pending.set(key, entry);
    const captured = entry;
    const done = () => { if (pending.get(key) === captured) pending.delete(key); };
    entry.promise.then(done, done);
  }
  entry.users++;
  const shared = entry;
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', abort);
      shared.users--;
      if (!shared.users) setTimeout(() => {
        if (!shared.users && pending.get(key) === shared) {
          pending.delete(key); shared.controller.abort();
        }
      }, 0);
      action();
    };
    const abort = () => finish(() => reject(new DOMException('Aborted', 'AbortError')));
    signal.addEventListener('abort', abort, {once: true});
    shared.promise.then(value => finish(() => resolve(value as T)), error => finish(() => reject(error)));
  });
}

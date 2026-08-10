export type BufferedDelivery<T> = {
  publish: (value: T) => void;
  subscribe: (callback: (value: T) => void) => () => void;
};

export function createBufferedDelivery<T>(
  scheduleMicrotask: (callback: () => void) => void = queueMicrotask,
): BufferedDelivery<T> {
  const callbacks = new Set<(value: T) => void>();
  let buffered: T | null = null;
  let flushScheduled = false;

  const flush = (): void => {
    flushScheduled = false;
    if (buffered === null || callbacks.size === 0) {
      return;
    }
    const value = buffered;
    buffered = null;
    [...callbacks].forEach((callback) => {
      if (callbacks.has(callback)) {
        callback(value);
      }
    });
  };

  const scheduleFlush = (): void => {
    if (flushScheduled || buffered === null || callbacks.size === 0) {
      return;
    }
    flushScheduled = true;
    scheduleMicrotask(flush);
  };

  return {
    publish(value: T): void {
      if (callbacks.size === 0) {
        buffered = value;
        return;
      }
      buffered = null;
      [...callbacks].forEach((callback) => {
        if (callbacks.has(callback)) {
          callback(value);
        }
      });
    },
    subscribe(callback: (value: T) => void): () => void {
      callbacks.add(callback);
      scheduleFlush();
      return () => {
        callbacks.delete(callback);
      };
    },
  };
}

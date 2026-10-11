type Handler = (event: { payload: string }) => void;
type UnlistenFn = () => void;

const handlers: Map<string, Handler[]> = new Map();

export async function listen(
  event: string,
  handler: Handler,
): Promise<UnlistenFn> {
  const list = handlers.get(event) ?? [];
  list.push(handler);
  handlers.set(event, list);
  return () => {
    const idx = list.indexOf(handler);
    if (idx >= 0) list.splice(idx, 1);
  };
}

export async function emit(_event: string, _payload?: unknown): Promise<void> {}

// Expose to Playwright for injecting mock events
(window as unknown as Record<string, unknown>).__tauriMockEmit = (event: string, payload: string) => {
  const list = handlers.get(event) ?? [];
  for (const h of list) {
    h({ payload });
  }
};

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

interface Reply {
  id?: string;
  ok: boolean;
  result?: unknown;
  error?: string;
  event?: string;
  data?: unknown;
}

let nextId = 0;
let listenerReady: Promise<unknown> | null = null;
const pending = new Map<string, (reply: Reply) => void>();
const eventHandlers = new Map<string, Set<(data: unknown) => void>>();

function ensureListener(): Promise<unknown> {
  if (!listenerReady) {
    listenerReady = listen<string>("sidecar-message", event => {
      let reply: Reply;
      try {
        reply = JSON.parse(event.payload) as Reply;
      } catch {
        return; // Native diagnostic lines are not RPC replies.
      }
      if (typeof reply.id === "string") pending.get(reply.id)?.(reply);
      if (typeof reply.event === "string") {
        eventHandlers.get(reply.event)?.forEach(handler => handler(reply.data));
      }
    }).catch(error => {
      listenerReady = null;
      throw error;
    });
  }
  return listenerReady;
}

export async function subscribeSidecarEvent(event: string, handler: (data: unknown) => void): Promise<() => void> {
  await ensureListener();
  const handlers = eventHandlers.get(event) ?? new Set<(data: unknown) => void>();
  eventHandlers.set(event, handlers);
  handlers.add(handler);
  return () => {
    handlers.delete(handler);
    if (!handlers.size) eventHandlers.delete(event);
  };
}

export async function rpc(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
  // Child component effects can run before App's push-event listener is installed.
  await ensureListener();
  const id = `ui-${++nextId}`;
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      pending.delete(id);
      clearTimeout(timer);
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`Backend did not reply to ${method} within 10 seconds. Check backend startup and retry.`));
    }, 10000);
    pending.set(id, reply => {
      cleanup();
      if (reply.ok) resolve(reply.result);
      else reject(new Error(reply.error ?? `Backend rejected ${method}`));
    });
    invoke("send_to_sidecar", { message: JSON.stringify({ id, method, params }) }).catch(error => {
      cleanup();
      reject(error instanceof Error ? error : new Error(String(error)));
    });
  });
}

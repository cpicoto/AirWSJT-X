const RPC_RESPONSES: Record<string, (params: Record<string, unknown>) => unknown> = {
  get_status: () => {
    const mock = window as unknown as Record<string, unknown>;
    return {
      state: mock.__mockState ?? "tracking",
      ...(!mock.__mockLegacySidecar && { wsjtx_known_rigs: mock.__mockRigs ?? [] }),
    };
  },
  callsign_lookup: (p) => ({
    callsign: p.callsign as string,
    grid: (window as unknown as Record<string, Record<string, string>>).__mockGrids?.[p.callsign as string] ?? null,
    name: null,
  }),
  set_station_calls: () => null,
  airscout_set_stations: () => null,
  airscout_apply_filters: () => null,
  airscout_get_sources: () => ({
    active_source: "opensky",
    priority: ["opensky"],
    sources: { opensky: { state: "connected", message: null } },
  }),
  wsjtx_set_rig_name_filter: () => null,
};

export async function invoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
  if (cmd === "plugin:shell|open") return;

  if (cmd === "send_to_sidecar" && args?.message) {
    const msg = JSON.parse(args.message as string) as {
      id: string;
      method: string;
      params: Record<string, unknown>;
    };
    const handler = RPC_RESPONSES[msg.method];
    if (handler) {
      const result = handler(msg.params ?? {});
      setTimeout(() => {
        const emit = (window as unknown as Record<string, (e: string, p: string) => void>).__tauriMockEmit;
        if (emit) emit("sidecar-message", JSON.stringify({ id: msg.id, ok: true, result }));
      }, 10);
    }
    return;
  }

  return {};
}

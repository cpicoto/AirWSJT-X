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
  set_my_station_override: p => ({ callsign: p.callsign, grid: p.grid }),
  airscout_set_stations: () => null,
  airscout_apply_filters: () => null,
  airscout_get_sources: () => ({
    active_source: "opensky",
    priority: ["opensky"],
    sources: {
      opensky: { state: "connected", message: null },
      dump1090: { state: "stopped", message: null },
      adsbexchange: { state: "stopped", message: null },
    },
  }),
  airscout_set_sources: p => ({
    active_source: (p.sources as string[])[0],
    priority: p.sources,
    sources: {
      opensky: { state: "stopped", message: null },
      dump1090: { state: "stopped", message: null },
      adsbexchange: { state: "connected", message: null },
    },
  }),
  wsjtx_set_rig_name_filter: () => null,
};

export async function invoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
  if (cmd === "fetch_remote_db") return "[]";
  if (cmd === "plugin:shell|open") return;
  if (cmd === "get_source_rate_limit_status") {
    return (window as unknown as Record<string, unknown>).__mockQuotas ?? {};
  }
  if (cmd === "get_source_credentials_status") {
    if ((window as unknown as Record<string, unknown>).__mockCredentialReadError) {
      throw "Cannot read credentials from the OS credential store";
    }
    return (window as unknown as Record<string, unknown>).__mockCredentialStatus ?? {
      opensky_client_id: "", opensky_configured: false, adsbexchange_configured: false,
      opensky_poll_interval_s: 30, adsbexchange_poll_interval_s: 30,
    };
  }
  if (cmd === "save_source_credentials") {
    const mock = window as unknown as Record<string, unknown>;
    if (mock.__mockCredentialSaveError) {
      throw mock.__mockCredentialSaveError;
    }
    const value = (mock.__mockCredentialStatus ?? {
      opensky_client_id: "", opensky_configured: false, adsbexchange_configured: false,
      opensky_poll_interval_s: 30, adsbexchange_poll_interval_s: 30,
    }) as Record<string, unknown>;
    if (args?.provider === "opensky") {
      value.opensky_configured = args.secret !== null;
      value.opensky_client_id = args.secret === null ? "" : args.clientId;
    } else {
      value.adsbexchange_configured = args?.secret !== null;
    }
    mock.__mockCredentialStatus = value;
    return;
  }
  if (cmd === "set_source_poll_interval") {
    const mock = window as unknown as Record<string, unknown>;
    const value = (mock.__mockCredentialStatus ?? {
      opensky_client_id: "", opensky_configured: false, adsbexchange_configured: false,
      opensky_poll_interval_s: 30, adsbexchange_poll_interval_s: 30,
    }) as Record<string, unknown>;
    value[`${args?.provider}_poll_interval_s`] = args?.seconds;
    mock.__mockCredentialStatus = value;
    return;
  }
  if (cmd === "fetch_mobile_aircraft") {
    const mock = window as unknown as Record<string, unknown>;
    mock.__mockAircraftRequests = [...(mock.__mockAircraftRequests as unknown[] ?? []), args];
    if (mock.__mockAircraftRateLimited) throw "RATE_LIMITED:1800";
    return args?.provider === "adsbexchange"
      ? { ac: [{ hex: "abc123", flight: "MOBILE", lat: 47.5, lon: -122, alt_geom: 30000, gs: 200, track: 90 }] }
      : { states: [["abc123", "MOBILE", "US", 1000, 1000, -122, 47.5, 9000, false, 100, 90, 0, null, 10000]] };
  }

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

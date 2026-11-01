const RPC_RESPONSES: Record<string, (params: Record<string, unknown>) => unknown> = {
  rotator_output_status: () => ({
    mode: (window as unknown as { __mockOutputMode?: string }).__mockOutputMode ?? "off",
    hamlib_connected: false,
    dde: (window as unknown as { __mockDDEStatus?: unknown }).__mockDDEStatus
      ?? { supported: false, enabled: false },
  }),
  rotator_output_set: params => {
    const mock = window as unknown as Record<string, unknown>;
    if (mock.__mockOutputError) throw mock.__mockOutputError;
    const dde = { supported: true, enabled: params.mode === "dde", service: "SatPC32",
      topic: "SatPcDdeConv", item: "SatPcDdeItem", target: "** NO SATELLITE **", error: null };
    Object.assign(window, { __mockOutputMode: params.mode, __mockDDEStatus: dde,
      __mockDDEEnableRequest: params.mode === "dde" });
    return { mode: params.mode, hamlib_connected: false, dde };
  },
  satpc32_dde_status: () => (window as unknown as { __mockDDEStatus?: unknown }).__mockDDEStatus
    ?? { supported: false, enabled: false },
  satpc32_dde_set: params => {
    const status = { supported: true, enabled: params.enabled, service: "SatPC32",
      topic: "SatPcDdeConv", item: "SatPcDdeItem", target: "** NO SATELLITE **", error: null };
    Object.assign(window, { __mockDDEStatus: status, __mockDDEEnableRequest: params.enabled });
    return status;
  },
  rtlsdr_auto_start: () => ({ attempted: true }),
  rtlsdr_discover: () => (window as unknown as { __mockReceiverDevices?: unknown }).__mockReceiverDevices
    ?? { devices: [] },
  rtlsdr_get_gains: () => ({ supported_gains_db: [0, 20.7, 32.8, 49.6] }),
  rtlsdr_status: () => (window as unknown as { __mockReceiverStatus?: unknown }).__mockReceiverStatus
    ?? { state: "stopped", message: null },
  rtlsdr_start: params => {
    const status = { state: "connected", message: "Receiving CRC-valid ADS-B.",
      device_index: params.device_index, frames: 20, crc_errors: 2,
      valid_messages: 18, positions: 4, aircraft: 2, with_position: 1,
      iq: { samples: 2400000, sample_rate: 2400000, elapsed_s: 1,
        clipped_fraction: 0.0001, mean_power_dbfs: -20, signal_power_dbfs: -12,
        candidate_quiet_magnitude: 4.5, dropped_buffers: 2, discontinuities: 1,
        usb_overruns: null, preamble_candidates: 20,
        df17_crc_rejected_phase_trials: 2, accepted_other_modes: 3,
        rejected_bad: 4, rejected_unknown_icao: 5 },
      last_frames: [{ frame: "8d40621d58c382d690c8ac2863a7", timestamp_12mhz: 6000, crc_syndrome: 0 }],
      gain_db: params.gain_db ?? null, supported_gains_db: [0, 20.7, 32.8, 49.6] };
    Object.assign(window, { __mockReceiverStatus: status, __mockReceiverActive: true });
    return status;
  },
  rtlsdr_set_gain: params => {
    const status = { state: "connected", message: "Receiver restarted.",
      frames: 0, crc_errors: 0, valid_messages: 0, positions: 0,
      gain_db: params.gain_db, supported_gains_db: [0, 20.7, 32.8, 49.6] };
    Object.assign(window, { __mockReceiverStatus: status });
    return status;
  },
  rtlsdr_stop: () => {
    const status = { state: "stopped", message: "Receiver stopped; USB device released." };
    Object.assign(window, { __mockReceiverStatus: status, __mockReceiverActive: false });
    return status;
  },
  get_status: () => {
    const mock = window as unknown as Record<string, unknown>;
    return {
      state: mock.__mockState ?? "tracking",
      ...(!mock.__mockLegacySidecar && { wsjtx_known_rigs: mock.__mockRigs ?? [] }),
      ...("__mockWsjtxError" in mock && { wsjtx_error: mock.__mockWsjtxError }),
    };
  },
  callsign_lookup: (p) => ({
    callsign: p.callsign as string,
    grid: (window as unknown as Record<string, Record<string, string>>).__mockGrids?.[p.callsign as string] ?? null,
    name: null,
  }),
  set_station_calls: () => null,
  set_my_station_override: p => ({ callsign: p.callsign, grid: p.grid, altitude_m: p.altitude_m ?? 0 }),
  set_dx_altitude: p => ({ altitude_m: p.altitude_m }),
  airscout_set_stations: () => null,
  airscout_apply_filters: () => null,
  airscout_get_sources: () => ({
    active_source: (window as unknown as { __mockReceiverActive?: boolean }).__mockReceiverActive ? "rtlsdr" : "opensky",
    priority: (window as unknown as { __mockReceiverActive?: boolean }).__mockReceiverActive ? ["rtlsdr", "opensky"] : ["opensky"],
    sources: {
      opensky: { state: "connected", message: null },
      dump1090: { state: "stopped", message: null },
      adsbexchange: { state: "stopped", message: null },
      ...((window as unknown as { __mockReceiverSupported?: boolean }).__mockReceiverSupported
        ? { rtlsdr: {
          state: (window as unknown as { __mockReceiverActive?: boolean }).__mockReceiverActive ? "connected" : "stopped",
          message: null,
        } } : {}),
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
  lock_aircraft: params => {
    Object.assign(window, { __mockLockRequest: params.icao24 });
    return null;
  },
  rotator_set: params => {
    Object.assign(window, { __mockRotatorTarget: params });
    return null;
  },
};

export async function invoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
  if (cmd === "plugin:shell|open") return;
  if (cmd === "fetch_remote_db") {
    return JSON.stringify((window as unknown as Record<string, unknown>).__mockRemoteDb ?? []);
  }
  if (cmd === "speak_native" || cmd === "stop_native_speech") {
    const mock = window as unknown as Record<string, unknown>;
    mock.__mockSpeechRequests = [
      ...(mock.__mockSpeechRequests as unknown[] ?? []), { cmd, ...args },
    ];
    const error = cmd === "speak_native" ? mock.__mockSpeechError : mock.__mockSpeechStopError;
    if (error) throw error;
    return;
  }
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
    if (mock.__mockMobileResponse) return mock.__mockMobileResponse;
    if (mock.__mockMobileStates) return { states: mock.__mockMobileStates };
    return args?.provider === "adsbexchange"
      ? { ac: [{ hex: "abc123", flight: "MOBILE", lat: 47.5, lon: -122, alt_geom: 30000, gs: 200, track: 90 }] }
      : { states: [["abc123", "MOBILE", "US", 1000, 1000, -122, 47.5, 9000, false, 100, 90, 0, null, 10000]] };
  }

  if (cmd === "fetch_mobile_dump1090") {
    const mock = window as unknown as Record<string, unknown>;
    mock.__mockDump1090Requests = [...(mock.__mockDump1090Requests as unknown[] ?? []), args];
    if (mock.__mockDump1090Response) return mock.__mockDump1090Response;
    return { aircraft: [{
      icao24: "abc123", callsign: "DUMP1090", lat: 47.5, lon: -122, altitude_m: 9000,
      velocity_ms: 200, heading_deg: 90, vertical_rate_ms: 0, on_ground: false,
      position_age_s: mock.__mockDump1090PositionAge ?? null,
    }] };
  }
  if (cmd === "start_mobile_rtlsdr") {
    const mock = window as unknown as Record<string, unknown>;
    mock.__mockRtlSdrStarts = [...(mock.__mockRtlSdrStarts as unknown[] ?? []), args];
    return;
  }
  if (cmd === "fetch_mobile_rtlsdr") {
    return {
      running: true, exit_code: null, valid_messages: 42, crc_errors: 0, positions: 3, log: [],
      aircraft: [{
        icao24: "a1b2c3", callsign: "RTLSDR1", lat: 47.6, lon: -122.2, altitude_m: 10000,
        velocity_ms: 220, heading_deg: 180, vertical_rate_ms: 0, on_ground: false, position_age_s: 1,
      }],
    };
  }
  if (cmd === "stop_mobile_rtlsdr") {
    const mock = window as unknown as Record<string, unknown>;
    mock.__mockRtlSdrStops = Number(mock.__mockRtlSdrStops ?? 0) + 1;
    return;
  }
  if (cmd === "stop_mobile_dump1090") {
    const mock = window as unknown as Record<string, unknown>;
    mock.__mockDump1090Stops = Number(mock.__mockDump1090Stops ?? 0) + 1;
    return;
  }

  if (cmd === "send_to_sidecar" && args?.message) {
    const msg = JSON.parse(args.message as string) as {
      id: string;
      method: string;
      params: Record<string, unknown>;
    };
    const mock = window as unknown as {
      __mockRpcRequests?: typeof msg[];
      __mockRpcErrors?: Record<string, string>;
      __mockRpcNoReply?: string[];
    };
    (mock.__mockRpcRequests ??= []).push(msg);
    if (mock.__mockRpcNoReply?.includes(msg.method)) return;
    const error = mock.__mockRpcErrors?.[msg.method];
    const handler = RPC_RESPONSES[msg.method];
    if (handler || error) {
      const result = error ? undefined : handler(msg.params ?? {});
      setTimeout(() => {
        const emit = (window as unknown as Record<string, (e: string, p: string) => void>).__tauriMockEmit;
        if (emit) emit("sidecar-message", JSON.stringify(error
          ? { id: msg.id, ok: false, error }
          : { id: msg.id, ok: true, result }));
      }, 10);
    }
    return;
  }

  return {};
}

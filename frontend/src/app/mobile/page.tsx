"use client";

import {
  backoffMs,
  formatClock,
  formatDate,
  formatInr,
  humanize,
  takeFragmentParam,
  wsUrl,
} from "@/lib/format";
import { PcmPlayer } from "@/lib/player";
import { isRecord, num, parseMessage, readStore, str, writeStore } from "@/lib/storage";
import { useEffect, useRef, useState } from "react";

const DEVICE_KEY = "mcd5.device";
const CALLER = "Cymbal Bank — AI Collections Desk";
// No "ready" in this time after the socket opens: close it and reconnect.
const AUTH_DEADLINE_MS = 5000;
// On wake, replace a control socket that had no message for this long.
const STALE_MS = 30_000;
// 0.5 s of 16 kHz Int16 audio. Above this backlog, drop mic frames instead of queueing them.
const MAX_MIC_BACKLOG_BYTES = 16_000;

type IncomingCall = { callId: string; caller: string; customerName: string; language: string };

type Phase =
  | { kind: "loading" }
  | { kind: "unpaired"; error?: string }
  | { kind: "idle" }
  | { kind: "ringing"; call: IncomingCall; error?: string }
  | { kind: "connecting"; call: IncomingCall }
  | { kind: "in-call"; call: IncomingCall; startedAt: number }
  | { kind: "ended"; call: IncomingCall; reason: string; status: string };

type Card =
  | { type: "payment_link"; amountInr: number | null; settlementType: string; upiUrl: string }
  | { type: "callback"; date: string; promisedAmountInr: number | null }
  | { type: "escalated"; branch: string };

// The resources of one accepted call. accept() registers this object before its first await.
// The object identity is the call generation: when callRef no longer points to it, the call
// is over, and each later step must dispose what it acquired and stop.
type CallResources = {
  callId: string;
  ws: WebSocket | null;
  capture: AudioContext | null;
  stream: MediaStream | null;
  node: AudioWorkletNode | null;
  // The audioSession type before the call. dispose() puts it back.
  prevSession: string | null;
};

const CLOSE_TEXT: Record<number, string> = {
  4401: "This phone is no longer paired.",
  4403: "This call is no longer active.",
};

const audioSession = () =>
  (navigator as Navigator & { audioSession?: { type: string } }).audioSession;

// Idempotent. Each field is cleared after release, so a second call releases only late arrivals.
function dispose(r: CallResources) {
  if (r.node) {
    r.node.port.onmessage = null;
    r.node.disconnect();
    r.node = null;
  }
  if (r.stream) {
    for (const track of r.stream.getTracks()) track.stop();
    r.stream = null;
  }
  if (r.capture) {
    if (r.capture.state !== "closed") void r.capture.close();
    r.capture = null;
  }
  if (r.ws) {
    const ws = r.ws;
    r.ws = null;
    if (ws.readyState <= WebSocket.OPEN) ws.close(1000);
  }
  if (r.prevSession !== null) {
    const session = audioSession();
    if (session) session.type = r.prevSession;
    r.prevSession = null;
  }
}

export default function MobilePage() {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [device, setDevice] = useState<string | null>(null);
  const [online, setOnline] = useState(false);
  const [cards, setCards] = useState<Card[]>([]);
  const [now, setNow] = useState(() => Date.now());
  // Re-renders the page when an AudioContext changes state.
  const [, setAudioTick] = useState(0);

  // One in-memory credential for both sockets.
  const deviceRef = useRef<string | null>(null);
  const controlRef = useRef<WebSocket | null>(null);
  const callRef = useRef<CallResources | null>(null);
  // The last call that rang. It dedupes the incoming_call that the backend sends again after ready.
  const callIdRef = useRef<string | null>(null);
  const ringingRef = useRef<string | null>(null);
  const playCtxRef = useRef<AudioContext | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  const ringRef = useRef<{ timer: ReturnType<typeof setInterval>; gain: GainNode } | null>(null);

  const bumpAudio = () => setAudioTick((t) => t + 1);

  function setPairing(credential: string | null) {
    deviceRef.current = credential;
    setDevice(credential);
  }

  async function pairWithToken(pairToken: string, fallbackStored: string | null) {
    let error = "";
    try {
      const res = await fetch("/api/mobile/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pair: pairToken }),
      });
      const credential = res.ok ? deviceFromPairResponse(await res.json()) : null;
      if (credential) {
        writeStore("local", DEVICE_KEY, credential);
        setPairing(credential);
        setPhase({ kind: "idle" });
        return;
      }
      error =
        res.status === 401
          ? "This pairing link is not valid. It is used or expired."
          : `Pairing failed (HTTP ${res.status}).`;
    } catch {
      error = "Pairing failed. Check the network and open the link again.";
    }
    setPairing(fallbackStored);
    setPhase(fallbackStored ? { kind: "idle" } : { kind: "unpaired", error });
  }

  async function autoPairDemo(fallbackStored: string | null = null) {
    setPhase({ kind: "loading" });
    try {
      const linkRes = await fetch("/api/demo/pair-link", { method: "POST" });
      if (linkRes.ok) {
        const body: unknown = await linkRes.json();
        const token = isRecord(body) && typeof body.pair === "string" ? body.pair : "";
        if (token) {
          await pairWithToken(token, fallbackStored);
          return;
        }
      }
    } catch {
      // Fall through to unpaired state.
    }
    setPairing(fallbackStored);
    setPhase(
      fallbackStored
        ? { kind: "idle" }
        : { kind: "unpaired", error: "Could not auto-pair. Click Auto-Pair below." },
    );
  }

  // Pair from the one-time link, reuse the stored device credential, or auto-pair on first open.
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs once on load.
  useEffect(() => {
    const pair = takeFragmentParam("pair");
    const stored = readStore("local", DEVICE_KEY);
    if (pair) {
      void pairWithToken(pair, stored);
      return;
    }
    if (stored) {
      setPairing(stored);
      setPhase({ kind: "idle" });
      return;
    }
    void autoPairDemo(null);
  }, []);

  // Control socket: first-message auth, then ready / incoming_call / call_ended.
  // The handlers use only refs and state setters, so a stale closure is safe here.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the socket must not reopen on render.
  useEffect(() => {
    if (!device) return;
    let ws: WebSocket | null = null;
    let attempt = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let authTimer: ReturnType<typeof setTimeout> | undefined;
    let lastMessageAt = 0;
    let stopped = false;

    const connect = () => {
      clearTimeout(retry);
      const sock = new WebSocket(wsUrl("/ws/mobile"));
      ws = sock;
      controlRef.current = sock;
      authTimer = setTimeout(() => {
        if (ws === sock) sock.close();
      }, AUTH_DEADLINE_MS);
      sock.onopen = () => sock.send(JSON.stringify({ type: "auth", device: deviceRef.current }));
      sock.onmessage = (e) => {
        if (ws !== sock) return;
        lastMessageAt = Date.now();
        const msg = typeof e.data === "string" ? parseMessage(e.data) : null;
        if (!msg) return;
        if (msg.type === "ready") {
          clearTimeout(authTimer);
          attempt = 0;
          setOnline(true);
        } else if (msg.type === "incoming_call") {
          onIncoming({
            callId: str(msg.call_id),
            caller: str(msg.caller) || CALLER,
            customerName: str(msg.customer_name),
            language: str(msg.language),
          });
        } else if (msg.type === "call_ended") {
          onEnded(str(msg.call_id), str(msg.reason), str(msg.status));
        }
      };
      sock.onclose = (e) => {
        if (ws !== sock) return;
        clearTimeout(authTimer);
        controlRef.current = null;
        ws = null;
        setOnline(false);
        if (stopped) return;
        if (e.code === 4401) {
          unpair(CLOSE_TEXT[4401]);
          return;
        }
        retry = setTimeout(connect, backoffMs(attempt++));
      };
    };

    // A phone that wakes from sleep or gets the network back may hold a dead socket.
    // Replace the socket at once if it is not open or has been silent.
    const replaceIfStale = () => {
      if (document.visibilityState !== "visible") return;
      if (ws?.readyState === WebSocket.OPEN && Date.now() - lastMessageAt < STALE_MS) return;
      const old = ws;
      ws = null;
      controlRef.current = null;
      clearTimeout(authTimer);
      old?.close();
      setOnline(false);
      attempt = 0;
      connect();
    };

    connect();
    document.addEventListener("visibilitychange", replaceIfStale);
    window.addEventListener("online", replaceIfStale);
    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(authTimer);
      document.removeEventListener("visibilitychange", replaceIfStale);
      window.removeEventListener("online", replaceIfStale);
      ws?.close();
    };
  }, [device]);

  // The browser releases the wake lock when the page is hidden. Get it again on return.
  // biome-ignore lint/correctness/useExhaustiveDependencies: requestWakeLock uses no state.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && playCtxRef.current) requestWakeLock();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  useEffect(() => {
    if (phase.kind !== "in-call") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [phase.kind]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: unmount cleanup with refs only.
  useEffect(() => () => endCallAudio(), []);

  function unpair(error: string) {
    writeStore("local", DEVICE_KEY, null);
    endCallAudio();
    setPairing(null);
    setPhase({ kind: "unpaired", error });
  }

  function requestWakeLock() {
    navigator.wakeLock?.request("screen").catch(() => {
      // Not fatal: the presenter can keep the screen awake by hand.
    });
  }

  function sendControl(msg: object) {
    const ws = controlRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  }

  // Creates or resumes the playback context. Call it only from a tap handler: iOS starts
  // and resumes an AudioContext only inside a user gesture.
  function armPlayback(): void {
    let ctx = playCtxRef.current;
    if (!ctx || ctx.state === "closed") {
      try {
        ctx = new AudioContext({ sampleRate: 24000 });
      } catch {
        // A browser without 24 kHz contexts. The player still makes 24 kHz AudioBuffers,
        // and the browser resamples them to the context rate.
        ctx = new AudioContext();
      }
      ctx.onstatechange = bumpAudio;
      playCtxRef.current = ctx;
      playerRef.current = new PcmPlayer(ctx);
    }
    if (ctx.state !== "running") {
      // Audio scheduled before a suspension or interruption is stale. Drop it.
      playerRef.current?.flush();
      ctx.resume().then(() => {
        bumpAudio();
        if (ringingRef.current) startRing();
      }, bumpAudio);
    }
    // A silent one-sample buffer in the gesture unlocks output on older iOS versions.
    const src = ctx.createBufferSource();
    src.buffer = ctx.createBuffer(1, 1, ctx.sampleRate);
    src.connect(ctx.destination);
    src.start();
    requestWakeLock();
  }

  // Tap handler for "Ready", "Enable sound" and "Resume audio".
  function resumeAudio() {
    armPlayback();
    const capture = callRef.current?.capture;
    if (capture && capture.state !== "running") capture.resume().then(bumpAudio, bumpAudio);
  }

  function onIncoming(call: IncomingCall) {
    if (!call.callId || call.callId === callIdRef.current || callRef.current) return;
    callIdRef.current = call.callId;
    ringingRef.current = call.callId;
    setCards([]);
    setPhase({ kind: "ringing", call });
    startRing();
  }

  function onEnded(callId: string, reason: string, status: string) {
    if (callId && callIdRef.current && callId !== callIdRef.current) return;
    endCallAudio();
    setPhase((p) =>
      p.kind === "ringing" || p.kind === "connecting" || p.kind === "in-call"
        ? { kind: "ended", call: p.call, reason, status }
        : p,
    );
  }

  function startRing() {
    const ctx = playCtxRef.current;
    if (ctx?.state !== "running" || ringRef.current) return;
    const gain = ctx.createGain();
    gain.gain.value = 0.12;
    gain.connect(ctx.destination);
    const burst = () => {
      for (const freq of [440, 480]) {
        const osc = ctx.createOscillator();
        osc.frequency.value = freq;
        osc.connect(gain);
        osc.start();
        osc.stop(ctx.currentTime + 1.2);
      }
      navigator.vibrate?.([400, 200, 400]);
    };
    burst();
    ringRef.current = { timer: setInterval(burst, 3000), gain };
  }

  function stopRing() {
    const ring = ringRef.current;
    if (!ring) return;
    clearInterval(ring.timer);
    ring.gain.disconnect();
    ringRef.current = null;
  }

  // Ends the current call on this phone: the ring, a pending accept, or a live call.
  function endCallAudio() {
    ringingRef.current = null;
    stopRing();
    const r = callRef.current;
    callRef.current = null;
    playerRef.current?.flush();
    if (r) dispose(r);
    bumpAudio();
  }

  async function accept(call: IncomingCall) {
    if (callRef.current) return;
    ringingRef.current = null;
    stopRing();
    // Create and resume both contexts before the first await, while the tap gesture is active.
    armPlayback();
    const session = audioSession();
    const r: CallResources = {
      callId: call.callId,
      ws: null,
      capture: null,
      stream: null,
      node: null,
      prevSession: session ? session.type : null,
    };
    callRef.current = r;
    const current = () => callRef.current === r;
    setPhase({ kind: "connecting", call });

    try {
      const capture = new AudioContext();
      r.capture = capture;
      capture.onstatechange = bumpAudio;
      void capture.resume();
      // Safari 16.4+: ask for the voice-call audio session, so playback and the mic work together.
      if (session) session.type = "play-and-record";

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      r.stream = stream;
      if (!current()) return dispose(r);
      await capture.audioWorklet.addModule("/pcm-capture.js");
      if (!current()) return dispose(r);
      // iOS can suspend the contexts when the audio session changes for the mic. If a resume
      // does not take effect, the page shows "Resume audio".
      void capture.resume();
      void playCtxRef.current?.resume();
      const node = new AudioWorkletNode(capture, "pcm-capture", {
        processorOptions: { outRate: 16000 },
      });
      r.node = node;
      capture.createMediaStreamSource(stream).connect(node);
      // The node outputs silence. The connection keeps the graph pulling audio on all engines.
      node.connect(capture.destination);
      openCallSocket(r, call, node);
    } catch (err) {
      dispose(r);
      if (!current()) return;
      callRef.current = null;
      bumpAudio();
      const why = err instanceof Error ? err.message : String(err);
      ringingRef.current = call.callId;
      setPhase({ kind: "ringing", call, error: `Microphone not available: ${why}` });
      startRing();
    }
  }

  function openCallSocket(r: CallResources, call: IncomingCall, node: AudioWorkletNode) {
    const ws = new WebSocket(wsUrl(`/ws/live-call/${encodeURIComponent(call.callId)}`));
    ws.binaryType = "arraybuffer";
    r.ws = ws;
    // Ignore events from a socket of an earlier call or an abandoned attempt.
    const mine = () => callRef.current === r && r.ws === ws;

    ws.onopen = () => {
      if (!mine()) return;
      ws.send(JSON.stringify({ type: "auth", device: deviceRef.current }));
      node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        if (
          mine() &&
          ws.readyState === WebSocket.OPEN &&
          ws.bufferedAmount < MAX_MIC_BACKLOG_BYTES
        ) {
          ws.send(e.data);
        }
      };
      setPhase({ kind: "in-call", call, startedAt: Date.now() });
    };
    ws.onmessage = (e: MessageEvent<ArrayBuffer | string>) => {
      if (!mine()) return;
      if (typeof e.data !== "string") {
        playerRef.current?.enqueue(e.data);
        return;
      }
      const msg = parseMessage(e.data);
      if (!msg) return;
      if (msg.type === "interrupted") {
        playerRef.current?.flush();
      } else if (msg.type === "payment_link") {
        addCard({
          type: "payment_link",
          amountInr: num(msg.amount_inr),
          settlementType: str(msg.settlement_type),
          upiUrl: str(msg.upi_url),
        });
      } else if (msg.type === "callback") {
        addCard({
          type: "callback",
          date: str(msg.date),
          promisedAmountInr: num(msg.promised_amount_inr),
        });
      } else if (msg.type === "escalated") {
        addCard({ type: "escalated", branch: str(msg.branch) });
      } else if (msg.type === "call_ended") {
        onEnded(call.callId, str(msg.reason), str(msg.status));
      }
    };
    ws.onclose = (e) => {
      if (!mine()) return;
      endCallAudio();
      if (e.code === 4401) {
        unpair(CLOSE_TEXT[4401]);
        return;
      }
      setPhase((p) =>
        p.kind === "connecting" || p.kind === "in-call"
          ? { kind: "ended", call, reason: CLOSE_TEXT[e.code] ?? "Connection closed", status: "" }
          : p,
      );
    };
  }

  function addCard(card: Card) {
    setCards((c) => [...c, card]);
  }

  function decline(call: IncomingCall) {
    endCallAudio();
    sendControl({ type: "decline", call_id: call.callId });
    setPhase({ kind: "ended", call, reason: "Declined", status: "" });
  }

  function hangUp() {
    const r = callRef.current;
    if (r?.ws?.readyState === WebSocket.OPEN) {
      r.ws.send(JSON.stringify({ type: "hangup" }));
    } else if (r) {
      // Setup is still pending, so the backend has no call socket yet. Decline the call.
      sendControl({ type: "decline", call_id: r.callId });
    }
    endCallAudio();
    setPhase((p) =>
      p.kind === "in-call" || p.kind === "connecting"
        ? { kind: "ended", call: p.call, reason: "You hung up", status: "" }
        : p,
    );
  }

  // Refs are read in render here; bumpAudio re-renders on each context state change.
  // Safari can also report "interrupted", so the test is for "running" only.
  const playRunning = playCtxRef.current?.state === "running";
  const capture = callRef.current?.capture;
  const audioOk = playRunning && (!capture || capture.state === "running");

  const onCallScreen =
    phase.kind === "ringing" ||
    phase.kind === "connecting" ||
    phase.kind === "in-call" ||
    phase.kind === "ended";

  return (
    <main className={onCallScreen ? "phone phone-call" : "phone"}>
      <header className="phone-bar">
        <span className="brand">
          <span className="brand-name">Cymbal Bank</span>
          <span className="brand-sub">Collections Desk</span>
        </span>
        {device && (
          <span className={online ? "pill pill-ok" : "pill pill-warn"}>
            <span className="pill-dot" aria-hidden />
            {online ? "Online" : "Reconnecting…"}
          </span>
        )}
      </header>

      {phase.kind === "loading" && <section className="screen center muted">Loading…</section>}

      {phase.kind === "unpaired" && (
        <section className="screen center">
          <div className="halo" aria-hidden>
            <LinkIcon />
          </div>
          <h1>Pair browser phone</h1>
          <p className="muted lead">
            Pair this browser tab as the active Cymbal Bank Web Audio call receiver.
          </p>
          {phase.error && <p className="error">{phase.error}</p>}
          <button
            type="button"
            className="btn btn-primary btn-wide"
            onClick={() => {
              armPlayback();
              void autoPairDemo(null);
            }}
          >
            Auto-Pair &amp; Arm Web Phone
          </button>
        </section>
      )}

      {phase.kind === "idle" && (
        <section className="screen center">
          <div className="halo" aria-hidden>
            <PhoneIcon />
          </div>
          <h1>Waiting for a call</h1>
          <p className="muted lead">Keep this screen open. Connect earphones before the call.</p>
          {playRunning ? (
            <p className="sound sound-on">
              <SpeakerIcon /> Sound is on. The phone rings when a call starts.
            </p>
          ) : (
            <>
              <p className="sound sound-off">
                <SpeakerIcon /> Sound is off. Tap Ready so that the phone can ring.
              </p>
              <button type="button" className="btn btn-primary btn-wide" onClick={resumeAudio}>
                Ready
              </button>
            </>
          )}
        </section>
      )}

      {phase.kind === "ringing" && (
        <section className="screen call-screen">
          <div className="call-top">
            <p className="call-state">Incoming call</p>
            <div className="avatar avatar-ringing" aria-hidden>
              CB
            </div>
            <h1 className="caller">{phase.call.caller}</h1>
            {phase.call.customerName && <p className="customer">To {phase.call.customerName}</p>}
            {phase.call.language && <p className="tag">{phase.call.language}</p>}
          </div>
          <div className="call-body">
            {phase.error && <p className="error">{phase.error}</p>}
            {!playRunning && (
              <button type="button" className="btn btn-ghost" onClick={resumeAudio}>
                Enable sound
              </button>
            )}
          </div>
          <div className="call-actions">
            <button
              type="button"
              className="round round-decline"
              onClick={() => decline(phase.call)}
            >
              <span className="round-icon">
                <PhoneIcon down />
              </span>
              Decline
            </button>
            <button type="button" className="round round-accept" onClick={() => accept(phase.call)}>
              <span className="round-icon">
                <PhoneIcon />
              </span>
              Accept
            </button>
          </div>
        </section>
      )}

      {(phase.kind === "connecting" || phase.kind === "in-call") && (
        <section className="screen call-screen">
          <div className="call-top call-top-compact">
            <p className="call-state timer">
              {phase.kind === "connecting"
                ? "Connecting…"
                : formatClock(Math.max(0, Math.floor((now - phase.startedAt) / 1000)))}
            </p>
            <div className="avatar avatar-small" aria-hidden>
              CB
            </div>
            <h1 className="caller">{phase.call.caller}</h1>
            {phase.call.customerName && <p className="customer">{phase.call.customerName}</p>}
          </div>
          <div className="call-body">
            {!audioOk && (
              <button type="button" className="btn btn-light" onClick={resumeAudio}>
                Resume audio
              </button>
            )}
            <CardList cards={cards} />
          </div>
          <div className="call-actions">
            <button type="button" className="round round-decline" onClick={hangUp}>
              <span className="round-icon">
                <PhoneIcon down />
              </span>
              Hang up
            </button>
          </div>
        </section>
      )}

      {phase.kind === "ended" && (
        <section className="screen call-screen">
          <div className="call-top call-top-compact">
            <p className="call-state">Call ended</p>
            <div className="avatar avatar-small avatar-ended" aria-hidden>
              CB
            </div>
            <h1 className="caller">{phase.call.caller}</h1>
            {phase.reason && <p className="customer">{humanize(phase.reason)}</p>}
            {phase.status && <p className="tag tag-status">{humanize(phase.status)}</p>}
          </div>
          <div className="call-body">
            <CardList cards={cards} />
          </div>
          <div className="call-actions">
            <button
              type="button"
              className="btn btn-ghost btn-wide"
              onClick={() => setPhase({ kind: "idle" })}
            >
              Done
            </button>
          </div>
        </section>
      )}
    </main>
  );
}

// The pair response is {"ok": true, "device": "<credential>"}.
function deviceFromPairResponse(body: unknown): string | null {
  return isRecord(body) && typeof body.device === "string" && body.device ? body.device : null;
}

function CardList({ cards }: { cards: Card[] }) {
  if (cards.length === 0) return null;
  return (
    <div className="cards">
      {cards.map((card, i) => (
        // The list only grows during a call, so the index is a stable key.
        // biome-ignore lint/suspicious/noArrayIndexKey: append-only list.
        <article key={i} className={`card card-${card.type}`}>
          {card.type === "payment_link" && (
            <>
              <p className="card-label">
                <CheckIcon /> UPI payment link sent
              </p>
              <p className="card-amount">
                {card.amountInr === null ? "—" : formatInr(card.amountInr)}
              </p>
              <p className="muted">
                {card.settlementType === "PARTIAL_TWO_MONTH"
                  ? "Partial payment: 2 months of interest"
                  : card.settlementType === "FULL_DUE"
                    ? "Full interest due"
                    : humanize(card.settlementType)}
              </p>
              <p className="card-link">{card.upiUrl}</p>
            </>
          )}
          {card.type === "callback" && (
            <>
              <p className="card-label">
                <CheckIcon /> Callback booked
              </p>
              <p className="card-amount">{formatDate(card.date)}</p>
              {card.promisedAmountInr !== null && (
                <p className="muted">Promised: {formatInr(card.promisedAmountInr)}</p>
              )}
            </>
          )}
          {card.type === "escalated" && (
            <>
              <p className="card-label">
                <CheckIcon /> Passed to the branch team
              </p>
              <p className="card-amount">{card.branch || "Branch manager"}</p>
              <p className="muted">A branch officer will call you.</p>
            </>
          )}
        </article>
      ))}
    </div>
  );
}

function PhoneIcon({ down = false }: { down?: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="28"
      height="28"
      aria-hidden="true"
      className={down ? "icon-down" : ""}
    >
      <path
        fill="currentColor"
        d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.6a1 1 0 0 1-.25 1z"
      />
    </svg>
  );
}

function SpeakerIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path fill="currentColor" d="M4 9v6h4l5 4V5L8 9z" />
      <path
        d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">
      <path
        d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" className="check">
      <path
        d="M5 12.5l4.5 4.5L19 7.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

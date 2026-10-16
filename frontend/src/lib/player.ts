// Plays 24 kHz Int16 LE mono PCM from the call socket without gaps.
// Each chunk starts exactly where the previous chunk ends (playHead). Only after an underrun
// (the queue ran dry) does the next chunk start a short lead time after now, so network
// jitter does not cut words.
const LEAD_S = 0.06;
// The backend sends audio about as fast as it plays. A longer queue is stale: drop it.
const MAX_AHEAD_S = 5;

export function int16ToFloat32(bytes: ArrayBuffer): Float32Array<ArrayBuffer> {
  const pcm = new Int16Array(bytes, 0, bytes.byteLength >> 1);
  const out = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / 0x8000;
  return out;
}

export class PcmPlayer {
  private playHead = 0;
  private sources = new Set<AudioBufferSourceNode>();

  constructor(private ctx: AudioContext) {}

  enqueue(bytes: ArrayBuffer): void {
    const samples = int16ToFloat32(bytes);
    if (samples.length === 0) return;
    const now = this.ctx.currentTime;
    if (this.playHead - now > MAX_AHEAD_S) this.flush();
    // The buffer is 24 kHz in all cases. If the context runs at another rate, the browser
    // resamples the buffer on playback.
    const buffer = this.ctx.createBuffer(1, samples.length, 24000);
    buffer.copyToChannel(samples, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);
    const start = this.playHead > now ? this.playHead : now + LEAD_S;
    src.start(start);
    this.playHead = start + buffer.duration;
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  // Barge-in: the borrower spoke, so drop all queued bot audio at once.
  flush(): void {
    for (const src of this.sources) {
      src.onended = null;
      try {
        src.stop();
      } catch {
        // stop() throws InvalidStateError on old Safari if the source already ended.
      }
      src.disconnect();
    }
    this.sources.clear();
    this.playHead = 0;
  }
}

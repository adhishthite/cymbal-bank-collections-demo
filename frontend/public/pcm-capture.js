// AudioWorklet module for /mobile. The browser loads it with audioWorklet.addModule().
// It changes the microphone audio from the context rate to 16 kHz Int16 LE mono.
// It posts one ArrayBuffer for each 40 ms frame (640 samples).

// The resampler is a windowed-sinc FIR low-pass filter, evaluated at a fractional input
// position for each output sample. The cutoff is 7 kHz (lower for input rates under 16 kHz),
// so energy above 8 kHz does not alias into the 16 kHz output. The Blackman window spans
// 8 zero crossings on each side: about 56 taps at 48 kHz, which is cheap in a worklet.
// A table of 128 fractional phases replaces trig calls in the audio thread.
const PHASES = 128;
const LOBES = 8;

export class Downsampler {
  constructor(inRate, outRate) {
    this.ratio = inRate / outRate;
    const fc = Math.min(7000, 0.45 * Math.min(inRate, outRate)) / inRate;
    this.half = Math.ceil(LOBES / (2 * fc));
    const taps = 2 * this.half;
    this.table = new Float32Array(PHASES * taps);
    for (let p = 0; p < PHASES; p++) {
      let sum = 0;
      for (let j = 0; j < taps; j++) {
        // Distance from the output time to input sample j of the window.
        const d = p / PHASES + this.half - 1 - j;
        const x = 2 * fc * d;
        const sinc = x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x);
        const w =
          0.42 +
          0.5 * Math.cos((Math.PI * d) / this.half) +
          0.08 * Math.cos((2 * Math.PI * d) / this.half);
        const h = Math.abs(d) >= this.half ? 0 : sinc * w;
        this.table[p * taps + j] = h;
        sum += h;
      }
      // Unity gain at DC for each phase.
      for (let j = 0; j < taps; j++) this.table[p * taps + j] /= sum;
    }
    // Zero history, so the first output has a full window.
    this.buf = new Float32Array(this.half);
    // Position of the next output sample, in input samples, relative to buf[0].
    this.pos = this.half - 1;
  }

  /** @param {Float32Array} input @returns {Int16Array} */
  process(input) {
    const buf = new Float32Array(this.buf.length + input.length);
    buf.set(this.buf);
    buf.set(input, this.buf.length);

    const taps = 2 * this.half;
    const out = new Int16Array(Math.ceil(buf.length / this.ratio) + 1);
    let n = 0;
    let pos = this.pos;
    // One spare sample: phase rounding can move i up by one.
    while (Math.floor(pos) + this.half + 1 < buf.length) {
      let i = Math.floor(pos);
      let phase = Math.round((pos - i) * PHASES);
      if (phase === PHASES) {
        i += 1;
        phase = 0;
      }
      const row = phase * taps;
      const first = i - this.half + 1;
      let v = 0;
      for (let j = 0; j < taps; j++) v += buf[first + j] * this.table[row + j];
      v = v < -1 ? -1 : v > 1 ? 1 : v;
      out[n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      pos += this.ratio;
    }

    const keep = Math.max(0, Math.floor(pos) - this.half + 1);
    this.buf = buf.slice(keep);
    this.pos = pos - keep;
    return out.subarray(0, n);
  }
}

const FRAME_SAMPLES = 640;

// AudioWorkletProcessor exists only in the worklet scope. The guard lets bun test import this file.
if (typeof AudioWorkletProcessor !== "undefined") {
  class PcmCapture extends AudioWorkletProcessor {
    constructor(options) {
      super();
      // sampleRate is a global of the worklet scope: the rate of the AudioContext.
      this.down = new Downsampler(sampleRate, options.processorOptions.outRate);
      this.frame = new Int16Array(FRAME_SAMPLES);
      this.fill = 0;
    }

    process(inputs) {
      const channel = inputs[0]?.[0];
      if (!channel) return true;
      const pcm = this.down.process(channel);
      for (let i = 0; i < pcm.length; i++) {
        this.frame[this.fill++] = pcm[i];
        if (this.fill === FRAME_SAMPLES) {
          // Int16Array uses the platform byte order. All browser platforms are little-endian.
          this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
          this.frame = new Int16Array(FRAME_SAMPLES);
          this.fill = 0;
        }
      }
      return true;
    }
  }
  registerProcessor("pcm-capture", PcmCapture);
}

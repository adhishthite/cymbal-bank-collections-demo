import { expect, test } from "bun:test";
import { Downsampler } from "../../public/pcm-capture.js";
import { int16ToFloat32 } from "./player";

// Feeds a tone in 128-sample render quanta, as the AudioWorklet does.
function resampleTone(inRate: number, freq: number, seconds = 1): Int16Array {
  const down = new Downsampler(inRate, 16000);
  const total = Math.round(inRate * seconds);
  const parts: Int16Array[] = [];
  for (let off = 0; off < total; off += 128) {
    const block = new Float32Array(Math.min(128, total - off));
    for (let i = 0; i < block.length; i++)
      block[i] = 0.5 * Math.sin((2 * Math.PI * freq * (off + i)) / inRate);
    parts.push(down.process(block));
  }
  const out = new Int16Array(parts.reduce((n, p) => n + p.length, 0));
  let n = 0;
  for (const p of parts) {
    out.set(p, n);
    n += p.length;
  }
  return out;
}

const rms = (pcm: Int16Array) =>
  Math.sqrt(pcm.reduce((s, v) => s + (v / 0x8000) ** 2, 0) / pcm.length);

test("device rates resample to 16 kHz with the tone intact", () => {
  for (const rate of [48000, 44100, 16000, 8000]) {
    const out = resampleTone(rate, 1000);
    // The filter delay holds back a few output samples.
    expect(Math.abs(out.length - 16000)).toBeLessThan(25);
    // The 1 kHz tone keeps its frequency: 2000 zero crossings in 1 s.
    let crossings = 0;
    for (let i = 1; i < out.length; i++) if (out[i - 1] < 0 !== out[i] < 0) crossings++;
    expect(Math.abs(crossings - 2000)).toBeLessThan(6);
    // The level stays: a 0.5 sine has an RMS of 0.354.
    expect(Math.abs(rms(out) - 0.354)).toBeLessThan(0.02);
  }
});

test("a 12 kHz tone at 48 kHz does not alias into the 16 kHz output", () => {
  // Without the low-pass filter, 12 kHz folds to 4 kHz at full level. Require at least -40 dB.
  expect(rms(resampleTone(48000, 12000))).toBeLessThan(0.354 * 0.01);
});

test("Int16 LE playback bytes decode to the right float range", () => {
  const bytes = new Int16Array([0, 16384, -32768, 32767]).buffer;
  expect(Array.from(int16ToFloat32(bytes))).toEqual([0, 0.5, -1, 32767 / 32768]);
});

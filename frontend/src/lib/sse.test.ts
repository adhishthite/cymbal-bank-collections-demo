import { expect, test } from "bun:test";
import { readSse } from "./sse";

test("SSE events parse across LF, CRLF split over chunks, lone CR and comments", async () => {
  const chunks = ['data: {"a":1}\n\n: keepalive\r', '\n\r\ndata: {"b":', "2}\r\r", "data: x\n"];
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const s of chunks) c.enqueue(new TextEncoder().encode(s));
      c.close();
    },
  });
  const got: string[] = [];
  await readSse(body, (d) => got.push(d));
  // "data: x" has no blank line after it, so it is not a complete event.
  expect(got).toEqual(['{"a":1}', '{"b":2}']);
});

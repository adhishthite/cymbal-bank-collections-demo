// Minimal SSE reader for fetch(). EventSource cannot send the Authorization header.
// It calls onData once for each event, with the joined "data:" lines. It ignores comments.
// Lines end with LF, CRLF or a lone CR, as the SSE format allows.
const MAX_BUFFER_CHARS = 1_000_000;

export async function readSse(
  body: ReadableStream<Uint8Array>,
  onData: (data: string) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let data: string[] = [];
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    for (let m = /\r\n|\r|\n/.exec(buf); m; m = /\r\n|\r|\n/.exec(buf)) {
      // A CR at the end of the buffer can be the first half of a CRLF that the next chunk ends.
      if (m[0] === "\r" && m.index === buf.length - 1) break;
      const line = buf.slice(0, m.index);
      buf = buf.slice(m.index + m[0].length);
      if (line === "") {
        if (data.length > 0) onData(data.join("\n"));
        data = [];
      } else if (line.startsWith("data:")) {
        data.push(line.slice(5).replace(/^ /, ""));
      }
    }
    // A stream with no line ends is broken. The caller reconnects on the error.
    if (buf.length > MAX_BUFFER_CHARS) throw new Error("SSE line too long");
  }
}

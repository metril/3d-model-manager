// Parses G-code off the main thread. Result buffers are transferred, not copied.
import { parseGcodeToolpath } from "./gcodeToolpath";

self.onmessage = (e: MessageEvent<string>) => {
  try {
    const result = parseGcodeToolpath(e.data);
    const transfer = result.layers.map((l) => l.paths.buffer);
    (self as unknown as Worker).postMessage({ ok: true, result }, transfer);
  } catch (err) {
    (self as unknown as Worker).postMessage({ ok: false, error: String(err) });
  }
};

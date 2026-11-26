// Shared SSE source — single fetch, multiple consumers.
//
// Used by WorkPanel + ChatList to subscribe to the same `/agent/chat/stream`
// SSE feed without double-fetching.  Each consumer calls `attach()` to get an
// independent AsyncIterable<StreamEvent> that pulls from one underlying
// ReadableStream.  Stream events fan out lazily as bytes flow in.

import type { StreamEvent } from '../types';
import { parseSSE } from './sse';

export interface StreamSource {
  /**
   * Returns an independent AsyncIterable.  Each call to attach() produces a
   * new iterator that reads from the same underlying body, byte by byte.
   * Stop iterating (return) to detach.
   */
  attach(signal?: AbortSignal): AsyncIterable<StreamEvent>;
  /** Close the underlying fetch (releases the body lock). */
  close(): void;
  /** True once the underlying response has signalled end-of-stream. */
  get done(): boolean;
}

export interface CreateStreamSourceOptions {
  url: string;
  init?: RequestInit;
}

/**
 * Build a fetch + ReadableStream pair and yield events to multiple consumers.
 *
 * The returned StreamSource is single-fetch — every `attach()` call returns an
 * AsyncIterable that drives the same byte pump, so chat + work panel see the
 * exact same events with no duplicate network traffic.
 */
export async function createStreamSource(
  opts: CreateStreamSourceOptions,
): Promise<StreamSource> {
  const response = await fetch(opts.url, opts.init);
  if (!response.ok) {
    throw new Error(`SSE fetch failed: ${response.status} ${response.statusText}`);
  }
  if (!response.body) {
    throw new Error('SSE response had no body');
  }

  let doneFlag = false;
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let pendingResolvers: Array<(chunk: string | null) => void> = [];

  function pump(): void {
    const reader = response.body!.getReader();
    const readOnce = async (): Promise<void> => {
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) {
            doneFlag = true;
            buffer += decoder.decode();
            // Flush any pending records + signal EOF to consumers.
            flushBuffer();
            for (const r of pendingResolvers) r(null);
            pendingResolvers = [];
            return;
          }
          buffer += decoder.decode(value, { stream: true });
          flushBuffer();
        }
      } catch {
        doneFlag = true;
        for (const r of pendingResolvers) r(null);
        pendingResolvers = [];
      }
    };
    void readOnce();
  }

  function flushBuffer(): void {
    buffer = buffer.replace(/\r\n/g, '\n');
    let sepIdx = buffer.indexOf('\n\n');
    while (sepIdx !== -1) {
      const record = buffer.slice(0, sepIdx);
      buffer = buffer.slice(sepIdx + 2);
      const ev = parseRecord(record);
      if (ev) {
        const r = pendingResolvers.shift();
        if (r) r(JSON.stringify(ev));
      }
      sepIdx = buffer.indexOf('\n\n');
    }
  }

  function parseRecord(record: string): StreamEvent | null {
    const lines = record.split('\n');
    const dataLines: string[] = [];
    for (const line of lines) {
      if (!line || line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      if (colon === -1) continue;
      const field = line.slice(0, colon).trim();
      let value = line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'data') dataLines.push(value);
    }
    if (dataLines.length === 0) return null;
    try {
      const payload = JSON.parse(dataLines.join('\n'));
      if (payload && typeof payload === 'object' && 'type' in payload && 'data' in payload) {
        return payload as StreamEvent;
      }
    } catch {
      // ignore parse errors
    }
    return null;
  }

  pump();

  return {
    attach(signal?: AbortSignal): AsyncIterable<StreamEvent> {
      return {
        [Symbol.asyncIterator](): AsyncIterator<StreamEvent> {
          const buffer2: string[] = [];
          let finished = false;
          if (signal) {
            signal.addEventListener('abort', () => {
              finished = true;
              for (const r of pendingResolvers) r(null);
            });
          }
          return {
            async next(): Promise<IteratorResult<StreamEvent>> {
              if (signal?.aborted) return { value: undefined, done: true };
              if (buffer2.length > 0) {
                const raw = buffer2.shift()!;
                return { value: JSON.parse(raw) as StreamEvent, done: false };
              }
              if (doneFlag) return { value: undefined, done: true };
              if (finished) return { value: undefined, done: true };
              const chunk = await new Promise<string | null>((resolve) => {
                pendingResolvers.push(resolve);
              });
              if (chunk === null) {
                finished = true;
                // Drain any remaining buffered records.
                if (buffer2.length > 0) {
                  const raw = buffer2.shift()!;
                  return { value: JSON.parse(raw) as StreamEvent, done: false };
                }
                return { value: undefined, done: true };
              }
              buffer2.push(chunk);
              const shifted = buffer2.shift();
              if (shifted) {
                return { value: JSON.parse(shifted) as StreamEvent, done: false };
              }
              return { value: undefined, done: true };
            },
            async return(): Promise<IteratorResult<StreamEvent>> {
              finished = true;
              return { value: undefined, done: true };
            },
          };
        },
      };
    },
    close(): void {
      doneFlag = true;
      for (const r of pendingResolvers) r(null);
      pendingResolvers = [];
    },
    get done(): boolean {
      return doneFlag;
    },
  };
}

// Re-export parseSSE for the legacy single-consumer path used by useChatStream.
export { parseSSE };
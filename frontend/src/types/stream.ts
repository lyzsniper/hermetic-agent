// Stream types — shared SSE source for multi-consumer subscriptions.

import type { StreamEvent } from './domain';

export interface StreamSource {
  attach(signal?: AbortSignal): AsyncIterable<StreamEvent>;
  close(): void;
  readonly done: boolean;
}
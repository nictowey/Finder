import type { PushSubscription, RequestOptions } from "web-push";

export function deliver(
  db: { query(sql: string, values?: unknown[]): Promise<{ rows: any[] }> },
  send?: (subscription: PushSubscription, payload: string, options: RequestOptions) => Promise<unknown>,
): Promise<{ status: string; accepted_events: number; devices: number; accepted_devices?: number; invalid_devices?: number; failed_devices?: number }>;

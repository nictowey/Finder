import type { PushSubscription, RequestOptions } from "web-push";

export function deliver(
  db: { query(sql: string, values?: unknown[]): Promise<{ rows: any[] }> },
  send?: (subscription: PushSubscription, payload: string, options: RequestOptions) => Promise<unknown>,
): Promise<{ delivered: number; devices: number; failed_devices?: number }>;

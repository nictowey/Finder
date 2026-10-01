export function checkNotificationPolicy(
  db: { query(sql: string, values?: unknown[]): Promise<{ rows: any[] }> },
): Promise<void>;

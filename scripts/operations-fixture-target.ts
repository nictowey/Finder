export function validateOperationsFixtureURL(value: string) {
  let valid = false;
  try {
    const url = new URL(value);
    valid = url.protocol === 'postgresql:' && url.hostname === '127.0.0.1'
      && /^\d+$/.test(url.port) && Number(url.port) > 0 && Number(url.port) < 65536
      && url.pathname === '/finder_ci' && url.username === 'finder_ci'
      && url.password === 'finder_ci_ephemeral' && !url.search && !url.hash;
  } catch { /* Reject without printing credentials or the rejected URL. */ }
  if (!valid) throw new Error('Operations gate requires the isolated loopback CI fixture');
}

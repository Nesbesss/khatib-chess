// Record a win against Kraken.
import { store } from './_store.js';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'POST only' });
    return;
  }
  const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}')
                                            : (req.body || {});
  // Names come from the browser, so they are untrusted: bound the length and
  // strip anything that is not plain text.
  const name = String(body.name || '').trim().slice(0, 16)
                 .replace(/[^\p{L}\p{N} _.\-]/gu, '');
  const level = String(body.level || '').slice(0, 24);
  const depth = Number(body.depth) || 0;
  if (!name) {
    res.status(400).json({ error: 'name required' });
    return;
  }
  try {
    await store.recordWin(name, level, depth);
    res.status(200).json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: 'could not record' });
  }
}

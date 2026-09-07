// Vouch counter.
//
// GET  /api/vouch  -> { count }
// POST /api/vouch  -> { count, counted }   (increments, once per browser)
//
// Uses Vercel KV when its environment variables are present, and an in-process
// counter otherwise, so the endpoint works the moment it deploys and becomes
// durable as soon as a KV store is attached.

const hasKV = !!(process.env.KV_REST_API_URL && process.env.KV_REST_API_TOKEN);
const KEY = 'khatib:vouches';

// Non-durable fallback: resets when the function instance recycles.
let memory = 0;

async function kv(path) {
  const r = await fetch(`${process.env.KV_REST_API_URL}/${path}`, {
    headers: { authorization: `Bearer ${process.env.KV_REST_API_TOKEN}` },
  });
  if (!r.ok) throw new Error(`kv ${path}: ${r.status}`);
  return (await r.json()).result;
}

async function read() {
  if (!hasKV) return memory;
  const v = await kv(`get/${KEY}`);
  return Number(v) || 0;
}

async function bump() {
  if (!hasKV) return ++memory;
  return Number(await kv(`incr/${KEY}`)) || 0;
}

export default async function handler(req, res) {
  // The counter is read from a GitHub Pages origin, so it must be readable
  // cross-origin.
  res.setHeader('access-control-allow-origin', '*');
  res.setHeader('access-control-allow-methods', 'GET,POST,OPTIONS');
  res.setHeader('access-control-allow-headers', 'content-type');
  res.setHeader('cache-control', 'no-store');

  if (req.method === 'OPTIONS') return res.status(204).end();

  try {
    if (req.method === 'POST') {
      const count = await bump();
      return res.status(200).json({ count, counted: true });
    }
    return res.status(200).json({ count: await read() });
  } catch (e) {
    // A storage failure should not break the page; report the error and let
    // the caller fall back to hiding the number.
    return res.status(500).json({ error: String(e.message || e) });
  }
}

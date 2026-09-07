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

// A GitHub gist is the durable store when no KV is attached: free, needs no
// card, and survives function recycles. Requires GIST_ID and GIST_TOKEN.
const GIST = process.env.GIST_ID;
const GIST_TOKEN = process.env.GIST_TOKEN;
const hasGist = !!(GIST && GIST_TOKEN);
const FILE = 'vouch.json';

// Last resort: resets when the function instance recycles.
let memory = 0;

async function gist(method, body) {
  const r = await fetch(`https://api.github.com/gists/${GIST}`, {
    method,
    headers: {
      authorization: `Bearer ${GIST_TOKEN}`,
      accept: 'application/vnd.github+json',
      'content-type': 'application/json',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`gist ${method}: ${r.status}`);
  return r.json();
}

async function gistRead() {
  const d = await gist('GET');
  const raw = d.files?.[FILE]?.content;
  return Number(JSON.parse(raw || '{}').count) || 0;
}

async function gistBump() {
  // Read-modify-write: two clicks in the same instant can collide and lose
  // one, which is acceptable for a vouch counter.
  const next = (await gistRead()) + 1;
  await gist('PATCH', {
    files: { [FILE]: { content: JSON.stringify({ count: next }) } },
  });
  return next;
}

async function kv(path) {
  const r = await fetch(`${process.env.KV_REST_API_URL}/${path}`, {
    headers: { authorization: `Bearer ${process.env.KV_REST_API_TOKEN}` },
  });
  if (!r.ok) throw new Error(`kv ${path}: ${r.status}`);
  return (await r.json()).result;
}

async function read() {
  if (hasGist) return gistRead();
  if (!hasKV) return memory;
  const v = await kv(`get/${KEY}`);
  return Number(v) || 0;
}

async function bump() {
  if (hasGist) return gistBump();
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

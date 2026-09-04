// Leaderboard storage.
//
// Uses Vercel KV when its environment variables are present, and an in-process
// map otherwise, so the site is playable the moment it deploys and gains a
// durable leaderboard as soon as a KV store is attached.

const hasKV = !!(process.env.KV_REST_API_URL && process.env.KV_REST_API_TOKEN);
const KEY = 'kraken:winners';

// Non-durable fallback: resets when the function instance recycles.
const memory = new Map();

async function kv(path, body) {
  const r = await fetch(`${process.env.KV_REST_API_URL}/${path}`, {
    method: body ? 'POST' : 'GET',
    headers: {
      authorization: `Bearer ${process.env.KV_REST_API_TOKEN}`,
      'content-type': 'application/json',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`kv ${r.status}`);
  return r.json();
}

export const store = {
  async recordWin(name, level, depth) {
    if (!hasKV) {
      const cur = memory.get(name) || { name, wins: 0, depth: 0, best_level: '' };
      cur.wins += 1;
      if (depth > cur.depth) { cur.depth = depth; cur.best_level = level; }
      memory.set(name, cur);
      return;
    }
    // hgetall/hset keyed by player, so repeat wins accumulate.
    const raw = await kv(`hget/${KEY}/${encodeURIComponent(name)}`)
                  .then(r => r.result).catch(() => null);
    const cur = raw ? JSON.parse(raw) : { name, wins: 0, depth: 0, best_level: '' };
    cur.wins += 1;
    if (depth > cur.depth) { cur.depth = depth; cur.best_level = level; }
    await kv(`hset/${KEY}/${encodeURIComponent(name)}`, [JSON.stringify(cur)]);
  },

  async top(n) {
    let rows;
    if (!hasKV) {
      rows = [...memory.values()];
    } else {
      const all = await kv(`hgetall/${KEY}`).then(r => r.result).catch(() => []);
      rows = [];
      // hgetall returns a flat [field, value, field, value, ...] list.
      for (let i = 1; i < (all || []).length; i += 2) {
        try { rows.push(JSON.parse(all[i])); } catch (e) { /* skip bad row */ }
      }
    }
    // Most wins first, then the harder level as a tiebreak.
    rows.sort((a, b) => b.wins - a.wins || b.depth - a.depth);
    return rows.slice(0, n);
  },
};

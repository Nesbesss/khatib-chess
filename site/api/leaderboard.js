// Leaderboard read. Backed by Vercel KV when configured; falls back to an
// in-memory store so the site still works on a fresh deploy without a database.
import { store } from './_store.js';

export default async function handler(req, res) {
  try {
    const rows = await store.top(25);
    res.setHeader('cache-control', 'public, max-age=10');
    res.status(200).json(rows);
  } catch (e) {
    res.status(200).json([]);
  }
}

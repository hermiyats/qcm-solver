// Tombstone for the old /api/solve path — ensures requests to this URL
// get a hard 404 rather than falling through to api/index.ts.
import type { VercelRequest, VercelResponse } from "@vercel/node";

export default function handler(_req: VercelRequest, res: VercelResponse) {
  res.status(404).json({ error: "Not found." });
}

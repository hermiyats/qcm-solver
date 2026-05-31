// Health check. Route: GET /api/health
import type { VercelRequest, VercelResponse } from "@vercel/node";

export default function handler(_req: VercelRequest, res: VercelResponse) {
  res.status(200).json({
    status: "ok",
    model: process.env.QCM_MODEL ?? "claude-opus-4-8",
  });
}

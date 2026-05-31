// Middle-layer API between the QCM desktop app and the Claude API.
//
// Deployed as a Vercel serverless function. Route: POST /api/solve
// The desktop app sends screenshots as base64 JSON; this function attaches a
// fixed QCM-solving prompt, calls Claude, and returns the answer as JSON.
//
// The Anthropic API key lives only here (Vercel env var ANTHROPIC_API_KEY) —
// the desktop client never sees it.

import type { VercelRequest, VercelResponse } from "@vercel/node";
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic(); // reads ANTHROPIC_API_KEY from the environment

const MODEL = process.env.QCM_MODEL ?? "claude-opus-4-8";
const MAX_TOKENS = Number(process.env.QCM_MAX_TOKENS ?? "16000");

const SUPPORTED_MEDIA_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

// The predefined prompt. It is identical on every request, so it goes in the
// system prompt with a cache breakpoint — repeated calls read it from cache
// (~90% cheaper) instead of reprocessing it each time.
const SYSTEM_PROMPT = `You are an expert tutor that solves multiple-choice questions \
(QCM — Questionnaire à Choix Multiples) shown in screenshots.

The user sends one or more screenshots. Together they contain one or more \
questions, each with a set of answer options (which may be labelled A/B/C/D, \
1/2/3/4, with checkboxes, radio buttons, or similar). Several screenshots may \
belong to a single long question, so read all of them before answering.

For every question you can identify:
1. Restate the question briefly so the user knows which one you mean.
2. State the correct option(s) clearly and unambiguously (e.g. "Answer: B"). \
Multiple options can be correct — say so when that is the case.
3. Give a short, focused justification (1-3 sentences) explaining why that \
answer is correct and, when useful, why the tempting alternatives are wrong.

Rules:
- Work in the same language as the question.
- If a screenshot is unreadable or a question is ambiguous, say so explicitly \
rather than guessing silently.
- Be concise. Do not add preamble such as "Here is the answer". Start directly \
with the first question.
- If no question is visible in the screenshots, say that clearly.`;

const USER_INSTRUCTION =
  "Solve the multiple-choice question(s) shown in the screenshot(s) above.";

interface ImageInput {
  media_type: string;
  data: string;
}

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "Method not allowed. Use POST." });
    return;
  }

  // Vercel parses JSON bodies automatically, but guard against a raw string.
  let body: { images?: ImageInput[] } = {};
  try {
    body = typeof req.body === "string" ? JSON.parse(req.body) : (req.body ?? {});
  } catch {
    res.status(400).json({ error: "Request body is not valid JSON." });
    return;
  }

  const images: ImageInput[] = [];
  for (const item of body.images ?? []) {
    if (!item?.data) continue;
    const media_type = SUPPORTED_MEDIA_TYPES.has(item.media_type)
      ? item.media_type
      : "image/png";
    images.push({ media_type, data: item.data });
  }

  if (images.length === 0) {
    res
      .status(400)
      .json({ error: "No images received. Send screenshots as base64 JSON: {\"images\":[{\"media_type\":\"image/png\",\"data\":\"...\"}]}" });
    return;
  }

  const content = [
    ...images.map((img) => ({
      type: "image" as const,
      source: {
        type: "base64" as const,
        media_type: img.media_type as "image/png" | "image/jpeg" | "image/gif" | "image/webp",
        data: img.data,
      },
    })),
    { type: "text" as const, text: USER_INSTRUCTION },
  ];

  // Assigned to a variable (not passed as a fresh literal) so newer request
  // fields stay forward-compatible across SDK versions.
  const params = {
    model: MODEL,
    max_tokens: MAX_TOKENS,
    thinking: { type: "adaptive" as const },
    output_config: { effort: "high" as const },
    system: [
      {
        type: "text" as const,
        text: SYSTEM_PROMPT,
        cache_control: { type: "ephemeral" as const },
      },
    ],
    messages: [{ role: "user" as const, content }],
  };

  try {
    const message = await client.messages.create(params);

    const answer =
      message.content
        .filter((b): b is Anthropic.TextBlock => b.type === "text")
        .map((b) => b.text)
        .join("\n")
        .trim() || "(The model returned no text answer.)";

    res.status(200).json({
      answer,
      image_count: images.length,
      model: message.model,
      request_id: message._request_id,
    });
  } catch (err) {
    if (err instanceof Anthropic.APIError) {
      res
        .status(502)
        .json({ error: `Claude API error (${err.status ?? "?"}): ${err.message}` });
      return;
    }
    res.status(500).json({ error: "Unexpected server error." });
  }
}

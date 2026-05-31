// Middle-layer API between the QCM desktop app and the Claude API.
//
// Deployed as a Vercel serverless function. Route: POST /api/solve
// The desktop app sends screenshots as base64 JSON; this function attaches a
// fixed QCM-solving prompt, calls Claude, and returns the answer as JSON.
//
// The Anthropic API key lives only here (Vercel env var ANTHROPIC_API_KEY) —
// the desktop client never sees it.

import { timingSafeEqual } from "node:crypto";
import type { VercelRequest, VercelResponse } from "@vercel/node";
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic(); // reads ANTHROPIC_API_KEY from the environment

// Shared secret. Only requests presenting this token (Authorization: Bearer …,
// or x-api-key) are accepted — this keeps random callers off our rate limits.
const ACCESS_TOKEN = process.env.QCM_ACCESS_TOKEN ?? "";

// Constant-time comparison so we don't leak the token length/contents via
// response timing.
function tokenMatches(provided: string): boolean {
  if (!ACCESS_TOKEN || !provided) return false;
  const a = Buffer.from(provided);
  const b = Buffer.from(ACCESS_TOKEN);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

function extractToken(req: VercelRequest): string {
  const auth = req.headers["authorization"];
  if (typeof auth === "string" && auth.startsWith("Bearer ")) {
    return auth.slice("Bearer ".length).trim();
  }
  const key = req.headers["x-api-key"];
  if (typeof key === "string") return key.trim();
  return "";
}

const DEFAULT_MODEL = process.env.QCM_MODEL ?? "claude-opus-4-8";
const MAX_TOKENS = Number(process.env.QCM_MAX_TOKENS ?? "16000");

// Latest generally-available models (verified against platform.claude.com,
// June 2026). Opus 4.8 is the most capable; Sonnet 4.6 balances speed/quality;
// Haiku 4.5 is fastest.
const ALLOWED_MODELS = new Set([
  "claude-opus-4-8",
  "claude-sonnet-4-6",
  "claude-haiku-4-5",
]);

// Models that support adaptive thinking + the `effort` parameter. Haiku 4.5
// supports NEITHER — sending `output_config.effort` or `thinking.adaptive` to
// it returns a 400, so we branch on this below.
const ADAPTIVE_EFFORT_MODELS = new Set([
  "claude-opus-4-8",
  "claude-sonnet-4-6",
]);

const ALLOWED_EFFORTS = new Set(["high", "medium", "low"]);

const SUPPORTED_MEDIA_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

// ---------------------------------------------------------------------------
// Pre-made system prompts — one per scenario
// ---------------------------------------------------------------------------

const SYSTEM_PROMPTS: Record<string, string> = {
  general: `You are an expert tutor that solves multiple-choice questions \
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
- If no question is visible in the screenshots, say that clearly.`,

  medical: `You are a medical expert tutor specialising in clinical sciences. \
You solve multiple-choice questions (QCM) from medical, nursing, pharmacy, and \
biology exams shown in screenshots.

For every question you can identify:
1. Restate the question briefly.
2. State the correct option(s) (e.g. "Answer: C"). Multiple correct answers \
are common in medical QCMs — identify all of them.
3. Give a focused clinical justification: the mechanism, the anatomical basis, \
the pharmacological action, or the diagnostic reasoning — whichever is relevant. \
Mention key contraindications or side-effects if the question touches on them.
4. Briefly explain why the most tempting wrong options are incorrect.

Rules:
- Use standard medical terminology.
- Work in the same language as the question.
- Never guess silently — flag ambiguous stems or unclear images.
- Be concise; avoid preamble.`,

  programming: `You are a senior software engineer and CS tutor. You solve \
multiple-choice questions (QCM) about programming, algorithms, data structures, \
computer architecture, networking, and software engineering shown in screenshots.

For every question you can identify:
1. Restate the question briefly.
2. State the correct option(s) (e.g. "Answer: A"). Flag all correct answers \
when multiple are valid.
3. Explain the reasoning: trace the code if needed, derive the time/space \
complexity, recall the relevant language rule or protocol specification.
4. Point out why the wrong options fail (e.g. off-by-one, incorrect Big-O, \
wrong output).

Rules:
- Use precise technical language.
- If the question contains code, analyse it step by step.
- Work in the same language as the question text.
- Flag unreadable code or ambiguous questions explicitly.
- Be concise; no preamble.`,

  math: `You are an expert mathematics and physics tutor. You solve \
multiple-choice questions (QCM) covering algebra, calculus, statistics, \
mechanics, electromagnetism, thermodynamics, and related fields shown in \
screenshots.

For every question you can identify:
1. Restate the question briefly.
2. State the correct option(s) (e.g. "Answer: D").
3. Show the key calculation or derivation step-by-step. Name the theorem, \
formula, or law you are applying. Include units where relevant and watch \
significant figures.
4. Briefly explain why the distractors are wrong (e.g. sign error, wrong \
formula, unit mismatch).

Rules:
- Present maths clearly using standard notation (fractions, exponents, etc.).
- Work in the same language as the question.
- If a diagram is unreadable, say so.
- Be concise; no preamble.`,

  language: `You are an expert language and literature tutor. You solve \
multiple-choice questions (QCM) about grammar, orthography, vocabulary, \
reading comprehension, literary analysis, and translation shown in screenshots.

For every question you can identify:
1. Restate the question briefly.
2. State the correct option(s) (e.g. "Answer: B").
3. Explain the rule or reasoning: cite the grammatical rule, define the word, \
identify the literary device, or paraphrase the relevant passage as needed.
4. Explain why the wrong options fail.

Rules:
- Match the language of the question (French grammar → answer in French, etc.).
- For comprehension questions, quote the relevant excerpt briefly.
- Be concise; no preamble.`,
};

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

  // ── Authorisation ──────────────────────────────────────────────────────────
  // Fail closed: if no token is configured server-side, reject everything
  // rather than silently running unprotected.
  if (!ACCESS_TOKEN) {
    res.status(503).json({ error: "Server is missing QCM_ACCESS_TOKEN configuration." });
    return;
  }
  if (!tokenMatches(extractToken(req))) {
    res.status(401).json({ error: "Unauthorized. Missing or invalid access token." });
    return;
  }

  let body: { images?: ImageInput[]; model?: string; effort?: string; prompt?: string } = {};
  try {
    body = typeof req.body === "string" ? JSON.parse(req.body) : (req.body ?? {});
  } catch {
    res.status(400).json({ error: "Request body is not valid JSON." });
    return;
  }

  // Resolve model — client value wins if it's in the allowed set.
  const model =
    body.model && ALLOWED_MODELS.has(body.model) ? body.model : DEFAULT_MODEL;

  // Resolve effort.
  const effort =
    body.effort && ALLOWED_EFFORTS.has(body.effort)
      ? (body.effort as "high" | "medium" | "low")
      : "high";

  // Resolve system prompt.
  const systemPrompt =
    body.prompt && SYSTEM_PROMPTS[body.prompt]
      ? SYSTEM_PROMPTS[body.prompt]
      : SYSTEM_PROMPTS.general;

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

  // Base request. Adaptive thinking + effort are only attached for models that
  // support them (Opus 4.8, Sonnet 4.6). Haiku 4.5 rejects both, so it runs a
  // plain request.
  const params: Anthropic.MessageCreateParamsStreaming = {
    model,
    max_tokens: MAX_TOKENS,
    stream: true,
    system: [
      {
        type: "text" as const,
        text: systemPrompt,
        cache_control: { type: "ephemeral" as const },
      },
    ],
    messages: [{ role: "user" as const, content }],
  };

  const supportsAdaptiveEffort = ADAPTIVE_EFFORT_MODELS.has(model);
  if (supportsAdaptiveEffort) {
    params.thinking = { type: "adaptive" };
    params.output_config = { effort };
  }

  try {
    // Stream the response so long answers and any internal processing (thinking,
    // tool steps) never trip an HTTP timeout. `finalMessage()` waits for the
    // model to fully finish and returns the complete, assembled message — we
    // only ever hand the desktop client the final, ready answer.
    const stream = client.messages.stream(params);
    const message = await stream.finalMessage();

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
      prompt: body.prompt ?? "general",
      effort: supportsAdaptiveEffort ? effort : null,
      request_id: stream.request_id,
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

#!/usr/bin/env node
/**
 * add-model.mjs — Add models to model_pricing.json using litellm as source of truth.
 *
 * Usage:
 *   node scripts/add-model.mjs openai/gpt-5-2025-08-07
 *   node scripts/add-model.mjs anthropic/claude-sonnet-4-5-20250929 google/gemini-3-flash
 *   node scripts/add-model.mjs openai/gpt-5-2025-08-07 --apply     # Write directly to model_pricing.json
 *   node scripts/add-model.mjs openai/gpt-5-2025-08-07 --dry-run   # Preview only (default)
 *
 * What it does:
 *   1. Fetches litellm pricing JSON (cached for 1hr in /tmp)
 *   2. Fetches OpenRouter model list (cached for 1hr in /tmp)
 *   3. For each model:
 *      - Extracts pricing from litellm
 *      - Derives capabilities from litellm flags
 *      - Finds OpenRouter identifier
 *      - Outputs a ready-to-merge JSON snippet
 *   4. With --apply: merges into model_pricing.json directly
 *
 * Capability mapping (litellm → our format):
 *   supports_function_calling  → "routing"
 *   supports_vision            → "image"
 *   supports_pdf_input         → "pdf"
 *   supports_web_search        → "search"
 *   supports_reasoning         → "reasoning"
 *   supports_computer_use      → "computer-use"
 *   mode === "image_generation" → "image-gen"
 *
 * Not auto-detected (add manually if needed): "mcp", "agentic", "file-search", "code-interpreter"
 */

import { readFileSync, writeFileSync, existsSync, statSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const MODEL_PRICING_PATH = join(__dirname, "..", "model_pricing.json");

const LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json";
const OPENROUTER_URL = "https://openrouter.ai/api/v1/models";

const LITELLM_CACHE = "/tmp/litellm_prices_cache.json";
const OPENROUTER_CACHE = "/tmp/openrouter_models_cache.json";
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

// ── Provider name normalization ────────────────────────────────────────────
// Maps litellm provider names → our model_pricing.json provider keys
const PROVIDER_MAP = {
  openai: "openai",
  anthropic: "anthropic",
  "vertex_ai-language-models": "google",
  vertex_ai: "google",
  gemini: "google",
  google: "google",
  cohere: "cohere",
  "cohere_chat": "cohere",
  mistral: "mistral",
  together_ai: "togetherai",
  perplexity: "perplexity",
  replicate: "replicate",
  bedrock: "bedrock",
  xai: "x-ai",
  "x-ai": "x-ai",
};

// ── Fetch with cache ───────────────────────────────────────────────────────
async function fetchWithCache(url, cachePath) {
  if (existsSync(cachePath)) {
    const age = Date.now() - statSync(cachePath).mtimeMs;
    if (age < CACHE_TTL_MS) {
      return JSON.parse(readFileSync(cachePath, "utf8"));
    }
  }
  console.error(`Fetching ${url}...`);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} fetching ${url}`);
  const data = await res.json();
  writeFileSync(cachePath, JSON.stringify(data));
  return data;
}

// ── Derive capabilities from litellm flags ─────────────────────────────────
function deriveCapabilities(entry) {
  const caps = [];
  if (entry.supports_function_calling) caps.push("routing");
  if (entry.supports_vision) caps.push("image");
  if (entry.supports_pdf_input) caps.push("pdf");
  if (entry.supports_web_search) caps.push("search");
  if (entry.supports_reasoning) caps.push("reasoning");
  if (entry.supports_computer_use) caps.push("computer-use");
  if (entry.mode === "image_generation") caps.push("image-gen");
  return caps;
}

// ── Find model in litellm ──────────────────────────────────────────────────
function findInLitellm(litellm, provider, model) {
  // Try exact keys in priority order
  const candidates = [
    model,
    `${provider}/${model}`,
    `openai/${model}`,
    `anthropic/${model}`,
    `google/${model}`,
    `gemini/${model}`,
    `xai/${model}`,
    `x-ai/${model}`,
    `together_ai/${model}`,
    `cohere_chat/${model}`,
    `mistral/${model}`,
    `perplexity/${model}`,
    `replicate/${model}`,
    `bedrock/${model}`,
  ];

  for (const key of candidates) {
    if (litellm[key]) return { key, data: litellm[key] };
  }

  // Fuzzy: strip provider prefix and search
  const bare = model.replace(/^[^/]+\//, "");
  for (const key of Object.keys(litellm)) {
    if (key.endsWith("/" + bare) || key === bare) {
      return { key, data: litellm[key] };
    }
  }

  return null;
}

// ── Find OpenRouter identifier ─────────────────────────────────────────────
function findOpenRouterId(orModels, provider, model) {
  const orSet = new Set(orModels.map((m) => m.id));

  // Try common patterns
  const candidates = [
    `${provider}/${model}`,
    `openai/${model}`,
    `anthropic/${model}`,
    `google/${model}`,
    `x-ai/${model}`,
    `meta-llama/${model}`,
    `mistralai/${model}`,
    `deepseek/${model}`,
  ];

  for (const c of candidates) {
    if (orSet.has(c)) return c;
  }

  // Fuzzy: search for model name substring
  const bare = model.replace(/^[^/]+\//, "");
  const match = orModels.find(
    (m) => m.id.endsWith("/" + bare) || m.id.includes(bare)
  );
  return match ? match.id : null;
}

// ── Detect alias (dated → non-dated) ───────────────────────────────────────
function detectAlias(model) {
  // Match patterns like gpt-5-2025-08-07 → gpt-5, claude-opus-4-1-20250805 → claude-opus-4-1
  const dateMatch = model.match(/^(.+)-(\d{4})-?(\d{2})-?(\d{2})$/);
  if (dateMatch) return dateMatch[1];

  // Match patterns like gemini-2.0-flash-001 → gemini-2.0-flash
  const versionMatch = model.match(/^(.+)-(\d{3})$/);
  if (versionMatch) return versionMatch[1];

  return null;
}

// ── Main ───────────────────────────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);
  const applyMode = args.includes("--apply");
  const models = args.filter((a) => !a.startsWith("--"));

  if (models.length === 0) {
    console.error(`
Usage: node scripts/add-model.mjs <provider/model> [provider/model...] [--apply]

Examples:
  node scripts/add-model.mjs openai/gpt-5-2025-08-07          # Dry-run preview
  node scripts/add-model.mjs anthropic/claude-sonnet-4-5 --apply  # Apply to model_pricing.json

Capability mapping (auto-detected from litellm):
  supports_function_calling  → "routing"
  supports_vision            → "image"
  supports_pdf_input         → "pdf"
  supports_web_search        → "search"
  supports_reasoning         → "reasoning"
  supports_computer_use      → "computer-use"
  mode = "image_generation"  → "image-gen"

Not auto-detected: "mcp", "agentic", "file-search", "code-interpreter"
    `);
    process.exit(1);
  }

  // Fetch data sources
  const [litellm, orData] = await Promise.all([
    fetchWithCache(LITELLM_URL, LITELLM_CACHE),
    fetchWithCache(OPENROUTER_URL, OPENROUTER_CACHE),
  ]);
  const orModels = orData.data || [];

  // Load current model_pricing.json
  const pricing = JSON.parse(readFileSync(MODEL_PRICING_PATH, "utf8"));

  const results = [];

  for (const input of models) {
    const parts = input.split("/");
    let provider, model;
    if (parts.length >= 2) {
      provider = parts[0];
      model = parts.slice(1).join("/");
    } else {
      model = parts[0];
      provider = null;
    }

    console.error(`\n${"─".repeat(60)}`);
    console.error(`Processing: ${input}`);

    // Find in litellm
    const llmResult = findInLitellm(litellm, provider, model);
    if (!llmResult) {
      console.error(`  ✗ NOT FOUND in litellm. Skipping.`);
      console.error(
        `    Try: https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json`
      );
      continue;
    }

    const llm = llmResult.data;
    console.error(`  ✓ Found in litellm as "${llmResult.key}"`);

    // Resolve provider
    const llmProvider = llm.litellm_provider || "";
    const ourProvider =
      PROVIDER_MAP[provider] ||
      PROVIDER_MAP[llmProvider] ||
      provider ||
      llmProvider;

    if (!ourProvider) {
      console.error(`  ✗ Cannot determine provider. Skipping.`);
      continue;
    }

    if (!pricing[ourProvider]) {
      console.error(
        `  ✗ Provider "${ourProvider}" not in model_pricing.json. Add it first.`
      );
      continue;
    }

    // Check if model already exists
    if (pricing[ourProvider].models?.includes(model)) {
      console.error(`  ⚠ Already exists in model_pricing.json. Skipping.`);
      continue;
    }

    // Extract pricing
    const inputPrice =
      Math.round(llm.input_cost_per_token * 1_000_000 * 100) / 100;
    const outputPrice =
      Math.round(llm.output_cost_per_token * 1_000_000 * 100) / 100;
    console.error(`  Price: $${inputPrice}/M input, $${outputPrice}/M output`);

    // Derive capabilities
    const caps = deriveCapabilities(llm);
    console.error(`  Capabilities: [${caps.join(", ")}]`);

    // Find OpenRouter ID
    const orId = findOpenRouterId(orModels, ourProvider, model);
    console.error(`  OpenRouter ID: ${orId || "(not found)"}`);

    // Detect alias
    const alias = detectAlias(model);
    if (alias) console.error(`  Alias: ${model} → ${alias}`);

    const entry = {
      provider: ourProvider,
      model,
      capabilities: caps,
      openrouter_identifier: orId,
      price: { input: inputPrice, output: outputPrice },
      alias: alias || undefined,
    };

    results.push(entry);

    // Apply to pricing object
    if (applyMode) {
      const p = pricing[ourProvider];
      p.models.push(model);
      if (caps.length) {
        if (!p.capabilities) p.capabilities = {};
        p.capabilities[model] = caps;
      }
      if (orId) {
        if (!p.openrouter_identifier) p.openrouter_identifier = {};
        p.openrouter_identifier[model] = orId;
      }
      p.price[model] = entry.price;
      if (alias) {
        if (!p.alias) p.alias = {};
        p.alias[model] = alias;
      }
      console.error(`  ✓ Added to model_pricing.json (in memory)`);
    }
  }

  // Output
  if (results.length === 0) {
    console.error("\nNo models to add.");
    process.exit(0);
  }

  if (applyMode) {
    writeFileSync(MODEL_PRICING_PATH, JSON.stringify(pricing, null, 4) + "\n");
    console.error(`\n✓ Wrote ${results.length} model(s) to model_pricing.json`);
  } else {
    // Dry-run: output JSON snippet
    console.error(`\n${"─".repeat(60)}`);
    console.error("DRY RUN — JSON snippet for each model:\n");
    console.log(JSON.stringify(results, null, 2));
    console.error(
      `\nRe-run with --apply to write to model_pricing.json`
    );
  }
}

main().catch((e) => {
  console.error("Fatal:", e.message);
  process.exit(1);
});

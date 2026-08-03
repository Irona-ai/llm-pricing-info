# Model capabilities reference

Each model entry in `model_pricing.json` has a `capabilities` array. These strings drive IronLabs product behavior (chat app filters, routing eligibility, Foundry modes, sync to Supabase). They are **not** all native provider API flags — some are IronLabs-specific markers.

When adding a model, pick capabilities from this list. Do not infer from LiteLLM alone; check a similar model in the same tier or ask in PR review.

---

## IronLabs platform markers

These describe **IronLabs product support**, not something the upstream provider documents on its own.

### `routing`

**Not a native model capability.** Marks whether the model is supported by **IronLabs' LLM-Routing algorithm** — eligible for model-select / tradeoff routing when the user picks a candidate set (cost, latency, performance).

Do **not** add `routing` because a model supports function calling or tools. Only add it when the model is wired into IronLabs routing end-to-end.

### `agent-mode`

Marks models that can be used as **Agent Mode** backends in **Foundry** ([chat.ironlabs.ai](https://chat.ironlabs.ai)) — IronLabs' agentic chat product (internal codename: Cottage). These models are vetted for long-horizon agent workflows (sandbox tool use, multi-step tasks) in Foundry, not merely “supports tools” on the provider API.

---

## Provider-native / feature capabilities

These reflect what the model can do on the wire (inputs, outputs, or provider features IronLabs surfaces).

| Capability | Meaning |
| --- | --- |
| `reasoning` | Extended thinking / reasoning effort (chain-of-thought, thinking tokens, or equivalent). Often paired with `reasoning_config.json` for gateway effort mapping. |
| `image` | Accepts image inputs (vision). |
| `video` | Accepts video inputs (multimodal). |
| `pdf` | Accepts PDF document inputs. |
| `search` | Provider or gateway web search / grounding (e.g. OpenAI search, Perplexity, Gemini search). |
| `xSearch` | X (Twitter) search integration exposed in Foundry search modes. |
| `image-gen` | Generates images as output (not just vision input). |
| `video-gen` | Generates video as output. |
| `computer-use` | Computer-use / GUI automation style capability (provider-native). |
| `code-interpreter` | Code interpreter / sandbox execution style capability. |
| `file-search` | File search / retrieval over uploaded or indexed files. |
| `agentic` | Provider-marketed agentic behavior (general tag; distinct from IronLabs `agent-mode` eligibility above). |

---

## Examples

**Muse Spark 1.1** (`meta/muse-spark-1.1`):

```json
"capabilities": [
  "reasoning",
  "image",
  "video",
  "pdf",
  "search",
  "agent-mode"
]
```

- No `routing` — not yet enrolled in IronLabs LLM-Routing; routed via LLM Gateway or OpenRouter (`meta/muse-spark-1.1`).
- `agent-mode` — eligible as a Foundry Agent Mode model.
- `reasoning`, `image`, `video`, `pdf`, `search` — native multimodal + reasoning features from the provider/gateway.

**GPT-5.6 Sol** (typical Pro routing + agent model):

```json
"capabilities": [
  "reasoning",
  "image",
  "search",
  "pdf",
  "agent-mode",
  "routing"
]
```

---

## Related files

| File | Role |
| --- | --- |
| `model_pricing.json` | Source of truth for capabilities, pricing, chat app metadata |
| `reasoning_config.json` | Gateway reasoning effort policy per provider/model (separate from this list) |
| `scripts/sync_models.py` | Syncs capabilities to Supabase for Foundry / Studio |

See [README.md](./README.md) for sync and validation workflow.

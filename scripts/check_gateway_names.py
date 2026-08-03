#!/usr/bin/env python3
"""Check model_pricing.json model keys against the live LLM Gateway catalog.

Every non-deprecated model key must resolve to a real LLM Gateway model id. The
key IS the apiString (see sync_models.py), so a key that does not resolve means
the gateway is being called with a name it does not know -- which is how
`gemini-3-flash` silently drifted from `gemini-3-flash-preview`.

Usage:
    python scripts/check_gateway_names.py

Exits non-zero if any current key fails to resolve, so it can be wired into CI.
"""
import json
import os
import re
import sys
import urllib.request

GATEWAY_MODELS_URL = "https://api.llmgateway.io/v1/models"

# Model keys that are not expected to resolve on LLM Gateway: dated provider
# snapshots and legacy models that were never routed through it. Keeping this
# explicit means a genuinely new mismatch stands out.
LEGACY_PROVIDERS = {
    "cohere", "mistral", "togetherai", "replicate", "bedrock", "kwaivgi", "bytedance",
}
LEGACY_KEY_RE = re.compile(
    r"-\d{4}-\d{2}-\d{2}$"      # gpt-5.2-2025-12-11
    r"|-\d{8}$"                  # claude-3-opus-20240229
    r"|^gpt-(3\.5|4)"            # legacy OpenAI families
    r"|^claude-(2|3)"            # legacy Claude families
    r"|^gemini-(1\.5|2\.0)"      # legacy Gemini families
    r"|-latest$|^o1-"
)

# Known-unresolvable keys that are NOT worth fixing, kept explicit so that a
# genuinely new mismatch still fails the check. Revisit if any of these become
# routable.
KNOWN_UNRESOLVED = {
    # Undated "floating" Claude 4 aliases; the gateway only carries dated ids.
    "anthropic/claude-opus-4-0",
    "anthropic/claude-opus-4-1",
    "anthropic/claude-sonnet-4-0",
    # On neither LLM Gateway nor OpenRouter as of 2026-08-01. The gateway's
    # nearest equivalents are grok-4-1-fast-reasoning / -non-reasoning.
    "xai/grok-4-fast",
}

# Output-only modalities; these route through the image/video services, not the
# text gateway, so their keys are not gateway model ids.
NON_TEXT_CAPS = {"image-gen", "video-gen"}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ironlabs-pricing-check"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def load_pricing():
    with open(os.path.join(REPO_ROOT, "model_pricing.json")) as fh:
        return json.load(fh)


def gateway_ids():
    """Every model id the gateway serves, plus declared aliases."""
    data = json.loads(fetch(GATEWAY_MODELS_URL))["data"]
    ids = set()
    for m in data:
        ids.add(m["id"])
        ids.update(m.get("aliases") or [])
    return ids


def active_models(pricing):
    """Yield (provider, key, capabilities) for non-deprecated text models."""
    for provider, block in pricing.items():
        deprecated = set(block.get("deprecated") or [])
        caps_map = block.get("capabilities") or {}
        for key in block.get("models", []):
            if key in deprecated:
                continue
            caps = caps_map.get(key) or []
            if NON_TEXT_CAPS & set(caps):
                continue
            yield provider, key, caps


def is_legacy(provider, key):
    return provider in LEGACY_PROVIDERS or bool(LEGACY_KEY_RE.search(key))


def check_names(pricing, known):
    print("== apiString validity (model key must be a real LLM Gateway id)")
    unexpected, legacy, waived = [], [], []
    for provider, key, _ in active_models(pricing):
        if key in known:
            continue
        name = f"{provider}/{key}"
        if name in KNOWN_UNRESOLVED:
            waived.append(name)
        elif is_legacy(provider, key):
            legacy.append(name)
        else:
            unexpected.append(name)

    if unexpected:
        print(f"   {len(unexpected)} key(s) do NOT resolve on LLM Gateway:")
        for name in unexpected:
            print(f"     {name}")
    else:
        print("   all current model keys resolve")
    print(f"   ({len(legacy)} legacy/dated skipped, {len(waived)} known-unresolvable waived)")
    return bool(unexpected)


def main():
    pricing = load_pricing()
    known = gateway_ids()
    print(f"LLM Gateway catalog: {len(known)} ids\n")
    return 1 if check_names(pricing, known) else 0


if __name__ == "__main__":
    sys.exit(main())

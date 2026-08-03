#!/usr/bin/env python3
"""Check model_pricing.json against the live LLM Gateway catalog.

Two independent checks, both read-only and unauthenticated:

  1. apiString validity -- every non-deprecated model key must resolve to a real
     LLM Gateway model id. The key IS the apiString (see sync_models.py), so a key
     that does not resolve means the gateway is being called with a name it does
     not know.

  2. llmgw_dp drift -- the set of models tagged `llmgw_dp` must match the DevPass
     directory. DevPass adds and drops models regularly.

Usage:
    python scripts/check_gateway_names.py            # both checks
    python scripts/check_gateway_names.py --devpass  # DevPass drift only
    python scripts/check_gateway_names.py --names    # apiString check only

Exits non-zero if either check reports drift, so it can be wired into CI.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

GATEWAY_MODELS_URL = "https://api.llmgateway.io/v1/models"
DEVPASS_MODELS_URL = "https://devpass.llmgateway.io/models?page={page}"
DEVPASS_PAGES = 16  # generous upper bound; pagination stops early when a page repeats

DEVPASS_CAPABILITY = "llmgw_dp"

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

# Output-only modalities: not text completion, so DevPass never carries them.
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


def devpass_ids():
    """Scrape the DevPass directory. Each rendered row links to the gateway model
    page, so the ids come straight out of the anchor hrefs."""
    seen = []
    previous_page = None
    for page in range(1, DEVPASS_PAGES + 1):
        html = fetch(DEVPASS_MODELS_URL.format(page=page))
        found = re.findall(r"https://llmgateway\.io/models/([^/\"\\]+)/[^\"\\?]+", html)
        if not found or found == previous_page:
            break  # past the last page; the site repeats the final page
        previous_page = found
        for model_id in found:
            if model_id not in seen:
                seen.append(model_id)
    return set(seen)


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


def check_devpass(pricing, on_devpass, known):
    print(f"== {DEVPASS_CAPABILITY} drift (tag must match the DevPass directory)")
    tagged, should_be = set(), set()
    for provider, key, caps in active_models(pricing):
        name = f"{provider}/{key}"
        if DEVPASS_CAPABILITY in caps:
            tagged.add(name)
        if key in on_devpass:
            should_be.add(name)

    missing = sorted(should_be - tagged)
    stale = sorted(tagged - should_be)

    if missing:
        print(f"   {len(missing)} on DevPass but NOT tagged -- add '{DEVPASS_CAPABILITY}':")
        for name in missing:
            print(f"     {name}")
    if stale:
        print(f"   {len(stale)} tagged but NOT on DevPass -- remove '{DEVPASS_CAPABILITY}':")
        for name in stale:
            print(f"     {name}")
    if not missing and not stale:
        print(f"   no drift ({len(tagged)} models tagged)")

    # Informational: gateway-available but DevPass-excluded is the interesting gap.
    excluded = sorted(
        f"{p}/{k}" for p, k, _ in active_models(pricing)
        if k in known and k not in on_devpass and not is_legacy(p, k)
    )
    print(f"   on LLM Gateway but excluded from DevPass ({len(excluded)}):")
    for name in excluded:
        print(f"     {name}")
    return bool(missing or stale)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--names", action="store_true", help="only the apiString check")
    ap.add_argument("--devpass", action="store_true", help="only the DevPass drift check")
    args = ap.parse_args()
    run_names = args.names or not args.devpass
    run_devpass = args.devpass or not args.names

    pricing = load_pricing()
    known = gateway_ids()
    print(f"LLM Gateway catalog: {len(known)} ids\n")

    drift = False
    if run_names:
        drift |= check_names(pricing, known)
        print()
    if run_devpass:
        on_devpass = devpass_ids()
        print(f"DevPass directory: {len(on_devpass)} models")
        drift |= check_devpass(pricing, on_devpass, known)

    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())

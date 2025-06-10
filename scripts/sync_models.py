#!/usr/bin/env python3
import os
import argparse
import json
import re
import logging
from datetime import datetime, date
from supabase import create_client, Client

# ------------------------------------------------------------------
# Configure logging
# ------------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
ch.setFormatter(formatter)
logger.addHandler(ch)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Sync models.json into Supabase with optional filtering"
    )
    parser.add_argument(
        "--json", required=True,
        help="Path to your models.json file"
    )
    parser.add_argument(
        "--env", required=True, choices=["staging", "production"],
        help="Environment to sync against"
    )
    parser.add_argument(
        "--models", nargs="*",
        help=(
            "Optional list of models to sync, in 'provider/model' format. "
            "If omitted, all models are synced."
        )
    )
    return parser.parse_args()


def extract_date(model_name: str):
    """
    Extract the first YYYY-MM-DD substring from a model name.
    Returns a datetime or None.
    """
    m = re.search(r"(\d{4}-\d{2}-\d{2})", model_name)
    if m:
        try:
            return datetime.fromisoformat(m.group(1))
        except ValueError:
            logger.warning(f"Unexpected date format in '{model_name}'")
    return None


def upsert_provider(sb: Client, provider_name: str):
    """
    Upsert a Provider row, returning its Supabase ID.
    """
    endpoint = os.getenv(f"{provider_name.upper()}_API_ENDPOINT", "")
    data = {"name": provider_name, "apiEndpoint": endpoint}
    try:
        res = sb.table("Provider").upsert(data, on_conflict="name").execute()
        provider_id = res.data[0]["id"]
        logger.info(f"Provider '{provider_name}' upserted (ID: {provider_id})")
        return provider_id
    except Exception as e:
        logger.error(f"Failed to upsert provider '{provider_name}': {e}")
        return None


def sync_model(sb: Client, provider_id: str, key: str, cfg: dict, models_filter: list):
    """
    Sync a single model record, inserting or updating only on diff.
    """
    full_name = f"{cfg['provider']}/{key}"

    # Apply model filter if provided
    if models_filter and full_name not in models_filter:
        logger.info(f"Skipping {full_name} (not in filter)")
        return

    # Ensure price info exists
    price_info = cfg.get("price", {}).get(key)
    if not price_info:
        logger.warning(f"No price info for '{full_name}', skipping")
        return

    # Determine depreciation
    dep_str = cfg.get("depreciationDate", {}).get(key)
    is_archived = False
    if dep_str:
        try:
            dep_date = datetime.fromisoformat(dep_str).date()
            if dep_date <= date.today():
                is_archived = True
        except ValueError:
            logger.warning(f"Invalid depreciationDate for '{full_name}': {dep_str}")

    # Build desired record
    rec = {
        "apiString": key,
        "providerId": provider_id,
        "name": cfg.get("name", {}).get(key, key),
        "costPerMillionTokenInput": price_info.get("input"),
        "costPerMillionTokenOutput": price_info.get("output"),
        "capabilities": cfg.get("support_media_inputs", {}).get(key, []),
        "availableForChatApp": (
            cfg.get("availableForChatApp", {}).get(key)
            if cfg.get("availableForChatApp", {}).get(key) in ["Free", "Pro"]
            else None
        ),
        "description": cfg.get("description", {}).get(key, ""),
        "releaseDate": extract_date(key),
        "isArchived": is_archived,
    }

    try:
        existing = (
            sb.table("Model")
              .select("*")
              .eq("apiString", rec["apiString"])
              .maybe_single()
              .execute()
        ).data
    except Exception as e:
        logger.error(f"Failed to fetch existing for '{full_name}': {e}")
        return

    if existing is None:
        # Insert
        try:
            sb.table("Model").insert(rec).execute()
            logger.info(f"Created model '{full_name}'")
        except Exception as e:
            logger.error(f"Failed to create '{full_name}': {e}")
        return

    # Compute updates
    updates = {}
    for field, desired in rec.items():
        if existing.get(field) != desired:
            updates[field] = desired

    if updates:
        try:
            sb.table("Model").update(updates).eq("apiString", rec["apiString"]).execute()
            logger.info(f"Updated '{full_name}': {list(updates.keys())}")
        except Exception as e:
            logger.error(f"Failed to update '{full_name}': {e}")
    else:
        logger.info(f"No changes for '{full_name}'")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    args = parse_args()
    # Build filter list
    models_filter = args.models if args.models else []

    # Initialize Supabase client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        return
    sb = create_client(url, key)

    # Load JSON with BOM support
    try:
        with open(args.json, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read JSON file '{args.json}': {e}")
        return

    # Sync each provider
    for provider, cfg in data.items():
        pid = upsert_provider(sb, provider)
        if not pid:
            logger.error(f"Skipping provider '{provider}' due to earlier error")
            continue

        # Attach provider name for full_name
        cfg['provider'] = provider
        for model_key in cfg.get('models', []):
            sync_model(sb, pid, model_key, cfg, models_filter)

    logger.info(f"[{args.env}] sync complete.")

if __name__ == "__main__":
    main()

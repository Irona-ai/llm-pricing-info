#!/usr/bin/env python3
import os
import argparse
import json
import re
import logging
from datetime import datetime, date
from supabase import create_client, Client
import uuid
import json  # For logging payload

# ------------------------------------------------------------------
# Configure logging
# ------------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)
logger.addHandler(ch)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sync models.json into Supabase with optional filtering"
    )
    parser.add_argument("--json", required=True, help="Path to your models.json file")
    parser.add_argument(
        "--env",
        required=True,
        choices=["staging", "production"],
        help="Environment to sync against",
    )
    parser.add_argument(
        "--models",
        nargs="*",
        help=(
            "Optional list of models to sync, in 'provider/model' format. "
            "If omitted, all models are synced."
        ),
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


def upsert_provider(sb: Client, provider_name: str, cfg: dict):
    """
    Ensure a Provider exists; update icon/apiEndpoint if needed; return its ID.
    """
    # Fetch existing provider
    try:
        res = (
            sb.table("Provider")
            .select("id, apiEndpoint, icon")
            .eq("name", provider_name)
            .maybe_single()
            .execute()
        )
        existing = res.data if res and hasattr(res, "data") else None
    except Exception as e:
        logger.error(f"Error querying Provider '{provider_name}': {e}")
        existing = None

    icon_url = cfg.get("icon", "")
    endpoint = os.getenv(f"{provider_name.upper()}_API_ENDPOINT", "")

    if existing:
        provider_id = existing.get("id")
        updates = {}
        # Only update if values differ
        if endpoint and existing.get("apiEndpoint") != endpoint:
            updates["apiEndpoint"] = endpoint
        if icon_url and existing.get("icon") != icon_url:
            updates["icon"] = icon_url
        if updates:
            try:
                (sb.table("Provider").update(updates).eq("id", provider_id).execute())
                logger.info(
                    f"Updated Provider '{provider_name}': {list(updates.keys())}"
                )
            except Exception as e:
                logger.error(f"Failed to update Provider '{provider_name}': {e}")
        else:
            logger.info(f"No change for Provider '{provider_name}'")
        return provider_id

    # Insert new provider
    new_id = str(uuid.uuid4())
    data = {"id": new_id, "name": provider_name, "apiEndpoint": endpoint, "icon": icon_url}
    try:
        sb.table("Provider").insert(data).execute()
        logger.info(f"Created Provider '{provider_name}' (ID: {new_id})")
        return new_id
    except Exception as e:
        logger.error(f"Failed to create Provider '{provider_name}': {e}")
        return None


def sync_model(sb: Client, provider_id: str, key: str, cfg: dict, models_filter: list):
    """
    Sync a single model record, inserting or updating only on diff.
    """
    full_name = f"{cfg['provider']}/{key}"

    # Filter models if specified
    if models_filter and full_name not in models_filter:
        logger.info(f"Skipping {full_name} (not in filter)")
        return

    # Ensure price info exists
    price_info = cfg.get("price", {}).get(key)
    if not price_info:
        logger.warning(f"No price info for '{full_name}', skipping")
        return

    # Deprecation archive flag
    dep_str = cfg.get("depreciationDate", {}).get(key)
    is_archived = False
    if dep_str:
        try:
            dep_date = datetime.fromisoformat(dep_str).date()
            if dep_date <= date.today():
                is_archived = True
        except ValueError:
            logger.warning(f"Invalid depreciationDate for '{full_name}': {dep_str}")

    # Build record
    date_val = extract_date(key)
    model_id = str(uuid.uuid4())  # Generate ID client-side
    current_time_iso = datetime.utcnow().isoformat()
    rec = {
        "id": model_id,  # Ensure ID is always new for this initial build
        "apiString": key,
        "providerId": provider_id,
        "createdAt": current_time_iso,  # Add createdAt for new records
        "updatedAt": current_time_iso,  # Add updatedAt for new records
        "name": cfg.get("name", {}).get(key, key),
        "costPerMillionTokenInput": price_info["input"],
        "costPerMillionTokenOutput": price_info["output"],
        "capabilities": cfg.get("capabilities", {}).get(key, []),
        "availableForChatApp": (
            cfg.get("availableForChatApp", {}).get(key)
            if cfg.get("availableForChatApp", {}).get(key) in ["Free", "Pro"]
            else None
        ),
        "description": cfg.get("descriptions", {}).get(
            key, ""
        ),  # Ensure plural 'descriptions'
        "releaseDate": date_val.isoformat() if date_val else None,
        "isArchived": is_archived,
    }

    # Fetch existing model
    try:
        resp = (
            sb.table("Model")
            .select("*")
            .eq("apiString", rec["apiString"])
            .maybe_single()
            .execute()
        )
        existing = resp.data if resp and hasattr(resp, "data") else None
        if resp and getattr(resp, "error", None):
            logger.error(f"Error fetching model '{full_name}': {resp.error}")
            existing = None
    except Exception as e:
        logger.error(f"Failed to fetch model '{full_name}': {e}")
        existing = None

    if existing:
        logger.info(f"Found existing model '{full_name}'")
        logger.info(f"Existing model: {existing}")
        excluded_from_comparison = ["id", "createdAt", "updatedAt"]

        # Compare fields, treating capabilities as a set so order doesn't matter
        updates = {}
        for field, val in rec.items():
            if field in excluded_from_comparison:
                continue
            existing_val = existing.get(field)

            if field == "capabilities":
                existing_set = set(existing_val or [])
                new_set = set(val or [])
                if existing_set != new_set:
                    updates[field] = val
            else:
                if existing_val != val:
                    updates[field] = val

        if updates:
            updates["updatedAt"] = datetime.utcnow().isoformat() # Add/update updatedAt timestamp
            try:
                logger.info(f"Updating model with details: {updates}")
                logger.info(f"Updating model with API string: {rec['apiString']}")
                (
                    sb.table("Model")
                    .update(updates)
                    .eq("apiString", rec["apiString"])
                    .execute()
                )
                logger.info(f"CHANGES applied to model '{full_name}': {list(updates.keys())}")
            except Exception as e:
                logger.error(f"Failed to update model '{full_name}': {e}")
        else:
            logger.info(f"NO CHANGES for model '{full_name}'")
    else:
        # Create new model
        try:
            logger.info(f"Creating new model '{full_name}'")
            sb.table("Model").insert(rec).execute()
            logger.info(f"Successfully created model '{full_name}'")
        except Exception as e:
            logger.error(f"Failed to create model '{full_name}': {e}")


# Module-level helper: archive models removed from JSON
def archive_removed_entities(sb: Client, models_by_provider: dict):
    """
    Archive models when a provider or model is missing from the JSON.
    models_by_provider: { provider_name: {"id": provider_id, "models_set": set(model_keys)} }
    """
    now_iso = datetime.utcnow().isoformat()

    # Fetch all providers in DB
    try:
        res = sb.table("Provider").select("id,name").execute()
        db_providers = res.data or []
    except Exception as e:
        logger.error(f"Failed to fetch providers for archival: {e}")
        return

    db_name_to_id = {p.get("name"): p.get("id") for p in db_providers}
    json_provider_names = set(models_by_provider.keys())

    # 1) Providers missing from JSON: archive all their models
    missing_providers = set(db_name_to_id.keys()) - json_provider_names
    for provider_name in missing_providers:
        pid = db_name_to_id.get(provider_name)
        try:
            resp = (
                sb.table("Model")
                .select("id,isArchived")
                .eq("providerId", pid)
                .execute()
            )
            models = resp.data or []
        except Exception as e:
            logger.error(f"Failed fetching models for provider '{provider_name}' to archive: {e}")
            continue

        to_archive = [m for m in models if not (m.get("isArchived") is True)]
        for m in to_archive:
            try:
                (
                    sb.table("Model")
                    .update({"isArchived": True, "updatedAt": now_iso})
                    .eq("id", m.get("id"))
                    .execute()
                )
            except Exception as e:
                logger.error(f"Failed to archive model id={m.get('id')} for provider '{provider_name}': {e}")
        if to_archive:
            logger.info(f"Archived {len(to_archive)} models for removed provider '{provider_name}'")

    # 2) Providers present in JSON: archive models missing from their model list
    for provider_name, info in models_by_provider.items():
        pid = info.get("id")
        valid_models = set(info.get("models_set", set()))
        try:
            resp = (
                sb.table("Model")
                .select("id,apiString,isArchived")
                .eq("providerId", pid)
                .execute()
            )
            models = resp.data or []
        except Exception as e:
            logger.error(f"Failed fetching models for provider '{provider_name}' to diff: {e}")
            continue

        to_archive = [
            m for m in models
            if (m.get("apiString") not in valid_models) and not (m.get("isArchived") is True)
        ]
        for m in to_archive:
            try:
                (
                    sb.table("Model")
                    .update({"isArchived": True, "updatedAt": now_iso})
                    .eq("id", m.get("id"))
                    .execute()
                )
            except Exception as e:
                logger.error(f"Failed to archive removed model '{m.get('apiString')}' for provider '{provider_name}': {e}")
        if to_archive:
            logger.info(f"Archived {len(to_archive)} removed models for provider '{provider_name}'")


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

    try:
        with open(args.json, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read JSON file '{args.json}': {e}")
        return

    for provider, cfg in data.items():
        pid = upsert_provider(sb, provider, cfg)
        if not pid:
            logger.error(f"Skipping provider '{provider}' due to earlier error")
            continue

        cfg["provider"] = provider
        for model_key in cfg.get("models", []):
            sync_model(sb, pid, model_key, cfg, models_filter)

    logger.info(f"[{args.env}] sync complete.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import os
import argparse
import json
import re
from datetime import datetime, date
from supabase import create_client, Client

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--json", required=True, help="Path to your models.json")
    p.add_argument("--env", required=True, choices=["staging","production"])
    return p.parse_args()

def extract_date(model_name: str):
    # extract first YYYY-MM-DD in the model name
    m = re.search(r"(\d{4}-\d{2}-\d{2})", model_name)
    if m:
        try:
            return datetime.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None

def upsert_provider(sb: Client, provider_name: str):
    # upsert provider row by name; use env var for apiEndpoint if set
    endpoint = os.getenv(f"{provider_name.upper()}_API_ENDPOINT", "")
    data = {"name": provider_name, "apiEndpoint": endpoint}
    res = sb.table("Provider").upsert(data, on_conflict="name").execute()
    return res.data[0]["id"]

def sync_model(sb: Client, provider_id: str, key: str, cfg: dict):
    # determine if this model should be archived based on depreciationDate
    dep_str = cfg.get("depreciationDate", {}).get(key)
    is_archived = False
    if dep_str:
        dep_date = datetime.fromisoformat(dep_str).date()
        if dep_date <= date.today():
            is_archived = True

    # build the record we want
    rec = {
        "apiString": key,
        "providerId": provider_id,
        "name": cfg.get("name", {}).get(key, key),

        # pricing
        "costPerMillionTokenInput":  cfg["price"][key]["input"],
        "costPerMillionTokenOutput": cfg["price"][key]["output"],

        # multimedia & PDF support
        "capabilities": cfg.get("support_media_inputs", {}).get(key, []),

        # chat-app tier
        "availableForChatApp": cfg.get("availableForChatApp", {}).get(key),

        # optional description
        "description": cfg.get("description", {}).get(key, ""),

        # optional release date
        "releaseDate": extract_date(key),

        # depreciation logic
        "isArchived": is_archived,
    }

    # fetch existing row by apiString
    existing = (
        sb.table("Model")
          .select("*")
          .eq("apiString", rec["apiString"])
          .maybe_single()
          .execute()
    ).data

    if existing is None:
        sb.table("Model").insert(rec).execute()
        print(f"↗️ created {rec['apiString']}")
        return

    # compute diffs and only update changed fields
    updates = {}
    for field, desired in rec.items():
        if existing.get(field) != desired:
            updates[field] = desired

    if updates:
        sb.table("Model").update(updates).eq("apiString", rec["apiString"]).execute()
        print(f"🔄 updated {rec['apiString']}: {list(updates.keys())}")
    else:
        print(f"✔️ no change for {rec['apiString']}")

def main():
    args = parse_args()
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    sb = create_client(url, key)

    with open(args.json) as f:
        data = json.load(f)

    for provider, cfg in data.items():
        pid = upsert_provider(sb, provider)
        for model_key in cfg["models"]:
            sync_model(sb, pid, model_key, cfg)

    print(f"[{args.env}] sync complete.")

if __name__ == "__main__":
    main()

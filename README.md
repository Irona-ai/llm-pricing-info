# LLM Pricing Info

A comprehensive repository containing pricing information for various Large Language Model (LLM) providers and their models. This data is automatically synced to Supabase databases for both staging and production environments.

## Overview

This repository maintains a centralized `model_pricing.json` file that contains:
- LLM provider information (OpenAI, Anthropic, etc.)
- Model names and versions
- API key requirements
- Supported media input types
- Pricing details

## Manual Workflow Trigger

To manually trigger the sync-models workflow, use the GitHub CLI:

```bash
gh workflow run sync-models.yml \
  --ref development \
  -f env=staging
```

You can also trigger for production:

```bash
gh workflow run sync-models.yml \
  --ref main \
  -f env=production
```

## Automatic Syncing

The repository automatically syncs model data to Supabase when:
- Changes are pushed to `development` branch (syncs to staging)
- Changes are pushed to `main` branch (syncs to production)
- Manual workflow dispatch is triggered

## JSON Structure Validation

To validate the JSON structure locally:

```bash
python -m json.tool model_pricing.json
```

## Project Structure

├── .github/workflows/
│   └── sync-models.yml          # GitHub Actions workflow
├── scripts/
│   ├── sync_models.py           # Python script to sync data to Supabase
│   └── schema.prisma            # Database schema
├── model_pricing.json           # Main data file with LLM pricing info
└── README.md                    # This file



## Development

### Prerequisites
- Python 3.10+
- Supabase account and project
- GitHub CLI (for manual workflow triggers)

### Environment Variables
The sync script requires these Supabase credentials:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

### Adding New Models
1. Update `model_pricing.json` with new provider/model information
2. Validate JSON structure: `python -m json.tool model_pricing.json`
3. Commit and push to `development` branch for staging sync
4. Merge to `main` branch for production sync

## Workflow Details

The `sync-models.yml` workflow:
- Validates JSON structure
- Installs Python dependencies
- Runs the sync script to update Supabase tables
- Supports both staging and production environments
- Can be triggered manually with environment selection
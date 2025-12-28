
name: Sync Garmin to Notion

on:
  schedule:
    - cron: '*/15 * * * *'   # every 15 minutes (UTC; job uses Europe/London for app logic)
  workflow_dispatch:

env:
  TZ: 'Europe/London'

concurrency:
  group: sync-garmin-to-notion
  cancel-in-progress: true

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Cache pip packages
        uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('**/requirements.txt') }}
          restore-keys: |
            ${{ runner.os }}-pip-

      - name: Create and activate venv
        run: |
          python -m venv .venv
          source .venv/bin/activate
          python -m pip install --upgrade pip setuptools wheel
          pip install -r requirements.txt

      - name: Validate required secrets
        run: |
          : "${{ secrets.GARMIN_EMAIL }}" || (echo "Missing GARMIN_EMAIL" && exit 1)
          : "${{ secrets.GARMIN_PASSWORD }}" || (echo "Missing GARMIN_PASSWORD" && exit 1)
          : "${{ secrets.NOTION_TOKEN }}" || (echo "Missing NOTION_TOKEN" && exit 1)
          : "${{ secrets.NOTION_SLEEP_DB_ID }}" || echo "WARN: Sleep DB not set"
          : "${{ secrets.NOTION_HEALTH_DB_ID }}" || echo "WARN: Health DB not set"
          : "${{ secrets.NOTION_DB_ID }}" || echo "INFO: Activities DB set"
          : "${{ secrets.NOTION_PR_DB_ID }}" || echo "INFO: PR DB set"
          : "${{ secrets.NOTION_STEPS_DB_ID }}" || echo "INFO: Steps DB set"

      - name: Run sync scripts
        env:
          GARMIN_EMAIL: ${{ secrets.GARMIN_EMAIL }}
          GARMIN_PASSWORD: ${{ secrets.GARMIN_PASSWORD }}
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
          NOTION_DB_ID: ${{ secrets.NOTION_DB_ID }}
          NOTION_PR_DB_ID: ${{ secrets.NOTION_PR_DB_ID }}
          NOTION_STEPS_DB_ID: ${{ secrets.NOTION_STEPS_DB_ID }}
          NOTION_SLEEP_DB_ID: ${{ secrets.NOTION_SLEEP_DB_ID }}
          NOTION_HEALTH_DB_ID: ${{ secrets.NOTION_HEALTH_DB_ID }}  # <- set to 2d70e6d78949819d923ddaa4d8a6c202
          TZ: 'Europe/London'
        run: |
          set -euo pipefail
          source .venv/bin/activate
          python garmin-activities.py
          python personal-records.py
          python daily-steps.py
          python sleep_data.py
          python health_metrics_all.py

      - name: Upload logs (optional)
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: sync-logs
          path: logs/

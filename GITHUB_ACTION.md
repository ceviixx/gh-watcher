# GitHub Action Setup Guide

This guide explains how to use the GitHub Release Monitor as a GitHub Action.

## Quick Start

### 1. Setup in Your Repository

1. Create a new file `.github/workflows/release-monitor.yml`:

```yaml
name: Monitor Releases

on:
  schedule:
    - cron: '*/10 * * * *'  # Every 10 minutes
  workflow_dispatch:  # Allow manual trigger

jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      
      - name: Restore state
        uses: actions/cache@v4
        with:
          path: ./state
          key: release-state-${{ github.run_id }}
          restore-keys: release-state-
      
      - name: Monitor releases
        uses: ceviixx/gh-watcher@main
        with:
          repos: 'cli/cli,python/cpython'
          discord_webhook_url: ${{ secrets.DISCORD_WEBHOOK_URL }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          skip_existing_on_init: 'true'
      
      - name: Save state
        if: always()
        uses: actions/cache/save@v4
        with:
          path: ./state
          key: release-state-${{ github.run_id }}
```

2. Add your Discord webhook as a secret:
   - Go to Settings → Secrets and variables → Actions
   - New repository secret → Name: `DISCORD_WEBHOOK_URL`
   - Paste your webhook URL

3. Commit and push the workflow file

4. Enable Actions if not already enabled

## Configuration Options

### Inputs

All inputs for the action (use in `with:` section):

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `repos` | ✅ Yes | - | Comma-separated list of repos (e.g., `owner/repo,owner2/repo2`) |
| `discord_webhook_url` | ✅ Yes | - | Discord webhook URL for notifications |
| `github_token` | No | `${{ github.token }}` | GitHub token (increases rate limits) |
| `state_dir` | No | `./state` | Directory for state files |
| `poll_interval` | No | `0` | Seconds between checks (0 = run once, recommended) |
| `only_latest` | No | `false` | Only check the latest release |
| `include_prerelease` | No | `true` | Include prereleases |
| `include_draft` | No | `false` | Include draft releases |
| `skip_existing_on_init` | No | `true` | Skip existing releases on first run |
| `notify_on` | No | `new_release,dl_increase,new_asset` | Events to notify on |
| `asset_name_include` | No | `''` | Regex filter for asset names (include only matching) |
| `asset_name_exclude` | No | `''` | Regex filter for asset names (exclude matching) |
| `use_embeds` | No | `true` | Use Discord embeds |
| `discord_username` | No | `GH Release Bot` | Bot username in Discord |
| `discord_avatar_url` | No | `''` | Bot avatar URL |
| `log_level` | No | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Examples

### Monitor Your Own Releases

```yaml
name: Monitor My Releases

on:
  schedule:
    - cron: '0 * * * *'  # Every hour
  workflow_dispatch:

jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - uses: ceviixx/gh-watcher@main
        with:
          repos: '${{ github.repository }}'
          discord_webhook_url: ${{ secrets.DISCORD_WEBHOOK_URL }}
          notify_on: 'new_release'
          skip_existing_on_init: 'true'
```

### Monitor Multiple Repos with Filtering

```yaml
- uses: ceviixx/gh-watcher@main
  with:
    repos: 'cli/cli,golang/go,python/cpython'
    discord_webhook_url: ${{ secrets.DISCORD_WEBHOOK_URL }}
    github_token: ${{ secrets.GITHUB_TOKEN }}
    only_latest: 'false'
    include_prerelease: 'true'
    notify_on: 'new_release,dl_increase'
    asset_name_exclude: 'sha256|\.sig$'
    use_embeds: 'true'
```

### Monitor with Download Tracking

```yaml
- uses: ceviixx/gh-watcher@main
  with:
    repos: 'microsoft/vscode,docker/compose'
    discord_webhook_url: ${{ secrets.DISCORD_WEBHOOK_URL }}
    notify_on: 'new_release,new_asset,dl_increase'
    skip_existing_on_init: 'false'  # Report all on first run
```

## Schedule Options

Common cron expressions:

```yaml
# Every 5 minutes (max frequency recommended)
- cron: '*/5 * * * *'

# Every 15 minutes
- cron: '*/15 * * * *'

# Every hour
- cron: '0 * * * *'

# Every 6 hours
- cron: '0 */6 * * *'

# Once per day at 9 AM UTC
- cron: '0 9 * * *'

# Every Monday at 10 AM UTC
- cron: '0 10 * * 1'
```

## State Persistence

The action uses a cache to persist state between runs. This prevents duplicate notifications.

**Important:** Always include the cache steps in your workflow:

```yaml
- name: Restore state
  uses: actions/cache@v4
  with:
    path: ./state
    key: release-state-${{ github.run_id }}
    restore-keys: release-state-

# ... your monitor step ...

- name: Save state
  if: always()  # Save even if the monitor step fails
  uses: actions/cache/save@v4
  with:
    path: ./state
    key: release-state-${{ github.run_id }}
```

## Troubleshooting

### No notifications received

1. Check the Actions logs for errors
2. Verify `DISCORD_WEBHOOK_URL` is set correctly
3. Set `log_level: 'DEBUG'` for detailed output
4. Check if `skip_existing_on_init: 'true'` is preventing initial notifications

### Rate limit errors

1. Add a `GITHUB_TOKEN` secret with a Personal Access Token
2. Increase the schedule interval (run less frequently)
3. Use `only_latest: 'true'` to reduce API calls

### State not persisting

1. Make sure both cache steps are present
2. Check if cache is being restored in Actions logs
3. Verify the `state_dir` path matches in both steps

### Testing

Run the workflow manually:
1. Go to Actions tab
2. Select your workflow
3. Click "Run workflow"
4. Check the logs for output

## Rate Limits

| Token Type | Requests/Hour |
|------------|---------------|
| No token | 60 |
| `github.token` | 1000 |
| Personal Access Token | 5000 |

**Tip:** For most use cases, the default `github.token` is sufficient.

## Best Practices

1. **Start with `skip_existing_on_init: 'true'`** to avoid spam on first run
2. **Use reasonable intervals** (5-15 minutes) to avoid rate limits
3. **Filter assets** if you only care about specific file types
4. **Monitor selectively** - only add repos you actually want notifications for
5. **Test first** using `workflow_dispatch` before enabling scheduled runs

## Discord Webhook Setup

1. Open Discord and go to Server Settings → Integrations
2. Click "Create Webhook" or "View Webhooks"
3. Click "New Webhook"
4. Give it a name (e.g., "Release Monitor")
5. Select the channel for notifications
6. Copy the webhook URL
7. Add it as a secret in your GitHub repository

## Advanced: Self-Hosted Setup

If you're using this repository directly (not as an action):

1. Fork this repository
2. Add secrets and variables (as described above)
3. The included workflow `.github/workflows/monitor.yml` will run automatically
4. Customize the schedule in the workflow file if needed

## Support

For issues or questions:
- Open an issue on GitHub
- Check existing issues for solutions
- Review the main README.md for additional documentation

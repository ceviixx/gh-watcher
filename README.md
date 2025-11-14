# GitHub Release Discord Bot

A Python bot that monitors GitHub repositories for new releases and sends notifications via Discord webhook.

## Features

- 🔔 Notifications for new releases
- 📦 Tracking of new assets
- 📈 Download counters for release assets
- 🎯 Flexible filtering (prerelease, draft, asset names)
- 🔄 Automatic polling at configurable intervals
- 🐳 Docker support with persistent state
- ⚡ GitHub Actions support (no infrastructure needed)

## Prerequisites

### GitHub Actions
- GitHub account
- Discord webhook URL

### Local Execution (without Docker)
- Python 3.12 or higher
- pip (Python Package Manager)

### Docker Execution
- Docker
- Docker Compose

## Installation & Execution

### Option 1: GitHub Actions (Recommended ⭐)

Run the bot directly in GitHub Actions - no server or Docker needed!

#### Setup for your own repositories:

1. **Fork or clone this repository**
   
2. **Configure Repository Secrets**
   
   Go to your repository → Settings → Secrets and variables → Actions → New repository secret:
   
   - `DISCORD_WEBHOOK_URL`: Your Discord webhook URL (required)
   - `GH_RELEASE_TOKEN`: Optional GitHub token for higher rate limits

3. **Configure Repository Variables**
   
   Go to Settings → Secrets and variables → Actions → Variables tab:
   
   - `REPOS`: Comma-separated list of repos to monitor (e.g., `cli/cli,python/cpython`)
   - Optional: `ONLY_LATEST`, `INCLUDE_PRERELEASE`, `NOTIFY_ON`, etc. (see Configuration section)

4. **Enable the workflow**
   
   The workflow runs automatically every 5 minutes. You can also trigger it manually:
   - Go to Actions → GitHub Release Monitor → Run workflow

5. **Optional: Adjust schedule**
   
   Edit `.github/workflows/monitor.yml` and change the cron expression:
   ```yaml
   schedule:
     - cron: '*/5 * * * *'  # Every 5 minutes
     # - cron: '*/15 * * * *'  # Every 15 minutes
     # - cron: '0 * * * *'     # Every hour
   ```

#### Use as reusable action in other repositories:

```yaml
name: Monitor Releases
on:
  schedule:
    - cron: '*/10 * * * *'
  workflow_dispatch:

jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - name: Monitor releases
        uses: ceviixx/gh-watcher@main
        with:
          repos: 'cli/cli,python/cpython'
          discord_webhook_url: ${{ secrets.DISCORD_WEBHOOK_URL }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          skip_existing_on_init: 'true'
          notify_on: 'new_release,dl_increase'
      
      # State persistence (optional but recommended)
      - name: Cache state
        uses: actions/cache@v4
        with:
          path: ./state
          key: release-state-${{ github.run_id }}
          restore-keys: release-state-
```

### Option 2: Local Execution (without Docker)

1. **Clone repository**
   ```bash
   git clone <your-repo-url>
   cd gh-watcher
   ```

2. **Create Python Virtual Environment (recommended)**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # macOS/Linux
   # or: venv\Scripts\activate  # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   
   Create a `.env` file (see `.env.example` as template):
   ```bash
   cp .env.example .env
   ```
   
   Edit the `.env` file and set at least:
   - `REPOS`: The GitHub repositories to monitor (e.g. `cli/cli,python/cpython`)
   - `DISCORD_WEBHOOK_URL`: Your Discord webhook URL

5. **Start bot**
   ```bash
   # Load environment variables and start bot
   export $(grep -v '^#' .env | xargs) && python3 bot.py
   ```
   
   Or set individual variables directly:
   ```bash
   export REPOS="cli/cli,python/cpython"
   export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxx/yyy"
   export STATE_DIR="./state"
   python3 bot.py
   ```

### Option 3: Docker Execution

#### Using pre-built image:

1. **Clone repository**
   ```bash
   git clone <your-repo-url>
   cd gh-watcher
   ```

2. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env (especially REPOS and DISCORD_WEBHOOK_URL)
   ```

3. **Start container with pre-built image**
   ```bash
   docker compose up -d
   ```

4. **View logs**
   ```bash
   docker compose logs -f
   ```

5. **Stop container**
   ```bash
   docker compose down
   ```

#### Building locally:

If you want to build the image yourself or make modifications:

1. **Use the build compose file**
   ```bash
   docker compose -f docker-compose.build.yml up --build -d
   ```
   
   Or edit `docker-compose.yml` and comment/uncomment the appropriate lines.

#### Docker run (without compose):

**With named volume (Docker-managed):**
```bash
docker run -d \
  --name gh-release-bot \
  --restart unless-stopped \
  -e REPOS="cli/cli,python/cpython" \
  -e DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxx/yyy" \
  -e POLL_INTERVAL=300 \
  -v gh_release_state:/state \
  ghcr.io/ceviixx/gh-watcher:latest
```

**With local directory mapping:**
```bash
docker run -d \
  --name gh-release-bot \
  --restart unless-stopped \
  -e REPOS="cli/cli,python/cpython" \
  -e DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxx/yyy" \
  -e POLL_INTERVAL=300 \
  -e STATE_DIR=/state \
  -v $(pwd)/state:/state \
  ghcr.io/ceviixx/gh-watcher:latest
```

**Custom state directory path:**
```bash
# Map host directory /my/custom/path to container's /data
docker run -d \
  --name gh-release-bot \
  --restart unless-stopped \
  -e REPOS="cli/cli,python/cpython" \
  -e DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxx/yyy" \
  -e STATE_DIR=/data \
  -v /my/custom/path:/data \
  ghcr.io/ceviixx/gh-watcher:latest
```

## Which Option Should I Choose?

| Feature | GitHub Actions | Docker 🐳 | Local |
|---------|----------------|-----------|-------|
| **Cost** | Free* (2000 min/month) | Server costs | Free |
| **Setup Complexity** | ⭐⭐⭐ Easy | ⭐⭐⭐ Easy | ⭐ Simple |
| **Maintenance** | None | Auto-updates | Manual |
| **Infrastructure** | None needed | Docker host | Python env |
| **State Persistence** | Actions cache | Volume | Local files |
| **Resource Usage** | Per-run | Continuous | Continuous |
| **Best For** | Hobby projects | Production 24/7 | Testing/Dev |

**Recommendation:** 
- 🐳 **Docker** for production use (pre-built image = fast & reliable)
- ⚡ **GitHub Actions** for personal monitoring without infrastructure
- 💻 **Local** for development and testing

\* GitHub Actions: Free for public repos, 2000 minutes/month for private repos (500 runs à 4 minutes)

## Configuration

All settings are configured via environment variables (see `.env.example`):

### Required Fields
- `REPOS`: Comma-separated list of GitHub repositories (format: `owner/repo`)
- `DISCORD_WEBHOOK_URL`: Discord webhook URL for notifications

### Optional Fields
- `GITHUB_TOKEN`: GitHub Personal Access Token (increases API rate limits)
- `POLL_INTERVAL`: Seconds between checks (default: 300, 0 = run once)
- `STATE_DIR`: Directory for state files (default: `/state` or `./state`)
- `ONLY_LATEST`: Only check the latest release (default: false)
- `INCLUDE_PRERELEASE`: Include prereleases (default: true)
- `INCLUDE_DRAFT`: Include draft releases (default: false)
- `SKIP_EXISTING_ON_INIT`: Skip existing releases on first start (default: true)
  - `true`: No notifications for already existing releases on first start
  - `false`: All existing releases will be reported as new on first start
- `NOTIFY_ON`: Events for notifications (default: `new_release,dl_increase,new_asset`)
- `ASSET_NAME_INCLUDE`: Regex filter for asset names (only matching assets)
- `ASSET_NAME_EXCLUDE`: Regex filter to exclude assets
- `USE_EMBEDS`: Use Discord embeds (default: true)
- `DISCORD_USERNAME`: Bot username in Discord
- `DISCORD_AVATAR_URL`: Bot avatar URL
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)

## Example Configuration

```bash
REPOS=cli/cli,python/cpython
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123456/abcdef
GITHUB_TOKEN=ghp_yourtoken
POLL_INTERVAL=300
ONLY_LATEST=false
INCLUDE_PRERELEASE=true
SKIP_EXISTING_ON_INIT=true
NOTIFY_ON=new_release,dl_increase
ASSET_NAME_EXCLUDE=sha256|checksums?\.txt$
USE_EMBEDS=true
LOG_LEVEL=INFO
```

## Tips

### Execution Modes

The bot supports two execution modes controlled by `POLL_INTERVAL`:

**Cron Mode (POLL_INTERVAL=0)** - Recommended for GitHub Actions and Cron Jobs
- ✅ Runs once and exits
- ✅ Efficient resource usage
- ✅ Better for scheduled execution
- ✅ Use for: GitHub Actions, cronjobs, scheduled tasks

**Continuous Mode (POLL_INTERVAL > 0)** - For long-running processes
- ✅ Runs continuously in a loop
- ✅ Self-contained monitoring
- ✅ Good for Docker deployments
- ✅ Use for: Docker containers, systemd services

**Example for different scenarios:**
```bash
# GitHub Actions / Cron (run once)
POLL_INTERVAL=0

# Docker / Always-on monitoring (every 5 minutes)
POLL_INTERVAL=300
```

### State Management
The bot stores its state in JSON files in `STATE_DIR`. This ensures:
- No duplicate notifications are sent
- Download counters are tracked correctly
- ETags are used for efficient API usage

### Rate Limits
- Without GitHub Token: 60 requests/hour
- With GitHub Token: 5000 requests/hour

Recommendation: Set a GitHub Personal Access Token for better rate limits.

### Debugging
Set `LOG_LEVEL=DEBUG` for detailed logging output.

## Troubleshooting

**Problem**: `REPOS not set` error
- Solution: Make sure the environment variable `REPOS` is set correctly

**Problem**: Rate Limit Errors
- Solution: Set a `GITHUB_TOKEN` or increase `POLL_INTERVAL`

**Problem**: No notifications
- Solution: Check `DISCORD_WEBHOOK_URL` and look at the logs (`LOG_LEVEL=DEBUG`)

## License

MIT (or your license)

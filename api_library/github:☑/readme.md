# GitHub Commit Monitor

A Python script that watches for new commits and prints both a concise summary line and full metadata for each one.

## 📋 Features

* Polls GitHub every **N seconds** (default 10s)
* Supports watching either:
  * A **single repo**: `<owner>/<repo>`
  * **All repos** under an owner: `<owner>`

* Detects and reports only **new** commits
* Prints a one-line summary for each commit:

  ```
  [2025-07-18T15:26:13Z] Summoner-Network/summoner-agents ▶ Remy Tuyeras: a39b14b – Fix typo in README
  ```

* Follows summary with a **pretty-printed JSON** block containing:

  * Full commit SHA, author name, date, message
  * Commit URL
  * Stats (`additions`, `deletions`, `total`)
  * Per-file changes

* Optional **`GITHUB_TOKEN`** support to raise rate limits and access private repos

## 🔑 Generating a GitHub Personal Access Token

To raise your rate limit (from 60→5 000 requests/hour) and access private repositories, create a Personal Access Token (PAT) and store it in your `.env`.

1. **Log in & navigate to Developer settings**

   * Sign in at github.com
   * Click your avatar → **Settings** → [**Developer settings**](https://github.com/settings/apps)

2. **Create a new token**

   * Select **Personal access tokens** → [**Tokens (classic)**](https://github.com/settings/tokens)
   * Click **Generate new token (classic)**

3. **Configure token details**

   * **Name**: e.g. “GitHub Commit Monitor”
   * **Expiration**: choose as needed (e.g. 30 days or “No expiration”)
   * **Scopes** (minimum):

     * `public_repo` (public repos)
     * `repo` (if you need private-repo access)

4. **Generate & copy**

   * Click **Generate token**
   * Copy the token string immediately (you won't see it again)

5. **Store in `.env`**

   ```env
   GITHUB_TOKEN=ghp_yourGeneratedTokenHere
   ```

   Make sure your `.env` is listed in `.gitignore`.


## 🚀 Installation

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```
2. (Optional) Create a `.env` file in the same folder with:

   ```env
   GITHUB_TOKEN=ghp_yourPersonalAccessTokenHere
   ```

## ⚙️ Usage

```bash
# 1) Single repo:
python github_commit_agent.py <owner> --repo <repo> [--interval N]

# 2) Entire account:
python github_commit_agent.py <owner> [--interval N]
```

* `<owner>`: GitHub username or organization
* `--repo <repo>`: (optional) specific repository name
* `--interval N`: polling interval in seconds (default: 10)

Examples:

```bash
# Watch just one repo every 10s
python github_commit_agent.py Summoner-Network --repo summoner-agents

# Watch all repos under Summoner-Network every 30s
python github_commit_agent.py Summoner-Network --interval 30
```

## 🔧 How It Works

1. **Repo discovery**

   * If `--repo` is provided, monitors only that repo.
   * Otherwise, lists **all** repos under the owner via `/users/{owner}/repos`.

2. **Commit polling**

   * Calls `/repos/{owner}/{repo}/commits?per_page=30` every `interval` seconds.
   * Tracks the most-recent commit SHA per repo as a baseline.

3. **New-commit fetch & display**

   * For each unseen SHA, fetches `/commits/{sha}` to get full metadata.
   * Prints:

     1. **Summary line**: `[timestamp] owner/repo ▶ author: short-SHA – subject`
     2. **Metadata block** via `pprint()`: full message, URL, stats, files

4. **Repeat**
   Sleeps for `interval` seconds, then repeats.


Customize `--interval` and `per_page` as needed, or adapt print/pprint sections for JSON-only output or TCP transport.

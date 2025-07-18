# GitHub Commit Monitor

A simple Python script that watches a GitHub repository for new commits and prints both a concise summary line and full metadata for each one.

## 📋 Features

* Polls the GitHub Commits API every **10 seconds**

* Detects and reports only **new** commits

* Prints a one-line summary for each commit:

  ```
  [2025-07-18T15:26:13Z] Remy Tuyeras: a39b14b – Fix typo in README
  ```

* Follows summary with a **pretty-printed JSON** block containing:

  * Full commit SHA, author name, date, message
  * Commit URL
  * Stats (`additions`, `deletions`, `total`)
  * Per-file changes

* Optional **`GITHUB_TOKEN`** support to raise rate limits and access private repos


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
python github_commit_agent.py <owner> <repo>
```

* `<owner>`: GitHub username or organization
* `<repo>`: Repository name

Example:

```bash
python github_commit_agent.py Summoner-Network summoner-agents
```

The script will print an initial “Last commit” line, then every 10 seconds:

1. A summary line for each new commit
2. A `pprint`-style JSON dictionary with detailed metadata


## 🔧 How It Works

1. **Fetch recent commits**
   Calls

   ```
   GET https://api.github.com/repos/{owner}/{repo}/commits?per_page=30
   ```

   Authenticated if `GITHUB_TOKEN` is set.

2. **Track the most-recent SHA**

   * On first run, records the latest commit without printing details.
   * On subsequent polls, it gathers all SHAs that are newer than the recorded one.

3. **Detail fetch & output**
   For each new SHA, it calls

   ```
   GET https://api.github.com/repos/{owner}/{repo}/commits/{sha}
   ```

   to retrieve full metadata, then prints:

   * **Summary line**: timestamp, author, short SHA, commit subject
   * **Metadata block**: full message, URL, stats, list of modified files

4. **Repeat**
   Sleeps for 10 seconds, then repeats.


## 🔄 Customization

* **Polling interval**
  Change the `interval` value in `monitor_commits(...)`, or modify the `asyncio.sleep(interval)` call.

* **Commit count**
  Adjust `per_page` in `fetch_commits(...)` to fetch more or fewer recent commits.

* **Output format**
  Tweak the `print(…)` and `pprint(info)` sections to suit your needs (e.g., JSON-only, log to file, send over TCP).

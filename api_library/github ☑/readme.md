# GitHub API

## How it works

1. **Endpoint selection**

   * **User/org**: `GET /users/{owner}/events`
   * **Repository**: `GET /repos/{owner}/{repo}/events`

2. **Polling loop**

   * Every `interval` seconds, fetches the latest events.
   * Filters out previously seen event IDs.
   * Prints new ones in chronological order.

3. **Optional GitHub token**

   * Place a Personal Access Token in a `.env` file as `GITHUB_TOKEN=` to raise your rate limit from 60 to 5,000 hits/hour.


## Example usage

```bash
# Monitor the user 'Summoner-Network' every 30 seconds
python api_library/github\ ☑/test.py Summoner-Network

# Monitor the repo 'Summoner-Network/summoner-agents' every 60 seconds
python api_library/github\ ☑/test.py Summoner-Network --repo summoner-agents
```

That should give you a live, terminal-based feed of GitHub activity tailored to any account or repository.

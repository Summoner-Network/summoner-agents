# Slack API

Create an agent here: [here](https://api.slack.com/apps)

API docs [here](https://slack.dev/python-slack-sdk/)

## ✅ Here is how to generate the `xoxb-` (Bot User) token:

### 1. **Go to: OAuth & Permissions** tab in your app's settings

URL format:

```
https://api.slack.com/apps/<YOUR_APP_ID>/oauth?
```

Or from your dashboard:

> Your App → Features → OAuth & Permissions


### 2. **Add Scopes** under **Bot Token Scopes**, for example:

* `app_mentions:read` (view messages that mention your bot)
* `channels:history` (read channel history)
* `channels:read` (list channels)
* `chat:write` (send messages as your bot)


### 3. **Install (or Reinstall) the App to Your Workspace**

Scroll to the top and click:

```
[Install to Workspace]
```

After approval, Slack will display your **Bot User OAuth Token**, which looks like:

```
xoxb-XXXXXXXXXX-XXXXXXXXXX-XXXXXXXXXXXXXXXX
```

Copy that value into your `.env` as:

```env
SLACK_BOT_TOKEN=xoxb-...
```


## ✅ Here is how to generate the `xapp-` (App-Level) token:

### 1. **Go to: Socket Mode** settings in your app's dashboard

URL format:

```
https://api.slack.com/apps/<YOUR_APP_ID>/settings/socket
```

Or from your dashboard:

> Your App → Settings → Socket Mode



### 2. **Enable Socket Mode**

Flip the toggle to **“Enable Socket Mode”**.



### 3. **Create an App-Level Token**

Under **App-Level Tokens**, click **“Generate Token”**.

* **Name:** e.g. `SummonerBot Socket Token`
* **Scopes:** at minimum `connections:write` (plus `authorizations:read` or `app_configurations:write` if needed)
* Click **“Save”**.

Slack will then show a token that looks like:

```
xapp-1-A1B2C3D4E5F6G7H8I9J0K
```

Copy that into your `.env` as:

```env
SLACK_APP_TOKEN=xapp-...
```


## ✅ Final `.env` Example

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

With those two tokens in place—and Socket Mode enabled plus the correct bot scopes—your Python Bolt app will connect and start receiving Slack events immediately.

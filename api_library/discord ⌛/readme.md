# Discord API

Create an agent here: [here](https://discord.com/developers/applications)

API docs [here](https://discordpy.readthedocs.io/)


## ✅ Step-by-step: How to Create and Use a Discord Bot with discord.py

### 1. Create a Discord Application and Bot Token

1. Go to: [https://discord.com/developers/applications](https://discord.com/developers/applications)

2. Click “New Application” → Give it a name.

3. Go to “Bot” → Click “Add Bot” → Confirm.

4. Under “Bot” tab:

   * Copy the bot token (click "Reset Token" if needed).
   * Enable the following:

     * Presence Intent
     * Server Members Intent
     * Message Content Intent (you use this!)
   * Save your changes.

5. Invite your bot to a server:

   * Go to “OAuth2” → “URL Generator”
   * Scopes: check bot
   * Bot Permissions: check at least “Send Messages” and “Read Messages”
   * Copy the generated URL, open it in your browser, and invite the bot to your server.

---

### 2. Store Token Securely in .env

Create a file called .env in the same directory as your script:

.env

```env
DISCORD_TOKEN=your_bot_token_here
```

Then install python-dotenv if you haven't:

```bash
pip install python-dotenv
```

---

### 3. Updated Working Code (using .env)

```python
import discord
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True  # Required for reading messages

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('$hello'):
        await message.channel.send('Hello!')

client.run(TOKEN)
```

---

## ✅ Common Mistakes That Trigger “Improper token” Error

| Mistake                            | Fix                                                |
| ---------------------------------- | -------------------------------------------------- |
| Pasting the token with quotes      | Don’t use quotes around the token in .env          |
| Extra spaces/newlines              | Make sure no whitespace before/after the token     |
| Using client secret instead        | Use the bot token from Bot tab, not OAuth2 secrets |
| Not enabling message intent        | Enable “Message Content Intent” on bot dashboard   |
| Not using client.run(TOKEN) safely | Use os.getenv and never hardcode tokens in code    |


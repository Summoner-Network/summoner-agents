# Multiplayer Game Agent

This agent is a fully-featured, secure, peer-to-peer multiplayer game client built with Pygame. It demonstrates a complete gameplay loop, including a lobby, active gameplay, and a game-over state, all synchronized through a `GeneralGameMasterAgent`.

---

## Gameplay Features

* **Lobby System**: When clients connect, they enter a waiting lobby where they can see all other connected players. The game starts when any player presses the **SPACEBAR**.
* **Live Gameplay**: Players control a colored square with the **WASD** keys and fire projectiles with a **mouse click**.
* **Health and Combat**: Each player has **10 health**. Projectiles deal **1 damage**. A player is eliminated when their health reaches zero.
* **Win Condition**: The last player with health remaining wins the game, at which point a "Game Over" screen is displayed declaring the winner.

---

## Why Use Cryptography?

In a traditional client-server game, the server is the single source of truth and security. But in this peer-to-peer model, clients must trust messages that come from other clients. Cryptography is what makes this trust possible.

* **Identity**: A player's public key serves as their unique, unforgeable identity. Unlike a simple username that could be spoofed, only the player who owns the corresponding private key can act on behalf of that identity.

* **Authenticity**: When "alice" sends a "shoot" command, she signs it with her private key. When "bob" receives this command, he uses alice's public key to verify the signature. If it's valid, he knows the command genuinely came from alice and not an imposter.

* **Integrity**: The digital signature is tied to the exact content of the message. If an attacker intercepted a "move right" command and tried to change it to a "move left" command, the signature would no longer match the content. All other clients would detect the tampering and reject the invalid message. 

This system creates a secure and decentralized environment where each agent can independently validate the actions of its peers, forming a "web of trust" without relying on a central authority.

---

## Technical Features

* **Peer-to-Peer Synchronization**: The game state is synchronized without a central game server. Each client processes a stream of events from a `ReportAgent` to build an identical simulation.
* **Cryptographic Identity**: Each player has a unique identity secured by a public/private key pair. All actions are digitally signed to prove their origin.
* **Authoritative Clock**: Player and projectile movement is timed by the `deltaTiming` value provided by the `ReportAgent`, ensuring smooth, consistent physics regardless of client performance or network lag.
* **Anti-Cheat**: The game features peer-side enforcement for game rules. For example, if a modified client attempts to fire faster than the allowed cooldown, their invalid shots are ignored by all other players.

---

## How to Set Up a Multiplayer Game

Setting up a multiplayer match involves running three components: the server, the reporter agent, and at least two game agents (one for each player).

### Step 1: Run the Server

```bash
# In a new terminal (Terminal 1)
# Remember to tweak the `rate_limit_msgs_per_minute` of `test_server_config.json` to 36000
python test_server.py
```

### Step 2: Start the Reporter

```bash
# In a new terminal (Terminal 2)
python agents/agent_GeneralGameMasterAgent_0/agent.py
```

### Step 3: Generate Player Identities

Before playing, you need to create a unique cryptographic identity for each player. Use the included `login.py` script with a `--name` argument.

Open a terminal and run the following commands:

```bash
# Create an identity for player "alice"
python agents/agent_PlayerAgent_2/login.py --name alice

# Create an identity for player "bob"
python agents/agent/login.py --name bob
```

### Step 4: Start the Game Clients

```bash
# In a new terminal (Terminal 3), start a client for "alice"
python agents/agent_PlayerAgent_2/agent.py --name alice

# In a new terminal (Terminal 4), start a client for "bob"
python agents/agent_PlayerAgent_2/agent.py --name bob
```

Two Pygame windows will appear. You will see both players in the lobby in each window.

### Step 5: Play the Game!

In either game window, press the SPACEBAR to start the match. The game will begin for all players.

Use WASD to move your player.

Click the mouse to fire projectiles at your opponent.

The last player with health remaining wins!
# `GameMasterAgent_1`

Work in progress! 

For now, you can try it out with the following commands:

```sh
python server.py --config configs/server_config_MMO.json

python agents/agent_GameMasterAgent_1/agent.py

# player 1
python agents/agent_GamePlayerAgent_1/agent.py --avatar wizard.png --id alice

#player 2
python agents/agent_GamePlayerAgent_1/agent.py --avatar wizard.png --id bob
```

With a slightly better game environment:

```sh
python server.py --config configs/server_config_MMO.json

python agents/agent_GameMasterAgent_1/agent.py

# player 1
python agents/agent_GamePlayerAgent_2/agent.py --avatar wizard.png --id alice --seed lava

#player 2
python agents/agent_GamePlayerAgent_2/agent.py --avatar wizard.png --id alice --seed lava
```
from summoner.client import SummonerClient
import argparse
import asyncio

agent = SummonerClient(name="SendAgent_0")

@agent.send(route="")
async def custom_send():
    await asyncio.sleep(1)
    return "Hello Server!"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config myproject/client_config.json)')
    args = parser.parse_args()

    agent.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")
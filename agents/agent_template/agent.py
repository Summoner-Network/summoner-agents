from summoner.client import SummonerClient
import argparse

client = SummonerClient(name="Agent")

# ...

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")
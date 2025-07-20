from summoner.server import SummonerServer

if __name__ == "__main__":
    SummonerServer(name="first_server").run(
        host="127.0.0.1",
        port=1234,
        config_path="server_config.json"
        )

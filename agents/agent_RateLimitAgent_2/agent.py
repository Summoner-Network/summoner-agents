from summoner.client import SummonerClient
import argparse, time
import asyncio

client = SummonerClient(name="RateLimitAgent")

tracker_lock = asyncio.Lock()
tracker = {"count": 0, "initial": time.time(), "defended": 0}

@client.receive(route="defenses")
async def custom_receive(msg: str) -> None:
    print(msg)
    if isinstance(msg, str) and msg.startswith("Warning:"):
        async with tracker_lock:
            tracker["defended"] +=1
        if tracker["defended"] >= 10:
            print(tracker)
            await client.quit()
        
@client.send(route="attack", multi=True)
async def custom_send() -> list[dict]:
    await asyncio.sleep(0.1)
    msg1 = "Lorem ipsum dolor sit amet consectetur adipiscing elit. Quisque faucibus ex sapien vitae pellentesque sem placerat. In id cursus mi pretium tellus duis convallis. Tempus leo eu aenean sed diam urna tempor. Pulvinar vivamus fringilla lacus nec metus bibendum egestas. Iaculis massa nisl malesuada lacinia integer nunc posuere. Ut hendrerit semper vel class aptent taciti sociosqu. Ad litora torquent per conubia nostra inceptos himenaeos."
    async with tracker_lock:
        tracker["count"] +=1
        msg = {
            "message": msg1, 
            "count": tracker["count"], 
            "time": str(time.time() - tracker["initial"]), 
            "defended": tracker["defended"]
            }
    
    return [msg] * 3


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")
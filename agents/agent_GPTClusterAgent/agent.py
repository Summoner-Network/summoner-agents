import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

from summoner.client import SummonerClient
from summoner.protocol import Direction
from typing import Any, Union, Optional
from pathlib import Path
import argparse, json, asyncio, os

from aioconsole import aprint
from dotenv import load_dotenv
import openai
from openai import AsyncOpenAI

from safeguards import (
    count_embedding_tokens,
    estimate_embedding_request_cost,
    actual_embedding_request_cost,
    get_usage_from_response,
)

# Try scikit-learn, but degrade gracefully if not installed
try:
    from sklearn.cluster import KMeans, AgglomerativeClustering, DBSCAN
    _SKLEARN_AVAILABLE = True
except Exception:
    KMeans = AgglomerativeClustering = DBSCAN = None  # type: ignore
    _SKLEARN_AVAILABLE = False

# -------------------- early parse so class can load configs --------------------
prompt_parser = argparse.ArgumentParser(add_help=False)
prompt_parser.add_argument("--gpt", dest="gpt_config_path", required=False, help="Path to gpt_config.json (defaults to file next to this script).")
prompt_parser.add_argument("--id", dest="id_json_path", required=False, help="Path to id.json (defaults to file next to this script).")
prompt_args, _ = prompt_parser.parse_known_args()

# -------------------- async queue --------------------
message_buffer = None

async def setup():
    """Initialize the internal message buffer used between receive/send handlers."""
    global message_buffer
    message_buffer = asyncio.Queue()

# -------------------- agent --------------------
class MyAgent(SummonerClient):
    """
    GPTClusterAgent:
    - Receives a message with content like: {"texts": [...], "clustering": {...}}.
    - Embeds 'texts' using the configured embedding model with token/cost guardrails.
    - Clusters the resulting embeddings using the selected algorithm (default k-means).
    - Returns JSON containing cost, model name, and clustering assignments.
    """
    def __init__(self, name: Optional[str] = None):
        super().__init__(name=name)

        # base dir
        try:
            self.base_dir = Path(__file__).resolve().parent
        except NameError:
            self.base_dir = Path.cwd()

        # env / client
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY missing in environment.")
        self.client = AsyncOpenAI(api_key=api_key)

        # ----- GPT config -----
        gpt_cfg_path = Path(prompt_args.gpt_config_path) if prompt_args.gpt_config_path else (self.base_dir / "gpt_config.json")
        self.gpt_cfg = self._load_json(gpt_cfg_path)

        # embeddings knobs (all configurable)
        self.embedding_model          = self.gpt_cfg.get("embedding_model", "text-embedding-3-small")
        self.max_embedding_tokens     = int(self.gpt_cfg.get("max_embedding_input_tokens", 500))
        self.embed_cost_limit_usd     = self.gpt_cfg.get("embed_cost_limit_usd")  # None or float
        self.debug                    = bool(self.gpt_cfg.get("debug", False))
        self.sleep_seconds            = float(self.gpt_cfg.get("sleep_seconds", 0.1))

        # default clustering config (can be overridden by incoming message.content.clustering)
        clustering_cfg = self.gpt_cfg.get("clustering", {}) or {}
        self.default_algo             = clustering_cfg.get("algo", "kmeans")
        self.default_k                = int(clustering_cfg.get("k", 3))
        self.default_max_iter         = int(clustering_cfg.get("max_iter", 20))
        self.default_seed             = int(clustering_cfg.get("seed", 0))

        # identity (from --id or default id.json)
        id_path = Path(prompt_args.id_json_path) if prompt_args.id_json_path else (self.base_dir / "id.json")
        try:
            with id_path.open("r", encoding="utf-8") as f:
                id_dict: dict = json.load(f)
            self.my_id = str(id_dict.get("uuid") or "unknown")
        except Exception:
            self.my_id = "unknown"
            self.logger.warning("id.json missing or invalid; using my_id='unknown'")

        # optional: model id sanity check (best-effort)
        try:
            model_ids = [m.id for m in openai.models.list().data]
        except Exception:
            model_ids = []
        if model_ids and self.embedding_model not in model_ids:
            raise ValueError(f"Invalid embedding_model in gpt_config.json: {self.embedding_model}. "
                             f"Available: {', '.join(model_ids)}")

        if not _SKLEARN_AVAILABLE:
            self.logger.warning("scikit-learn not available; clustering will fallback to a single cluster.")

    # ------------- helpers -------------
    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    async def _embed(self, texts: list[str]) -> dict[str, Any]:
        """
        Embed a list of strings with token and cost guards.
        Returns: {"output": list[list[float]] | None, "cost": float | None}
        """
        # Short-circuit if nothing to embed (avoids API call)
        if not texts:
            return {"output": [], "cost": 0.0}

        # Diagnostics + budgeting (keep your print style)
        text_tokens = count_embedding_tokens(texts, self.embedding_model)
        if self.debug:
            await aprint(f"\033[96mEmbedding tokens: {text_tokens} > {self.max_embedding_tokens} ? {text_tokens > self.max_embedding_tokens}\033[0m")

        est_cost = estimate_embedding_request_cost(self.embedding_model, text_tokens)
        if self.debug:
            await aprint(f"\033[95m[embed] Estimated cost: ${est_cost:.10f}\033[0m")

        # Guard 1: token ceiling (simple stop)
        if text_tokens > self.max_embedding_tokens:
            if self.debug:
                await aprint("\033[93m[embed] Tokens exceeded — unable to send the request.\033[0m")
            return {"output": None, "cost": None}

        # Guard 2: cost ceiling (compare estimated cost to limit)
        if self.embed_cost_limit_usd is not None and est_cost > self.embed_cost_limit_usd:
            if self.debug:
                await aprint(
                    f"\033[93m[embed] Skipping request: estimated cost ${est_cost:.10f} "
                    f"exceeds cost_limit ${self.embed_cost_limit_usd:.10f}.\033[0m"
                )
            return {"output": None, "cost": None}

        # Call embeddings API
        response = await self.client.embeddings.create(
            model=self.embedding_model,
            input=texts,
        )

        # Usage & cost
        usage = get_usage_from_response(response)
        act_cost = None
        if usage:
            act_cost = actual_embedding_request_cost(self.embedding_model, usage.total_tokens)
            if self.debug:
                await aprint(f"\033[95m[embed] Actual cost: ${act_cost:.10f}\033[0m")
        else:
            if self.debug:
                await aprint("\033[93m[embed] Note: usage not available. Skipping cost.\033[0m")

        return {"output": [rec.embedding for rec in response.data], "cost": act_cost}

    # -------------------- clustering dispatcher --------------------
    def _cluster(self, embeddings: list[list[float]], cfg: dict[str, Any]) -> dict[str, Any]:
        """
        Dispatch to a clustering algorithm and return a JSON-friendly result.

        Supported configs (message-level via content.clustering or defaults from gpt_config.json):

        KMeans (default):
          {"algo": "kmeans", "k": 4, "max_iter": 100, "seed": 42}

        Agglomerative:
          {"algo": "agglomerative", "k": 3, "linkage": "ward", "metric": "euclidean"}

        DBSCAN:
          {"algo": "dbscan", "eps": 0.8, "min_samples": 4, "metric": "euclidean"}

        Returns:
          {
            "algo": "<algo>",
            ...algo_params...,
            "assignments": [int, ...],     # label per embedding
            "clusters": {"<label>": [indices...] }
          }
        """
        if not embeddings:
            return {
                "algo": (cfg.get("algo") or self.default_algo),
                "assignments": [],
                "clusters": {},
                "note": "No embeddings to cluster."
            }

        if not _SKLEARN_AVAILABLE:
            assigns = [0] * len(embeddings)
            return {
                "algo": "none",
                "k": 1,
                "assignments": assigns,
                "clusters": {"0": list(range(len(embeddings)))},
                "note": "scikit-learn not available; defaulted to single cluster."
            }

        algo = (cfg.get("algo") or self.default_algo).lower()

        # --- KMeans ---
        if algo in ("kmeans", "k-means"):
            k = int(cfg.get("k", self.default_k))
            max_iter = int(cfg.get("max_iter", self.default_max_iter))
            seed = int(cfg.get("seed", self.default_seed))
            model = KMeans(n_clusters=max(1, k), n_init=10, random_state=seed, max_iter=max_iter)
            labels = model.fit_predict(embeddings).tolist()

            clusters: dict[str, list[int]] = {}
            for i, lab in enumerate(labels):
                clusters.setdefault(str(int(lab)), []).append(i)

            return {
                "algo": "kmeans",
                "k": k,
                "assignments": labels,
                "clusters": clusters
            }

        # --- Agglomerative ---
        if algo in ("agglomerative", "hierarchical", "agglo"):
            k = int(cfg.get("k", self.default_k))
            linkage = str(cfg.get("linkage", "ward"))
            metric = str(cfg.get("metric", "euclidean"))
            model = AgglomerativeClustering(n_clusters=max(1, k), linkage=linkage, metric=metric)
            labels = model.fit_predict(embeddings).tolist()

            clusters: dict[str, list[int]] = {}
            for i, lab in enumerate(labels):
                clusters.setdefault(str(int(lab)), []).append(i)

            return {
                "algo": "agglomerative",
                "k": k,
                "linkage": linkage,
                "metric": metric,
                "assignments": labels,
                "clusters": clusters
            }

        # --- DBSCAN ---
        if algo == "dbscan":
            eps = float(cfg.get("eps", 0.5))
            min_samples = int(cfg.get("min_samples", 5))
            metric = str(cfg.get("metric", "euclidean"))
            model = DBSCAN(eps=eps, min_samples=min_samples, metric=metric)
            labels = model.fit_predict(embeddings).tolist()

            clusters: dict[str, list[int]] = {}
            for i, lab in enumerate(labels):
                clusters.setdefault(str(int(lab)), []).append(i)

            return {
                "algo": "dbscan",
                "eps": eps,
                "min_samples": min_samples,
                "metric": metric,
                "assignments": labels,
                "clusters": clusters
            }

        # --- Fallback if unknown algo ---
        assigns = [0] * len(embeddings)
        return {
            "algo": "none",
            "k": 1,
            "assignments": assigns,
            "clusters": {"0": list(range(len(embeddings)))},
            "note": f"Unsupported algo '{algo}', defaulted to single cluster."
        }

# instantiate
agent = MyAgent(name="GPTClusterAgent")

# -------------------- hooks --------------------
@agent.hook(direction=Direction.RECEIVE)
async def validate(msg: Any) -> Optional[dict]:
    if isinstance(msg, str) and msg.startswith("Warning:"):
        agent.logger.warning(msg.replace("Warning:", "[From Server]"))
        return  # drop

    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg):
        agent.logger.info("[hook:recv] missing address/content")
        return

    agent.logger.info(f"[hook:recv] {msg['remote_addr']} passed validation")
    return msg

@agent.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    agent.logger.info(f"[hook:send] sign {agent.my_id}")
    if isinstance(msg, str):
        msg = {"message": msg}
    if not isinstance(msg, dict):
        return
    msg.update({"from": agent.my_id})
    return msg

# -------------------- handlers --------------------
@agent.receive(route="")
async def receiver_handler(msg: Any) -> None:
    address = msg["remote_addr"]
    content = json.dumps(msg["content"])
    await message_buffer.put(content)
    agent.logger.info(f"Buffered message from:(SocketAddress={address}).")

@agent.send(route="")
async def send_handler() -> Union[dict, str]:
    content = await message_buffer.get()
    payload = json.loads(content)

    # Expect: payload like {"texts": [...], "clustering": {"algo":"kmeans","k":3}} OR just {"texts":[...]}
    texts_in = payload.get("texts")
    if not isinstance(texts_in, (list, tuple)):
        texts_in = []
    texts = [str(x) for x in texts_in]  # normalize to strings

    # Embeddings
    embs_res = await agent._embed(texts)
    embeddings = embs_res.get("output") or []

    # Clustering config (message-level override)
    cluster_cfg = payload.get("clustering") or {}
    result = agent._cluster(embeddings, cluster_cfg)

    # Build output
    output: dict[str, Any] = {
        "embeddings_cost": embs_res.get("cost"),
        "embedding_model": agent.embedding_model,
        "num_texts": len(texts),
        "result": result,
    }

    if "from" in payload:
        output["to"] = payload["from"]

    agent.logger.info(f"[cluster] model={agent.embedding_model} id={agent.my_id} texts={len(texts)}")
    await asyncio.sleep(agent.sleep_seconds)

    return output

# -------------------- main --------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the client config (JSON), e.g., --config configs/client_config.json')
    args, _ = parser.parse_known_args()

    agent.loop.run_until_complete(setup())
    agent.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")

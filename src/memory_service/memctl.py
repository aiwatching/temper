"""memctl — terminal client for Memory Service.

Single-file CLI so it's easy to grok and copy. Wraps the same REST API
that `examples/english_agent_*.py` use, just with subcommands + a config
file so you don't have to paste `X-API-Key` everywhere.

Auth resolution (highest priority first):
    1. --key / --token on the command line
    2. MEMCTL_API_KEY / MEMCTL_JWT env vars
    3. MS_API_KEY env var (matches the examples)
    4. ~/.config/memctl/config.toml

Output is table-by-default; pass --json to get raw API JSON for piping.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "memctl"
CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_BASE_URL = "http://localhost:8000"


# ---------- config -------------------------------------------------------


def _read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return tomllib.loads(CONFIG_PATH.read_text())
    except Exception as exc:
        die(f"failed to read {CONFIG_PATH}: {exc}")


def _write_config(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Hand-roll TOML so we don't take a `tomli-w` dep just for this. Two
    # types are enough for our config: strings and we don't have anything
    # else.
    lines: list[str] = []
    for k, v in cfg.items():
        if v is None:
            continue
        escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{k} = "{escaped}"')
    CONFIG_PATH.write_text("\n".join(lines) + "\n")
    # 0600 — these are credentials.
    CONFIG_PATH.chmod(0o600)


# ---------- HTTP --------------------------------------------------------


def _resolve_creds(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    """Pick base_url + auth headers. Returns (base_url, headers)."""
    cfg = _read_config()
    base_url = (
        getattr(args, "base_url", None)
        or os.environ.get("MEMCTL_BASE_URL")
        or os.environ.get("MS_BASE_URL")
        or cfg.get("base_url")
        or DEFAULT_BASE_URL
    ).rstrip("/")

    api_key = (
        getattr(args, "key", None)
        or os.environ.get("MEMCTL_API_KEY")
        or os.environ.get("MS_API_KEY")
        or cfg.get("api_key")
    )
    jwt = (
        getattr(args, "token", None)
        or os.environ.get("MEMCTL_JWT")
        or cfg.get("jwt")
    )

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    elif jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    return base_url, headers


def _request(args: argparse.Namespace, method: str, path: str, **kw: Any) -> Any:
    base_url, headers = _resolve_creds(args)
    # Some endpoints (login, register) are public — let those run without
    # credentials by allowing this layer to be called with `auth=False`.
    if kw.pop("auth", True) is False:
        headers.pop("X-API-Key", None)
        headers.pop("Authorization", None)
    with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as c:
        r = c.request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        die(f"{method} {path} → {r.status_code}: {detail}")
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


# ---------- output ------------------------------------------------------


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write(f"memctl: {msg}\n")
    sys.exit(code)


def emit(args: argparse.Namespace, data: Any, columns: list[str] | None = None) -> None:
    """Print as a table or as JSON depending on --json. `columns` is a list
    of dotted keypaths into each row; the column header is the last segment.
    """
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return
    if data is None:
        return
    if isinstance(data, dict):
        # key:value pairs
        width = max((len(k) for k in data.keys()), default=0)
        for k, v in data.items():
            print(f"  {k.ljust(width)}  {_fmt(v)}")
        return
    if not isinstance(data, list):
        print(data)
        return
    if not data:
        print("  (no rows)")
        return
    if columns is None:
        columns = list(data[0].keys())
    rows = [[_get(item, c) for c in columns] for item in data]
    headers = [c.split(".")[-1] for c in columns]
    _print_table(headers, rows)


def _get(item: dict[str, Any], path: str) -> Any:
    cur: Any = item
    for part in path.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part, "")
    return cur


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    # Squash newlines so multi-line summaries don't wreck table alignment.
    return str(v).replace("\n", " ⏎ ")


def _dump_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _print_table(headers: list[str], rows: list[list[Any]]) -> None:
    cols = list(zip(headers, *(([_fmt(c) for c in row]) for row in rows)))
    widths = [max(len(str(x)) for x in col) for col in cols]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*(_fmt(c) for c in row)))


# ---------- commands ----------------------------------------------------


def cmd_login(args: argparse.Namespace) -> None:
    """Interactive: log in with email+password and stash a JWT (or accept
    a pasted API key).
    """
    base_url = (
        args.base_url
        or os.environ.get("MEMCTL_BASE_URL")
        or os.environ.get("MS_BASE_URL")
        or _read_config().get("base_url")
        or DEFAULT_BASE_URL
    ).rstrip("/")

    email = args.email or input("email: ").strip()
    password = args.password or getpass.getpass("password: ")

    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post("/v1/auth/login", json={"email": email, "password": password})
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = r.text
            die(f"login failed: {detail}")
        body = r.json()

    cfg = _read_config()
    cfg["base_url"] = base_url
    cfg["jwt"] = body["access_token"]
    cfg["email"] = email
    # Keep any existing api_key; login doesn't clear it.
    _write_config(cfg)
    print(f"  logged in as {email} → {CONFIG_PATH}")


def cmd_set_key(args: argparse.Namespace) -> None:
    """Persist an API key into the config so agents can use the same
    credential as the human."""
    cfg = _read_config()
    if args.base_url:
        cfg["base_url"] = args.base_url.rstrip("/")
    cfg["api_key"] = args.key
    _write_config(cfg)
    print(f"  api_key saved → {CONFIG_PATH}")


def cmd_logout(args: argparse.Namespace) -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
        print(f"  removed {CONFIG_PATH}")
    else:
        print("  no config to remove")


def cmd_whoami(args: argparse.Namespace) -> None:
    data = _request(args, "GET", "/v1/auth/me")
    emit(args, data)


# --- episodes --------------------------------------------------------


def cmd_write(args: argparse.Namespace) -> None:
    body = {"content": args.content, "source_type": args.source}
    if args.namespace:
        body["namespace"] = args.namespace
    if args.tags:
        body["tags"] = args.tags
    if args.saga:
        body["saga"] = args.saga
    params = {"async_extract": "true"} if args.async_extract else None
    data = _request(args, "POST", "/v1/episodes", json=body, params=params)
    if getattr(args, "json", False):
        emit(args, data)
        return
    print(f"  episode_id: {data['episode_id']}")
    print(f"  namespace:  {data['namespace']}")
    if data.get("extracted_facts"):
        print(f"  facts ({len(data['extracted_facts'])}):")
        for f in data["extracted_facts"]:
            print(f"    - {f['fact']}")
    elif data.get("extracted_entities"):
        print(f"  entities ({len(data['extracted_entities'])}):")
        for e in data["extracted_entities"]:
            print(f"    - {e['name']}: {e.get('summary') or ''}")
    else:
        print("  (LLM produced no entities or facts for this content)")


def cmd_write_bulk(args: argparse.Namespace) -> None:
    """Read newline-delimited text from --file (or stdin) and submit as bulk."""
    if args.file == "-" or args.file is None:
        source = sys.stdin
        close = False
    else:
        source = open(args.file)  # noqa: SIM115
        close = True
    try:
        lines = [ln.strip() for ln in source if ln.strip()]
    finally:
        if close:
            source.close()
    if not lines:
        die("no non-empty lines to write")

    items = [{"content": ln, "source_type": args.source} for ln in lines]
    if args.tags:
        for it in items:
            it["tags"] = args.tags
    body: dict[str, Any] = {"items": items}
    if args.namespace:
        body["namespace"] = args.namespace
    if args.saga:
        body["saga"] = args.saga
    data = _request(args, "POST", "/v1/episodes/bulk", json=body)
    if getattr(args, "json", False):
        _dump_json(data)
        return
    print(f"  wrote {len(data['episode_ids'])} episode(s) → {data['namespace']}")
    print(f"  extracted {data['total_entities']} entities, {data['total_facts']} facts")


def cmd_search(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {"query": args.query, "limit": args.limit}
    if args.namespace:
        params["namespaces"] = args.namespace
    if args.as_of:
        params["as_of"] = args.as_of
    if args.edge_types:
        params["edge_types"] = args.edge_types
    if args.node_labels:
        params["node_labels"] = args.node_labels
    if args.center:
        params["center"] = args.center
    if args.bfs_origins:
        params["bfs_origins"] = args.bfs_origins
    if args.bfs_max_depth is not None:
        params["bfs_max_depth"] = args.bfs_max_depth
    if args.reranker:
        params["reranker"] = args.reranker
    data = _request(args, "GET", "/v1/search", params=params)
    if getattr(args, "json", False):
        _dump_json(data)
        return
    header = f"  query: {data['query']}  hits: {len(data['facts'])}"
    if args.as_of:
        header += f"  (as_of {args.as_of})"
    print(header)
    emit(args, data["facts"], columns=["kind", "fact", "namespace"])


def cmd_ls(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {"limit": args.limit}
    if args.namespace:
        params["namespace"] = args.namespace
    data = _request(args, "GET", "/v1/episodes", params=params)
    if getattr(args, "json", False):
        _dump_json(data)
        return
    emit(
        args,
        data["episodes"],
        columns=["episode_id", "namespace", "created_by_agent", "created_at"],
    )


def cmd_show(args: argparse.Namespace) -> None:
    data = _request(args, "GET", f"/v1/episodes/{args.episode_id}")
    if getattr(args, "json", False):
        emit(args, data)
        return
    for k in (
        "episode_id",
        "namespace",
        "created_by_agent",
        "source_type",
        "tags",
        "reference_time",
        "created_at",
    ):
        print(f"  {k}: {_fmt(data.get(k))}")
    print()
    print(f"  content:\n    {data.get('content') or ''}")
    if data.get("entities"):
        print(f"\n  entities ({len(data['entities'])}):")
        for e in data["entities"]:
            print(f"    - {e['name']}: {e.get('summary') or ''}")
    if data.get("facts"):
        print(f"\n  facts ({len(data['facts'])}):")
        for f in data["facts"]:
            print(f"    - {f['fact']}")


def cmd_rm(args: argparse.Namespace) -> None:
    _request(args, "DELETE", f"/v1/episodes/{args.episode_id}")
    print(f"  deleted {args.episode_id}")


def cmd_status(args: argparse.Namespace) -> None:
    data = _request(args, "GET", f"/v1/episodes/{args.episode_id}/status")
    emit(args, data)


# --- api keys --------------------------------------------------------


def cmd_key_create(args: argparse.Namespace) -> None:
    data = _request(
        args, "POST", "/v1/users/me/api-keys", json={"agent_name": args.name}
    )
    if getattr(args, "json", False):
        emit(args, data)
        return
    print(f"  key_id: {data['id']}")
    print(f"  agent:  {data['agent_name']}")
    print(f"  key:    {data['key']}        # shown ONCE — store it now")


def cmd_key_ls(args: argparse.Namespace) -> None:
    data = _request(args, "GET", "/v1/users/me/api-keys")
    emit(args, data, columns=["id", "agent_name", "prefix", "revoked", "last_used_at"])


def cmd_key_rm(args: argparse.Namespace) -> None:
    _request(args, "DELETE", f"/v1/users/me/api-keys/{args.key_id}")
    print(f"  revoked {args.key_id}")


# --- orgs ------------------------------------------------------------


def cmd_org_create(args: argparse.Namespace) -> None:
    data = _request(
        args, "POST", "/v1/orgs", json={"slug": args.slug, "name": args.name}
    )
    emit(args, data)


def cmd_org_ls(args: argparse.Namespace) -> None:
    data = _request(args, "GET", "/v1/orgs")
    emit(args, data, columns=["slug", "name", "member_count", "created_at"])


def cmd_org_rm(args: argparse.Namespace) -> None:
    _request(args, "DELETE", f"/v1/orgs/{args.slug}")
    print(f"  deleted org {args.slug}")


def cmd_org_member_add(args: argparse.Namespace) -> None:
    data = _request(
        args,
        "POST",
        f"/v1/orgs/{args.slug}/members",
        json={"user_id": args.user_id, "is_org_admin": args.admin},
    )
    emit(args, data)


def cmd_org_member_ls(args: argparse.Namespace) -> None:
    data = _request(args, "GET", f"/v1/orgs/{args.slug}/members")
    emit(args, data, columns=["user_id", "email", "display_name", "is_org_admin"])


def cmd_org_member_rm(args: argparse.Namespace) -> None:
    _request(args, "DELETE", f"/v1/orgs/{args.slug}/members/{args.user_id}")
    print(f"  removed {args.user_id} from {args.slug}")


# --- groups ----------------------------------------------------------


def cmd_group_create(args: argparse.Namespace) -> None:
    body = {"slug": args.slug, "name": args.name}
    if args.org:
        body["org_slug"] = args.org
    data = _request(args, "POST", "/v1/groups", json=body)
    emit(args, data)


def cmd_group_ls(args: argparse.Namespace) -> None:
    data = _request(args, "GET", "/v1/groups")
    emit(
        args,
        data,
        columns=["slug", "name", "org_slug", "member_count", "created_at"],
    )


def cmd_group_rm(args: argparse.Namespace) -> None:
    _request(args, "DELETE", f"/v1/groups/{args.slug}")
    print(f"  deleted group {args.slug}")


def cmd_group_member_add(args: argparse.Namespace) -> None:
    role = "admin" if args.admin else "member"
    data = _request(
        args,
        "POST",
        f"/v1/groups/{args.slug}/members",
        json={"user_id": args.user_id, "role": role},
    )
    emit(args, data)


def cmd_group_member_ls(args: argparse.Namespace) -> None:
    data = _request(args, "GET", f"/v1/groups/{args.slug}/members")
    emit(args, data, columns=["user_id", "email", "display_name", "role"])


def cmd_group_member_rm(args: argparse.Namespace) -> None:
    _request(args, "DELETE", f"/v1/groups/{args.slug}/members/{args.user_id}")
    print(f"  removed {args.user_id} from group {args.slug}")


# --- graph (direct FalkorDB inspection) ------------------------------
#
# Talks directly to FalkorDB rather than going through the service. This
# is meant for inspection / debugging — it bypasses our namespace permission
# layer, so trust boundary is "whoever can reach localhost:6380." For local
# dev that's the same person already running `redis-cli`.


def _encode_namespace(raw: str) -> str:
    """Mirror core.namespaces.Namespace.as_graphiti_group_id without
    importing the server side (keeps memctl independent of DB models)."""
    return raw.replace(":", "__").replace("-", "_")


def _falkordb(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    import falkordb  # local import — only graph subcommands need it

    host = (
        getattr(args, "falkordb_host", None)
        or os.environ.get("FALKORDB_HOST")
        or "localhost"
    )
    port = int(
        getattr(args, "falkordb_port", None)
        or os.environ.get("FALKORDB_PORT")
        or 6380
    )
    return falkordb.FalkorDB(host=host, port=port)


def _resolve_graph_name(args: argparse.Namespace) -> str:
    """Figure out which FalkorDB graph (encoded group_id) to talk to.

    `--namespace user:me` → fetch /v1/auth/me to learn the caller's UUID.
    `--namespace user:<id>` or `group:<slug>` etc. → encode literally.
    No --namespace → default to user:me.
    """
    raw = args.namespace or "user:me"
    if raw == "user:me":
        me = _request(args, "GET", "/v1/auth/me")
        raw = f"user:{me['id']}"
    return _encode_namespace(raw)


def _rows_from(result) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    """Turn a FalkorDB QueryResult into a list of {column: value} dicts."""
    headers = [h[1] for h in result.header]
    out: list[dict[str, Any]] = []
    for row in result.result_set:
        rec: dict[str, Any] = {}
        for h, v in zip(headers, row):
            # Node / Edge instances have a `.properties` dict; flatten them
            # to a one-line repr for the table view, dump as JSON otherwise.
            if hasattr(v, "properties"):
                rec[h] = json.dumps(v.properties, default=str)
            elif isinstance(v, list):
                rec[h] = ", ".join(str(x) for x in v)
            else:
                rec[h] = v
        out.append(rec)
    return out


def cmd_graph_list(args: argparse.Namespace) -> None:
    db = _falkordb(args)
    graphs = db.list_graphs()
    if getattr(args, "json", False):
        _dump_json(graphs)
        return
    # Annotate each with size + label breakdown so the user can tell which
    # ones are alive and which are just leftover empty shells.
    rows: list[dict[str, Any]] = []
    for name in graphs:
        try:
            r = db.select_graph(name).ro_query(
                "MATCH (n) RETURN count(n) AS nodes"
            )
            nodes = r.result_set[0][0] if r.result_set else 0
        except Exception:
            nodes = "?"
        try:
            r = db.select_graph(name).ro_query(
                "MATCH ()-[r]->() RETURN count(r) AS edges"
            )
            edges = r.result_set[0][0] if r.result_set else 0
        except Exception:
            edges = "?"
        rows.append({"graph": name, "nodes": nodes, "edges": edges})
    emit(args, rows, columns=["graph", "nodes", "edges"])


def cmd_graph_summary(args: argparse.Namespace) -> None:
    db = _falkordb(args)
    g = db.select_graph(_resolve_graph_name(args))
    nodes_by_label = _rows_from(
        g.ro_query(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count "
            "ORDER BY count DESC"
        )
    )
    edges_by_type = _rows_from(
        g.ro_query(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count "
            "ORDER BY count DESC"
        )
    )
    if getattr(args, "json", False):
        _dump_json({"nodes": nodes_by_label, "edges": edges_by_type})
        return
    print(f"  graph: {g.name}\n")
    print("  nodes:")
    emit(args, nodes_by_label, columns=["label", "count"])
    print("\n  edges:")
    emit(args, edges_by_type, columns=["type", "count"])


def cmd_graph_nodes(args: argparse.Namespace) -> None:
    db = _falkordb(args)
    g = db.select_graph(_resolve_graph_name(args))
    where = f"WHERE n:{args.label}" if args.label else ""
    q = (
        f"MATCH (n) {where} "
        "RETURN labels(n)[0] AS label, n.name AS name, n.summary AS summary, "
        "       n.content AS content "
        f"ORDER BY n.created_at DESC LIMIT {args.limit}"
    )
    rows = _rows_from(g.ro_query(q))
    emit(args, rows, columns=["label", "name", "summary", "content"])


def cmd_graph_edges(args: argparse.Namespace) -> None:
    db = _falkordb(args)
    g = db.select_graph(_resolve_graph_name(args))
    q = (
        "MATCH (a)-[r:RELATES_TO]->(b) "
        "RETURN a.name AS source, r.name AS rel, b.name AS target, r.fact AS fact "
        f"ORDER BY r.created_at DESC LIMIT {args.limit}"
    )
    rows = _rows_from(g.ro_query(q))
    emit(args, rows, columns=["source", "rel", "target", "fact"])


def cmd_cypher(args: argparse.Namespace) -> None:
    """Server-side Cypher: respects namespace ACL, server-enforced timeout."""
    body: dict[str, Any] = {"query": args.query}
    if args.namespace:
        body["namespace"] = args.namespace
    if args.timeout_ms:
        body["timeout_ms"] = args.timeout_ms
    data = _request(args, "POST", "/v1/graph/cypher", json=body)
    if getattr(args, "json", False):
        _dump_json(data)
        return
    print(f"  namespace: {data['namespace']}  rows: {len(data['rows'])}")
    emit(args, data["rows"])


def cmd_admin_build_communities(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.namespace:
        params["namespace"] = args.namespace
    data = _request(args, "POST", "/v1/admin/communities/build", params=params)
    emit(args, data)


def cmd_entity_show(args: argparse.Namespace) -> None:
    data = _request(args, "GET", f"/v1/entities/{args.uuid}")
    emit(args, data)


def cmd_fact_show(args: argparse.Namespace) -> None:
    data = _request(args, "GET", f"/v1/facts/{args.uuid}")
    emit(args, data)


def cmd_fact_invalidate(args: argparse.Namespace) -> None:
    """PATCH /v1/facts/{uuid} — set invalid_at (default now) or reactivate."""
    if args.reactivate:
        body = {"invalid_at": None}
    else:
        when = args.at or datetime.now(UTC).isoformat()
        body = {"invalid_at": when}
    data = _request(args, "PATCH", f"/v1/facts/{args.uuid}", json=body)
    emit(args, data)


def cmd_fact_rm(args: argparse.Namespace) -> None:
    _request(args, "DELETE", f"/v1/facts/{args.uuid}")
    print(f"  deleted fact {args.uuid}")


def cmd_schema_create(args: argparse.Namespace) -> None:
    """Register an entity schema. Fields are space-separated `name:type` tokens
    (suffix `!` to mark required), e.g. `email:string! signup:datetime`.
    """
    fields = []
    for tok in args.fields or []:
        required = tok.endswith("!")
        bare = tok[:-1] if required else tok
        if ":" not in bare:
            die(f"bad field {tok!r}: must be name:type[!]")
        name, ftype = bare.split(":", 1)
        fields.append({"name": name, "type": ftype, "required": required})
    body = {"name": args.name, "description": args.description, "fields": fields}
    params: dict[str, Any] = {}
    if args.namespace:
        params["namespace"] = args.namespace
    data = _request(
        args, "POST", "/v1/schemas/entity-types", json=body, params=params
    )
    emit(args, data)


def cmd_schema_ls(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.namespace:
        params["namespace"] = args.namespace
    data = _request(args, "GET", "/v1/schemas/entity-types", params=params)
    if getattr(args, "json", False):
        _dump_json(data)
        return
    emit(args, data, columns=["name", "description", "created_at", "id"])


def cmd_schema_show(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.namespace:
        params["namespace"] = args.namespace
    data = _request(
        args, "GET", f"/v1/schemas/entity-types/{args.name}", params=params
    )
    emit(args, data)


def cmd_schema_rm(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.namespace:
        params["namespace"] = args.namespace
    _request(
        args, "DELETE", f"/v1/schemas/entity-types/{args.name}", params=params
    )
    print(f"  deleted schema {args.name}")


def cmd_saga_ls(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.namespace:
        params["namespace"] = args.namespace
    data = _request(args, "GET", "/v1/sagas", params=params)
    if getattr(args, "json", False):
        _dump_json(data)
        return
    print(f"  namespace: {data['namespace']}  sagas: {len(data['sagas'])}")
    emit(args, data["sagas"], columns=["name", "episode_count", "created_at", "uuid"])


def cmd_saga_show(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.namespace:
        params["namespace"] = args.namespace
    data = _request(args, "GET", f"/v1/sagas/{args.name_or_uuid}", params=params)
    if getattr(args, "json", False):
        _dump_json(data)
        return
    saga = data["saga"]
    print(f"  saga: {saga['name']}  ({saga['uuid']})")
    print(f"  episodes: {len(data['episodes'])}")
    if data["episodes"]:
        emit(args, data["episodes"], columns=["created_at", "content", "uuid"])


def cmd_graph_cypher(args: argparse.Namespace) -> None:
    db = _falkordb(args)
    g = db.select_graph(_resolve_graph_name(args))
    # `ro_query` rejects writes server-side — safer for an inspection tool.
    rows = _rows_from(g.ro_query(args.query))
    emit(args, rows)


# ---------- argparse glue ----------------------------------------------


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base-url", help="Override base URL (env: MEMCTL_BASE_URL)")
    p.add_argument("--key", help="API key for this invocation only")
    p.add_argument("--token", help="JWT for this invocation only")
    p.add_argument("--json", action="store_true", help="Emit raw API JSON")
    p.add_argument(
        "--falkordb-host",
        help="FalkorDB host for `graph` subcommands (env: FALKORDB_HOST)",
    )
    p.add_argument(
        "--falkordb-port",
        type=int,
        help="FalkorDB port for `graph` subcommands (env: FALKORDB_PORT, default 6380)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memctl",
        description="Terminal client for Memory Service.",
    )
    _add_common_flags(p)
    sub = p.add_subparsers(dest="cmd", required=True)

    # login / logout / whoami
    sp = sub.add_parser("login", help="Log in with email/password, store JWT")
    sp.add_argument("--email")
    sp.add_argument("--password")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("set-key", help="Save an API key into the config")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_set_key)

    sub.add_parser("logout", help="Wipe ~/.config/memctl/config.toml").set_defaults(
        func=cmd_logout
    )
    sub.add_parser("whoami", help="Show current user").set_defaults(func=cmd_whoami)

    # write / search / ls / show / rm
    sp = sub.add_parser("write", help="Add an episode")
    sp.add_argument("content")
    sp.add_argument("-n", "--namespace", help="user:me | group:<slug> | org:<slug>")
    sp.add_argument("--source", default="text", choices=["text", "message", "json"])
    sp.add_argument("--tags", nargs="*", help="Free-form tags")
    sp.add_argument("--saga", help="Saga name to chain this episode into")
    sp.add_argument(
        "--async",
        dest="async_extract",
        action="store_true",
        help="Return immediately; extraction runs in background. Poll status with `memctl status <id>`.",
    )
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser(
        "write-bulk",
        help="Bulk-write episodes (one per line of input)",
    )
    sp.add_argument(
        "--file", "-f",
        help="Path to a file of newline-separated content. '-' or omitted = stdin.",
    )
    sp.add_argument("-n", "--namespace")
    sp.add_argument("--source", default="text", choices=["text", "message", "json"])
    sp.add_argument("--tags", nargs="*")
    sp.add_argument("--saga", help="Saga name to chain all items into")
    sp.set_defaults(func=cmd_write_bulk)

    sp = sub.add_parser("search", help="Semantic search")
    sp.add_argument("query")
    sp.add_argument("-n", "--namespace", help="Comma-separated list to pin search")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument(
        "--as-of",
        dest="as_of",
        help="ISO-8601 instant; returns only facts active at that time",
    )
    sp.add_argument(
        "--edge-types",
        dest="edge_types",
        help="Comma-separated RELATES_TO names, e.g. LIVES_IN,TEACHES",
    )
    sp.add_argument(
        "--node-labels",
        dest="node_labels",
        help="Comma-separated entity labels, e.g. Person,Place",
    )
    sp.add_argument(
        "--center",
        help="Node UUID to bias ranking around",
    )
    sp.add_argument(
        "--bfs-origins",
        dest="bfs_origins",
        help="Comma-separated node UUIDs to BFS from",
    )
    sp.add_argument(
        "--bfs-max-depth",
        dest="bfs_max_depth",
        type=int,
        default=None,
        help="BFS hop limit (1-10, default 3)",
    )
    sp.add_argument(
        "--reranker",
        choices=["rrf", "mmr", "cross_encoder"],
        help="Override reranking strategy for this call",
    )
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("ls", help="List episodes")
    sp.add_argument("-n", "--namespace")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_ls)

    sp = sub.add_parser("show", help="Episode detail")
    sp.add_argument("episode_id")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("rm", help="Delete an episode")
    sp.add_argument("episode_id")
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("status", help="Extraction status of an episode")
    sp.add_argument("episode_id")
    sp.set_defaults(func=cmd_status)

    # api keys
    key = sub.add_parser("key", help="API key management").add_subparsers(
        dest="subcmd", required=True
    )
    sp = key.add_parser("create", help="Mint a new API key")
    sp.add_argument("--name", default="memctl", help="Agent label")
    sp.set_defaults(func=cmd_key_create)
    key.add_parser("ls", help="List your keys").set_defaults(func=cmd_key_ls)
    sp = key.add_parser("rm", help="Revoke a key")
    sp.add_argument("key_id")
    sp.set_defaults(func=cmd_key_rm)

    # orgs
    org = sub.add_parser("org", help="Organization management").add_subparsers(
        dest="subcmd", required=True
    )
    sp = org.add_parser("create", help="(super_admin) Create an org")
    sp.add_argument("slug")
    sp.add_argument("--name", required=True)
    sp.set_defaults(func=cmd_org_create)
    org.add_parser("ls", help="List orgs you can see").set_defaults(func=cmd_org_ls)
    sp = org.add_parser("rm", help="(super_admin) Delete an org")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_org_rm)

    member = org.add_parser("member", help="Org member management").add_subparsers(
        dest="member_cmd", required=True
    )
    sp = member.add_parser("add", help="Add a user to an org")
    sp.add_argument("slug")
    sp.add_argument("user_id")
    sp.add_argument("--admin", action="store_true", help="Make them org_admin")
    sp.set_defaults(func=cmd_org_member_add)
    sp = member.add_parser("ls", help="List members")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_org_member_ls)
    sp = member.add_parser("rm", help="Remove a user from an org")
    sp.add_argument("slug")
    sp.add_argument("user_id")
    sp.set_defaults(func=cmd_org_member_rm)

    # groups
    group = sub.add_parser("group", help="Group management").add_subparsers(
        dest="subcmd", required=True
    )
    sp = group.add_parser("create", help="Create a group inside an org")
    sp.add_argument("slug")
    sp.add_argument("--name", required=True)
    sp.add_argument("--org", help="(super_admin) Target a specific org")
    sp.set_defaults(func=cmd_group_create)
    group.add_parser("ls", help="List groups you can see").set_defaults(
        func=cmd_group_ls
    )
    sp = group.add_parser("rm", help="Delete a group")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_group_rm)

    gmember = group.add_parser(
        "member", help="Group member management"
    ).add_subparsers(dest="member_cmd", required=True)
    sp = gmember.add_parser("add", help="Add a user")
    sp.add_argument("slug")
    sp.add_argument("user_id")
    sp.add_argument("--admin", action="store_true", help="role=admin")
    sp.set_defaults(func=cmd_group_member_add)
    sp = gmember.add_parser("ls", help="List group members")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_group_member_ls)
    sp = gmember.add_parser("rm", help="Remove (or self-leave)")
    sp.add_argument("slug")
    sp.add_argument("user_id")
    sp.set_defaults(func=cmd_group_member_rm)

    # graph (FalkorDB inspection)
    graph = sub.add_parser(
        "graph", help="Inspect the FalkorDB graph directly"
    ).add_subparsers(dest="subcmd", required=True)
    graph.add_parser("list", help="All graphs in FalkorDB with node/edge counts").set_defaults(
        func=cmd_graph_list
    )
    sp = graph.add_parser("summary", help="Per-label counts for a namespace's graph")
    sp.add_argument("-n", "--namespace", help="user:me (default) | user:<id> | group:<slug> | ...")
    sp.set_defaults(func=cmd_graph_summary)
    sp = graph.add_parser("nodes", help="List nodes in a namespace's graph")
    sp.add_argument("-n", "--namespace")
    sp.add_argument("--label", help="Filter (Episodic | Entity | Community)")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_graph_nodes)
    sp = graph.add_parser("edges", help="List RELATES_TO edges (facts) in a namespace")
    sp.add_argument("-n", "--namespace")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_graph_edges)
    sp = graph.add_parser("cypher", help="Run an arbitrary read-only Cypher query")
    sp.add_argument("query")
    sp.add_argument("-n", "--namespace")
    sp.set_defaults(func=cmd_graph_cypher)

    # server-side cypher (ACL'd, timeout-bounded)
    sp = sub.add_parser(
        "cypher",
        help="Run read-only Cypher through the service (namespace-scoped, ACL'd)",
    )
    sp.add_argument("query")
    sp.add_argument("-n", "--namespace", help="Target namespace (default user:me)")
    sp.add_argument(
        "--timeout-ms",
        dest="timeout_ms",
        type=int,
        default=None,
        help="Server-side timeout (100-60000ms, default 10000)",
    )
    sp.set_defaults(func=cmd_cypher)

    # admin (graph maintenance jobs)
    admin = sub.add_parser("admin", help="Graph maintenance jobs").add_subparsers(
        dest="subcmd", required=True
    )
    communities = admin.add_parser(
        "communities", help="Entity-cluster (Community) management"
    ).add_subparsers(dest="comm_cmd", required=True)
    sp = communities.add_parser(
        "build", help="Run Graphiti's clustering on a namespace's entities"
    )
    sp.add_argument("-n", "--namespace", help="Target namespace (default user:me)")
    sp.set_defaults(func=cmd_admin_build_communities)

    # entity / fact lookup by uuid
    sp = sub.add_parser("entity", help="Show one entity by UUID")
    sp.add_argument("uuid")
    sp.set_defaults(func=cmd_entity_show)

    schema = sub.add_parser(
        "schema", help="Per-namespace entity-type schemas"
    ).add_subparsers(dest="schema_cmd", required=True)
    sp = schema.add_parser("create", help="Register or update an entity schema")
    sp.add_argument("name")
    sp.add_argument(
        "fields",
        nargs="*",
        help="Field defs: name:type or name:type! for required. "
        "Types: string, integer, number, boolean, datetime.",
    )
    sp.add_argument("--description")
    sp.add_argument("-n", "--namespace")
    sp.set_defaults(func=cmd_schema_create)
    sp = schema.add_parser("ls", help="List schemas in a namespace")
    sp.add_argument("-n", "--namespace")
    sp.set_defaults(func=cmd_schema_ls)
    sp = schema.add_parser("show", help="Show one schema")
    sp.add_argument("name")
    sp.add_argument("-n", "--namespace")
    sp.set_defaults(func=cmd_schema_show)
    sp = schema.add_parser("rm", help="Delete a schema")
    sp.add_argument("name")
    sp.add_argument("-n", "--namespace")
    sp.set_defaults(func=cmd_schema_rm)

    saga = sub.add_parser("saga", help="Inspect saga groupings").add_subparsers(
        dest="saga_cmd", required=True
    )
    sp = saga.add_parser("ls", help="List sagas in a namespace")
    sp.add_argument("-n", "--namespace")
    sp.set_defaults(func=cmd_saga_ls)
    sp = saga.add_parser("show", help="Show a saga + its episode chain")
    sp.add_argument("name_or_uuid")
    sp.add_argument("-n", "--namespace")
    sp.set_defaults(func=cmd_saga_show)

    fact = sub.add_parser(
        "fact", help="Operate on one RELATES_TO fact by UUID"
    )
    fact_sub = fact.add_subparsers(dest="fact_cmd", required=True)
    sp = fact_sub.add_parser("show", help="Show this fact")
    sp.add_argument("uuid")
    sp.set_defaults(func=cmd_fact_show)
    sp = fact_sub.add_parser(
        "invalidate", help="Mark this fact invalid_at (default: now)"
    )
    sp.add_argument("uuid")
    sp.add_argument("--at", help="ISO-8601 timestamp; default = now")
    sp.add_argument(
        "--reactivate",
        action="store_true",
        help="Clear invalid_at instead of setting it (undo an invalidation)",
    )
    sp.set_defaults(func=cmd_fact_invalidate)
    sp = fact_sub.add_parser("rm", help="Hard-delete this fact")
    sp.add_argument("uuid")
    sp.set_defaults(func=cmd_fact_rm)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except httpx.HTTPError as exc:
        die(f"network error: {exc}")


if __name__ == "__main__":
    main()

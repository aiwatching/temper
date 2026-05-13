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
    data = _request(args, "POST", "/v1/episodes", json=body)
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


def cmd_search(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {"query": args.query, "limit": args.limit}
    if args.namespace:
        params["namespaces"] = args.namespace
    data = _request(args, "GET", "/v1/search", params=params)
    if getattr(args, "json", False):
        _dump_json(data)
        return
    print(f"  query: {data['query']}  hits: {len(data['facts'])}")
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


# ---------- argparse glue ----------------------------------------------


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base-url", help="Override base URL (env: MEMCTL_BASE_URL)")
    p.add_argument("--key", help="API key for this invocation only")
    p.add_argument("--token", help="JWT for this invocation only")
    p.add_argument("--json", action="store_true", help="Emit raw API JSON")


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
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser("search", help="Semantic search")
    sp.add_argument("query")
    sp.add_argument("-n", "--namespace", help="Comma-separated list to pin search")
    sp.add_argument("--limit", type=int, default=10)
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

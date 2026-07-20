#!/usr/bin/env python3
"""Archive GitHub repository traffic and repository metadata.

GitHub only exposes the latest 14 days of views and clones. This collector runs
regularly and merges those overlapping windows into durable JSON history.
Popular paths and referrers are rolling snapshots, so they are archived as
snapshots instead of being added together.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data"


class GitHubAPI:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "github-repo-traffic-archive",
            }
        )

    def request(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        optional: bool = False,
    ) -> tuple[Any | None, requests.Response]:
        url = endpoint if endpoint.startswith("http") else f"{API_URL}{endpoint}"
        response: requests.Response | None = None
        for attempt in range(3):
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code not in {429, 502, 503, 504}:
                break
            time.sleep(2**attempt)

        assert response is not None
        if optional and response.status_code in {202, 403, 404, 409, 422}:
            return None, response
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None, response
        return response.json(), response

    def paginate(
        self, endpoint: str, *, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            query = dict(params or {})
            query.update({"per_page": 100, "page": page})
            payload, _ = self.request(endpoint, params=query)
            if not isinstance(payload, list):
                raise RuntimeError(f"Expected a list from {endpoint}")
            items.extend(payload)
            if len(payload) < 100:
                return items
            page += 1

    def count(self, endpoint: str) -> int | None:
        payload, response = self.request(
            endpoint, params={"per_page": 1, "page": 1}, optional=True
        )
        if payload is None:
            return None
        link = response.headers.get("Link", "")
        match = re.search(r"[?&]page=(\d+)>; rel=\"last\"", link)
        if match:
            return int(match.group(1))
        return len(payload) if isinstance(payload, list) else None


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    temporary.replace(path)


def repo_filename(name: str) -> str:
    return f"{quote(name, safe='._-')}.json"


def merge_timeline(
    old_items: list[dict[str, Any]], new_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = {
        item["timestamp"]: {
            "timestamp": item["timestamp"],
            "count": int(item.get("count", 0)),
            "uniques": int(item.get("uniques", 0)),
        }
        for item in old_items + new_items
        if item.get("timestamp")
    }
    return [merged[key] for key in sorted(merged)]


def upsert_daily_snapshot(
    snapshots: list[dict[str, Any]], captured_at: str, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    date = captured_at[:10]
    result = [item for item in snapshots if item.get("date") != date]
    result.append({"date": date, "captured_at": captured_at, "items": items})
    return sorted(result, key=lambda item: item["date"])


def upsert_metadata_history(
    history: list[dict[str, Any]], captured_at: str, repo: dict[str, Any]
) -> list[dict[str, Any]]:
    date = captured_at[:10]
    point = {
        "date": date,
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "watchers": repo.get("subscribers_count", 0),
        "open_issues_and_pulls": repo.get("open_issues_count", 0),
        "size_kb": repo.get("size", 0),
    }
    result = [item for item in history if item.get("date") != date]
    result.append(point)
    return sorted(result, key=lambda item: item["date"])


def compact_repo(repo: dict[str, Any]) -> dict[str, Any]:
    license_info = repo.get("license") or {}
    return {
        "id": repo.get("id"),
        "node_id": repo.get("node_id"),
        "name": repo.get("name"),
        "full_name": repo.get("full_name"),
        "html_url": repo.get("html_url"),
        "description": repo.get("description"),
        "homepage": repo.get("homepage"),
        "private": repo.get("private", False),
        "visibility": repo.get("visibility", "private" if repo.get("private") else "public"),
        "archived": repo.get("archived", False),
        "disabled": repo.get("disabled", False),
        "fork": repo.get("fork", False),
        "is_template": repo.get("is_template", False),
        "default_branch": repo.get("default_branch"),
        "language": repo.get("language"),
        "topics": repo.get("topics", []),
        "license": license_info.get("spdx_id"),
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "size_kb": repo.get("size", 0),
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "watchers": repo.get("subscribers_count", 0),
        "open_issues_and_pulls": repo.get("open_issues_count", 0),
        "has_issues": repo.get("has_issues", False),
        "has_projects": repo.get("has_projects", False),
        "has_wiki": repo.get("has_wiki", False),
        "has_pages": repo.get("has_pages", False),
        "has_discussions": repo.get("has_discussions", False),
    }


def optional_json(
    api: GitHubAPI,
    endpoint: str,
    errors: list[str],
    label: str,
    *,
    params: dict[str, Any] | None = None,
    default: Any = None,
) -> Any:
    try:
        payload, response = api.request(endpoint, params=params, optional=True)
        if payload is None and response.status_code not in {202, 404, 409}:
            errors.append(f"{label}: HTTP {response.status_code}")
        return default if payload is None else payload
    except requests.RequestException as exc:
        errors.append(f"{label}: {exc}")
        return default


def collect_repository(
    api: GitHubAPI,
    owner: str,
    listed_repo: dict[str, Any],
    data_dir: Path,
    captured_at: str,
) -> dict[str, Any]:
    name = listed_repo["name"]
    encoded_owner = quote(owner, safe="")
    encoded_name = quote(name, safe="")
    base = f"/repos/{encoded_owner}/{encoded_name}"
    path = data_dir / "repositories" / repo_filename(name)
    previous = read_json(path, {})
    errors: list[str] = []

    repo = optional_json(api, base, errors, "repository", default=listed_repo)
    repository = compact_repo(repo)
    languages = optional_json(api, f"{base}/languages", errors, "languages", default={})
    community = optional_json(
        api, f"{base}/community/profile", errors, "community profile", default={}
    )
    participation = optional_json(
        api, f"{base}/stats/participation", errors, "participation", default={}
    )

    counts: dict[str, int | None] = {}
    count_endpoints = {
        "contributors": f"{base}/contributors?anon=true",
        "branches": f"{base}/branches",
        "tags": f"{base}/tags",
        "releases": f"{base}/releases",
        "open_pull_requests": f"{base}/pulls?state=open",
    }
    for label, endpoint in count_endpoints.items():
        try:
            counts[label] = api.count(endpoint)
        except requests.RequestException as exc:
            counts[label] = None
            errors.append(f"{label}: {exc}")

    old_traffic = previous.get("traffic", {})
    traffic: dict[str, Any] = {
        "views": old_traffic.get("views", []),
        "clones": old_traffic.get("clones", []),
        "popular_paths": old_traffic.get("popular_paths", []),
        "popular_referrers": old_traffic.get("popular_referrers", []),
    }

    views = optional_json(
        api,
        f"{base}/traffic/views",
        errors,
        "traffic views",
        params={"per": "day"},
        default={},
    )
    if views.get("views") is not None:
        traffic["views"] = merge_timeline(traffic["views"], views["views"])

    clones = optional_json(
        api,
        f"{base}/traffic/clones",
        errors,
        "traffic clones",
        params={"per": "day"},
        default={},
    )
    if clones.get("clones") is not None:
        traffic["clones"] = merge_timeline(traffic["clones"], clones["clones"])

    paths = optional_json(
        api, f"{base}/traffic/popular/paths", errors, "popular paths", default=None
    )
    if paths is not None:
        traffic["popular_paths"] = upsert_daily_snapshot(
            traffic["popular_paths"], captured_at, paths
        )

    referrers = optional_json(
        api,
        f"{base}/traffic/popular/referrers",
        errors,
        "popular referrers",
        default=None,
    )
    if referrers is not None:
        traffic["popular_referrers"] = upsert_daily_snapshot(
            traffic["popular_referrers"], captured_at, referrers
        )

    payload = {
        "schema_version": 1,
        "owner": owner,
        "repository": repository,
        "collected_at": captured_at,
        "metadata_history": upsert_metadata_history(
            previous.get("metadata_history", []), captured_at, repo
        ),
        "languages": languages,
        "community": community,
        "counts": counts,
        "participation": participation,
        "traffic": traffic,
        "collection_errors": errors,
    }
    write_json(path, payload)
    return payload


def timeline_totals(items: list[dict[str, Any]], since: str | None = None) -> dict[str, int]:
    selected = [item for item in items if since is None or item["timestamp"][:10] >= since]
    return {
        "count": sum(int(item.get("count", 0)) for item in selected),
        "uniques": sum(int(item.get("uniques", 0)) for item in selected),
    }


def make_summary(owner: str, captured_at: str, repos: list[dict[str, Any]]) -> dict[str, Any]:
    since_14d = (datetime.fromisoformat(captured_at) - timedelta(days=13)).date().isoformat()
    summaries: list[dict[str, Any]] = []
    for payload in repos:
        repository = dict(payload["repository"])
        traffic = payload["traffic"]
        repository.update(
            {
                "languages": payload.get("languages", {}),
                "participation_52_weeks": payload.get("participation", {}).get("all", []),
                "community_health_percentage": payload.get("community", {}).get(
                    "health_percentage"
                ),
                "counts": payload.get("counts", {}),
                "traffic": {
                    "views_all_time": timeline_totals(traffic.get("views", [])),
                    "views_14d": timeline_totals(traffic.get("views", []), since_14d),
                    "clones_all_time": timeline_totals(traffic.get("clones", [])),
                    "clones_14d": timeline_totals(traffic.get("clones", []), since_14d),
                    "first_archived_date": min(
                        [item["timestamp"][:10] for item in traffic.get("views", [])]
                        + [item["timestamp"][:10] for item in traffic.get("clones", [])],
                        default=None,
                    ),
                    "latest_popular_paths": (traffic.get("popular_paths") or [{}])[-1].get(
                        "items", []
                    ),
                    "latest_popular_referrers": (
                        traffic.get("popular_referrers") or [{}]
                    )[-1].get("items", []),
                },
                "data_file": f"data/repositories/{repo_filename(repository['name'])}",
                "collection_errors": payload.get("collection_errors", []),
            }
        )
        summaries.append(repository)

    summaries.sort(key=lambda item: item["name"].lower())
    return {
        "schema_version": 1,
        "owner": owner,
        "generated_at": captured_at,
        "repository_count": len(summaries),
        "repositories": summaries,
    }


def discover_owner(api: GitHubAPI, configured_owner: str | None) -> tuple[str, str]:
    user, _ = api.request("/user")
    login = user["login"]
    return (configured_owner or login), login


def list_owned_repositories(api: GitHubAPI, owner: str, login: str) -> list[dict[str, Any]]:
    if owner.lower() == login.lower():
        repos = api.paginate(
            "/user/repos",
            params={
                "affiliation": "owner",
                "visibility": "all",
                "sort": "full_name",
                "direction": "asc",
            },
        )
        return [
            repo
            for repo in repos
            if repo.get("owner", {}).get("login", "").lower() == owner.lower()
        ]
    return api.paginate(
        f"/users/{quote(owner, safe='')}/repos",
        params={"type": "owner", "sort": "full_name", "direction": "asc"},
    )


def validate_data(data_dir: Path) -> int:
    summary_path = data_dir / "dashboard.json"
    summary = read_json(summary_path, None)
    if not isinstance(summary, dict) or not isinstance(summary.get("repositories"), list):
        print(f"Invalid or missing {summary_path}", file=sys.stderr)
        return 1
    for repo in summary["repositories"]:
        path = ROOT / repo["data_file"]
        payload = read_json(path, None)
        if not payload or payload.get("repository", {}).get("name") != repo.get("name"):
            print(f"Invalid repository data: {path}", file=sys.stderr)
            return 1
    print(f"Validated {len(summary['repositories'])} repositories")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--validate", action="store_true", help="validate existing data only")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    if args.validate:
        return validate_data(data_dir)

    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        parser.error("GH_TOKEN is required (use a fine-grained PAT with access to every repository)")

    api = GitHubAPI(token)
    owner, login = discover_owner(api, os.getenv("GITHUB_OWNER"))
    repositories = list_owned_repositories(api, owner, login)
    if not repositories:
        raise RuntimeError(f"No repositories visible for owner {owner!r}")

    captured_at = utc_now().isoformat().replace("+00:00", "Z")
    collected: list[dict[str, Any]] = []
    for index, repo in enumerate(repositories, start=1):
        print(f"[{index}/{len(repositories)}] Collecting {repo['full_name']}", flush=True)
        try:
            collected.append(
                collect_repository(api, owner, repo, data_dir, captured_at)
            )
        except requests.RequestException as exc:
            print(f"  Failed: {exc}", file=sys.stderr)

    if not collected:
        raise RuntimeError("All repository collections failed")

    summary = make_summary(owner, captured_at, collected)
    write_json(data_dir / "dashboard.json", summary)
    error_count = sum(bool(repo.get("collection_errors")) for repo in collected)
    print(
        f"Archived {len(collected)} repositories for {owner}; "
        f"{error_count} repositories had one or more optional endpoint errors."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

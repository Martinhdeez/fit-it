"""Feature marketplace: publish, search, subscribe, update, fork, report. Spec: docs/specs/marketplace.md."""
import db
import hooks
import trace

STATUS = {"not_found": 404, "forbidden": 403, "own_feature": 409, "already_subscribed": 409, "up_to_date": 409,
          "name_taken": 409, "invalid": 422, "dry_run_failed": 422}


class Error(Exception):
    def __init__(self, error: str, detail: str):
        super().__init__(detail)
        self.error = error
        self.status = STATUS[error]


# One row per feature as seen by %(user)s; the latest version's route, code and time.
FEATURE_SQL = """
SELECT f.id, f.title, f.description, v.route, f.owner_id, f.version,
       (SELECT count(*) FROM subscriptions s WHERE s.feature_id = f.id) AS subscribers,
       v.published_at, f.owner_id = %(user)s AS mine,
       EXISTS (SELECT 1 FROM subscriptions s WHERE s.feature_id = f.id AND s.user_id = %(user)s) AS subscribed,
       f.published, v.code, f.hook_name
FROM features f JOIN feature_versions v ON v.feature_id = f.id AND v.version = f.version
"""
LISTING_KEYS = ("id", "title", "description", "route", "owner", "version", "subscribers", "published_at", "mine",
                "subscribed")

SUBSCRIPTION_SQL = """
SELECT s.feature_id, f.title, v.route, f.owner_id, s.version, f.version, s.enabled, f.published, v.code
FROM subscriptions s JOIN features f ON f.id = s.feature_id
JOIN feature_versions v ON v.feature_id = s.feature_id AND v.version = s.version
WHERE s.user_id = %(user)s
"""


def _feature(conn, user: str, feature_id: int) -> dict:
    """The feature as a dict with every detail key plus `hook`; not_found unless visible to `user`."""
    row = conn.execute(FEATURE_SQL + " WHERE f.id = %(id)s", {"user": user, "id": feature_id}).fetchone()
    if row is None or not (row[10] or row[8] or row[9]):
        raise Error("not_found", f"no feature {feature_id}")
    entry = dict(zip(LISTING_KEYS + ("published", "code", "hook"), row))
    entry["published_at"] = entry["published_at"].isoformat()
    return entry


def _subscription(row) -> dict:
    feature_id, title, route, owner, version, latest, enabled, published, _ = row
    return {"feature_id": feature_id, "name": f"@{feature_id}", "title": title, "route": route, "owner": owner,
            "version": version, "latest_version": latest, "update_available": latest > version,
            "enabled": enabled, "published": published}


def _subscribed(conn, user: str, feature_id: int):
    return conn.execute(SUBSCRIPTION_SQL + " AND s.feature_id = %(id)s", {"user": user, "id": feature_id}).fetchone()


def _text(field: str, value: str, limit: int) -> str:
    value = value.strip()
    if not 1 <= len(value) <= limit:
        raise Error("invalid", f"{field} must be 1 to {limit} characters")
    return value


def _dry_run(user: str, code: str, api, feature_id: int | None = None) -> None:
    """Dry-run `code` as `user` on data chosen as the agent's write_hook does (marketplace spec, "Dry run").
    With `feature_id`, inside the user's whole chain for the route, the feature in its place."""
    chain = {} if feature_id is None else {"name": f"@{feature_id}", "features": active(user)}
    try:
        method, path = hooks.check_code(code)
        if path.startswith("/x/"):
            hooks.dry_run(user, code, None, api, **chain)
        elif method == "GET" and "{" not in path:
            hooks.dry_run(user, code, api.get(path), api, **chain)
    except hooks.HookError as e:
        raise Error("dry_run_failed", str(e) if e.hook is None else f"{e.hook} failed: {e}")
    except Exception as e:
        raise Error("dry_run_failed", str(e))


def publish(user: str, hook: str, title: str, description: str) -> dict:
    title, description = _text("title", title, 80), _text("description", description, 500)
    try:
        code = hooks.read_hook(user, hook)
    except KeyError:
        raise Error("not_found", f"no hook named '{hook}'")
    try:
        route = " ".join(hooks.check_code(code))
    except ValueError as e:
        raise Error("invalid", str(e))
    with db.connect() as conn:
        row = conn.execute("SELECT f.id, f.version, v.code FROM features f JOIN feature_versions v"
                           " ON v.feature_id = f.id AND v.version = f.version"
                           " WHERE f.owner_id = %s AND f.hook_name = %s FOR UPDATE OF f", (user, hook)).fetchone()
        created = row is None
        if created:
            (feature_id,) = conn.execute(
                "INSERT INTO features (owner_id, hook_name, title, description, version) VALUES (%s, %s, %s, %s, 1)"
                " RETURNING id", (user, hook, title, description)).fetchone()
            version = 1
        else:
            feature_id, version, latest_code = row
            if latest_code != code:
                version += 1
            conn.execute("UPDATE features SET title = %s, description = %s, published = true, version = %s"
                         " WHERE id = %s", (title, description, version, feature_id))
        if created or version != row[1]:
            conn.execute("INSERT INTO feature_versions (feature_id, version, code, route) VALUES (%s, %s, %s, %s)",
                         (feature_id, version, code, route))
    trace.emit("feature.published", user=user, feature_id=feature_id, hook=hook, version=version, created=created)
    return {"id": feature_id, "version": version, "created": created}


def unpublish(user: str, feature_id: int) -> None:
    with db.connect() as conn:
        if not _feature(conn, user, feature_id)["mine"]:
            raise Error("forbidden", "only the owner can unpublish a feature")
        conn.execute("UPDATE features SET published = false WHERE id = %s", (feature_id,))
    trace.emit("feature.unpublished", user=user, feature_id=feature_id)


def search(user: str, q: str = "", sort: str = "subscribers") -> list[dict]:
    # ponytail: substring match per word, 50 results, no pagination; full-text search when listings grow (ADR 0022)
    order = {"subscribers": "subscribers DESC, f.id DESC", "newest": "v.published_at DESC, f.id DESC"}.get(sort)
    if order is None:
        raise Error("invalid", "sort must be 'subscribers' or 'newest'")
    words = q.split()
    where = "".join(f" AND (strpos(lower(f.title), lower(%(w{i})s)) > 0"
                    f" OR strpos(lower(f.description), lower(%(w{i})s)) > 0)" for i in range(len(words)))
    params = {"user": user, **{f"w{i}": w for i, w in enumerate(words)}}
    with db.connect() as conn:
        rows = conn.execute(FEATURE_SQL + " WHERE f.published" + where + " ORDER BY " + order + " LIMIT 50",
                            params).fetchall()
    return [{**dict(zip(LISTING_KEYS, row)), "published_at": row[7].isoformat()} for row in rows]


def get_feature(user: str, feature_id: int) -> dict:
    with db.connect() as conn:
        entry = _feature(conn, user, feature_id)
    del entry["hook"]
    return entry


def subscribe(user: str, feature_id: int, api) -> dict:
    with db.connect() as conn:
        feature = _feature(conn, user, feature_id)
    if not feature["published"]:
        raise Error("not_found", f"feature {feature_id} is not published")
    if feature["mine"]:
        raise Error("own_feature", "you cannot subscribe to your own feature")
    if feature["subscribed"]:
        raise Error("already_subscribed", f"already subscribed to feature {feature_id}")
    _dry_run(user, feature["code"], api, feature_id)
    with db.connect() as conn:
        if not conn.execute("INSERT INTO subscriptions (user_id, feature_id, version) VALUES (%s, %s, %s)"
                            " ON CONFLICT DO NOTHING", (user, feature_id, feature["version"])).rowcount:
            raise Error("already_subscribed", f"already subscribed to feature {feature_id}")
        entry = _subscription(_subscribed(conn, user, feature_id))
    trace.emit("feature.subscribed", user=user, feature_id=feature_id, version=feature["version"])
    return entry


def toggle(user: str, feature_id: int) -> bool:
    with db.connect() as conn:
        row = conn.execute("UPDATE subscriptions SET enabled = NOT enabled WHERE user_id = %s AND feature_id = %s"
                           " RETURNING enabled", (user, feature_id)).fetchone()
    if row is None:
        raise Error("not_found", f"not subscribed to feature {feature_id}")
    trace.emit("feature.toggled", user=user, feature_id=feature_id, enabled=row[0])
    return row[0]


def unsubscribe(user: str, feature_id: int) -> None:
    with db.connect() as conn:
        if not conn.execute("DELETE FROM subscriptions WHERE user_id = %s AND feature_id = %s",
                            (user, feature_id)).rowcount:
            raise Error("not_found", f"not subscribed to feature {feature_id}")
    trace.emit("feature.unsubscribed", user=user, feature_id=feature_id)


def accept_update(user: str, feature_id: int, api) -> dict:
    with db.connect() as conn:
        row = _subscribed(conn, user, feature_id)
        if row is None:
            raise Error("not_found", f"not subscribed to feature {feature_id}")
        pinned, latest = row[4], row[5]
        if latest <= pinned:
            raise Error("up_to_date", f"already on the latest version {latest}")
        (code,) = conn.execute("SELECT code FROM feature_versions WHERE feature_id = %s AND version = %s",
                               (feature_id, latest)).fetchone()
    _dry_run(user, code, api, feature_id)
    with db.connect() as conn:
        conn.execute("UPDATE subscriptions SET version = %s WHERE user_id = %s AND feature_id = %s",
                     (latest, user, feature_id))
        row = _subscribed(conn, user, feature_id)
    if row is None:
        raise Error("not_found", f"not subscribed to feature {feature_id}")
    trace.emit("feature.updated", user=user, feature_id=feature_id, version=latest)
    return _subscription(row)


def fork(user: str, feature_id: int, api, name: str | None = None) -> dict:
    with db.connect() as conn:
        feature = _feature(conn, user, feature_id)
        pinned = _subscribed(conn, user, feature_id)
    if feature["mine"]:
        raise Error("own_feature", "you cannot fork your own feature")
    name = feature["hook"] if name is None else name
    code, version = (pinned[8], pinned[4]) if pinned else (feature["code"], feature["version"])
    try:
        hooks.read_hook(user, name)
        raise Error("name_taken", f"you already have a hook named '{name}'")
    except KeyError:
        pass
    _dry_run(user, code, api)
    # ponytail: the hook save and the unsubscribe are two transactions (save_hook owns its own); a crash between
    # them leaves both, never neither. One transaction needs a connection parameter on hooks.save_hook.
    try:
        diff = hooks.save_hook(user, name, code)
    except ValueError as e:
        raise Error("invalid", str(e))
    with db.connect() as conn:
        conn.execute("DELETE FROM subscriptions WHERE user_id = %s AND feature_id = %s", (user, feature_id))
    route = " ".join(hooks.check_code(code))
    trace.emit("hook.saved", user=user, hook=name, route=route, diff=diff)
    trace.emit("feature.forked", user=user, feature_id=feature_id, version=version, hook=name)
    return {"name": name, "route": route, "diff": diff}


def report(user: str, feature_id: int, reason: str) -> None:
    reason = _text("reason", reason, 500)
    with db.connect() as conn:
        _feature(conn, user, feature_id)
        conn.execute("INSERT INTO reports (feature_id, user_id, reason) VALUES (%s, %s, %s)",
                     (feature_id, user, reason))
    trace.emit("feature.reported", user=user, feature_id=feature_id, reason=reason)


def _subscription_rows(user: str, where: str = "") -> list:
    with db.connect() as conn:
        return conn.execute(SUBSCRIPTION_SQL + where + " ORDER BY s.id", {"user": user}).fetchall()


def subscriptions(user: str) -> list[dict]:
    return [_subscription(row) for row in _subscription_rows(user)]


def active(user: str) -> list[dict]:
    return [{"name": f"@{row[0]}", "code": row[8]} for row in _subscription_rows(user, " AND s.enabled")]


def my_features(user: str) -> list[dict]:
    with db.connect() as conn:
        published = {name: (fid, pub, version) for name, fid, pub, version in conn.execute(
            "SELECT hook_name, id, published, version FROM features WHERE owner_id = %s", (user,)).fetchall()}
    own = []
    for hook in hooks.list_hooks(user):
        fid, pub, version = published.get(hook["name"], (None, False, None))
        own.append({"source": "own", **hook, "feature_id": fid, "published": pub, "published_version": version})
    return [{"source": "subscribed", **entry} for entry in subscriptions(user)] + own

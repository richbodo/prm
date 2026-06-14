"""core/candidates.py — stdlib duplicate-candidate detection (``find_duplicate_candidates``).

The deterministic half of dedupe: **normalize → block → score (tiered) → cluster (guarded)**, using
only the standard library (``difflib``, ``unicodedata``, ``re``, ``hashlib`` — no numpy/scikit/ML).
Operates on the canonical contacts of ``core.projection``, so it proposes which *contacts* to merge.

Tiers (docs/design-notes/dedupe-design.md): **confident** (exact normalized email/phone, or LinkedIn
profile-URL), **strong** (a corroborated near-match worth a look), **fuzzy** (name-only — the Facebook
case). Honors **AC-PRM-B**: name-only / name+company never enter the auto-clustered set; only confident
edges are unioned, and even those are size- and cohesion-guarded against transitive over-merge.
Returns candidate clusters; nothing is applied (that's M3c). Shared with the MCP surface (M4).
"""

from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata

from core import relationships_db, projection

CONFIDENT, STRONG, FUZZY, REVIEW = "confident", "strong", "fuzzy", "review"
# A guarded (oversized/low-cohesion) confident cluster is downgraded to REVIEW — still high priority,
# but flagged so the reviewer doesn't trust it as a clean merge.
_TIER_ORDER = {CONFIDENT: 0, REVIEW: 1, STRONG: 2, FUZZY: 3}

# A small, deliberately short nickname table — enough to catch the common English variants without a
# dependency. Extended as real exports motivate it.
_NICKNAMES = {
    "bob": "robert", "bobby": "robert", "rob": "robert", "bill": "william", "will": "william",
    "billy": "william", "dick": "richard", "rick": "richard", "jim": "james", "jimmy": "james",
    "tom": "thomas", "tommy": "thomas", "tony": "anthony", "kate": "katherine", "katie": "katherine",
    "kathy": "katherine", "liz": "elizabeth", "beth": "elizabeth", "betty": "elizabeth",
    "peggy": "margaret", "meg": "margaret", "maggie": "margaret", "ed": "edward", "ted": "edward",
    "ben": "benjamin", "mike": "michael", "nick": "nicholas", "chris": "christopher", "dan": "daniel",
    "danny": "daniel", "joe": "joseph", "sam": "samuel", "alex": "alexander", "andy": "andrew",
    "matt": "matthew", "steve": "stephen", "greg": "gregory", "jon": "jonathan", "dave": "david",
}
_SOUNDEX = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"),
            **dict.fromkeys("dt", "3"), "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}


# --------------------------------------------------------------------------- normalization
def fold(s: str) -> str:
    """Accent-fold (NFKD, drop combining marks) + casefold + collapse whitespace."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.casefold()).strip()


def normalize_email(e: str) -> str:
    e = fold(e).replace(" ", "")
    if "@" not in e:
        return ""
    local, _, domain = e.partition("@")
    local = local.split("+", 1)[0]                     # strip +tag
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"   # gmail dots/alias canonicalization
    return f"{local}@{domain}" if local and domain else ""


def normalize_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p or "")
    return digits[-10:] if len(digits) >= 7 else ""    # last-10 match key (E.164 proxy without a lib)


def name_tokens(name: str) -> list[str]:
    s = fold(name).replace("'", "").replace("’", "")     # keep O'Brien / D'Angelo as one token
    return [_NICKNAMES.get(t, t) for t in re.findall(r"[a-z0-9]+", s)]


def soundex(word: str) -> str:
    word = re.sub(r"[^a-z]", "", fold(word))
    if not word:
        return ""
    out, prev = word[0].upper(), _SOUNDEX.get(word[0], "")
    for ch in word[1:]:
        code = _SOUNDEX.get(ch, "")
        if code and code != prev:
            out += code
        if ch not in "hw":
            prev = code
        if len(out) >= 4:
            break
    return (out + "000")[:4]


def _linkedin_slug(url: str) -> str:
    m = re.search(r"linkedin\.com/in/([^/?#]+)", fold(url))
    return m.group(1) if m else ""


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


# --------------------------------------------------------------------------- matchable contacts
def _matchable(contact: dict) -> dict:
    emails, phones, slugs, org = set(), set(), set(), ""
    for f in contact.get("fields", []):
        name = f["name"]
        for v in f.get("values", []):
            val = v.get("value", "")
            if name == "email":
                if (e := normalize_email(val)):
                    emails.add(e)
            elif name == "tel":
                if (p := normalize_phone(val)):
                    phones.add(p)
            elif name == "url":
                if (s := _linkedin_slug(val)):
                    slugs.add(s)
            elif name == "org" and not org:
                org = fold(val)
    toks = name_tokens(contact.get("fn", ""))
    return {
        "id": contact["id"], "name": contact.get("fn", ""), "source": contact.get("source", ""),
        "email": contact.get("email", ""), "fold": fold(contact.get("fn", "")), "tokens": toks,
        "surname": toks[-1] if toks else "", "emails": emails, "phones": phones, "slugs": slugs, "org": org,
    }


def blocking_keys(m: dict) -> set:
    """Cheap keys that put plausibly-matching contacts in the same bucket (sub-O(n²))."""
    keys = {("email", e) for e in m["emails"]} | {("phone", p) for p in m["phones"]} \
        | {("url", s) for s in m["slugs"]}
    if m["fold"]:
        keys.add(("ntok", " ".join(sorted(m["tokens"]))))
        if m["surname"]:
            keys.add(("name5", m["surname"][:5]))
            keys.add(("phon", soundex(m["surname"])))
    return keys


# --------------------------------------------------------------------------- scoring
def score(a: dict, b: dict) -> tuple[str | None, list[str]]:
    """Tier + signals for a candidate pair, or ``(None, [])`` if not a candidate."""
    shared_email = a["emails"] & b["emails"]
    shared_phone = a["phones"] & b["phones"]
    shared_slug = a["slugs"] & b["slugs"]
    nr = _ratio(a["fold"], b["fold"])
    diff_phones = bool(a["phones"]) and bool(b["phones"]) and not shared_phone

    if shared_slug:
        return CONFIDENT, ["profile_url"]
    if shared_email:
        if diff_phones and nr < 0.4:                    # shared inbox, clearly different people → review
            return STRONG, ["shared_email_conflict"]
        return CONFIDENT, ["email_exact"] + (["phone_exact"] if shared_phone else [])
    if shared_phone:
        return (CONFIDENT, ["phone+name"]) if nr >= 0.6 else (STRONG, ["phone_exact"])
    same_surname = bool(a["surname"]) and a["surname"] == b["surname"]
    if same_surname and nr >= 0.85 and a["org"] and a["org"] == b["org"]:
        return STRONG, ["name+org"]
    if nr >= 0.85:
        return FUZZY, ["name_similar"]
    return None, []


# --------------------------------------------------------------------------- clustering
def _union_find(edges: list[tuple[str, str]]) -> list[set]:
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:        # path compression
            parent[x], x = root, parent[x]
        return root

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict[str, set] = {}
    for x in list(parent):
        groups.setdefault(find(x), set()).add(x)
    return list(groups.values())


def cluster_key(ids) -> str:
    return hashlib.sha1("|".join(sorted(ids)).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- public detector
def detect(contacts: list[dict], *, rejected: set | None = None, max_cluster: int = 5,
           cohesion: float = 0.5) -> list[dict]:
    """Find duplicate-candidate clusters among already-projected canonical contacts.

    Only **confident** edges are unioned into clusters; **strong**/**fuzzy** matches are surfaced as
    standalone pairs (never auto-linked — AC-PRM-B). Confident clusters are guarded: a cluster larger
    than ``max_cluster`` or whose members don't all clear a relaxed name ``cohesion`` ratio is tagged
    ``oversized`` for the reviewer rather than presented as a clean merge.
    """
    rejected = rejected or set()
    ms = [_matchable(c) for c in contacts]
    by_id = {m["id"]: m for m in ms}

    # block → candidate pairs (dedup unordered)
    buckets: dict[tuple, list[str]] = {}
    for m in ms:
        for k in blocking_keys(m):
            buckets.setdefault(k, []).append(m["id"])
    seen_pairs: set[tuple[str, str]] = set()
    scored: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for ids in buckets.values():
        if len(ids) < 2:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pair = tuple(sorted((ids[i], ids[j])))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                tier, signals = score(by_id[pair[0]], by_id[pair[1]])
                if tier:
                    scored[pair] = (tier, signals)

    confident_edges = [p for p, (t, _) in scored.items() if t == CONFIDENT]
    clusters: list[dict] = []

    # confident → union-find clusters (guarded)
    for members in _union_find(confident_edges):
        if len(members) < 2:
            continue
        ids = sorted(members)
        key = cluster_key(ids)
        if key in rejected:
            continue
        folds = [by_id[i]["fold"] for i in ids]
        cohesive = all(_ratio(folds[i], folds[j]) >= cohesion
                       for i in range(len(folds)) for j in range(i + 1, len(folds)))
        signals = sorted({s for p, (t, sg) in scored.items()
                          if t == CONFIDENT and p[0] in members and p[1] in members for s in sg})
        clusters.append(_cluster(ids, by_id, CONFIDENT, signals,
                                 oversized=len(ids) > max_cluster or not cohesive))

    # strong / fuzzy → standalone pairs (not auto-linked)
    clustered = {i for c in clusters for i in c["member_ids"]}
    for pair, (tier, signals) in scored.items():
        if tier == CONFIDENT or pair[0] in clustered or pair[1] in clustered:
            continue
        key = cluster_key(pair)
        if key in rejected:
            continue
        clusters.append(_cluster(list(pair), by_id, tier, signals, oversized=False))

    clusters.sort(key=lambda c: (_TIER_ORDER[c["tier"]], c["key"]))
    return clusters


def _cluster(ids: list[str], by_id: dict, tier: str, signals: list[str], *, oversized: bool) -> dict:
    return {
        "key": cluster_key(ids),
        "tier": REVIEW if oversized else tier,
        "signals": signals + (["oversized"] if oversized else []),
        "member_ids": ids,
        "members": [{"id": i, "name": by_id[i]["name"], "email": by_id[i]["email"],
                     "source": by_id[i]["source"]} for i in ids],
    }


def find_duplicate_candidates(home) -> list[dict]:
    """Detect duplicate-candidate clusters across the home's canonical contacts (the projection),
    excluding any the user has dismissed. This is the ``find_duplicate_candidates`` of plan §6 —
    used by the workspace (M3d) and exposed over MCP (M4)."""
    contacts = projection.all_contacts(home)
    rejected = relationships_db.rejected_pairs(home.relationships_db) if home.relationships_db.exists() else set()
    return detect(contacts, rejected=rejected)

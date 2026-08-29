import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.resolver.entity_resolver import EntityResolver  # noqa: E402

SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "canonical_seed.json")


def load_resolver():
    with open(SEED_PATH) as f:
        seed = json.load(f)
    return EntityResolver(canonical_seed=seed)


def test_exact_alias_match():
    r = load_resolver()
    canonical, method, conf = r.resolve("Open AI")
    assert canonical == "OpenAI"
    assert method == "exact"
    assert conf == 1.0


def test_normalized_match_strips_inc():
    r = load_resolver()
    canonical, method, conf = r.resolve("OpenAI, Inc.")
    assert canonical == "OpenAI"


def test_fuzzy_match_typo():
    r = load_resolver()
    canonical, method, conf = r.resolve("Antrhopic")  # typo
    assert canonical == "Anthropic"
    assert method == "fuzzy"
    assert conf >= 0.87


def test_unresolved_unknown_entity():
    r = load_resolver()
    canonical, method, conf = r.resolve("Some Totally Unknown Startup Xyz123")
    assert method == "unresolved"
    assert conf == 0.0


def test_dynamic_registration():
    r = load_resolver()
    r.register_new_canonical("Brand New Startup")
    canonical, method, conf = r.resolve("Brand New Startup")
    assert canonical == "Brand New Startup"
    assert method == "exact"


if __name__ == "__main__":
    test_exact_alias_match()
    test_normalized_match_strips_inc()
    test_fuzzy_match_typo()
    test_unresolved_unknown_entity()
    test_dynamic_registration()
    print("All entity resolver tests passed.")

from hashlib import sha256

import pytest

from backend.agent.prompts import build_system_prompt, build_user_prompt
from backend.config import AgentSettings


def test_default_prompt_profile(monkeypatch):
    monkeypatch.setattr("repo_service.env.ENV_FILES", ())
    monkeypatch.delenv("FUNCGUARD_AGENT_PROMPT_PROFILE", raising=False)
    assert AgentSettings.from_env().prompt_profile == "retrieval_first"


def test_unknown_prompt_profile_is_rejected(monkeypatch):
    monkeypatch.setenv("FUNCGUARD_AGENT_PROMPT_PROFILE", "invalid")
    with pytest.raises(ValueError, match="FUNCGUARD_AGENT_PROMPT_PROFILE"):
        AgentSettings.from_env()


def test_legacy_prompts_match_the_original_bytes():
    # 这些哈希在修改提示词之前采集，并覆盖有无离线定位路径两种用户提示。
    system = build_system_prompt("legacy", knowledge_available=True)
    assert sha256(system.encode("utf-8")).hexdigest() == (
        "b8d9f6007546e7e5c6975f7b095d096a72a976a051e12aab74f3ff40bed55048"
    )
    args = (42, "https://example.test/commit/42", "  int f(void) { return 0; }  ", "snap")
    for path, digest in [
        (None, "27c2911b7c2b54abd88d5ec9b82096412fdff6ea72fdb8c113138aacf3726643"),
        ("src/a.c", "c85ee4b78a6524e84db5d5d378cdf09e77dc1e2cf8c22f4745a6cc4f68e76e21"),
    ]:
        user = build_user_prompt(*args, locate_path=path, profile="legacy")
        assert sha256(user.encode("utf-8")).hexdigest() == digest

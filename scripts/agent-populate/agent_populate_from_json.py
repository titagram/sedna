"""Populate the Sedna KB from pre-built bundle JSON files in /tmp/bundles/.

Each file /tmp/bundles/<Machine>.json is a validated SemanticDraftBundle dict.
This harness feeds each bundle to Sedna's real pipeline via a stub host so
manifest + canonical bundle + retrieval index are persisted consistently.
"""
import sys, json, shutil, glob, traceback
from pathlib import Path
sys.path.insert(0, '/home/titagram/sedna/src')
sys.path.insert(0, '/home/titagram/sedna/tests/knowledge')

from test_semantic_llm import _prepared_from_markdown
from sedna.knowledge.semantic.drafts import SemanticDraftBundle
from sedna.knowledge.hades_runtime import HadesKnowledgeRuntime

BATCH = Path('/tmp/sedna-batch-all/write-ups/machines')
BUNDLE_DIR = Path('/tmp/bundles')


class SimpleNamespace:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class StubResult:
    def __init__(self, parsed):
        self.parsed = parsed
        self.provider = "agent"
        self.model = "agent-main"
        self.agent_id = "agent-main"
        self.usage = SimpleNamespace(input_tokens=0, output_tokens=0)
        self.audit = {}


class StubHost:
    accepts_schema = True

    def __init__(self, bundle_dict):
        self._bundle = bundle_dict

    def complete_structured(self, *, purpose, **kw):
        if purpose == "sedna.semantic.extract":
            return StubResult(self._bundle)
        if purpose == "sedna.semantic.critic":
            return StubResult({"accepted": True, "findings": []})
        if purpose == "sedna.semantic.repair":
            return StubResult(self._bundle)
        return StubResult(None)


def main():
    kb_root = Path(sys.argv[1])
    subset = sys.argv[2:] or None
    if kb_root.exists():
        shutil.rmtree(kb_root)
    kb_root.mkdir(parents=True)
    kb_root.chmod(0o700)

    bundle_files = sorted(BUNDLE_DIR.glob("*.json"))
    machines = [f.stem for f in bundle_files]
    if subset:
        machines = [m for m in machines if m in subset]

    tmp_src = Path('/tmp/sedna-agent-populate-src')
    if tmp_src.exists():
        shutil.rmtree(tmp_src)
    for m in machines:
        src = BATCH / m / f"{m}.md"
        if src.exists():
            dst = tmp_src / 'write-ups' / 'machines' / m
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst / f"{m}.md")

    results = {}
    for m in machines:
        bf = BUNDLE_DIR / f"{m}.json"
        md_path = BATCH / m / f"{m}.md"
        if not md_path.exists():
            results[m] = {"status": "SKIP", "reason": "md not found"}
            continue
        try:
            bundle_dict = json.loads(bf.read_text())
            SemanticDraftBundle.model_validate(bundle_dict)
        except Exception as e:
            results[m] = {"status": "BUNDLE_INVALID", "error": f"{type(e).__name__}: {str(e)[:150]}"}
            continue
        m_root = kb_root / m
        m_root.mkdir(parents=True)
        m_root.chmod(0o700)
        one_src = tmp_src / 'write-ups' / 'machines' / m
        try:
            host = StubHost(bundle_dict)
            with HadesKnowledgeRuntime.create(host, m_root, external_source_path=one_src) as rt:
                rep = rt.learning.learn(one_src)
            out = rep.model_dump(mode='json')
            results[m] = {"status": "OK", "verified": out.get("verified_source_count"),
                          "failed": out.get("failed_source_count"),
                          "semq": out.get("semantic_quarantined_source_count")}
        except Exception as e:
            results[m] = {"status": "RUN_FAIL", "error": f"{type(e).__name__}: {str(e)[:200]}"}
            traceback.print_exc(file=sys.stderr)

    print("=== AGENT POPULATION (from JSON bundles) ===")
    for m, r in results.items():
        print(m, "->", r)


if __name__ == '__main__':
    main()
